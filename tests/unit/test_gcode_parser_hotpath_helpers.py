import pytest

from simple_sender import gcode_parser_core
from simple_sender import gcode_parser_split

pytestmark = pytest.mark.unit


def test_core_parse_words_and_collect_g_codes_skips_invalid_values() -> None:
    parsed, g_codes = gcode_parser_core._parse_words_and_collect_g_codes(
        [("G", "1"), ("X", "2.5"), ("G", "bad"), ("Y", "-3")]
    )

    assert parsed == [("G", 1.0), ("X", 2.5), ("Y", -3.0)]
    assert g_codes == {1.0}


def test_split_parse_axis_words_and_collect_g_codes_skips_invalid_values() -> None:
    parsed, g_codes = gcode_parser_split._parse_axis_words_and_collect_g_codes(
        [("G", "0"), ("Z", ".125"), ("G", "bad"), ("F", "1000")]
    )

    assert parsed == [("Z", 0.125)]
    assert g_codes == {0.0}
