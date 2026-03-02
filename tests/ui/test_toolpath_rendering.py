import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import Toolpath3D

pytestmark = pytest.mark.ui


def _make_view() -> Toolpath3D:
    view = Toolpath3D.__new__(Toolpath3D)
    view._visible = True
    view._render_pending = False
    view._render_interval = 0.5
    view._last_render_ts = 0.0
    view._streaming_mode = False
    view._streaming_prev_render_interval = None
    view._streaming_render_interval = 0.25
    view._deferred_full_parse = False
    view._last_gcode_lines = None
    view.after = lambda _delay, _func: None
    view.set_gcode_async = lambda _lines, lines_hash=None: None
    return view


def test_schedule_render_uses_delay(monkeypatch) -> None:
    view = _make_view()
    view._last_render_ts = 99.8
    delays = []

    view.after = lambda delay, _func: delays.append(delay)
    monkeypatch.setattr("simple_sender.ui.toolpath.time.time", lambda: 100.0)

    view._schedule_render()

    assert len(delays) == 1
    assert 295 <= delays[0] <= 305


def test_set_streaming_mode_restores_interval_and_parses() -> None:
    view = _make_view()
    view._streaming_mode = True
    view._render_interval = 0.4
    view._streaming_prev_render_interval = 0.1
    view._deferred_full_parse = True
    view._last_gcode_lines = ["G0 X0"]
    calls = []

    view.set_gcode_async = lambda lines, lines_hash=None: calls.append(lines)
    view._schedule_render = lambda: None

    view.set_streaming_mode(False)

    assert view._streaming_mode is False
    assert view._render_interval == 0.1
    assert view._deferred_full_parse is False
    assert calls == [["G0 X0"]]


def test_set_streaming_render_interval_clamps_and_schedules() -> None:
    view = _make_view()
    view._streaming_mode = True
    view._render_interval = 0.1
    calls = []

    view._schedule_render = lambda: calls.append("scheduled")

    view.set_streaming_render_interval(0.01)

    assert view._streaming_render_interval == 0.05
    assert view._render_interval == 0.1
    assert calls == ["scheduled"]
