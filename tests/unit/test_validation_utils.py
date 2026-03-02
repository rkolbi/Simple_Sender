import pytest

from simple_sender.utils.exceptions import InvalidParameterError, InvalidRangeError
from simple_sender.utils.validation import (
    validate_baud_rate,
    validate_feed_rate,
    validate_grbl_setting,
    validate_interval,
    validate_port_name,
    validate_rpm,
    validate_unit_mode,
)

pytestmark = pytest.mark.unit


def test_validate_feed_rate_rejects_non_positive() -> None:
    with pytest.raises(InvalidParameterError):
        validate_feed_rate(0)
    with pytest.raises(InvalidParameterError):
        validate_feed_rate(-1)


def test_validate_unit_mode_rejects_invalid() -> None:
    with pytest.raises(InvalidParameterError):
        validate_unit_mode("cm")


def test_validate_grbl_setting_unknown_id() -> None:
    with pytest.raises(InvalidParameterError):
        validate_grbl_setting(999, "1")


def test_validate_grbl_setting_out_of_range() -> None:
    with pytest.raises(InvalidRangeError):
        validate_grbl_setting(110, "999999")


def test_validate_grbl_setting_accepts_numeric() -> None:
    setting_id, value = validate_grbl_setting(110, "1000")
    assert setting_id == 110
    assert value == 1000.0


def test_validate_baud_rate_rejects_invalid() -> None:
    with pytest.raises(InvalidParameterError):
        validate_baud_rate(123)


def test_validate_interval_rejects_below_minimum() -> None:
    with pytest.raises(InvalidParameterError):
        validate_interval(-1, min_val=0.1)


def test_validate_port_name_rejects_blank() -> None:
    with pytest.raises(InvalidParameterError):
        validate_port_name(" ")


def test_validate_rpm_rejects_out_of_range() -> None:
    with pytest.raises(InvalidRangeError):
        validate_rpm(1000000, min_rpm=0, max_rpm=1000)
