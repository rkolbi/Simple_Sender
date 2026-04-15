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
from typing import Any

_SSMETA_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*=")
_SSMETA_NEXT_KEY_RE = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_.-]*\s*=")
_SSMETA_FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _ssmeta_clean_value(raw: str) -> str:
    value = str(raw or "").strip()
    if len(value) >= 2:
        if value[0] == "(" and value[-1] == ")":
            value = value[1:-1].strip()
        elif value[0] == "[" and value[-1] == "]":
            value = value[1:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    value = value.strip().strip(",;")
    while value.endswith(")") and value.count("(") < value.count(")"):
        value = value[:-1].rstrip()
    while value.endswith("]") and value.count("[") < value.count("]"):
        value = value[:-1].rstrip()
    return value


def _parse_ssmeta_blob(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    out: dict[str, str] = {}
    first_key = _SSMETA_KEY_RE.search(text, 0)
    section_prefix = ""
    if first_key is not None and int(first_key.start()) > 0:
        prefix_raw = str(text[: int(first_key.start())] or "").strip().strip(":")
        if prefix_raw and "=" not in prefix_raw:
            prefix = prefix_raw.lower().replace(" ", "_")
            if re.fullmatch(r"[a-z_][a-z0-9_.-]*", prefix):
                section_prefix = prefix
    pos = 0
    length = len(text)
    while pos < length:
        while pos < length and text[pos] in " \t,;":
            pos += 1
        if pos >= length:
            break
        match = _SSMETA_KEY_RE.match(text, pos)
        if match is None:
            pos += 1
            continue
        key = str(match.group(1) or "").strip().lower()
        pos = int(match.end())
        while pos < length and text[pos].isspace():
            pos += 1
        if pos >= length:
            break
        quote = text[pos] if text[pos] in {"'", '"'} else None
        if quote:
            pos += 1
            end = text.find(quote, pos)
            if end < 0:
                value = text[pos:]
                pos = length
            else:
                value = text[pos:end]
                pos = end + 1
        else:
            next_key = _SSMETA_NEXT_KEY_RE.search(text, pos)
            end = int(next_key.start()) if next_key is not None else length
            value = text[pos:end]
            pos = end
        cleaned = _ssmeta_clean_value(value)
        if key and cleaned:
            out[key] = cleaned
            if section_prefix:
                out[f"{section_prefix}_{key}"] = cleaned
    return out


def _parse_ssmeta_line(raw_line: str) -> dict[str, str]:
    text = str(raw_line or "").strip()
    if not text:
        return {}
    marker_index = text.upper().find("SSMETA")
    if marker_index < 0:
        return {}
    payload = text[marker_index + len("SSMETA") :].strip()
    if payload.startswith(":"):
        payload = payload[1:].strip()
    return _parse_ssmeta_blob(payload)


def _read_ssmeta_header(
    path: str,
    *,
    max_lines: int,
    max_bytes: int,
) -> tuple[bool, dict[str, str]]:
    if max_lines <= 0 or max_bytes <= 0:
        return False, {}
    lines_read = 0
    bytes_read = 0
    found = False
    metadata: dict[str, str] = {}
    toolpaths_entries: list[str] = []
    tool_entries: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            while lines_read < max_lines and bytes_read < max_bytes:
                raw_line = handle.readline()
                if not raw_line:
                    break
                lines_read += 1
                bytes_read += len(raw_line.encode("utf-8", "ignore"))
                if "SSMETA" not in raw_line.upper():
                    continue
                found = True
                parsed = _parse_ssmeta_line(raw_line)
                if not parsed:
                    continue
                for key, value in parsed.items():
                    clean_key = str(key).strip().lower()
                    clean_value = _ssmeta_clean_value(value)
                    metadata[clean_key] = clean_value
                    if clean_key in {"toolpaths_output", "toolpaths"} and clean_value:
                        toolpaths_entries.append(clean_value)
                    if clean_key in {"tools_used", "tools"} and clean_value:
                        tool_entries.append(clean_value)
    except Exception:
        return False, {}
    if toolpaths_entries:
        metadata["__ssmeta_toolpaths_list"] = "\n".join(toolpaths_entries)
    if tool_entries:
        metadata["__ssmeta_tools_list"] = "\n".join(tool_entries)
    return bool(found), metadata


def _ssmeta_extract_float(ssmeta: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = ssmeta.get(str(key).strip().lower())
        if raw is None:
            continue
        direct = _safe_float(raw)
        if direct is not None:
            return direct
        match = _SSMETA_FLOAT_RE.search(str(raw))
        if match is None:
            continue
        parsed = _safe_float(match.group(0))
        if parsed is not None:
            return parsed
    return None


def _ssmeta_extract_triplet(ssmeta: dict[str, str], key: str) -> tuple[float, float, float] | None:
    raw = ssmeta.get(str(key).strip().lower())
    if raw is None:
        return None
    values = _SSMETA_FLOAT_RE.findall(str(raw))
    if len(values) < 3:
        return None
    x = _safe_float(values[0])
    y = _safe_float(values[1])
    z = _safe_float(values[2])
    if x is None or y is None or z is None:
        return None
    return (float(x), float(y), float(z))


def _ssmeta_units_value(ssmeta: dict[str, str]) -> str | None:
    raw = ssmeta.get("units") or ssmeta.get("unit") or ssmeta.get("job_units") or ""
    units = _ssmeta_clean_value(str(raw)).strip().lower()
    if units in {"mm", "millimeter", "millimeters"}:
        return "mm"
    if units in {"in", "inch", "inches"}:
        return "inch"
    return None


def _ssmeta_bounds_from_minmax(
    ssmeta: dict[str, str],
    *,
    suffix: str,
    scale_to_mm: float,
) -> dict[str, float] | None:
    xmin = _ssmeta_extract_float(
        ssmeta,
        f"xmin{suffix}",
        f"min_x{suffix}",
        f"x_min{suffix}",
        f"extents{suffix}_xmin",
        f"extents{suffix}_min_x",
    )
    xmax = _ssmeta_extract_float(
        ssmeta,
        f"xmax{suffix}",
        f"max_x{suffix}",
        f"x_max{suffix}",
        f"extents{suffix}_xmax",
        f"extents{suffix}_max_x",
    )
    ymin = _ssmeta_extract_float(
        ssmeta,
        f"ymin{suffix}",
        f"min_y{suffix}",
        f"y_min{suffix}",
        f"extents{suffix}_ymin",
        f"extents{suffix}_min_y",
    )
    ymax = _ssmeta_extract_float(
        ssmeta,
        f"ymax{suffix}",
        f"max_y{suffix}",
        f"y_max{suffix}",
        f"extents{suffix}_ymax",
        f"extents{suffix}_max_y",
    )
    if xmin is None or xmax is None or ymin is None or ymax is None:
        return None
    zmin = _ssmeta_extract_float(
        ssmeta,
        f"zmin{suffix}",
        f"min_z{suffix}",
        f"z_min{suffix}",
        f"extents{suffix}_zmin",
        f"extents{suffix}_min_z",
    )
    zmax = _ssmeta_extract_float(
        ssmeta,
        f"zmax{suffix}",
        f"max_z{suffix}",
        f"z_max{suffix}",
        f"extents{suffix}_zmax",
        f"extents{suffix}_max_z",
    )
    if zmin is None:
        zmin = 0.0
    if zmax is None:
        zmax = zmin
    min_x = float(xmin) * float(scale_to_mm)
    max_x = float(xmax) * float(scale_to_mm)
    min_y = float(ymin) * float(scale_to_mm)
    max_y = float(ymax) * float(scale_to_mm)
    min_z = float(zmin) * float(scale_to_mm)
    max_z = float(zmax) * float(scale_to_mm)
    if max_x < min_x:
        min_x, max_x = max_x, min_x
    if max_y < min_y:
        min_y, max_y = max_y, min_y
    if max_z < min_z:
        min_z, max_z = max_z, min_z
    return {
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "min_z": min_z,
        "max_z": max_z,
        "width": float(max(0.0, max_x - min_x)),
        "height": float(max(0.0, max_y - min_y)),
    }


def _ssmeta_bounds_box(ssmeta: dict[str, str]) -> dict[str, float] | None:
    units = _ssmeta_units_value(ssmeta)
    if units == "inch":
        bounds = _ssmeta_bounds_from_minmax(ssmeta, suffix="", scale_to_mm=25.4)
        if bounds is not None:
            return bounds
    elif units == "mm":
        bounds = _ssmeta_bounds_from_minmax(ssmeta, suffix="", scale_to_mm=1.0)
        if bounds is not None:
            return bounds

    for suffix, scale in (("_mm", 1.0), ("_in", 25.4)):
        bounds = _ssmeta_bounds_from_minmax(ssmeta, suffix=suffix, scale_to_mm=scale)
        if bounds is not None:
            return bounds

    for key, scale in (("extents_mm", 1.0), ("extents_in", 25.4)):
        triplet = _ssmeta_extract_triplet(ssmeta, key)
        if triplet is None:
            continue
        x, y, z = triplet
        x_mm = float(x) * scale
        y_mm = float(y) * scale
        z_mm = float(z) * scale
        return {
            "min_x": 0.0,
            "max_x": max(0.0, x_mm),
            "min_y": 0.0,
            "max_y": max(0.0, y_mm),
            "min_z": 0.0,
            "max_z": max(0.0, z_mm),
            "width": max(0.0, x_mm),
            "height": max(0.0, y_mm),
        }
    return None
