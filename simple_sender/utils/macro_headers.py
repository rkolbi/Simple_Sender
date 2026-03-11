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


def parse_macro_header(
    lines: Sequence[str],
    *,
    color_validator: Callable[[str], bool] | None = None,
) -> tuple[str, str, str | None, str | None, int]:
    """Parse macro metadata.

    Returns (name, tooltip, button_color, button_text_color, body_start_index).

    Supports both legacy and new header layouts:
      - legacy: line 1 label, line 2 tooltip, body starts at line 3
      - new: line 3 button color (or blank), line 4 text color (or blank)

    Body start is determined by whether line 3/4 are recognizable header lines.
    """
    name = str(lines[0]).strip() if lines else ""
    tip = str(lines[1]).strip() if len(lines) > 1 else ""
    button_color: str | None = None
    button_text_color: str | None = None
    body_start = 2

    third_line = str(lines[2]).strip() if len(lines) > 2 else ""
    color_token = parse_macro_color_line(
        third_line,
        kind="button",
        color_validator=color_validator,
    )
    third_explicit = bool(_COLOR_PREFIX_PAT.match(third_line))
    third_reserved = (third_line == "") or (color_token is not None) or third_explicit
    if color_token:
        button_color = color_token
    if third_reserved:
        body_start = 3
        fourth_line = str(lines[3]).strip() if len(lines) > 3 else ""
        text_color_token = parse_macro_color_line(
            fourth_line,
            kind="text",
            color_validator=color_validator,
        )
        fourth_explicit = bool(_TEXT_COLOR_PREFIX_PAT.match(fourth_line))
        fourth_reserved = (
            fourth_line == ""
            or text_color_token is not None
            or fourth_explicit
        )
        if text_color_token:
            button_text_color = text_color_token
        if fourth_reserved:
            body_start = 4

    return name, tip, button_color, button_text_color, body_start
