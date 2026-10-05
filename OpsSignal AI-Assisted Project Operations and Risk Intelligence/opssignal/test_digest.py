"""Checks the digest with a throwaway database and a fake Slack. Never touches opssignal.db.

Run:  python test_digest.py
"""
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import db
import digest

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 5)
results = []


def check(name, ok):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name)


c = db.get_connection()
db.init_db(c)

# an empty database first
empty = digest.build_digest(c, TODAY, NOW)
check("empty database: every section says so", all(s in empty for s in
      ("No changes recorded.", "No open risks.", "Nothing waiting.", "Nothing due.")))

c.execute("INSERT INTO projects (linear_id, name, status) VALUES ('p1', 'Project Atlas', 'Started')")
for n, (ident, prio, due, status) in enumerate([
        ("TOS-5", "Medium", "2026-10-24", "Backlog"),
        ("TOS-4", "High", "2026-10-09", "Todo"),          # due within 7 days
        ("TOS-6", "Low", "2026-10-08", "Done"),           # due soon but closed: excluded
        ("TOS-7", "Low", "2026-10-30", "Todo")], start=1):  # due too late: excluded
    c.execute("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date)
                 VALUES (?, ?, 1, ?, ?, NULL, 'TOS', ?, ?)""", (f"l{n}", ident, f"Issue {ident}", prio, status, due))
hist = [
    (1, "due_date", "2026-10-17", "2026-10-24", "bindu", "2026-10-04T22:49:13+00:00", "slack"),
    (1, "priority", "Urgent", "Medium", None, "2026-09-29T10:00:00+00:00", "linear"),   # inside 7 days
    (1, "title", "Old", "Older", None, "2026-09-20T10:00:00+00:00", "linear"),          # too old
]
for h in hist:
    c.execute("""INSERT INTO change_history (issue_id, field_name, old_value, new_value, changed_by, changed_at, source)
                 VALUES (?,?,?,?,?,?,?)""", h)
c.execute("""INSERT INTO change_history (project_id, field_name, old_value, new_value, changed_at, source)
             VALUES (1, 'health', 'onTrack', 'offTrack', '2026-10-05T01:00:00+00:00', 'linear')""")
for rid, sev, status, first in (("a", "medium", "open", "t1"), ("b", "critical", "open", "t2"), ("c", "high", "resolved", "t3")):
    c.execute("""INSERT INTO risks (risk_id, rule, issue_id, project_id, severity, status, title, detail, first_seen_at, opened_at, last_seen_at)
                 VALUES (?, 'r', 2, 1, ?, ?, ?, ?, ?, ?, ?)""", (rid, sev, status, f"Title {rid}", f"Detail {rid}", first, first, first))
c.execute("""INSERT INTO approvals (issue_id, field_name, old_value, new_value, source_message, decision)
             VALUES (2, 'priority', 'High', 'Urgent', 'm', 'pending')""")
c.execute("""INSERT INTO approvals (issue_id, field_name, new_value, source_message, decision)
             VALUES (2, 'due_date', '2026-10-30', 'm', 'needs_clarification')""")
c.execute("""INSERT INTO approvals (issue_id, field_name, new_value, source_message, decision)
             VALUES (2, 'due_date', '2026-10-31', 'm', 'rejected')""")
c.commit()

text = digest.build_digest(c, TODAY, NOW)
print("\n" + text + "\n")
changed, risk, nxt = text.split("WHAT'S AT RISK")[0], text.split("WHAT'S AT RISK")[1].split("WHAT HAPPENS NEXT")[0], text.split("WHAT HAPPENS NEXT")[1]

check("changed: last 7 days only (old change excluded), field, old → new, who, source shown",
      "TOS-5 due_date: 2026-10-17 → 2026-10-24 · by bindu (slack) · Oct 4 22:49 UTC" in changed and "Older" not in changed)
check("changed: an issue change with no changer shows 'unknown'; project-level change shown by project name",
      "priority: Urgent → Medium · by unknown (linear)" in changed and "Project Atlas health: onTrack → offTrack" in changed)
check("changed: newest first", changed.index("health") < changed.index("due_date") < changed.index("priority"))
check("risk: open risks only, most severe first, severity + title + detail + issue",
      "(2 open)" in risk and "Detail c" not in risk and risk.index("[critical]") < risk.index("[medium]")
      and "[critical] Title b (TOS-4): Detail b" in risk)
check("next: only pending approvals (not clarification/rejected)",
      "TOS-4 priority → Urgent (approval #1)" in nxt and "2026-10-30" not in nxt and "2026-10-31" not in nxt)
check("next: issues due in the next 7 days, open only, soonest first",
      "TOS-4 Issue TOS-4 — due Oct 9 (owner: Unassigned)" in nxt and "TOS-6" not in nxt and "TOS-7" not in nxt)
c.close()


# --- sending ---
class FakeSlack:
    def __init__(self, ok=True):
        self.ok, self.posted = ok, []

    def post_text(self, channel, text):
        self.posted.append((channel, text))
        return {"ok": True} if self.ok else {"ok": False, "error": "channel_not_found"}


fs = FakeSlack()
digest.send_digest(text, fs, "#all-new-workspace")
check("--send posts the same text to the channel", fs.posted == [("#all-new-workspace", text)])
try:
    digest.send_digest(text, FakeSlack(ok=False), "#x")
    failed_loudly = False
except RuntimeError:
    failed_loudly = True
check("a Slack failure is reported, not hidden", failed_loudly)

print("\nALL PASSED" if all(results) else "\nSOME FAILED")
raise SystemExit(0 if all(results) else 1)
