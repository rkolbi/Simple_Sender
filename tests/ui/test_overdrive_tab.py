import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import overdrive_tab

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _DummyWidget:
    def __init__(self, *_args, **kwargs) -> None:
        self.kwargs = kwargs
        self.text = kwargs.get("text", "")
        self.command = kwargs.get("command")
        self.value = None

    def pack(self, **_kwargs) -> None:
        return None

    def config(self, **kwargs) -> None:
        self.kwargs.update(kwargs)

    def set(self, value) -> None:
        self.value = value

    def bind(self, *_args, **_kwargs) -> None:
        return None


class _DummyButton(_DummyWidget):
    pass


class _DummyFrame(_DummyWidget):
    pass


class _DummyLabelframe(_DummyWidget):
    pass


class _DummyScale(_DummyWidget):
    pass


class _DummyLabel(_DummyWidget):
    pass


class _DummyEntry(_DummyWidget):
    pass


class _DummyTtk:
    Frame = _DummyFrame
    Labelframe = _DummyLabelframe
    Button = _DummyButton
    Scale = _DummyScale
    Label = _DummyLabel
    Entry = _DummyEntry


def test_build_overdrive_tab_creates_spoilboard_button(monkeypatch) -> None:
    monkeypatch.setattr(overdrive_tab, "ttk", _DummyTtk)
    monkeypatch.setattr(overdrive_tab, "tk", type("_Tk", (), {"StringVar": _Var}))
    kb_calls: list[str] = []
    monkeypatch.setattr(overdrive_tab, "set_kb_id", lambda _btn, key: kb_calls.append(key))
    monkeypatch.setattr(overdrive_tab, "apply_tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(overdrive_tab, "attach_log_gcode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(overdrive_tab, "attach_numeric_keypad", lambda *_args, **_kwargs: None)

    class _Grbl:
        def spindle_on(self, _rpm) -> None:
            return None

        def spindle_off(self) -> None:
            return None

        def send_realtime(self, _code) -> None:
            return None

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self._manual_controls = []
            self._offline_controls = set()
            self._override_controls = []
            self.settings = {"spindle_control_rpm": 18000}
            self.override_info_var = _Var("")
            self.feed_override_display = _Var("100%")
            self.spindle_override_display = _Var("100%")

        def _confirm_and_run(self, _label: str, func):
            return func

        def _on_feed_override_slider(self, _value) -> None:
            return None

        def _on_spindle_override_slider(self, _value) -> None:
            return None

        def _show_spoilboard_generator_dialog(self) -> None:
            return None

        def _set_feed_override_slider_value(self, value: int) -> None:
            self.feed_override_display.set(f"{value}%")

        def _set_spindle_override_slider_value(self, value: int) -> None:
            self.spindle_override_display.set(f"{value}%")

        def _refresh_override_info(self) -> None:
            return None

    app = _App()
    overdrive_tab.build_overdrive_tab(app, parent=object())

    assert hasattr(app, "btn_spoilboard")
    assert app.btn_spoilboard in app._manual_controls
    assert app.btn_spoilboard in app._offline_controls
    assert "spoilboard_generator" in kb_calls
    assert hasattr(app, "spindle_rpm_entry")
    assert app.spindle_rpm_var.get() == "18000"


def test_save_spindle_control_rpm_setting_updates_settings() -> None:
    class _App:
        def __init__(self) -> None:
            self.spindle_rpm_var = _Var("20000")
            self.settings = {"spindle_control_rpm": 18000}

    app = _App()
    rpm = overdrive_tab._save_spindle_control_rpm_setting(app)

    assert rpm == 20000
    assert app.settings["spindle_control_rpm"] == 20000
