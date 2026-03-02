from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import level_gcode_file, level_gcode_lines


def _height_map_x_offset() -> HeightMap:
    height_map = HeightMap([0.0, 10.0], [0.0, 10.0])
    height_map.set_index(0, 0, 0.0)
    height_map.set_index(1, 0, 1.0)
    height_map.set_index(0, 1, 0.0)
    height_map.set_index(1, 1, 1.0)
    return height_map


def test_level_gcode_lines_applies_height_offset() -> None:
    height_map = _height_map_x_offset()
    lines = ["G90", "G1 X0 Y0 Z0", "G1 X10 Y0 Z0"]

    result = level_gcode_lines(lines, height_map, interpolation="bilinear")

    assert result.error is None
    assert result.lines[-1] == "G1X10Y0Z1"


def test_level_gcode_lines_rejects_incremental_mode() -> None:
    height_map = _height_map_x_offset()
    lines = ["G91", "G1 X1"]

    result = level_gcode_lines(lines, height_map)

    assert result.error is not None
    assert "Incremental" in result.error


def test_level_gcode_file_writes_output(tmp_path) -> None:
    height_map = _height_map_x_offset()
    input_path = tmp_path / "job.gcode"
    output_path = tmp_path / "job_leveled.gcode"
    input_path.write_text("G90\nG1 X0 Y0 Z0\nG1 X10 Y0 Z0\n", encoding="utf-8")

    result = level_gcode_file(str(input_path), str(output_path), height_map, interpolation="bilinear")

    assert result.error is None
    assert result.output_path == str(output_path)
    assert result.lines_written == 3
    out_lines = output_path.read_text(encoding="utf-8").splitlines()
    assert out_lines[-1] == "G1X10Y0Z1"


def test_level_gcode_file_writes_header_lines(tmp_path) -> None:
    height_map = _height_map_x_offset()
    input_path = tmp_path / "job.gcode"
    output_path = tmp_path / "job_leveled.gcode"
    input_path.write_text("G90\nG1 X0 Y0 Z0\n", encoding="utf-8")
    header_lines = ["(Auto-Level from job.gcode)"]

    result = level_gcode_file(
        str(input_path),
        str(output_path),
        height_map,
        interpolation="bilinear",
        header_lines=header_lines,
    )

    assert result.error is None
    out_lines = output_path.read_text(encoding="utf-8").splitlines()
    assert out_lines[0] == header_lines[0]


def test_level_gcode_file_removes_output_on_error(tmp_path) -> None:
    height_map = _height_map_x_offset()
    input_path = tmp_path / "bad.gcode"
    output_path = tmp_path / "bad_leveled.gcode"
    input_path.write_text("G91\nG1 X1\n", encoding="utf-8")

    result = level_gcode_file(str(input_path), str(output_path), height_map)

    assert result.output_path is None
    assert result.error is not None
    assert "Incremental" in result.error
    assert not output_path.exists()
