import pytest

from simple_sender.ui.status import bar

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value) -> None:
        self._value = value

    def get(self):
        return self._value


class _Button:
    def __init__(self) -> None:
        self.mapped = False
        self.pack_calls = 0
        self.forget_calls = 0
        self.state = "normal"

    def pack(self, **_kwargs) -> None:
        self.mapped = True
        self.pack_calls += 1

    def pack_forget(self) -> None:
        self.mapped = False
        self.forget_calls += 1

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = str(kwargs["state"])


class _Notebook:
    def __init__(self, label: str) -> None:
        self.label = label

    def select(self):
        return "tab-id"

    def tab(self, _tab_id, _key):
        return self.label


def _make_app(*, tab: str = "G-code", connected: bool = True):
    class _App:
        def __init__(self) -> None:
            self.show_quick_tips_button = _Var(True)
            self.show_quick_3d_button = _Var(True)
            self.show_quick_keys_button = _Var(True)
            self.show_quick_alo_button = _Var(True)
            self.show_quick_vac_button = _Var(True)
            self.show_quick_light_button = _Var(True)
            self.show_quick_release_button = _Var(True)
            self.render3d_enabled = _Var(True)
            self.show_autolevel_overlay = _Var(False)
            self.kasa_enabled = _Var(False)
            self.kasa_device_identifier = _Var("")
            self.vacuum_enabled = _Var(False)
            self.light_enabled = _Var(False)
            self._kasa_outlet_count = 2
            self.connected = connected
            self._alarm_locked = False
            self._stream_state = None
            self._stream_done_pending_idle = False
            self._render3d_blocked = False
            self._auto_level_grid = None
            self._gcode_source = None
            self._last_gcode_lines = []
            self.gview = type("GView", (), {"lines_count": 0})()
            self.notebook = _Notebook(tab)
            self.btn_toggle_tips = _Button()
            self.btn_toggle_3d = _Button()
            self.btn_toggle_keybinds = _Button()
            self.btn_toggle_autolevel_overlay = _Button()
            self.btn_toggle_kasa_vacuum = _Button()
            self.btn_toggle_kasa_light = _Button()
            self.btn_release_checklist = _Button()
            self._quick_button_visibility_signature = None

    return _App()


def test_update_quick_button_visibility_hides_toolpath_buttons_without_job(monkeypatch) -> None:
    monkeypatch.setattr(bar.sys, "platform", "linux")
    app = _make_app(tab="G-code")

    bar.update_quick_button_visibility(app)

    assert app.btn_toggle_tips.mapped is True
    assert app.btn_toggle_keybinds.mapped is True
    assert app.btn_release_checklist.mapped is True
    assert app.btn_toggle_3d.mapped is False
    assert app.btn_toggle_autolevel_overlay.mapped is False
    assert app.btn_toggle_kasa_vacuum.mapped is True
    assert app.btn_toggle_kasa_light.mapped is True
    assert app.btn_toggle_kasa_vacuum.state == "disabled"
    assert app.btn_toggle_kasa_light.state == "disabled"


def test_update_quick_button_visibility_shows_toolpath_buttons_on_3d_tab(monkeypatch) -> None:
    monkeypatch.setattr(bar.sys, "platform", "linux")
    app = _make_app(tab="3D View")
    app._last_gcode_lines = ["G1 X1"]
    app._auto_level_grid = object()

    bar.update_quick_button_visibility(app)

    assert app.btn_toggle_3d.mapped is True
    assert app.btn_toggle_autolevel_overlay.mapped is True


def test_update_quick_button_visibility_hides_release_while_streaming(monkeypatch) -> None:
    monkeypatch.setattr(bar.sys, "platform", "linux")
    app = _make_app(tab="G-code")
    app._stream_state = "running"

    bar.update_quick_button_visibility(app)

    assert app.btn_release_checklist.mapped is False


def test_update_quick_button_visibility_uses_signature_cache(monkeypatch) -> None:
    monkeypatch.setattr(bar.sys, "platform", "linux")
    app = _make_app(tab="G-code")

    bar.update_quick_button_visibility(app)
    first_pack_calls = app.btn_toggle_tips.pack_calls
    first_forget_calls = app.btn_toggle_tips.forget_calls
    bar.update_quick_button_visibility(app)

    assert app.btn_toggle_tips.pack_calls == first_pack_calls
    assert app.btn_toggle_tips.forget_calls == first_forget_calls


def test_update_quick_button_visibility_shows_kasa_quick_buttons_when_ready(monkeypatch) -> None:
    monkeypatch.setattr(bar.sys, "platform", "linux")
    app = _make_app(tab="G-code")
    app.kasa_enabled = _Var(True)
    app.kasa_device_identifier = _Var("device@192.168.0.2")
    app.vacuum_enabled = _Var(True)
    app.light_enabled = _Var(True)

    bar.update_quick_button_visibility(app)

    assert app.btn_toggle_kasa_vacuum.mapped is True
    assert app.btn_toggle_kasa_light.mapped is True
    assert app.btn_toggle_kasa_vacuum.state == "normal"
    assert app.btn_toggle_kasa_light.state == "normal"
