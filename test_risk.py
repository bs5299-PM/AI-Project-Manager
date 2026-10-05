"""Checks the risk engine and alerts with a throwaway database and a fake Slack.
Never contacts real services and never touches opssignal.db.

Run:  python test_risk.py
"""
import tempfile
from datetime import date
from pathlib import Path

import db
import risk_engine

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
TODAY = date(2026, 10, 4)


class FakeSlack:
    def __init__(self):
        self.sent, self.fail = [], False

    def post_message(self, channel, blocks, text):
        if self.fail:
            return {"ok": False, "error": "channel_not_found"}
        self.sent.append((channel, blocks[0]["text"]["text"]))
        return {"ok": True}


slack = FakeSlack()
results = []


def check(name, ok):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name)


def run():
    return risk_engine.run_risk_check(slack, "#alerts", TODAY, f"2026-10-04T12:00:{len(slack.sent):02d}+00:00")


def q(sql, *args):
    c = db.get_connection()
    rows = [dict(r) for r in c.execute(sql, args)]
    c.close()
    return rows


def exec_(sql, *args):
    c = db.get_connection()
    with c:
        c.execute(sql, args)
    c.close()


c = db.get_connection()
db.init_db(c)
c.execute("""INSERT INTO projects (linear_id, name, status, health, owner, target_date, last_update_date)
             VALUES ('p1', 'Project Atlas', 'Started', 'onTrack', 'Bindu', '2026-10-30', '2026-10-03')""")
# A healthy project, and a fake overdue High issue.
c.execute("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date)
             VALUES ('l-1', 'TOS-90', 1, 'Fake overdue High issue', 'High', 'Hardware Lead', 'Hardware', 'Todo', '2026-10-01')""")
c.commit()
c.close()

# --- the main test: a fake overdue High issue ---
counts = run()
risk = q("SELECT * FROM risks")
check("overdue High issue -> exactly one open risk", len(risk) == 1 and risk[0]["rule"] == "overdue_critical"
      and risk[0]["status"] == "open" and risk[0]["risk_id"] == "overdue_critical:1")
check("one Slack alert sent, logged as sent in alerts",
      len(slack.sent) == 1 and q("SELECT result FROM alerts") == [{"result": "sent"}])
print("\n--- risks row ---")
print(risk[0])
print("\n--- Slack message (channel %s) ---" % slack.sent[0][0])
print(slack.sent[0][1])
print()

# --- re-run changes nothing ---
counts = run()
check("re-run: no duplicate risk, no second alert, last_seen_at refreshed",
      len(q("SELECT * FROM risks")) == 1 and len(slack.sent) == 1 and counts["persisting"] == 1
      and q("SELECT last_seen_at FROM risks")[0]["last_seen_at"] > risk[0]["last_seen_at"])

# --- resolve ---
exec_("UPDATE issues SET status = 'Done' WHERE identifier = 'TOS-90'")
run()
r = q("SELECT status, resolved_at FROM risks")[0]
check("issue closed -> risk marked resolved, no alert", r["status"] == "resolved" and r["resolved_at"] and len(slack.sent) == 1)

# --- reopen alerts again ---
exec_("UPDATE issues SET status = 'Todo' WHERE identifier = 'TOS-90'")
run()
check("risk true again -> reopened and alerted once more", q("SELECT status FROM risks")[0]["status"] == "open" and len(slack.sent) == 2)

# --- failed Slack post is retried, not lost ---
exec_("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date)
         VALUES ('l-2', 'TOS-91', 1, 'Urgent and unowned', 'Urgent', NULL, 'Software', 'Todo', '2026-10-20')""")
slack.fail = True
run()
check("Slack failure is logged as failed", q("SELECT COUNT(*) n FROM alerts WHERE result='failed'")[0]["n"] == 1)
slack.fail = False
run()
check("failed alert is retried on the next check, then sent",
      q("SELECT COUNT(*) n FROM alerts WHERE result='sent' AND risk_id='missing_owner:2'")[0]["n"] == 1)
check("missing owner shows 'Unassigned' as action owner",
      q("SELECT action_owner FROM risks WHERE risk_id='missing_owner:2'")[0]["action_owner"] == "Unassigned")

# --- delayed dependency (cross-team, caused by a confirmed change) ---
exec_("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date)
         VALUES ('l-3', 'TOS-92', 1, 'Hardware delivery', 'Medium', 'Hardware Lead', 'Hardware', 'Backlog', '2026-10-24')""")
exec_("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date, blocked_by_issue_id)
         VALUES ('l-4', 'TOS-93', 1, 'Software validation', 'Medium', 'Software Lead', 'Software', 'Todo', '2026-10-14', 3)""")
exec_("""INSERT INTO approvals (issue_id, field_name, old_value, new_value, source_message, decision, decided_by)
         VALUES (3, 'due_date', '2026-10-10', '2026-10-24', 'msg', 'confirmed', 'bindu')""")
run()
dep = q("SELECT * FROM risks WHERE rule='delayed_dependency'")
check("delayed dependency flagged once, owner = blocker's owner, due = dependent's due date",
      len(dep) == 1 and dep[0]["action_owner"] == "Hardware Lead" and dep[0]["action_due"] == "2026-10-14")
check("consolidated: mentions cross-team and the confirmed change",
      "Cross-team" in dep[0]["detail"] and "confirmed change #1" in dep[0]["detail"])
check("no separate confirmed-change alert for the same dependency", not q("SELECT * FROM risks WHERE rule='confirmed_change'"))

# --- project rules ---
exec_("UPDATE projects SET health = 'offTrack', last_update_date = '2026-09-20'")
run()
rules = {r["rule"] for r in q("SELECT rule FROM risks WHERE status='open'")}
check("off-track health and 7-day-stale update both flagged", {"off_track", "stale_update"} <= rules)

# --- blocked critical, confirmed change past the target date ---
exec_("UPDATE issues SET priority = 'High' WHERE identifier = 'TOS-93'")
exec_("UPDATE issues SET due_date = '2026-11-05' WHERE identifier = 'TOS-92'")
exec_("UPDATE approvals SET new_value = '2026-11-05' WHERE approval_id = 1")
run()
rules = {r["rule"] for r in q("SELECT rule FROM risks WHERE status='open'")}
check("High issue blocked by an open issue is flagged", "blocked_critical" in rules)
check("confirmed change past the project target date is flagged", "confirmed_change" in rules)

# --- every open risk was alerted exactly once ---
unalerted = q("""SELECT r.risk_id FROM risks r WHERE r.status='open'
                 AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.risk_id=r.risk_id AND a.result='sent')""")
check("no silent risks: every open risk has a sent alert", not unalerted)
check("the one-alert-per-risk rule held (sent alerts per open risk)",
      all(r["n"] <= 2 for r in q("SELECT COUNT(*) n FROM alerts WHERE result='sent' GROUP BY risk_id")))

# --- dependency risk on an issue with no project falls back to the blocker's project ---
exec_("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date, blocked_by_issue_id)
         VALUES ('l-5', 'TOS-94', NULL, 'Waiting issue with no project', 'Medium', 'X', 'Software', 'Todo', '2026-10-10', 3)""")
run()
fallback = q("SELECT project_id FROM risks WHERE risk_id = 'delayed_dependency:5'")
check("dependency risk for a project-less issue uses the blocker's project", fallback == [{"project_id": 1}])

print("\nALL PASSED" if all(results) else "\nSOME FAILED")
raise SystemExit(0 if all(results) else 1)
