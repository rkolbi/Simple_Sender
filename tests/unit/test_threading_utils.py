import queue
import threading

import pytest

from simple_sender.ui import threading_utils
from simple_sender.ui.events import router as event_router

pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self.ui_q = queue.Queue()
        self._closing = False
        self.errors = []

    def _log_exception(self, context: str, exc: Exception) -> None:
        self.errors.append((context, type(exc)))


def test_call_on_ui_thread_timeout_sets_cancel_token() -> None:
    app = _App()
    result = {}

    def _run() -> None:
        result["value"] = threading_utils.call_on_ui_thread(app, lambda: "ok", timeout=0.01)

    worker = threading.Thread(target=_run)
    worker.start()
    worker.join(timeout=1.0)

    assert result["value"] is None
    queued_call = app.ui_q.get_nowait()
    assert queued_call[0] == "ui_call"
    assert len(queued_call) == 6
    assert queued_call[5].is_set() is True
    assert app.ui_q.get_nowait() == ("log", "[ui] Action timed out.")


def test_handle_ui_call_skips_canceled_request() -> None:
    app = _App()
    ran = {"value": False}
    result_q = queue.Queue(maxsize=1)
    cancel_token = threading.Event()
    cancel_token.set()

    def _target() -> str:
        ran["value"] = True
        return "ok"

    event_router.handle_ui_call(
        app,
        _target,
        (),
        {},
        result_q,
        cancel_token=cancel_token,
    )

    assert ran["value"] is False
    assert result_q.empty() is True
    assert app.errors == []
