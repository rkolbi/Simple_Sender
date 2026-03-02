import types

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=False) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _PygameEvent:
    @staticmethod
    def event_name(event_type: int) -> str:
        return f"TYPE{event_type}"


class _Pygame:
    JOYBUTTONDOWN = 10
    JOYBUTTONUP = 11
    JOYAXISMOTION = 12
    JOYHATMOTION = 13
    event = _PygameEvent


def test_describe_joystick_event_button() -> None:
    class _App:
        def _get_pygame_module(self):
            return _Pygame

    event = types.SimpleNamespace(type=_Pygame.JOYBUTTONDOWN, joy=1, button=2)

    desc = input_bindings.describe_joystick_event(_App(), event)

    assert "TYPE10" in desc
    assert "joy=1" in desc
    assert "button=2" in desc


def test_update_keyboard_live_status_formats_mods() -> None:
    class _App:
        keyboard_live_status = _Var("")
        _kb_mod_keys_down = {"Control_L", "Shift_L"}
        _kb_mod_keysyms = {"Control_L": "Ctrl", "Shift_L": "Shift"}

    app = _App()

    input_bindings.update_keyboard_live_status(app)

    assert app.keyboard_live_status.value == "Keyboard: Ctrl+Shift held"


def test_handle_joystick_event_blocks_without_safety() -> None:
    py = _Pygame

    class _App:
        def __init__(self) -> None:
            self._joystick_safety_binding = None
            self._joystick_safety_active = False
            self._joystick_axis_active = set()
            self._joystick_hat_active = set()
            self._joystick_capture_state = None
            self._joystick_binding_map = {}
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(True)
            self.joystick_event_status = _Var("")

        def _get_pygame_module(self):
            return py

        def _describe_joystick_event(self, _event):
            return None

        def _set_joystick_event_status(self, text: str) -> None:
            self.joystick_event_status.set(text)

        def _joystick_binding_key(self, _binding):
            return None

        def _joystick_binding_from_event(self, _key):
            return None

        def _clear_duplicate_joystick_binding(self, _key, _binding_id: str) -> None:
            return None

        def _apply_keyboard_bindings(self) -> None:
            return None

        def _reset_joystick_axis_state(self, _joy, _axis) -> None:
            return None

        def _reset_joystick_hat_state(self, _joy, _hat) -> None:
            return None

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

        def _on_key_binding(self, _btn) -> None:
            self._key_called = True

        def _stop_joystick_hold(self, _binding_id=None) -> None:
            self._stop_called = True

    app = _App()
    event = types.SimpleNamespace(type=py.JOYBUTTONDOWN, joy=0, button=1)

    input_bindings.handle_joystick_event(app, event)

    assert "Safety enabled" in app.joystick_event_status.value


def test_handle_joystick_event_invokes_button_without_keyboard_gate() -> None:
    py = _Pygame

    class _Button:
        def cget(self, key: str):
            if key == "state":
                return "normal"
            raise KeyError(key)

    class _App:
        def __init__(self) -> None:
            self._btn = _Button()
            self._joystick_safety_binding = None
            self._joystick_safety_active = False
            self._joystick_axis_active = set()
            self._joystick_hat_active = set()
            self._joystick_capture_state = None
            self._joystick_instances = {0: object()}
            self._joystick_binding_map = {("button", 0, 1): self._btn}
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(False)
            self.joystick_event_status = _Var("")
            self.logged = False
            self.invoked = False
            self.keyboard_path_called = False

        def _get_pygame_module(self):
            return py

        def _describe_joystick_event(self, _event):
            return None

        def _set_joystick_event_status(self, text: str) -> None:
            self.joystick_event_status.set(text)

        def _joystick_binding_key(self, _binding):
            return None

        def _joystick_binding_from_event(self, _key):
            return None

        def _clear_duplicate_joystick_binding(self, _key, _binding_id: str) -> None:
            return None

        def _apply_keyboard_bindings(self) -> None:
            return None

        def _reset_joystick_axis_state(self, _joy, _axis) -> None:
            return None

        def _reset_joystick_hat_state(self, _joy, _hat) -> None:
            return None

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

        def _log_button_action(self, _btn) -> None:
            self.logged = True

        def _invoke_button(self, _btn) -> None:
            self.invoked = True

        def _on_key_binding(self, _btn) -> None:
            self.keyboard_path_called = True

    app = _App()
    event = types.SimpleNamespace(type=py.JOYBUTTONDOWN, joy=0, button=1)

    input_bindings.handle_joystick_event(app, event)

    assert app.logged is True
    assert app.invoked is True
    assert app.keyboard_path_called is False


def test_handle_joystick_event_single_joy_fallback_matches_stale_joy_id() -> None:
    py = _Pygame

    class _Button:
        def cget(self, key: str):
            if key == "state":
                return "normal"
            raise KeyError(key)

    class _App:
        def __init__(self) -> None:
            self._btn = _Button()
            self._joystick_safety_binding = None
            self._joystick_safety_active = False
            self._joystick_axis_active = set()
            self._joystick_hat_active = set()
            self._joystick_capture_state = None
            self._joystick_instances = {0: object()}
            self._joystick_binding_map = {("button", 0, 2): self._btn}
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(False)
            self.joystick_event_status = _Var("")
            self.invoked = False

        def _get_pygame_module(self):
            return py

        def _describe_joystick_event(self, _event):
            return None

        def _set_joystick_event_status(self, text: str) -> None:
            self.joystick_event_status.set(text)

        def _joystick_binding_key(self, _binding):
            return None

        def _joystick_binding_from_event(self, _key):
            return None

        def _clear_duplicate_joystick_binding(self, _key, _binding_id: str) -> None:
            return None

        def _apply_keyboard_bindings(self) -> None:
            return None

        def _reset_joystick_axis_state(self, _joy, _axis) -> None:
            return None

        def _reset_joystick_hat_state(self, _joy, _hat) -> None:
            return None

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

        def _log_button_action(self, _btn) -> None:
            return None

        def _invoke_button(self, _btn) -> None:
            self.invoked = True

    app = _App()
    # Event joy id differs from saved binding id, but only one joystick is connected.
    event = types.SimpleNamespace(type=py.JOYBUTTONDOWN, joy=7, button=2)

    input_bindings.handle_joystick_event(app, event)

    assert app.invoked is True


def test_handle_joystick_event_ignored_when_screen_locked() -> None:
    py = _Pygame

    class _App:
        def __init__(self) -> None:
            self._screen_lock_active = True
            self._joystick_safety_binding = None
            self._joystick_safety_active = False
            self._joystick_axis_active = set()
            self._joystick_hat_active = set()
            self._joystick_capture_state = None
            self._joystick_instances = {0: object()}
            self._joystick_binding_map = {}
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(False)
            self.joystick_event_status = _Var("")
            self.called = False

        def _get_pygame_module(self):
            return py

        def _describe_joystick_event(self, _event):
            return None

        def _set_joystick_event_status(self, _text: str) -> None:
            self.called = True

        def _joystick_binding_key(self, _binding):
            return None

        def _joystick_binding_from_event(self, _key):
            return None

        def _clear_duplicate_joystick_binding(self, _key, _binding_id: str) -> None:
            return None

        def _apply_keyboard_bindings(self) -> None:
            return None

        def _reset_joystick_axis_state(self, _joy, _axis) -> None:
            return None

        def _reset_joystick_hat_state(self, _joy, _hat) -> None:
            return None

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

    app = _App()
    event = types.SimpleNamespace(type=py.JOYBUTTONDOWN, joy=0, button=1)

    result = input_bindings.handle_joystick_event(app, event)

    assert result is None
    assert app.called is False


def test_handle_joystick_button_release_cancels_jog_bound_button() -> None:
    class _Button:
        pass

    class _Grbl:
        def __init__(self) -> None:
            self.cancel_calls = 0
            self.purge_calls = 0

        def jog_cancel(self) -> None:
            self.cancel_calls += 1

        def cancel_pending_jogs(self) -> None:
            self.purge_calls += 1

    class _App:
        def __init__(self) -> None:
            self._btn = _Button()
            self._joystick_instances = {0: object()}
            self._joystick_binding_map = {("button", 0, 1): self._btn}
            self.grbl = _Grbl()

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

        def _button_binding_id(self, _btn) -> str:
            return "jog_x_plus"

    app = _App()

    input_bindings.handle_joystick_button_release(app, ("button", 0, 1))

    assert app.grbl.cancel_calls == 1
    assert app.grbl.purge_calls == 1


def test_handle_joystick_button_release_ignores_non_jog_binding() -> None:
    class _Button:
        pass

    class _Grbl:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def jog_cancel(self) -> None:
            self.cancel_calls += 1

        def cancel_pending_jogs(self) -> None:
            self.cancel_calls += 1

    class _App:
        def __init__(self) -> None:
            self._btn = _Button()
            self._joystick_instances = {0: object()}
            self._joystick_binding_map = {("button", 0, 1): self._btn}
            self.grbl = _Grbl()

        def _is_virtual_hold_button(self, _btn) -> bool:
            return False

        def _button_binding_id(self, _btn) -> str:
            return "zero_x"

    app = _App()

    input_bindings.handle_joystick_button_release(app, ("button", 0, 1))

    assert app.grbl.cancel_calls == 0
