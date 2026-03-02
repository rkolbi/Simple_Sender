import pytest

from simple_sender.utils.grbl_errors import (
    annotate_grbl_alarm,
    annotate_grbl_error,
    annotate_grbl_message,
    extract_grbl_code,
)

pytestmark = pytest.mark.unit


def test_annotate_grbl_error_adds_description() -> None:
    annotated = annotate_grbl_error("error:2")
    assert "error:2 (" in annotated


def test_annotate_grbl_alarm_adds_description() -> None:
    annotated = annotate_grbl_alarm("ALARM:1")
    assert "ALARM:1 (" in annotated


def test_annotate_grbl_message_prefers_error() -> None:
    annotated = annotate_grbl_message("error:1")
    assert annotated.startswith("error:1")


def test_annotate_grbl_message_returns_alarm() -> None:
    annotated = annotate_grbl_message("ALARM:2")
    assert annotated.startswith("ALARM:2")


def test_extract_grbl_code_parses_error_and_definition() -> None:
    parsed = extract_grbl_code("error:38")
    assert parsed == ("error", 38, "Tool number > max supported.")


def test_extract_grbl_code_parses_alarm_and_definition() -> None:
    parsed = extract_grbl_code("ALARM:10")
    assert parsed is not None
    assert parsed[0] == "alarm"
    assert parsed[1] == 10
    assert "dual-axis second switch" in parsed[2]
