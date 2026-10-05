"""Checks the Slack loop with fake Slack, Claude and Linear and a throwaway database.
It never contacts real services and never touches opssignal.db.

Run:  python test_slack.py
"""
import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import db
import slack_routes
from webhook_server import create_app

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
conn = db.get_connection()
db.init_db(conn)
conn.execute("INSERT INTO projects (linear_id, name) VALUES ('p1', 'Project Atlas')")
conn.execute("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, team, status, due_date)
                VALUES ('l-5', 'TOS-5', 1, 'Hardware V2 deliver', 'Medium', 'TOS', 'Backlog', '2026-10-10')""")
conn.execute("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, team, status, due_date)
                VALUES ('l-4', 'TOS-4', 1, 'Software validation', 'High', 'TOS', 'Todo', '2026-10-14')""")
conn.commit()
conn.close()

SECRET, CHANNEL, ME, STRANGER = "slack-test-secret", "C123", "UME", "UOTHER"
settings = slack_routes.SlackSettings(SECRET, "xoxb-fake", {CHANNEL}, {ME})


class FakeSlack:
    def __init__(self):
        self.posted, self.updated, self.ephemeral = [], [], []

    def post_message(self, channel, blocks, text):
        self.posted.append((channel, blocks, text))

    def update_message(self, channel, ts, blocks, text):
        self.updated.append((channel, ts, blocks, text))

    def post_ephemeral(self, channel, user, text):
        self.ephemeral.append((channel, user, text))


class FakeLinear:
    def __init__(self):
        self.values = {"due_date": "2026-10-10", "priority": "Medium"}
        self.writes = []

    def get_issue_fields(self, linear_id):
        return dict(self.values)

    def update_issue(self, linear_id, field, value):
        self.writes.append((linear_id, field, value))
        self.values[field] = value


claude_answer = {}
claude_calls = []


def fake_claude(text, candidates, today):
    claude_calls.append(text)
    return dict(claude_answer)


slack, linear = FakeSlack(), FakeLinear()
bp = slack_routes.create_slack_blueprint(settings, fake_claude, slack, linear, run_async=lambda fn, *a: fn(*a))
client = create_app(secret="x", sync_fn=lambda: {}, slack_blueprint=bp).test_client()
results = []


def check(name, ok):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name)


def signed(body, secret=SECRET, age=0):
    ts = str(int(time.time() - age))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def send_message(text, channel=CHANNEL, event_id="E1", extra=None, headers_extra=None):
    event = {"type": "message", "channel": channel, "user": ME, "text": text, **(extra or {})}
    body = json.dumps({"type": "event_callback", "event_id": event_id, "event": event}).encode()
    return client.post("/slack-events", data=body, content_type="application/json",
                       headers={**signed(body), **(headers_extra or {})})


def click(action_id, approval_id, user=ME):
    payload = {"type": "block_actions", "user": {"id": user, "name": "bindu"},
               "channel": {"id": CHANNEL}, "message": {"ts": "111.222"},
               "actions": [{"action_id": action_id, "value": str(approval_id)}]}
    body = urlencode({"payload": json.dumps(payload)}).encode()
    return client.post("/slack-interactive", data=body, headers=signed(body),
                       content_type="application/x-www-form-urlencoded")


def approval(approval_id):
    c = db.get_connection()
    row = c.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def history_rows():
    c = db.get_connection()
    rows = [dict(r) for r in c.execute("SELECT * FROM change_history")]
    c.close()
    return rows


# --- security ---
body = b'{"type":"url_verification","challenge":"abc"}'
check("events: unsigned request rejected (401)", client.post("/slack-events", data=body).status_code == 401)
check("events: wrong signature rejected (401)",
      client.post("/slack-events", data=body, headers=signed(body, "wrong")).status_code == 401)
check("events: old timestamp rejected (401)",
      client.post("/slack-events", data=body, headers=signed(body, age=3600)).status_code == 401)
r = client.post("/slack-events", data=body, headers=signed(body), content_type="application/json")
check("events: Slack's setup challenge is answered", r.get_json() == {"challenge": "abc"})
ibody = urlencode({"payload": "{}"}).encode()
check("interactive: unsigned request rejected (401)", client.post("/slack-interactive", data=ibody).status_code == 401)

# --- messages that must be ignored ---
send_message("Hardware V2 delivery moves to Oct 17", channel="C999", event_id="E-other")
send_message("Hardware V2 delivery moves to Oct 17", event_id="E-bot", extra={"bot_id": "B1"})
send_message("Hardware V2 delivery moves to Oct 17", event_id="E-edit", extra={"subtype": "message_changed"})
check("ignored: unapproved channel, bot message, edited message (no Claude call, no card)",
      not claude_calls and not slack.posted)

# --- a clear message -> pending proposal + card with buttons ---
claude_answer.update(outcome="proposal", identifier="TOS-5", field="due_date", new_value="2026-10-17", reason="clear")
send_message("Hardware V2 delivery is moving from Oct 10 to Oct 17", event_id="E1")
a = approval(1)
check("clear message: saved as pending with the right change",
      a and a["decision"] == "pending" and a["field_name"] == "due_date"
      and a["old_value"] == "2026-10-10" and a["new_value"] == "2026-10-17")
has_buttons = any(b["type"] == "actions" for b in slack.posted[-1][1])
check("clear message: card posted with Confirm and Reject buttons", len(slack.posted) == 1 and has_buttons)

send_message("Hardware V2 delivery is moving from Oct 10 to Oct 17", event_id="E1-again")
send_message("x", event_id="E1-retry", headers_extra={"X-Slack-Retry-Num": "1"})
send_message("Hardware V2 delivery is moving", event_id="E1")  # same event id again
check("duplicates: Slack retry and repeated event id are ignored",
      len(claude_calls) == 2 and len(slack.posted) == 2)  # 'E1-again' is a genuinely new event
# (the extra proposal from 'E1-again' is expected; clean it up so later checks are exact)
c = db.get_connection(); c.execute("UPDATE approvals SET decision='rejected' WHERE approval_id=2"); c.commit(); c.close()

# --- Claude unsure / invents an issue / nothing matches ---
claude_answer.clear(); claude_answer.update(outcome="not_sure", reason="no date given")
send_message("Hardware V2 is delayed", event_id="E2")
a3 = approval(3)
check("unsure: saved as needs_clarification, card has no buttons",
      a3["decision"] == "needs_clarification" and a3["issue_id"] is None
      and not any(b["type"] == "actions" for b in slack.posted[-1][1]))

claude_answer.clear(); claude_answer.update(outcome="proposal", identifier="TOS-99", field="due_date",
                                           new_value="2026-10-17", reason="made up")
send_message("Hardware V2 delivery to Oct 17", event_id="E3")
check("invented issue (not in candidates) becomes needs_clarification", approval(4)["decision"] == "needs_clarification")

before = len(claude_calls)
send_message("Weather looks great today", event_id="E4")
check("no matching issue: needs_clarification without asking Claude",
      approval(5)["decision"] == "needs_clarification" and len(claude_calls) == before)

claude_answer.clear(); claude_answer.update(outcome="proposal", identifier="TOS-5", field="status",
                                           new_value="Done", reason="status")
send_message("Hardware V2 is done", event_id="E5")
check("unsupported field (status) becomes needs_clarification", approval(6)["decision"] == "needs_clarification")

# --- clicks ---
check("confirm by a non-approver is refused, nothing changes",
      click("approval_confirm", 1, user=STRANGER).status_code == 200
      and approval(1)["decision"] == "pending" and not linear.writes and slack.ephemeral)

click("approval_confirm", 1)
a = approval(1)
check("confirm: Linear updated once", linear.writes == [("l-5", "due_date", "2026-10-17")])
check("confirm: approval confirmed with who and when", a["decision"] == "confirmed" and a["decided_by"] == "bindu" and a["decided_at"])
h = history_rows()
check("confirm: one history row, source slack, changed_by = clicker",
      len(h) == 1 and h[0]["source"] == "slack" and h[0]["changed_by"] == "bindu"
      and (h[0]["old_value"], h[0]["new_value"]) == ("2026-10-10", "2026-10-17"))
c = db.get_connection()
check("confirm: our copy of the issue updated",
      c.execute("SELECT due_date FROM issues WHERE identifier='TOS-5'").fetchone()[0] == "2026-10-17")
c.close()
check("confirm: card updated", slack.updated and "Confirmed" in slack.updated[-1][2][-1]["elements"][0]["text"])

click("approval_confirm", 1)
check("double click does nothing", len(linear.writes) == 1 and len(history_rows()) == 1)

# reject
claude_answer.clear(); claude_answer.update(outcome="proposal", identifier="TOS-5", field="priority",
                                           new_value="Urgent", reason="clear")
send_message("Hardware V2 delivery is now urgent", event_id="E6")
rid = max(r for r in range(1, 20) if approval(r))
click("approval_reject", rid)
check("reject: approval rejected, Linear and history untouched",
      approval(rid)["decision"] == "rejected" and len(linear.writes) == 1 and len(history_rows()) == 1)

# stale
send_message("Hardware V2 delivery is now high", event_id="E7")
rid = max(r for r in range(1, 20) if approval(r))
linear.values["priority"] = "Low"  # someone changed it in Linear after the card was posted
click("approval_confirm", rid)
check("stale card: refused, Linear not written, marked needs_clarification",
      len(linear.writes) == 1 and approval(rid)["decision"] == "needs_clarification")

print("\nALL PASSED" if all(results) else "\nSOME FAILED")
raise SystemExit(0 if all(results) else 1)
