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

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, MutableMapping, Optional, Set

logger = logging.getLogger(__name__)
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
    """Normalized move summary for stats/estimates."""

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
    """Parsed segments, bounds, and move summaries for geometry/stat analysis."""

    segments: List[tuple[float, float, float, float, float, float, str]]
    bounds: tuple[float, float, float, float, float, float] | None
    moves: List[GcodeMove]


def clean_gcode_line(line: str) -> str:
    """Strip comments/whitespace and return a safe, normalized line."""
    line = line.replace("\ufeff", "")
    # Preserve tool-change directive payloads verbatim so CAM-emitted tool names
    # (including parentheses/symbols) survive into the sender workflow.
    stripped = line.strip()
    if stripped.startswith("TC:"):
        return stripped
    out_chars: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    for ch in line:
        if ch == ";" and paren_depth <= 0 and bracket_depth <= 0:
            break
        if ch == "(":
            paren_depth += 1
            continue
        if ch == ")" and paren_depth > 0:
            paren_depth -= 1
            continue
        if ch == "[":
            bracket_depth += 1
            continue
        if ch == "]" and bracket_depth > 0:
            bracket_depth -= 1
            continue
        if paren_depth > 0 or bracket_depth > 0:
            continue
        # Unmatched closing comment markers are noise in GRBL jobs.
        if ch in (")", "]"):
            continue
        out_chars.append(ch)
    line = "".join(out_chars).strip()
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


def _parse_words_and_collect_g_codes(
    words: list[tuple[str, str]],
) -> tuple[list[tuple[str, float]], Set[float]]:
    parsed_words: list[tuple[str, float]] = []
    g_codes: Set[float] = set()
    for w, val in words:
        try:
            raw_val = float(val)
        except ValueError:
            continue
        parsed_words.append((w, raw_val))
        if w == "G":
            g_codes.add(round(raw_val, 3))
    return parsed_words, g_codes


def _keep_running_allowed(keep_running: Optional[Callable], section: str) -> bool:
    if keep_running is None:
        return True
    try:
        return bool(keep_running(section))
    except TypeError:
        return bool(keep_running())


def parse_gcode_lines(
    lines: Iterable[str],
    arc_step_rad: float = math.pi / 18,
    keep_running: Optional[Callable[[], bool]] = None,
    max_segments: int | None = None,
    include_moves: bool = True,
    move_callback: Optional[Callable[[GcodeMove], None]] = None,
    move_values_callback: Optional[
        Callable[
            [int, float | None, str, float, float, float, float, float | None], None
        ]
    ] = None,
    segment_count_callback: Optional[Callable[[int], None]] = None,
    include_segments: bool = True,
    stop_after_max_segments: bool = False,
    parser_state: MutableMapping[str, Any] | None = None,
) -> Optional[GcodeParseResult]:
    """Parse G-code into segments, bounds, and move summaries."""
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
    if parser_state is not None:
        try:
            x = float(parser_state.get("x", 0.0) or 0.0)
            y = float(parser_state.get("y", 0.0) or 0.0)
            z = float(parser_state.get("z", 0.0) or 0.0)
            units = float(parser_state.get("units", 1.0) or 1.0)
            absolute = bool(parser_state.get("absolute", True))
            plane = str(parser_state.get("plane", "G17") or "G17")
            feed_mode = str(parser_state.get("feed_mode", "G94") or "G94")
            arc_abs = bool(parser_state.get("arc_abs", False))
            feed_raw_val = parser_state.get("feed_raw", None)
            feed_mm_val = parser_state.get("feed_mm", None)
            feed_raw = float(feed_raw_val) if feed_raw_val is not None else None
            feed_mm = float(feed_mm_val) if feed_mm_val is not None else None
            raw_g92 = parser_state.get("g92_offset", [0.0, 0.0, 0.0]) or [0.0, 0.0, 0.0]
            if isinstance(raw_g92, (list, tuple)) and len(raw_g92) >= 3:
                g92_offset = [
                    float(raw_g92[0] or 0.0),
                    float(raw_g92[1] or 0.0),
                    float(raw_g92[2] or 0.0),
                ]
            g92_enabled = bool(parser_state.get("g92_enabled", True))
            last_motion = int(parser_state.get("last_motion", 1) or 1)
        except Exception:
            pass
    max_segments = max_segments if max_segments and max_segments > 0 else None
    stop_after_max_segments = bool(stop_after_max_segments and max_segments is not None)
    segments: List[tuple[float, float, float, float, float, float, str]] = []
    moves: List[GcodeMove] = []
    segment_stride = 1
    segment_total = 0
    if parser_state is not None:
        try:
            segment_total = max(0, int(parser_state.get("segment_total", 0) or 0))
        except Exception:
            segment_total = 0
    segment_limit_reached = False

    segment_count_emit_stride = 1024

    def _emit_segment_progress(force: bool = False) -> None:
        if segment_count_callback is None:
            return
        if not force and (segment_total % segment_count_emit_stride) != 0:
            return
        try:
            segment_count_callback(int(segment_total))
        except Exception:
            pass

    def append_segment(
        segment: tuple[float, float, float, float, float, float, str],
    ) -> None:
        nonlocal segment_stride, segment_total, segments, segment_limit_reached
        segment_total += 1
        _emit_segment_progress()
        if not include_segments:
            if (
                stop_after_max_segments
                and max_segments is not None
                and segment_total >= max_segments
            ):
                segment_limit_reached = True
            return
        if max_segments is None or segment_total % segment_stride == 0:
            segments.append(segment)
            if max_segments is not None and len(segments) > max_segments:
                # Downsample segments as the list grows to cap memory/CPU.
                segments = segments[::2]
                segment_stride *= 2
        if (
            stop_after_max_segments
            and max_segments is not None
            and segment_total >= max_segments
        ):
            segment_limit_reached = True

    minx: float | None = None
    miny: float | None = None
    minz: float | None = None
    maxx: float | None = None
    maxy: float | None = None
    maxz: float | None = None
    if parser_state is not None:
        def _state_float(key: str) -> float | None:
            raw = parser_state.get(key, None)
            if raw is None:
                return None
            return float(raw)

        try:
            minx = _state_float("minx")
            miny = _state_float("miny")
            minz = _state_float("minz")
            maxx = _state_float("maxx")
            maxy = _state_float("maxy")
            maxz = _state_float("maxz")
        except Exception:
            minx = miny = minz = maxx = maxy = maxz = None

    def update_bounds(nx: float, ny: float, nz: float) -> None:
        nonlocal minx, maxx, miny, maxy, minz, maxz
        if minx is None:
            minx = maxx = nx
            miny = maxy = ny
            minz = maxz = nz
            return
        assert minx is not None
        assert maxx is not None
        assert miny is not None
        assert maxy is not None
        assert minz is not None
        assert maxz is not None
        if nx < minx:
            minx = nx
        if nx > maxx:
            maxx = nx
        if ny < miny:
            miny = ny
        if ny > maxy:
            maxy = ny
        if nz < minz:
            minz = nz
        if nz > maxz:
            maxz = nz

    for raw in lines:
        if segment_limit_reached:
            break
        if not _keep_running_allowed(keep_running, "line_loop"):
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
        parsed_words, g_codes = _parse_words_and_collect_g_codes(words)

        if 20.0 in g_codes:
            units = 25.4
            if feed_raw is not None:
                feed_mm = feed_raw * units
        if 21.0 in g_codes:
            units = 1.0
            if feed_raw is not None:
                feed_mm = feed_raw * units
        if 90.0 in g_codes:
            absolute = True
        if 91.0 in g_codes:
            absolute = False
        if 17.0 in g_codes:
            plane = "G17"
        if 18.0 in g_codes:
            plane = "G18"
        if 19.0 in g_codes:
            plane = "G19"
        if 93.0 in g_codes:
            feed_mode = "G93"
        if 94.0 in g_codes:
            feed_mode = "G94"
        if 90.1 in g_codes:
            arc_abs = True
        if 91.1 in g_codes:
            arc_abs = False

        nx, ny, nz = x, y, z
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        i_val = j_val = k_val = r_val = None
        for word_idx, (w, raw_val) in enumerate(parsed_words, start=1):
            if (word_idx % 16) == 0 and not _keep_running_allowed(
                keep_running, "line_words"
            ):
                return None
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

        if 92.0 in g_codes:
            # G92 temporarily shifts the working origin until cleared.
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
        if 92.1 in g_codes:
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_offset = [0.0, 0.0, 0.0]
            g92_enabled = False
            continue
        if 92.2 in g_codes:
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_enabled = False
            continue
        if 92.3 in g_codes:
            if not g92_enabled:
                x -= g92_offset[0]
                y -= g92_offset[1]
                z -= g92_offset[2]
            g92_enabled = True
            continue

        motion: Optional[int] = None
        if 0.0 in g_codes:
            motion = 0
        elif 1.0 in g_codes:
            motion = 1
        elif 2.0 in g_codes:
            motion = 2
        elif 3.0 in g_codes:
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
            append_segment((x, y, z, nx, ny, nz, color))
            if include_moves or move_callback is not None:
                move = GcodeMove(
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
                if include_moves:
                    moves.append(move)
                if move_callback is not None:
                    move_callback(move)
            if move_values_callback is not None:
                move_values_callback(
                    motion,
                    feed_for_mode,
                    feed_mode,
                    dx,
                    dy,
                    dz,
                    dist,
                    None,
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
                # Project onto XY plane for arc math; Z is linear.
                u0, v0, u1, v1 = x, y, nx, ny
                w0, w1 = z, nz
                off1, off2 = i_val, j_val

                def to_xyz(u: float, v: float, w: float) -> tuple[float, float, float]:
                    return u, v, w
            elif plane == "G18":
                # Project onto XZ plane for arc math; Y is linear.
                u0, v0, u1, v1 = x, z, nx, nz
                w0, w1 = y, ny
                off1, off2 = i_val, k_val

                def to_xyz(u: float, v: float, w: float) -> tuple[float, float, float]:
                    return u, w, v
            else:
                # Project onto YZ plane for arc math; X is linear.
                u0, v0, u1, v1 = y, z, ny, nz
                w0, w1 = x, nx
                off1, off2 = j_val, k_val

                def to_xyz(u: float, v: float, w: float) -> tuple[float, float, float]:
                    return w, u, v

            arc_len2d = math.hypot(u1 - u0, v1 - v0)
            full_circle = abs(u1 - u0) < 1e-6 and abs(v1 - v0) < 1e-6
            sweep = 0.0
            if r_val is not None:
                # Radius mode chooses the valid center based on CW/CCW sweep.
                if full_circle:
                    if off1 is not None or off2 is not None:
                        if off1 is None:
                            off1 = u0 if arc_abs else 0.0
                        if off2 is None:
                            off2 = v0 if arc_abs else 0.0
                        cu = off1 if arc_abs else (u0 + off1)
                        cv = off2 if arc_abs else (v0 + off2)
                        r = math.hypot(u0 - cu, v0 - cv)
                        if r <= 0:
                            x, y, z = nx, ny, nz
                            continue
                        arc_len2d = 2 * math.pi * r
                        sweep = 2 * math.pi
                    else:
                        r = abs(r_val)
                        if r <= 0:
                            x, y, z = nx, ny, nz
                            continue
                        arc_len2d = 2 * math.pi * r
                        sweep = 2 * math.pi
                        cu = u0 + r
                        cv = v0
                        logger.warning(
                            "Full-circle R arc without I/J offsets; assuming center offset from start."
                        )
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
                sweep = (
                    2 * math.pi
                    if full_circle
                    else _arc_sweep(u0, v0, u1, v1, cu, cv, cw)
                )
                r = math.hypot(u0 - cu, v0 - cv)
            if sweep == 0 or r == 0:
                x, y, z = nx, ny, nz
                continue
            arc_len2d = abs(sweep) * r
            steps = max(8, int(abs(sweep) / arc_step_rad))
            start_ang = math.atan2(v0 - cv, u0 - cu)
            px, py, pz = x, y, z
            for i in range(1, steps + 1):
                if (i % 2) == 0 and not _keep_running_allowed(
                    keep_running, "arc_subdivide"
                ):
                    return None
                t = i / steps
                ang = start_ang - sweep * t if cw else start_ang + sweep * t
                u = cu + r * math.cos(ang)
                v = cv + r * math.sin(ang)
                w_coord = w0 + (w1 - w0) * t
                qx, qy, qz = to_xyz(u, v, w_coord)
                append_segment((px, py, pz, qx, qy, qz, "arc"))
                px, py, pz = qx, qy, qz
                if segment_limit_reached:
                    break
            if segment_limit_reached:
                break
            dist = math.hypot(arc_len2d, w1 - w0)
            dx = nx - x
            dy = ny - y
            dz = nz - z
            if include_moves or move_callback is not None:
                move = GcodeMove(
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
                if include_moves:
                    moves.append(move)
                if move_callback is not None:
                    move_callback(move)
            if move_values_callback is not None:
                move_values_callback(
                    motion,
                    feed_for_mode,
                    feed_mode,
                    dx,
                    dy,
                    dz,
                    dist,
                    arc_len2d,
                )
            update_bounds(nx, ny, nz)
            x, y, z = nx, ny, nz
            last_motion = motion
            continue

    if (
        minx is None
        or miny is None
        or minz is None
        or maxx is None
        or maxy is None
        or maxz is None
    ):
        bounds = None
    else:
        bounds = (minx, maxx, miny, maxy, minz, maxz)
    if parser_state is not None:
        parser_state["x"] = float(x)
        parser_state["y"] = float(y)
        parser_state["z"] = float(z)
        parser_state["units"] = float(units)
        parser_state["absolute"] = bool(absolute)
        parser_state["plane"] = str(plane)
        parser_state["feed_mode"] = str(feed_mode)
        parser_state["arc_abs"] = bool(arc_abs)
        parser_state["feed_raw"] = float(feed_raw) if feed_raw is not None else None
        parser_state["feed_mm"] = float(feed_mm) if feed_mm is not None else None
        parser_state["g92_offset"] = [
            float(g92_offset[0]),
            float(g92_offset[1]),
            float(g92_offset[2]),
        ]
        parser_state["g92_enabled"] = bool(g92_enabled)
        parser_state["last_motion"] = int(last_motion)
        parser_state["segment_total"] = int(segment_total)
        parser_state["minx"] = minx
        parser_state["miny"] = miny
        parser_state["minz"] = minz
        parser_state["maxx"] = maxx
        parser_state["maxy"] = maxy
        parser_state["maxz"] = maxz
    _emit_segment_progress(force=True)
    return GcodeParseResult(segments=segments, bounds=bounds, moves=moves)
