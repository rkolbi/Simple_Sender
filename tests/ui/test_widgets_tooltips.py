import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import widgets_tooltips
from simple_sender.ui.widgets_tooltips import ToolTip, _clamp_tooltip_position

pytestmark = pytest.mark.ui


def test_clamp_tooltip_position_right_and_bottom_edges() -> None:
    x, y = _clamp_tooltip_position(
        1900,
        1060,
        300,
        120,
        1920,
        1080,
    )

    assert x == 1612
    assert y == 952


def test_clamp_tooltip_position_negative_coordinates() -> None:
    x, y = _clamp_tooltip_position(
        -50,
        -10,
        240,
        100,
        1920,
        1080,
    )

    assert x == 8
    assert y == 8


def test_clamp_tooltip_position_oversized_tooltip() -> None:
    x, y = _clamp_tooltip_position(
        100,
        100,
        5000,
        3000,
        1920,
        1080,
    )

    assert x == 8
    assert y == 8


class _FakeWidget:
    def __init__(self) -> None:
        self.bindings: dict[str, object] = {}
        self.after_calls: list[tuple[int, object]] = []
        self.after_cancel_calls: list[str] = []
        self._after_next_id = 0

    def bind(self, event: str, callback, add=None):
        self.bindings[event] = callback
        return callback

    def after(self, delay_ms: int, callback):
        after_id = f"after_{self._after_next_id}"
        self._after_next_id += 1
        self.after_calls.append((delay_ms, callback))
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self.after_cancel_calls.append(after_id)


def test_tooltip_click_suppresses_until_leave() -> None:
    widget = _FakeWidget()
    tip = ToolTip(widget, "Start", delay_ms=150)

    enter = widget.bindings["<Enter>"]
    leave = widget.bindings["<Leave>"]
    press = widget.bindings["<ButtonPress>"]

    enter()
    assert tip._after_id is not None
    assert len(widget.after_calls) == 1

    press()
    assert tip._after_id is None
    assert len(widget.after_cancel_calls) == 1

    enter()
    assert tip._after_id is None
    assert len(widget.after_calls) == 1

    leave()
    enter()
    assert tip._after_id is not None
    assert len(widget.after_calls) == 2


def test_apply_tooltip_reuses_existing_tooltip_instance(monkeypatch) -> None:
    class _FakeTip:
        def __init__(self, widget, text: str):
            self.widget = widget
            self.text = text
            self.set_calls: list[str] = []

        def set_text(self, text: str) -> None:
            self.text = text
            self.set_calls.append(text)

    class _SimpleWidget:
        pass

    monkeypatch.setattr(widgets_tooltips, "ToolTip", _FakeTip)
    widget = _SimpleWidget()

    first = widgets_tooltips.apply_tooltip(widget, "First")
    second = widgets_tooltips.apply_tooltip(widget, "Second")

    assert first is second
    assert isinstance(first, _FakeTip)
    assert first.set_calls == ["Second"]
    assert widget._tooltip_text == "Second"
