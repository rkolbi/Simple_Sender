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
# SPDX-License-Identifier: GPL-3.0-or-later

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Set, Tuple

PAREN_COMMENT_PAT = re.compile(r"\(.*?\)")
WORD_PAT = re.compile(r"([A-Z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
AXIS_WORDS = ("X", "Y", "Z")
UNSUPPORTED_AXIS_WORDS = ("A", "B", "C", "U", "V", "W")
SPLIT_DECIMALS = (6, 5, 4, 3)
MAX_SPLIT_SEGMENTS = 1000
SPLIT_ALLOWED_G_CODES = {
    0.0,
    1.0,
    17.0,
    18.0,
    19.0,
    20.0,
    21.0,
    90.0,
    91.0,
    90.1,
    91.1,
    93.0,
    94.0,
}


@dataclass
class GcodeMove:
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    motion: int
    feed: float | None
    feed_mode: str
    dx: float
    dy: float
    dz: float
    dist: float
    arc_len: float | None


@dataclass
class GcodeParseResult:
    segments: List[tuple[float, float, float, float, float, float, str]]
    bounds: tuple[float, float, float, float, float, float] | None
    moves: List[GcodeMove]


@dataclass
class GcodeSplitResult:
    lines: List[str]
    split_count: int
    modified_count: int
    failed_index: int | None = None
    failed_len: int | None = None


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


def clean_gcode_line(line: str) -> str:
    """Strip comments and whitespace; keep simple + safe."""
    line = line.replace("\ufeff", "")
    line = PAREN_COMMENT_PAT.sub("", line)
    if ";" in line:
        line = line.split(";", 1)[0]
    line = line.strip()
    if line.startswith("%"):
        return ""
    if not line:
        return ""
    return line


def _arc_sweep(
    u0: float, v0: float, u1: float, v1: float, cu: float, cv: float, cw: bool
) -> float:
    start_ang = math.atan2(v0 - cv, u0 - cu)
    end_ang = math.atan2(v1 - cv, u1 - cu)
    if cw:
        sweep = (start_ang - end_ang) % (2 * math.pi)
    else:
        sweep = (end_ang - start_ang) % (2 * math.pi)
    return sweep


def _arc_center_from_radius(
    u0: float, v0: float, u1: float, v1: float, r: float, cw: bool
) -> tuple[float, float, float] | None:
    if r == 0:
        return None
    r_abs = abs(r)
    dx = u1 - u0
    dy = v1 - v0
    d = math.hypot(dx, dy)
    if d == 0 or d > 2 * r_abs:
        return None
    um = (u0 + u1) / 2.0
    vm = (v0 + v1) / 2.0
    h = math.sqrt(max(r_abs * r_abs - (d / 2) * (d / 2), 0.0))
    ux = -dy / d
    uy = dx / d
    c1 = (um + ux * h, vm + uy * h)
    c2 = (um - ux * h, vm - uy * h)
    sweep1 = _arc_sweep(u0, v0, u1, v1, c1[0], c1[1], cw)
    sweep2 = _arc_sweep(u0, v0, u1, v1, c2[0], c2[1], cw)
    if r > 0:
        if sweep1 <= sweep2:
            return c1[0], c1[1], sweep1
        return c2[0], c2[1], sweep2
    if sweep1 >= sweep2:
        return c1[0], c1[1], sweep1
    return c2[0], c2[1], sweep2


def parse_gcode_lines(
    lines: Iterable[str],
    arc_step_rad: float = math.pi / 18,
    keep_running: Optional[Callable[[], bool]] = None,
) -> Optional[GcodeParseResult]:
    arc_step_rad = max(1e-6, arc_step_rad)
    x = y = z = 0.0
    units = 1.0
    absolute = True
    plane = "G17"
    feed_mode = "G94"
    arc_abs = False
    feed_raw: float | None = None
    feed_mm: float | None = None
    g92_offset = [0.0, 0.0, 0.0]
    g92_enabled = True
    last_motion = 1
    segments: List[tuple[float, float, float, float, float, float, str]] = []
    moves: List[GcodeMove] = []
    minx = miny = minz = None
    maxx = maxy = maxz = None

    def update_bounds(nx: float, ny: float, nz: float) -> None:
        nonlocal minx, maxx, miny, maxy, minz, maxz
        if minx is None:
            minx = maxx = nx
            miny = maxy = ny
            minz = maxz = nz
            return
        minx = min(minx, nx)
        maxx = max(maxx, nx)
        miny = min(miny, ny)
        maxy = max(maxy, ny)
        minz = min(minz, nz)
        maxz = max(maxz, nz)

    for raw in lines:
        if keep_running and not keep_running():
            return None
        s = raw.strip().upper()
        if not s:
            continue
        if "(" in s:
            s = PAREN_COMMENT_PAT.sub("", s)
        if ";" in s:
            s = s.split(";", 1)[0]
        s = s.strip()
        if not s or s.startswith("%"):
            continue
        words = WORD_PAT.findall(s)
        if not words:
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
            units = 25.4
            if feed_raw is not None:
                feed_mm = feed_raw * units
        if has_g(21):
            units = 1.0
            if feed_raw is not None:
                feed_mm = feed_raw * units
        if has_g(90):
            absolute = True
        if has_g(91):
            absolute = False
        if has_g(17):
            plane = "G17"
        if has_g(18):
            plane = "G18"
        if has_g(19):
            plane = "G19"
        if has_g(93):
            feed_mode = "G93"
        if has_g(94):
            feed_mode = "G94"
        if has_g(90.1):
            arc_abs = True
        if has_g(91.1):
            arc_abs = False

        nx, ny, nz = x, y, z
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        i_val = j_val = k_val = r_val = None
        for w, val in words:
            try:
                raw_val = float(val)
            except Exception:
                continue
            if w == "P":
                continue
            fval = raw_val * units
            if w == "X":
                has_axis = True
                has_x = True
                nx = fval if absolute else (nx + fval)
            elif w == "Y":
                has_axis = True
                has_y = True
                ny = fval if absolute else (ny + fval)
            elif w == "Z":
                has_axis = True
                has_z = True
                nz = fval if absolute else (nz + fval)
            elif w == "F":
                feed_raw = raw_val
                feed_mm = raw_val * units
            elif w == "I":
                i_val = fval
            elif w == "J":
                j_val = fval
            elif w == "K":
                k_val = fval
            elif w == "R":
                r_val = fval

        if has_g(92):
            if not (has_x or has_y or has_z):
                if g92_enabled:
                    x += g92_offset[0]
                    y += g92_offset[1]
                    z += g92_offset[2]
                g92_offset = [0.0, 0.0, 0.0]
            else:
                if has_x:
                    mx = x + (g92_offset[0] if g92_enabled else 0.0)
                    g92_offset[0] = mx - nx
                    x = nx
                if has_y:
                    my = y + (g92_offset[1] if g92_enabled else 0.0)
                    g92_offset[1] = my - ny
                    y = ny
                if has_z:
                    mz = z + (g92_offset[2] if g92_enabled else 0.0)
                    g92_offset[2] = mz - nz
                    z = nz
            g92_enabled = True
            continue
        if has_g(92.1):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_offset = [0.0, 0.0, 0.0]
            g92_enabled = False
            continue
        if has_g(92.2):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_enabled = False
            continue
        if has_g(92.3):
            if not g92_enabled:
                x -= g92_offset[0]
                y -= g92_offset[1]
                z -= g92_offset[2]
            g92_enabled = True
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
            motion = last_motion

        feed_for_mode = feed_raw if feed_mode == "G93" else feed_mm
        if motion in (0, 1) and has_axis:
            dx = nx - x
            dy = ny - y
            dz = nz - z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            color = "rapid" if motion == 0 else "feed"
            segments.append((x, y, z, nx, ny, nz, color))
            moves.append(
                GcodeMove(
                    start=(x, y, z),
                    end=(nx, ny, nz),
                    motion=motion,
                    feed=feed_for_mode,
                    feed_mode=feed_mode,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    dist=dist,
                    arc_len=None,
                )
            )
            update_bounds(x, y, z)
            update_bounds(nx, ny, nz)
            x, y, z = nx, ny, nz
            if motion is not None:
                last_motion = motion
            continue

        if motion in (2, 3) and has_axis:
            update_bounds(x, y, z)
            cw = motion == 2
            if plane == "G17":
                u0, v0, u1, v1 = x, y, nx, ny
                w0, w1 = z, nz
                off1, off2 = i_val, j_val
                to_xyz = lambda u, v, w: (u, v, w)
            elif plane == "G18":
                u0, v0, u1, v1 = x, z, nx, nz
                w0, w1 = y, ny
                off1, off2 = i_val, k_val
                to_xyz = lambda u, v, w: (u, w, v)
            else:
                u0, v0, u1, v1 = y, z, ny, nz
                w0, w1 = x, nx
                off1, off2 = j_val, k_val
                to_xyz = lambda u, v, w: (w, u, v)

            arc_len2d = math.hypot(u1 - u0, v1 - v0)
            full_circle = abs(u1 - u0) < 1e-6 and abs(v1 - v0) < 1e-6
            sweep = 0.0
            if r_val is not None:
                if full_circle:
                    r = abs(r_val)
                    arc_len2d = 2 * math.pi * r if r > 0 else 0.0
                    sweep = 2 * math.pi if r > 0 else 0.0
                    cu = u0 + r
                    cv = v0
                else:
                    res = _arc_center_from_radius(u0, v0, u1, v1, r_val, cw)
                    if res:
                        cu, cv, sweep = res
                        r = math.hypot(u0 - cu, v0 - cv)
                    else:
                        x, y, z = nx, ny, nz
                        continue
            else:
                if off1 is None:
                    off1 = u0 if arc_abs else 0.0
                if off2 is None:
                    off2 = v0 if arc_abs else 0.0
                cu = off1 if arc_abs else (u0 + off1)
                cv = off2 if arc_abs else (v0 + off2)
                sweep = 2 * math.pi if full_circle else _arc_sweep(u0, v0, u1, v1, cu, cv, cw)
                r = math.hypot(u0 - cu, v0 - cv)
            if sweep == 0 or r == 0:
                x, y, z = nx, ny, nz
                continue
            arc_len2d = abs(sweep) * r
            steps = max(8, int(abs(sweep) / arc_step_rad))
            start_ang = math.atan2(v0 - cv, u0 - cu)
            px, py, pz = x, y, z
            for i in range(1, steps + 1):
                t = i / steps
                ang = start_ang - sweep * t if cw else start_ang + sweep * t
                u = cu + r * math.cos(ang)
                v = cv + r * math.sin(ang)
                w = w0 + (w1 - w0) * t
                qx, qy, qz = to_xyz(u, v, w)
                segments.append((px, py, pz, qx, qy, qz, "arc"))
                px, py, pz = qx, qy, qz
            dist = math.hypot(arc_len2d, w1 - w0)
            dx = nx - x
            dy = ny - y
            dz = nz - z
            moves.append(
                GcodeMove(
                    start=(x, y, z),
                    end=(nx, ny, nz),
                    motion=motion,
                    feed=feed_for_mode,
                    feed_mode=feed_mode,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    dist=dist,
                    arc_len=arc_len2d,
                )
            )
            update_bounds(nx, ny, nz)
            x, y, z = nx, ny, nz
            last_motion = motion
            continue

    if minx is None:
        bounds = None
    else:
        bounds = (minx, maxx, miny, maxy, minz, maxz)
    return GcodeParseResult(segments=segments, bounds=bounds, moves=moves)


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
