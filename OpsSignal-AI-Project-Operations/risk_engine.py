"""Deterministic risk rules: plain if/else on the stored data, no AI.

Every risk found goes into the risks table and gets exactly one Slack alert (see alerting.py).
A risk has a stable ID, so running the check again never duplicates it:
  new risk        -> inserted as open, alerted once
  still true      -> last_alert_at refreshed, no new alert
  no longer true  -> marked resolved (no alert)
  true again      -> reopened, alerted once more
"""
import logging
from datetime import date, datetime, timezone

from db import get_connection, init_db

log = logging.getLogger("opssignal.webhook")

CRITICAL_PRIORITIES = ("Urgent", "High")
CLOSED_STATUSES = {"done", "canceled", "cancelled", "duplicate"}
ACTIVE_PROJECT_STATUSES = {"started"}
STALE_AFTER_DAYS = 7


def _closed(status):
    return (status or "").strip().lower() in CLOSED_STATUSES


def _pretty(iso):
    if not iso:
        return "no date"
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def _risk(rule, key, title, severity, *, issue=None, project_id=None, detail, impact, action, owner, due):
    return {
        "risk_id": f"{rule}:{key}", "rule": rule, "title": title, "severity": severity,
        "issue_id": issue["issue_id"] if issue else None,
        "project_id": project_id if project_id is not None else (issue["project_id"] if issue else None),
        "detail": detail, "impact": impact, "required_action": action,
        "action_owner": owner or "Unassigned", "action_due": due,
    }


def evaluate(conn, today=None):
    """Apply every rule to the stored data. Returns the list of risks that are true right now."""
    today = today or date.today()
    issues = {r["issue_id"]: dict(r) for r in conn.execute("SELECT * FROM issues")}
    projects = {r["project_id"]: dict(r) for r in conn.execute("SELECT * FROM projects")}
    confirmed = [dict(r) for r in conn.execute(
        "SELECT * FROM approvals WHERE decision = 'confirmed' ORDER BY approval_id")]
    risks = []

    for i in issues.values():
        if _closed(i["status"]):
            continue
        ident, critical = i["identifier"], i["priority"] in CRITICAL_PRIORITIES
        severity = "critical" if i["priority"] == "Urgent" else "high"
        blocker = issues.get(i["blocked_by_issue_id"])

        if critical and i["due_date"] and date.fromisoformat(i["due_date"]) < today:
            risks.append(_risk(
                "overdue_critical", i["issue_id"], "Overdue critical issue", severity, issue=i,
                detail=f"{ident} ({i['title']}) was due {_pretty(i['due_date'])} and is still {i['status']}.",
                impact=f"A {i['priority']} issue is past its due date.",
                action=f"Re-plan or escalate {ident}.", owner=i["owner"], due=i["due_date"]))

        if critical and blocker and not _closed(blocker["status"]):
            risks.append(_risk(
                "blocked_critical", i["issue_id"], "Blocked critical issue", severity, issue=i,
                detail=f"{ident} ({i['title']}) is blocked by {blocker['identifier']}, which is {blocker['status']}.",
                impact=f"{i['priority']} work cannot finish until {blocker['identifier']} is done.",
                action=f"Unblock {blocker['identifier']} or re-plan {ident}.", owner=i["owner"], due=i["due_date"]))

        if critical and not i["owner"]:
            risks.append(_risk(
                "missing_owner", i["issue_id"], "Critical issue has no owner", severity, issue=i,
                detail=f"{ident} ({i['title']}) is {i['priority']} and has no assignee.",
                impact="Nobody is accountable for this issue.",
                action=f"Assign an owner to {ident}.", owner=None, due=i["due_date"]))

        # Delayed dependency: the blocker is due after the work that waits on it.
        if blocker and not _closed(blocker["status"]) and i["due_date"] and blocker["due_date"] \
                and date.fromisoformat(blocker["due_date"]) > date.fromisoformat(i["due_date"]):
            detail = (f"{blocker['identifier']} is due {_pretty(blocker['due_date'])}, "
                      f"after {ident} is due ({_pretty(i['due_date'])}).")
            if blocker["team"] != i["team"]:
                detail += f" Cross-team: {blocker['team']} blocks {i['team']}."
            cause = next((a for a in confirmed if a["issue_id"] == blocker["issue_id"]
                          and a["field_name"] == "due_date" and a["new_value"] == blocker["due_date"]), None)
            if cause:
                detail += f" Caused by confirmed change #{cause['approval_id']}."
            risks.append(_risk(
                "delayed_dependency", i["issue_id"], "Dependency risk", "high", issue=i,
                # If the waiting issue is in no project, use the blocker's so the risk is not hidden.
                project_id=i["project_id"] if i["project_id"] is not None else blocker["project_id"],
                detail=detail,
                impact=f"{ident} depends on {blocker['identifier']} and cannot finish on time.",
                action=f"Revise the {ident} schedule or escalate the {blocker['identifier']} date.",
                owner=blocker["owner"], due=i["due_date"]))

    for p in projects.values():
        name = p["name"]
        if (p["health"] or "").replace(" ", "").lower() == "offtrack":
            risks.append(_risk(
                "off_track", p["project_id"], "Project health is Off track", "high", project_id=p["project_id"],
                detail=f"The project lead set {name} to Off track in Linear.",
                impact="Delivery of the project is at risk.",
                action="Review the open risks and update the plan.", owner=p["owner"], due=p["target_date"]))

        if (p["status"] or "").lower() in ACTIVE_PROJECT_STATUSES:
            last = p["last_update_date"]
            if not last or (today - date.fromisoformat(last)).days >= STALE_AFTER_DAYS:
                since = f"since {_pretty(last)}" if last else "ever"
                risks.append(_risk(
                    "stale_update", p["project_id"], "Project update is stale", "medium", project_id=p["project_id"],
                    detail=f"{name} has had no project update {since} (limit: {STALE_AFTER_DAYS} days).",
                    impact="Leaders cannot trust the project's reported health.",
                    action="Post a project update in Linear.", owner=p["owner"], due=None))

    # A confirmed due-date change that pushes an issue past its project's target date.
    # (Dependency conflicts caused by a confirmed change are reported in the dependency risk above.)
    for a in confirmed:
        i = issues.get(a["issue_id"])
        p = projects.get(i["project_id"]) if i else None
        if not (i and p and a["field_name"] == "due_date" and p["target_date"]) or _closed(i["status"]):
            continue
        if i["due_date"] == a["new_value"] and a["new_value"] > p["target_date"]:
            risks.append(_risk(
                "confirmed_change", f"approval-{a['approval_id']}", "Confirmed change creates milestone risk",
                "high", issue=i,
                detail=(f"Confirmed change #{a['approval_id']} moved {i['identifier']} to {_pretty(a['new_value'])}, "
                        f"after the project target date {_pretty(p['target_date'])}."),
                impact="The project target date is at risk.",
                action=f"Re-plan {i['identifier']} or move the target date.", owner=i["owner"], due=p["target_date"]))
    return risks


def reconcile(conn, risks, now=None):
    """Insert new risks, refresh persisting ones, resolve vanished ones. Returns the counts."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {r["risk_id"]: dict(r) for r in conn.execute("SELECT * FROM risks")}
    fields = ("rule", "issue_id", "project_id", "severity", "title", "detail", "impact",
              "required_action", "action_owner", "action_due")
    counts = {"new": 0, "reopened": 0, "persisting": 0, "resolved": 0}
    with conn:
        for r in risks:
            old = existing.get(r["risk_id"])
            values = [r[f] for f in fields]
            if old is None:
                conn.execute(
                    f"""INSERT INTO risks (risk_id, {', '.join(fields)}, status, first_seen_at, opened_at, last_alert_at)
                        VALUES (?, {', '.join('?' for _ in fields)}, 'open', ?, ?, ?)""",
                    [r["risk_id"], *values, now, now, now])
                counts["new"] += 1
            elif old["status"] == "resolved":
                conn.execute(
                    f"""UPDATE risks SET {', '.join(f + ' = ?' for f in fields)},
                        status='open', opened_at=?, last_alert_at=?, resolved_at=NULL WHERE risk_id=?""",
                    [*values, now, now, r["risk_id"]])
                counts["reopened"] += 1
            else:
                conn.execute(
                    f"UPDATE risks SET {', '.join(f + ' = ?' for f in fields)}, last_alert_at=? WHERE risk_id=?",
                    [*values, now, r["risk_id"]])
                counts["persisting"] += 1
        current = {r["risk_id"] for r in risks}
        for risk_id, old in existing.items():
            if old["status"] == "open" and risk_id not in current:
                conn.execute("UPDATE risks SET status='resolved', resolved_at=? WHERE risk_id=?", (now, risk_id))
                counts["resolved"] += 1
    return counts


def run_risk_check(slack=None, channel=None, today=None, now=None):
    """Evaluate the rules, update the risks table, then send one alert per new risk."""
    import alerting  # imported here so the rules stay usable without Slack configured

    conn = get_connection()
    try:
        init_db(conn)
        counts = reconcile(conn, evaluate(conn, today), now)
        counts["alerts"] = alerting.send_pending_alerts(conn, slack, channel, now)
        counts["open"] = conn.execute("SELECT COUNT(*) FROM risks WHERE status='open'").fetchone()[0]
    finally:
        conn.close()
    log.info("RISK CHECK: %s open, %s new, %s reopened, %s resolved; alerts: %s",
             counts["open"], counts["new"], counts["reopened"], counts["resolved"], counts["alerts"])
    return counts


if __name__ == "__main__":
    print(run_risk_check())
