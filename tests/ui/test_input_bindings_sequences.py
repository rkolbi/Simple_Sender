import types

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


class _App:
    def __init__(self) -> None:
        self._kb_mod_keysyms = {"Control_L": "Ctrl", "Shift_L": "Shift"}
        self._kb_mod_keys_down = set()
        self._key_sequence_timeout = 0.5
        self._key_sequence_last_time = 0.0
        self._key_sequence_buffer = []
        self._key_sequence_after_id = None
        self._key_sequence_map = {}
        self._bound_key_sequences = set()

    def _normalize_key_chord(self, text: str) -> str:
        return input_bindings.normalize_key_chord(self, text)

    def _normalize_key_label(self, text: str) -> str:
        return input_bindings.normalize_key_label(self, text)

    def _update_modifier_state(self, event, pressed: bool = True) -> bool:
        return input_bindings.update_modifier_state(self, event, pressed)

    def _modifier_active(self, name: str, event_state: int | None = None) -> bool:
        return input_bindings.modifier_active(self, name, event_state)

    def _keyboard_binding_allowed(self) -> bool:
        return True

    def _event_to_binding_label(self, event) -> str:
        return input_bindings.event_to_binding_label(self, event)

    def _on_key_binding(self, btn) -> None:
        self._bound_key_sequences.add(btn)

    def _clear_key_sequence_buffer(self) -> None:
        input_bindings.clear_key_sequence_buffer(self)

    def after(self, _delay_ms: int, _func):
        return "after_id"

    def after_cancel(self, _after_id) -> None:
        return None


def test_event_to_binding_label_includes_ctrl() -> None:
    app = _App()
    event = types.SimpleNamespace(keysym="a", state=0x4)

    label = input_bindings.event_to_binding_label(app, event)

    assert label == "Ctrl+A"


def test_on_key_sequence_triggers_binding(monkeypatch) -> None:
    app = _App()
    app._key_sequence_map = {("Ctrl+A",): "BTN"}
    app._event_to_binding_label = lambda _event: "Ctrl+A"

    monkeypatch.setattr(input_bindings.time, "time", lambda: 100.0)

    input_bindings.on_key_sequence(app, types.SimpleNamespace())

    assert "BTN" in app._bound_key_sequences
    assert app._key_sequence_buffer == []


def test_on_key_sequence_buffers_when_no_match(monkeypatch) -> None:
    app = _App()
    app._event_to_binding_label = lambda _event: "Ctrl+A"

    monkeypatch.setattr(input_bindings.time, "time", lambda: 100.0)

    input_bindings.on_key_sequence(app, types.SimpleNamespace())

    assert app._key_sequence_buffer == ["Ctrl+A"]
    assert app._key_sequence_after_id == "after_id"
