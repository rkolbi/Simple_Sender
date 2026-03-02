import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


def test_normalize_key_chord_aliases() -> None:
    assert input_bindings.normalize_key_chord(object(), "ctrl+shift+space") == "Ctrl+Shift+Space"
    assert input_bindings.normalize_key_chord(object(), "alt+enter") == "Alt+Enter"


def test_normalize_key_label_limits_to_three_chords() -> None:
    class _App:
        def _normalize_key_chord(self, text: str) -> str:
            return input_bindings.normalize_key_chord(self, text)

    app = _App()
    label = input_bindings.normalize_key_label(app, "Ctrl+A B C D")

    assert label == "Ctrl+A B C"


def test_key_sequence_tuple_limits_length() -> None:
    class _App:
        def _normalize_key_label(self, text: str) -> str:
            return input_bindings.normalize_key_label(self, text)

        def _normalize_key_chord(self, text: str) -> str:
            return input_bindings.normalize_key_chord(self, text)

    app = _App()
    seq = input_bindings.key_sequence_tuple(app, "Ctrl+A B C D")

    assert seq == ("Ctrl+A", "B", "C")


def test_sequence_conflict_pair_detects_prefix() -> None:
    app = object()
    assert input_bindings.sequence_conflict_pair(app, ("Ctrl+A",), ("Ctrl+A", "B")) is True
    assert input_bindings.sequence_conflict_pair(app, ("Ctrl+A",), ("Ctrl+B",)) is False
