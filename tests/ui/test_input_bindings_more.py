import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _Button:
    def __init__(self) -> None:
        self.text = ""
        self.state = ""

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "state" in kwargs:
            self.state = kwargs["state"]


class _MessageBox:
    def __init__(self) -> None:
        self.calls = []

    def showwarning(self, title: str, message: str) -> None:
        self.calls.append((title, message))


def test_refresh_joystick_toggle_text_disables_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(input_bindings, "PYGAME_AVAILABLE", False)

    class _App:
        btn_toggle_joystick_bindings = _Button()

        def _refresh_joystick_test_info(self):
            self._refreshed = True

    app = _App()

    input_bindings.refresh_joystick_toggle_text(app)

    assert "requires pygame" in app.btn_toggle_joystick_bindings.text
    assert app.btn_toggle_joystick_bindings.state == "disabled"


def test_toggle_joystick_bindings_warns_when_missing(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(input_bindings, "PYGAME_AVAILABLE", False)
    monkeypatch.setattr(input_bindings, "messagebox", msgbox)

    class _App:
        joystick_bindings_enabled = _Var(False)

    input_bindings.toggle_joystick_bindings(_App())

    assert msgbox.calls


def test_maybe_refresh_joystick_devices_updates_when_changed(monkeypatch) -> None:
    class _PyJoy:
        def __init__(self) -> None:
            self._count = 1

        def get_count(self) -> int:
            return self._count

    class _Py:
        joystick = _PyJoy()

    updates = []

    monkeypatch.setattr(input_bindings, "update_joystick_test_status", lambda *_args, **_kwargs: updates.append("test"))
    monkeypatch.setattr(input_bindings, "update_joystick_device_status", lambda *_args, **_kwargs: updates.append("device"))
    monkeypatch.setattr(input_bindings.time, "monotonic", lambda: 10.0)

    class _App:
        _joystick_last_discovery = 0.0
        _joystick_device_count = 0
        _joystick_instances = {}

        def _discover_joysticks(self, _py, count: int):
            return [f"Joy {idx}" for idx in range(count)]

    app = _App()

    changed = input_bindings.maybe_refresh_joystick_devices(app, _Py, force=False, reason="Refresh")

    assert changed
    assert updates == ["test", "device"]


def test_update_joystick_polling_state_stops_hold_on_backend_failure(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(input_bindings, "PYGAME_AVAILABLE", True)
    monkeypatch.setattr(input_bindings, "messagebox", msgbox)
    monkeypatch.setattr(input_bindings._core, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.joystick_bindings_enabled = _Var(True)
            self._stopped_hold = False

        def _refresh_joystick_toggle_text(self) -> None:
            return None

        def _cancel_joystick_capture(self) -> None:
            return None

        def _stop_joystick_polling(self) -> None:
            return None

        def _stop_joystick_hold(self, _binding_id=None) -> None:
            self._stopped_hold = True

        def _ensure_joystick_backend(self) -> bool:
            return False

    app = _App()

    input_bindings.update_joystick_polling_state(app)

    assert app._stopped_hold is True
    assert app.joystick_bindings_enabled.get() is False
