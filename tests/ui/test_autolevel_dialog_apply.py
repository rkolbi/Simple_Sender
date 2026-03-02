import pytest

from simple_sender.utils.constants import MAX_LINE_LENGTH

pytest.importorskip("tkinter")

from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import LevelFileResult
from simple_sender.ui.autolevel_dialog import workflow as autolevel_workflow

pytestmark = pytest.mark.ui


def _complete_height_map() -> HeightMap:
    height_map = HeightMap([0.0, 1.0], [0.0, 1.0])
    for x in height_map.xs:
        for y in height_map.ys:
            height_map.set_point(x, y, 0.0)
    return height_map


def test_apply_auto_level_falls_back_to_temp(monkeypatch) -> None:
    height_map = _complete_height_map()
    calls = []

    def fake_write(path, lines, *, header_lines=None):
        calls.append(path)
        if path == "job-AL.gcode":
            return LevelFileResult(None, 0, "denied", True)
        return LevelFileResult(path, len(lines), None, False)

    result, is_temp, warning = autolevel_workflow._apply_auto_level_to_path(
        source_path="job.gcode",
        source_lines=["G1 X0 Y0 Z0"],
        output_path="job-AL.gcode",
        temp_path_fn=lambda _path: "job-AL-temp.gcode",
        height_map=height_map,
        arc_step_rad=0.1,
        interpolation="bilinear",
        header_lines=["(Auto-Level from job.gcode)"],
        streaming_mode=False,
        log_fn=None,
        write_gcode_lines_fn=fake_write,
    )

    assert is_temp is True
    assert "temporary leveled file" in warning
    assert result.output_path == "job-AL-temp.gcode"
    assert calls == ["job-AL.gcode", "job-AL-temp.gcode"]


def test_apply_auto_level_streaming_preserves_comments(tmp_path, monkeypatch) -> None:
    height_map = _complete_height_map()
    output_path = tmp_path / "job-AL.gcode"
    header = "(Auto-Level from job.gcode)"
    long_number = "1" * (MAX_LINE_LENGTH + 10)

    def fake_level_file(_input_path, output_path, *_args, **kwargs):
        headers = kwargs.get("header_lines") or []
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            for line in headers:
                f.write(line)
                f.write("\n")
            f.write("\n")
            f.write("(keep this)\n")
            f.write(f"N{long_number} G1 X0 Y0 (inline)\n")
        return LevelFileResult(str(output_path), len(headers) + 2, None, False)

    result, is_temp, warning = autolevel_workflow._apply_auto_level_to_path(
        source_path="job.gcode",
        source_lines=None,
        output_path=str(output_path),
        temp_path_fn=lambda _path: str(tmp_path / "job-AL-temp.gcode"),
        height_map=height_map,
        arc_step_rad=0.1,
        interpolation="bilinear",
        header_lines=[header],
        streaming_mode=True,
        log_fn=None,
        level_gcode_file_fn=fake_level_file,
    )

    assert result.error is None
    assert is_temp is False
    assert warning is None
    contents = output_path.read_text(encoding="utf-8").splitlines()
    assert contents[0] == header
    assert "" in contents
    assert "(keep this)" in contents
    assert "(inline)" in contents
    assert any(line == "G1X0Y0" for line in contents)
    assert long_number not in "\n".join(contents)


def test_apply_auto_level_streaming_error_reports_raw_line(tmp_path, monkeypatch) -> None:
    height_map = _complete_height_map()
    output_path = tmp_path / "job-AL.gcode"
    header = "(Auto-Level from job.gcode)"
    long_number = "2" * (MAX_LINE_LENGTH + 20)

    def fake_level_file(_input_path, output_path, *_args, **kwargs):
        headers = kwargs.get("header_lines") or []
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            for line in headers:
                f.write(line)
                f.write("\n")
            f.write("(note)\n")
            f.write(f"G2 X{long_number} Y0\n")
            f.write(f"G2 X{long_number} Y1\n")
        return LevelFileResult(str(output_path), len(headers) + 3, None, False)

    result, _is_temp, _warning = autolevel_workflow._apply_auto_level_to_path(
        source_path="job.gcode",
        source_lines=None,
        output_path=str(output_path),
        temp_path_fn=lambda _path: str(tmp_path / "job-AL-temp.gcode"),
        height_map=height_map,
        arc_step_rad=0.1,
        interpolation="bilinear",
        header_lines=[header],
        streaming_mode=True,
        log_fn=None,
        level_gcode_file_fn=fake_level_file,
    )

    assert result.error is not None
    assert "2 non-empty line(s)" in result.error
    assert "line 3" in result.error
    assert not output_path.exists()


def test_apply_auto_level_streaming_skips_rewrite_when_unchanged(tmp_path, monkeypatch) -> None:
    height_map = _complete_height_map()
    output_path = tmp_path / "job-AL.gcode"
    header = "(Auto-Level from job.gcode)"
    original = "\n".join(
        [
            header,
            "(note)",
            "",
            "G1 X0 Y0",
            "G1 X1 Y1",
        ]
    )
    output_path.write_text(original + "\n", encoding="utf-8")

    def fake_level_file(_input_path, output_path, *_args, **_kwargs):
        return LevelFileResult(str(output_path), 5, None, False)

    monkeypatch.setattr(autolevel_workflow, "level_gcode_file", fake_level_file)

    def fail_temp(*_args, **_kwargs):
        raise AssertionError("temp file should not be created")

    monkeypatch.setattr(autolevel_workflow.tempfile, "NamedTemporaryFile", fail_temp)

    result, is_temp, warning = autolevel_workflow._apply_auto_level_to_path(
        source_path="job.gcode",
        source_lines=None,
        output_path=str(output_path),
        temp_path_fn=lambda _path: str(tmp_path / "job-AL-temp.gcode"),
        height_map=height_map,
        arc_step_rad=0.1,
        interpolation="bilinear",
        header_lines=[header],
        streaming_mode=True,
        log_fn=None,
        level_gcode_file_fn=fake_level_file,
    )

    assert result.error is None
    assert is_temp is False
    assert warning is None
    assert output_path.read_text(encoding="utf-8") == original + "\n"
