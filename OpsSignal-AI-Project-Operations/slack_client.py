"""Talks to Slack: posts and updates the approval cards. The bot token is never logged."""
import logging

import requests

log = logging.getLogger("opssignal.webhook")

SLACK_API = "https://slack.com/api/"
FIELD_LABELS = {"due_date": "Due date", "priority": "Priority"}


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quote(text):
    return "\n".join("> " + line for line in _esc(text).splitlines()[:6])


def _change_text(a):
    label = FIELD_LABELS.get(a["field_name"], a["field_name"])
    return (f"*{_esc(a['identifier'])}* · {_esc(a['title'])}\n"
            f"*{label}:* `{_esc(a['old_value'] or 'none')}` → `{_esc(a['new_value'])}`")


def _source(a):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": "From Slack:\n" + _quote(a["source_message"])}]}


def proposal_blocks(a):
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Proposed change* — needs approval\n" + _change_text(a)}},
        _source(a),
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "approval_confirm", "style": "primary",
             "text": {"type": "plain_text", "text": "Confirm"}, "value": str(a["approval_id"])},
            {"type": "button", "action_id": "approval_reject", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}, "value": str(a["approval_id"])},
        ]},
    ]


def clarification_blocks(a, reason):
    text = (f"*I'm not sure what change you mean.* {_esc(reason)}\n"
            "Please name the issue (for example TOS-5), the field (due date or priority) and the new value. "
            "No change was made.")
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}, _source(a)]


def decided_blocks(a, status_line):
    """The card after a decision: same details, buttons replaced by the outcome."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Proposed change*\n" + _change_text(a)}},
        _source(a),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": status_line}]},
    ]


class SlackClient:
    def __init__(self, token):
        self._token = token

    def _call(self, method, payload):
        resp = requests.post(SLACK_API + method, json=payload, timeout=15,
                             headers={"Authorization": f"Bearer {self._token}"})
        body = resp.json()
        if not body.get("ok"):
            log.error("  Slack %s failed: %s", method, body.get("error"))
        return body

    def post_message(self, channel, blocks, text):
        return self._call("chat.postMessage", {"channel": channel, "blocks": blocks, "text": text})

    def post_text(self, channel, text):
        return self._call("chat.postMessage", {"channel": channel, "text": text})

    def update_message(self, channel, ts, blocks, text):
        return self._call("chat.update", {"channel": channel, "ts": ts, "blocks": blocks, "text": text})

    def post_ephemeral(self, channel, user, text):
        return self._call("chat.postEphemeral", {"channel": channel, "user": user, "text": text})
