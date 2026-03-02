from types import SimpleNamespace

from simple_sender.ui import widgets_keypad
from simple_sender.ui.widgets_keypad import attach_numeric_keypad


class _FakeEntry:
    def __init__(self) -> None:
        self.bind_calls: list[tuple[str, str | None]] = []

    def bind(self, event: str, _callback, add: str | None = None):
        self.bind_calls.append((event, add))
        return f"bind_{len(self.bind_calls)}"

    def winfo_pointerx(self) -> int:
        return 5

    def winfo_pointery(self) -> int:
        return 7

    def winfo_containing(self, _x: int, _y: int):
        return None


def test_attach_numeric_keypad_is_idempotent_for_repeated_specs() -> None:
    entry = _FakeEntry()

    attach_numeric_keypad(entry, allow_decimal=True, allow_negative=False, allow_empty=True)
    attach_numeric_keypad(entry, allow_decimal=True, allow_negative=False, allow_empty=True)

    assert entry.bind_calls == [("<Button-1>", "+"), ("<FocusIn>", "+")]
    assert entry._numeric_keypad_bound is True


def test_attach_numeric_keypad_updates_spec_without_rebinding() -> None:
    entry = _FakeEntry()

    attach_numeric_keypad(entry, allow_decimal=True, allow_negative=False, allow_empty=True)
    attach_numeric_keypad(entry, allow_decimal=False, allow_negative=True, allow_empty=False, title="Signed")

    assert entry.bind_calls == [("<Button-1>", "+"), ("<FocusIn>", "+")]
    assert entry._numeric_keypad_spec == {
        "allow_decimal": False,
        "allow_negative": True,
        "allow_empty": False,
        "title": "Signed",
    }


def test_open_numeric_keypad_from_focus_ignores_keyboard_tab_focus(monkeypatch) -> None:
    entry = _FakeEntry()
    attach_numeric_keypad(entry)
    event = SimpleNamespace(widget=entry)
    calls: list[object] = []

    def _fake_open(evt):
        calls.append(evt)
        return "break"

    monkeypatch.setattr(widgets_keypad, "_open_numeric_keypad", _fake_open)

    assert widgets_keypad._open_numeric_keypad_from_focus(event) is None
    assert calls == []


def test_open_numeric_keypad_from_focus_delegates_when_pointer_is_over_entry(monkeypatch) -> None:
    entry = _FakeEntry()
    attach_numeric_keypad(entry)
    entry.winfo_containing = lambda _x, _y: entry  # type: ignore[method-assign]
    event = SimpleNamespace(widget=entry)
    calls: list[object] = []

    def _fake_open(evt):
        calls.append(evt)
        return "break"

    monkeypatch.setattr(widgets_keypad, "_open_numeric_keypad", _fake_open)

    assert widgets_keypad._open_numeric_keypad_from_focus(event) is None
    assert calls == [event]


def test_show_numeric_keypad_keeps_zero_visible_with_decimal(monkeypatch) -> None:
    button_positions: dict[str, tuple[int, int]] = {}

    class _FakeVar:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def set(self, value: str) -> None:
            self.value = value

    class _FakeDialog:
        def title(self, _text: str) -> None:
            pass

        def transient(self, _parent) -> None:
            pass

        def lift(self) -> None:
            pass

        def resizable(self, _w: bool, _h: bool) -> None:
            pass

        def protocol(self, _name: str, _callback) -> None:
            pass

        def update_idletasks(self) -> None:
            pass

        def wait_visibility(self) -> None:
            pass

        def grab_set(self) -> None:
            pass

        def grab_release(self) -> None:
            pass

        def destroy(self) -> None:
            pass

        def winfo_exists(self) -> bool:
            return True

    class _FakeFrame:
        def __init__(self, _parent, padding: int = 0) -> None:
            self.padding = padding

        def pack(self, **_kwargs) -> None:
            pass

        def grid_columnconfigure(self, _index: int, weight: int = 0) -> None:
            pass

    class _FakeDisplayEntry:
        def __init__(self, _parent, **_kwargs) -> None:
            pass

        def grid(self, **_kwargs) -> None:
            pass

        def configure(self, **_kwargs) -> None:
            pass

    class _FakeButton:
        def __init__(self, _parent, *, text: str, command, **_kwargs) -> None:
            self.text = text
            self.command = command

        def grid(self, *, row: int, column: int, **_kwargs) -> None:
            button_positions[self.text] = (row, column)

    class _FakeEntryForDialog:
        def __init__(self) -> None:
            self.value = "12"

        def get(self) -> str:
            return self.value

        def winfo_toplevel(self):
            return self

        def focus_set(self) -> None:
            pass

        def selection_range(self, _start, _end) -> None:
            pass

        def icursor(self, _index) -> None:
            pass

        def delete(self, _start, _end) -> None:
            self.value = ""

        def insert(self, _index, text: str) -> None:
            self.value = text

        def event_generate(self, _event: str) -> None:
            pass

    monkeypatch.setattr(widgets_keypad.tk, "StringVar", _FakeVar)
    monkeypatch.setattr(widgets_keypad.tk, "Toplevel", lambda _parent: _FakeDialog())
    monkeypatch.setattr(widgets_keypad.ttk, "Frame", _FakeFrame)
    monkeypatch.setattr(widgets_keypad.ttk, "Entry", _FakeDisplayEntry)
    monkeypatch.setattr(widgets_keypad.ttk, "Button", _FakeButton)
    monkeypatch.setattr(widgets_keypad, "_center_modal", lambda *_args, **_kwargs: None)

    entry = _FakeEntryForDialog()
    widgets_keypad._show_numeric_keypad(
        entry,
        {
            "allow_decimal": True,
            "allow_negative": False,
            "allow_empty": True,
            "title": "Enter value",
        },
    )

    assert "0" in button_positions
    assert "." in button_positions
    assert button_positions["0"] != button_positions["."]


def test_prompt_numeric_keypad_forwards_value_to_apply_callback(monkeypatch) -> None:
    seen = {}
    applied: list[str] = []

    def _fake_show(entry, spec):
        seen["spec"] = spec
        entry.delete(0, "end")
        entry.insert(0, "-12.500")
        entry.event_generate("<Return>")

    monkeypatch.setattr(widgets_keypad, "_show_numeric_keypad", _fake_show)

    class _Parent:
        def winfo_toplevel(self):
            return self

    widgets_keypad.prompt_numeric_keypad(
        _Parent(),
        initial_value="1.000",
        allow_decimal=True,
        allow_negative=True,
        allow_empty=False,
        title="Jog X to (MPos)",
        on_apply=applied.append,
    )

    assert applied == ["-12.500"]
    assert seen["spec"]["allow_negative"] is True
    assert seen["spec"]["allow_empty"] is False
    assert seen["spec"]["title"] == "Jog X to (MPos)"
