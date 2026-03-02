import pytest

from simple_sender.ui import tooltip_policy

pytestmark = pytest.mark.ui


class _Widget:
    def __init__(self):
        self._disabled_reason = None


def _resolve_owner(app):
    def resolver(_widget, _attr):
        return app

    return resolver


def test_resolve_disabled_reason_prefers_widget_reason() -> None:
    widget = _Widget()
    widget._disabled_reason = "Nope"

    reason = tooltip_policy.resolve_disabled_reason(widget, lambda _w, _attr: None)

    assert reason == "Nope"


def test_resolve_disabled_reason_without_app_returns_none() -> None:
    reason = tooltip_policy.resolve_disabled_reason(_Widget(), lambda _w, _attr: None)

    assert reason is None


def test_resolve_disabled_reason_manual_controls_requires_connect() -> None:
    class _App:
        connected = False
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_state = "idle"
        _gcode_loading = False

    widget = _Widget()
    app = _App()
    app._manual_controls = [widget]

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Connect to enable."


def test_resolve_disabled_reason_open_button_while_streaming() -> None:
    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_state = "running"
        _gcode_loading = False

    widget = _Widget()
    app = _App()
    app.btn_open = widget

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Stop the stream to load a new job."


def test_resolve_disabled_reason_run_button_needs_job() -> None:
    class _Gview:
        lines_count = 0

    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_state = "idle"
        _gcode_loading = False
        gview = _Gview()

    widget = _Widget()
    app = _App()
    app.btn_run = widget

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Load a job to enable."


def test_resolve_disabled_reason_pause_and_resume_states() -> None:
    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_state = "idle"
        _gcode_loading = False

    pause_btn = _Widget()
    resume_btn = _Widget()
    app = _App()
    app.btn_pause = pause_btn
    app.btn_resume = resume_btn

    pause_reason = tooltip_policy.resolve_disabled_reason(pause_btn, _resolve_owner(app))
    resume_reason = tooltip_policy.resolve_disabled_reason(resume_btn, _resolve_owner(app))

    assert pause_reason == "Start a job to enable."
    assert resume_reason == "Pause a job to enable."


def test_resolve_disabled_reason_stop_without_active_job() -> None:
    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_state = "idle"
        _gcode_loading = False

    widget = _Widget()
    app = _App()
    app.btn_stop = widget

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "No active job to stop."


def test_resolve_disabled_reason_manual_controls_connecting() -> None:
    class _App:
        connected = False
        _grbl_ready = False
        _status_seen = False
        _alarm_locked = False
        _stream_done_pending_idle = False
        _stream_state = "idle"
        _gcode_loading = False
        _connecting = True
        _disconnecting = False

    widget = _Widget()
    app = _App()
    app._manual_controls = [widget]

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Connecting to controller."


def test_resolve_disabled_reason_waiting_for_idle_completion() -> None:
    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_done_pending_idle = True
        _stream_state = "done"
        _gcode_loading = False
        _connecting = False
        _disconnecting = False

    widget = _Widget()
    app = _App()
    app.btn_open = widget

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Stop the stream to load a new job."


def test_resolve_disabled_reason_connect_button_while_streaming() -> None:
    class _App:
        connected = True
        _grbl_ready = True
        _status_seen = True
        _alarm_locked = False
        _stream_done_pending_idle = False
        _stream_state = "running"
        _gcode_loading = False
        _connecting = False
        _disconnecting = False

    widget = _Widget()
    app = _App()
    app.btn_conn = widget

    reason = tooltip_policy.resolve_disabled_reason(widget, _resolve_owner(app))

    assert reason == "Stop the stream before disconnecting."
