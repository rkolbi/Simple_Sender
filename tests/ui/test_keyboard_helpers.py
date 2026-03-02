import pytest

tk = pytest.importorskip("tkinter")

from simple_sender.ui.bindings import keyboard

pytestmark = pytest.mark.ui


def test_button_label_falls_back_when_cget_raises() -> None:
    class _Btn:
        _text = "Jog X+"

        def cget(self, _key: str):
            raise KeyError("text")

        def winfo_name(self) -> str:
            return "btn_jog_x_plus"

        def winfo_class(self) -> str:
            return "TButton"

    assert keyboard.button_label(object(), _Btn()) == "Jog X+"


def test_commit_kb_edit_handles_tclerror_cleanup_and_applies_binding() -> None:
    class _Entry:
        def get(self) -> str:
            raise tk.TclError("no value")

        def after_cancel(self, _after_id) -> None:
            return None

        def destroy(self) -> None:
            raise tk.TclError("already destroyed")

    class _App:
        def __init__(self, entry) -> None:
            self._kb_edit = entry
            self._kb_edit_state = {entry: {"placeholder": False, "after_id": "after_1"}}
            self._kb_item_to_button = {"row1": object()}
            self._key_bindings: dict[str, str] = {}
            self.apply_called = 0

        def _normalize_key_label(self, value: str) -> str:
            return value.strip()

        def _button_binding_id(self, _btn) -> str:
            return "binding-1"

        def _apply_keyboard_bindings(self) -> None:
            self.apply_called += 1

    entry = _Entry()
    app = _App(entry)

    keyboard.commit_kb_edit(app, "row1", entry, label_override=None)

    assert app._kb_edit is None
    assert app.apply_called == 1
    assert app._key_bindings["binding-1"] == ""
