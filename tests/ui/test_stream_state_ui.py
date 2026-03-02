import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.events import stream_state_ui

pytestmark = pytest.mark.ui


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


class _App:
    def __init__(self) -> None:
        self.connected = True
        self._grbl_ready = True
        self._status_seen = True
        self._alarm_locked = False
        self.settings_controller = _SettingsController()
        self.toolpath_panel = _ToolpathPanel()
        self._pending_settings_refresh = False
        self._toolpath_reparse_deferred = False
        self._last_gcode_lines = ["G0 X0"]
        self._gcode_hash = "abc"
        self._gcode_streaming_mode = False
        self._streaming_lock = None
        self._manual_enabled = None
        self._settings_dump_calls = 0

        class _Grbl:
            @staticmethod
            def is_streaming() -> bool:
                return False

        self.grbl = _Grbl()

    def _request_settings_dump(self) -> None:
        self._settings_dump_calls += 1

    def _set_manual_controls_enabled(self, enabled: bool) -> None:
        self._manual_enabled = enabled

    def _set_streaming_lock(self, locked: bool) -> None:
        self._streaming_lock = locked


def test_restore_controls_after_stream_uses_hooks() -> None:
    app = _App()
    calls: dict[str, object] = {}

    def _job_ready(_app, *args):
        calls["job_ready_args"] = args
        return True

    def _set_run_resume(_app, ready: bool) -> None:
        calls["run_ready"] = ready

    stream_state_ui.restore_controls_after_stream(
        app,
        loaded_total=7,
        job_ready_hook=_job_ready,
        set_run_resume_hook=_set_run_resume,
    )

    assert calls["job_ready_args"] == (True,)
    assert calls["run_ready"] is True
    assert app._manual_enabled is True
    assert app._streaming_lock is False


def test_apply_stream_busy_state_flushes_deferred_idle_work() -> None:
    app = _App()
    app._pending_settings_refresh = True
    app._toolpath_reparse_deferred = True

    stream_state_ui.apply_stream_busy_state(app, False)

    assert app.settings_controller.locked is False
    assert app.toolpath_panel.streaming is False
    assert app._settings_dump_calls == 1
    assert app.toolpath_panel.reparse_calls == 1
