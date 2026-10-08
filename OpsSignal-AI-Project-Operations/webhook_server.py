"""Receives Linear webhook events and triggers the existing sync.

Run:  python webhook_server.py
"""
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import date

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import notion_sync
import slack_routes
import digest as digest_module
from db import get_connection, init_db
import sync as sync_module
import webhook_handlers
from coordination import write_lock
from risk_engine import run_risk_check

MAX_EVENT_AGE_SECONDS = 60
LOG_FILE = os.path.join(os.path.dirname(__file__), "webhook.log")

log = logging.getLogger("opssignal.webhook")


def setup_logging():
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    for handler in (logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


class SyncRunner:
    """Runs the sync in the background, never two at once.
    If more events arrive during a sync, exactly one more sync runs afterwards."""

    def __init__(self, sync_fn, after_sync=None, after_unlocked=None):
        self._sync_fn = sync_fn
        self._after_sync = after_sync
        self._after_unlocked = after_unlocked  # slow work (Notion) that must not hold the lock
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        self.idle = threading.Event()
        self.idle.set()

    def request(self, reason):
        with self._lock:
            if self._running:
                self._again = True
                log.info("  sync already running; will run once more afterwards (%s)", reason)
                return
            self._running = True
            self.idle.clear()
        threading.Thread(target=self._loop, args=(reason,), daemon=True).start()

    def _loop(self, reason):
        while True:
            log.info("  sync started (%s)", reason)
            try:
                with write_lock:  # waits if a confirmed Slack change is being saved
                    r = self._sync_fn()
                    if self._after_sync:  # the risk check always runs on freshly synced data
                        try:
                            self._after_sync()
                        except Exception as exc:
                            log.error("  risk check FAILED: %s", exc)
                if self._after_unlocked:
                    try:
                        self._after_unlocked()
                    except Exception as exc:
                        log.error("  Notion sync FAILED: %s", exc)
                log.info("  sync finished: %s projects, %s issues, %s change(s) recorded",
                         r.get("projects"), r.get("issues"), r.get("changes"))
            except Exception as exc:  # keep the worker alive; the error is logged, not hidden
                log.error("  sync FAILED: %s", exc)
            with self._lock:
                if self._again:
                    self._again = False
                    reason = "events received during previous sync"
                    continue
                self._running = False
                self.idle.set()
                return


def signature_is_valid(secret, raw_body, header_value):
    if not secret or not header_value:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value.strip())


def _cron_authorized():
    """cron-job.org proves itself with the shared secret header."""
    expected = os.getenv("CRON_SECRET", "")
    provided = request.headers.get("X-Cron-Secret", "")
    return bool(expected) and hmac.compare_digest(provided, expected)


def create_app(secret=None, sync_fn=None, slack_blueprint=None, after_sync=None):
    setup_logging()
    if secret is None:
        load_dotenv()
        secret = os.getenv("LINEAR_WEBHOOK_SECRET", "").strip()
    if not secret:
        log.warning("LINEAR_WEBHOOK_SECRET is empty: every event will be rejected until it is set.")

    app = Flask(__name__)
    after_unlocked = None
    if sync_fn is None and after_sync is None:  # real run: after every real sync, check risks then update Notion
        after_sync = run_risk_check
        after_unlocked = notion_sync.run_if_configured
    runner = SyncRunner(sync_fn or sync_module.sync, after_sync, after_unlocked)
    app.config["sync_runner"] = runner
    context = {"sync_runner": runner}
    app.register_blueprint(slack_blueprint or slack_routes.create_slack_blueprint(slack_routes.load_settings()))

    @app.get("/health")
    def health():
        return jsonify(status="up")

    @app.post("/linear-webhook")
    def linear_webhook():
        raw = request.get_data()
        if not signature_is_valid(secret, raw, request.headers.get("Linear-Signature")):
            reason = "no signature" if not request.headers.get("Linear-Signature") else "bad signature"
            if not secret:
                reason = "server has no secret configured"
            log.info("REJECTED (%s) from %s", reason, request.remote_addr)
            return jsonify(error="invalid signature"), 401

        try:
            event = json.loads(raw)
        except ValueError:
            log.info("REJECTED (signed but not valid JSON)")
            return jsonify(error="invalid body"), 400

        sent_ms = event.get("webhookTimestamp")
        if not isinstance(sent_ms, (int, float)) or abs(time.time() * 1000 - sent_ms) > MAX_EVENT_AGE_SECONDS * 1000:
            log.info("REJECTED (timestamp missing or older than %ss) type=%s", MAX_EVENT_AGE_SECONDS, event.get("type"))
            return jsonify(error="stale or missing timestamp"), 400

        data = event.get("data") or {}
        what = data.get("identifier") or data.get("name") or data.get("id")
        log.info("EVENT %s %s %s", event.get("type"), event.get("action"), what)
        ran = webhook_handlers.dispatch(event, context)
        if not ran:
            log.info("  no handler for type %s; ignored", event.get("type"))
        return jsonify(status="received"), 200

    @app.post("/digest")
    def digest_endpoint():
        if not _cron_authorized():
            return jsonify(error="unauthorized"), 401
        week = date.today().isocalendar()
        week_key = f"{week[0]}-W{week[1]:02d}"
        conn = get_connection()
        try:
            init_db(conn)
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            row = conn.execute("SELECT value FROM meta WHERE key = 'digest_week'").fetchone()
            if row and row["value"] == week_key:
                log.info("DIGEST already sent for %s; skipping", week_key)
                return jsonify(status="already sent", week=week_key), 200
            text = digest_module.build_digest(conn)
            channel = digest_module.send_digest(text)
            with conn:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('digest_week', ?)", (week_key,))
            log.info("DIGEST sent for %s to %s", week_key, channel)
            return jsonify(status="sent", week=week_key, channel=channel), 200
        except Exception as exc:
            log.error("DIGEST FAILED: %s", exc)
            return jsonify(error=str(exc)), 500
        finally:
            conn.close()

    @app.post("/daily-check")
    def daily_check():
        if not _cron_authorized():
            return jsonify(error="unauthorized"), 401
        try:
            with write_lock:
                sync_counts = sync_module.sync()
                risk_counts = run_risk_check()
            notion = notion_sync.run_if_configured()
            log.info("DAILY-CHECK: sync=%s risks=%s notion=%s", sync_counts, risk_counts, notion)
            return jsonify(status="ok", sync=sync_counts,
                           risks={k: risk_counts[k] for k in ("open", "new", "reopened", "resolved", "alerts")},
                           notion=notion), 200
        except Exception as exc:
            log.error("DAILY-CHECK FAILED: %s", exc)
            return jsonify(error=str(exc)), 500

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000)
