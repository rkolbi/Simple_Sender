import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import scrollbar_touch

pytestmark = pytest.mark.ui


class _Tk:
    def __init__(self) -> None:
        self.calls = []

    def call(self, *args):
        self.calls.append(args)


class _Widget:
    def __init__(self) -> None:
        self.tk = _Tk()
        self._drag_state = None
        self._pointer_x = 8
        self._pointer_y = 60

    def identify(self, _x: int, _y: int):
        return "slider"

    def cget(self, key: str):
        if key == "orient":
            return "vertical"
        if key == "command":
            return "fake_scroll_command"
        return ""

    def get(self):
        return (0.25, 0.45)

    def winfo_width(self):
        return 50

    def winfo_height(self):
        return 200

    def winfo_pointerx(self):
        return self._pointer_x

    def winfo_pointery(self):
        return self._pointer_y

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0


class _Event:
    def __init__(self, widget, x: int = 0, y: int = 0) -> None:
        self.widget = widget
        self.x = x
        self.y = y


def test_scrollbar_touch_drag_moves_with_moveto(monkeypatch) -> None:
    widget = _Widget()
    monkeypatch.setattr(scrollbar_touch, "_is_scrollbar_widget", lambda _w: True)
    scrollbar_touch._ACTIVE_SCROLLBAR = None

    press_result = scrollbar_touch._on_scrollbar_touch_press(_Event(widget, x=8, y=60))
    widget._pointer_y = 100
    move_result = scrollbar_touch._on_scrollbar_touch_motion(_Event(widget, x=8, y=100))
    release_result = scrollbar_touch._on_scrollbar_touch_release(_Event(widget, x=8, y=100))

    assert press_result is None
    assert move_result == "break"
    assert release_result is None
    assert widget.tk.calls
    cmd, action, value = widget.tk.calls[-1]
    assert cmd == "fake_scroll_command"
    assert action == "moveto"
    assert 0.0 <= float(value) <= 1.0


def test_scrollbar_touch_press_on_trough_starts_centered_drag(monkeypatch) -> None:
    widget = _Widget()
    widget.identify = lambda _x, _y: "trough2"
    monkeypatch.setattr(scrollbar_touch, "_is_scrollbar_widget", lambda _w: True)
    scrollbar_touch._ACTIVE_SCROLLBAR = None

    result = scrollbar_touch._on_scrollbar_touch_press(_Event(widget, x=5, y=20))
    widget._pointer_y = 120
    move_result = scrollbar_touch._on_scrollbar_touch_motion(_Event(widget, x=5, y=120))

    assert result == "break"
    assert move_result == "break"
    assert widget.tk.calls


def test_scrollbar_touch_motion_uses_active_scrollbar_when_event_widget_differs(monkeypatch) -> None:
    widget = _Widget()
    other = object()
    monkeypatch.setattr(scrollbar_touch, "_is_scrollbar_widget", lambda w: w is widget)
    scrollbar_touch._ACTIVE_SCROLLBAR = None

    scrollbar_touch._on_scrollbar_touch_press(_Event(widget, x=8, y=60))
    widget._pointer_y = 130
    move_result = scrollbar_touch._on_scrollbar_touch_motion(_Event(other, x=1, y=1))

    assert move_result == "break"
    assert widget.tk.calls
