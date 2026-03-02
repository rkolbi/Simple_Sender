import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import Toolpath3D
from simple_sender.utils.constants import (
    TOOLPATH_STREAMING_RENDER_INTERVAL_MIN,
    VIEW_3D_ARC_STEP_FAST_THRESHOLD,
)

pytestmark = pytest.mark.ui


def _make_view(tk_root) -> Toolpath3D:
    view = Toolpath3D(tk_root)
    view._schedule_render = lambda: None
    return view


def test_toolpath3d_clamp_draw_percent(tk_root) -> None:
    view = _make_view(tk_root)

    assert view._clamp_draw_percent("bad") == view._draw_percent_default
    assert view._clamp_draw_percent(200) == 100
    assert view._clamp_draw_percent(-5) == 0


def test_toolpath3d_draw_target_and_sample(tk_root) -> None:
    view = _make_view(tk_root)
    view._draw_percent = 50

    assert view._draw_target(10, None) == 5
    assert view._draw_target(10, 3) == 3
    assert view._draw_target(0, None) == 0

    segments = list(range(10))
    sampled = view._sample_segments(segments, 3)
    assert sampled == [0, 3, 6]


def test_toolpath3d_select_arc_step_respects_override(tk_root) -> None:
    view = _make_view(tk_root)
    view._arc_step_override_rad = 0.123

    assert view.select_arc_step_rad(1_000_000) == 0.123


def test_toolpath3d_select_arc_step_thresholds(tk_root) -> None:
    view = _make_view(tk_root)
    view._arc_step_override_rad = None

    assert view.select_arc_step_rad(1) == view._arc_step_default
    assert view.select_arc_step_rad(VIEW_3D_ARC_STEP_FAST_THRESHOLD + 1) == view._arc_step_fast
    assert view.select_arc_step_rad(view._full_parse_limit + 1) == view._arc_step_large


def test_toolpath3d_streaming_interval_clamps_and_applies(tk_root) -> None:
    view = _make_view(tk_root)
    view._render_interval = 0.0
    view._streaming_mode = True
    view._streaming_prev_render_interval = 0.0

    view.set_streaming_render_interval(0.0)

    assert view._streaming_render_interval == TOOLPATH_STREAMING_RENDER_INTERVAL_MIN
    assert view._render_interval == TOOLPATH_STREAMING_RENDER_INTERVAL_MIN


def test_toolpath3d_streaming_mode_restores_interval(tk_root) -> None:
    view = _make_view(tk_root)
    view._render_interval = 0.2
    view._streaming_render_interval = 0.5

    view.set_streaming_mode(True)

    assert view._streaming_prev_render_interval == 0.2
    assert view._render_interval == 0.5

    view.set_streaming_mode(False)

    assert view._render_interval == 0.2
