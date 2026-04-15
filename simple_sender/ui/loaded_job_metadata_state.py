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

from dataclasses import dataclass, field


@dataclass(slots=True)
class LoadedJobMetadataState:
    source: object | None = None
    last_gcode_path: str | None = None
    gcode_hash: str | None = None
    storage_mode: str = "none"
    load_mode: str = ""
    index_mode: str = "none"
    source_line_count_known: bool = False
    retained_line_count: int = 0
    source_offset_count: int = 0
    source_offset_type: str = ""
    file_size_bytes: int = 0
    file_line_count: int = 0
    file_line_count_known: bool = False
    total_lines: int = 0
    total_lines_known: bool = False
    executable_lines: int = 0
    executable_lines_known: bool = False
    motion_lines: int = 0
    motion_lines_known: bool = False
    bounds_box: dict[str, object] | None = None
    bounds_confidence: str = "rough"
    dimensions_confidence: str = "rough"
    estimated_job_time_sec: float | None = None
    estimate_confidence: str = "provisional"
    ssmeta_present: bool = False
    ssmeta: dict[str, object] = field(default_factory=dict)
    dimensions_source: str = "scan"
    units_source: str = "scan"
    ssmeta_scan_reduced: bool = False


def _safe_bool_attr(app: object, name: str, fallback: bool = False) -> bool:
    try:
        return bool(getattr(app, name, fallback))
    except Exception:
        return bool(fallback)


def _safe_int_attr(app: object, name: str, fallback: int = 0) -> int:
    try:
        return int(getattr(app, name, fallback) or 0)
    except Exception:
        return int(fallback)


def _safe_str_attr(app: object, name: str, fallback: str = "") -> str:
    try:
        value = getattr(app, name, fallback)
    except Exception:
        value = fallback
    return str(value or "").strip()


def _safe_optional_str_attr(app: object, name: str) -> str | None:
    value = _safe_str_attr(app, name, "")
    return value or None


def _safe_optional_float_attr(app: object, name: str, fallback: float | None) -> float | None:
    try:
        value = getattr(app, name, fallback)
    except Exception:
        value = fallback
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return fallback


def _safe_dict_attr(app: object, name: str, fallback: dict[str, object]) -> dict[str, object]:
    try:
        value = getattr(app, name, fallback)
    except Exception:
        value = fallback
    return dict(value) if isinstance(value, dict) else dict(fallback)


def _safe_optional_dict_attr(
    app: object,
    name: str,
    fallback: dict[str, object] | None,
) -> dict[str, object] | None:
    try:
        value = getattr(app, name, fallback)
    except Exception:
        value = fallback
    if value is None:
        return None
    return dict(value) if isinstance(value, dict) else None


def sync_loaded_job_metadata_state_from_app(
    app: object,
    state: LoadedJobMetadataState,
) -> LoadedJobMetadataState:
    state.source = getattr(app, "_gcode_source", state.source)
    state.last_gcode_path = _safe_optional_str_attr(app, "_last_gcode_path")
    state.gcode_hash = _safe_optional_str_attr(app, "_gcode_hash")
    state.storage_mode = _safe_str_attr(app, "_gcode_storage_mode", state.storage_mode)
    state.load_mode = _safe_str_attr(app, "_gcode_load_mode", state.load_mode)
    state.index_mode = _safe_str_attr(app, "_gcode_index_mode", state.index_mode)
    state.source_line_count_known = _safe_bool_attr(
        app,
        "_gcode_source_line_count_known",
        state.source_line_count_known,
    )
    state.retained_line_count = _safe_int_attr(
        app,
        "_gcode_retained_line_count",
        state.retained_line_count,
    )
    state.source_offset_count = _safe_int_attr(
        app,
        "_gcode_source_offset_count",
        state.source_offset_count,
    )
    state.source_offset_type = _safe_str_attr(
        app,
        "_gcode_source_offset_type",
        state.source_offset_type,
    )
    state.file_size_bytes = _safe_int_attr(app, "_gcode_file_size_bytes", state.file_size_bytes)
    state.file_line_count = _safe_int_attr(app, "_gcode_file_line_count", state.file_line_count)
    state.file_line_count_known = _safe_bool_attr(
        app,
        "_gcode_file_line_count_known",
        state.file_line_count_known,
    )
    state.total_lines = _safe_int_attr(app, "_gcode_total_lines", state.total_lines)
    state.total_lines_known = _safe_bool_attr(
        app,
        "_gcode_total_lines_known",
        state.total_lines_known,
    )
    state.executable_lines = _safe_int_attr(
        app,
        "_gcode_executable_lines",
        state.executable_lines,
    )
    state.executable_lines_known = _safe_bool_attr(
        app,
        "_gcode_executable_lines_known",
        state.executable_lines_known,
    )
    state.motion_lines = _safe_int_attr(app, "_gcode_motion_lines", state.motion_lines)
    state.motion_lines_known = _safe_bool_attr(
        app,
        "_gcode_motion_lines_known",
        state.motion_lines_known,
    )
    state.bounds_box = _safe_optional_dict_attr(app, "_gcode_bounds_box", state.bounds_box)
    state.bounds_confidence = _safe_str_attr(
        app,
        "_gcode_bounds_confidence",
        state.bounds_confidence,
    )
    state.dimensions_confidence = _safe_str_attr(
        app,
        "_gcode_dimensions_confidence",
        state.dimensions_confidence,
    )
    state.estimated_job_time_sec = _safe_optional_float_attr(
        app,
        "_gcode_estimated_job_time_sec",
        state.estimated_job_time_sec,
    )
    state.estimate_confidence = _safe_str_attr(
        app,
        "_estimate_confidence",
        state.estimate_confidence,
    )
    state.ssmeta_present = _safe_bool_attr(app, "_gcode_ssmeta_present", state.ssmeta_present)
    state.ssmeta = _safe_dict_attr(app, "_gcode_ssmeta", state.ssmeta)
    state.dimensions_source = _safe_str_attr(
        app,
        "_gcode_dimensions_source",
        state.dimensions_source,
    )
    state.units_source = _safe_str_attr(app, "_gcode_units_source", state.units_source)
    state.ssmeta_scan_reduced = _safe_bool_attr(
        app,
        "_gcode_ssmeta_scan_reduced",
        state.ssmeta_scan_reduced,
    )
    return state


def sync_loaded_job_metadata_state_to_app(
    app: object,
    state: LoadedJobMetadataState,
) -> LoadedJobMetadataState:
    setattr(app, "_loaded_job_metadata_state", state)
    setattr(app, "_gcode_source", state.source)
    setattr(app, "_last_gcode_path", state.last_gcode_path)
    setattr(app, "_gcode_hash", state.gcode_hash)
    setattr(app, "_gcode_storage_mode", str(state.storage_mode or "none").strip() or "none")
    setattr(app, "_gcode_load_mode", str(state.load_mode or "").strip())
    setattr(app, "_gcode_index_mode", str(state.index_mode or "none").strip() or "none")
    setattr(app, "_gcode_source_line_count_known", bool(state.source_line_count_known))
    setattr(app, "_gcode_retained_line_count", max(0, int(state.retained_line_count)))
    setattr(app, "_gcode_source_offset_count", max(0, int(state.source_offset_count)))
    setattr(app, "_gcode_source_offset_type", str(state.source_offset_type or "").strip())
    setattr(app, "_gcode_file_size_bytes", max(0, int(state.file_size_bytes)))
    setattr(app, "_gcode_file_line_count", max(0, int(state.file_line_count)))
    setattr(app, "_gcode_file_line_count_known", bool(state.file_line_count_known))
    setattr(app, "_gcode_total_lines", max(0, int(state.total_lines)))
    setattr(app, "_gcode_total_lines_known", bool(state.total_lines_known))
    setattr(app, "_gcode_executable_lines", max(0, int(state.executable_lines)))
    setattr(app, "_gcode_executable_lines_known", bool(state.executable_lines_known))
    setattr(app, "_gcode_motion_lines", max(0, int(state.motion_lines)))
    setattr(app, "_gcode_motion_lines_known", bool(state.motion_lines_known))
    setattr(app, "_gcode_bounds_box", dict(state.bounds_box) if isinstance(state.bounds_box, dict) else None)
    setattr(app, "_gcode_bounds_confidence", str(state.bounds_confidence or "rough").strip() or "rough")
    setattr(
        app,
        "_gcode_dimensions_confidence",
        str(state.dimensions_confidence or "rough").strip() or "rough",
    )
    setattr(app, "_gcode_estimated_job_time_sec", state.estimated_job_time_sec)
    setattr(app, "_estimate_confidence", str(state.estimate_confidence or "provisional").strip() or "provisional")
    setattr(app, "_gcode_ssmeta_present", bool(state.ssmeta_present))
    setattr(app, "_gcode_ssmeta", dict(state.ssmeta))
    setattr(app, "_gcode_dimensions_source", str(state.dimensions_source or "scan").strip() or "scan")
    setattr(app, "_gcode_units_source", str(state.units_source or "scan").strip() or "scan")
    setattr(app, "_gcode_ssmeta_scan_reduced", bool(state.ssmeta_scan_reduced))
    return state


def get_loaded_job_metadata_state(app: object) -> LoadedJobMetadataState:
    state = getattr(app, "_loaded_job_metadata_state", None)
    if not isinstance(state, LoadedJobMetadataState):
        state = LoadedJobMetadataState()
    sync_loaded_job_metadata_state_from_app(app, state)
    sync_loaded_job_metadata_state_to_app(app, state)
    return state
