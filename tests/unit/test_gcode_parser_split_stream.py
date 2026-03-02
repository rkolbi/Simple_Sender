import pytest

from simple_sender.gcode_parser import clean_gcode_line, split_gcode_lines_stream

pytestmark = pytest.mark.unit


def test_split_stream_rewrites_overlong_raw_inline_comment() -> None:
    line = "G1 X1 Y1 (" + ("C" * 70) + ")"
    emitted: list[str] = []

    result = split_gcode_lines_stream(
        [line],
        max_len=80,
        clean_line=clean_gcode_line,
        preserve_raw=True,
        write_line=emitted.append,
    )

    assert result.failed_index is None
    assert result.modified_count == 1
    assert len(emitted) == 2
    assert emitted[0].startswith("(")
    assert emitted[1].startswith("G1")
    assert all((len(entry.encode("utf-8")) + 1) <= 80 for entry in emitted)


def test_split_stream_fails_on_overlong_comment_line() -> None:
    line = ";" + ("X" * 90)
    emitted: list[str] = []

    result = split_gcode_lines_stream(
        [line],
        max_len=80,
        clean_line=clean_gcode_line,
        preserve_raw=True,
        write_line=emitted.append,
    )

    assert result.failed_index == 0
    assert result.failed_len is not None
    assert result.failed_len > 80
    assert result.too_long == 1
    assert emitted == []


def test_split_stream_handles_long_arc_line_and_preserves_limit() -> None:
    line = "G2 X120.125 Y45.875 I10.250 J-5.125 F1200.0 (finishing arc pass around profile edge)"
    emitted: list[str] = []

    result = split_gcode_lines_stream(
        [line],
        max_len=80,
        clean_line=clean_gcode_line,
        preserve_raw=True,
        write_line=emitted.append,
    )

    assert result.failed_index is None
    assert emitted
    assert all((len(entry.encode("utf-8")) + 1) <= 80 for entry in emitted)


def test_split_stream_fails_on_overlong_unsplittable_token() -> None:
    line = "G1 X" + ("1" * 120)
    emitted: list[str] = []

    result = split_gcode_lines_stream(
        [line],
        max_len=80,
        clean_line=clean_gcode_line,
        preserve_raw=True,
        write_line=emitted.append,
    )

    assert result.failed_index == 0
    assert result.failed_len is not None
    assert result.failed_len > 80
    assert emitted == []
