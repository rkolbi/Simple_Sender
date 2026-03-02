import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.events import status as status_events

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

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]


class _SettingsController:
    def __init__(self) -> None:
        self.locked = None

    def set_streaming_lock(self, locked: bool) -> None:
        self.locked = locked


class _ToolpathPanel:
    def __init__(self) -> None:
        self.streaming = None
        self.reparse_calls = 0

    def set_streaming(self, streaming: bool) -> None:
        self.streaming = streaming

    def reparse_lines(self, _lines, *, lines_hash=None) -> None:
        self.reparse_calls += 1


class _GView:
    lines_count = 5


def _make_app() -> object:
    class _App:
        def __init__(self) -> None:
            self._stream_state = "done"
            self._stream_done_pending_idle = True
            self._gcode_total_lines = 5
            self._last_acked_index = 4
            self.progress_pct = _Var(100)
            self._notify_calls = []
            self.connected = True
            self._grbl_ready = True
            self._status_seen = True
            self._alarm_locked = False
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.btn_pause = _Widget()
            self.btn_resume = _Widget()
            self._manual_enabled = None
            self._streaming_lock = None
            self.settings_controller = _SettingsController()
            self.toolpath_panel = _ToolpathPanel()
            self._pending_settings_refresh = False
            self._toolpath_reparse_deferred = False
            self._last_gcode_lines = []
            self._gcode_hash = None
            self._gcode_streaming_mode = False
            self._status_profile_applied = False
            self._settings_dump_calls = 0
            self.gview = _GView()

        def _maybe_notify_job_completion(self, done: int, total: int) -> None:
            self._notify_calls.append((done, total))

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self._manual_enabled = enabled

        def _set_streaming_lock(self, locked: bool) -> None:
            self._streaming_lock = locked

        def _apply_status_poll_profile(self) -> None:
            self._status_profile_applied = True

        def _request_settings_dump(self) -> None:
            self._settings_dump_calls += 1

        class grbl:
            @staticmethod
            def is_streaming() -> bool:
                return False

    return _App()


def test_sync_deferred_completion_finalizes_on_idle() -> None:
    app = _make_app()

    status_events._sync_deferred_stream_completion(app, "Idle")

    assert app._stream_done_pending_idle is False
    assert app._stream_state == "done"
    assert app.progress_pct.get() == 100
    assert app._notify_calls == [(5, 5)]
    assert app.btn_run.state == "normal"
    assert app.btn_resume_from.state == "normal"
    assert app.btn_pause.state == "disabled"
    assert app.btn_resume.state == "disabled"
    assert app._manual_enabled is True
    assert app._streaming_lock is False
    assert app.settings_controller.locked is False
    assert app.toolpath_panel.streaming is False
    assert app._status_profile_applied is True


def test_sync_deferred_completion_holds_at_99_until_idle() -> None:
    app = _make_app()

    status_events._sync_deferred_stream_completion(app, "Run")

    assert app._stream_done_pending_idle is True
    assert app.progress_pct.get() == 99
    assert app._notify_calls == []


def test_sync_deferred_completion_flushes_deferred_refresh_and_reparse() -> None:
    app = _make_app()
    app._pending_settings_refresh = True
    app._toolpath_reparse_deferred = True
    app._last_gcode_lines = ["G0 X0"]
    app._gcode_hash = "abc123"

    status_events._sync_deferred_stream_completion(app, "Idle")

    assert app._settings_dump_calls == 1
    assert app.toolpath_panel.reparse_calls == 1
