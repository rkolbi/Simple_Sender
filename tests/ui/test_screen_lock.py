import types

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

from simple_sender.ui import screen_lock

pytestmark = pytest.mark.ui


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, **kwargs) -> None:
        self.text = kwargs.get("text", self.text)


def test_init_screen_lock_guard_injects_bindtag(tk_root) -> None:
    app = tk_root
    app.status = _Status()
    app.btn_screen_lock = ttk.Button(app, text="Lock")
    app.btn_screen_lock.pack()
    entry = ttk.Entry(app)
    entry.pack()
    app._on_screen_lock_event = lambda event: screen_lock.on_screen_lock_event(app, event)
    app._on_screen_lock_widget_mapped = (
        lambda event: screen_lock.on_screen_lock_widget_mapped(app, event)
    )

    screen_lock.init_screen_lock_guard(app)

    assert app._screen_lock_guard_ready is True
    assert screen_lock.SCREEN_LOCK_BINDTAG in app.bindtags()
    assert screen_lock.SCREEN_LOCK_BINDTAG in app.btn_screen_lock.bindtags()
    assert screen_lock.SCREEN_LOCK_BINDTAG in entry.bindtags()


def test_screen_lock_blocks_non_unlock_widgets(tk_root) -> None:
    app = tk_root
    app.status = _Status()
    app.btn_screen_lock = ttk.Button(app, text="Lock")
    other_btn = ttk.Button(app, text="Other")
    app._screen_lock_active = True

    blocked = screen_lock.on_screen_lock_event(app, types.SimpleNamespace(widget=other_btn))
    allowed = screen_lock.on_screen_lock_event(app, types.SimpleNamespace(widget=app.btn_screen_lock))

    assert blocked == "break"
    assert allowed is None


def test_toggle_screen_lock_updates_button_and_status(tk_root, monkeypatch) -> None:
    app = tk_root
    app.status = _Status()
    app.btn_screen_lock = ttk.Button(app, text="Lock")
    app._screen_lock_active = False
    calls = {"clear": 0, "stop": 0}
    app._clear_key_sequence_buffer = lambda: calls.__setitem__("clear", calls["clear"] + 1)
    app._stop_joystick_hold = lambda: calls.__setitem__("stop", calls["stop"] + 1)
    monkeypatch.setattr(screen_lock.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    screen_lock.toggle_screen_lock(app)
    assert app._screen_lock_active is True
    assert app.btn_screen_lock.cget("text") == "Unlock"
    assert "locked" in app.status.text.lower()
    assert calls["clear"] == 1
    assert calls["stop"] == 1

    screen_lock.toggle_screen_lock(app)
    assert app._screen_lock_active is False
    assert app.btn_screen_lock.cget("text") == "Lock"
    assert "unlocked" in app.status.text.lower()
    assert calls["clear"] == 2


def test_toggle_screen_lock_cancel_keeps_state(tk_root, monkeypatch) -> None:
    app = tk_root
    app.status = _Status()
    app.btn_screen_lock = ttk.Button(app, text="Lock")
    app._screen_lock_active = False
    calls = {"clear": 0, "stop": 0}
    app._clear_key_sequence_buffer = lambda: calls.__setitem__("clear", calls["clear"] + 1)
    app._stop_joystick_hold = lambda: calls.__setitem__("stop", calls["stop"] + 1)
    monkeypatch.setattr(screen_lock.messagebox, "askyesno", lambda *_args, **_kwargs: False)

    screen_lock.toggle_screen_lock(app)

    assert app._screen_lock_active is False
    assert app.btn_screen_lock.cget("text") == "Lock"
    assert calls["clear"] == 0
    assert calls["stop"] == 0


def test_on_screen_lock_widget_mapped_adds_bindtag(tk_root) -> None:
    app = tk_root
    app._screen_lock_bindtag = screen_lock.SCREEN_LOCK_BINDTAG
    btn = ttk.Button(app, text="Late")
    assert screen_lock.SCREEN_LOCK_BINDTAG not in btn.bindtags()

    screen_lock.on_screen_lock_widget_mapped(app, types.SimpleNamespace(widget=btn))

    assert screen_lock.SCREEN_LOCK_BINDTAG in btn.bindtags()
