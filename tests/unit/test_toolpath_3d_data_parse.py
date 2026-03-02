import pytest

from simple_sender.ui.toolpath import toolpath_3d_data

pytestmark = pytest.mark.unit


class _Dummy(toolpath_3d_data.Toolpath3DDataMixin):
    def __init__(self) -> None:
        self._arc_step_rad = 0.25
        self._parse_token = 3
        self._perf = []

    def _report_perf(self, label: str, duration: float):
        self._perf.append((label, duration))


def test_parse_gcode_disables_move_collection(monkeypatch) -> None:
    calls = {}

    class _Result:
        segments = [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, "rapid")]
        bounds = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0)

    def _parse(lines, arc_step_rad, keep_running=None, include_moves=True):
        _ = lines, arc_step_rad
        assert callable(keep_running)
        assert keep_running() is True
        calls["include_moves"] = include_moves
        return _Result()

    monkeypatch.setattr(toolpath_3d_data, "parse_gcode_lines", _parse)
    dummy = _Dummy()

    segments, bounds = dummy._parse_gcode(["G0 X0"], token=3)

    assert calls["include_moves"] is False
    assert segments == _Result.segments
    assert bounds == _Result.bounds
