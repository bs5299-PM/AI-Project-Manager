"""Copies one row per project into a Notion database (the leadership dashboard).

Rows are matched by a Linear ID column first, then by project name for rows made before that column
existed, so a project is never duplicated, even after a rename. A row is only rewritten when its
content changed since the last run.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from db import get_connection, init_db

log = logging.getLogger("opssignal.webhook")

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2}
HEALTH_LABELS = {"ontrack": "On track", "atrisk": "At risk", "offtrack": "Off track"}
NEXT_ACTION = "Suggested next action (suggestion)"
LINEAR_ID = "Linear ID"
MAX_TEXT = 1900  # Notion rejects rich text over 2000 characters

# Every column except the project name (Notion's built-in title column).
COLUMNS = {
    "Milestone": {"rich_text": {}},
    "Status": {"select": {}},
    "Health": {"select": {}},
    "Owner": {"rich_text": {}},
    "Target date": {"date": {}},
    "Latest confirmed change": {"rich_text": {}},
    "Open risks": {"number": {}},
    "Top risk": {"rich_text": {}},
    NEXT_ACTION: {"rich_text": {}},
    "Action owner": {"rich_text": {}},
    "Source": {"url": {}},
    "Last synced": {"date": {}},
    LINEAR_ID: {"rich_text": {}},
}


def _text(value):
    return {"rich_text": [{"type": "text", "text": {"content": (value or "")[:MAX_TEXT]}}]} if value else {"rich_text": []}


def _pretty_ts(iso):
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC") if iso else ""


# ---------- reading our database ----------

def build_rows(conn):
    """One dict per project: the column values plus the page-body lines."""
    rows = []
    for p in [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY name")]:
        pid = p["project_id"]
        risks = [dict(r) for r in conn.execute(
            "SELECT * FROM risks WHERE project_id = ? AND status = 'open'", (pid,))]
        risks.sort(key=lambda r: (SEVERITY_RANK.get(r["severity"], 9), r["first_seen_at"]))
        top = risks[0] if risks else None

        last_change = conn.execute(
            """SELECT a.*, i.identifier FROM approvals a JOIN issues i ON i.issue_id = a.issue_id
               WHERE a.decision = 'confirmed' AND i.project_id = ?
               ORDER BY a.decided_at DESC LIMIT 1""", (pid,)).fetchone()
        change_text = ""
        if last_change:
            label = last_change["field_name"].replace("_", " ")
            change_text = (f"{last_change['identifier']} {label}: {last_change['old_value'] or 'none'} → "
                           f"{last_change['new_value']} (confirmed by {last_change['decided_by']}, "
                           f"{_pretty_ts(last_change['decided_at'])})")

        history = [dict(r) for r in conn.execute(
            """SELECT h.*, i.identifier FROM change_history h
               LEFT JOIN issues i ON i.issue_id = h.issue_id
               WHERE h.project_id = ? OR i.project_id = ?
               ORDER BY h.changed_at DESC, h.history_id DESC LIMIT 50""", (pid, pid))]
        history_lines = []
        for h in history:
            who = f", by {h['changed_by']}" if h["changed_by"] else ""
            item = h["identifier"] or "Project"
            history_lines.append(f"{_pretty_ts(h['changed_at'])} · {item} {h['field_name']}: "
                                 f"{h['old_value'] or 'none'} → {h['new_value'] or 'none'} ({h['source']}{who})")
        risk_lines = [f"[{r['severity']}] {r['title']}: {r['detail']} — Action: {r['required_action']} "
                      f"(owner: {r['action_owner']}, due: {r['action_due'] or 'none'})" for r in risks]

        rows.append({
            "linear_id": p["linear_id"], "name": p["name"], "url": p["url"],
            "values": {
                "Milestone": p["milestone"],
                "Status": p["status"],
                "Health": HEALTH_LABELS.get((p["health"] or "").replace(" ", "").lower(), p["health"]),
                "Owner": p["owner"],
                "Target date": p["target_date"],
                "Latest confirmed change": change_text,
                "Open risks": len(risks),
                "Top risk": f"{top['title']}: {top['detail']}" if top else "",
                NEXT_ACTION: top["required_action"] if top else "",
                "Action owner": top["action_owner"] if top else "",
            },
            "risk_lines": risk_lines, "history_lines": history_lines,
        })
    return rows


def _properties(row, title_name, available):
    """Notion property payload. Columns whose type in Notion does not match are skipped."""
    v = row["values"]
    props = {title_name: {"title": [{"type": "text", "text": {"content": row["name"]}}]}}
    builders = {
        "Milestone": lambda: _text(v["Milestone"]),
        "Status": lambda: {"select": {"name": v["Status"]} if v["Status"] else None},
        "Health": lambda: {"select": {"name": v["Health"]} if v["Health"] else None},
        "Owner": lambda: _text(v["Owner"]),
        "Target date": lambda: {"date": {"start": v["Target date"]} if v["Target date"] else None},
        "Latest confirmed change": lambda: _text(v["Latest confirmed change"]),
        "Open risks": lambda: {"number": v["Open risks"]},
        "Top risk": lambda: _text(v["Top risk"]),
        NEXT_ACTION: lambda: _text(v[NEXT_ACTION]),
        "Action owner": lambda: _text(v["Action owner"]),
        "Source": lambda: {"url": row["url"] or None},
        LINEAR_ID: lambda: _text(row["linear_id"]),
    }
    for name, build in builders.items():
        if name in available:
            props[name] = build()
    return props


def _bullet(text):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:MAX_TEXT]}}]}}


def _heading(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _paragraph(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def body_blocks(row):
    blocks = [_heading("Open risks")]
    blocks += [_bullet(t) for t in row["risk_lines"]] or [_paragraph("No open risks.")]
    blocks.append(_heading("Change history"))
    blocks += [_bullet(t) for t in row["history_lines"]] or [_paragraph("No changes recorded yet.")]
    return blocks[:100]


# ---------- talking to Notion ----------

def _resolve_data_source(client, database_id):
    """In the current Notion API a database holds one or more data sources; use the first."""
    sources = client.databases.retrieve(database_id=database_id).get("data_sources") or []
    if not sources:
        raise RuntimeError("That Notion database has no data source. Check NOTION_DATABASE_ID.")
    return sources[0]["id"]


def ensure_columns(client, data_source_id):
    """Add any missing columns. Returns (title column name, names of usable columns)."""
    existing = client.data_sources.retrieve(data_source_id=data_source_id)["properties"]
    title_name = next(n for n, p in existing.items() if p["type"] == "title")
    missing = {n: d for n, d in COLUMNS.items() if n not in existing}
    if missing:
        client.data_sources.update(data_source_id=data_source_id, properties=missing)
        log.info("  Notion: added columns %s", sorted(missing))
        existing = client.data_sources.retrieve(data_source_id=data_source_id)["properties"]
    usable = set()
    for name, definition in COLUMNS.items():
        kind = next(iter(definition))
        if existing.get(name, {}).get("type") == kind:
            usable.add(name)
        else:
            log.warning("  Notion: column %r has the wrong type (want %s); it will be skipped", name, kind)
    return title_name, usable


def _query_one(client, data_source_id, flt):
    return client.data_sources.query(data_source_id=data_source_id, filter=flt, page_size=5)["results"]


def _find_page(client, data_source_id, title_name, row, state_page_id):
    """Existing row for this project: remembered page, then Linear ID, then same name with no Linear ID."""
    if state_page_id:
        try:
            page = client.pages.retrieve(page_id=state_page_id)
            if not page.get("archived") and not page.get("in_trash"):
                return page["id"]
        except Exception:
            pass  # deleted in Notion; fall through and look again
    hits = _query_one(client, data_source_id, {"property": LINEAR_ID, "rich_text": {"equals": row["linear_id"]}})
    if hits:
        return hits[0]["id"]
    for page in _query_one(client, data_source_id, {"property": title_name, "title": {"equals": row["name"]}}):
        if not "".join(t["plain_text"] for t in page["properties"].get(LINEAR_ID, {}).get("rich_text", [])):
            return page["id"]
    return None


def _replace_body(client, page_id, blocks):
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        page = client.blocks.children.list(**kwargs)
        for b in page["results"]:
            client.blocks.delete(block_id=b["id"])
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
    client.blocks.children.append(block_id=page_id, children=blocks)


def sync_notion(client=None, database_id=None, conn=None, now=None):
    """Create or update one Notion row per project. Returns counts."""
    load_dotenv()
    if client is None:
        key = os.getenv("NOTION_API_KEY", "").strip()
        if not key:
            raise RuntimeError("NOTION_API_KEY is missing or empty in .env")
        from notion_client import Client
        client = Client(auth=key)
    database_id = database_id or os.getenv("NOTION_DATABASE_ID", "").strip()
    if not database_id:
        raise RuntimeError("NOTION_DATABASE_ID is missing or empty in .env")

    own_conn = conn is None
    conn = conn or get_connection()
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    try:
        init_db(conn)
        data_source_id = _resolve_data_source(client, database_id)
        title_name, usable = ensure_columns(client, data_source_id)
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = {r["linear_id"]: dict(r) for r in conn.execute("SELECT * FROM notion_sync_state")}

        for row in build_rows(conn):
            try:
                content = {"name": row["name"], "url": row["url"], "values": row["values"],
                           "risks": row["risk_lines"], "history": row["history_lines"]}
                digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
                known = state.get(row["linear_id"])
                page_id = _find_page(client, data_source_id, title_name, row, known["page_id"] if known else None)
                if page_id and known and known["page_id"] == page_id and known["content_hash"] == digest:
                    counts["unchanged"] += 1
                    continue
                props = _properties(row, title_name, usable)
                if "Last synced" in usable:
                    props["Last synced"] = {"date": {"start": now}}
                if page_id is None:
                    page_id = client.pages.create(parent={"type": "data_source_id", "data_source_id": data_source_id},
                                                  properties=props)["id"]
                    counts["created"] += 1
                else:
                    client.pages.update(page_id=page_id, properties=props)
                    counts["updated"] += 1
                _replace_body(client, page_id, body_blocks(row))
                with conn:
                    conn.execute(
                        """INSERT INTO notion_sync_state (linear_id, page_id, content_hash, synced_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(linear_id) DO UPDATE SET page_id=excluded.page_id,
                               content_hash=excluded.content_hash, synced_at=excluded.synced_at""",
                        (row["linear_id"], page_id, digest, now))
            except Exception as exc:
                counts["failed"] += 1
                log.error("  Notion: project %r FAILED: %s", row["name"], exc)
    finally:
        if own_conn:
            conn.close()
    log.info("NOTION SYNC: %s created, %s updated, %s unchanged, %s failed",
             counts["created"], counts["updated"], counts["unchanged"], counts["failed"])
    return counts


def run_if_configured():
    """For the after-sync hook: do nothing (quietly) until both Notion settings are in .env."""
    load_dotenv()
    if not (os.getenv("NOTION_API_KEY", "").strip() and os.getenv("NOTION_DATABASE_ID", "").strip()):
        log.info("Notion sync skipped: NOTION_API_KEY / NOTION_DATABASE_ID not set")
        return None
    return sync_notion()


if __name__ == "__main__":
    print(sync_notion())
