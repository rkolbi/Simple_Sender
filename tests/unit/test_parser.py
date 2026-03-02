import math

import pytest

from simple_sender.gcode_parser import parse_gcode_lines

pytestmark = pytest.mark.unit


def test_parse_relative_moves_update_bounds() -> None:
    lines = [
        "G90",
        "G0 X0 Y0",
        "G91",
        "G0 X1 Y2",
        "G0 X1 Y-1",
        "G90",
        "G0 X0 Y0",
    ]
    result = parse_gcode_lines(lines)
    assert result is not None
    bounds = result.bounds
    assert bounds is not None
    assert bounds[0] == pytest.approx(0.0)
    assert bounds[1] == pytest.approx(2.0)
    assert bounds[2] == pytest.approx(0.0)
    assert bounds[3] == pytest.approx(2.0)


def test_parse_arc_move_has_length() -> None:
    lines = ["G90", "G0 X0 Y0", "G2 X10 Y0 I5 J0"]
    result = parse_gcode_lines(lines, arc_step_rad=math.pi / 90)
    assert result is not None
    arc_moves = [move for move in result.moves if move.motion == 2]
    assert len(arc_moves) == 1
    arc_move = arc_moves[0]
    assert arc_move.arc_len == pytest.approx(math.pi * 5, rel=1e-3)
    assert any(seg[-1] == "arc" for seg in result.segments)


def test_parse_inch_units_update_bounds() -> None:
    lines = ["G20", "G0 X1 Y1", "G1 X2 Y1"]
    result = parse_gcode_lines(lines)
    assert result is not None
    bounds = result.bounds
    assert bounds is not None
    assert bounds[1] == pytest.approx(2 * 25.4)
    assert bounds[3] == pytest.approx(1 * 25.4)


def test_parse_full_circle_arc_r() -> None:
    lines = ["G90", "G0 X0 Y0", "G2 X0 Y0 R5"]
    result = parse_gcode_lines(lines, arc_step_rad=math.pi / 90)
    assert result is not None
    arc_moves = [move for move in result.moves if move.motion == 2]
    assert len(arc_moves) == 1
    assert arc_moves[0].arc_len == pytest.approx(2 * math.pi * 5, rel=1e-3)


def test_parse_invalid_arc_radius_skips_move() -> None:
    lines = ["G90", "G0 X0 Y0", "G2 X10 Y0 R1"]
    result = parse_gcode_lines(lines)
    assert result is not None
    arc_moves = [move for move in result.moves if move.motion == 2]
    assert not arc_moves
