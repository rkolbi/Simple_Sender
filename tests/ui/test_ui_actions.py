import time

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import ui_actions

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]


class _StreamingController:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def handle_log(self, msg: str) -> None:
        self.lines.append(msg)

    def flush_console(self) -> None:
        self.lines.append("flush")

    def render_console(self) -> None:
        self.lines.append("render")


class _MessageBox:
    def __init__(self, *, answer: bool = True) -> None:
        self.answer = answer
        self.warnings: list[tuple[str, str]] = []
        self.questions: list[tuple[str, str]] = []

    def showwarning(self, title: str, message: str) -> None:
        self.warnings.append((title, message))

    def askyesno(self, title: str, message: str) -> bool:
        self.questions.append((title, message))
        return self.answer


def test_coerce_ui_scale_defaults_and_clamps() -> None:
    assert ui_actions._coerce_ui_scale("bad", 1.0) == 1.0
    assert ui_actions._coerce_ui_scale(-3.0, 1.0) == 1.0
    assert ui_actions._coerce_ui_scale(10.0, 1.0) == 3.0
    assert ui_actions._coerce_ui_scale(0.333, 1.0) == 0.5


def test_coerce_scrollbar_width_normalizes_values() -> None:
    assert ui_actions._coerce_scrollbar_width("narrow") == "default"
    assert ui_actions._coerce_scrollbar_width("wide") == "wide"
    assert ui_actions._coerce_scrollbar_width("unknown", "wider") == "wider"


def test_coerce_touch_scroll_mode_normalizes_values() -> None:
    assert ui_actions._coerce_touch_scroll_mode("thumb_only") == "thumb_only"
    assert ui_actions._coerce_touch_scroll_mode("Thumb + swipe") == "thumb_and_swipe"
    assert ui_actions._coerce_touch_scroll_mode("unknown", "thumb_only") == "thumb_only"


def test_on_touch_scroll_mode_change_applies_thumb_only() -> None:
    class _App:
        touch_scroll_mode = _Var("thumb_only")
        settings = {}
        status = _Status()

        def _unbind_app_settings_touch_scroll(self) -> None:
            self.unbound = True

        def _bind_app_settings_touch_scroll(self) -> None:
            self.bound = True

    app = _App()
    ui_actions.on_touch_scroll_mode_change(app)
    assert app.touch_scroll_mode.get() == "thumb_only"
    assert app.settings["touch_scroll_mode"] == "thumb_only"
    assert getattr(app, "unbound", False) is True
    assert "Thumb only" in app.status.text


def test_on_touch_scroll_mode_change_applies_thumb_and_swipe() -> None:
    class _App:
        touch_scroll_mode = _Var("thumb_and_swipe")
        settings = {}
        status = _Status()

        def _unbind_app_settings_touch_scroll(self) -> None:
            self.unbound = True

        def _bind_app_settings_touch_scroll(self) -> None:
            self.bound = True

    app = _App()
    ui_actions.on_touch_scroll_mode_change(app)
    assert app.touch_scroll_mode.get() == "thumb_and_swipe"
    assert app.settings["touch_scroll_mode"] == "thumb_and_swipe"
    assert getattr(app, "bound", False) is True
    assert "Thumb + swipe" in app.status.text


def test_format_bytes_handles_none_and_units() -> None:
    assert ui_actions._format_bytes(None) == "n/a"
    assert ui_actions._format_bytes(999) == "999 B"
    assert ui_actions._format_bytes(2048).endswith("KB")


def test_job_estimate_text_uses_rate_source_suffix() -> None:
    class _App:
        def __init__(self) -> None:
            self._last_stats = {"time_min": 1.0, "rapid_min": 0.5}
            self._last_rate_source = "profile"

        def _estimate_factor_value(self) -> float:
            return 1.0

    feed_only, total, finish_at = ui_actions._job_estimate_text(_App())
    assert feed_only != "n/a"
    assert total.endswith("(profile)")
    assert finish_at != "n/a"


def test_confirm_and_run_executes_without_training_wheels() -> None:
    called: list[str] = []

    class _App:
        training_wheels = _Var(False)
        _confirm_last_time: dict[str, float] = {}
        _confirm_debounce_sec = 0.5

    ui_actions.confirm_and_run(_App(), "Run job", lambda: called.append("ok"))
    assert called == ["ok"]


def test_confirm_and_run_respects_debounce() -> None:
    called: list[str] = []

    class _App:
        training_wheels = _Var(True)
        _confirm_last_time: dict[str, float] = {"Stop": time.time()}
        _confirm_debounce_sec = 10.0

    ui_actions.confirm_and_run(_App(), "Stop", lambda: called.append("ok"))
    assert called == []


def test_confirm_and_run_uses_standard_confirmation(monkeypatch) -> None:
    msgbox = _MessageBox(answer=True)
    monkeypatch.setattr(ui_actions, "messagebox", msgbox)
    called: list[str] = []

    class _App:
        training_wheels = _Var(True)
        _confirm_last_time: dict[str, float] = {}
        _confirm_debounce_sec = 0.0

    ui_actions.confirm_and_run(_App(), "Stop", lambda: called.append("ok"))
    assert called == ["ok"]
    assert msgbox.questions


def test_confirm_and_run_uses_run_confirmation_path(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(ui_actions, "_confirm_run_job", lambda _app, _label: True)

    class _App:
        training_wheels = _Var(True)
        _confirm_last_time: dict[str, float] = {}
        _confirm_debounce_sec = 0.0

    ui_actions.confirm_and_run(_App(), "Run job", lambda: called.append("ok"))
    assert called == ["ok"]


def test_on_gui_logging_change_logs_status_line() -> None:
    class _App:
        gui_logging_enabled = _Var(True)
        streaming_controller = _StreamingController()

    app = _App()
    ui_actions.on_gui_logging_change(app)
    assert any("GUI logging enabled" in line for line in app.streaming_controller.lines)


def test_on_performance_mode_change_flushes_when_disabling() -> None:
    class _App:
        performance_mode = _Var(False)
        streaming_controller = _StreamingController()
        status = _Status()

        def _apply_status_poll_profile(self) -> None:
            self.profile_applied = True

    app = _App()
    ui_actions.on_performance_mode_change(app)
    assert "flush" in app.streaming_controller.lines
    assert "Performance mode: Off" in app.status.text
    assert app.profile_applied is True


def test_toggle_console_pos_status_updates_button_and_renders() -> None:
    class _Btn:
        def __init__(self) -> None:
            self.text = ""

        def config(self, **kwargs) -> None:
            self.text = kwargs.get("text", "")

    class _App:
        console_positions_enabled = _Var(False)
        btn_console_pos = _Btn()
        streaming_controller = _StreamingController()

    app = _App()
    ui_actions.toggle_console_pos_status(app)
    assert app.console_positions_enabled.get() is True
    assert app.btn_console_pos.text == "Pos/Status: On"
    assert "render" in app.streaming_controller.lines


def test_require_grbl_connection_warns_when_disconnected(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(ui_actions, "messagebox", msgbox)

    class _Grbl:
        @staticmethod
        def is_connected() -> bool:
            return False

    class _App:
        grbl = _Grbl()

    assert ui_actions.require_grbl_connection(_App()) is False
    assert msgbox.warnings


class _TouchButton:
    def __init__(self, *, text: str = "Run", state: str = "normal") -> None:
        self.master = None
        self._text = text
        self._state = state
        self.state_calls: list[tuple[str, ...]] = []
        self.after_calls: list[tuple[int, object]] = []

    def invoke(self) -> None:
        return None

    def winfo_class(self) -> str:
        return "TButton"

    def winfo_name(self) -> str:
        return "touch_button"

    def cget(self, key: str) -> str:
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        raise KeyError(key)

    def instate(self, states: tuple[str, ...]) -> bool:
        return "disabled" in states and self._state == "disabled"

    def state(self, values: list[str]) -> None:
        self.state_calls.append(tuple(values))

    def after(self, ms: int, callback) -> str:
        self.after_calls.append((ms, callback))
        return f"touch-after-{len(self.after_calls)}"


class _TouchApp:
    def __init__(self) -> None:
        self.status = _Status()
        self.status.text = "Ready"
        self._after: dict[str, object] = {}
        self._next_after_id = 0

    def after(self, _ms: int, callback) -> str:
        self._next_after_id += 1
        after_id = f"after-{self._next_after_id}"
        self._after[after_id] = callback
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self._after.pop(after_id, None)


def test_on_touch_command_feedback_sets_status_and_restores_after_timer() -> None:
    app = _TouchApp()
    btn = _TouchButton(text="Run job")
    event = type("_Event", (), {"widget": btn})()

    ui_actions.on_touch_command_feedback(app, event)

    assert app.status.text == "Touch received: Run job"
    assert ("pressed",) in btn.state_calls

    for callback in list(app._after.values()):
        callback()

    assert app.status.text == "Ready"


def test_on_touch_command_feedback_ignores_disabled_buttons() -> None:
    app = _TouchApp()
    btn = _TouchButton(text="Run job", state="disabled")
    event = type("_Event", (), {"widget": btn})()

    ui_actions.on_touch_command_feedback(app, event)

    assert app.status.text == "Ready"
    assert btn.state_calls == []
