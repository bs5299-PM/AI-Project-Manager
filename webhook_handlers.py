"""Says what to do for each kind of Linear event.

To add behaviour later (rules, alerts, Slack), register another function for an event type here;
the server code does not need to change.
"""
import logging

log = logging.getLogger("opssignal.webhook")

# event type (Linear's "type" field) -> function(event, context)
HANDLERS = {}


def register(*event_types):
    def decorator(fn):
        for t in event_types:
            HANDLERS.setdefault(t, []).append(fn)
        return fn
    return decorator


@register("Issue", "Project", "ProjectUpdate", "IssueRelation")
def refresh_from_linear(event, context):
    """Any change to these means our copy may be stale, so ask for a sync."""
    context["sync_runner"].request(f"{event.get('type')}/{event.get('action')}")


def dispatch(event, context):
    """Run every handler registered for this event's type. Returns how many ran."""
    handlers = HANDLERS.get(event.get("type"), [])
    for fn in handlers:
        fn(event, context)
    return len(handlers)
