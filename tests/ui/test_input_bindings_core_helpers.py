import pytest

tk = pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


class _Button:
    def __init__(self, state: str = "normal", command=None, invoke_error: bool = False) -> None:
        self._state = state
        self._command = command
        self._invoke_error = invoke_error
        self.invoked = False

    def cget(self, key: str):
        if key == "state":
            return self._state
        if key == "command":
            return self._command
        raise KeyError(key)

    def invoke(self) -> None:
        if self._invoke_error:
            raise tk.TclError("invoke failure")
        self.invoked = True


def test_on_key_jog_stop_ignores_disabled_button() -> None:
    class _Grbl:
        def __init__(self) -> None:
            self.calls = 0

        def jog_cancel(self) -> None:
            self.calls += 1

    class _App:
        def __init__(self) -> None:
            self.btn_jog_cancel = _Button(state="disabled")
            self.grbl = _Grbl()
            self.stop_calls = 0

        def _keyboard_binding_allowed(self) -> bool:
            return True

        def _stop_joystick_hold(self) -> None:
            self.stop_calls += 1

    app = _App()

    input_bindings.on_key_jog_stop(app, None)

    assert app.stop_calls == 0
    assert app.grbl.calls == 0


def test_invoke_button_falls_back_to_command_when_invoke_raises() -> None:
    called = {"cmd": 0}

    def _cmd() -> None:
        called["cmd"] += 1

    btn = _Button(command=_cmd, invoke_error=True)

    input_bindings.invoke_button(object(), btn)

    assert called["cmd"] == 1


def test_clear_key_sequence_buffer_swallows_tclerror() -> None:
    class _App:
        def __init__(self) -> None:
            self._key_sequence_buffer = ["Ctrl+A"]
            self._key_sequence_after_id = "after_1"

        def after_cancel(self, _after_id: str) -> None:
            raise tk.TclError("already canceled")

    app = _App()

    input_bindings.clear_key_sequence_buffer(app)

    assert app._key_sequence_buffer == []
    assert app._key_sequence_after_id is None
