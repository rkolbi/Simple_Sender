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

"""Split/compact G-code lines to fit GRBL's line-length limits.

Algorithm overview:
- Only "safe" word-only lines are eligible for compaction/splitting.
- Lines are first compacted by trimming numeric formats and dropping N words.
- Linear moves (G0/G1) may be split into multiple segments when needed.
- Splitting is disabled for inverse-time feed (G93) and unsupported axes.
- G92/G92.x offset handling is tracked to preserve coordinate semantics.

Edge cases to be aware of:
- Lines with comments or other non-word characters are not split; if too long,
  they are reported as failures.
- Unsupported axis words (A/B/C/etc.) block splitting for that line.
- Arcs (G2/G3) are never split; they rely on compaction only.
- In streaming mode with preserve_raw=True, long comment segments can still
  trigger failures even if the compacted motion line would fit.
- Length limits are measured in UTF-8 bytes and include the trailing newline.
"""

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Set

from simple_sender.gcode_parser_core import (
    AXIS_WORDS,
    MAX_SPLIT_SEGMENTS,
    SPLIT_ALLOWED_G_CODES,
    SPLIT_DECIMALS,
    UNSUPPORTED_AXIS_WORDS,
    WORD_PAT,
)

@dataclass
class GcodeSplitResult:
    lines: List[str]
    split_count: int
    modified_count: int
    failed_index: int | None = None
    failed_len: int | None = None


@dataclass
class GcodeSplitStreamResult:
    lines_written: int
    split_count: int
    modified_count: int
    failed_index: int | None = None
    failed_len: int | None = None
    too_long: int = 0


@dataclass
class _SplitState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    units: float = 1.0
    absolute: bool = True
    feed_mode: str = "G94"
    g92_offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    g92_enabled: bool = True
    last_motion: int = 1
    can_split: bool = True


def _line_len_bytes(line: str) -> int:
    return len(line.encode("utf-8")) + 1


def _trim_number_str(value: str) -> str:
    text = value.strip()
    if not text:
        return "0"
    sign = ""
    if text[0] in "+-":
        if text[0] == "-":
            sign = "-"
        text = text[1:]
    if "." in text:
        int_part, frac_part = text.split(".", 1)
        int_part = int_part.lstrip("0") or "0"
        frac_part = frac_part.rstrip("0")
        text = f"{int_part}.{frac_part}" if frac_part else int_part
    else:
        text = text.lstrip("0") or "0"
    if text == "0":
        sign = ""
    return sign + text


def _format_float(value: float, max_decimals: int) -> str:
    text = f"{value:.{max_decimals}f}"
    text = text.rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return text


def _format_word_from_str(letter: str, value: str, max_decimals: int | None = None) -> str:
    if max_decimals is None:
        return f"{letter}{_trim_number_str(value)}"
    try:
        number = float(value)
    except Exception:
        return f"{letter}{_trim_number_str(value)}"
    return f"{letter}{_format_float(number, max_decimals)}"


def _build_compact_line(
    words: list[tuple[str, str]],
    max_decimals: int | None = None,
    drop_line_numbers: bool = True,
) -> str:
    parts = []
    for w, val in words:
        if drop_line_numbers and w == "N":
            continue
        parts.append(_format_word_from_str(w, val, max_decimals))
    return "".join(parts)


def _is_safe_word_line(line: str) -> bool:
    return not WORD_PAT.sub("", line).strip()


def _split_linear_move(
    state: _SplitState,
    words: list[tuple[str, str]],
    has_x: bool,
    has_y: bool,
    has_z: bool,
    sx: float,
    sy: float,
    sz: float,
    nx: float,
    ny: float,
    nz: float,
    max_len: int,
) -> Optional[List[str]]:
    prefix_tokens: list[str] = []
    for w, val in words:
        if w in AXIS_WORDS or w == "N":
            continue
        prefix_tokens.append(_format_word_from_str(w, val))
    prefix_line = "".join(prefix_tokens)
    if _line_len_bytes(prefix_line) > max_len:
        return None
    units = state.units
    dx = nx - sx
    dy = ny - sy
    dz = nz - sz
    for decimals in SPLIT_DECIMALS:
        for segments in range(2, MAX_SPLIT_SEGMENTS + 1):
            lines: list[str] = []
            if state.absolute:
                for i in range(1, segments + 1):
                    t = i / segments
                    axis_tokens: list[str] = []
                    if has_x:
                        axis_tokens.append(
                            f"X{_format_float((sx + dx * t) / units, decimals)}"
                        )
                    if has_y:
                        axis_tokens.append(
                            f"Y{_format_float((sy + dy * t) / units, decimals)}"
                        )
                    if has_z:
                        axis_tokens.append(
                            f"Z{_format_float((sz + dz * t) / units, decimals)}"
                        )
                    parts = prefix_tokens if i == 1 else []
                    line = "".join(parts + axis_tokens)
                    if _line_len_bytes(line) > max_len:
                        lines = []
                        break
                    lines.append(line)
            else:
                rem_x = dx
                rem_y = dy
                rem_z = dz
                for i in range(segments):
                    axis_tokens = []
                    if has_x:
                        if i < segments - 1:
                            seg_x = dx / segments
                            rem_x -= seg_x
                        else:
                            seg_x = rem_x
                        axis_tokens.append(
                            f"X{_format_float(seg_x / units, decimals)}"
                        )
                    if has_y:
                        if i < segments - 1:
                            seg_y = dy / segments
                            rem_y -= seg_y
                        else:
                            seg_y = rem_y
                        axis_tokens.append(
                            f"Y{_format_float(seg_y / units, decimals)}"
                        )
                    if has_z:
                        if i < segments - 1:
                            seg_z = dz / segments
                            rem_z -= seg_z
                        else:
                            seg_z = rem_z
                        axis_tokens.append(
                            f"Z{_format_float(seg_z / units, decimals)}"
                        )
                    parts = prefix_tokens if i == 0 else []
                    line = "".join(parts + axis_tokens)
                    if _line_len_bytes(line) > max_len:
                        lines = []
                        break
                    lines.append(line)
            if lines:
                return lines
    return None


def split_gcode_lines(lines: Iterable[str], max_len: int = 80) -> GcodeSplitResult:
    state = _SplitState()
    out_lines: list[str] = []
    split_count = 0
    modified_count = 0

    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if not line:
            out_lines.append(line)
            continue
        line_len = _line_len_bytes(line)
        upper = line.strip().upper()
        if not upper:
            out_lines.append(line)
            continue
        if not _is_safe_word_line(upper):
            state.can_split = False
            if line_len > max_len:
                return GcodeSplitResult(
                    lines=out_lines,
                    split_count=split_count,
                    modified_count=modified_count,
                    failed_index=idx,
                    failed_len=line_len,
                )
            out_lines.append(line)
            continue
        words = [(m.group(1), m.group(2)) for m in WORD_PAT.finditer(upper)]
        if not words:
            state.can_split = False
            if line_len > max_len:
                return GcodeSplitResult(
                    lines=out_lines,
                    split_count=split_count,
                    modified_count=modified_count,
                    failed_index=idx,
                    failed_len=line_len,
                )
            out_lines.append(line)
            continue

        g_codes: Set[float] = set()
        for w, val in words:
            if w == "G":
                try:
                    g_codes.add(round(float(val), 3))
                except Exception:
                    pass

        def has_g(code: float) -> bool:
            return round(code, 3) in g_codes

        if has_g(20):
            state.units = 25.4
        if has_g(21):
            state.units = 1.0
        if has_g(90):
            state.absolute = True
        if has_g(91):
            state.absolute = False
        if has_g(93):
            state.feed_mode = "G93"
        if has_g(94):
            state.feed_mode = "G94"

        sx, sy, sz = state.x, state.y, state.z
        nx, ny, nz = sx, sy, sz
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        unsupported_axis = False
        for w, val in words:
            if w in UNSUPPORTED_AXIS_WORDS:
                unsupported_axis = True
            if w not in AXIS_WORDS:
                continue
            try:
                raw_val = float(val)
            except Exception:
                continue
            fval = raw_val * state.units
            if w == "X":
                has_axis = True
                has_x = True
                nx = fval if state.absolute else (nx + fval)
            elif w == "Y":
                has_axis = True
                has_y = True
                ny = fval if state.absolute else (ny + fval)
            elif w == "Z":
                has_axis = True
                has_z = True
                nz = fval if state.absolute else (nz + fval)

        if has_g(92):
            if not (has_x or has_y or has_z):
                if state.g92_enabled:
                    state.x += state.g92_offset[0]
                    state.y += state.g92_offset[1]
                    state.z += state.g92_offset[2]
                state.g92_offset = [0.0, 0.0, 0.0]
            else:
                if has_x:
                    mx = state.x + (state.g92_offset[0] if state.g92_enabled else 0.0)
                    state.g92_offset[0] = mx - nx
                    state.x = nx
                if has_y:
                    my = state.y + (state.g92_offset[1] if state.g92_enabled else 0.0)
                    state.g92_offset[1] = my - ny
                    state.y = ny
                if has_z:
                    mz = state.z + (state.g92_offset[2] if state.g92_enabled else 0.0)
                    state.g92_offset[2] = mz - nz
                    state.z = nz
            state.g92_enabled = True
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    return GcodeSplitResult(
                        lines=out_lines,
                        split_count=split_count,
                        modified_count=modified_count,
                        failed_index=idx,
                        failed_len=line_len,
                    )
                out_lines.append(compact)
                modified_count += 1
            else:
                out_lines.append(line)
            continue
        if has_g(92.1):
            if state.g92_enabled:
                state.x += state.g92_offset[0]
                state.y += state.g92_offset[1]
                state.z += state.g92_offset[2]
            state.g92_offset = [0.0, 0.0, 0.0]
            state.g92_enabled = False
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    return GcodeSplitResult(
                        lines=out_lines,
                        split_count=split_count,
                        modified_count=modified_count,
                        failed_index=idx,
                        failed_len=line_len,
                    )
                out_lines.append(compact)
                modified_count += 1
            else:
                out_lines.append(line)
            continue
        if has_g(92.2):
            if state.g92_enabled:
                state.x += state.g92_offset[0]
                state.y += state.g92_offset[1]
                state.z += state.g92_offset[2]
            state.g92_enabled = False
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    return GcodeSplitResult(
                        lines=out_lines,
                        split_count=split_count,
                        modified_count=modified_count,
                        failed_index=idx,
                        failed_len=line_len,
                    )
                out_lines.append(compact)
                modified_count += 1
            else:
                out_lines.append(line)
            continue
        if has_g(92.3):
            if not state.g92_enabled:
                state.x -= state.g92_offset[0]
                state.y -= state.g92_offset[1]
                state.z -= state.g92_offset[2]
            state.g92_enabled = True
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    return GcodeSplitResult(
                        lines=out_lines,
                        split_count=split_count,
                        modified_count=modified_count,
                        failed_index=idx,
                        failed_len=line_len,
                    )
                out_lines.append(compact)
                modified_count += 1
            else:
                out_lines.append(line)
            continue

        motion: Optional[int] = None
        for g in g_codes:
            if abs(g - 0) < 1e-3:
                motion = 0
            elif abs(g - 1) < 1e-3:
                motion = 1
            elif abs(g - 2) < 1e-3:
                motion = 2
            elif abs(g - 3) < 1e-3:
                motion = 3
        if motion is None and has_axis:
            motion = state.last_motion
        split_allowed = all(code in SPLIT_ALLOWED_G_CODES for code in g_codes)

        if line_len <= max_len:
            out_lines.append(line)
            if motion is not None and has_axis:
                state.x, state.y, state.z = nx, ny, nz
                state.last_motion = motion
            continue

        compact = _build_compact_line(words)
        if _line_len_bytes(compact) <= max_len:
            out_lines.append(compact)
            modified_count += 1
            if motion is not None and has_axis:
                state.x, state.y, state.z = nx, ny, nz
                state.last_motion = motion
            continue

        if (
            motion in (0, 1)
            and has_axis
            and state.feed_mode != "G93"
            and state.can_split
            and split_allowed
            and not unsupported_axis
        ):
            split_lines = _split_linear_move(
                state,
                words,
                has_x,
                has_y,
                has_z,
                sx,
                sy,
                sz,
                nx,
                ny,
                nz,
                max_len,
            )
            if split_lines:
                out_lines.extend(split_lines)
                split_count += 1
                modified_count += 1
                state.x, state.y, state.z = nx, ny, nz
                state.last_motion = motion
                continue

        return GcodeSplitResult(
            lines=out_lines,
            split_count=split_count,
            modified_count=modified_count,
            failed_index=idx,
            failed_len=line_len,
        )

    return GcodeSplitResult(
        lines=out_lines,
        split_count=split_count,
        modified_count=modified_count,
    )


def split_gcode_lines_stream(
    raw_lines: Iterable[str],
    *,
    max_len: int = 80,
    clean_line: Callable[[str], str] | None = None,
    preserve_raw: bool = False,
    write_line: Callable[[str], None] | None = None,
) -> GcodeSplitStreamResult:
    state = _SplitState()
    split_count = 0
    modified_count = 0
    lines_written = 0
    too_long = 0
    failed_index = None
    failed_len = None
    failed = False

    def emit(line: str) -> None:
        nonlocal lines_written
        if write_line is not None:
            write_line(line)
        lines_written += 1

    def comment_segments(raw_text: str) -> list[str]:
        if not preserve_raw:
            return []
        segments: list[str] = []
        buf: list[str] = []
        in_paren = False
        i = 0
        while i < len(raw_text):
            ch = raw_text[i]
            if in_paren:
                buf.append(ch)
                if ch == ")":
                    segments.append("".join(buf))
                    buf = []
                    in_paren = False
                i += 1
                continue
            if ch == ";":
                segments.append(raw_text[i:])
                return segments
            if ch == "(":
                in_paren = True
                buf = [ch]
                i += 1
                continue
            i += 1
        if buf:
            segments.append("".join(buf))
        return segments

    def emit_comment_segments(raw_text: str, line_index: int) -> bool:
        nonlocal too_long, failed_index, failed_len, failed
        for segment in comment_segments(raw_text):
            seg_len = _line_len_bytes(segment)
            if seg_len > max_len:
                too_long += 1
                if failed_index is None:
                    failed_index = line_index
                    failed_len = seg_len
                failed = True
                return False
            emit(segment)
        return True

    def emit_with_comments(raw_text: str, lines: list[str], line_index: int) -> bool:
        if not emit_comment_segments(raw_text, line_index):
            return False
        for line in lines:
            emit(line)
        return True

    for idx, raw_line in enumerate(raw_lines):
        raw_text = raw_line.rstrip("\r\n")
        line = raw_text
        if clean_line is not None:
            line = clean_line(raw_text)
        if not line:
            if not failed and preserve_raw:
                if raw_text:
                    raw_len = _line_len_bytes(raw_text)
                    if raw_len > max_len:
                        if emit_comment_segments(raw_text, idx):
                            modified_count += 1
                    else:
                        emit(raw_text)
                else:
                    emit(raw_text)
            continue
        line_len = _line_len_bytes(line)
        raw_len = _line_len_bytes(raw_text) if preserve_raw and raw_text else 0
        raw_too_long = bool(preserve_raw and raw_text and raw_len > max_len)
        if line_len > max_len:
            too_long += 1
        if failed:
            continue
        upper = line.strip().upper()
        if not upper:
            if preserve_raw and raw_text:
                emit(raw_text)
            continue
        if not _is_safe_word_line(upper):
            state.can_split = False
            if line_len > max_len or raw_too_long:
                if raw_too_long and line_len <= max_len:
                    too_long += 1
                if failed_index is None:
                    failed_index = idx
                    failed_len = raw_len if raw_too_long else line_len
                failed = True
                continue
            emit(raw_text if preserve_raw else line)
            continue
        words = [(m.group(1), m.group(2)) for m in WORD_PAT.finditer(upper)]
        if not words:
            state.can_split = False
            if line_len > max_len or raw_too_long:
                if raw_too_long and line_len <= max_len:
                    too_long += 1
                if failed_index is None:
                    failed_index = idx
                    failed_len = raw_len if raw_too_long else line_len
                failed = True
                continue
            emit(raw_text if preserve_raw else line)
            continue

        g_codes: Set[float] = set()
        for w, val in words:
            if w == "G":
                try:
                    g_codes.add(round(float(val), 3))
                except Exception:
                    pass

        def has_g(code: float) -> bool:
            return round(code, 3) in g_codes

        if has_g(20):
            state.units = 25.4
        if has_g(21):
            state.units = 1.0
        if has_g(90):
            state.absolute = True
        if has_g(91):
            state.absolute = False
        if has_g(93):
            state.feed_mode = "G93"
        if has_g(94):
            state.feed_mode = "G94"

        sx, sy, sz = state.x, state.y, state.z
        nx, ny, nz = sx, sy, sz
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        unsupported_axis = False
        for w, val in words:
            if w in UNSUPPORTED_AXIS_WORDS:
                unsupported_axis = True
            if w not in AXIS_WORDS:
                continue
            try:
                raw_val = float(val)
            except Exception:
                continue
            fval = raw_val * state.units
            if w == "X":
                has_axis = True
                has_x = True
                nx = fval if state.absolute else (nx + fval)
            elif w == "Y":
                has_axis = True
                has_y = True
                ny = fval if state.absolute else (ny + fval)
            elif w == "Z":
                has_axis = True
                has_z = True
                nz = fval if state.absolute else (nz + fval)

        if has_g(92):
            if not (has_x or has_y or has_z):
                if state.g92_enabled:
                    state.x += state.g92_offset[0]
                    state.y += state.g92_offset[1]
                    state.z += state.g92_offset[2]
                state.g92_offset = [0.0, 0.0, 0.0]
            else:
                if has_x:
                    mx = state.x + (state.g92_offset[0] if state.g92_enabled else 0.0)
                    state.g92_offset[0] = mx - nx
                    state.x = nx
                if has_y:
                    my = state.y + (state.g92_offset[1] if state.g92_enabled else 0.0)
                    state.g92_offset[1] = my - ny
                    state.y = ny
                if has_z:
                    mz = state.z + (state.g92_offset[2] if state.g92_enabled else 0.0)
                    state.g92_offset[2] = mz - nz
                    state.z = nz
            state.g92_enabled = True
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    if failed_index is None:
                        failed_index = idx
                        failed_len = line_len
                    failed = True
                    continue
                if emit_with_comments(raw_text, [compact], idx):
                    modified_count += 1
            else:
                emit(raw_text if preserve_raw else line)
            continue
        if has_g(92.1):
            if state.g92_enabled:
                state.x += state.g92_offset[0]
                state.y += state.g92_offset[1]
                state.z += state.g92_offset[2]
            state.g92_offset = [0.0, 0.0, 0.0]
            state.g92_enabled = False
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    if failed_index is None:
                        failed_index = idx
                        failed_len = line_len
                    failed = True
                    continue
                if emit_with_comments(raw_text, [compact], idx):
                    modified_count += 1
            else:
                emit(raw_text if preserve_raw else line)
            continue
        if has_g(92.2):
            if state.g92_enabled:
                state.x += state.g92_offset[0]
                state.y += state.g92_offset[1]
                state.z += state.g92_offset[2]
            state.g92_enabled = False
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    if failed_index is None:
                        failed_index = idx
                        failed_len = line_len
                    failed = True
                    continue
                if emit_with_comments(raw_text, [compact], idx):
                    modified_count += 1
            else:
                emit(raw_text if preserve_raw else line)
            continue
        if has_g(92.3):
            if not state.g92_enabled:
                state.x -= state.g92_offset[0]
                state.y -= state.g92_offset[1]
                state.z -= state.g92_offset[2]
            state.g92_enabled = True
            if line_len > max_len:
                compact = _build_compact_line(words)
                if _line_len_bytes(compact) > max_len:
                    if failed_index is None:
                        failed_index = idx
                        failed_len = line_len
                    failed = True
                    continue
                if emit_with_comments(raw_text, [compact], idx):
                    modified_count += 1
            else:
                emit(raw_text if preserve_raw else line)
            continue

        motion: Optional[int] = None
        for g in g_codes:
            if abs(g - 0) < 1e-3:
                motion = 0
            elif abs(g - 1) < 1e-3:
                motion = 1
            elif abs(g - 2) < 1e-3:
                motion = 2
            elif abs(g - 3) < 1e-3:
                motion = 3
        if motion is None and has_axis:
            motion = state.last_motion
        split_allowed = all(code in SPLIT_ALLOWED_G_CODES for code in g_codes)

        if line_len <= max_len:
            if preserve_raw:
                if raw_too_long:
                    if emit_with_comments(raw_text, [line], idx):
                        modified_count += 1
                else:
                    emit(raw_text)
            else:
                emit(line)
            if motion is not None and has_axis:
                state.x, state.y, state.z = nx, ny, nz
                state.last_motion = motion
            continue

        compact = _build_compact_line(words)
        if _line_len_bytes(compact) <= max_len:
            if emit_with_comments(raw_text, [compact], idx):
                modified_count += 1
                if motion is not None and has_axis:
                    state.x, state.y, state.z = nx, ny, nz
                    state.last_motion = motion
            continue

        if (
            motion in (0, 1)
            and has_axis
            and state.feed_mode != "G93"
            and state.can_split
            and split_allowed
            and not unsupported_axis
        ):
            split_lines = _split_linear_move(
                state,
                words,
                has_x,
                has_y,
                has_z,
                sx,
                sy,
                sz,
                nx,
                ny,
                nz,
                max_len,
            )
            if split_lines:
                if emit_with_comments(raw_text, split_lines, idx):
                    split_count += 1
                    modified_count += 1
                    state.x, state.y, state.z = nx, ny, nz
                    state.last_motion = motion
                continue

        if failed_index is None:
            failed_index = idx
            failed_len = line_len
        failed = True

    return GcodeSplitStreamResult(
        lines_written=lines_written,
        split_count=split_count,
        modified_count=modified_count,
        failed_index=failed_index,
        failed_len=failed_len,
        too_long=too_long,
    )
