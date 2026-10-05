"""Read-only: prints what is in the database so you can verify a sync."""
from db import get_connection


def show(conn, title, sql):
    rows = conn.execute(sql).fetchall()
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    for r in rows:
        print(" | ".join(f"{k}={r[k]}" for k in r.keys()))


conn = get_connection()
print("Row counts:",
      conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], "projects,",
      conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0], "issues")
show(conn, "Projects", "SELECT * FROM projects")
show(conn, "Issues (first 15)", "SELECT * FROM issues ORDER BY issue_id LIMIT 15")
show(conn, "Issues blocked by another issue",
     """SELECT i.identifier AS issue, b.identifier AS blocked_by
        FROM issues i JOIN issues b ON b.issue_id = i.blocked_by_issue_id""")
show(conn, "Change history (newest first)",
     """SELECT h.history_id, COALESCE(i.identifier, p.name) AS item, h.field_name,
               h.old_value, h.new_value, h.changed_by, h.changed_at, h.source
        FROM change_history h
        LEFT JOIN issues i ON i.issue_id = h.issue_id
        LEFT JOIN projects p ON p.project_id = h.project_id
        ORDER BY h.history_id DESC""")
show(conn, "Approvals (newest first)",
     """SELECT a.approval_id, i.identifier AS issue, a.field_name, a.old_value, a.new_value,
               a.decision, a.decided_by, a.decided_at, a.source_message
        FROM approvals a LEFT JOIN issues i ON i.issue_id = a.issue_id
        ORDER BY a.approval_id DESC""")
show(conn, "Issues with no project", "SELECT identifier, title FROM issues WHERE project_id IS NULL LIMIT 10")
