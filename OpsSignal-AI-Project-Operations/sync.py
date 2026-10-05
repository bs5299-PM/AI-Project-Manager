"""Copies projects and issues from Linear into SQLite. Safe to run repeatedly."""
from datetime import datetime, timezone

from db import get_connection, init_db
from linear_client import fetch_issues, fetch_projects


def _next_milestone(milestones):
    """The earliest-dated milestone that is not finished yet."""
    open_ones = [m for m in milestones if m.get("status") != "done"]
    open_ones.sort(key=lambda m: (m.get("targetDate") is None, m.get("targetDate") or ""))
    return open_ones[0]["name"] if open_ones else None


def _latest_update(updates):
    return max(updates, key=lambda u: u["createdAt"]) if updates else None


def _save_projects(conn, projects):
    for p in projects:
        update = _latest_update(p["projectUpdates"]["nodes"])
        conn.execute(
            """
            INSERT INTO projects (linear_id, name, milestone, status, health, owner,
                                  target_date, last_update_date, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(linear_id) DO UPDATE SET
                name=excluded.name, milestone=excluded.milestone, status=excluded.status,
                health=excluded.health, owner=excluded.owner,
                target_date=excluded.target_date, last_update_date=excluded.last_update_date,
                url=excluded.url
            """,
            (
                p["id"],
                p["name"],
                _next_milestone(p["projectMilestones"]["nodes"]),
                (p.get("status") or {}).get("name"),
                update["health"] if update else None,
                (p.get("lead") or {}).get("name"),
                p.get("targetDate"),
                update["createdAt"][:10] if update else None,
                p.get("url"),
            ),
        )


def _save_issues(conn, issues):
    project_ids = {r["linear_id"]: r["project_id"] for r in conn.execute("SELECT linear_id, project_id FROM projects")}
    # Pass 1: save every issue without its blocked-by link.
    for i in issues:
        conn.execute(
            """
            INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner,
                                team, status, due_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(linear_id) DO UPDATE SET
                identifier=excluded.identifier, project_id=excluded.project_id,
                title=excluded.title, priority=excluded.priority, owner=excluded.owner,
                team=excluded.team, status=excluded.status, due_date=excluded.due_date
            """,
            (
                i["id"],
                i["identifier"],
                project_ids.get((i.get("project") or {}).get("id")),
                i["title"],
                i.get("priorityLabel"),
                (i.get("assignee") or {}).get("name"),
                (i.get("team") or {}).get("name"),
                (i.get("state") or {}).get("name"),
                i.get("dueDate"),
            ),
        )
    # Pass 2: now every issue has our own ID, so blocked-by links can be resolved.
    issue_ids = {r["linear_id"]: r["issue_id"] for r in conn.execute("SELECT linear_id, issue_id FROM issues")}
    for i in issues:
        blockers = [r["issue"]["id"] for r in i["inverseRelations"]["nodes"] if r["type"] == "blocks"]
        blocker_id = next((issue_ids[b] for b in blockers if b in issue_ids), None)
        conn.execute("UPDATE issues SET blocked_by_issue_id = ? WHERE linear_id = ?", (blocker_id, i["id"]))


PROJECT_FIELDS = ["name", "milestone", "status", "health", "owner", "target_date"]
ISSUE_FIELDS = ["title", "priority", "owner", "team", "status", "due_date",
                "blocked_by_issue_id", "project_id"]


def _snapshot(conn):
    """Copy of the stored rows (plus readable names) as they are before this sync changes them."""
    projects = {r["linear_id"]: dict(r) for r in conn.execute("SELECT * FROM projects")}
    issues = {r["linear_id"]: dict(r) for r in conn.execute("SELECT * FROM issues")}
    project_names = {r["project_id"]: r["name"] for r in projects.values()}
    issue_labels = {r["issue_id"]: r["identifier"] for r in issues.values()}
    return {"projects": projects, "issues": issues,
            "project_names": project_names, "issue_labels": issue_labels}


def _blank_to_none(value):
    return None if value == "" else value


def _readable(field, value, names):
    """Link fields hold our internal numbers; show them as 'TOS-5' / project name instead."""
    if value is None:
        return None
    if field == "blocked_by_issue_id":
        return names["issue_labels"].get(value)
    if field == "project_id":
        return names["project_names"].get(value)
    return None if value is None else str(value)


def _record_changes(conn, before):
    """Compare each stored row with its snapshot and write one history row per changed field.
    Items that were not in the snapshot are new, so there is nothing to compare."""
    after = _snapshot(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    for kind, fields, id_col in (("projects", PROJECT_FIELDS, "project_id"),
                                 ("issues", ISSUE_FIELDS, "issue_id")):
        for linear_id, old_row in before[kind].items():
            new_row = after[kind][linear_id]
            for field in fields:
                old, new = _blank_to_none(old_row[field]), _blank_to_none(new_row[field])
                if old == new:
                    continue
                conn.execute(
                    f"""INSERT INTO change_history
                        ({id_col}, field_name, old_value, new_value, changed_by, changed_at, source)
                        VALUES (?, ?, ?, ?, NULL, ?, 'linear')""",
                    (new_row[id_col], field,
                     _readable(field, old, before), _readable(field, new, after), now),
                )
                written += 1
    return written


def sync():
    """Fetch everything from Linear, copy it into the database, and record what changed.
    Returns row counts and the number of history rows written."""
    projects = fetch_projects()
    issues = fetch_issues()
    conn = get_connection()
    try:
        init_db(conn)
        with conn:  # all-or-nothing: a failure leaves data and history as they were
            before = _snapshot(conn)
            _save_projects(conn, projects)
            _save_issues(conn, issues)
            changes = _record_changes(conn, before)
        return {
            "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "issues": conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0],
            "changes": changes,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    from risk_engine import run_risk_check

    counts = sync()
    print(f"Sync complete: {counts['projects']} projects, {counts['issues']} issues in the database. "
          f"{counts['changes']} change(s) recorded.")
    risks = run_risk_check()
    print(f"Risk check: {risks['open']} open risk(s), {risks['new']} new, "
          f"{risks['resolved']} resolved, {risks['alerts']} alert(s) sent.")
    from notion_sync import run_if_configured

    notion = run_if_configured()
    print(f"Notion sync: {notion}" if notion else "Notion sync: skipped (not configured).")
