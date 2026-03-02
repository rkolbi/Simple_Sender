import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.unit


class _Event:
    def __init__(self, keysym: str, state: int = 0) -> None:
        self.keysym = keysym
        self.state = state


class _App:
    def __init__(self) -> None:
        self._kb_mod_keysyms = {
            "Control_L": "Ctrl",
            "Shift_L": "Shift",
            "Alt_L": "Alt",
        }
        self._kb_mod_keys_down = set()
        self._joystick_names = {1: "Pad"}

        self._normalize_key_chord = lambda text: input_bindings.normalize_key_chord(self, text)
        self._normalize_key_label = lambda text: input_bindings.normalize_key_label(self, text)
        self._key_sequence_tuple = lambda label: input_bindings.key_sequence_tuple(self, label)
        self._update_modifier_state = lambda event, pressed: input_bindings.update_modifier_state(
            self, event, pressed
        )
        self._modifier_active = lambda name, event_state=None: input_bindings.modifier_active(
            self, name, event_state
        )
        self._sequence_conflict_pair = lambda a, b: input_bindings.sequence_conflict_pair(self, a, b)


def test_normalize_key_chord_orders_mods() -> None:
    app = _App()

    assert input_bindings.normalize_key_chord(app, "alt+ctrl+a") == "Ctrl+Alt+A"
    assert input_bindings.normalize_key_chord(app, "spc") == "Space"
    assert input_bindings.normalize_key_chord(app, "") == ""


def test_normalize_key_label_trims_to_three() -> None:
    app = _App()

    result = input_bindings.normalize_key_label(app, "Ctrl+A Shift+B C D")

    assert result == "Ctrl+A Shift+B C"


def test_key_sequence_tuple_limits_length() -> None:
    app = _App()

    seq = input_bindings.key_sequence_tuple(app, "Ctrl+A Shift+B C D")

    assert seq == ("Ctrl+A", "Shift+B", "C")


def test_update_modifier_state_and_modifier_active() -> None:
    app = _App()
    event = _Event("Shift_L")

    assert input_bindings.update_modifier_state(app, event, pressed=True) is True
    assert "Shift_L" in app._kb_mod_keys_down
    assert input_bindings.modifier_active(app, "Shift") is True

    assert input_bindings.update_modifier_state(app, event, pressed=False) is True
    assert "Shift_L" not in app._kb_mod_keys_down

    assert input_bindings.modifier_active(app, "Ctrl", event_state=0x4) is True


def test_event_to_binding_label_uses_modifier_state() -> None:
    app = _App()
    event = _Event("a", state=0x4)

    assert input_bindings.event_to_binding_label(app, event) == "Ctrl+A"


def test_event_to_binding_label_ignores_modifier_keys() -> None:
    app = _App()
    event = _Event("Control_L")

    assert input_bindings.event_to_binding_label(app, event) == ""
    assert "Control_L" in app._kb_mod_keys_down


def test_sequence_conflict_helpers() -> None:
    app = _App()
    seq_a = ("Ctrl+A",)
    seq_b = ("Ctrl+A", "B")
    seq_c = ("Ctrl+B",)

    assert input_bindings.sequence_conflict_pair(app, seq_a, seq_b) is True
    assert input_bindings.sequence_conflict_pair(app, seq_a, seq_c) is False
    assert input_bindings.sequence_conflict(app, ("Ctrl+A", "C"), {seq_a: 1, seq_c: 2}) == seq_a
    assert input_bindings.sequence_conflict(app, ("Ctrl+X",), {seq_a: 1}) is None


def test_joystick_binding_helpers() -> None:
    app = _App()

    assert input_bindings.joystick_binding_display(
        app, {"kind": "button", "joy_id": 1, "index": 2}
    ) == "Pad Button 2"
    assert input_bindings.joystick_binding_display(
        app, {"kind": "axis", "joy_id": 1, "index": 0, "direction": 1}
    ) == "Pad Axis 0+"
    assert input_bindings.joystick_binding_display(
        app, {"kind": "hat", "joy_id": 1, "index": 0, "value": (1, 0)}
    ) == "Pad Hat 0 (1, 0)"

    assert input_bindings.joystick_binding_key(
        app, {"kind": "button", "joy_id": 2, "index": 3}
    ) == ("button", 2, 3)


def test_button_binding_id_prefers_kb_id() -> None:
    app = _App()

    class _Btn:
        _kb_id = "bind-id"

    assert input_bindings.button_binding_id(app, _Btn()) == "bind-id"
