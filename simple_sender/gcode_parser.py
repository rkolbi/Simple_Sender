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

from simple_sender.gcode_parser_core import (
    AXIS_WORDS,
    MAX_SPLIT_SEGMENTS,
    PAREN_COMMENT_PAT,
    SPLIT_ALLOWED_G_CODES,
    SPLIT_DECIMALS,
    UNSUPPORTED_AXIS_WORDS,
    WORD_PAT,
    GcodeMove,
    GcodeParseResult,
    clean_gcode_line,
    parse_gcode_lines,
    _arc_center_from_radius,
    _arc_sweep,
)
from simple_sender.gcode_parser_split import (
    GcodeSplitResult,
    GcodeSplitStreamResult,
    split_gcode_lines,
    split_gcode_lines_stream,
    _SplitState,
    _build_compact_line,
    _format_float,
    _format_word_from_str,
    _is_safe_word_line,
    _line_len_bytes,
    _split_linear_move,
    _trim_number_str,
)
