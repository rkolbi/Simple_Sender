import contextlib
import queue
import time

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import events as event_router

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Widget:
    def __init__(self) -> None:
        self.state = None
        self.text = ""

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]

    def cget(self, key: str) -> str:
        if key == "text":
            return self.text
        return ""


class _MacroExecutor:
    def __init__(self) -> None:
        self._vars = {"running": False, "paused": False}
        self.alarm_msg = None

    @contextlib.contextmanager
    def macro_vars(self):
        yield self._vars

    def notify_alarm(self, message: str) -> None:
        self.alarm_msg = message


class _SettingsController:
    def __init__(self) -> None:
        self.stream_lock = None

    def set_streaming_lock(self, locked: bool) -> None:
        self.stream_lock = locked


class _ToolpathPanel:
    def __init__(self) -> None:
        self.streaming = None
        self.reparsed = False

    def set_streaming(self, value: bool) -> None:
        self.streaming = value

    def reparse_lines(self, _lines, *, lines_hash=None) -> None:
        self.reparsed = True


def _make_app():
    class _App:
        def __init__(self) -> None:
            self._stream_state = "stopped"
            self._stream_start_ts = None
            self._stream_pause_total = 0.0
            self._stream_paused_at = None
            self._live_estimate_min = None
            self._toolpath_reparse_deferred = False
            self._last_gcode_lines = ["G0 X0"]
            self._gcode_hash = "hash"
            self.connected = True
            self._grbl_ready = True
            self._status_seen = True
            self._alarm_locked = False
            self._homing_in_progress = False
            self._homing_state_seen = False
            self._machine_state_text = "Idle"
            self._last_status_ts = time.time()
            self.status_poll_interval = _Var(0.2)
            self._connected_port = "COM3"
            self.gview = type("GView", (), {"lines_count": 1})()
            self.btn_run = _Widget()
            self.btn_pause = _Widget()
            self.btn_resume = _Widget()
            self.btn_resume_from = _Widget()
            self.btn_alarm_recover = _Widget()
            self.progress_pct = _Var(0)
            self.throughput_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.macro_executor = _MacroExecutor()
            self.settings_controller = _SettingsController()
            self.toolpath_panel = _ToolpathPanel()
            self._spindle_states: list[bool] = []
            self._kasa_stop_calls: list[str] = []

        def _refresh_gcode_stats_display(self) -> None:
            self._stats_refreshed = True

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_streaming_lock(self, locked: bool) -> None:
            self._streaming_lock = locked

        def _apply_status_poll_profile(self) -> None:
            self._status_profile_applied = True

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _handle_stream_spindle_state(self, is_on: bool) -> None:
            self._spindle_states.append(bool(is_on))

        def _stop_job_accessories(self, source: str) -> None:
            self._kasa_stop_calls.append(str(source))

    return _App()


def test_handle_streaming_validation_prompt_accepts(monkeypatch) -> None:
    app = type("App", (), {"_gcode_load_token": 10})()
    result_q = queue.Queue(maxsize=1)

    monkeypatch.setattr(event_router.messagebox, "askyesno", lambda _title, _msg: True)

    event_router.handle_streaming_validation_prompt(
        app,
        10,
        "job.nc",
        120,
        100,
        result_q,
    )

    assert result_q.get_nowait() is True


def test_handle_manual_queue_drop_updates_ui_state() -> None:
    app = _make_app()

    event_router.handle_event(app, ("manual_queue_drop", 2, 7))

    assert app._manual_queue_drop_total == 7
    assert app.status.text == "Manual queue full: dropped 7 command(s)."


def test_handle_spindle_state_routes_to_app_handler() -> None:
    app = _make_app()

    event_router.handle_event(app, ("spindle_state", True, 12))
    event_router.handle_event(app, ("spindle_state", False, None))

    assert app._spindle_states == [True, False]


def test_handle_stream_state_terminal_events_stop_job_accessories() -> None:
    app = _make_app()

    event_router.handle_stream_state_event(app, ("stream_state", "done", None))
    event_router.handle_stream_state_event(app, ("stream_state", "stopped", None))
    event_router.handle_stream_state_event(app, ("stream_state", "error", "oops"))
    event_router.handle_stream_state_event(app, ("stream_state", "alarm", "ALARM:1"))

    assert app._kasa_stop_calls == ["job_done", "job_stopped", "job_error", "job_alarm"]


def test_handle_gcode_load_invalid_reports_counts(monkeypatch) -> None:
    class _App:
        def __init__(self) -> None:
            self._gcode_load_token = 3
            self._gcode_loading = True
            self._gcode_validation_report = "old"
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.finished = False

        def _finish_gcode_loading(self) -> None:
            self.finished = True

    calls = {}

    def _showerror(title, msg):
        calls["title"] = title
        calls["msg"] = msg

    monkeypatch.setattr(event_router.messagebox, "showerror", _showerror)

    app = _App()

    event_router.handle_gcode_load_invalid(
        app,
        3,
        "job.nc",
        2,
        4,
        120,
        10,
        8,
    )

    assert app.finished is True
    assert app.gcode_stats_var.get() == "No file loaded"
    assert app.status.text == "G-code load failed"
    assert "non-empty" in calls["msg"]
    assert "File lines" in calls["msg"]


def test_handle_gcode_load_error_updates_status(monkeypatch) -> None:
    class _App:
        def __init__(self) -> None:
            self._gcode_load_token = 5
            self._gcode_loading = True
            self._gcode_validation_report = "old"
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.finished = False

        def _finish_gcode_loading(self) -> None:
            self.finished = True

    calls = {}

    def _showerror(_title, msg):
        calls["msg"] = msg

    monkeypatch.setattr(event_router.messagebox, "showerror", _showerror)

    app = _App()

    event_router.handle_gcode_load_error(app, 5, "job.nc", "boom")

    assert app.finished is True
    assert app.gcode_stats_var.get() == "No file loaded"
    assert app.status.text == "G-code load failed"
    assert "boom" in calls["msg"]


def test_handle_gcode_load_invalid_command_reports_system(monkeypatch) -> None:
    class _App:
        def __init__(self) -> None:
            self._gcode_load_token = 4
            self._gcode_loading = True
            self._gcode_validation_report = "old"
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.finished = False

        def _finish_gcode_loading(self) -> None:
            self.finished = True

    calls = {}

    def _showerror(_title, msg):
        calls["msg"] = msg

    monkeypatch.setattr(event_router.messagebox, "showerror", _showerror)

    app = _App()

    event_router.handle_gcode_load_invalid_command(app, 4, "job.nc", 4, "$H")

    assert app.finished is True
    assert app.gcode_stats_var.get() == "No file loaded"
    assert app.status.text == "G-code load failed"
    assert "system commands" in calls["msg"]
    assert "line 4" in calls["msg"]


def test_handle_gcode_load_error_clears_autolevel_restore(monkeypatch, tmp_path) -> None:
    temp_path = tmp_path / "job-AL.gcode"
    temp_path.write_text("G0 X0\n", encoding="utf-8")

    class _App:
        def __init__(self) -> None:
            self._gcode_load_token = 7
            self._gcode_loading = True
            self._gcode_validation_report = "old"
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.finished = False
            self._auto_level_restore = {
                "leveled_path": str(temp_path),
                "leveled_temp": True,
            }
            self._auto_level_leveled_lines = None
            self._auto_level_leveled_path = str(temp_path)
            self._auto_level_leveled_temp = True
            self._auto_level_leveled_name = temp_path.name

        def _finish_gcode_loading(self) -> None:
            self.finished = True

    monkeypatch.setattr(event_router.messagebox, "showerror", lambda *_args, **_kwargs: None)

    app = _App()

    event_router.handle_gcode_load_error(app, 7, "job.nc", "boom")

    assert app._auto_level_restore is None
    assert app._auto_level_leveled_path is None
    assert app._auto_level_leveled_temp is False
    assert not temp_path.exists()


def test_handle_status_event_updates_positions_and_leds() -> None:
    class _ToolpathPanel:
        def __init__(self) -> None:
            self.position = None

        def set_position(self, x, y, z) -> None:
            self.position = (x, y, z)

    class _App:
        def __init__(self) -> None:
            self._status_seen = False
            self._homing_in_progress = False
            self._homing_state_seen = False
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.mpos_rpm = _Var("0")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self._feed_override = None
            self._spindle_override = None
            self._override_refreshed = False
            self._led_state = None
            self._manual_enabled = None
            self._highlight = None

        def _update_state_highlight(self, text: str) -> None:
            self._highlight = text

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, value: int) -> None:
            self._feed_override = value

        def _set_spindle_override_slider_value(self, value: int) -> None:
            self._spindle_override = value

        def _refresh_override_info(self) -> None:
            self._override_refreshed = True

        def _update_led_panel(self, endstop: bool, probe: bool, hold: bool) -> None:
            self._led_state = (endstop, probe, hold)

    app = _App()

    event_router.handle_status_event(
        app,
        "<Idle|WPos:1.000,2.000,3.000|MPos:1.100,2.200,3.300|WCO:0.100,0.200,0.300|"
        "FS:0,0|Ov:110,100,120|Pn:PXH>",
    )

    assert app._status_seen is True
    assert app.wpos_x.get() == "1.000"
    assert app.mpos_z.get() == "3.300"
    assert app.mpos_rpm.get() == "0"
    assert app.toolpath_panel.position == (1.0, 2.0, 3.0)
    assert app._feed_override == 110
    assert app._spindle_override == 120
    assert app._override_refreshed is True
    assert app._led_state == (True, True, True)
    with app.macro_executor.macro_vars() as macro_vars:
        assert macro_vars["pins"] == "PXH"


def test_handle_status_event_skips_redundant_position_pushes() -> None:
    class _ToolpathPanel:
        def __init__(self) -> None:
            self.position = None
            self.set_position_calls = 0

        def set_position(self, x, y, z) -> None:
            self.position = (x, y, z)
            self.set_position_calls += 1

    class _App:
        def __init__(self) -> None:
            self._status_seen = False
            self._homing_in_progress = False
            self._homing_state_seen = False
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()

        def _update_state_highlight(self, _text: str) -> None:
            return None

        def _set_manual_controls_enabled(self, _enabled: bool) -> None:
            return None

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, _value: int) -> None:
            return None

        def _set_spindle_override_slider_value(self, _value: int) -> None:
            return None

        def _refresh_override_info(self) -> None:
            return None

        def _update_led_panel(self, _endstop: bool, _probe: bool, _hold: bool) -> None:
            return None

    app = _App()
    status = "<Idle|WPos:1.000,2.000,3.000|MPos:1.100,2.200,3.300|WCO:0.100,0.200,0.300|FS:0,0>"

    event_router.handle_status_event(app, status)
    event_router.handle_status_event(app, status)

    assert app.toolpath_panel.set_position_calls == 1


def test_handle_status_event_clears_homing_watchdog() -> None:
    class _ToolpathPanel:
        def __init__(self) -> None:
            self.position = None

        def set_position(self, x, y, z) -> None:
            self.position = (x, y, z)

    class _Grbl:
        def __init__(self) -> None:
            self.cleared = None

        def clear_watchdog_ignore(self, reason: str | None = None) -> None:
            self.cleared = reason

    class _App:
        def __init__(self) -> None:
            self._status_seen = False
            self._homing_in_progress = True
            self._homing_state_seen = True
            self._homing_start_ts = 0.0
            self._homing_timeout_s = 30.0
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self._feed_override = None
            self._spindle_override = None
            self._override_refreshed = False
            self._led_state = None
            self._manual_enabled = None
            self._highlight = None
            self.grbl = _Grbl()

        def _update_state_highlight(self, text: str) -> None:
            self._highlight = text

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, value: int) -> None:
            self._feed_override = value

        def _set_spindle_override_slider_value(self, value: int) -> None:
            self._spindle_override = value

        def _refresh_override_info(self) -> None:
            self._override_refreshed = True

        def _update_led_panel(self, endstop: bool, probe: bool, hold: bool) -> None:
            self._led_state = (endstop, probe, hold)

    app = _App()

    event_router.handle_status_event(
        app,
        "<Idle|WPos:1.000,2.000,3.000|MPos:1.100,2.200,3.300|WCO:0.100,0.200,0.300|"
        "FS:0,0|Ov:110,100,120|Pn:PXH>",
    )

    assert app._homing_in_progress is False
    assert app.grbl.cleared == "homing"


def test_handle_status_event_clears_homing_when_idle_persists_without_home_state() -> None:
    class _ToolpathPanel:
        def __init__(self) -> None:
            self.position = None

        def set_position(self, x, y, z) -> None:
            self.position = (x, y, z)

    class _Grbl:
        def __init__(self) -> None:
            self.cleared = None

        def clear_watchdog_ignore(self, reason: str | None = None) -> None:
            self.cleared = reason

    class _App:
        def __init__(self) -> None:
            self._status_seen = False
            self._homing_in_progress = True
            self._homing_state_seen = False
            self._homing_start_ts = time.time() - 2.0
            self._homing_timeout_s = 30.0
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self.status_poll_interval = _Var(0.2)
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self._feed_override = None
            self._spindle_override = None
            self._override_refreshed = False
            self._led_state = None
            self._manual_enabled = None
            self._highlight = None
            self.grbl = _Grbl()

        def _update_state_highlight(self, text: str) -> None:
            self._highlight = text

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, value: int) -> None:
            self._feed_override = value

        def _set_spindle_override_slider_value(self, value: int) -> None:
            self._spindle_override = value

        def _refresh_override_info(self) -> None:
            self._override_refreshed = True

        def _update_led_panel(self, endstop: bool, probe: bool, hold: bool) -> None:
            self._led_state = (endstop, probe, hold)

    app = _App()

    event_router.handle_status_event(
        app,
        "<Idle|WPos:1.000,2.000,3.000|MPos:1.100,2.200,3.300|WCO:0.100,0.200,0.300|"
        "FS:0,0|Ov:110,100,120|Pn:PXH>",
    )

    assert app._homing_in_progress is False
    assert app.machine_state.get() == "Idle"
    assert app.grbl.cleared == "homing"


def test_handle_status_event_keeps_homing_during_initial_idle_grace(monkeypatch) -> None:
    class _ToolpathPanel:
        def __init__(self) -> None:
            self.position = None

        def set_position(self, x, y, z) -> None:
            self.position = (x, y, z)

    class _Grbl:
        def __init__(self) -> None:
            self.cleared = None

        def clear_watchdog_ignore(self, reason: str | None = None) -> None:
            self.cleared = reason

    class _App:
        def __init__(self, start_ts: float) -> None:
            self._status_seen = False
            self._homing_in_progress = True
            self._homing_state_seen = False
            self._homing_start_ts = start_ts
            self._homing_timeout_s = 30.0
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self.status_poll_interval = _Var(0.2)
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self._feed_override = None
            self._spindle_override = None
            self._override_refreshed = False
            self._led_state = None
            self._manual_enabled = None
            self._highlight = None
            self.grbl = _Grbl()

        def _update_state_highlight(self, text: str) -> None:
            self._highlight = text

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, value: int) -> None:
            self._feed_override = value

        def _set_spindle_override_slider_value(self, value: int) -> None:
            self._spindle_override = value

        def _refresh_override_info(self) -> None:
            self._override_refreshed = True

        def _update_led_panel(self, endstop: bool, probe: bool, hold: bool) -> None:
            self._led_state = (endstop, probe, hold)

    fake_now = 12345.0
    monkeypatch.setattr(event_router._status.time, "time", lambda: fake_now)
    app = _App(start_ts=fake_now)

    event_router.handle_status_event(
        app,
        "<Idle|WPos:1.000,2.000,3.000|MPos:1.100,2.200,3.300|WCO:0.100,0.200,0.300|"
        "FS:0,0|Ov:110,100,120|Pn:PXH>",
    )

    assert app._homing_in_progress is True
    assert app.machine_state.get() == "Homing"
    assert app.grbl.cleared is None


def test_handle_stream_state_running_sets_controls() -> None:
    app = _make_app()

    event_router.handle_stream_state_event(app, ("stream_state", "running", None))

    assert app.btn_pause.state == "normal"
    assert app.btn_run.state == "disabled"
    assert app._streaming_lock
    with app.macro_executor.macro_vars() as macro_vars:
        assert macro_vars["running"] is True
        assert macro_vars["paused"] is False


def test_handle_stream_state_done_sets_buttons() -> None:
    app = _make_app()

    event_router.handle_stream_state_event(app, ("stream_state", "done", None))

    assert app.progress_pct.get() == 100
    assert app.btn_run.state == "normal"
    assert app.btn_resume_from.state == "normal"
    assert app.toolpath_panel.streaming is False


def test_handle_stream_state_done_defers_100pct_while_motion_active() -> None:
    app = _make_app()
    app._machine_state_text = "Run"

    event_router.handle_stream_state_event(app, ("stream_state", "done", None))

    assert app.progress_pct.get() == 99
    assert app._stream_done_pending_idle is True
    assert app._streaming_lock is True
    assert app.toolpath_panel.streaming is True


def test_handle_stream_state_done_defers_when_idle_status_is_stale() -> None:
    app = _make_app()
    app._machine_state_text = "Idle"
    app._last_status_ts = time.time() - 5.0
    app.status_poll_interval.set(0.2)

    event_router.handle_stream_state_event(app, ("stream_state", "done", None))

    assert app.progress_pct.get() == 99
    assert app._stream_done_pending_idle is True
    assert app._streaming_lock is True


def test_handle_stream_state_done_defers_when_machine_state_unknown() -> None:
    app = _make_app()
    app._machine_state_text = ""

    event_router.handle_stream_state_event(app, ("stream_state", "done", None))

    assert app.progress_pct.get() == 99
    assert app._stream_done_pending_idle is True
    assert app._streaming_lock is True


def test_handle_event_manual_error_updates_status() -> None:
    app = _make_app()
    app._homing_in_progress = True

    event_router.handle_event(app, ("manual_error", "error:2", "console"))

    assert "error:2" in app.status.text
    assert app._homing_in_progress is False


def test_handle_event_manual_error_shows_popup(monkeypatch) -> None:
    app = _make_app()
    calls: list[str] = []

    monkeypatch.setattr(
        event_router._router,
        "show_grbl_code_popup",
        lambda _app, message: calls.append(str(message)),
    )

    event_router.handle_event(app, ("manual_error", "error:2", "console"))

    assert calls
    assert "error:2" in calls[0]


def test_handle_event_manual_error_joystick_error15_shows_limit_hint() -> None:
    app = _make_app()
    app.joystick_event_status = _Var("")

    event_router.handle_event(app, ("manual_error", "error:15", "joystick"))

    assert "Jog blocked by travel limits" in app.status.text
    assert "error:15" in app.status.text
    assert "Jog blocked by travel limits" in app.joystick_event_status.get()


def test_handle_event_alarm_shows_popup(monkeypatch) -> None:
    app = _make_app()
    calls: list[str] = []

    monkeypatch.setattr(
        event_router._router,
        "show_grbl_code_popup",
        lambda _app, message: calls.append(str(message)),
    )

    event_router.handle_event(app, ("alarm", "ALARM:1"))

    assert calls
    assert "ALARM:1" in calls[0]


def test_handle_stream_state_paused_updates_controls() -> None:
    app = _make_app()
    app._stream_state = "running"

    event_router.handle_stream_state_event(app, ("stream_state", "paused", None))

    assert app.btn_pause.state == "disabled"
    assert app.btn_resume.state == "normal"
    assert app._streaming_lock is True
    assert app.settings_controller.stream_lock is True
    assert app.toolpath_panel.streaming is True
    with app.macro_executor.macro_vars() as macro_vars:
        assert macro_vars["running"] is True
        assert macro_vars["paused"] is True


def test_handle_stream_state_error_updates_status(monkeypatch) -> None:
    app = _make_app()
    called = {}
    monkeypatch.setattr(event_router, "job_controls_ready", lambda _app, *_args: True)
    monkeypatch.setattr(
        event_router,
        "set_run_resume_from",
        lambda _app, _ready: called.setdefault("resume", True),
    )

    event_router.handle_stream_state_event(app, ("stream_state", "error", "oops"))

    assert app.progress_pct.get() == 0
    assert "Stream error: oops" in app.status.text
    assert app.btn_pause.state == "disabled"
    assert app.btn_resume.state == "disabled"
    assert app._streaming_lock is False
    assert app.toolpath_panel.streaming is False
    assert called["resume"] is True


def test_handle_stream_state_alarm_sets_lock() -> None:
    app = _make_app()

    event_router.handle_stream_state_event(app, ("stream_state", "alarm", "ALARM:1"))

    assert app._alarm_locked is True
    assert "ALARM:1" in app._alarm_message
    assert app.btn_run.state == "disabled"
    assert app.btn_pause.state == "disabled"
    assert app.btn_resume.state == "disabled"
    assert app._streaming_lock is False


def test_handle_stream_interrupted_sets_resume_fields() -> None:
    app = _make_app()
    app._last_acked_index = 5
    app._last_gcode_path = "C:/jobs/test.nc"
    app._user_disconnect = False

    event_router.handle_stream_interrupted(app, ("stream_interrupted", True))

    assert app._resume_after_disconnect is True
    assert app._resume_from_index == 6
    assert app._resume_job_name == "test.nc"


def test_handle_event_log_rx_updates_units() -> None:
    class _SettingsController:
        def __init__(self) -> None:
            self.lines = []

        def handle_line(self, raw: str) -> None:
            self.lines.append(raw)

    class _StreamingController:
        def __init__(self) -> None:
            self.lines = []

        def handle_log_rx(self, raw: str) -> None:
            self.lines.append(raw)

    class _App:
        def __init__(self) -> None:
            self._modal_units = None
            self._report_units = None
            self._connected_port = "COM4"
            self.unit_mode = _Var("mm")
            self.status = _Widget()
            self.status.text = "Connected: COM4"
            self.macro_executor = _MacroExecutor()
            self.settings_controller = _SettingsController()
            self.streaming_controller = _StreamingController()
            self._unit_updates = 0
            self._dro_refreshed = 0

        def _set_unit_mode(self, _mode: str) -> None:
            self._unit_updates += 1

        def _update_unit_toggle_display(self) -> None:
            self._unit_updates += 1

        def _refresh_dro_display(self) -> None:
            self._dro_refreshed += 1

    app = _App()

    event_router.handle_event(app, ("log_rx", "[GC:G20 G90 M5]"))
    event_router.handle_event(app, ("log_rx", "$13=1 (report inches)"))

    assert app._modal_units == "inch"
    assert app._report_units == "inch"
    assert "Report: inch" in app.status.text
    assert app._unit_updates >= 1
    assert app._dro_refreshed == 1


def test_handle_event_unknown_event_logs_warning() -> None:
    class _StreamingController:
        def __init__(self) -> None:
            self.logs: list[str] = []

        def handle_log(self, text: str) -> None:
            self.logs.append(text)

    class _App:
        def __init__(self) -> None:
            self.errors: list[tuple[str, str]] = []
            self.streaming_controller = _StreamingController()

        def _log_exception(self, context: str, exc: Exception) -> None:
            self.errors.append((context, str(exc)))

    app = _App()

    event_router.handle_event(app, ("mystery", 123))

    assert app.errors
    assert app.errors[0][0] == "Unhandled UI event"
    assert "Unhandled UI event" in app.errors[0][1]
    assert app.streaming_controller.logs
    assert "Unhandled UI event" in app.streaming_controller.logs[0]
