import pytest

from simple_sender import gcode_validator
from simple_sender.gcode_validator import (
    DETAIL_LINE_LIMIT,
    format_validation_details,
    format_validation_report,
    validate_gcode_lines,
)
from simple_sender.utils.constants import MAX_LINE_LENGTH

pytestmark = pytest.mark.unit


def test_validate_gcode_lines_flags_issues() -> None:
    long_line = "G1 X" + ("1" * MAX_LINE_LENGTH)
    lines = [
        "G91",
        "G99",
        "M11",
        "M3.5",
        "A1",
        "H2",
        long_line,
    ]

    report = validate_gcode_lines(lines)

    assert report.long_line_count == 1
    assert report.long_lines[0][0] == 7
    assert report.unsupported_axes["A"] == 1
    assert report.unsupported_words["H"] == 1
    assert report.unsupported_g_codes["G99"] == 1
    assert report.unsupported_m_codes["M11"] == 1
    assert report.unsupported_m_codes["M3.5"] == 1
    assert "G91 (incremental distance mode)" in report.modal_hazards


def test_validation_report_formats_summary() -> None:
    report = validate_gcode_lines(["G99", "M11"])
    text = format_validation_report(report)

    assert "Unsupported G-codes" in text
    assert "Unsupported M-codes" in text


def test_validation_warns_on_g901() -> None:
    report = validate_gcode_lines(["G90.1"])

    assert "G90.1" not in report.unsupported_g_codes
    assert report.grbl_warnings["G90.1 (arc center absolute) is not supported by GRBL 1.1h"] == 1


def test_validation_details_trims_long_lines() -> None:
    long_line = "G1 X" + ("1" * (MAX_LINE_LENGTH * 3))
    report = validate_gcode_lines([long_line])
    details = format_validation_details(report)

    assert "..." in details


def test_validate_gcode_deduplicates_line_issues_and_handles_nonword_long_line() -> None:
    nonword_long_line = "(" + ("x" * MAX_LINE_LENGTH) + ")"
    report = validate_gcode_lines(["", "A1 A2", nonword_long_line])

    assert report.unsupported_axes["A"] == 2
    assert report.unsupported_words["A"] == 2
    assert report.line_issue_count == 2
    assert report.line_issues[0].issues.count("Unsupported axis A") == 1
    assert report.line_issues[0].issues.count("Unknown word letter A") == 0
    assert report.line_issues[1].issues[0].startswith("Long line (")


def test_validate_gcode_formats_non_integer_gcode_labels() -> None:
    report = validate_gcode_lines(["G38.25"])

    assert report.unsupported_g_codes["G38.25"] == 1


def test_validate_gcode_ignores_unparseable_g_and_m_tokens() -> None:
    class _Pattern:
        def findall(self, _line: str):
            return [("G", "not_a_float"), ("M", "still_not_a_float")]

    report = validate_gcode_lines(["IGNORED"], word_pattern=_Pattern())

    assert report.line_issue_count == 0
    assert report.unsupported_g_codes == {}
    assert report.unsupported_m_codes == {}


def test_validate_gcode_truncates_line_issue_details_after_limit() -> None:
    lines = [f"G99 X{idx}" for idx in range(DETAIL_LINE_LIMIT + 2)]

    report = validate_gcode_lines(lines)

    assert report.line_issue_count == DETAIL_LINE_LIMIT + 2
    assert len(report.line_issues) == DETAIL_LINE_LIMIT
    assert report.line_issues_truncated is True


def test_validation_report_handles_none_and_no_issue_paths() -> None:
    assert format_validation_report(None) == "G-code validation: unavailable."

    clean_text = format_validation_report(validate_gcode_lines(["G0 X0 Y0 Z0"]))

    assert "No issues detected." in clean_text


def test_validation_report_formats_long_axes_warning_modal_and_unknown_sections() -> None:
    long_line = "G1 X" + ("1" * MAX_LINE_LENGTH)
    report = validate_gcode_lines([long_line, "G91 G90.1 A1 H2"])

    text = format_validation_report(report)

    assert "Long lines" in text
    assert "Unsupported axes" in text
    assert "GRBL warnings" in text
    assert "Modal hazards" in text
    assert "Unknown word letters" in text


def test_trim_detail_line_handles_small_and_within_limit_text() -> None:
    assert gcode_validator._trim_detail_line("abcdef", limit=2) == "ab"
    assert gcode_validator._trim_detail_line("abc", limit=10) == "abc"


def test_validation_details_handles_none_clean_warning_only_and_truncated() -> None:
    assert format_validation_details(None) == "G-code validation details: unavailable."

    clean_details = format_validation_details(validate_gcode_lines(["G0 X0"]))
    assert clean_details.endswith("No issues detected.")

    warning_only = validate_gcode_lines(["G90.1"])
    warning_details = format_validation_details(warning_only)
    assert "GRBL warnings:" in warning_details
    assert "No other issues detected." in warning_details

    truncated = validate_gcode_lines([f"G99 X{idx}" for idx in range(DETAIL_LINE_LIMIT + 5)])
    truncated_details = format_validation_details(truncated)
    assert "Showing first" in truncated_details
    assert "... additional issue lines omitted." in truncated_details
