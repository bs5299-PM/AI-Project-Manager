"""Checks the Notion sync with a fake Notion and a throwaway database.
Never contacts Notion and never touches opssignal.db.

Run:  python test_notion.py
"""
import itertools
import tempfile
from pathlib import Path

import db
import notion_sync

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"


# ---------- a tiny in-memory stand-in for the Notion API ----------
class FakeNotion:
    def __init__(self):
        self.props = {"Name": {"type": "title"}}
        self.pages, self.blocks, self.calls = {}, {}, []
        self.ids = itertools.count(1)
        self.fail_titles = set()
        outer = self

        class Databases:
            def retrieve(self, database_id):
                return {"data_sources": [{"id": "ds1"}]}

        class DataSources:
            def retrieve(self, data_source_id):
                return {"properties": dict(outer.props)}

            def update(self, data_source_id, properties):
                for name, definition in properties.items():
                    outer.props[name] = {"type": next(iter(definition))}

            def query(self, data_source_id, filter, page_size=5):
                prop, (kind, cond) = filter["property"], next((k, v) for k, v in filter.items() if k != "property")
                want = cond["equals"]
                return {"results": [p for p in outer.pages.values()
                                    if not p["archived"] and outer._text(p, prop) == want]}

        class Pages:
            def create(self, parent, properties):
                title = properties["Name"]["title"][0]["text"]["content"]
                if title in outer.fail_titles:
                    raise RuntimeError("boom")
                pid = f"page{next(outer.ids)}"
                outer.pages[pid] = {"id": pid, "archived": False, "properties": {}}
                outer.calls.append(("create", pid))
                outer._write(pid, properties)
                outer.blocks[pid] = []
                return {"id": pid}

            def update(self, page_id, properties):
                outer.calls.append(("update", page_id))
                outer._write(page_id, properties)

            def retrieve(self, page_id):
                if page_id not in outer.pages:
                    raise RuntimeError("not found")
                return outer.pages[page_id]

        class Children:
            def list(self, block_id, page_size=100, start_cursor=None):
                return {"results": [{"id": b["id"]} for b in outer.blocks[block_id]], "has_more": False}

            def append(self, block_id, children):
                for ch in children:
                    ch = dict(ch, id=f"b{next(outer.ids)}")
                    outer.blocks[block_id].append(ch)

        class Blocks:
            children = Children()

            def delete(self, block_id):
                for lst in outer.blocks.values():
                    lst[:] = [b for b in lst if b["id"] != block_id]

        self.databases, self.data_sources, self.pages_api, self.blocks_api = Databases(), DataSources(), Pages(), Blocks()
        self.pages_ns = self.pages_api

    # the client exposes .pages and .blocks; keep the dict named differently above
    @property
    def blocks_ns(self):
        return self.blocks_api

    def _write(self, pid, properties):
        store = self.pages[pid]["properties"]
        for name, payload in properties.items():
            if "title" in payload:
                store[name] = {"title": [{"plain_text": payload["title"][0]["text"]["content"]}]}
            elif "rich_text" in payload:
                store[name] = {"rich_text": [{"plain_text": t["text"]["content"]} for t in payload["rich_text"]]}
            else:
                store[name] = payload

    def _text(self, page, prop):
        p = page["properties"].get(prop, {})
        key = "title" if "title" in p else "rich_text"
        return "".join(t["plain_text"] for t in p.get(key, []))

    def rows(self):
        return [p for p in self.pages.values() if not p["archived"]]

    def body(self, pid):
        return [b["heading_2"]["rich_text"][0]["text"]["content"] if b["type"] == "heading_2"
                else (b.get("bulleted_list_item") or b["paragraph"])["rich_text"][0]["text"]["content"]
                for b in self.blocks[pid]]


class Client:  # what notion_sync sees: .databases .data_sources .pages .blocks
    def __init__(self, fake):
        self.databases, self.data_sources = fake.databases, fake.data_sources
        self.pages, self.blocks = fake.pages_api, fake.blocks_api


# ---------- our data ----------
c = db.get_connection()
db.init_db(c)
c.execute("""INSERT INTO projects (linear_id, name, milestone, status, health, owner, target_date, last_update_date, url)
             VALUES ('p1', 'Project Atlas', 'Sales handoff', 'Planned', 'onTrack', 'Bindu Singh', '2026-10-30',
                     '2026-10-04', 'https://linear.app/x/project/atlas-1')""")
c.execute("""INSERT INTO projects (linear_id, name, status, url) VALUES ('p2', 'Quiet Project', 'Started', NULL)""")
c.execute("""INSERT INTO issues (linear_id, identifier, project_id, title, priority, owner, team, status, due_date)
             VALUES ('l-5', 'TOS-5', 1, 'Hardware V2 deliver', 'Medium', 'Bindu Singh', 'TOS', 'Backlog', '2026-10-24')""")
c.execute("""INSERT INTO approvals (issue_id, field_name, old_value, new_value, source_message, decision, decided_by, decided_at)
             VALUES (1, 'due_date', '2026-10-17', '2026-10-24', 'msg', 'confirmed', 'bindu', '2026-10-04T22:49:13+00:00')""")
c.execute("""INSERT INTO change_history (issue_id, field_name, old_value, new_value, changed_by, changed_at, source)
             VALUES (1, 'due_date', '2026-10-17', '2026-10-24', 'bindu', '2026-10-04T22:49:13+00:00', 'slack')""")
c.execute("""INSERT INTO risks (risk_id, rule, issue_id, project_id, severity, status, title, detail, impact, required_action,
                              action_owner, action_due, first_seen_at, opened_at, last_seen_at)
             VALUES ('delayed_dependency:4', 'delayed_dependency', 1, 1, 'high', 'open', 'Dependency risk',
                     'TOS-5 is due Oct 24, after TOS-4 is due (Oct 14).', 'x', 'Revise the TOS-4 schedule.',
                     'Bindu Singh', '2026-10-14', 't', 't', 't')""")
c.commit()
c.close()

fake = FakeNotion()
client = Client(fake)
results = []


def check(name, ok):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name)


def run():
    return notion_sync.sync_notion(client, "db1")


def atlas():
    return next(p for p in fake.rows() if fake._text(p, "Name") == "Project Atlas" or fake._text(p, notion_sync.LINEAR_ID) == "p1")


# --- first run ---
counts = run()
check("first run creates one row per project (2)", counts["created"] == 2 and len(fake.rows()) == 2)
check("all dashboard columns were created in Notion",
      set(notion_sync.COLUMNS) <= set(fake.props) and "Linear ID" in fake.props)
a = atlas()
t = lambda name: fake._text(a, name)
check("name, milestone, owner, Linear ID filled",
      t("Name") == "Project Atlas" and t("Milestone") == "Sales handoff" and t("Owner") == "Bindu Singh"
      and t("Linear ID") == "p1")
check("status, health ('On track'), target date, source link filled",
      a["properties"]["Status"]["select"]["name"] == "Planned"
      and a["properties"]["Health"]["select"]["name"] == "On track"
      and a["properties"]["Target date"]["date"]["start"] == "2026-10-30"
      and a["properties"]["Source"]["url"] == "https://linear.app/x/project/atlas-1")
check("latest confirmed change comes from approvals", "TOS-5 due date: 2026-10-17 → 2026-10-24" in t("Latest confirmed change")
      and "bindu" in t("Latest confirmed change"))
check("open-risk count, top risk, suggested action and action owner come from risks",
      a["properties"]["Open risks"]["number"] == 1 and "Dependency risk" in t("Top risk")
      and t(notion_sync.NEXT_ACTION) == "Revise the TOS-4 schedule." and t("Action owner") == "Bindu Singh")
body = fake.body(a["id"])
check("page body has open risks and change history",
      body[0] == "Open risks" and any("Dependency risk" in b for b in body)
      and "Change history" in body and any("slack" in b and "due_date" in b for b in body))
quiet = next(p for p in fake.rows() if p["id"] != a["id"])
check("project with no risks: count 0 and 'No open risks.'",
      quiet["properties"]["Open risks"]["number"] == 0 and "No open risks." in fake.body(quiet["id"]))

# --- second run changes nothing ---
fake.calls.clear()
counts = run()
check("second run: nothing written, no duplicates", counts["unchanged"] == 2 and not fake.calls and len(fake.rows()) == 2)

# --- a real change updates the same row ---
c = db.get_connection()
c.execute("UPDATE projects SET health = 'offTrack' WHERE linear_id = 'p1'")
c.commit(); c.close()
counts = run()
check("changed project is updated in place (still 2 rows)",
      counts["updated"] == 1 and counts["unchanged"] == 1 and len(fake.rows()) == 2
      and atlas()["properties"]["Health"]["select"]["name"] == "Off track")

# --- rename: matched by Linear ID, not duplicated ---
c = db.get_connection()
c.execute("UPDATE projects SET name = 'Project Atlas (Site A)' WHERE linear_id = 'p1'")
c.commit(); c.close()
run()
check("renamed project updates the same row (no duplicate)",
      len(fake.rows()) == 2 and fake._text(atlas(), "Name") == "Project Atlas (Site A)")

# --- page deleted in Notion is recreated ---
fake.pages[atlas()["id"]]["archived"] = True
run()
check("a row deleted in Notion is recreated", len(fake.rows()) == 2)

# --- adopting a hand-made row with the same name and no Linear ID ---
fake2 = FakeNotion(); client2 = Client(fake2)
fake2.pages["manual"] = {"id": "manual", "archived": False,
                         "properties": {"Name": {"title": [{"plain_text": "Quiet Project"}]}}}
fake2.blocks["manual"] = []
notion_sync.sync_notion(client2, "db1")
check("existing same-name row without a Linear ID is adopted, not duplicated",
      len(fake2.rows()) == 2 and fake2._text(fake2.pages["manual"], "Linear ID") == "p2")

# --- one failure does not stop the others ---
fake3 = FakeNotion(); client3 = Client(fake3); fake3.fail_titles = {"Quiet Project"}
c = db.get_connection(); c.execute("DELETE FROM notion_sync_state"); c.commit(); c.close()
counts = notion_sync.sync_notion(client3, "db1")
check("a failing project is counted and the others still sync", counts["failed"] == 1 and counts["created"] == 1)

print("\nALL PASSED" if all(results) else "\nSOME FAILED")
raise SystemExit(0 if all(results) else 1)
