"""The two doors Slack calls: /slack-events (messages) and /slack-interactive (button clicks)."""
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qs

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

import approvals
import linear_client
import proposals
import slack_client
from db import get_connection

log = logging.getLogger("opssignal.webhook")

MAX_REQUEST_AGE_SECONDS = 300


@dataclass
class SlackSettings:
    signing_secret: str = ""
    bot_token: str = ""
    allowed_channels: set = field(default_factory=set)
    approver_ids: set = field(default_factory=set)


def _csv(value):
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def load_settings():
    load_dotenv()
    return SlackSettings(
        signing_secret=os.getenv("SLACK_SIGNING_SECRET", "").strip(),
        bot_token=os.getenv("SLACK_BOT_TOKEN", "").strip(),
        allowed_channels=_csv(os.getenv("SLACK_ALLOWED_CHANNELS")),
        approver_ids=_csv(os.getenv("SLACK_APPROVER_IDS")),
    )


def signature_is_valid(secret, timestamp, signature, raw_body, now=None):
    """Slack signs 'v0:<timestamp>:<body>' with the signing secret."""
    if not (secret and timestamp and signature):
        return False
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > MAX_REQUEST_AGE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _run_in_thread(fn, *args):
    def runner():
        try:
            fn(*args)
        except Exception:
            log.exception("  background Slack work FAILED")
    threading.Thread(target=runner, daemon=True).start()


def create_slack_blueprint(settings, claude_fn=None, slack=None, linear=None, run_async=None):
    """Dependencies can be swapped (tests do) so nothing real is called."""
    claude_fn = claude_fn or proposals.ask_claude
    slack = slack or slack_client.SlackClient(settings.bot_token)
    linear = linear or linear_client
    run_async = run_async or _run_in_thread
    bp = Blueprint("slack", __name__)
    seen_events = deque(maxlen=500)

    if not settings.signing_secret:
        log.warning("SLACK_SIGNING_SECRET is empty: every Slack request will be rejected until it is set.")
    if not settings.allowed_channels:
        log.warning("SLACK_ALLOWED_CHANNELS is empty: no Slack message will be processed.")
    if not settings.approver_ids:
        log.warning("SLACK_APPROVER_IDS is empty: nobody can Confirm or Reject.")

    def verified(raw):
        return signature_is_valid(settings.signing_secret,
                                  request.headers.get("X-Slack-Request-Timestamp"),
                                  request.headers.get("X-Slack-Signature"), raw)

    # ---- a message arrives -> proposal card ----
    def process_message(event):
        text, channel = event["text"], event["channel"]
        log.info("SLACK MESSAGE channel=%s user=%s text=%r", channel, event.get("user"), text)
        conn = get_connection()
        try:
            outcome = proposals.propose(conn, text, claude_fn)
            approval_id = approvals.save_outcome(conn, outcome, text)
            a = approvals.load(conn, approval_id)
        finally:
            conn.close()
        if outcome.proposal:
            blocks = slack_client.proposal_blocks(a)
            fallback = f"Proposed change to {a['identifier']}: {a['field_name']} {a['old_value']} -> {a['new_value']}"
        else:
            blocks = slack_client.clarification_blocks(a, outcome.reason)
            fallback = "I'm not sure what change you mean."
        slack.post_message(channel, blocks, fallback)
        log.info("  approval #%s saved as %s; card posted", approval_id, a["decision"])

    @bp.post("/slack-events")
    def slack_events():
        raw = request.get_data()
        if not verified(raw):
            log.info("REJECTED Slack events request (bad or missing signature)")
            return jsonify(error="invalid signature"), 401
        body = json.loads(raw)
        if body.get("type") == "url_verification":
            return jsonify(challenge=body.get("challenge"))
        if request.headers.get("X-Slack-Retry-Num"):
            log.info("SLACK retry ignored (already handled): %s", body.get("event_id"))
            return "", 200
        event = body.get("event") or {}
        if body.get("event_id") in seen_events:
            return "", 200
        seen_events.append(body.get("event_id"))

        if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
            return "", 200  # edits, joins, and our own cards are not requests
        if event.get("channel") not in settings.allowed_channels:
            log.info("SLACK message ignored: channel %s is not approved", event.get("channel"))
            return "", 200
        if not (event.get("text") or "").strip():
            return "", 200
        run_async(process_message, event)
        return "", 200

    # ---- a button is clicked -> decision ----
    def process_click(payload):
        action = payload["actions"][0]
        user = payload["user"]
        who = user.get("name") or user.get("username") or user["id"]
        channel, ts = payload["channel"]["id"], payload["message"]["ts"]
        approval_id = int(action["value"])
        log.info("SLACK CLICK %s on approval #%s by %s (%s)", action["action_id"], approval_id, who, user["id"])

        if user["id"] not in settings.approver_ids:
            log.info("  refused: %s is not an approver", user["id"])
            slack.post_ephemeral(channel, user["id"], "Only a designated approver can confirm or reject changes.")
            return

        if action["action_id"] == "approval_confirm":
            result = approvals.confirm(approval_id, who, linear)
        else:
            result = approvals.reject(approval_id, who)
        log.info("  result: %s %s", result.status, result.detail)

        a = result.approval
        if result.status == "confirmed":
            slack.update_message(channel, ts, slack_client.decided_blocks(
                a, f":white_check_mark: *Confirmed* by {who} — Linear updated"), "Change confirmed")
        elif result.status == "rejected":
            slack.update_message(channel, ts, slack_client.decided_blocks(
                a, f":x: *Rejected* by {who} — no change made"), "Change rejected")
        elif result.status == "stale":
            slack.update_message(channel, ts, slack_client.decided_blocks(
                a, f":warning: *Out of date* — {result.detail}. No change was made. Send a new message."),
                "Proposal out of date")
        elif result.status == "already_decided":
            slack.post_ephemeral(channel, user["id"], f"That proposal was already {a['decision']}.")
        elif result.status == "failed":
            slack.post_ephemeral(channel, user["id"],
                                 f"Linear did not accept the change ({result.detail}). The proposal is still pending.")
        else:
            slack.post_ephemeral(channel, user["id"], "I could not find that proposal.")

    @bp.post("/slack-interactive")
    def slack_interactive():
        raw = request.get_data()
        if not verified(raw):
            log.info("REJECTED Slack interactive request (bad or missing signature)")
            return jsonify(error="invalid signature"), 401
        payload = json.loads(parse_qs(raw.decode()).get("payload", ["{}"])[0])
        if payload.get("type") != "block_actions" or not payload.get("actions"):
            return "", 200
        if payload["actions"][0].get("action_id") not in ("approval_confirm", "approval_reject"):
            return "", 200
        run_async(process_click, payload)
        return "", 200

    return bp
