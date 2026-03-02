import queue

from simple_sender.ui import pi_profile


class _Var:
    def __init__(self, value) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, *, text: str) -> None:
        self.text = text


class _ToolpathPanel:
    def __init__(self) -> None:
        self.enabled_values: list[bool] = []

    def set_enabled(self, value: bool) -> None:
        self.enabled_values.append(bool(value))


def _make_app() -> object:
    class _App:
        def __init__(self) -> None:
            self.ui_q: queue.Queue[tuple[str, str]] = queue.Queue()
            self.status = _Status()
            self.settings = {}
            self.toolpath_panel = _ToolpathPanel()
            self.pi_profile_enabled = _Var(False)
            self.performance_mode = _Var(False)
            self.gui_logging_enabled = _Var(True)
            self.validate_streaming_gcode = _Var(True)
            self.streaming_line_threshold = _Var(250000)
            self.status_poll_interval = _Var(0.2)
            self.toolpath_lightweight = _Var(False)
            self.toolpath_streaming_render_interval = _Var(0.25)
            self.render3d_enabled = _Var(True)
            self.show_autolevel_overlay = _Var(True)
            self._ui_queue_idle_interval_ms = 125
            self._ui_maintenance_idle_interval_s = 1.0
            self._auto_reconnect_check_idle_interval_s = 1.0
            self.save_calls = 0
            self.calls: dict[str, int] = {}

        def _save_settings(self) -> None:
            self.save_calls += 1

        def _mark(self, key: str) -> None:
            self.calls[key] = self.calls.get(key, 0) + 1

        def _on_performance_mode_change(self) -> None:
            self._mark("perf")

        def _on_gui_logging_change(self) -> None:
            self._mark("gui_log")

        def _on_status_interval_change(self) -> None:
            self._mark("status")

        def _on_toolpath_lightweight_change(self) -> None:
            self._mark("lightweight")

        def _apply_toolpath_streaming_render_interval(self, _event=None) -> None:
            self._mark("stream_interval")

        def _refresh_render_3d_toggle_text(self) -> None:
            self._mark("render_text")

        def _on_autolevel_overlay_change(self) -> None:
            self._mark("overlay")

    return _App()


def test_apply_pi_profile_enabled_applies_and_saves() -> None:
    app = _make_app()

    pi_profile.apply_pi_profile(app, enabled=True, save_settings=True, emit_status=True)

    assert app.performance_mode.get() is True
    assert app.gui_logging_enabled.get() is False
    assert app.validate_streaming_gcode.get() is False
    assert app.streaming_line_threshold.get() == pi_profile.PI_PROFILE_STREAMING_LINE_THRESHOLD
    assert app.status_poll_interval.get() == pi_profile.PI_PROFILE_STATUS_POLL_INTERVAL
    assert app.toolpath_lightweight.get() is True
    assert app.toolpath_streaming_render_interval.get() == pi_profile.PI_PROFILE_STREAMING_RENDER_INTERVAL
    assert app.render3d_enabled.get() is False
    assert app.show_autolevel_overlay.get() is False
    assert app._ui_queue_idle_interval_ms == pi_profile.PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_MS
    assert app._ui_maintenance_idle_interval_s == pi_profile.PI_PROFILE_UI_MAINTENANCE_IDLE_INTERVAL_S
    assert app._auto_reconnect_check_idle_interval_s == pi_profile.PI_PROFILE_UI_RECONNECT_IDLE_INTERVAL_S
    assert app.toolpath_panel.enabled_values == [False]
    assert app.save_calls == 1
    assert app.status.text == "[settings] Pi profile enabled"
    assert app.ui_q.get_nowait() == ("log", "[settings] Pi profile enabled")


def test_apply_pi_profile_from_state_disabled_restores_idle_interval() -> None:
    app = _make_app()
    app._ui_queue_idle_interval_ms = 999
    app.pi_profile_enabled.set(False)

    pi_profile.apply_pi_profile_from_state(app, save_settings=False, emit_status=False)

    assert app._ui_queue_idle_interval_ms == pi_profile.PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_DEFAULT_MS
    assert app._ui_maintenance_idle_interval_s == 1.0
    assert app._auto_reconnect_check_idle_interval_s == 1.0
    assert app.save_calls == 0


def test_offer_pi_profile_if_recommended_applies_once(monkeypatch) -> None:
    app = _make_app()
    app.settings = {
        "pi_profile_enabled": False,
        pi_profile.PI_PROFILE_PROMPT_SHOWN_KEY: False,
    }
    monkeypatch.setattr(pi_profile, "detect_raspberry_pi", lambda: True)
    monkeypatch.setattr(pi_profile.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    accepted = pi_profile.offer_pi_profile_if_recommended(app)
    accepted_second = pi_profile.offer_pi_profile_if_recommended(app)

    assert accepted is True
    assert accepted_second is False
    assert app.pi_profile_enabled.get() is True
    assert app.settings[pi_profile.PI_PROFILE_PROMPT_SHOWN_KEY] is True
    assert app.save_calls == 1
