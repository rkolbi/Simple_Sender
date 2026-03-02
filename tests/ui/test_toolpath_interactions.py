import types

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import Toolpath3D
import math

pytestmark = pytest.mark.ui


def _make_view() -> Toolpath3D:
    view = Toolpath3D.__new__(Toolpath3D)
    view.azimuth = 0.0
    view.elevation = 0.0
    view.zoom = 1.0
    view.pan_x = 0.0
    view.pan_y = 0.0
    view._drag_start = None
    view._pan_start = None
    view._schedule_render = lambda: None
    view._enter_fast_mode = lambda: None
    return view


def test_drag_updates_angles_and_clamps() -> None:
    view = _make_view()
    calls = []
    view._schedule_render = lambda: calls.append("render")
    view._enter_fast_mode = lambda: calls.append("fast")

    view._on_drag_start(types.SimpleNamespace(x=0, y=0))
    view._on_drag(types.SimpleNamespace(x=10, y=1000))

    assert view.azimuth > 0
    limit = math.pi / 2 - 0.1
    assert abs(view.elevation) <= limit
    assert calls == ["render", "fast"]


def test_pan_updates_offsets() -> None:
    view = _make_view()
    calls = []
    view._schedule_render = lambda: calls.append("render")
    view._enter_fast_mode = lambda: calls.append("fast")

    view._on_pan_start(types.SimpleNamespace(x=5, y=5))
    view._on_pan(types.SimpleNamespace(x=15, y=20))

    assert view.pan_x == 10
    assert view.pan_y == 15
    assert calls == ["render", "fast"]


def test_mousewheel_adjusts_zoom_and_clamps() -> None:
    view = _make_view()
    calls = []
    view._schedule_render = lambda: calls.append("render")
    view._enter_fast_mode = lambda: calls.append("fast")

    view.zoom = 5.0
    view._on_mousewheel(types.SimpleNamespace(delta=120))
    assert view.zoom == 5.0

    view.zoom = 0.2
    view._on_mousewheel(types.SimpleNamespace(delta=-120))
    assert view.zoom == 0.2

    assert calls == ["render", "fast", "render", "fast"]
