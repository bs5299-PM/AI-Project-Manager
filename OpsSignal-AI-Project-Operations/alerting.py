"""Sends one Slack alert per new or materially changed risk and logs every attempt in the alerts table."""
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from slack_client import SlackClient

log = logging.getLogger("opssignal.webhook")

PENDING_RISKS = """
SELECT r.*, i.identifier, p.name AS project_name
FROM risks r
LEFT JOIN issues i ON i.issue_id = r.issue_id
LEFT JOIN projects p ON p.project_id = r.project_id
WHERE r.status = 'open'
  AND NOT EXISTS (SELECT 1 FROM alerts a
                  WHERE a.risk_id = r.risk_id AND a.result = 'sent' AND a.sent_at >= r.last_alert_at)
ORDER BY r.first_seen_at
"""


def _pretty(iso):
    if not iso:
        return "-"
    from datetime import date
    d = date.fromisoformat(iso[:10])
    return f"{d:%B} {d.day}"


def format_alert(r, updated=False):
    """The spec's Slack alert format; a re-alert is marked as updated."""
    source = f"Linear issue {r['identifier']}" if r["identifier"] else "Linear project"
    return "\n".join([
        f"*{r['project_name'] or 'No project'}: {r['title']}*" + (" (updated)" if updated else ""),
        f"Detected: {r['detail']}",
        f"Impact: {r['impact']}",
        f"Required action: {r['required_action']}",
        f"Action owner: {r['action_owner']}",
        f"Action due: {_pretty(r['action_due'])}",
        f"Source: {source}",
    ])


def send_pending_alerts(conn, slack=None, channel=None, now=None):
    """One alert for each open risk that has not been alerted since its last_alert_at. Returns how many were sent."""
    load_dotenv()
    channel = channel or os.getenv("ALERT_CHANNEL", "").strip()
    slack = slack or SlackClient(os.getenv("SLACK_BOT_TOKEN", "").strip())
    sent = 0
    for r in [dict(x) for x in conn.execute(PENDING_RISKS)]:
        updated = conn.execute("SELECT 1 FROM alerts WHERE risk_id=? AND result='sent' LIMIT 1",
                               (r["risk_id"],)).fetchone() is not None
        message = format_alert(r, updated)
        error = None
        if not channel:
            error = "ALERT_CHANNEL is empty in .env"
        else:
            try:
                resp = slack.post_message(channel, [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
                                          f"{r['title']}: {r['detail']}")
                if not (resp and resp.get("ok")):
                    error = (resp or {}).get("error", "no response from Slack")
            except Exception as exc:
                error = str(exc)
        when = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with conn:
            conn.execute("INSERT INTO alerts (risk_id, sent_at, channel, message, result, error) VALUES (?,?,?,?,?,?)",
                         (r["risk_id"], when, channel, message, "failed" if error else "sent", error))
        if error:
            log.error("  ALERT FAILED for %s: %s (will retry on the next check)", r["risk_id"], error)
        else:
            sent += 1
            log.info("  ALERT sent for %s to %s", r["risk_id"], channel)
    return sent
