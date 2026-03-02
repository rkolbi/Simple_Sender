import queue

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.gcode import pipeline as gcode_pipeline

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value


class _ToolpathPanel:
    def __init__(self) -> None:
        self.applied = False
        self.last_lines = None
        self.last_result = None

    def get_arc_step_rad(self, _line_count: int) -> float:
        return 0.1

    def apply_parse_result(self, lines, result, *, lines_hash=None):
        self.applied = True
        self.last_lines = lines
        self.last_result = result
        self.last_hash = lines_hash


class _Thread:
    def __init__(self, target, daemon=False) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def test_schedule_gcode_parse_applies_result(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline.threading, "Thread", _Thread)
    parse_calls = {}

    class _Result:
        def __init__(self) -> None:
            self.segments = [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, "rapid")]
            self.bounds = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
            self.moves = []

    def _parse(lines, arc_step, keep_running=None, max_segments=None, include_moves=True):
        _ = lines, arc_step, keep_running
        parse_calls["max_segments"] = max_segments
        parse_calls["include_moves"] = include_moves
        return _Result()

    monkeypatch.setattr(gcode_pipeline, "parse_gcode_lines", _parse)

    class _App:
        def __init__(self) -> None:
            self.toolpath_panel = _ToolpathPanel()
            self._gcode_parse_token = 0
            self._last_parse_result = None
            self._last_parse_hash = None
            self._stats_token = 0
            self._last_stats = None
            self._last_rate_source = None
            self.ui_q = queue.Queue()
            self.gcode_stats_var = _Var("")
            self._update_called = False

        def after(self, _delay_ms: int, func):
            func()
            return None

        def _update_gcode_stats(self, _lines, parse_result=None) -> None:
            self._update_called = True

    app = _App()
    lines = ["G0 X0", "G1 X1"]

    gcode_pipeline.schedule_gcode_parse(app, lines, "hash")

    assert app._last_parse_result is not None
    assert app._last_parse_hash == "hash"
    assert app.toolpath_panel.applied
    assert app._update_called
    assert parse_calls["include_moves"] is True
    assert parse_calls["max_segments"] is not None


def test_schedule_gcode_parse_handles_error(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(gcode_pipeline, "parse_gcode_lines", _boom)
    monkeypatch.setattr(gcode_pipeline.threading, "Thread", _Thread)

    class _App:
        def __init__(self) -> None:
            self.toolpath_panel = _ToolpathPanel()
            self._gcode_parse_token = 0
            self._last_parse_result = "old"
            self._last_parse_hash = "old"
            self._stats_token = 0
            self._last_stats = "old"
            self._last_rate_source = "old"
            self.ui_q = queue.Queue()
            self.gcode_stats_var = _Var("")

        def after(self, _delay_ms: int, func):
            func()
            return None

    app = _App()

    gcode_pipeline.schedule_gcode_parse(app, ["G0 X0"], "hash")

    assert app._last_parse_result is None
    assert app._last_parse_hash is None
    assert app._stats_token == 1
    assert app._last_stats is None
    assert app._last_rate_source is None
    assert app.gcode_stats_var.value == "Estimate unavailable"
    logs = list(app.ui_q.queue)
    assert any(evt[0] == "log" and "Parse failed" in evt[1] for evt in logs)
