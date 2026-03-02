import pytest

from simple_sender.kasa_accessory import SpindleCommandDetector


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("M3", True),
        ("M03", True),
        ("M3 S1000", True),
        ("  m4   s5000", True),
        ("M04 S5000", True),
        ("M5", False),
        ("M05", False),
        (" m5 ", False),
        ("m03 s12000", True),
    ],
)
def test_spindle_detector_matches_expected_codes(line: str, expected: bool) -> None:
    assert SpindleCommandDetector.detect_state_change(line) is expected


@pytest.mark.parametrize(
    "line",
    [
        "M30",
        "G0 X0",
        "; M3 in comment",
        "(M4 in comment)",
        "",
        "  ",
        "M50",
        "T1 M6",
    ],
)
def test_spindle_detector_ignores_non_spindle_commands(line: str) -> None:
    assert SpindleCommandDetector.detect_state_change(line) is None
