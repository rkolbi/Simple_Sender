#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from typing import Callable, Literal, Sequence

_HEX_COLOR_PAT = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_COLOR_PREFIX_PAT = re.compile(r"^\s*color\s*[:=]\s*(.*?)\s*$", re.IGNORECASE)
_TEXT_COLOR_PREFIX_PAT = re.compile(
    r"^\s*(?:text[_\s-]*color|foreground|fg)\s*[:=]\s*(.*?)\s*$",
    re.IGNORECASE,
)


class MacroFormatError(ValueError):
    def __init__(self, issues: Sequence[str]) -> None:
        normalized = [str(item).strip() for item in issues if str(item).strip()]
        self.issues = normalized
        super().__init__("; ".join(normalized) or "Unsupported macro format.")

    def format_details(self) -> str:
        return "\n".join(f"- {issue}" for issue in self.issues)


def _is_valid_color_token(token: str, color_validator: Callable[[str], bool] | None) -> bool:
    if not token:
        return False
    if color_validator is not None:
        try:
            return bool(color_validator(token))
        except Exception:
            return False
    return bool(_HEX_COLOR_PAT.fullmatch(token))


def parse_macro_color_line(
    line: str,
    *,
    kind: Literal["button", "text"] = "button",
    color_validator: Callable[[str], bool] | None = None,
) -> str | None:
    """Parse one macro color header line.

    Returns:
      - ``""`` when the header line is blank
      - normalized color token when valid
      - ``None`` when a non-blank line is invalid
    """
    raw = str(line).strip()
    if not raw:
        return ""
    prefix_match = _COLOR_PREFIX_PAT.match(raw)
    if kind == "text":
        prefix_match = _TEXT_COLOR_PREFIX_PAT.match(raw)
    token = prefix_match.group(1).strip() if prefix_match else raw
    if _is_valid_color_token(token, color_validator):
        return token
    return None


def validate_macro_header(
    lines: Sequence[str],
    *,
    color_validator: Callable[[str], bool] | None = None,
) -> None:
    issues: list[str] = []
    if len(lines) < 5:
        issues.append(
            "macro file must include label, tooltip, button color, text color, and at least one body line"
        )
    else:
        third_line = str(lines[2]).strip()
        fourth_line = str(lines[3]).strip()
        if third_line and (
            parse_macro_color_line(
                third_line,
                kind="button",
                color_validator=color_validator,
            )
            is None
        ):
            issues.append("line 3 button color is invalid")
        if fourth_line and (
            parse_macro_color_line(
                fourth_line,
                kind="text",
                color_validator=color_validator,
            )
            is None
        ):
            issues.append("line 4 text color is invalid")
        if not any(str(line).strip() for line in lines[4:]):
            issues.append("macro body must contain at least one non-blank line")
    if issues:
        raise MacroFormatError(issues)


def parse_macro_header(
    lines: Sequence[str],
    *,
    color_validator: Callable[[str], bool] | None = None,
) -> tuple[str, str, str | None, str | None, int]:
    """Parse macro metadata.

    Returns (name, tooltip, button_color, button_text_color, body_start_index).

    Supported header format:
      - line 1: label
      - line 2: tooltip
      - line 3: button color or blank
      - line 4: button text color or blank
      - remaining lines: macro body

    Raises:
        MacroFormatError: If the file does not match the supported macro format.
    """
    validate_macro_header(lines, color_validator=color_validator)
    name = str(lines[0]).strip() if lines else ""
    tip = str(lines[1]).strip() if len(lines) > 1 else ""
    body_start = 4
    third_line = str(lines[2]).strip() if len(lines) > 2 else ""
    button_color_token = parse_macro_color_line(
        third_line,
        kind="button",
        color_validator=color_validator,
    )
    button_color = button_color_token or None
    fourth_line = str(lines[3]).strip() if len(lines) > 3 else ""
    button_text_color_token = parse_macro_color_line(
        fourth_line,
        kind="text",
        color_validator=color_validator,
    )
    button_text_color = button_text_color_token or None

    return name, tip, button_color, button_text_color, body_start
