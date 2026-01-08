import logging

from simple_sender.utils.constants import STATUS_POLL_DEFAULT

logger = logging.getLogger(__name__)


def on_status_interval_change(app, _event=None):
    try:
        val = float(app.status_poll_interval.get())
    except Exception:
        val = app.settings.get("status_poll_interval", STATUS_POLL_DEFAULT)
    if val <= 0:
        val = STATUS_POLL_DEFAULT
    if val < 0.05:
        val = 0.05
    app.status_poll_interval.set(val)
    app._apply_status_poll_profile()


def on_status_failure_limit_change(app, _event=None):
    try:
        limit = int(app.status_query_failure_limit.get())
    except Exception:
        limit = app.settings.get("status_query_failure_limit", 3)
    if limit < 1:
        limit = 1
    if limit > 10:
        limit = 10
    app.status_query_failure_limit.set(limit)
    try:
        app.grbl.set_status_query_failure_limit(limit)
    except Exception as exc:
        logger.exception("Failed to set status failure limit: %s", exc)
