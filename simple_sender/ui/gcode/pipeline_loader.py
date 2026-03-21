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
from collections import deque
import math
import re
from typing import Any, cast

from simple_sender.constants.messages import BusyMessages, DialogTitles
from simple_sender.utils.task_timing import record_task_timing


def _format_mb(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{value / (1024 * 1024):.1f} MB"


def _emit_progress(app, token_id: int, done: int, total: int, label: str) -> None:
    app.ui_q.put(("gcode_load_progress", token_id, done, total, label))


class _GcodeLoadCancelled(Exception):
    """Raised when a newer load token supersedes the active worker."""


@dataclass(slots=True)
class _FastPrepareData:
    sample_lines: list[str]
    sampled_head_lines: int
    sampled_tail_lines: int
    sampled_interval_lines: int
    sampled_max_lines: int
    sampled_line_count: int
    file_size_bytes: int
    file_line_count: int
    file_line_count_known: bool
    cleaned_lines_estimate: int
    cleaned_lines_known: bool
    executable_lines_estimate: int
    motion_lines_estimate: int
    sampled_executable_lines: int
    sampled_motion_lines: int
    raw_lines_scanned: int
    bytes_scanned: int
    lines_hash_quick: str
    quick_scan_ms: float
    bounds_box: dict[str, float] | None
    bounds_confidence: str
    dimensions_confidence_reasons: dict[str, bool]
    estimated_job_time_sec: int | None
    estimate_confidence: str
    estimate_confidence_reasons: dict[str, bool]
    estimate_inputs_snapshot: dict[str, Any]
    autolevel_prereq_snapshot: dict[str, Any]
    ssmeta_present: bool
    ssmeta: dict[str, str]
    dimensions_source: str
    units_source: str
    ssmeta_scan_reduced: bool


def _check_load_token(app, token: int) -> None:
    if token != app._gcode_load_token:
        raise _GcodeLoadCancelled()


def _pi_profile_enabled(app) -> bool:
    var = getattr(app, "pi_profile_enabled", None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("pi_profile_enabled", False))
        except Exception:
            return False
    return False


def _resolve_ultra_large_threshold_bytes(app, deps) -> int:
    default_bytes = 0
    try:
        default_bytes = int(getattr(deps, "GCODE_ULTRA_LARGE_SIZE_THRESHOLD", 0) or 0)
    except Exception:
        default_bytes = 0
    if app is None:
        return max(0, default_bytes)
    var = getattr(app, "ultra_large_size_threshold_mb", None)
    if var is None:
        return max(0, default_bytes)
    try:
        threshold_mb = int(var.get())
    except Exception:
        try:
            threshold_mb = int(var)
        except Exception:
            return max(0, default_bytes)
    if threshold_mb <= 0:
        return 0
    return int(threshold_mb) * 1024 * 1024


def _is_ultra_large_file_with_threshold(
    *, file_size: int | None, threshold_bytes: int
) -> bool:
    if file_size is None:
        return False
    return int(threshold_bytes) > 0 and int(file_size) >= int(threshold_bytes)


def _quick_file_fingerprint(deps, path: str, *, file_size: int | None) -> str:
    try:
        stat = deps.os.stat(path)
        mtime_ns = int(getattr(stat, "st_mtime_ns", 0) or 0)
    except Exception:
        mtime_ns = 0
    size_part = int(file_size) if file_size is not None else -1
    return f"quick:{size_part}:{mtime_ns}"


_MOTION_GCODE_PAT = re.compile(r"(?<![0-9.])G(?:0|1|2|3)(?![0-9.])")
_MOTION_AXIS_PAT = re.compile(r"[XYZ][-+]?(?:\d+(?:\.\d*)?|\.\d+)?", re.IGNORECASE)
_G20_PAT = re.compile(r"(?<![0-9.])G20(?![0-9.])", re.IGNORECASE)
_G21_PAT = re.compile(r"(?<![0-9.])G21(?![0-9.])", re.IGNORECASE)
_G90_PAT = re.compile(r"(?<![0-9.])G90(?![0-9.])", re.IGNORECASE)
_G91_PAT = re.compile(r"(?<![0-9.])G91(?![0-9.])", re.IGNORECASE)
_G0_PAT = re.compile(r"(?<![0-9.])G0(?![0-9.])", re.IGNORECASE)
_G1_PAT = re.compile(r"(?<![0-9.])G1(?![0-9.])", re.IGNORECASE)
_G2_PAT = re.compile(r"(?<![0-9.])G2(?![0-9.])", re.IGNORECASE)
_G3_PAT = re.compile(r"(?<![0-9.])G3(?![0-9.])", re.IGNORECASE)
_WORD_PAT = re.compile(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_AXIS_WORDS = ("X", "Y", "Z")
_SSMETA_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*=")
_SSMETA_NEXT_KEY_RE = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_.-]*\s*=")
_SSMETA_FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def _is_motion_line(line: str) -> bool:
    text = str(line or "").strip().upper()
    if not text:
        return False
    if _MOTION_GCODE_PAT.search(text):
        return True
    return bool(_MOTION_AXIS_PAT.search(text))


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
    # Remove unmatched trailing comment wrappers while preserving balanced
    # parentheses/brackets used in tool descriptions.
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
                scoped_key = f"{section_prefix}_{key}"
                out[scoped_key] = cleaned
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
    raw = (
        ssmeta.get("units")
        or ssmeta.get("unit")
        or ssmeta.get("job_units")
        or ""
    )
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


def _extract_motion_setting(settings_data: Any, key: str) -> float | None:
    if not isinstance(settings_data, dict):
        return None
    raw_entry = settings_data.get(key)
    if isinstance(raw_entry, tuple):
        raw = raw_entry[0] if raw_entry else None
    else:
        raw = raw_entry
    return _safe_float(raw)


def _capture_motion_settings_snapshot(app: Any) -> dict[str, float | None]:
    motion_settings: dict[str, float | None] = {}
    settings_controller = getattr(app, "settings_controller", None)
    settings_data = (
        getattr(settings_controller, "_settings_data", None)
        if settings_controller is not None
        else None
    )
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        motion_settings[key] = _extract_motion_setting(settings_data, key)
    return motion_settings


def _has_complete_motion_settings(motion_settings: dict[str, float | None]) -> bool:
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        if _safe_float(motion_settings.get(key)) is None:
            return False
    return True


def _resolve_quick_rate_tuple(
    app: Any, motion_settings: dict[str, float | None]
) -> tuple[tuple[float, float, float], str]:
    if _has_complete_motion_settings(motion_settings):
        return (
            (
                float(motion_settings.get("$110") or 0.0),
                float(motion_settings.get("$111") or 0.0),
                float(motion_settings.get("$112") or 0.0),
            ),
            "grbl",
        )
    try:
        rate_x_var = getattr(app, "estimate_rate_x_var", None)
        rate_y_var = getattr(app, "estimate_rate_y_var", None)
        rate_z_var = getattr(app, "estimate_rate_z_var", None)
        if rate_x_var is None or rate_y_var is None or rate_z_var is None:
            raise ValueError("missing estimate rate variables")
        rx = float(rate_x_var.get())
        ry = float(rate_y_var.get())
        rz = float(rate_z_var.get())
        if rx > 0.0 and ry > 0.0 and rz > 0.0:
            units = str(
                getattr(getattr(app, "unit_mode", None), "get", lambda: "mm")() or "mm"
            ).lower()
            scale = 25.4 if units.startswith("in") else 1.0
            return ((rx * scale, ry * scale, rz * scale), "estimate")
    except Exception:
        pass
    try:
        fallback_var = getattr(app, "fallback_rapid_rate", None)
        if fallback_var is None:
            raise ValueError("missing fallback rapid rate")
        fallback = float(fallback_var.get())
        if fallback > 0.0:
            return ((fallback, fallback, fallback), "fallback")
    except Exception:
        pass
    # Keep a deterministic default for rough quick-scan estimates.
    return ((2000.0, 2000.0, 500.0), "default")


def _quick_scan_bounds_and_estimate(
    app: Any,
    deps: Any,
    *,
    sampled_lines: list[str],
    sampled_executable_lines: int,
    sampled_motion_lines: int,
    executable_total_estimate: int,
    motion_total_estimate: int,
    cleaned_lines_known: bool,
    source_path: str,
    source_hash: str,
    source_total_lines: int,
) -> tuple[
    dict[str, float] | None,
    str,
    dict[str, bool],
    int | None,
    str,
    dict[str, bool],
    dict[str, Any],
    dict[str, Any],
]:
    if not sampled_lines:
        now_ts = float(deps.time.time())
        empty_snapshot = {
            "captured_at_ts": now_ts,
            "capture_stage": "quick_scan",
            "gcode_hash": str(source_hash or ""),
            "stats_mode": "quick_scan_empty",
            "stats_sample_scale": 1.0,
            "stats_sample_line_count": 0,
            "stats_sample_total_lines": int(max(0, source_total_lines)),
            "stats_sample_executable_lines": 0,
            "stats_sample_motion_lines": 0,
            "stats_executable_total_lines": int(max(0, executable_total_estimate)),
            "stats_motion_total_lines": int(max(0, motion_total_estimate)),
            "rate_source": "n/a",
            "estimate_confidence": "provisional",
            "grbl_motion_settings": _capture_motion_settings_snapshot(app),
            "modal_context": {
                "units": None,
                "distance_mode": None,
                "feed_mode": None,
                "sampled_lines": 0,
                "sample_limit": 0,
            },
        }
        autolevel_snapshot = {
            "stage": "quick_scan",
            "prepared_at_ts": now_ts,
            "source_path": str(source_path or ""),
            "source_exists": bool(source_path and deps.os.path.isfile(source_path)),
            "source_hash": str(source_hash or ""),
            "source_total_lines": int(max(0, source_total_lines)),
            "bounds_ready": False,
            "bounds": None,
            "bounds_confidence": "rough",
            "xy_width_mm": 0.0,
            "xy_height_mm": 0.0,
            "z_min_mm": 0.0,
            "z_max_mm": 0.0,
            "probe_grid_applicable": False,
        }
        return (
            None,
            "rough",
            {
                "sampled_scan": True,
                "scan_incomplete": True,
                "no_motion_lines_found": True,
            },
            None,
            "provisional",
            {
                "missing_grbl_settings": True,
                "sampled_scan": True,
                "scan_incomplete": True,
                "no_motion_lines_found": True,
            },
            empty_snapshot,
            autolevel_snapshot,
        )

    motion_settings = _capture_motion_settings_snapshot(app)
    rapid_rates, rate_source = _resolve_quick_rate_tuple(app, motion_settings)
    default_feed = max(50.0, min(rapid_rates))

    units_scale = 1.0
    distance_mode = "absolute"
    feed_mode = "units_per_minute"
    motion_mode = 1
    feed_mm_min = float(default_feed)

    x = 0.0
    y = 0.0
    z = 0.0
    min_x = math.inf
    max_x = -math.inf
    min_y = math.inf
    max_y = -math.inf
    min_z = math.inf
    max_z = -math.inf
    have_bounds = False
    sample_exec_count = 0
    sample_motion_count = 0
    sample_motion_distance_mm = 0.0
    sample_rapid_distance_mm = 0.0
    sample_motion_time_min = 0.0
    sample_rapid_time_min = 0.0

    for raw_line in sampled_lines:
        line = str(raw_line or "").strip().upper()
        if not line:
            continue
        sample_exec_count += 1
        if _G20_PAT.search(line):
            units_scale = 25.4
        elif _G21_PAT.search(line):
            units_scale = 1.0
        if _G90_PAT.search(line):
            distance_mode = "absolute"
        elif _G91_PAT.search(line):
            distance_mode = "relative"
        if _G0_PAT.search(line):
            motion_mode = 0
        elif _G1_PAT.search(line):
            motion_mode = 1
        elif _G2_PAT.search(line):
            motion_mode = 2
        elif _G3_PAT.search(line):
            motion_mode = 3

        words: dict[str, float] = {}
        for word, value in _WORD_PAT.findall(line):
            val = _safe_float(value)
            if val is None:
                continue
            words[str(word).upper()] = float(val)
        if "F" in words and words["F"] > 0.0:
            feed_mm_min = max(1.0, float(words["F"]) * units_scale)

        prev_x, prev_y, prev_z = x, y, z
        moved = False
        for axis_name in _AXIS_WORDS:
            if axis_name not in words:
                continue
            moved = True
            val_mm = float(words[axis_name]) * units_scale
            if distance_mode == "relative":
                if axis_name == "X":
                    x += val_mm
                elif axis_name == "Y":
                    y += val_mm
                else:
                    z += val_mm
            else:
                if axis_name == "X":
                    x = val_mm
                elif axis_name == "Y":
                    y = val_mm
                else:
                    z = val_mm
        if not moved:
            continue

        have_bounds = True
        min_x = min(min_x, prev_x, x)
        max_x = max(max_x, prev_x, x)
        min_y = min(min_y, prev_y, y)
        max_y = max(max_y, prev_y, y)
        min_z = min(min_z, prev_z, z)
        max_z = max(max_z, prev_z, z)

        dx = x - prev_x
        dy = y - prev_y
        dz = z - prev_z
        dist_mm = math.sqrt((dx * dx) + (dy * dy) + (dz * dz))
        if dist_mm <= 0.0:
            continue
        sample_motion_count += 1
        if motion_mode == 0:
            axis_rates = []
            if abs(dx) > 1e-9:
                axis_rates.append(float(rapid_rates[0]))
            if abs(dy) > 1e-9:
                axis_rates.append(float(rapid_rates[1]))
            if abs(dz) > 1e-9:
                axis_rates.append(float(rapid_rates[2]))
            rapid_rate = min(axis_rates) if axis_rates else float(min(rapid_rates))
            rapid_rate = max(1.0, rapid_rate)
            sample_rapid_distance_mm += dist_mm
            sample_rapid_time_min += dist_mm / rapid_rate
        else:
            effective_feed = max(1.0, float(feed_mm_min))
            sample_motion_distance_mm += dist_mm
            sample_motion_time_min += dist_mm / effective_feed

    bounds_box: dict[str, float] | None = None
    if have_bounds:
        bounds_box = {
            "min_x": float(min_x),
            "max_x": float(max_x),
            "min_y": float(min_y),
            "max_y": float(max_y),
            "min_z": float(min_z),
            "max_z": float(max_z),
            "width": float(max(0.0, max_x - min_x)),
            "height": float(max(0.0, max_y - min_y)),
        }
    bounds_confidence = (
        "confident" if (cleaned_lines_known and have_bounds) else "rough"
    )
    dimensions_confidence_reasons = {
        "sampled_scan": not bool(cleaned_lines_known),
        "scan_incomplete": not bool(cleaned_lines_known),
        "no_motion_lines_found": not bool(have_bounds),
    }

    sample_scale = 1.0
    min_exec = max(
        1,
        int(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES", 2000) or 2000),
    )
    min_motion = max(
        1, int(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES", 500) or 500)
    )
    max_scale = max(
        1.0, float(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MAX_SCALE", 64.0) or 64.0)
    )
    if sample_motion_count >= min_motion and motion_total_estimate > 0:
        sample_scale = float(motion_total_estimate) / float(max(1, sample_motion_count))
    elif sample_exec_count >= min_exec and executable_total_estimate > 0:
        sample_scale = float(executable_total_estimate) / float(
            max(1, sample_exec_count)
        )
        sample_scale = min(sample_scale, max_scale * 0.5)
    elif sample_exec_count >= 250 and executable_total_estimate > 0:
        sample_scale = min(
            8.0, float(executable_total_estimate) / float(max(1, sample_exec_count))
        )
    sample_scale = max(1.0, min(float(sample_scale), max_scale))

    total_time_min = (sample_motion_time_min + sample_rapid_time_min) * sample_scale
    estimated_job_time_sec = (
        int(round(max(0.0, total_time_min) * 60.0)) if total_time_min > 0.0 else None
    )
    has_motion_settings = _has_complete_motion_settings(motion_settings)
    estimate_confidence = (
        "confident"
        if (
            has_motion_settings
            and bool(cleaned_lines_known)
            and sample_motion_count > 0
        )
        else "provisional"
    )
    estimate_confidence_reasons = {
        "missing_grbl_settings": not has_motion_settings,
        "sampled_scan": not bool(cleaned_lines_known),
        "scan_incomplete": not bool(cleaned_lines_known),
        "no_motion_lines_found": sample_motion_count <= 0,
    }

    now_ts = float(deps.time.time())
    modal_context = {
        "units": "inch" if units_scale > 1.0 else "mm",
        "distance_mode": str(distance_mode),
        "feed_mode": str(feed_mode),
        "sampled_lines": int(sample_exec_count),
        "sample_limit": int(len(sampled_lines)),
    }
    estimate_snapshot: dict[str, Any] = {
        "captured_at_ts": now_ts,
        "capture_stage": "quick_scan",
        "gcode_hash": str(source_hash or ""),
        "stats_mode": "quick_scan",
        "stats_sample_scale": float(sample_scale),
        "stats_sample_line_count": int(len(sampled_lines)),
        "stats_sample_total_lines": int(max(0, source_total_lines)),
        "stats_sample_executable_lines": int(max(0, sampled_executable_lines)),
        "stats_sample_motion_lines": int(max(0, sampled_motion_lines)),
        "stats_executable_total_lines": int(max(0, executable_total_estimate)),
        "stats_motion_total_lines": int(max(0, motion_total_estimate)),
        "sample_motion_count_quick": int(max(0, sample_motion_count)),
        "rate_source": str(rate_source),
        "estimate_confidence": str(estimate_confidence),
        "estimated_job_time_sec": int(estimated_job_time_sec)
        if estimated_job_time_sec is not None
        else None,
        "sample_motion_distance_mm": float(max(0.0, sample_motion_distance_mm)),
        "sample_rapid_distance_mm": float(max(0.0, sample_rapid_distance_mm)),
        "sample_motion_time_min": float(max(0.0, sample_motion_time_min)),
        "sample_rapid_time_min": float(max(0.0, sample_rapid_time_min)),
        "grbl_motion_settings": motion_settings,
        "rapid_rates_mm_min": (
            float(rapid_rates[0]),
            float(rapid_rates[1]),
            float(rapid_rates[2]),
        ),
        "modal_context": modal_context,
    }
    autolevel_prereq_snapshot = {
        "stage": "quick_scan",
        "prepared_at_ts": now_ts,
        "source_path": str(source_path or ""),
        "source_exists": bool(source_path and deps.os.path.isfile(source_path)),
        "source_hash": str(source_hash or ""),
        "source_total_lines": int(max(0, source_total_lines)),
        "bounds_ready": bool(bounds_box is not None),
        "bounds_confidence": str(bounds_confidence),
        "bounds": (
            (
                float(bounds_box["min_x"]),
                float(bounds_box["max_x"]),
                float(bounds_box["min_y"]),
                float(bounds_box["max_y"]),
                float(bounds_box["min_z"]),
                float(bounds_box["max_z"]),
            )
            if bounds_box is not None
            else None
        ),
        "xy_width_mm": float(bounds_box["width"]) if bounds_box is not None else 0.0,
        "xy_height_mm": float(bounds_box["height"]) if bounds_box is not None else 0.0,
        "z_min_mm": float(bounds_box["min_z"]) if bounds_box is not None else 0.0,
        "z_max_mm": float(bounds_box["max_z"]) if bounds_box is not None else 0.0,
        "probe_grid_applicable": bool(
            bounds_box is not None
            and float(bounds_box["width"]) > 0.0
            and float(bounds_box["height"]) > 0.0
        ),
    }
    return (
        bounds_box,
        bounds_confidence,
        dimensions_confidence_reasons,
        estimated_job_time_sec,
        estimate_confidence,
        estimate_confidence_reasons,
        estimate_snapshot,
        autolevel_prereq_snapshot,
    )


def _resolve_fast_prepare_scan_limit_lines(app, deps, *, file_size: int | None) -> int:
    try:
        default_limit = int(
            getattr(deps, "GCODE_PREP_FAST_SCAN_MAX_LINES_DEFAULT", 50_000)
        )
    except Exception:
        default_limit = 50_000
    try:
        low_power_limit = int(
            getattr(deps, "GCODE_PREP_FAST_SCAN_MAX_LINES_LOW_POWER", 20_000)
        )
    except Exception:
        low_power_limit = 20_000
    limit = low_power_limit if _pi_profile_enabled(app) else default_limit
    limit = max(1_000, int(limit))
    if file_size is not None and int(file_size) <= 8 * 1024 * 1024:
        # Small files complete quickly; allow more scan coverage.
        limit = max(limit, 200_000)
    return limit


def _resolve_index_mode_policy(
    deps,
    *,
    file_size: int | None,
    cleaned_lines_estimate: int,
) -> str:
    try:
        full_max_bytes = int(getattr(deps, "GCODE_OFFSET_INDEX_MAX_FILE_BYTES", 0) or 0)
    except Exception:
        full_max_bytes = 0
    try:
        full_max_lines = int(
            getattr(deps, "GCODE_OFFSET_INDEX_MAX_LINES", 250_000) or 0
        )
    except Exception:
        full_max_lines = 250_000
    try:
        sparse_min_lines = int(
            getattr(deps, "GCODE_OFFSET_INDEX_SPARSE_MIN_LINES", 40_000) or 0
        )
    except Exception:
        sparse_min_lines = 40_000
    try:
        sparse_stride = int(
            getattr(deps, "GCODE_OFFSET_INDEX_SPARSE_STRIDE_LINES", 256) or 0
        )
    except Exception:
        sparse_stride = 256
    if (
        full_max_bytes > 0
        and file_size is not None
        and int(file_size) <= full_max_bytes
    ):
        if full_max_lines <= 0 or cleaned_lines_estimate <= full_max_lines:
            return "full"
    if cleaned_lines_estimate >= max(1_000, sparse_min_lines) and sparse_stride > 1:
        return "sparse"
    return "none"


def _sample_tail_lines_from_file(
    path: str,
    *,
    deps,
    limit: int,
    file_size: int | None,
) -> list[str]:
    if limit <= 0:
        return []
    max_bytes = 2 * 1024 * 1024
    if file_size is not None:
        max_bytes = min(int(file_size), max_bytes)
    try:
        with open(path, "rb") as handle:
            if file_size is None:
                try:
                    handle.seek(0, 2)
                    file_size = int(handle.tell())
                except Exception:
                    file_size = None
            if file_size is not None and file_size > 0:
                handle.seek(max(0, int(file_size) - max_bytes))
            raw = handle.read()
    except Exception:
        return []
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = cast(str, deps.clean_gcode_line(raw_line))
        if line:
            cleaned.append(line)
    if not cleaned:
        return []
    return cleaned[-limit:]


def _trim_sampled_lines(
    *,
    head_lines: list[str],
    periodic_lines: list[str],
    tail_lines: list[str],
    sampled_max_lines: int,
) -> list[str]:
    if sampled_max_lines <= 0:
        merged = list(head_lines)
        merged.extend(periodic_lines)
        merged.extend(tail_lines)
        return merged
    out: list[str] = []
    head_take = min(len(head_lines), sampled_max_lines)
    out.extend(head_lines[:head_take])
    remaining = sampled_max_lines - len(out)
    if remaining <= 0:
        return out

    tail_reserve = min(len(tail_lines), max(0, remaining // 2))
    periodic_budget = max(0, remaining - tail_reserve)
    if periodic_budget > 0 and periodic_lines:
        if len(periodic_lines) <= periodic_budget:
            out.extend(periodic_lines)
        else:
            stride = max(1, len(periodic_lines) // periodic_budget)
            out.extend(periodic_lines[::stride][:periodic_budget])
    remaining = sampled_max_lines - len(out)
    if remaining > 0 and tail_lines:
        out.extend(tail_lines[-remaining:])
    return out[:sampled_max_lines]


def _prepare_stream_source_fast(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _FastPrepareData:
    started_at = deps.time.perf_counter()
    success = False
    try:
        _check_load_token(app, token)
        sample_lines: list[str] = []
        sampled_head: list[str] = []
        sampled_periodic: list[str] = []
        sampled_tail_limit = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_TAIL_LINES", 0) or 0)
        )
        sampled_tail: deque[str] = deque(
            maxlen=sampled_tail_limit if sampled_tail_limit > 0 else None
        )
        sampled_head_limit = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_HEAD_LINES", 0) or 0)
        )
        sampled_interval = max(
            1, int(getattr(deps, "GCODE_PREP_SAMPLE_INTERVAL_LINES", 1) or 1)
        )
        sampled_max_lines = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_MAX_LINES", 0) or 0)
        )
        sample_limit = max(
            0, int(getattr(deps, "GCODE_STREAMING_SAMPLE_LINES", 0) or 0)
        )
        line_scan_limit = _resolve_fast_prepare_scan_limit_lines(
            app, deps, file_size=file_size
        )
        ssmeta_present, ssmeta = _read_ssmeta_header(
            path,
            max_lines=max(
                1,
                int(getattr(deps, "GCODE_SSMETA_HEADER_MAX_LINES", 500) or 500),
            ),
            max_bytes=max(
                1024,
                int(getattr(deps, "GCODE_SSMETA_HEADER_MAX_BYTES", 64 * 1024) or (64 * 1024)),
            ),
        )
        ssmeta_bounds = _ssmeta_bounds_box(ssmeta)
        ssmeta_units = _ssmeta_units_value(ssmeta)
        ssmeta_scan_reduced = False
        if ssmeta_bounds is not None and ssmeta_units is not None:
            reduced_limit = max(
                1_000,
                int(
                    getattr(deps, "GCODE_SSMETA_FAST_SCAN_MAX_LINES", 4_000) or 4_000
                ),
            )
            if line_scan_limit > reduced_limit:
                line_scan_limit = reduced_limit
                ssmeta_scan_reduced = True

        progress_label = f"Preparing {deps.os.path.basename(path)}"
        progress_last_ts = 0.0
        progress_last_pct = -1
        raw_lines_scanned = 0
        cleaned_lines_scanned = 0
        motion_lines_scanned = 0
        bytes_scanned = 0
        sampled_line_index = 0
        reached_eof = False
        sample_hasher = deps.hashlib.sha256()

        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            while raw_lines_scanned < line_scan_limit:
                if (raw_lines_scanned & 0x1FF) == 0:
                    _check_load_token(app, token)
                raw_line = handle.readline()
                if not raw_line:
                    reached_eof = True
                    break
                raw_lines_scanned += 1
                try:
                    bytes_scanned = int(handle.tell())
                except Exception:
                    bytes_scanned = max(0, bytes_scanned)
                if file_size and (raw_lines_scanned & 0x7F) == 0:
                    now = deps.time.perf_counter()
                    if now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL:
                        pct = min(100, int(bytes_scanned * 100 / file_size))
                        if pct != progress_last_pct:
                            _emit_progress(app, token, pct, 100, progress_label)
                            progress_last_pct = pct
                        progress_last_ts = now
                cleaned = cast(str, deps.clean_gcode_line(raw_line))
                if not cleaned:
                    continue
                cleaned_lines_scanned += 1
                if _is_motion_line(cleaned):
                    motion_lines_scanned += 1
                sampled_line_index += 1
                if sample_limit > 0 and len(sample_lines) < sample_limit:
                    sample_lines.append(cleaned)
                if sampled_head_limit > 0 and sampled_line_index <= sampled_head_limit:
                    sampled_head.append(cleaned)
                elif (
                    sampled_interval > 0
                    and (sampled_line_index % sampled_interval) == 0
                ):
                    sampled_periodic.append(cleaned)
                if sampled_tail_limit > 0:
                    sampled_tail.append(cleaned)
                if len(sampled_head) < 256:
                    sample_hasher.update(cleaned.encode("utf-8"))
                    sample_hasher.update(b"\n")

        if file_size:
            _emit_progress(
                app,
                token,
                min(progress_last_pct if progress_last_pct >= 0 else 0, 95),
                100,
                progress_label,
            )
        tail_lines = list(sampled_tail)
        if not reached_eof and sampled_tail_limit > 0:
            tail_lines = _sample_tail_lines_from_file(
                path,
                deps=deps,
                limit=sampled_tail_limit,
                file_size=file_size,
            )
        sampled_lines = _trim_sampled_lines(
            head_lines=sampled_head,
            periodic_lines=sampled_periodic,
            tail_lines=tail_lines,
            sampled_max_lines=sampled_max_lines,
        )
        _emit_progress(
            app, token, 95, 100, f"Computing bounds: {deps.os.path.basename(path)}"
        )
        sampled_executable_lines = int(len(sampled_lines))
        sampled_motion_lines = 0
        for sampled_line in sampled_lines:
            if _is_motion_line(sampled_line):
                sampled_motion_lines += 1
        cleaned_lines_known = bool(reached_eof)
        file_line_count_known = bool(reached_eof)
        file_line_count = int(raw_lines_scanned)
        if (
            not file_line_count_known
            and file_size
            and bytes_scanned > 0
            and raw_lines_scanned > 0
        ):
            try:
                file_line_count = max(
                    file_line_count,
                    int(
                        (float(raw_lines_scanned) / float(bytes_scanned))
                        * float(file_size)
                    ),
                )
            except Exception:
                file_line_count = int(raw_lines_scanned)
        cleaned_lines_estimate = int(cleaned_lines_scanned)
        executable_lines_estimate = int(cleaned_lines_scanned)
        motion_lines_estimate = int(motion_lines_scanned)
        if not cleaned_lines_known:
            if file_size and bytes_scanned > 0 and cleaned_lines_scanned > 0:
                density = float(cleaned_lines_scanned) / float(max(1, bytes_scanned))
                estimated = int(float(file_size) * density)
                cleaned_lines_estimate = max(cleaned_lines_estimate, estimated)
                executable_lines_estimate = max(executable_lines_estimate, estimated)
            else:
                cleaned_lines_estimate = max(cleaned_lines_estimate, raw_lines_scanned)
                executable_lines_estimate = max(
                    executable_lines_estimate, raw_lines_scanned
                )
            if cleaned_lines_scanned > 0 and motion_lines_scanned > 0:
                motion_ratio = float(motion_lines_scanned) / float(
                    max(1, cleaned_lines_scanned)
                )
                motion_lines_estimate = max(
                    motion_lines_estimate,
                    int(max(1.0, float(executable_lines_estimate) * motion_ratio)),
                )
        cleaned_lines_estimate = max(cleaned_lines_estimate, len(sample_lines))
        executable_lines_estimate = max(executable_lines_estimate, len(sample_lines))
        motion_lines_estimate = max(motion_lines_estimate, sampled_motion_lines)
        quick_scan_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        (
            bounds_box,
            bounds_confidence,
            dimensions_confidence_reasons,
            estimated_job_time_sec,
            estimate_confidence,
            estimate_confidence_reasons,
            estimate_inputs_snapshot,
            autolevel_prereq_snapshot,
        ) = _quick_scan_bounds_and_estimate(
            app,
            deps,
            sampled_lines=sampled_lines,
            sampled_executable_lines=sampled_executable_lines,
            sampled_motion_lines=sampled_motion_lines,
            executable_total_estimate=executable_lines_estimate,
            motion_total_estimate=motion_lines_estimate,
            cleaned_lines_known=cleaned_lines_known,
            source_path=path,
            source_hash="",
            source_total_lines=cleaned_lines_estimate,
        )
        dimensions_source = "scan"
        units_source = "scan"
        if ssmeta_units:
            modal_context = estimate_inputs_snapshot.get("modal_context", None)
            if isinstance(modal_context, dict):
                modal_context["units"] = ssmeta_units
            estimate_inputs_snapshot["units_source"] = "ssmeta"
            units_source = "ssmeta"
        if ssmeta_bounds is not None:
            bounds_box = dict(ssmeta_bounds)
            bounds_confidence = "confident"
            dimensions_confidence_reasons = {
                "sampled_scan": False,
                "scan_incomplete": False,
                "no_motion_lines_found": False,
                "ssmeta_override": True,
            }
            dimensions_source = "ssmeta"
            estimate_inputs_snapshot["dimensions_source"] = "ssmeta"
            autolevel_prereq_snapshot["bounds_ready"] = True
            autolevel_prereq_snapshot["bounds_confidence"] = "confident"
            autolevel_prereq_snapshot["bounds"] = (
                float(bounds_box["min_x"]),
                float(bounds_box["max_x"]),
                float(bounds_box["min_y"]),
                float(bounds_box["max_y"]),
                float(bounds_box["min_z"]),
                float(bounds_box["max_z"]),
            )
            autolevel_prereq_snapshot["xy_width_mm"] = float(bounds_box["width"])
            autolevel_prereq_snapshot["xy_height_mm"] = float(bounds_box["height"])
            autolevel_prereq_snapshot["z_min_mm"] = float(bounds_box["min_z"])
            autolevel_prereq_snapshot["z_max_mm"] = float(bounds_box["max_z"])
            autolevel_prereq_snapshot["probe_grid_applicable"] = bool(
                float(bounds_box["width"]) > 0.0 and float(bounds_box["height"]) > 0.0
            )
        estimate_inputs_snapshot["ssmeta_present"] = bool(ssmeta_present)
        estimate_inputs_snapshot["ssmeta_fields"] = sorted(ssmeta.keys())
        _emit_progress(
            app, token, 97, 100, f"Computing estimate: {deps.os.path.basename(path)}"
        )
        _emit_progress(
            app,
            token,
            99,
            100,
            f"Preparing auto-level data: {deps.os.path.basename(path)}",
        )
        if cleaned_lines_known:
            _emit_progress(app, token, 100, 100, progress_label)
        else:
            _emit_progress(app, token, 100, 100, f"{progress_label} (sampled)")
        quick_fingerprint = _quick_file_fingerprint(deps, path, file_size=file_size)
        lines_hash_quick = f"{quick_fingerprint}:{sample_hasher.hexdigest()[:16]}"
        estimate_inputs_snapshot["gcode_hash"] = lines_hash_quick
        autolevel_prereq_snapshot["source_hash"] = lines_hash_quick
        success = True
        result = _FastPrepareData(
            sample_lines=sample_lines,
            sampled_head_lines=sampled_head_limit,
            sampled_tail_lines=sampled_tail_limit,
            sampled_interval_lines=sampled_interval,
            sampled_max_lines=sampled_max_lines,
            sampled_line_count=int(len(sampled_lines)),
            file_size_bytes=int(file_size or 0),
            file_line_count=int(file_line_count),
            file_line_count_known=bool(file_line_count_known),
            cleaned_lines_estimate=cleaned_lines_estimate,
            cleaned_lines_known=cleaned_lines_known,
            executable_lines_estimate=executable_lines_estimate,
            motion_lines_estimate=motion_lines_estimate,
            sampled_executable_lines=sampled_executable_lines,
            sampled_motion_lines=sampled_motion_lines,
            raw_lines_scanned=raw_lines_scanned,
            bytes_scanned=bytes_scanned,
            lines_hash_quick=lines_hash_quick,
            quick_scan_ms=float(quick_scan_ms),
            bounds_box=bounds_box,
            bounds_confidence=str(bounds_confidence),
            dimensions_confidence_reasons=dict(dimensions_confidence_reasons),
            estimated_job_time_sec=estimated_job_time_sec,
            estimate_confidence=str(estimate_confidence),
            estimate_confidence_reasons=dict(estimate_confidence_reasons),
            estimate_inputs_snapshot=estimate_inputs_snapshot,
            autolevel_prereq_snapshot=autolevel_prereq_snapshot,
            ssmeta_present=bool(ssmeta_present),
            ssmeta=dict(ssmeta),
            dimensions_source=str(dimensions_source),
            units_source=str(units_source),
            ssmeta_scan_reduced=bool(ssmeta_scan_reduced),
        )
        # Drop scan-temporary buffers before returning to keep worker RSS steady.
        sampled_head.clear()
        sampled_periodic.clear()
        sampled_tail.clear()
        tail_lines.clear()
        sampled_lines.clear()
        return result
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.prepare_fast", elapsed_ms, success=success)


def quick_scan_gcode(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _FastPrepareData:
    """Unified low-memory quick scan used by every load before stream-ready."""
    return _prepare_stream_source_fast(
        app,
        path,
        token,
        deps,
        file_size=file_size,
    )


def _stream_from_disk(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    log_message: str | None = None,
) -> None:
    started_at = deps.time.perf_counter()
    success = False
    prepare_data: _FastPrepareData | None = None
    try:
        _check_load_token(app, token)
        if log_message:
            app.ui_q.put(("log", log_message))
        # Fast file-backed load path: become stream-ready immediately without
        # mandatory full rewrite/validation/hash/index passes.
        sample_only = True
        cache_profile = "disabled"
        setattr(app, "_gcode_full_line_cache_profile", cache_profile)
        setattr(app, "_gcode_full_line_cache_cap_lines", 0)
        setattr(app, "_gcode_full_line_cache_cap_hit", False)
        setattr(
            app,
            "_gcode_sample_line_cap",
            int(getattr(deps, "GCODE_STREAMING_SAMPLE_LINES", 0) or 0),
        )
        prepare_data = quick_scan_gcode(
            app,
            path,
            token,
            deps,
            file_size=file_size,
        )
        _check_load_token(app, token)
        app._gcode_quick_scan_ms = float(
            getattr(prepare_data, "quick_scan_ms", 0.0) or 0.0
        )
        app._gcode_post_popup_background_tasks = "none"
        app.ui_q.put(
            (
                "log",
                "[gcode] Run path stays fast; use Overdrive > Validate Loaded Job for optional deep validation.",
            )
        )
        cleaned_lines_estimate = max(0, int(prepare_data.cleaned_lines_estimate))
        index_mode_requested = _resolve_index_mode_policy(
            deps,
            file_size=file_size,
            cleaned_lines_estimate=cleaned_lines_estimate,
        )
        lines_for_app = list(prepare_data.sample_lines)
        source = deps.FileGcodeSource(
            path,
            offsets=None,
            already_clean=False,
            total_lines=cleaned_lines_estimate,
            line_count_known=bool(prepare_data.cleaned_lines_known),
        )
        setattr(source, "_cleanup_path", None)
        setattr(source, "_prepare_sampled_lines", None)
        setattr(
            source, "_prepare_sample_head_lines", int(prepare_data.sampled_head_lines)
        )
        setattr(
            source, "_prepare_sample_tail_lines", int(prepare_data.sampled_tail_lines)
        )
        setattr(
            source,
            "_prepare_sample_interval_lines",
            int(prepare_data.sampled_interval_lines),
        )
        setattr(
            source, "_prepare_sample_max_lines", int(prepare_data.sampled_max_lines)
        )
        setattr(
            source, "_prepare_sample_line_count", int(prepare_data.sampled_line_count)
        )
        setattr(
            source,
            "_prepare_file_size_bytes",
            int(getattr(prepare_data, "file_size_bytes", 0) or 0),
        )
        setattr(
            source,
            "_prepare_file_line_count",
            int(getattr(prepare_data, "file_line_count", 0) or 0),
        )
        setattr(
            source,
            "_prepare_file_line_count_known",
            bool(getattr(prepare_data, "file_line_count_known", False)),
        )
        setattr(
            source,
            "_prepare_executable_total_lines",
            int(prepare_data.executable_lines_estimate),
        )
        setattr(
            source,
            "_prepare_executable_total_lines_known",
            bool(getattr(prepare_data, "cleaned_lines_known", False)),
        )
        setattr(
            source,
            "_prepare_motion_total_lines",
            int(prepare_data.motion_lines_estimate),
        )
        setattr(
            source,
            "_prepare_motion_total_lines_known",
            bool(getattr(prepare_data, "cleaned_lines_known", False)),
        )
        setattr(
            source,
            "_prepare_sampled_executable_lines",
            int(prepare_data.sampled_executable_lines),
        )
        setattr(
            source,
            "_prepare_sampled_motion_lines",
            int(prepare_data.sampled_motion_lines),
        )
        setattr(source, "_offset_index_enabled", False)
        setattr(source, "_index_mode_requested", str(index_mode_requested))
        setattr(source, "_load_mode", "quick_scan_ready")
        setattr(source, "_quick_hash", prepare_data.lines_hash_quick)
        setattr(source, "_strict_validation_requested", False)
        setattr(
            source,
            "_quick_scan_ms",
            float(getattr(prepare_data, "quick_scan_ms", 0.0) or 0.0),
        )
        setattr(source, "_quick_bounds_box", prepare_data.bounds_box)
        setattr(
            source,
            "_quick_bounds_confidence",
            str(getattr(prepare_data, "bounds_confidence", "rough") or "rough"),
        )
        setattr(
            source,
            "_quick_dimensions_confidence",
            str(getattr(prepare_data, "bounds_confidence", "rough") or "rough"),
        )
        setattr(
            source,
            "_quick_dimensions_confidence_reasons",
            dict(getattr(prepare_data, "dimensions_confidence_reasons", {}) or {}),
        )
        setattr(
            source, "_quick_estimated_job_time_sec", prepare_data.estimated_job_time_sec
        )
        setattr(
            source,
            "_quick_estimate_confidence",
            str(
                getattr(prepare_data, "estimate_confidence", "provisional")
                or "provisional"
            ),
        )
        setattr(
            source,
            "_quick_estimate_confidence_reasons",
            dict(getattr(prepare_data, "estimate_confidence_reasons", {}) or {}),
        )
        setattr(
            source,
            "_quick_estimate_inputs_snapshot",
            dict(getattr(prepare_data, "estimate_inputs_snapshot", {}) or {}),
        )
        setattr(
            source,
            "_quick_autolevel_prereq_snapshot",
            dict(getattr(prepare_data, "autolevel_prereq_snapshot", {}) or {}),
        )
        setattr(source, "_quick_ssmeta_present", bool(prepare_data.ssmeta_present))
        setattr(source, "_quick_ssmeta", dict(getattr(prepare_data, "ssmeta", {}) or {}))
        setattr(
            source,
            "_quick_dimensions_source",
            str(getattr(prepare_data, "dimensions_source", "scan") or "scan"),
        )
        setattr(
            source,
            "_quick_units_source",
            str(getattr(prepare_data, "units_source", "scan") or "scan"),
        )
        setattr(
            source,
            "_quick_ssmeta_scan_reduced",
            bool(getattr(prepare_data, "ssmeta_scan_reduced", False)),
        )
        app.ui_q.put(
            (
                "log",
                "[gcode] Quick scan complete; file-backed stream-ready: "
                f"mode=quick_scan_ready, "
                f"index={index_mode_requested}, "
                f"line_count={'known' if prepare_data.cleaned_lines_known else 'estimated'}({cleaned_lines_estimate:,}).",
            )
        )
        app.ui_q.put(
            (
                "log",
                f"[gcode] Quick Scan summary: bounds_conf={prepare_data.bounds_confidence}, "
                f"estimate_conf={prepare_data.estimate_confidence}, "
                f"estimate_sec={prepare_data.estimated_job_time_sec if prepare_data.estimated_job_time_sec is not None else 'n/a'}, "
                f"quick_scan_ms={float(getattr(prepare_data, 'quick_scan_ms', 0.0) or 0.0):.2f}, "
                f"ssmeta={'present' if prepare_data.ssmeta_present else 'not_found'}, "
                f"dimensions_source={prepare_data.dimensions_source}, "
                f"units_source={prepare_data.units_source}.",
            )
        )
        app.ui_q.put(
            (
                "gcode_loaded_stream",
                token,
                path,
                source,
                lines_for_app,
                prepare_data.lines_hash_quick,
                cleaned_lines_estimate,
                None,
                sample_only,
            )
        )
        record_task_timing(
            app,
            "gcode.load.time_to_stream_ready",
            max(0.0, (deps.time.perf_counter() - started_at) * 1000.0),
            success=True,
        )
        success = True
    except _GcodeLoadCancelled:
        return
    except Exception:
        raise
    finally:
        # Release scan objects as soon as possible; streaming source retains only
        # compact metadata and bounded sample lines passed through the UI event.
        prepare_data = None
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.stream_total", elapsed_ms, success=success)


def load_gcode_from_path(app, path: str, module):
    deps = module
    if app.grbl.is_streaming():
        deps.messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_LOADING_NEW_GCODE,
        )
        return
    if not deps.os.path.isfile(path):
        deps.messagebox.showerror("Open G-code", "File not found.")
        return
    app.settings["last_gcode_dir"] = deps.os.path.dirname(path)
    app._gcode_load_token += 1
    token = app._gcode_load_token
    app._gcode_loading = True
    try:
        app._gcode_load_started_at = deps.time.perf_counter()
    except Exception:
        app._gcode_load_started_at = None
    deps.disable_job_controls(app)
    app.gcode_stats_var.set("Preparing job...")
    app._gcode_status_last_text = "Preparing job..."
    app.status.config(text=f"Preparing job: {deps.os.path.basename(path)}")
    app._set_gcode_loading_indeterminate(f"reading {deps.os.path.basename(path)}")
    app.gview.clear()

    file_size = None
    ultra_large_mode = False
    ultra_large_threshold_bytes = _resolve_ultra_large_threshold_bytes(app, deps)
    try:
        file_size = deps.os.path.getsize(path)
        ultra_large_mode = _is_ultra_large_file_with_threshold(
            file_size=file_size,
            threshold_bytes=ultra_large_threshold_bytes,
        )
    except OSError:
        ultra_large_mode = False
    def worker():
        try:
            log_message = "[gcode] Using file-backed streaming load mode (bounded sample/sample retention)."
            if ultra_large_mode:
                ultra_threshold_text = _format_mb(ultra_large_threshold_bytes)
                size_text = _format_mb(file_size)
                app.ui_q.put(
                    (
                        "log",
                        f"[gcode] Ultra-large mode active ({size_text} >= {ultra_threshold_text}); "
                        "forcing fast-load mode with sampled prepare.",
                    )
                )
            _stream_from_disk(
                app,
                path,
                token,
                deps,
                file_size=file_size,
                log_message=log_message,
            )
        except _GcodeLoadCancelled:
            return
        except Exception as exc:
            app.ui_q.put(("gcode_load_error", token, path, str(exc)))

    deps.threading.Thread(target=worker, daemon=True).start()
