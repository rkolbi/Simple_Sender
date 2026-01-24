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

from dataclasses import dataclass
import math
import os
from typing import Iterable

from simple_sender.autolevel.height_map import HeightMap
from simple_sender.gcode_parser import (
    UNSUPPORTED_AXIS_WORDS,
    WORD_PAT,
    _arc_center_from_radius,
    _arc_sweep,
    _format_float,
    clean_gcode_line,
)


@dataclass(frozen=True)
class LevelResult:
    lines: list[str]
    error: str | None


@dataclass(frozen=True)
class LevelFileResult:
    output_path: str | None
    lines_written: int
    error: str | None
    io_error: bool = False


class _LevelerError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


LEVEL_DECIMALS = 4


def _level_gcode_iter(
    lines: Iterable[str],
    height_map: HeightMap,
    *,
    arc_step_rad: float,
    apply_to_rapids: bool,
    interpolation: str,
):
    if not height_map.is_complete():
        raise _LevelerError("Height map is incomplete.")
    if arc_step_rad <= 0:
        arc_step_rad = math.pi / 18

    max_step = _grid_step(height_map)
    units = 1.0
    absolute = True
    plane = "G17"
    feed_mode = "G94"
    arc_abs = False
    feed_raw = None
    last_feed_out = None
    x = y = z = 0.0
    g92_offset = [0.0, 0.0, 0.0]
    g92_enabled = True
    last_motion = 1
    for raw_line in lines:
        raw_line_text = raw_line.rstrip("\r\n")
        if not raw_line_text.strip():
            continue
        clean_line = clean_gcode_line(raw_line_text)
        if not clean_line:
            if raw_line_text.strip():
                yield raw_line_text
            continue
        s = clean_line.upper()
        words = WORD_PAT.findall(s)
        if not words:
            yield raw_line_text
            continue
        g_codes = _collect_g_codes(words)
        for w, _ in words:
            if w in UNSUPPORTED_AXIS_WORDS:
                raise _LevelerError(f"Unsupported axis word: {w}")

        def has_g(code: float) -> bool:
            return round(code, 3) in g_codes

        if has_g(20):
            units = 25.4
        if has_g(21):
            units = 1.0
        if has_g(90):
            absolute = True
        if has_g(91):
            raise _LevelerError("Incremental (G91) moves are not supported for auto-level.")
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

        if feed_mode == "G93":
            raise _LevelerError("Inverse time feed (G93) is not supported for auto-level.")

        nx, ny, nz = x, y, z
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        feed_specified = False
        i_val = j_val = k_val = r_val = None
        for w, val in words:
            try:
                raw_val = float(val)
            except Exception:
                continue
            if w == "X":
                has_axis = True
                has_x = True
                fval = raw_val * units
                nx = fval if absolute else (nx + fval)
            elif w == "Y":
                has_axis = True
                has_y = True
                fval = raw_val * units
                ny = fval if absolute else (ny + fval)
            elif w == "Z":
                has_axis = True
                has_z = True
                fval = raw_val * units
                nz = fval if absolute else (nz + fval)
            elif w == "F":
                feed_raw = raw_val
                feed_specified = True
            elif w == "I":
                i_val = raw_val * units
            elif w == "J":
                j_val = raw_val * units
            elif w == "K":
                k_val = raw_val * units
            elif w == "R":
                r_val = raw_val * units

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
            yield raw_line_text
            continue
        if has_g(92.1):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_offset = [0.0, 0.0, 0.0]
            g92_enabled = False
            yield raw_line_text
            continue
        if has_g(92.2):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_enabled = False
            yield raw_line_text
            continue
        if has_g(92.3):
            if not g92_enabled:
                x -= g92_offset[0]
                y -= g92_offset[1]
                z -= g92_offset[2]
            g92_enabled = True
            yield raw_line_text
            continue

        motion = None
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

        if motion in (0, 1) and has_axis:
            dx = nx - x
            dy = ny - y
            dz = nz - z
            if motion == 0 and not apply_to_rapids:
                yield raw_line_text
            elif motion == 1 and dx == 0 and dy == 0:
                if not has_z:
                    yield raw_line_text
                else:
                    offset = height_map.interpolate(x, y, interpolation)
                    if offset is None:
                        raise _LevelerError("Height map interpolation failed.")
                    z_out = nz + offset
                    parts = []
                    prefix = _prefix_words(words, motion)
                    feed_out = _feed_word(feed_raw, feed_specified, last_feed_out)
                    if prefix:
                        parts.extend(prefix)
                    parts.append("G1")
                    if has_x:
                        parts.append(_axis_word("X", nx, units))
                    if has_y:
                        parts.append(_axis_word("Y", ny, units))
                    parts.append(_axis_word("Z", z_out, units))
                    if feed_out:
                        parts.append(feed_out)
                        last_feed_out = feed_raw
                    yield "".join(parts)
            else:
                segments = _linear_segments(x, y, z, nx, ny, nz, max_step if motion == 1 else 0.0)
                prefix = _prefix_words(words, motion)
                feed_out = _feed_word(feed_raw, feed_specified, last_feed_out)
                for idx, (sx, sy, sz) in enumerate(segments, start=1):
                    z_out = sz
                    if motion != 0 or apply_to_rapids:
                        offset = height_map.interpolate(sx, sy, interpolation)
                        if offset is None:
                            raise _LevelerError("Height map interpolation failed.")
                        z_out = sz + offset
                    parts = []
                    if idx == 1 and prefix:
                        parts.extend(prefix)
                    parts.append("G0" if motion == 0 else "G1")
                    parts.append(_axis_word("X", sx, units))
                    parts.append(_axis_word("Y", sy, units))
                    parts.append(_axis_word("Z", z_out, units))
                    if idx == 1 and feed_out:
                        parts.append(feed_out)
                        last_feed_out = feed_raw
                    yield "".join(parts)
            x, y, z = nx, ny, nz
            last_motion = motion if motion is not None else last_motion
            continue

        if motion in (2, 3) and has_axis:
            if plane != "G17":
                raise _LevelerError(f"Unsupported arc plane: {plane}")
            cw = motion == 2
            u0, v0, u1, v1 = x, y, nx, ny
            w0, w1 = z, nz
            off1, off2 = i_val, j_val
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
                        raise _LevelerError("Arc radius is invalid.")
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
                raise _LevelerError("Arc sweep is zero.")
            arc_len2d = abs(sweep) * r
            if max_step > 0:
                steps = max(8, int(math.ceil(arc_len2d / max_step)))
            else:
                steps = max(8, int(abs(sweep) / arc_step_rad))
            start_ang = math.atan2(v0 - cv, u0 - cu)
            prefix = _prefix_words(words, motion)
            feed_out = _feed_word(feed_raw, feed_specified, last_feed_out)
            for i in range(1, steps + 1):
                t = i / steps
                ang = start_ang - sweep * t if cw else start_ang + sweep * t
                sx = cu + r * math.cos(ang)
                sy = cv + r * math.sin(ang)
                sz = w0 + (w1 - w0) * t
                offset = height_map.interpolate(sx, sy, interpolation)
                if offset is None:
                    raise _LevelerError("Height map interpolation failed.")
                z_out = sz + offset
                parts = []
                if i == 1 and prefix:
                    parts.extend(prefix)
                parts.append("G1")
                parts.append(_axis_word("X", sx, units))
                parts.append(_axis_word("Y", sy, units))
                parts.append(_axis_word("Z", z_out, units))
                if i == 1 and feed_out:
                    parts.append(feed_out)
                    last_feed_out = feed_raw
                yield "".join(parts)
            x, y, z = nx, ny, nz
            last_motion = motion
            continue

        yield raw_line_text


def level_gcode_lines(
    lines: Iterable[str],
    height_map: HeightMap,
    *,
    arc_step_rad: float = math.pi / 18,
    apply_to_rapids: bool = False,
    interpolation: str = "bilinear",
) -> LevelResult:
    try:
        out_lines = list(
            _level_gcode_iter(
                lines,
                height_map,
                arc_step_rad=arc_step_rad,
                apply_to_rapids=apply_to_rapids,
                interpolation=interpolation,
            )
        )
    except _LevelerError as exc:
        return LevelResult([], exc.message)
    return LevelResult(out_lines, None)


def level_gcode_file(
    input_path: str,
    output_path: str,
    height_map: HeightMap,
    *,
    arc_step_rad: float = math.pi / 18,
    apply_to_rapids: bool = False,
    interpolation: str = "bilinear",
    input_encoding: str = "utf-8",
    header_lines: list[str] | None = None,
) -> LevelFileResult:
    try:
        lines_written = 0
        with open(input_path, "r", encoding=input_encoding, errors="replace", newline="") as infile:
            with open(output_path, "w", encoding="utf-8", newline="") as outfile:
                if header_lines:
                    for header in header_lines:
                        header_text = header.rstrip("\r\n")
                        outfile.write(header_text)
                        outfile.write("\n")
                        lines_written += 1
                for line in _level_gcode_iter(
                    infile,
                    height_map,
                    arc_step_rad=arc_step_rad,
                    apply_to_rapids=apply_to_rapids,
                    interpolation=interpolation,
                ):
                    outfile.write(line.rstrip("\n"))
                    outfile.write("\n")
                    lines_written += 1
    except _LevelerError as exc:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, exc.message, False)
    except Exception as exc:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))
    return LevelFileResult(output_path, lines_written, None, False)


def write_gcode_lines(
    output_path: str,
    lines: Iterable[str],
    *,
    header_lines: list[str] | None = None,
) -> LevelFileResult:
    try:
        lines_written = 0
        with open(output_path, "w", encoding="utf-8", newline="") as outfile:
            if header_lines:
                for header in header_lines:
                    header_text = header.rstrip("\r\n")
                    outfile.write(header_text)
                    outfile.write("\n")
                    lines_written += 1
            for line in lines:
                outfile.write(line.rstrip("\n"))
                outfile.write("\n")
                lines_written += 1
    except Exception as exc:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))
    return LevelFileResult(output_path, lines_written, None, False)


def _collect_g_codes(words: list[tuple[str, str]]) -> set[float]:
    g_codes: set[float] = set()
    for w, val in words:
        if w != "G":
            continue
        try:
            g_codes.add(round(float(val), 3))
        except Exception:
            continue
    return g_codes


def _grid_step(height_map: HeightMap) -> float:
    step_x = _min_step(height_map.xs)
    step_y = _min_step(height_map.ys)
    if step_x and step_y:
        return min(step_x, step_y)
    return step_x or step_y or 0.0


def _min_step(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    steps = [abs(values[i + 1] - values[i]) for i in range(len(values) - 1)]
    steps = [s for s in steps if s > 1e-9]
    return min(steps) if steps else 0.0


def _linear_segments(
    x0: float,
    y0: float,
    z0: float,
    x1: float,
    y1: float,
    z1: float,
    max_step: float,
) -> list[tuple[float, float, float]]:
    dx = x1 - x0
    dy = y1 - y0
    dz = z1 - z0
    dist = math.hypot(dx, dy)
    steps = 1
    if max_step > 0 and dist > max_step:
        steps = int(math.ceil(dist / max_step))
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        points.append((x0 + dx * t, y0 + dy * t, z0 + dz * t))
    return points


def _axis_word(letter: str, value_mm: float, units: float) -> str:
    value_units = value_mm / units if units else value_mm
    return f"{letter}{_format_float(value_units, LEVEL_DECIMALS)}"


def _prefix_words(words: list[tuple[str, str]], motion: int | None) -> list[str]:
    parts: list[str] = []
    for w, val in words:
        if w in ("X", "Y", "Z", "I", "J", "K", "R", "F", "N"):
            continue
        if w == "G":
            try:
                g = float(val)
            except Exception:
                continue
            if motion is not None and abs(g - motion) < 1e-3:
                continue
            if g in (0.0, 1.0, 2.0, 3.0):
                continue
        parts.append(f"{w}{val}")
    return parts


def _feed_word(feed_raw: float | None, feed_specified: bool, last_feed_out: float | None) -> str | None:
    if feed_raw is None:
        return None
    if feed_specified or last_feed_out is None or abs(feed_raw - last_feed_out) > 1e-9:
        return f"F{_format_float(feed_raw, LEVEL_DECIMALS)}"
    return None
