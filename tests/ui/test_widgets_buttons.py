import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

from simple_sender.ui.widgets_buttons import StopSignButton, VirtualHoldButton

pytestmark = pytest.mark.ui


def test_stop_sign_button_invokable_respects_disabled_state(tk_root) -> None:
    host = ttk.Frame(tk_root)
    host.pack(fill="both", expand=True)
    called = {"count": 0}

    btn = StopSignButton(
        host,
        text="STOP",
        fill="#d83b2d",
        text_color="#ffffff",
        command=lambda: called.__setitem__("count", called["count"] + 1),
    )
    btn.invoke()
    btn.config(state="disabled")
    btn.invoke()

    assert isinstance(btn, StopSignButton)
    assert called["count"] == 1
    assert btn.cget("state") == "disabled"


def test_virtual_hold_button_exposes_expected_fields() -> None:
    btn = VirtualHoldButton("X+", "hold_x_pos", "X", 1)

    assert isinstance(btn, VirtualHoldButton)
    assert btn.cget("text") == "X+"
    assert btn.cget("state") == "normal"
