import queue
import threading
import time

from simple_sender.ui import ui_queue as ui_queue_module
from simple_sender.ui.ui_queue import UiEventQueue, drain_ui_queue


class _App:
    def __init__(self) -> None:
        self.ui_q = queue.Queue()
        self.handled = []
        self.errors = []
        self.after_calls = []
        self.sync_calls = 0
        self.reconnect_calls = 0
        self.raise_sync = False
        self.raise_reconnect = False
        self.raise_after = False
        self._closing = False
        self._drain_ui_queue = lambda: None

    def _handle_evt(self, evt) -> None:
        self.handled.append(evt)
        if evt == "boom":
            raise ValueError("bad event")

    def _log_exception(self, msg: str, exc: Exception) -> None:
        self.errors.append((msg, type(exc)))

    def _maybe_auto_reconnect(self) -> None:
        self.reconnect_calls += 1
        if self.raise_reconnect:
            raise RuntimeError("reconnect failed")

    def _sync_tool_reference_label(self) -> None:
        self.sync_calls += 1
        if self.raise_sync:
            raise RuntimeError("sync failed")

    def after(self, delay: int, func) -> str:
        self.after_calls.append((delay, func))
        if self.raise_after:
            raise RuntimeError("after failed")
        return "after-id"


def test_drain_ui_queue_handles_events_and_schedules() -> None:
    app = _App()
    app.ui_q.put("one")
    app.ui_q.put("boom")
    app.ui_q.put("two")

    drain_ui_queue(app)

    assert app.handled == ["one", "boom", "two"]
    assert app.errors == [("UI event error", ValueError)]
    assert app.sync_calls == 1
    assert app.reconnect_calls == 1
    assert app.after_calls == [(50, app._drain_ui_queue)]


def test_drain_ui_queue_stops_when_closing() -> None:
    app = _App()
    app._closing = True
    app.ui_q.put("one")

    drain_ui_queue(app)

    assert app.handled == ["one"]
    assert app.after_calls == []
    assert app.reconnect_calls == 0


def test_drain_ui_queue_limits_processing() -> None:
    app = _App()
    for idx in range(101):
        app.ui_q.put(idx)

    drain_ui_queue(app)

    assert len(app.handled) == 100
    assert app.ui_q.qsize() == 1


def test_drain_ui_queue_reschedules_faster_when_backlog_remains() -> None:
    app = _App()
    for idx in range(220):
        app.ui_q.put(idx)

    drain_ui_queue(app)

    assert len(app.handled) == 100
    assert app.ui_q.qsize() == 120
    assert app.after_calls == [(15, app._drain_ui_queue)]


def test_ui_event_queue_caps_high_priority_log_entries() -> None:
    ui_q = UiEventQueue(maxsize=10, high_priority_log_maxsize=2, drop_notice_interval=0.0)
    ui_q.put(("log", "oldest"))
    ui_q.put(("conn", True, "COM9"))
    ui_q.put(("log", "middle"))
    ui_q.put(("log", "newest"))

    first = ui_q.get_nowait()
    second = ui_q.get_nowait()
    third = ui_q.get_nowait()

    assert first == ("conn", True, "COM9")
    assert second == ("log", "middle")
    assert third == ("log", "newest")
    assert ui_q.pop_drop_summary(now=1.0) == "[ui] Dropped 1 queued event(s): log=1"


def test_drain_ui_queue_logs_and_recovers_sync_hook_error() -> None:
    app = _App()
    app.raise_sync = True

    drain_ui_queue(app)

    assert app.sync_calls == 1
    assert app.reconnect_calls == 1
    assert app.after_calls == [(50, app._drain_ui_queue)]
    assert app.errors == [("UI tool-reference sync error", RuntimeError)]


def test_drain_ui_queue_logs_and_recovers_reconnect_hook_error() -> None:
    app = _App()
    app.raise_reconnect = True

    drain_ui_queue(app)

    assert app.sync_calls == 1
    assert app.reconnect_calls == 1
    assert app.after_calls == [(50, app._drain_ui_queue)]
    assert app.errors == [("UI auto-reconnect check error", RuntimeError)]


def test_drain_ui_queue_logs_reschedule_error() -> None:
    app = _App()
    app.raise_after = True

    drain_ui_queue(app)

    assert app.sync_calls == 1
    assert app.reconnect_calls == 1
    assert app.after_calls == [(50, app._drain_ui_queue)]
    assert app.errors == [("UI queue reschedule error", RuntimeError)]


def test_drain_ui_queue_throttles_idle_maintenance(monkeypatch) -> None:
    app = _App()
    times = iter((100.0, 100.1, 100.2, 100.5))
    app._ui_maintenance_interval_s = 0.25
    app._auto_reconnect_check_interval_s = 0.25

    monkeypatch.setattr(ui_queue_module.time, "monotonic", lambda: next(times))

    drain_ui_queue(app)
    drain_ui_queue(app)
    drain_ui_queue(app)
    drain_ui_queue(app)

    assert app.sync_calls == 2
    assert app.reconnect_calls == 2


def test_drain_ui_queue_uses_idle_specific_intervals_when_configured(monkeypatch) -> None:
    app = _App()
    times = iter((100.0, 100.3, 100.6, 101.1))
    app._ui_maintenance_interval_s = 0.25
    app._ui_maintenance_idle_interval_s = 1.0
    app._auto_reconnect_check_interval_s = 0.25
    app._auto_reconnect_check_idle_interval_s = 1.0

    monkeypatch.setattr(ui_queue_module.time, "monotonic", lambda: next(times))

    drain_ui_queue(app)
    drain_ui_queue(app)
    drain_ui_queue(app)
    drain_ui_queue(app)

    assert app.sync_calls == 2
    assert app.reconnect_calls == 2


def test_ui_event_queue_caps_critical_log_rx_entries() -> None:
    ui_q = UiEventQueue(maxsize=10, high_priority_log_maxsize=2, drop_notice_interval=0.0)
    ui_q.put(("log_rx", "ALARM:1"))
    ui_q.put(("log_rx", "error:2"))
    ui_q.put(("log_rx", "GRBL 1.1h"))

    first = ui_q.get_nowait()
    second = ui_q.get_nowait()

    assert first == ("log_rx", "error:2")
    assert second == ("log_rx", "GRBL 1.1h")
    assert ui_q.pop_drop_summary(now=1.0) == "[ui] Dropped 1 queued event(s): log_rx=1"


def test_ui_event_queue_caps_non_log_high_priority_entries() -> None:
    ui_q = UiEventQueue(
        maxsize=10,
        high_priority_maxsize=2,
        high_priority_log_maxsize=2,
        drop_notice_interval=0.0,
    )
    ui_q.put(("ready", True))
    ui_q.put(("conn", True, "COM1"))
    ui_q.put(("stream_state", "running", None))

    first = ui_q.get_nowait()
    second = ui_q.get_nowait()
    third = ui_q.get_nowait()

    assert first == ("ready", True)
    assert second == ("conn", True, "COM1")
    assert third == ("stream_state", "running", None)
    assert ui_q.pop_drop_summary(now=1.0) is None


def test_ui_event_queue_drops_non_lossless_high_priority_first() -> None:
    ui_q = UiEventQueue(
        maxsize=10,
        high_priority_maxsize=1,
        high_priority_log_maxsize=2,
        drop_notice_interval=0.0,
    )
    ui_q.put(("ready", True))
    ui_q.put(("manual_queue_drop", 1, 1))

    first = ui_q.get_nowait()

    assert first == ("ready", True)
    assert ui_q.empty() is True
    assert ui_q.pop_drop_summary(now=1.0) == "[ui] Dropped 1 queued event(s): manual_queue_drop=1"


def test_ui_event_queue_emergency_caps_lossless_high_priority_growth() -> None:
    ui_q = UiEventQueue(
        maxsize=1,
        high_priority_maxsize=1,
        high_priority_log_maxsize=1,
        drop_notice_interval=0.0,
    )
    for _ in range(6):
        ui_q.put(("ready", True))

    assert ui_q.qsize() == 4
    assert ui_q.pop_drop_summary(now=1.0) == "[ui] Dropped 2 queued event(s): ready=2"


def test_ui_event_queue_keeps_spindle_state_lossless_high_priority() -> None:
    ui_q = UiEventQueue(
        maxsize=1,
        high_priority_maxsize=1,
        high_priority_log_maxsize=1,
        drop_notice_interval=0.0,
    )
    ui_q.put(("stream_state", "running", None))
    ui_q.put(("spindle_state", True, 2))

    assert ui_q.get_nowait() == ("stream_state", "running", None)
    assert ui_q.get_nowait() == ("spindle_state", True, 2)


def test_ui_event_queue_blocking_put_waits_for_low_priority_slot() -> None:
    ui_q = UiEventQueue(maxsize=1, drop_notice_interval=0.0)
    ui_q.put(("log_tx", "first"))
    done = threading.Event()

    def _writer() -> None:
        ui_q.put(("log_tx", "second"), block=True, timeout=0.2)
        done.set()

    worker = threading.Thread(target=_writer)
    worker.start()
    time.sleep(0.01)

    assert done.is_set() is False
    assert ui_q.get_nowait() == ("log_tx", "first")

    worker.join(timeout=0.5)
    assert done.is_set() is True
    assert ui_q.get_nowait() == ("log_tx", "second")


def test_ui_event_queue_blocking_put_timeout_records_drop() -> None:
    ui_q = UiEventQueue(maxsize=1, drop_notice_interval=0.0)
    ui_q.put(("log_tx", "first"))

    ui_q.put(("log_tx", "second"), block=True, timeout=0.01)

    assert ui_q.get_nowait() == ("log_tx", "first")
    assert ui_q.empty() is True
    assert ui_q.pop_drop_summary(now=1.0) == "[ui] Dropped 1 queued event(s): log_tx=1"
