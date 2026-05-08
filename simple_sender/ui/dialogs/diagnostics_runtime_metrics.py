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

import os
import time
from collections import deque
from typing import Any, Callable, cast


def _safe_float_var_get(app: Any, attr_name: str) -> float:
    try:
        return float(getattr(app, attr_name).get())
    except Exception:
        return 0.0


def _normalize_confidence_label(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value == "confident":
        return "confident"
    if value == "provisional":
        return "provisional"
    return "rough"


def _append_kasa_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    kasa_status_snapshot: Callable[[Any], dict[str, Any]],
    format_kasa_status_line: Callable[[Any], str],
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    try:
        kasa_snapshot = kasa_status_snapshot(app)
    except Exception as exc:
        log_suppressed("Failed collecting Kasa status snapshot for diagnostics", exc)
        kasa_snapshot = {}
    if isinstance(kasa_snapshot, dict) and kasa_snapshot:
        metrics["kasa_status"] = dict(kasa_snapshot)
    try:
        kasa_line = str(format_kasa_status_line(app) or "").strip()
    except Exception as exc:
        log_suppressed("Failed building Kasa status line for diagnostics", exc)
        kasa_line = ""
    if kasa_line:
        metrics["kasa_status_line"] = kasa_line


def _append_grbl_runtime_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    log_suppressed: Callable[[str, BaseException], None],
) -> Any:
    grbl = getattr(app, "grbl", None)
    getter = getattr(grbl, "get_runtime_metrics", None) if grbl is not None else None
    if callable(getter):
        try:
            raw = getter()
        except Exception as exc:
            log_suppressed("Failed collecting runtime telemetry metrics", exc)
            raw = {}
        if isinstance(raw, dict):
            metrics.update(cast(dict[str, Any], raw))
            if (
                "status_poll_interval_effective_s" not in metrics
                and "status_poll_interval_s" in metrics
            ):
                metrics["status_poll_interval_effective_s"] = float(
                    metrics.get("status_poll_interval_s", 0.0) or 0.0
                )
    return grbl


def _append_perf_monitor_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    perf_monitor = getattr(app, "_perf_monitor", None)
    metrics["perf_available"] = False
    if perf_monitor is None:
        metrics["perf_phase_sampling_note"] = (
            "Runtime performance profiling is disabled. "
            "Enable diagnostics performance profiling and restart before capture."
        )
        return
    snapshot_getter = getattr(perf_monitor, "runtime_snapshot", None)
    if not callable(snapshot_getter):
        metrics["perf_phase_sampling_note"] = (
            "Runtime performance profiling monitor is unavailable."
        )
        return
    try:
        snapshot = snapshot_getter()
    except Exception as exc:
        log_suppressed(
            "Failed collecting performance-monitor runtime snapshot", exc
        )
        snapshot = None
    if not isinstance(snapshot, dict):
        metrics["perf_phase_sampling_note"] = (
            "Runtime performance profiling snapshot is unavailable."
        )
        return
    metrics["perf_available"] = bool(snapshot.get("available", True))
    for key, value in snapshot.items():
        metrics[f"perf_{key}"] = value
    phase_metrics = snapshot.get("phase_metrics")
    if isinstance(phase_metrics, dict):
        phase_sample_counts: dict[str, int] = {}
        missing_phases: list[str] = []
        for phase_name in ("idle_connected", "streaming"):
            phase_entry = phase_metrics.get(phase_name)
            samples = 0
            if isinstance(phase_entry, dict):
                samples = int(phase_entry.get("samples", 0) or 0)
            phase_sample_counts[phase_name] = samples
            if samples <= 0:
                missing_phases.append(phase_name)
        metrics["perf_phase_sample_counts"] = phase_sample_counts
        metrics["perf_phase_sampling_active"] = bool(
            any(count > 0 for count in phase_sample_counts.values())
        )
        if missing_phases:
            metrics["perf_phase_sampling_note"] = (
                "Phase samples missing for: "
                + ", ".join(missing_phases)
                + ". Keep diagnostics profiling enabled and spend at least one sample interval in each phase."
            )
        else:
            metrics["perf_phase_sampling_note"] = (
                "Phase sampling populated for idle-connected and streaming."
            )
    else:
        metrics["perf_phase_sampling_note"] = (
            "Phase metrics unavailable from performance monitor snapshot."
        )


def _append_status_perf_metrics(app: Any, metrics: dict[str, Any]) -> None:
    raw_status_perf = getattr(app, "_status_perf_metrics", None)
    if not isinstance(raw_status_perf, dict) or not raw_status_perf:
        return
    status_perf: dict[str, dict[str, float | int]] = {}
    for raw_name, raw_entry in raw_status_perf.items():
        if not isinstance(raw_entry, dict):
            continue
        name = str(raw_name or "").strip()
        if not name:
            continue
        count = int(raw_entry.get("count", 0) or 0)
        total_ms = float(raw_entry.get("total_ms", 0.0) or 0.0)
        max_ms = float(raw_entry.get("max_ms", 0.0) or 0.0)
        avg_ms = (total_ms / count) if count > 0 else 0.0
        status_perf[name] = {"count": count, "avg_ms": avg_ms, "max_ms": max_ms}
    if status_perf:
        metrics["status_perf_metrics"] = status_perf


def _append_live_state_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    headless_live_state_line_estimate: Callable[[Any], int],
) -> None:
    gview = getattr(app, "gview", None)
    if gview is not None:
        try:
            metrics["headless_live_state_line_estimate"] = (
                headless_live_state_line_estimate(gview)
            )
        except Exception:
            metrics["headless_live_state_line_estimate"] = 0
    metrics["live_gcode_past_count"] = int(getattr(app, "_live_gcode_past_count", 0) or 0)
    metrics["live_gcode_current_count"] = int(getattr(app, "_live_gcode_current_count", 0) or 0)
    metrics["live_gcode_next_count"] = int(getattr(app, "_live_gcode_next_count", 0) or 0)
    metrics["live_gcode_pending_depth"] = int(getattr(app, "_live_gcode_pending_depth", 0) or 0)
    metrics["live_gcode_last_acked_index"] = int(
        getattr(app, "_live_gcode_last_acked_index", -1) or -1
    )
    metrics["live_gcode_last_acked_byte_offset"] = int(
        getattr(app, "_live_gcode_last_acked_byte_offset", 0) or 0
    )
    metrics["last_stream_error_message"] = str(getattr(app, "_last_stream_error_message", "") or "")
    metrics["last_stream_error_file_name"] = str(
        getattr(app, "_last_stream_error_file_name", "") or ""
    )
    metrics["last_stream_error_line_index"] = int(
        getattr(app, "_last_stream_error_line_index", -1) or -1
    )
    metrics["last_stream_error_line_number"] = int(
        getattr(app, "_last_stream_error_line_number", 0) or 0
    )
    metrics["last_stream_error_line_text"] = str(
        getattr(app, "_last_stream_error_line_text", "") or ""
    )
    metrics["last_stream_error_hint"] = str(getattr(app, "_last_stream_error_hint", "") or "")


def _append_jog_metrics(app: Any, metrics: dict[str, Any]) -> None:
    jog_dro_trace = getattr(app, "_jog_dro_trace", None)
    if isinstance(jog_dro_trace, deque):
        trace_tail = list(jog_dro_trace)[-400:]
        metrics["jog_dro_trace_count"] = int(len(jog_dro_trace))
        metrics["jog_dro_trace_tail"] = trace_tail
    elif isinstance(jog_dro_trace, list):
        trace_tail = list(jog_dro_trace)[-400:]
        metrics["jog_dro_trace_count"] = int(len(jog_dro_trace))
        metrics["jog_dro_trace_tail"] = trace_tail
    else:
        metrics["jog_dro_trace_count"] = 0
    jog_interp_stats = getattr(app, "_jog_dro_interp_stats", None)
    if isinstance(jog_interp_stats, dict):
        metrics["jog_dro_interp_stats"] = dict(jog_interp_stats)
    jog_mode = "off"
    jog_mode_var = getattr(app, "jog_dro_smoothing_mode", None)
    if jog_mode_var is not None:
        try:
            jog_mode = str(jog_mode_var.get() or "").strip().lower() or "off"
        except Exception:
            jog_mode = "off"
    elif isinstance(getattr(app, "settings", None), dict):
        jog_mode = (
            str(getattr(app, "settings", {}).get("jog_dro_smoothing_mode", "off") or "")
            .strip()
            .lower()
            or "off"
        )
    metrics["jog_dro_interp_mode"] = jog_mode
    jog_state = getattr(app, "_manual_jog_predict_state", None)
    jog_source = ""
    if isinstance(jog_state, dict):
        jog_source = str(jog_state.get("source", "") or "").strip().lower()
    jog_source_is_joystick = jog_source.startswith("joystick") or jog_source.startswith("jog_hold")
    jog_mode_allows_source = bool(
        jog_mode == "all_jog" or (jog_mode == "ui_jog_only" and not jog_source_is_joystick)
    )
    metrics["jog_dro_interp_active"] = bool(jog_state and jog_mode_allows_source)


def _append_gcode_source_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    effective_line_cache_cap_lines: Callable[[Any], tuple[int, str]],
    bounded_ssmeta: Callable[[Any], dict[str, str]],
) -> None:
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip() or "none"
    gcode_source = getattr(app, "_gcode_source", None)
    source_path = str(getattr(gcode_source, "path", "") or "").strip()
    source_line_count_known = bool(getattr(app, "_gcode_source_line_count_known", False))
    if gcode_source is not None:
        try:
            checker = getattr(gcode_source, "line_count_known", None)
            if callable(checker):
                source_line_count_known = bool(checker())
            else:
                source_line_count_known = bool(
                    getattr(gcode_source, "_line_count_known", source_line_count_known)
                )
        except Exception:
            pass
    elif storage_mode != "in_memory":
        source_line_count_known = False
    metrics["gcode_storage_mode"] = storage_mode
    metrics["gcode_load_mode"] = str(getattr(app, "_gcode_load_mode", "") or "").strip()
    metrics["gcode_index_mode"] = str(getattr(app, "_gcode_index_mode", "") or "").strip()
    metrics["gcode_source_line_count_known"] = bool(source_line_count_known)
    time_to_ready = getattr(app, "_gcode_time_to_stream_ready_ms", None)
    time_to_popup = getattr(app, "_gcode_time_to_popup_close_ms", None)
    try:
        metrics["gcode_time_to_stream_ready_ms"] = (
            float(time_to_ready) if time_to_ready is not None else None
        )
    except (TypeError, ValueError):
        metrics["gcode_time_to_stream_ready_ms"] = None
    try:
        metrics["gcode_time_to_popup_close_ms"] = (
            float(time_to_popup) if time_to_popup is not None else None
        )
    except (TypeError, ValueError):
        metrics["gcode_time_to_popup_close_ms"] = None
    metrics["gcode_file_backed"] = bool(gcode_source is not None)
    cap_lines, cap_profile = effective_line_cache_cap_lines(app)
    metrics["gcode_line_cache_cap_lines"] = int(cap_lines)
    metrics["gcode_line_cache_cap_profile"] = cap_profile
    metrics["gcode_line_cache_cap_hit"] = bool(
        getattr(app, "_gcode_full_line_cache_cap_hit", False)
    )
    try:
        metrics["gcode_sample_line_cap"] = int(getattr(app, "_gcode_sample_line_cap", 0) or 0)
    except (TypeError, ValueError):
        metrics["gcode_sample_line_cap"] = 0
    try:
        retained_line_count = int(getattr(app, "_gcode_retained_line_count", 0) or 0)
    except (TypeError, ValueError):
        retained_line_count = 0
    if retained_line_count <= 0:
        retained = getattr(app, "_last_gcode_lines", None)
        try:
            retained_line_count = int(len(retained)) if retained is not None else 0
        except Exception:
            retained_line_count = 0
    metrics["gcode_retained_line_count"] = retained_line_count
    try:
        metrics["gcode_source_offset_count"] = int(
            getattr(app, "_gcode_source_offset_count", 0) or 0
        )
    except (TypeError, ValueError):
        metrics["gcode_source_offset_count"] = 0
    metrics["gcode_source_offset_type"] = str(getattr(app, "_gcode_source_offset_type", "") or "")
    metrics["gcode_offset_index_enabled"] = bool(
        getattr(app, "_gcode_offset_index_enabled", False)
    )
    metrics["gcode_prepare_sample_line_count"] = int(
        getattr(app, "_gcode_prepare_sample_line_count", 0) or 0
    )
    metrics["gcode_prepare_sample_head_lines"] = int(
        getattr(app, "_gcode_prepare_sample_head_lines", 0) or 0
    )
    metrics["gcode_prepare_sample_tail_lines"] = int(
        getattr(app, "_gcode_prepare_sample_tail_lines", 0) or 0
    )
    metrics["gcode_prepare_sample_interval_lines"] = int(
        getattr(app, "_gcode_prepare_sample_interval_lines", 0) or 0
    )
    metrics["gcode_prepare_sample_max_lines"] = int(
        getattr(app, "_gcode_prepare_sample_max_lines", 0) or 0
    )
    file_size_bytes = int(getattr(app, "_gcode_file_size_bytes", 0) or 0)
    if file_size_bytes <= 0 and source_path:
        try:
            if os.path.isfile(source_path):
                file_size_bytes = max(0, int(os.path.getsize(source_path)))
        except Exception:
            pass
    file_line_count = int(getattr(app, "_gcode_file_line_count", 0) or 0)
    if file_line_count <= 0 and gcode_source is not None:
        try:
            file_line_count = max(0, int(getattr(gcode_source, "_line_count", 0) or 0))
        except Exception:
            file_line_count = 0
    if file_line_count <= 0:
        try:
            file_line_count = max(0, int(getattr(app, "_gcode_total_lines", 0) or 0))
        except Exception:
            file_line_count = 0
    file_line_count_known = bool(getattr(app, "_gcode_file_line_count_known", False))
    if (not file_line_count_known) and source_line_count_known and file_line_count > 0:
        file_line_count_known = True
    if file_line_count <= 0:
        file_line_count_known = False
    executable_line_count = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    if executable_line_count <= 0:
        executable_line_count = int(
            getattr(app, "_gcode_prepare_executable_total_lines", 0) or 0
        )
    if executable_line_count <= 0:
        executable_line_count = int(getattr(app, "_gcode_total_lines", 0) or 0)
    executable_line_count_known = bool(getattr(app, "_gcode_executable_lines_known", False))
    if (not executable_line_count_known) and executable_line_count > 0:
        executable_line_count_known = bool(
            getattr(app, "_gcode_source_line_count_known", False)
        )
    motion_line_count = int(getattr(app, "_gcode_motion_lines", 0) or 0)
    if motion_line_count <= 0:
        motion_line_count = int(getattr(app, "_gcode_prepare_motion_total_lines", 0) or 0)
    motion_line_count_known = bool(getattr(app, "_gcode_motion_lines_known", False))
    if (not motion_line_count_known) and motion_line_count > 0:
        motion_line_count_known = bool(executable_line_count_known)
    total_line_count = int(file_line_count)
    total_line_count_known = bool(file_line_count_known)
    metrics["gcode_file_size_bytes"] = int(file_size_bytes)
    metrics["file_size_bytes"] = int(file_size_bytes)
    metrics["gcode_total_lines"] = int(total_line_count)
    metrics["gcode_total_lines_known"] = bool(total_line_count_known)
    metrics["gcode_total_lines_estimated"] = bool(
        total_line_count > 0 and (not total_line_count_known)
    )
    metrics["gcode_executable_lines"] = int(executable_line_count)
    metrics["gcode_executable_lines_known"] = bool(executable_line_count_known)
    metrics["gcode_executable_lines_estimated"] = bool(
        executable_line_count > 0 and (not executable_line_count_known)
    )
    metrics["gcode_motion_lines"] = int(motion_line_count)
    metrics["gcode_motion_lines_known"] = bool(motion_line_count_known)
    metrics["gcode_motion_lines_estimated"] = bool(
        motion_line_count > 0 and (not motion_line_count_known)
    )
    metrics["gcode_prepare_executable_total_lines"] = int(
        getattr(app, "_gcode_prepare_executable_total_lines", 0) or 0
    )
    metrics["gcode_prepare_motion_total_lines"] = int(
        getattr(app, "_gcode_prepare_motion_total_lines", 0) or 0
    )
    metrics["gcode_prepare_sampled_executable_lines"] = int(
        getattr(app, "_gcode_prepare_sampled_executable_lines", 0) or 0
    )
    metrics["gcode_prepare_sampled_motion_lines"] = int(
        getattr(app, "_gcode_prepare_sampled_motion_lines", 0) or 0
    )
    metrics["gcode_quick_scan_ms"] = float(getattr(app, "_gcode_quick_scan_ms", 0.0) or 0.0)
    metrics["quick_scan_ms"] = float(metrics["gcode_quick_scan_ms"])
    bounds_box = getattr(app, "_gcode_bounds_box", None)
    if isinstance(bounds_box, dict):
        metrics["gcode_bounds_box"] = dict(bounds_box)
        metrics["bounds_box"] = dict(bounds_box)
    metrics["gcode_bounds_confidence"] = str(getattr(app, "_gcode_bounds_confidence", "") or "")
    metrics["bounds_confidence"] = str(metrics["gcode_bounds_confidence"])
    estimated_job_time_sec = getattr(app, "_gcode_estimated_job_time_sec", None)
    try:
        metrics["estimated_job_time_sec"] = (
            int(estimated_job_time_sec) if estimated_job_time_sec is not None else None
        )
    except (TypeError, ValueError):
        metrics["estimated_job_time_sec"] = None
    raw_estimate_confidence = str(getattr(app, "_estimate_confidence", "") or "")
    metrics["estimate_confidence"] = _normalize_confidence_label(raw_estimate_confidence)
    raw_dimensions_confidence = str(
        getattr(app, "_gcode_dimensions_confidence", "")
        or getattr(app, "_gcode_bounds_confidence", "")
    )
    metrics["dimensions_confidence"] = _normalize_confidence_label(
        raw_dimensions_confidence
    )
    estimate_reasons = getattr(app, "_gcode_estimate_confidence_reasons", None)
    if isinstance(estimate_reasons, dict):
        metrics["estimate_confidence_reasons"] = {
            str(k): bool(v) for k, v in estimate_reasons.items()
        }
    dim_reasons = getattr(app, "_gcode_dimensions_confidence_reasons", None)
    if isinstance(dim_reasons, dict):
        metrics["dimensions_confidence_reasons"] = {
            str(k): bool(v) for k, v in dim_reasons.items()
        }
    metrics["ssmeta_present"] = bool(getattr(app, "_gcode_ssmeta_present", False))
    metrics["gcode_ssmeta"] = bounded_ssmeta(getattr(app, "_gcode_ssmeta", None))
    metrics["dimensions_source"] = str(getattr(app, "_gcode_dimensions_source", "scan") or "scan")
    metrics["units_source"] = str(getattr(app, "_gcode_units_source", "scan") or "scan")
    metrics["ssmeta_scan_reduced"] = bool(getattr(app, "_gcode_ssmeta_scan_reduced", False))
    metrics["post_popup_background_tasks"] = "none"
    metrics["time_to_popup_close_ms"] = metrics.get("gcode_time_to_popup_close_ms")
    metrics["time_to_stream_ready_ms"] = metrics.get("gcode_time_to_stream_ready_ms")
    metrics["gcode_stats_compute_mode"] = str(getattr(app, "_gcode_stats_compute_mode", "") or "")
    metrics["gcode_stats_sample_scale"] = float(
        getattr(app, "_gcode_stats_sample_scale", 1.0) or 1.0
    )
    metrics["gcode_stats_sample_line_count"] = int(
        getattr(app, "_gcode_stats_sample_line_count", 0) or 0
    )
    metrics["gcode_stats_sample_total_lines"] = int(
        getattr(app, "_gcode_stats_sample_total_lines", 0) or 0
    )
    metrics["gcode_stats_sample_executable_lines"] = int(
        getattr(app, "_gcode_stats_sample_executable_lines", 0) or 0
    )
    metrics["gcode_stats_sample_motion_lines"] = int(
        getattr(app, "_gcode_stats_sample_motion_lines", 0) or 0
    )
    metrics["gcode_stats_executable_total_lines"] = int(
        getattr(app, "_gcode_stats_executable_total_lines", 0) or 0
    )
    metrics["gcode_stats_motion_total_lines"] = int(
        getattr(app, "_gcode_stats_motion_total_lines", 0) or 0
    )
    metrics["gcode_stats_chunk_max_ms"] = float(
        getattr(app, "_gcode_stats_chunk_max_ms", 0.0) or 0.0
    )
    metrics["gcode_stats_chunk_max_section"] = str(
        getattr(app, "_gcode_stats_chunk_max_section", "") or "unknown"
    )
    metrics["gcode_stats_chunk_yield_count"] = int(
        getattr(app, "_gcode_stats_chunk_yield_count", 0) or 0
    )


def _append_stream_progress_metrics(
    app: Any,
    metrics: dict[str, Any],
    *,
    deferred_completion_wait_snapshot: Callable[..., tuple[bool, float, float, float, int]],
) -> None:
    file_size_bytes = int(metrics.get("gcode_file_size_bytes", 0) or 0)
    stream_file_size_bytes = int(
        getattr(app, "_stream_progress_file_size_bytes", metrics.get("stream_file_size_bytes", 0))
        or 0
    )
    if stream_file_size_bytes <= 0:
        stream_file_size_bytes = int(file_size_bytes)
    acked_byte_offset = int(
        getattr(app, "_stream_acked_byte_offset", metrics.get("acked_byte_offset", 0)) or 0
    )
    if stream_file_size_bytes > 0:
        acked_byte_offset = min(max(0, acked_byte_offset), stream_file_size_bytes)
    else:
        acked_byte_offset = max(0, acked_byte_offset)
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    stream_done_pending_idle = bool(getattr(app, "_stream_done_pending_idle", False))
    (
        stream_done_wait_active,
        stream_done_wait_current_s,
        stream_done_wait_last_s,
        stream_done_wait_total_s,
        stream_done_wait_count,
    ) = deferred_completion_wait_snapshot(app, now_ts=time.time())
    if (
        stream_state == "done"
        and not stream_done_pending_idle
        and stream_file_size_bytes > 0
    ):
        acked_byte_offset = int(stream_file_size_bytes)
    stream_progress_pct = float(
        getattr(app, "_stream_progress_pct", metrics.get("stream_progress_pct", 0.0)) or 0.0
    )
    if stream_file_size_bytes > 0:
        stream_progress_pct = max(
            0.0,
            min(
                100.0,
                (float(acked_byte_offset) / float(stream_file_size_bytes)) * 100.0,
            ),
        )
        if stream_state == "done" and not stream_done_pending_idle:
            stream_progress_pct = 100.0
    else:
        stream_progress_pct = max(0.0, min(100.0, stream_progress_pct))
    metrics["stream_progress_pct"] = float(stream_progress_pct)
    metrics["acked_byte_offset"] = int(acked_byte_offset)
    metrics["stream_file_size_bytes"] = int(stream_file_size_bytes)
    metrics["stream_done_pending_idle"] = bool(stream_done_pending_idle)
    metrics["stream_done_wait_active"] = bool(stream_done_wait_active)
    metrics["stream_done_wait_current_s"] = float(stream_done_wait_current_s)
    metrics["stream_done_wait_last_s"] = float(stream_done_wait_last_s)
    metrics["stream_done_wait_total_s"] = float(stream_done_wait_total_s)
    metrics["stream_done_wait_count"] = int(stream_done_wait_count)
    metrics["stream_completion_verified_eof"] = bool(
        getattr(app, "_stream_completion_verified_eof", False)
    )
    metrics["stream_completion_total_lines"] = int(
        getattr(app, "_stream_completion_total_lines", 0) or 0
    )
    metrics["stream_completion_total_lines_known"] = bool(
        getattr(app, "_stream_completion_total_lines_known", False)
    )
    raw_completion_acked_index = getattr(app, "_stream_completion_last_acked_index", -1)
    try:
        completion_acked_index = int(raw_completion_acked_index)
    except Exception:
        completion_acked_index = -1
    metrics["stream_completion_last_acked_index"] = int(completion_acked_index)
    metrics["stream_completion_last_acked_line"] = max(
        0,
        int(completion_acked_index) + 1,
    )
    raw_completion_send_index = getattr(app, "_stream_completion_send_index", -1)
    try:
        completion_send_index = int(raw_completion_send_index)
    except Exception:
        completion_send_index = -1
    metrics["stream_completion_send_index"] = int(completion_send_index)
    metrics["stream_completion_evidence_authoritative"] = bool(
        getattr(app, "_stream_completion_evidence_authoritative", False)
    )
    metrics["stream_completion_shortfall_lines"] = int(
        getattr(app, "_stream_completion_shortfall_lines", 0) or 0
    )
    metrics["stream_completion_warning"] = str(
        getattr(app, "_stream_completion_warning", "") or ""
    )
    metrics["file_size_bytes"] = int(
        stream_file_size_bytes if stream_file_size_bytes > 0 else file_size_bytes
    )


def _append_estimator_and_auto_level_metrics(app: Any, metrics: dict[str, Any]) -> None:
    loaded_total_min = getattr(app, "_loaded_estimate_total_min", None)
    try:
        metrics["estimate_loaded_total_min"] = (
            float(loaded_total_min) if loaded_total_min is not None else None
        )
    except (TypeError, ValueError):
        metrics["estimate_loaded_total_min"] = None
    metrics["estimate_loaded_source"] = str(getattr(app, "_loaded_estimate_source", "") or "")
    observed_total_min = getattr(app, "_live_estimate_observed_total_min", None)
    try:
        metrics["estimate_live_observed_total_min"] = (
            float(observed_total_min) if observed_total_min is not None else None
        )
    except (TypeError, ValueError):
        metrics["estimate_live_observed_total_min"] = None
    metrics["estimate_rate_source"] = str(getattr(app, "_rapid_rates_source", "") or "")
    rapid_rates = getattr(app, "_rapid_rates", None)
    if isinstance(rapid_rates, tuple) and len(rapid_rates) == 3:
        try:
            metrics["estimate_rapid_rates_mm_min"] = [
                float(rapid_rates[0]),
                float(rapid_rates[1]),
                float(rapid_rates[2]),
            ]
        except Exception:
            pass
    accel_rates = getattr(app, "_accel_rates", None)
    if isinstance(accel_rates, tuple) and len(accel_rates) == 3:
        try:
            metrics["estimate_accel_rates_mm_s2"] = [
                float(accel_rates[0]),
                float(accel_rates[1]),
                float(accel_rates[2]),
            ]
        except Exception:
            pass
    estimate_inputs = getattr(app, "_estimate_inputs_snapshot", None)
    if isinstance(estimate_inputs, dict):
        metrics["estimate_inputs_snapshot"] = dict(estimate_inputs)
    al_source_path = str(getattr(app, "_auto_level_job_source_path", "") or "").strip()
    metrics["auto_level_source_path"] = al_source_path
    metrics["auto_level_source_exists"] = bool(
        al_source_path and os.path.isfile(al_source_path)
    )
    metrics["auto_level_source_hash"] = str(getattr(app, "_auto_level_job_hash", "") or "")
    metrics["auto_level_source_total_lines"] = int(
        getattr(app, "_auto_level_job_total_lines", 0) or 0
    )
    prereq_snapshot = getattr(app, "_auto_level_prereq_snapshot", None)
    if isinstance(prereq_snapshot, dict):
        metrics["auto_level_prereq_snapshot"] = dict(prereq_snapshot)
        metrics["auto_level_prereq_ready"] = bool(prereq_snapshot.get("bounds_ready", False))
        metrics["auto_level_prereq_stage"] = str(prereq_snapshot.get("stage", "") or "")


def _append_stream_buffer_metrics(grbl: Any, metrics: dict[str, Any]) -> None:
    stream_queue_depth = None
    stream_resume_depth = None
    stream_pending_item = None
    stream_buf_used = None
    if grbl is not None:
        try:
            line_queue = getattr(grbl, "_stream_line_queue", None)
            stream_queue_depth = int(len(line_queue)) if line_queue is not None else None
        except Exception:
            stream_queue_depth = None
        try:
            resume_preamble = getattr(grbl, "_resume_preamble", None)
            stream_resume_depth = int(len(resume_preamble)) if resume_preamble is not None else None
        except Exception:
            stream_resume_depth = None
        try:
            stream_pending_item = bool(getattr(grbl, "_stream_pending_item", None) is not None)
        except Exception:
            stream_pending_item = None
        try:
            stream_buf_used = int(getattr(grbl, "_stream_buf_used", 0) or 0)
        except Exception:
            stream_buf_used = None
    if stream_queue_depth is not None:
        metrics["stream_outstanding_queue_depth"] = stream_queue_depth
    if stream_resume_depth is not None:
        metrics["stream_resume_preamble_depth"] = stream_resume_depth
    if stream_pending_item is not None:
        metrics["stream_pending_item"] = bool(stream_pending_item)
    if stream_buf_used is not None:
        metrics["stream_buf_used_bytes"] = stream_buf_used


def build_runtime_metrics(
    app: Any,
    *,
    collect_build_info: Callable[[Any], dict[str, Any]],
    kasa_status_snapshot: Callable[[Any], dict[str, Any]],
    format_kasa_status_line: Callable[[Any], str],
    log_suppressed: Callable[[str, BaseException], None],
    headless_live_state_line_estimate: Callable[[Any], int],
    effective_line_cache_cap_lines: Callable[[Any], tuple[int, str]],
    deferred_completion_wait_snapshot: Callable[..., tuple[bool, float, float, float, int]],
    bounded_ssmeta: Callable[[Any], dict[str, str]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    metrics["build_info"] = collect_build_info(app)
    configured_poll = _safe_float_var_get(app, "status_poll_interval")
    if configured_poll > 0.0:
        metrics["status_poll_interval_configured_s"] = float(configured_poll)
    _append_kasa_metrics(
        app,
        metrics,
        kasa_status_snapshot=kasa_status_snapshot,
        format_kasa_status_line=format_kasa_status_line,
        log_suppressed=log_suppressed,
    )
    grbl = _append_grbl_runtime_metrics(app, metrics, log_suppressed=log_suppressed)
    _append_perf_monitor_metrics(app, metrics, log_suppressed=log_suppressed)
    _append_status_perf_metrics(app, metrics)
    _append_live_state_metrics(
        app,
        metrics,
        headless_live_state_line_estimate=headless_live_state_line_estimate,
    )
    _append_jog_metrics(app, metrics)
    _append_gcode_source_metrics(
        app,
        metrics,
        effective_line_cache_cap_lines=effective_line_cache_cap_lines,
        bounded_ssmeta=bounded_ssmeta,
    )
    _append_stream_progress_metrics(
        app,
        metrics,
        deferred_completion_wait_snapshot=deferred_completion_wait_snapshot,
    )
    _append_estimator_and_auto_level_metrics(app, metrics)
    _append_stream_buffer_metrics(grbl, metrics)
    return metrics
