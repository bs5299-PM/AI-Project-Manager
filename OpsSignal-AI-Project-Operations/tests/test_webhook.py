"""Checks the webhook receiver's safety rules using fake events and a fake sync.
It never contacts Linear and never touches the real database.

Run:  python test_webhook.py
"""
import hashlib
import hmac
import json
import time

from webhook_server import create_app

SECRET = "test-secret"
calls = []


def fake_sync():
    calls.append(1)
    return {"projects": 1, "issues": 5, "changes": 0}


def sign(body, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def event(age_seconds=0, type_="Issue"):
    return json.dumps({
        "action": "update", "type": type_, "data": {"id": "x", "identifier": "TOS-5"},
        "webhookTimestamp": int((time.time() - age_seconds) * 1000),
    }).encode()


app = create_app(secret=SECRET, sync_fn=fake_sync)
client = app.test_client()
runner = app.config["sync_runner"]
results = []


def check(name, condition):
    results.append(condition)
    print(("PASS  " if condition else "FAIL  ") + name)


def post(body, signature=None):
    headers = {"Linear-Signature": signature} if signature is not None else {}
    return client.post("/linear-webhook", data=body, headers=headers, content_type="application/json")


body = event()
r = post(body)
check("unsigned request is rejected (401), no sync", r.status_code == 401 and not calls)

r = post(body, sign(body, "wrong-secret"))
check("wrong signature is rejected (401), no sync", r.status_code == 401 and not calls)

old = event(age_seconds=600)
r = post(old, sign(old))
check("correctly signed but old event is rejected (400), no sync", r.status_code == 400 and not calls)

r = post(body, sign(body))
runner.idle.wait(5)
check("correctly signed fresh event is accepted (200) and runs one sync", r.status_code == 200 and len(calls) == 1)

other = event(type_="Comment")
r = post(other, sign(other))
runner.idle.wait(5)
check("signed event of an unhandled type is accepted but runs no sync", r.status_code == 200 and len(calls) == 1)

print("\nALL PASSED" if all(results) else "\nSOME FAILED")
raise SystemExit(0 if all(results) else 1)
