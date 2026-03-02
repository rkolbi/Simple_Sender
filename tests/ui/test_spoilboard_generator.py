from datetime import datetime

import pytest

pytest.importorskip("tkinter")

from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.ui.dialogs import spoilboard_generator

pytestmark = pytest.mark.ui


def test_default_spoilboard_program_name_uses_timestamp() -> None:
    name = spoilboard_generator.default_spoilboard_program_name(
        datetime(2026, 2, 21, 13, 4, 5)
    )
    assert name == "surfacing-20260221-130405.nc"


def test_build_spoilboard_gcode_lines_contains_expected_commands() -> None:
    params = spoilboard_generator.SpoilboardGeneratorParams(
        width=100.0,
        height=40.0,
        tool_diameter=10.0,
        stepover_pct=50.0,
        feed_xy=1200.0,
        feed_z=300.0,
        spindle_rpm=12000,
        start_x=0.0,
        start_y=0.0,
        surfacing_depth=0.254,
    )
    lines = spoilboard_generator.build_spoilboard_gcode_lines(params)
    assert lines[0] == "G21"
    assert "G91" in lines
    assert "G0 Z10.000" in lines
    assert f"G0 X{params.start_x:.3f} Y{params.start_y:.3f}" in lines
    assert "M3 S12000" in lines
    assert "G4 P5" in lines
    assert f"G1 Z{-params.surfacing_depth:.3f} F{params.feed_z:.3f}" in lines
    assert lines.index("M3 S12000") < lines.index(f"G0 X{params.start_x:.3f} Y{params.start_y:.3f}")
    assert lines.index(f"G0 X{params.start_x:.3f} Y{params.start_y:.3f}") < lines.index(
        f"G1 Z{-params.surfacing_depth:.3f} F{params.feed_z:.3f}"
    )
    assert "G1 Z-10.000" not in lines
    assert "G1 Z-0.200" not in lines
    assert lines[-2] == "M5"
    assert lines[-1] == "M30"


def test_build_spoilboard_gcode_lines_reports_incremental_hazard_only() -> None:
    params = spoilboard_generator.SpoilboardGeneratorParams(
        width=120.0,
        height=60.0,
        tool_diameter=20.0,
        stepover_pct=70.0,
        feed_xy=900.0,
        feed_z=250.0,
        spindle_rpm=18000,
        start_x=0.0,
        start_y=0.0,
        surfacing_depth=0.5,
    )
    lines = spoilboard_generator.build_spoilboard_gcode_lines(params)
    report = validate_gcode_lines(lines)

    assert report.line_issue_count == 2
    assert report.modal_hazards == {"G91 (incremental distance mode)"}


def test_build_spoilboard_gcode_lines_allows_zero_depth() -> None:
    params = spoilboard_generator.SpoilboardGeneratorParams(
        width=10.0,
        height=10.0,
        tool_diameter=10.0,
        stepover_pct=50.0,
        feed_xy=600.0,
        feed_z=150.0,
        spindle_rpm=10000,
        start_x=1.0,
        start_y=2.0,
        surfacing_depth=0.0,
    )
    lines = spoilboard_generator.build_spoilboard_gcode_lines(params)
    assert "G1 Z0.000 F150.000" in lines
    assert "G1 Z-0.000 F150.000" not in lines


def test_resolve_default_surfacing_depth_mm_reads_configured_value() -> None:
    defaults = {"surfacing_depth": 0.01}
    depth_mm, used_default_text = spoilboard_generator._resolve_default_surfacing_depth_mm(defaults)
    assert depth_mm == pytest.approx(0.01)
    assert used_default_text is False


def test_resolve_default_surfacing_depth_mm_keeps_user_value() -> None:
    defaults = {"surfacing_depth": 0.01, "surfacing_depth_user_set": True}
    depth_mm, used_default_text = spoilboard_generator._resolve_default_surfacing_depth_mm(defaults)
    assert depth_mm == pytest.approx(0.01)
    assert used_default_text is False


def test_resolve_default_surfacing_depth_mm_formats_default_when_not_user_set() -> None:
    defaults = {"surfacing_depth": 0.5, "surfacing_depth_user_set": False}
    depth_mm, used_default_text = spoilboard_generator._resolve_default_surfacing_depth_mm(defaults)
    assert depth_mm == pytest.approx(0.5)
    assert used_default_text is True


def test_one_step_smaller_font_size_handles_positive_and_negative() -> None:
    assert spoilboard_generator._one_step_smaller_font_size(10) == 9
    assert spoilboard_generator._one_step_smaller_font_size(1) == 1
    assert spoilboard_generator._one_step_smaller_font_size(-10) == -9
    assert spoilboard_generator._one_step_smaller_font_size(-1) == -1


def test_load_generated_gcode_into_app_uses_apply_pipeline() -> None:
    class _Notebook:
        def __init__(self) -> None:
            self.selected = None

        def select(self, tab) -> None:
            self.selected = tab

    class _App:
        def __init__(self) -> None:
            self.notebook = _Notebook()
            self.gcode_tab = object()
            self.loaded = None

        def _apply_loaded_gcode(self, path: str, lines: list[str], validated: bool = False) -> None:
            self.loaded = (path, lines, validated)

    app = _App()
    spoilboard_generator._load_generated_gcode_into_app(
        app,
        "G21\nG0 X0 Y0\n",
        "surfacing-20260221-130405.nc",
    )

    assert app.notebook.selected is app.gcode_tab
    assert app.loaded == (
        "surfacing-20260221-130405.nc",
        ["G21", "G0 X0 Y0"],
        False,
    )


def test_save_generated_gcode_writes_file_and_updates_status(monkeypatch, tmp_path) -> None:
    target = tmp_path / "surfacing-test.nc"
    monkeypatch.setattr(spoilboard_generator, "get_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        spoilboard_generator,
        "run_file_dialog",
        lambda *_args, **_kwargs: str(target),
    )

    class _Status:
        def __init__(self) -> None:
            self.text = ""

        def config(self, **kwargs) -> None:
            self.text = kwargs.get("text", self.text)

    class _Streaming:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log(self, message: str) -> None:
            self.messages.append(message)

    class _App:
        def __init__(self) -> None:
            self.settings = {}
            self.status = _Status()
            self.streaming_controller = _Streaming()

    app = _App()
    saved_path = spoilboard_generator._save_generated_gcode(
        app,
        "G21\nM30\n",
        default_name="surfacing-20260221-130405.nc",
    )

    assert saved_path == str(target)
    assert target.read_text(encoding="utf-8") == "G21\nM30\n"
    assert app.status.text == f"Saved: {target}"
    assert app.settings["last_gcode_dir"] == str(tmp_path)
    assert app.streaming_controller.messages[-1] == f"Saved: {target}"
