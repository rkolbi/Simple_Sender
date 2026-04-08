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

"""Diagnostics session text helpers."""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime
from typing import Any, Callable


def build_session_diagnostics_lines(
    app: Any,
    *,
    format_kasa_status_line: Callable[[Any], str],
    log_suppressed: Callable[[str, BaseException], None],
    effective_line_cache_cap_lines: Callable[[Any], tuple[int, str]],
    headless_live_state_line_estimate: Callable[[Any], int],
    format_validation_summary: Callable[[Any], list[str]],
    runtime_metrics: Callable[[Any], dict[str, Any]],
    format_runtime_metrics: Callable[..., list[str]],
) -> list[str]:
    lines: list[str] = []
    lines.append("Simple Sender diagnostics")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    version_text = ""
    try:
        version_text = app.version_var.get()
    except Exception:
        version_text = ""
    if version_text:
        lines.append(f"Version: {version_text}")
    lines.append(f"Connected: {getattr(app, 'connected', False)}")
    lines.append(f"Port: {getattr(app, '_connected_port', '')}")
    lines.append(f"Streaming: {getattr(app, '_stream_state', '')}")
    lines.append(f"G-code path: {getattr(app, '_last_gcode_path', '')}")
    last_stream_error_message = str(
        getattr(app, "_last_stream_error_message", "") or ""
    ).strip()
    if last_stream_error_message:
        last_stream_error_file = str(
            getattr(app, "_last_stream_error_file_name", "") or ""
        ).strip()
        last_stream_error_line = int(
            getattr(app, "_last_stream_error_line_number", 0) or 0
        )
        last_stream_error_line_text = str(
            getattr(app, "_last_stream_error_line_text", "") or ""
        ).strip()
        lines.append("Last stream error: " + last_stream_error_message)
        if last_stream_error_line > 0:
            location = (
                f"{last_stream_error_file} line {last_stream_error_line}"
                if last_stream_error_file
                else f"line {last_stream_error_line}"
            )
            lines.append(f"Last stream error location: {location}")
        if last_stream_error_line_text:
            lines.append(f"Last stream error line text: {last_stream_error_line_text}")
        last_stream_error_hint = str(
            getattr(app, "_last_stream_error_hint", "") or ""
        ).strip()
        if last_stream_error_hint:
            lines.append(f"Last stream error hint: {last_stream_error_hint}")
    jog_interp_stats = getattr(app, "_jog_dro_interp_stats", None)
    if isinstance(jog_interp_stats, dict):
        sync_count = int(jog_interp_stats.get("status_sync_count", 0) or 0)
        jog_trace = getattr(app, "_jog_dro_trace", None)
        jog_mode = "off"
        jog_mode_var = getattr(app, "jog_dro_smoothing_mode", None)
        if jog_mode_var is not None and hasattr(jog_mode_var, "get"):
            try:
                jog_mode = str(jog_mode_var.get() or "").strip().lower() or "off"
            except Exception:
                jog_mode = "off"
        elif isinstance(getattr(app, "settings", None), dict):
            jog_mode = (
                str(
                    getattr(app, "settings", {}).get(
                        "jog_dro_smoothing_mode", "off"
                    )
                    or ""
                ).strip().lower()
                or "off"
            )
        jog_state = getattr(app, "_manual_jog_predict_state", None)
        jog_source = ""
        if isinstance(jog_state, dict):
            jog_source = str(jog_state.get("source", "") or "").strip().lower()
        jog_source_is_joystick = jog_source.startswith(
            "joystick"
        ) or jog_source.startswith("jog_hold")
        jog_mode_allows_source = bool(
            jog_mode == "all_jog"
            or (jog_mode == "ui_jog_only" and not jog_source_is_joystick)
        )
        try:
            if isinstance(jog_trace, (deque, list)):
                jog_trace_count = int(len(jog_trace))
            else:
                jog_trace_count = 0
        except Exception:
            jog_trace_count = 0
        lines.append(
            "Jog DRO interpolation: "
            f"mode={jog_mode}, "
            f"active={bool(jog_state and jog_mode_allows_source)}, "
            f"samples={jog_trace_count}, "
            f"status_sync={sync_count}"
        )
        if sync_count > 0:
            lines.append(
                "Jog DRO delta abs (report units): "
                f"avg=({float(jog_interp_stats.get('delta_abs_avg_x', 0.0) or 0.0):.4f}, "
                f"{float(jog_interp_stats.get('delta_abs_avg_y', 0.0) or 0.0):.4f}, "
                f"{float(jog_interp_stats.get('delta_abs_avg_z', 0.0) or 0.0):.4f}), "
                f"max=({float(jog_interp_stats.get('delta_abs_max_x', 0.0) or 0.0):.4f}, "
                f"{float(jog_interp_stats.get('delta_abs_max_y', 0.0) or 0.0):.4f}, "
                f"{float(jog_interp_stats.get('delta_abs_max_z', 0.0) or 0.0):.4f})"
            )
            lines.append(
                "Jog DRO sync cadence (s): "
                f"avg={float(jog_interp_stats.get('status_sync_interval_avg_s', 0.0) or 0.0):.3f}, "
                f"max={float(jog_interp_stats.get('status_sync_interval_max_s', 0.0) or 0.0):.3f}; "
                "prediction horizon (s): "
                f"avg={float(jog_interp_stats.get('predict_horizon_avg_s', 0.0) or 0.0):.3f}, "
                f"max={float(jog_interp_stats.get('predict_horizon_max_s', 0.0) or 0.0):.3f}"
            )
    try:
        lines.append(f"Kasa status: {format_kasa_status_line(app)}")
    except Exception as exc:
        log_suppressed("Failed appending Kasa status line to session diagnostics", exc)
    auto_level_source_path = str(
        getattr(app, "_auto_level_job_source_path", "") or ""
    ).strip()
    auto_level_source_hash = str(getattr(app, "_auto_level_job_hash", "") or "").strip()
    auto_level_source_total = int(getattr(app, "_auto_level_job_total_lines", 0) or 0)
    auto_level_source_exists = bool(
        auto_level_source_path and os.path.isfile(auto_level_source_path)
    )
    lines.append(
        "Auto-level source: "
        f"exists={auto_level_source_exists}, lines={auto_level_source_total:,}, "
        f"hash={auto_level_source_hash or 'n/a'}, path={auto_level_source_path or 'n/a'}"
    )
    prereq_snapshot = getattr(app, "_auto_level_prereq_snapshot", None)
    if isinstance(prereq_snapshot, dict):
        stage = str(prereq_snapshot.get("stage", "") or "n/a")
        bounds_ready = bool(prereq_snapshot.get("bounds_ready", False))
        width_mm = float(prereq_snapshot.get("xy_width_mm", 0.0) or 0.0)
        height_mm = float(prereq_snapshot.get("xy_height_mm", 0.0) or 0.0)
        lines.append(
            "Auto-level prereq: "
            f"stage={stage}, bounds_ready={bounds_ready}, "
            f"size_mm={width_mm:.3f}x{height_mm:.3f}, "
            f"grid_applicable={bool(prereq_snapshot.get('probe_grid_applicable', False))}"
        )
    lines.append(
        f"G-code streaming mode: {getattr(app, '_gcode_streaming_mode', False)}"
    )
    total_lines = int(getattr(app, "_gcode_file_line_count", 0) or 0)
    total_known = bool(getattr(app, "_gcode_file_line_count_known", False))
    exec_lines = int(
        getattr(app, "_gcode_executable_lines", 0)
        or getattr(app, "_gcode_prepare_executable_total_lines", 0)
        or getattr(app, "_gcode_total_lines", 0)
        or 0
    )
    exec_known = bool(getattr(app, "_gcode_executable_lines_known", False))
    motion_lines = int(
        getattr(app, "_gcode_motion_lines", 0)
        or getattr(app, "_gcode_prepare_motion_total_lines", 0)
        or 0
    )
    motion_known = bool(getattr(app, "_gcode_motion_lines_known", False))
    lines.append(
        "G-code line counts: "
        f"total={total_lines:,} ({'known' if total_known else 'estimated'}), "
        f"executable={exec_lines:,} ({'known' if exec_known else 'estimated'}), "
        f"motion={motion_lines:,} ({'known' if motion_known else 'estimated'})"
    )
    lines.append(
        "SSMETA: "
        f"{'present' if bool(getattr(app, '_gcode_ssmeta_present', False)) else 'not_found'}, "
        f"dimensions_source={str(getattr(app, '_gcode_dimensions_source', 'scan') or 'scan')}, "
        f"units_source={str(getattr(app, '_gcode_units_source', 'scan') or 'scan')}, "
        f"scan_reduced={bool(getattr(app, '_gcode_ssmeta_scan_reduced', False))}"
    )
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip() or "none"
    load_mode = str(getattr(app, "_gcode_load_mode", "") or "").strip() or "n/a"
    index_mode = str(getattr(app, "_gcode_index_mode", "") or "").strip() or "n/a"
    line_count_known = bool(getattr(app, "_gcode_source_line_count_known", True))
    time_to_ready_ms = getattr(app, "_gcode_time_to_stream_ready_ms", None)
    time_to_popup_ms = getattr(app, "_gcode_time_to_popup_close_ms", None)
    cap_lines, cap_profile = effective_line_cache_cap_lines(app)
    cap_hit = bool(getattr(app, "_gcode_full_line_cache_cap_hit", False))
    sample_cap = int(getattr(app, "_gcode_sample_line_cap", 0) or 0)
    retained_lines = int(getattr(app, "_gcode_retained_line_count", 0) or 0)
    if retained_lines <= 0:
        retained = getattr(app, "_last_gcode_lines", None)
        try:
            retained_lines = int(len(retained)) if retained is not None else 0
        except Exception:
            retained_lines = 0
    live_state_lines = headless_live_state_line_estimate(getattr(app, "gview", None))
    storage_detail = (
        f"{storage_mode}, load_mode={load_mode}, index_mode={index_mode}, "
        f"line_count_known={line_count_known}"
    )
    try:
        if time_to_ready_ms is not None:
            storage_detail += f", time_to_stream_ready_ms={float(time_to_ready_ms):.2f}"
    except (TypeError, ValueError):
        pass
    try:
        if time_to_popup_ms is not None:
            storage_detail += f", time_to_popup_close_ms={float(time_to_popup_ms):.2f}"
    except (TypeError, ValueError):
        pass
    lines.append("G-code storage mode: " + storage_detail)
    lines.append(
        "G-code line-cache cap: "
        f"{int(cap_lines):,} ({cap_profile}), cap_hit={cap_hit}, sample_cap={sample_cap:,}"
    )
    lines.append(
        "G-code retained lines/headless state estimate: "
        f"{retained_lines:,}/{live_state_lines:,}"
    )
    source_offsets = int(getattr(app, "_gcode_source_offset_count", 0) or 0)
    source_offset_type = str(
        getattr(app, "_gcode_source_offset_type", "") or ""
    ).strip()
    source_index_enabled = bool(getattr(app, "_gcode_offset_index_enabled", False))
    lines.append(
        "G-code source offsets: "
        + (
            f"{source_offsets:,} ({source_offset_type})"
            if source_offset_type
            else f"{source_offsets:,}"
        )
    )
    lines.append(f"G-code source offset index enabled: {source_index_enabled}")
    sample_lines = int(getattr(app, "_gcode_prepare_sample_line_count", 0) or 0)
    sample_head = int(getattr(app, "_gcode_prepare_sample_head_lines", 0) or 0)
    sample_tail = int(getattr(app, "_gcode_prepare_sample_tail_lines", 0) or 0)
    sample_interval = int(
        getattr(app, "_gcode_prepare_sample_interval_lines", 0) or 0
    )
    sample_max = int(getattr(app, "_gcode_prepare_sample_max_lines", 0) or 0)
    prep_exec_total = int(
        getattr(app, "_gcode_prepare_executable_total_lines", 0) or 0
    )
    prep_motion_total = int(
        getattr(app, "_gcode_prepare_motion_total_lines", 0) or 0
    )
    prep_sample_exec = int(
        getattr(app, "_gcode_prepare_sampled_executable_lines", 0) or 0
    )
    prep_sample_motion = int(
        getattr(app, "_gcode_prepare_sampled_motion_lines", 0) or 0
    )
    stats_mode = (
        str(getattr(app, "_gcode_stats_compute_mode", "") or "").strip() or "n/a"
    )
    stats_scale = float(getattr(app, "_gcode_stats_sample_scale", 1.0) or 1.0)
    stats_sample_lines = int(getattr(app, "_gcode_stats_sample_line_count", 0) or 0)
    stats_sample_total = int(getattr(app, "_gcode_stats_sample_total_lines", 0) or 0)
    stats_sample_executable = int(
        getattr(app, "_gcode_stats_sample_executable_lines", 0) or 0
    )
    stats_sample_motion = int(
        getattr(app, "_gcode_stats_sample_motion_lines", 0) or 0
    )
    stats_total_executable = int(
        getattr(app, "_gcode_stats_executable_total_lines", 0) or 0
    )
    stats_total_motion = int(getattr(app, "_gcode_stats_motion_total_lines", 0) or 0)
    stats_chunk_max_ms = float(getattr(app, "_gcode_stats_chunk_max_ms", 0.0) or 0.0)
    stats_chunk_max_section = str(
        getattr(app, "_gcode_stats_chunk_max_section", "") or "unknown"
    )
    stats_chunk_yields = int(getattr(app, "_gcode_stats_chunk_yield_count", 0) or 0)
    lines.append(
        "G-code prepare sample policy: "
        f"sample_lines={sample_lines:,}, head={sample_head:,}, tail={sample_tail:,}, "
        f"interval={sample_interval:,}, max={sample_max:,}"
    )
    lines.append(
        "G-code prepare sampled counts: "
        f"sample_exec={prep_sample_exec:,}, sample_motion={prep_sample_motion:,}, "
        f"total_exec={prep_exec_total:,}, total_motion={prep_motion_total:,}"
    )
    lines.append(
        "G-code prepare sampled modes: "
        f"stats={stats_mode}, "
        f"stats_scale={stats_scale:.2f}, stats_sample={stats_sample_lines:,}/{stats_sample_total:,}"
    )
    lines.append(
        "Estimator sample coverage: "
        f"sample_exec={stats_sample_executable:,}, sample_motion={stats_sample_motion:,}, "
        f"total_exec={stats_total_executable:,}, total_motion={stats_total_motion:,}"
    )
    lines.append(
        "G-code stats cooperative chunking: "
        f"chunk_max_ms={stats_chunk_max_ms:.2f}, chunk_max_section={stats_chunk_max_section}, yields={stats_chunk_yields:,}"
    )
    lines.append(
        "Post-load background tasks: "
        f"{str(getattr(app, '_gcode_post_popup_background_tasks', 'none') or 'none')}"
    )
    lines.append(
        f"Estimator confidence: {str(getattr(app, '_estimate_confidence', 'provisional') or 'provisional')}"
    )
    lines.append("")
    report = getattr(app, "_gcode_validation_report", None)
    report_summary = format_validation_summary(report)
    if report_summary:
        lines.append("Validation summary:")
        lines.extend(f"- {item}" for item in report_summary)
        lines.append("")
    metrics = runtime_metrics(app)
    if metrics:
        lines.append("Runtime telemetry:")
        lines.extend(
            format_runtime_metrics(metrics, include_samples=True, sample_limit=20)
        )
        lines.append("")
    last_status = getattr(app, "_last_status_raw", "")
    if last_status:
        lines.append("Last status:")
        lines.append(last_status.strip())
        lines.append("")
    history = getattr(app, "_status_history", [])
    history_entries = list(history) if history else []
    if history_entries:
        lines.append("Recent status history:")
        for ts, raw in history_entries[-50:]:
            stamp = datetime.fromtimestamp(ts).isoformat(timespec="seconds")
            lines.append(f"{stamp} {raw.strip()}")
        lines.append("")
    connection_history = getattr(app, "_connection_timeline", [])
    connection_entries = list(connection_history) if connection_history else []
    if connection_entries:
        lines.append("Connection timeline:")
        for entry in connection_entries[-80:]:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts")
            try:
                if isinstance(ts, (int, float)):
                    stamp_ts = float(ts)
                elif isinstance(ts, (str, bytes, bytearray)):
                    stamp_ts = float(ts)
                else:
                    raise TypeError("unsupported timestamp type")
                stamp = datetime.fromtimestamp(stamp_ts).isoformat(timespec="seconds")
            except Exception:
                stamp = str(ts or "n/a")
            event = str(entry.get("event", "") or "unknown")
            details = str(entry.get("details", "") or "")
            if details:
                lines.append(f"{stamp} {event} | {details}")
            else:
                lines.append(f"{stamp} {event}")
        lines.append("")
    console_lines = []
    try:
        console_lines = app.streaming_controller.get_console_lines()
    except Exception:
        console_lines = []
    if console_lines:
        lines.append("Recent console log:")
        for entry, tag in console_lines[-200:]:
            tag_text = f"[{tag}] " if tag else ""
            lines.append(f"{tag_text}{entry}")
        lines.append("")
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        lines.append("Settings:")
        lines.append(json.dumps(settings, indent=2, sort_keys=True))
        lines.append("")
    return lines
