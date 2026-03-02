import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import Toolpath3D

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value) -> None:
        self.value = value


def _make_toolpath() -> Toolpath3D:
    view = Toolpath3D.__new__(Toolpath3D)
    view._draw_percent_default = 50
    view._draw_percent = 50
    view._draw_percent_text = _Var()
    view._full_parse_skipped = False
    view._last_gcode_lines = None
    view._full_parse_limit = 10
    view._streaming_mode = False
    view._deferred_full_parse = False
    view._last_render_ts = 0.0
    view._invalidate_render_cache = lambda: None
    view._schedule_render = lambda: None
    view.set_gcode_async = lambda _lines, lines_hash=None: None
    return view


def test_draw_target_respects_percent_and_limits() -> None:
    view = _make_toolpath()
    view._draw_percent = 0
    assert view._draw_target(100, None) == 0
    view._draw_percent = 100
    assert view._draw_target(100, None) == 100
    view._draw_percent = 50
    assert view._draw_target(100, 20) == 20


def test_sample_segments_downsamples() -> None:
    view = _make_toolpath()
    segments = list(range(10))

    sampled = view._sample_segments(segments, 3)

    assert len(sampled) == 3
    assert all(item in segments for item in sampled)


def test_apply_draw_percent_defers_full_parse_when_streaming() -> None:
    view = _make_toolpath()
    view._full_parse_skipped = True
    view._last_gcode_lines = ["G0"] * 20
    view._streaming_mode = True

    view._apply_draw_percent(100, update_scale=False)

    assert view._deferred_full_parse
    assert view._draw_percent_text.value == "100%"


def test_apply_draw_percent_triggers_full_parse_when_idle() -> None:
    calls = []
    view = _make_toolpath()
    view._full_parse_skipped = True
    view._last_gcode_lines = ["G0"] * 20
    view._streaming_mode = False
    view.set_gcode_async = lambda lines, lines_hash=None: calls.append(lines)

    view._apply_draw_percent(100, update_scale=False)

    assert calls == [view._last_gcode_lines]


def test_clamp_draw_percent_defaults_on_invalid() -> None:
    view = _make_toolpath()

    assert view._clamp_draw_percent("bad") == view._draw_percent_default


def test_apply_draw_percent_updates_scale_when_requested() -> None:
    view = _make_toolpath()

    class _Scale:
        def __init__(self) -> None:
            self.value = None

        def set(self, value) -> None:
            self.value = value

    view.draw_percent_scale = _Scale()

    view._apply_draw_percent(60, update_scale=True)

    assert view._draw_percent_text.value == "60%"
    assert view.draw_percent_scale.value == 60


def test_build_projection_cache_filters_colors() -> None:
    view = _make_toolpath()
    view.segments = [
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.0, "rapid"),
        (1.0, 1.0, 0.0, 2.0, 2.0, 0.0, "feed"),
    ]
    view._draw_percent = 100
    view._project = lambda x, y, z: (x, y)
    view._report_perf = lambda _label, _duration: None

    proj, bounds, drawn, total = view._build_projection_cache((False, True, False), None)

    assert drawn == 1
    assert total == 2
    assert proj[0][-1] == "feed"
    assert bounds == (1.0, 2.0, 1.0, 2.0)
