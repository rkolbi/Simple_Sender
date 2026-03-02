import pytest
import time

pytest.importorskip("tkinter")

from simple_sender.streaming_controller import StreamingController
from simple_sender.utils.constants import (
    CONSOLE_PENDING_BATCH_MAX,
    MAX_CONSOLE_LINES,
)

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _GView:
    def __init__(self) -> None:
        self.sent = None
        self.acked = None

    def mark_sent_upto(self, idx: int) -> None:
        self.sent = idx

    def mark_acked_upto(self, idx: int) -> None:
        self.acked = idx


class _App:
    def __init__(self) -> None:
        self.console_positions_enabled = _Var(True)
        self.performance_mode = _Var(False)
        self.gui_logging_enabled = _Var(True)
        self._stream_state = "idle"
        self._machine_state_text = "Idle"
        self._last_status_ts = time.time()
        self.status_poll_interval = _Var(0.2)
        self.connected = True
        self._status_seen = True
        self._ui_throttle_ms = 0
        self._last_sent_index = -1
        self._last_acked_index = -1
        self._update_calls = []
        self._notify_calls = []
        self._after_cancels = []

    def after(self, _delay_ms: int, func):
        func()
        return None

    def after_cancel(self, _after_id) -> None:
        self._after_cancels.append(_after_id)
        return None

    def _update_current_highlight(self) -> None:
        return None

    def _update_live_estimate(self, done: int, total: int) -> None:
        self._update_calls.append((done, total))

    def _maybe_notify_job_completion(self, done: int, total: int) -> None:
        self._notify_calls.append((done, total))

    def _format_throughput(self, bps: float) -> str:
        return f"TX: {bps:.0f} B/s"


class _Console:
    def __init__(self) -> None:
        self.state = "normal"
        self.lines: list[str] = []

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]

    def insert(self, _pos: str, text: str, _tags=()) -> None:
        self.lines.append(text.rstrip("\n"))

    def see(self, _pos: str) -> None:
        return None

    def delete(self, start: str, end: str) -> None:
        if start == "1.0" and end == "end":
            self.lines = []
            return
        try:
            count = int(end.split(".")[0]) - 1
        except (ValueError, IndexError):
            self.lines = []
            return
        if count > 0:
            self.lines = self.lines[count:]


class _Widget:
    def __init__(self, text: str = "", state: str = "normal", name: str = "btn") -> None:
        self._text = text
        self._state = state
        self._name = name

    def cget(self, key: str) -> str:
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        raise KeyError(key)

    def winfo_name(self) -> str:
        return self._name


class _Event:
    def __init__(self, widget) -> None:
        self.widget = widget


def test_handle_buffer_fill_updates_vars() -> None:
    app = _App()
    controller = StreamingController(app)
    buffer_fill = _Var("")
    buffer_fill_pct = _Var(0)
    controller.attach_widgets(None, _GView(), _Var(0), buffer_fill, buffer_fill_pct, _Var(""))

    controller.handle_buffer_fill(50, 10, 20)

    assert buffer_fill.value == "Buffer: 50% (10/20)"
    assert buffer_fill_pct.value == 50


def test_handle_progress_updates_estimate() -> None:
    app = _App()
    controller = StreamingController(app)
    progress_pct = _Var(0)
    controller.attach_widgets(None, _GView(), progress_pct, _Var(""), _Var(0), _Var(""))

    controller.handle_progress(5, 10)

    assert progress_pct.value == 50
    assert app._update_calls == [(5, 10)]
    assert app._notify_calls == [(5, 10)]


def test_handle_progress_defers_completion_until_idle() -> None:
    app = _App()
    app._machine_state_text = "Run"
    controller = StreamingController(app)
    progress_pct = _Var(0)
    controller.attach_widgets(None, _GView(), progress_pct, _Var(""), _Var(0), _Var(""))

    controller.handle_progress(10, 10)

    assert progress_pct.value == 99
    assert app._update_calls == []
    assert app._notify_calls == []


def test_handle_progress_defers_completion_when_idle_status_is_stale() -> None:
    app = _App()
    app._machine_state_text = "Idle"
    app._last_status_ts = time.time() - 5.0
    controller = StreamingController(app)
    progress_pct = _Var(0)
    controller.attach_widgets(None, _GView(), progress_pct, _Var(""), _Var(0), _Var(""))

    controller.handle_progress(10, 10)

    assert progress_pct.value == 99
    assert app._update_calls == []
    assert app._notify_calls == []


def test_handle_progress_defers_completion_when_machine_state_unknown() -> None:
    app = _App()
    app._machine_state_text = ""
    controller = StreamingController(app)
    progress_pct = _Var(0)
    controller.attach_widgets(None, _GView(), progress_pct, _Var(""), _Var(0), _Var(""))

    controller.handle_progress(10, 10)

    assert progress_pct.value == 99
    assert app._update_calls == []
    assert app._notify_calls == []


def test_handle_gcode_marks_updates_gview() -> None:
    app = _App()
    gview = _GView()
    app.gview = gview
    controller = StreamingController(app)
    controller.attach_widgets(None, gview, _Var(0), _Var(""), _Var(0), _Var(""))

    controller.handle_gcode_sent(3)
    controller.handle_gcode_acked(2)

    assert gview.sent == 3
    assert gview.acked == 2
    assert app._last_sent_index == 3
    assert app._last_acked_index == 2


def test_log_skips_position_lines_when_disabled() -> None:
    app = _App()
    app.console_positions_enabled = _Var(False)
    controller = StreamingController(app)

    controller.log("<< <Idle|WPos:0.000,0.000,0.000>")

    assert controller.get_console_lines() == []


def test_handle_log_rx_suppresses_status_when_performance_mode() -> None:
    app = _App()
    app.performance_mode = _Var(True)
    app._stream_state = "running"
    controller = StreamingController(app)

    controller.handle_log_rx("<Idle|WPos:0,0,0>")

    assert controller.get_console_lines() == []


def test_set_console_filter_renders_filtered_lines() -> None:
    app = _App()
    controller = StreamingController(app)
    console = _Console()
    controller.attach_widgets(console, _GView(), _Var(0), _Var(""), _Var(0), _Var(""))
    controller._console_lines = [
        ("ALARM:1", "console_alarm"),
        ("ERROR:2", "console_error"),
        ("OK", "console_ok"),
    ]

    controller.set_console_filter("alarms")

    assert console.lines == ["ALARM:1"]


def test_handle_log_rx_keeps_alarms_in_performance_mode() -> None:
    app = _App()
    app.performance_mode = _Var(True)
    app._stream_state = "running"
    controller = StreamingController(app)
    console = _Console()
    controller.attach_widgets(console, _GView(), _Var(0), _Var(""), _Var(0), _Var(""))

    controller.handle_log_rx("ALARM:1")

    assert console.lines == ["<< ALARM:1"]


def test_on_button_press_logs_tip_and_gcode(monkeypatch) -> None:
    app = _App()
    controller = StreamingController(app)
    widget = _Widget(text="Run", name="run_button")
    widget._tooltip_text = "Start job"
    widget._log_gcode_get = "G0 X0"

    monkeypatch.setattr("simple_sender.streaming_controller.time.strftime", lambda _fmt: "12:34:56")

    controller._on_button_press(_Event(widget))

    assert controller.get_console_lines() == [
        ("[12:34:56] Button: Run | Tip: Start job | GCode: G0 X0", None)
    ]


def test_flush_gcode_marks_updates_last_sent_from_acked() -> None:
    app = _App()
    gview = _GView()
    app.gview = gview
    app._last_sent_index = 2
    controller = StreamingController(app)
    controller._pending_acked_index = 5

    controller._flush_gcode_marks()

    assert gview.acked == 5
    assert app._last_acked_index == 5
    assert app._last_sent_index == 5


def test_clear_pending_ui_updates_cancels_after_handles() -> None:
    app = _App()
    controller = StreamingController(app)
    controller._pending_marks_after_id = "marks"
    controller._progress_after_id = "progress"
    controller._buffer_after_id = "buffer"
    controller._console_after_id = "console"
    controller._pending_console_entries = [("line", None)]
    controller._pending_console_trim = 2
    controller._console_render_pending = True

    controller.clear_pending_ui_updates()

    assert app._after_cancels == ["marks", "progress", "buffer", "console"]
    assert controller._pending_console_entries == []
    assert controller._pending_console_trim == 0
    assert controller._console_render_pending is False


def test_matches_filter_skips_positions_for_save() -> None:
    app = _App()
    controller = StreamingController(app)

    assert controller.matches_filter(("<< <Idle|WPos:0,0,0>", None), for_save=True) is False


def test_log_caps_console_history_without_list_slicing() -> None:
    app = _App()
    controller = StreamingController(app)

    for idx in range(MAX_CONSOLE_LINES + 5):
        controller.log(f"line {idx}")

    lines = controller.get_console_lines()
    assert len(lines) == MAX_CONSOLE_LINES
    assert lines[0] == ("line 5", None)


def test_log_caps_pending_console_entries_in_performance_mode() -> None:
    app = _App()
    app.performance_mode = _Var(True)
    controller = StreamingController(app)
    controller._schedule_console_flush = lambda: None

    for idx in range(int(CONSOLE_PENDING_BATCH_MAX) + 5):
        controller.log(f"line {idx}")

    assert controller._console_render_pending is True
    assert len(controller._pending_console_entries) < int(CONSOLE_PENDING_BATCH_MAX)


def test_log_caps_console_history_by_bytes(monkeypatch) -> None:
    app = _App()
    controller = StreamingController(app)
    monkeypatch.setattr("simple_sender.streaming_controller.CONSOLE_MAX_BUFFER_BYTES", 24)

    controller.log("AAAAAAAAAA")
    controller.log("BBBBBBBBBB")
    controller.log("CCCCCCCCCC")

    lines = controller.get_console_lines()
    assert [line for line, _tag in lines] == ["BBBBBBBBBB", "CCCCCCCCCC"]
