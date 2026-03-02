import pytest

from simple_sender.utils.macro_headers import parse_macro_color_line, parse_macro_header

pytestmark = pytest.mark.unit


def _color_validator(color: str) -> bool:
    return color in {"red", "white", "#00ff00", "#ffffff"}


def test_parse_macro_color_line_blank_returns_empty_string() -> None:
    assert parse_macro_color_line("", color_validator=_color_validator) == ""


def test_parse_macro_color_line_accepts_prefixed_button_color() -> None:
    assert (
        parse_macro_color_line(
            "color: red",
            kind="button",
            color_validator=_color_validator,
        )
        == "red"
    )


def test_parse_macro_color_line_accepts_prefixed_text_color() -> None:
    assert (
        parse_macro_color_line(
            "fg: white",
            kind="text",
            color_validator=_color_validator,
        )
        == "white"
    )


def test_parse_macro_color_line_invalid_returns_none() -> None:
    assert (
        parse_macro_color_line(
            "color: nope",
            kind="button",
            color_validator=_color_validator,
        )
        is None
    )


def test_parse_macro_header_legacy_two_line_header() -> None:
    name, tip, color, text_color, body_start = parse_macro_header(["Macro", "Tip", "G21"])
    assert name == "Macro"
    assert tip == "Tip"
    assert color is None
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_accepts_hex_on_line_three() -> None:
    name, tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "#00ff00", "G21"]
    )
    assert name == "Macro"
    assert tip == "Tip"
    assert color == "#00ff00"
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_accepts_named_color_with_validator() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "red", "G21"],
        color_validator=_color_validator,
    )
    assert color == "red"
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_ignores_invalid_named_color_without_validator() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(["Macro", "Tip", "red", "G21"])
    assert color is None
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_accepts_prefixed_color_header() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "color: red", "G21"],
        color_validator=_color_validator,
    )
    assert color == "red"
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_prefixed_invalid_color_still_reserves_line_three() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "color: definitely-not-a-color", "G21"],
        color_validator=_color_validator,
    )
    assert color is None
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_accepts_line_four_text_color() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "#00ff00", "#ffffff", "G21"],
        color_validator=_color_validator,
    )
    assert color == "#00ff00"
    assert text_color == "#ffffff"
    assert body_start == 4


def test_parse_macro_header_line_four_non_color_is_ignored_header() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "#00ff00", "G21"],
        color_validator=_color_validator,
    )
    assert color == "#00ff00"
    assert text_color is None
    assert body_start == 4


def test_parse_macro_header_accepts_prefixed_text_color() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "#00ff00", "text_color: white", "G21"],
        color_validator=_color_validator,
    )
    assert color == "#00ff00"
    assert text_color == "white"
    assert body_start == 4


def test_parse_macro_header_prefixed_invalid_text_color_reserves_line_four() -> None:
    _name, _tip, color, text_color, body_start = parse_macro_header(
        ["Macro", "Tip", "#00ff00", "text_color: nope", "G21"],
        color_validator=_color_validator,
    )
    assert color == "#00ff00"
    assert text_color is None
    assert body_start == 4
