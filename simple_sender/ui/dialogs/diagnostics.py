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
import json
import os
import platform
import sys
import threading
import zipfile
from collections import deque
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any, cast

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.kasa_actions import format_kasa_status_line, kasa_status_snapshot
from simple_sender.ui.macro_files import discover_macro_assets
from simple_sender.ui.pi_profile import PI_PROFILE_STATUS_POLL_INTERVAL
from simple_sender.utils.constants import (
    GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT,
    GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER,
)
from simple_sender.utils.logging_config import get_log_dir
from .popup_utils import center_window

CHECKLIST_ITEMS = [
    "Connect/disconnect: port list refreshes, status shows connected, $G and $$ populate settings.",
    "Units: modal units match controller; $13 reporting indicator updates; unit toggle locked while streaming.",
    "Load G-code: file name, size, estimates, and bounds render in the correct units.",
    "Streaming: start/pause/resume/stop behaves correctly; buffer fill and progress update smoothly.",
    "Completion: popup shows run stats; progress bar resets after acknowledgment.",
    "Overrides: feed/spindle sliders send real-time commands and update the UI.",
    "Jogging: on-screen jog works; jog cancel halts motion; joystick hold stops on release.",
    "Safety: joystick safety hold gates actions; blocked actions emit status/log text.",
    "Alarms: alarm/lock messages display; unlock and recovery actions behave as expected.",
]
RUN_CHECKLIST_ITEMS = [
    "Confirm emergency stop and limit switches are functional.",
    "Home the machine and verify travel direction/limits.",
    "Set WCS zero and confirm units (G20/G21) match expectations.",
    "Verify tool, clamp clearance, and safe Z height.",
    "Dry-run in air if the job is new or the setup changed.",
]
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
PREFLIGHT_DECISION_HISTORY_LIMIT = 100
RUNTIME_TELEMETRY_REFRESH_MS = 1000
PERF_TEST_STATUS_POLL_INTERVAL = max(1.0, float(PI_PROFILE_STATUS_POLL_INTERVAL))
DIAG_BUNDLE_LOG_MAX_FILES = 12
DIAG_BUNDLE_LOG_TAIL_MAX_BYTES = 512_000


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _set_var_value(app: Any, attr_name: str, value: Any) -> None:
    var = getattr(app, attr_name, None)
    setter = getattr(var, "set", None)
    if not callable(setter):
        return
    try:
        setter(value)
    except Exception as exc:
        _log_suppressed(f"Failed setting {attr_name}", exc)


def _json_dump(obj: Any) -> str:
    def _default(value: Any):
        return str(value)

    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=_default)
    except Exception as exc:
        _log_suppressed("Failed serializing diagnostics JSON payload", exc)
        return "{}"


def _build_system_info_text(app: Any) -> str:
    lines: list[str] = []
    lines.append("Simple Sender system snapshot")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    version_text = str(version_getter() if callable(version_getter) else "").strip()
    if version_text:
        lines.append(f"App version: {version_text}")
    lines.append(f"Python: {sys.version.splitlines()[0] if sys.version else 'n/a'}")
    lines.append(f"Executable: {sys.executable}")
    lines.append(f"Platform: {platform.platform()}")
    lines.append(f"Machine: {platform.machine()}")
    lines.append(f"Processor: {platform.processor()}")
    lines.append(f"PID: {os.getpid()}")
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if cwd:
        lines.append(f"CWD: {cwd}")
    env = os.environ
    for key in ("USER", "LOGNAME", "HOME", "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
        value = str(env.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def _resolved_settings_path(app: Any) -> Path | None:
    raw_path = str(getattr(app, "settings_path", "") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    return path


def _safe_job_hash(app: Any) -> str:
    raw_hash = (
        getattr(app, "_gcode_hash", None)
        or getattr(app, "_last_parse_hash", None)
        or ""
    )
    return str(raw_hash).strip()


def _format_mb(value_bytes: Any) -> str:
    try:
        if value_bytes is None:
            return "n/a"
        return f"{(float(value_bytes) / (1024.0 * 1024.0)):.2f} MB"
    except (TypeError, ValueError):
        return "n/a"


def _pi_profile_enabled(app: Any) -> bool:
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


def _effective_line_cache_cap_lines(app: Any) -> tuple[int, str]:
    stored = getattr(app, "_gcode_full_line_cache_cap_lines", None)
    if stored is not None:
        try:
            cap = max(0, int(stored))
            profile = str(
                getattr(app, "_gcode_full_line_cache_profile", "") or ""
            ).strip()
            if profile:
                return cap, profile
        except Exception:
            pass
    if _pi_profile_enabled(app):
        return int(GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER), "pi"
    return int(GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT), "default"


def _viewer_window_line_count(gview: Any) -> int:
    if gview is None:
        return 0
    try:
        if bool(getattr(gview, "_virtual_enabled", False)):
            start = int(getattr(gview, "_virtual_window_start", 0) or 0)
            end = int(getattr(gview, "_virtual_window_end", 0) or 0)
            return max(0, end - start)
    except Exception:
        return 0
    try:
        return int(getattr(gview, "lines_count", 0) or 0)
    except Exception:
        return 0


def _runtime_metrics(app: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    try:
        kasa_snapshot = kasa_status_snapshot(app)
    except Exception as exc:
        _log_suppressed("Failed collecting Kasa status snapshot for diagnostics", exc)
        kasa_snapshot = {}
    if isinstance(kasa_snapshot, dict) and kasa_snapshot:
        metrics["kasa_status"] = dict(kasa_snapshot)
    try:
        kasa_line = str(format_kasa_status_line(app) or "").strip()
    except Exception as exc:
        _log_suppressed("Failed building Kasa status line for diagnostics", exc)
        kasa_line = ""
    if kasa_line:
        metrics["kasa_status_line"] = kasa_line
    grbl = getattr(app, "grbl", None)
    getter = getattr(grbl, "get_runtime_metrics", None) if grbl is not None else None
    if callable(getter):
        try:
            raw = getter()
        except Exception as exc:
            _log_suppressed("Failed collecting runtime telemetry metrics", exc)
            raw = {}
        if isinstance(raw, dict):
            metrics.update(cast(dict[str, Any], raw))
    perf_monitor = getattr(app, "_perf_monitor", None)
    metrics["perf_available"] = False
    if perf_monitor is None:
        metrics["perf_phase_sampling_note"] = (
            "Runtime performance profiling is disabled. "
            "Enable diagnostics performance profiling and restart before capture."
        )
    else:
        snapshot_getter = getattr(perf_monitor, "runtime_snapshot", None)
        if not callable(snapshot_getter):
            metrics["perf_phase_sampling_note"] = (
                "Runtime performance profiling monitor is unavailable."
            )
        else:
            try:
                snapshot = snapshot_getter()
            except Exception as exc:
                _log_suppressed(
                    "Failed collecting performance-monitor runtime snapshot", exc
                )
                snapshot = None
            if not isinstance(snapshot, dict):
                metrics["perf_phase_sampling_note"] = (
                    "Runtime performance profiling snapshot is unavailable."
                )
            else:
                metrics["perf_available"] = bool(snapshot.get("available", True))
                for key, value in snapshot.items():
                    metrics[f"perf_{key}"] = value
                phase_metrics = snapshot.get("phase_metrics")
                if isinstance(phase_metrics, dict):
                    phase_sample_counts: dict[str, int] = {}
                    missing_phases: list[str] = []
                    for phase_name in (
                        "idle_gcode_visible",
                        "idle_gcode_hidden",
                        "streaming",
                    ):
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
                            "Phase sampling populated for idle-visible, idle-hidden, and streaming."
                        )
                else:
                    metrics["perf_phase_sampling_note"] = (
                        "Phase metrics unavailable from performance monitor snapshot."
                    )
    raw_status_perf = getattr(app, "_status_perf_metrics", None)
    if isinstance(raw_status_perf, dict) and raw_status_perf:
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
            status_perf[name] = {
                "count": count,
                "avg_ms": avg_ms,
                "max_ms": max_ms,
            }
        if status_perf:
            metrics["status_perf_metrics"] = status_perf
    viewer_mode = str(getattr(app, "_gcode_viewer_mode", "") or "").strip()
    if viewer_mode:
        metrics["viewer_mode"] = viewer_mode
    policy_lines = getattr(app, "_gcode_viewer_policy_line_count", None)
    sample_lines = getattr(app, "_gcode_viewer_sample_line_count", None)
    threshold = getattr(app, "_gcode_viewer_virtualization_threshold", None)
    window = getattr(app, "_gcode_viewer_virtual_window", None)
    chunk_size = getattr(app, "_gcode_viewer_chunk_size", None)
    sample_only = getattr(app, "_gcode_viewer_sample_only", None)
    if policy_lines is not None:
        metrics["viewer_policy_line_count"] = int(policy_lines)
    if sample_lines is not None:
        metrics["viewer_sample_line_count"] = int(sample_lines)
    if threshold is not None:
        metrics["viewer_virtualization_threshold"] = int(threshold)
    if window is not None:
        metrics["viewer_virtual_window_size"] = int(window)
    if chunk_size is not None:
        metrics["viewer_chunk_size"] = int(chunk_size)
    if sample_only is not None:
        metrics["viewer_sample_only"] = bool(sample_only)
    gview = getattr(app, "gview", None)
    if gview is not None:
        for attr_name, metric_name in (
            ("_insert_hidden_delay_ms", "viewer_hidden_insert_delay_ms"),
            ("_insert_hidden_max_chunks_per_tick", "viewer_hidden_max_chunks_per_tick"),
            ("_insert_hidden_chunk_size", "viewer_hidden_chunk_size"),
        ):
            raw_value = getattr(gview, attr_name, None)
            if raw_value is None:
                continue
            try:
                metrics[metric_name] = int(raw_value)
            except (TypeError, ValueError):
                continue
        try:
            metrics["viewer_window_line_count"] = _viewer_window_line_count(gview)
        except Exception:
            metrics["viewer_window_line_count"] = 0
        try:
            insert_lines = getattr(gview, "_insert_lines", None)
            metrics["viewer_insert_pending_lines"] = (
                int(len(insert_lines)) if insert_lines is not None else 0
            )
        except Exception:
            metrics["viewer_insert_pending_lines"] = 0

    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip() or "none"
    gcode_source = getattr(app, "_gcode_source", None)
    source_path = str(getattr(gcode_source, "path", "") or "").strip()
    source_line_count_known = bool(
        getattr(app, "_gcode_source_line_count_known", False)
    )
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
    metrics["gcode_index_mode"] = str(
        getattr(app, "_gcode_index_mode", "") or ""
    ).strip()
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
    cap_lines, cap_profile = _effective_line_cache_cap_lines(app)
    metrics["gcode_line_cache_cap_lines"] = int(cap_lines)
    metrics["gcode_line_cache_cap_profile"] = cap_profile
    metrics["gcode_line_cache_cap_hit"] = bool(
        getattr(app, "_gcode_full_line_cache_cap_hit", False)
    )
    try:
        metrics["gcode_sample_line_cap"] = int(
            getattr(app, "_gcode_sample_line_cap", 0) or 0
        )
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
    metrics["gcode_source_offset_type"] = str(
        getattr(app, "_gcode_source_offset_type", "") or ""
    )
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
    file_line_count_known = bool(
        getattr(app, "_gcode_file_line_count_known", False)
    )
    if (not file_line_count_known) and source_line_count_known and file_line_count > 0:
        file_line_count_known = True
    if file_line_count <= 0:
        file_line_count_known = False
    metrics["gcode_file_size_bytes"] = int(file_size_bytes)
    metrics["gcode_file_line_count"] = int(file_line_count)
    metrics["gcode_file_line_count_known"] = bool(file_line_count_known)
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
    metrics["gcode_quick_scan_ms"] = float(
        getattr(app, "_gcode_quick_scan_ms", 0.0) or 0.0
    )
    metrics["quick_scan_ms"] = float(metrics["gcode_quick_scan_ms"])
    bounds_box = getattr(app, "_gcode_bounds_box", None)
    if isinstance(bounds_box, dict):
        metrics["gcode_bounds_box"] = dict(bounds_box)
        metrics["bounds_box"] = dict(bounds_box)
    metrics["gcode_bounds_confidence"] = str(
        getattr(app, "_gcode_bounds_confidence", "") or ""
    )
    metrics["bounds_confidence"] = str(metrics["gcode_bounds_confidence"])
    estimated_job_time_sec = getattr(app, "_gcode_estimated_job_time_sec", None)
    try:
        metrics["estimated_job_time_sec"] = (
            int(estimated_job_time_sec) if estimated_job_time_sec is not None else None
        )
    except (TypeError, ValueError):
        metrics["estimated_job_time_sec"] = None
    raw_estimate_confidence = str(getattr(app, "_estimate_confidence", "") or "")
    metrics["estimate_confidence"] = (
        "confident"
        if raw_estimate_confidence.strip().lower() == "confident"
        else "rough"
    )
    raw_dimensions_confidence = str(
        getattr(app, "_gcode_dimensions_confidence", "")
        or getattr(app, "_gcode_bounds_confidence", "")
    )
    metrics["dimensions_confidence"] = (
        "confident"
        if raw_dimensions_confidence.strip().lower() == "confident"
        else "rough"
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
    metrics["post_popup_background_tasks"] = "none"
    metrics["time_to_popup_close_ms"] = metrics.get("gcode_time_to_popup_close_ms")
    metrics["time_to_stream_ready_ms"] = metrics.get("gcode_time_to_stream_ready_ms")
    metrics["gcode_stats_compute_mode"] = str(
        getattr(app, "_gcode_stats_compute_mode", "") or ""
    )
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
    loaded_total_min = getattr(app, "_loaded_estimate_total_min", None)
    try:
        metrics["estimate_loaded_total_min"] = (
            float(loaded_total_min) if loaded_total_min is not None else None
        )
    except (TypeError, ValueError):
        metrics["estimate_loaded_total_min"] = None
    metrics["estimate_loaded_source"] = str(
        getattr(app, "_loaded_estimate_source", "") or ""
    )
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
    metrics["auto_level_source_hash"] = str(
        getattr(app, "_auto_level_job_hash", "") or ""
    )
    metrics["auto_level_source_total_lines"] = int(
        getattr(app, "_auto_level_job_total_lines", 0) or 0
    )
    prereq_snapshot = getattr(app, "_auto_level_prereq_snapshot", None)
    if isinstance(prereq_snapshot, dict):
        metrics["auto_level_prereq_snapshot"] = dict(prereq_snapshot)
        metrics["auto_level_prereq_ready"] = bool(
            prereq_snapshot.get("bounds_ready", False)
        )
        metrics["auto_level_prereq_stage"] = str(prereq_snapshot.get("stage", "") or "")

    stream_queue_depth = None
    stream_resume_depth = None
    stream_pending_item = None
    stream_buf_used = None
    if grbl is not None:
        try:
            line_queue = getattr(grbl, "_stream_line_queue", None)
            stream_queue_depth = (
                int(len(line_queue)) if line_queue is not None else None
            )
        except Exception:
            stream_queue_depth = None
        try:
            resume_preamble = getattr(grbl, "_resume_preamble", None)
            stream_resume_depth = (
                int(len(resume_preamble)) if resume_preamble is not None else None
            )
        except Exception:
            stream_resume_depth = None
        try:
            stream_pending_item = bool(
                getattr(grbl, "_stream_pending_item", None) is not None
            )
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
    return metrics


def _format_runtime_metrics(
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int = 20,
) -> list[str]:
    if not metrics:
        return []
    lines: list[str] = []
    kasa_status_line = str(metrics.get("kasa_status_line", "") or "").strip()
    if kasa_status_line:
        lines.append(f"- Kasa status: {kasa_status_line}")
    has_worker_metrics = any(
        key in metrics
        for key in (
            "tx_lines_per_sec",
            "tx_loop_cycles",
            "queue_depth_last",
        )
    )
    if has_worker_metrics:
        tx_lines_per_sec = float(metrics.get("tx_lines_per_sec", 0.0) or 0.0)
        ok_last = float(metrics.get("ok_latency_ms_last", 0.0) or 0.0)
        ok_avg = float(metrics.get("ok_latency_ms_avg", 0.0) or 0.0)
        ok_samples = int(metrics.get("ok_latency_samples", 0) or 0)
        lines.append(f"- TX lines/sec: {tx_lines_per_sec:.2f}")
        lines.append(
            f"- ACK latency ms: last={ok_last:.2f}, avg={ok_avg:.2f}, samples={ok_samples}"
        )
        tx_loop_cycles = int(metrics.get("tx_loop_cycles", 0) or 0)
        tx_loop_idle_cycles = int(metrics.get("tx_loop_idle_cycles", 0) or 0)
        tx_loop_active_cycles = int(metrics.get("tx_loop_active_cycles", 0) or 0)
        tx_loop_idle_wait_total_s = float(
            metrics.get("tx_loop_idle_wait_total_s", 0.0) or 0.0
        )
        tx_loop_idle_ratio = float(metrics.get("tx_loop_idle_ratio", 0.0) or 0.0)
        lines.append(
            "- TX loop: "
            f"cycles={tx_loop_cycles}, "
            f"active={tx_loop_active_cycles}, "
            f"idle={tx_loop_idle_cycles}, "
            f"idle_ratio={tx_loop_idle_ratio:.3f}, "
            f"idle_wait_s={tx_loop_idle_wait_total_s:.2f}"
        )
        queue_last = metrics.get("queue_depth_last", {})
        if isinstance(queue_last, dict):
            lines.append(
                "- Queue depth last: "
                f"stream={int(queue_last.get('stream', 0) or 0)}, "
                f"manual={int(queue_last.get('manual', 0) or 0)}, "
                f"ui={int(queue_last.get('ui', 0) or 0)} "
                f"@ {str(queue_last.get('timestamp', '') or 'n/a')}"
            )
        if include_samples:
            queue_samples = metrics.get("queue_depth_samples", [])
            if isinstance(queue_samples, list) and queue_samples:
                lines.append("- Queue depth samples:")
                for sample in queue_samples[-sample_limit:]:
                    if not isinstance(sample, dict):
                        continue
                    lines.append(
                        "  "
                        f"{str(sample.get('timestamp', '') or 'n/a')} "
                        f"stream={int(sample.get('stream', 0) or 0)} "
                        f"manual={int(sample.get('manual', 0) or 0)} "
                        f"ui={int(sample.get('ui', 0) or 0)}"
                    )
    else:
        lines.append("- Worker runtime telemetry unavailable.")

    perf_available = bool(metrics.get("perf_available", False))
    if not perf_available:
        lines.append(
            "- Perf monitor disabled (enable runtime performance profiling for CPU/RSS telemetry)."
        )
        return lines

    idle_cpu_avg = metrics.get("perf_idle_cpu_avg")
    idle_cpu_p95 = metrics.get("perf_idle_cpu_p95")
    quiet_idle_cpu_avg = metrics.get("perf_quiet_idle_cpu_avg")
    quiet_idle_cpu_p95 = metrics.get("perf_quiet_idle_cpu_p95")
    quiet_idle_samples = metrics.get("perf_quiet_idle_cpu_samples")
    stream_cpu_avg = metrics.get("perf_stream_cpu_avg")
    stream_cpu_p95 = metrics.get("perf_stream_cpu_p95")
    if idle_cpu_avg is None or idle_cpu_p95 is None:
        lines.append("- Idle CPU avg/p95: n/a")
    else:
        lines.append(
            f"- Idle CPU avg/p95: {float(idle_cpu_avg):.2f}% / {float(idle_cpu_p95):.2f}%"
        )
    if quiet_idle_cpu_avg is None or quiet_idle_cpu_p95 is None:
        lines.append("- Quiet idle CPU avg/p95: n/a")
    else:
        sample_suffix = ""
        try:
            sample_count = int(quiet_idle_samples or 0)
        except (TypeError, ValueError):
            sample_count = 0
        if sample_count > 0:
            sample_suffix = f" (samples={sample_count})"
        lines.append(
            "- Quiet idle CPU avg/p95: "
            f"{float(quiet_idle_cpu_avg):.2f}% / {float(quiet_idle_cpu_p95):.2f}%"
            f"{sample_suffix}"
        )
    if stream_cpu_avg is None or stream_cpu_p95 is None:
        lines.append("- Streaming CPU avg/p95: n/a")
    else:
        lines.append(
            f"- Streaming CPU avg/p95: {float(stream_cpu_avg):.2f}% / {float(stream_cpu_p95):.2f}%"
        )
    lines.append(
        f"- RSS start/current/peak: {_format_mb(metrics.get('perf_rss_start_bytes'))} / "
        f"{_format_mb(metrics.get('perf_rss_current_bytes'))} / "
        f"{_format_mb(metrics.get('perf_rss_peak_bytes'))}"
    )
    steady_after = metrics.get("perf_steady_state_after_s")
    steady_label = (
        f"{int(float(steady_after))}s" if steady_after is not None else "steady-state"
    )
    lines.append(
        f"- RSS {steady_label}: {_format_mb(metrics.get('perf_rss_steady_state_bytes'))}"
    )
    uptime_s = metrics.get("perf_uptime_s")
    startup_s = metrics.get("perf_startup_time_s")
    if uptime_s is not None:
        lines.append(f"- Perf monitor uptime: {float(uptime_s):.1f}s")
    if startup_s is not None:
        lines.append(f"- Startup time: {float(startup_s):.3f}s")
    max_drain_ms = metrics.get("perf_ui_queue_drain_runtime_max_ms")
    stall_count = metrics.get("perf_ui_queue_drain_runtime_stall_count")
    if max_drain_ms is None:
        max_drain_ms = metrics.get("perf_ui_queue_drain_max_ms")
    if stall_count is None:
        stall_count = metrics.get("perf_ui_queue_drain_stall_count")
    stall_budget = metrics.get("perf_ui_queue_drain_stall_budget_ms")
    if max_drain_ms is not None and stall_count is not None:
        budget_text = (
            f"{float(stall_budget):.1f}" if stall_budget is not None else "n/a"
        )
        lines.append(
            "- UI queue drain max/stalls: "
            f"{float(max_drain_ms):.2f} ms / {int(stall_count)} (budget {budget_text} ms)"
        )
    slow_event_ms = metrics.get("perf_ui_queue_drain_runtime_slowest_event_ms")
    if slow_event_ms is not None:
        try:
            slow_ms = float(slow_event_ms)
        except (TypeError, ValueError):
            slow_ms = 0.0
        if slow_ms > 0.0:
            slow_kind = str(
                metrics.get("perf_ui_queue_drain_runtime_slowest_event_kind", "") or ""
            )
            lines.append(
                "- UI queue slowest runtime event: "
                f"{slow_kind or 'unknown'} ({slow_ms:.2f} ms)"
            )
    phase_metrics = metrics.get("perf_phase_metrics")
    if isinstance(phase_metrics, dict) and phase_metrics:
        lines.append("- Phase CPU/RSS:")
        for phase_name in ("idle_gcode_visible", "idle_gcode_hidden", "streaming"):
            phase = phase_metrics.get(phase_name)
            if not isinstance(phase, dict):
                continue
            label = phase_name.replace("_", " ")
            samples = int(phase.get("samples", 0) or 0)
            cpu_avg = phase.get("cpu_avg")
            cpu_p95 = phase.get("cpu_p95")
            if cpu_avg is None or cpu_p95 is None:
                cpu_text = "n/a"
            else:
                cpu_text = f"{float(cpu_avg):.2f}% / {float(cpu_p95):.2f}%"
            lines.append(
                "  "
                f"{label}: "
                f"cpu avg/p95={cpu_text}, "
                f"samples={samples}, "
                f"rss start/current/peak="
                f"{_format_mb(phase.get('rss_start_bytes'))} / "
                f"{_format_mb(phase.get('rss_current_bytes'))} / "
                f"{_format_mb(phase.get('rss_peak_bytes'))}"
            )
    phase_sampling_note = str(metrics.get("perf_phase_sampling_note", "") or "").strip()
    if phase_sampling_note:
        lines.append(f"- Phase sampling: {phase_sampling_note}")
    outlier_total = metrics.get("perf_ui_queue_drain_outlier_total")
    outlier_entries = metrics.get("perf_ui_queue_drain_outliers")
    if isinstance(outlier_total, (int, float)) or (
        isinstance(outlier_entries, list) and outlier_entries
    ):
        lines.append(f"- UI queue outlier ticks captured: {int(outlier_total or 0)}")
    if isinstance(outlier_entries, list) and outlier_entries:
        lines.append("- UI queue outlier details (recent):")
        for entry in outlier_entries[-5:]:
            if not isinstance(entry, dict):
                continue
            tick_ms = float(entry.get("tick_ms", 0.0) or 0.0)
            tab = str(entry.get("active_tab", "") or "unknown")
            events = int(entry.get("events_drained", 0) or 0)
            pending_after = int(entry.get("pending_after_tick", 0) or 0)
            slow_kind = str(entry.get("slowest_event_kind", "") or "unknown")
            slow_ms = float(entry.get("slowest_event_ms", 0.0) or 0.0)
            lines.append(
                "  "
                f"tick={tick_ms:.2f} ms, tab={tab}, events={events}, pending={pending_after}, "
                f"slowest={slow_kind} ({slow_ms:.2f} ms)"
            )
            top_kind_timing = entry.get("top_kind_timing")
            if isinstance(top_kind_timing, list) and top_kind_timing:
                top_parts: list[str] = []
                for item in top_kind_timing[:5]:
                    if not isinstance(item, dict):
                        continue
                    kind = str(item.get("kind", "") or "unknown")
                    total_ms = float(item.get("ms", 0.0) or 0.0)
                    count = int(item.get("count", 0) or 0)
                    top_parts.append(f"{kind}={total_ms:.2f}ms ({count})")
                if top_parts:
                    lines.append("    top kinds: " + ", ".join(top_parts))
            per_kind_counts = entry.get("per_kind_counts")
            if isinstance(per_kind_counts, dict) and per_kind_counts:
                count_parts = []
                for idx, (kind, count) in enumerate(per_kind_counts.items()):
                    if idx >= 8:
                        break
                    count_parts.append(f"{str(kind)}={int(count)}")
                if count_parts:
                    lines.append("    event counts: " + ", ".join(count_parts))
            special_ops = entry.get("special_ops")
            if isinstance(special_ops, list) and special_ops:
                lines.append(
                    "    special ops: "
                    + ", ".join(str(item) for item in special_ops if item)
                )
    status_perf = metrics.get("status_perf_metrics")
    if isinstance(status_perf, dict) and status_perf:
        lines.append("- Status handler timings:")
        for name in sorted(status_perf.keys()):
            entry = status_perf.get(name)
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{name}: "
                f"count={int(entry.get('count', 0) or 0)}, "
                f"avg={float(entry.get('avg_ms', 0.0) or 0.0):.3f} ms, "
                f"max={float(entry.get('max_ms', 0.0) or 0.0):.3f} ms"
            )
    task_timings = metrics.get("perf_background_task_timings")
    if isinstance(task_timings, dict) and task_timings:
        load_end = task_timings.get("gcode.load.end_to_end")
        if isinstance(load_end, dict):
            lines.append(
                "- Preparing Job timing (end-to-end): "
                f"count={int(load_end.get('count', 0) or 0)}, "
                f"avg={float(load_end.get('avg_ms', 0.0) or 0.0):.2f} ms, "
                f"max={float(load_end.get('max_ms', 0.0) or 0.0):.2f} ms"
            )
        lines.append("- Background I/O timings:")
        for task_name in sorted(task_timings.keys()):
            entry = task_timings.get(task_name)
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{task_name}: "
                f"count={int(entry.get('count', 0) or 0)}, "
                f"ok={int(entry.get('ok_count', 0) or 0)}, "
                f"err={int(entry.get('err_count', 0) or 0)}, "
                f"avg={float(entry.get('avg_ms', 0.0) or 0.0):.2f} ms, "
                f"max={float(entry.get('max_ms', 0.0) or 0.0):.2f} ms"
            )
    viewer_mode = str(metrics.get("viewer_mode", "") or "").strip()
    if viewer_mode:
        details: list[str] = [f"mode={viewer_mode}"]
        policy_lines = metrics.get("viewer_policy_line_count")
        sample_lines = metrics.get("viewer_sample_line_count")
        threshold = metrics.get("viewer_virtualization_threshold")
        sample_only = metrics.get("viewer_sample_only")
        window_size = metrics.get("viewer_virtual_window_size")
        chunk_size = metrics.get("viewer_chunk_size")
        if policy_lines is not None:
            details.append(f"policy_lines={int(policy_lines):,}")
        if sample_lines is not None:
            details.append(f"sample_lines={int(sample_lines):,}")
        if threshold is not None:
            details.append(f"threshold={int(threshold):,}")
        if sample_only is not None:
            details.append(f"sample_only={bool(sample_only)}")
        if window_size is not None:
            details.append(f"window={int(window_size):,}")
        if chunk_size is not None:
            details.append(f"chunk={int(chunk_size):,}")
        lines.append("- G-code viewer load policy: " + ", ".join(details))
    hidden_delay = metrics.get("viewer_hidden_insert_delay_ms")
    hidden_chunks = metrics.get("viewer_hidden_max_chunks_per_tick")
    hidden_chunk_size = metrics.get("viewer_hidden_chunk_size")
    if (
        hidden_delay is not None
        or hidden_chunks is not None
        or hidden_chunk_size is not None
    ):
        details: list[str] = []
        if hidden_delay is not None:
            details.append(f"delay={int(hidden_delay)} ms")
        if hidden_chunks is not None:
            details.append(f"max_chunks={int(hidden_chunks)}")
        if hidden_chunk_size is not None:
            details.append(f"chunk_size={int(hidden_chunk_size)}")
        if details:
            lines.append("- G-code viewer hidden-tab throttle: " + ", ".join(details))
    storage_mode = str(metrics.get("gcode_storage_mode", "") or "").strip()
    load_mode = str(metrics.get("gcode_load_mode", "") or "").strip() or "n/a"
    index_mode = str(metrics.get("gcode_index_mode", "") or "").strip() or "n/a"
    line_count_known = bool(metrics.get("gcode_source_line_count_known", True))
    time_to_ready_ms = metrics.get("gcode_time_to_stream_ready_ms")
    time_to_popup_ms = metrics.get("gcode_time_to_popup_close_ms")
    if storage_mode:
        timing_parts: list[str] = []
        try:
            if time_to_ready_ms is not None:
                timing_parts.append(
                    f"time_to_stream_ready_ms={float(time_to_ready_ms):.2f}"
                )
        except (TypeError, ValueError):
            pass
        try:
            if time_to_popup_ms is not None:
                timing_parts.append(
                    f"time_to_popup_close_ms={float(time_to_popup_ms):.2f}"
                )
        except (TypeError, ValueError):
            pass
        detail = (
            f"{storage_mode}, load_mode={load_mode}, index_mode={index_mode}, "
            f"line_count_known={line_count_known}"
        )
        if timing_parts:
            detail += ", " + ", ".join(timing_parts)
        lines.append("- G-code storage mode: " + detail)
    quick_scan_ms = float(metrics.get("gcode_quick_scan_ms", 0.0) or 0.0)
    bounds_confidence = (
        str(metrics.get("gcode_bounds_confidence", "") or "").strip() or "rough"
    )
    dimensions_confidence = (
        str(metrics.get("dimensions_confidence", "") or "").strip() or "rough"
    )
    estimate_confidence = (
        str(metrics.get("estimate_confidence", "") or "").strip() or "rough"
    )
    estimated_job_time_sec = metrics.get("estimated_job_time_sec")
    file_size_bytes = int(metrics.get("gcode_file_size_bytes", 0) or 0)
    file_line_count = int(metrics.get("gcode_file_line_count", 0) or 0)
    file_line_count_known = bool(metrics.get("gcode_file_line_count_known", False))
    lines.append(
        "- Quick Assessment: "
        f"quick_scan_ms={quick_scan_ms:.2f}, "
        f"file_size_bytes={file_size_bytes:,}, "
        f"file_line_count={file_line_count:,} ({'known' if file_line_count_known else 'estimated'}), "
        f"estimated_job_time_sec={int(estimated_job_time_sec) if estimated_job_time_sec is not None else 'n/a'}, "
        f"estimate_confidence={estimate_confidence}, "
        f"dimensions_confidence={dimensions_confidence}, "
        f"bounds_confidence={bounds_confidence}"
    )
    bounds_box = metrics.get("gcode_bounds_box")
    if isinstance(bounds_box, dict):
        try:
            lines.append(
                "- Bounds footprint box: "
                f"X[{float(bounds_box.get('min_x', 0.0)):.3f}..{float(bounds_box.get('max_x', 0.0)):.3f}] "
                f"Y[{float(bounds_box.get('min_y', 0.0)):.3f}..{float(bounds_box.get('max_y', 0.0)):.3f}] "
                f"Z[{float(bounds_box.get('min_z', 0.0)):.3f}..{float(bounds_box.get('max_z', 0.0)):.3f}] "
                f"size={float(bounds_box.get('width', 0.0)):.3f}x{float(bounds_box.get('height', 0.0)):.3f} mm"
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        "- Post-popup background tasks: "
        + str(metrics.get("post_popup_background_tasks", "none") or "none")
    )
    cap_lines = metrics.get("gcode_line_cache_cap_lines")
    cap_profile = str(metrics.get("gcode_line_cache_cap_profile", "") or "").strip()
    cap_hit = metrics.get("gcode_line_cache_cap_hit")
    sample_cap = metrics.get("gcode_sample_line_cap")
    if cap_lines is not None:
        detail = f"cap={int(cap_lines):,} lines"
        if cap_profile:
            detail += f" ({cap_profile})"
        if cap_hit is not None:
            detail += f", cap_hit={bool(cap_hit)}"
        if sample_cap is not None:
            detail += f", sample_cap={int(sample_cap):,}"
        lines.append("- G-code line-cache policy: " + detail)
    retained = metrics.get("gcode_retained_line_count")
    viewer_window = metrics.get("viewer_window_line_count")
    viewer_pending = metrics.get("viewer_insert_pending_lines")
    retention_parts: list[str] = []
    if retained is not None:
        retention_parts.append(f"retained_lines={int(retained):,}")
    if viewer_window is not None:
        retention_parts.append(f"viewer_window_lines={int(viewer_window):,}")
    if viewer_pending is not None:
        retention_parts.append(f"viewer_insert_pending={int(viewer_pending):,}")
    if retention_parts:
        lines.append("- G-code retained footprint: " + ", ".join(retention_parts))
    source_offsets = metrics.get("gcode_source_offset_count")
    source_offset_type = str(metrics.get("gcode_source_offset_type", "") or "").strip()
    offset_index_enabled = bool(metrics.get("gcode_offset_index_enabled", False))
    if source_offsets is not None:
        offset_details = f"offsets={int(source_offsets):,}"
        if source_offset_type:
            offset_details += f" ({source_offset_type})"
        offset_details += f", enabled={offset_index_enabled}"
        lines.append("- File-backed source index: " + offset_details)
    sample_lines = int(metrics.get("gcode_prepare_sample_line_count", 0) or 0)
    sample_head = int(metrics.get("gcode_prepare_sample_head_lines", 0) or 0)
    sample_tail = int(metrics.get("gcode_prepare_sample_tail_lines", 0) or 0)
    sample_interval = int(metrics.get("gcode_prepare_sample_interval_lines", 0) or 0)
    sample_max = int(metrics.get("gcode_prepare_sample_max_lines", 0) or 0)
    prep_exec_total = int(metrics.get("gcode_prepare_executable_total_lines", 0) or 0)
    prep_motion_total = int(metrics.get("gcode_prepare_motion_total_lines", 0) or 0)
    prep_sample_exec = int(
        metrics.get("gcode_prepare_sampled_executable_lines", 0) or 0
    )
    prep_sample_motion = int(metrics.get("gcode_prepare_sampled_motion_lines", 0) or 0)
    stats_mode = str(metrics.get("gcode_stats_compute_mode", "") or "").strip() or "n/a"
    stats_scale = float(metrics.get("gcode_stats_sample_scale", 1.0) or 1.0)
    stats_sample_lines = int(metrics.get("gcode_stats_sample_line_count", 0) or 0)
    stats_sample_total = int(metrics.get("gcode_stats_sample_total_lines", 0) or 0)
    stats_sample_executable = int(
        metrics.get("gcode_stats_sample_executable_lines", 0) or 0
    )
    stats_sample_motion = int(metrics.get("gcode_stats_sample_motion_lines", 0) or 0)
    stats_total_executable = int(
        metrics.get("gcode_stats_executable_total_lines", 0) or 0
    )
    stats_total_motion = int(metrics.get("gcode_stats_motion_total_lines", 0) or 0)
    stats_chunk_max_ms = float(metrics.get("gcode_stats_chunk_max_ms", 0.0) or 0.0)
    stats_chunk_max_section = str(
        metrics.get("gcode_stats_chunk_max_section", "") or "unknown"
    )
    stats_chunk_yields = int(metrics.get("gcode_stats_chunk_yield_count", 0) or 0)
    lines.append(
        "- Prepare sample policy: "
        f"sample_lines={sample_lines:,}, head={sample_head:,}, tail={sample_tail:,}, "
        f"interval={sample_interval:,}, max={sample_max:,}"
    )
    lines.append(
        "- Prepare sampled counts: "
        f"sample_exec={prep_sample_exec:,}, sample_motion={prep_sample_motion:,}, "
        f"total_exec={prep_exec_total:,}, total_motion={prep_motion_total:,}"
    )
    lines.append(
        "- Prepare sampled modes: "
        f"stats={stats_mode}, "
        f"stats_scale={stats_scale:.2f}, stats_sample={stats_sample_lines:,}/{stats_sample_total:,}"
    )
    lines.append(
        "- Estimate sample coverage: "
        f"sample_exec={stats_sample_executable:,}, sample_motion={stats_sample_motion:,}, "
        f"total_exec={stats_total_executable:,}, total_motion={stats_total_motion:,}"
    )
    lines.append(
        "- Stats cooperative chunking: "
        f"chunk_max_ms={stats_chunk_max_ms:.2f}, chunk_max_section={stats_chunk_max_section}, yields={stats_chunk_yields:,}"
    )
    loaded_estimate_min = metrics.get("estimate_loaded_total_min")
    loaded_estimate_source = str(
        metrics.get("estimate_loaded_source", "") or ""
    ).strip()
    estimate_confidence = (
        str(metrics.get("estimate_confidence", "") or "").strip() or "rough"
    )
    observed_total_min = metrics.get("estimate_live_observed_total_min")
    try:
        loaded_estimate_min_f = (
            float(loaded_estimate_min) if loaded_estimate_min is not None else 0.0
        )
    except (TypeError, ValueError):
        loaded_estimate_min_f = 0.0
    estimate_parts: list[str] = []
    if loaded_estimate_min_f > 0.0:
        estimate_parts.append(f"baseline_total_min={loaded_estimate_min_f:.3f}")
    if loaded_estimate_source:
        estimate_parts.append(f"baseline_source={loaded_estimate_source}")
    try:
        observed_total_min_f = (
            float(observed_total_min) if observed_total_min is not None else None
        )
    except (TypeError, ValueError):
        observed_total_min_f = None
    if observed_total_min_f is not None and observed_total_min_f > 0.0:
        estimate_parts.append(f"observed_total_min={observed_total_min_f:.3f}")
    if estimate_parts:
        lines.append("- Estimator runtime totals: " + ", ".join(estimate_parts))
    lines.append(f"- Estimator confidence: {estimate_confidence}")
    rate_source = str(metrics.get("estimate_rate_source", "") or "").strip()
    rapid_rates = metrics.get("estimate_rapid_rates_mm_min")
    accel_rates = metrics.get("estimate_accel_rates_mm_s2")
    rate_parts: list[str] = []
    if rate_source:
        rate_parts.append(f"rapid_source={rate_source}")
    if isinstance(rapid_rates, list) and len(rapid_rates) == 3:
        try:
            rate_parts.append(
                "rapid_mm_min=("
                + ", ".join(f"{float(rapid_rates[idx]):.3f}" for idx in range(3))
                + ")"
            )
        except (TypeError, ValueError):
            pass
    if isinstance(accel_rates, list) and len(accel_rates) == 3:
        try:
            rate_parts.append(
                "accel_mm_s2=("
                + ", ".join(f"{float(accel_rates[idx]):.3f}" for idx in range(3))
                + ")"
            )
        except (TypeError, ValueError):
            pass
    if rate_parts:
        lines.append("- Estimator motion rates: " + ", ".join(rate_parts))
    estimate_inputs = metrics.get("estimate_inputs_snapshot")
    if isinstance(estimate_inputs, dict):
        capture_stage = (
            str(estimate_inputs.get("capture_stage", "") or "").strip() or "n/a"
        )
        capture_rate_source = (
            str(estimate_inputs.get("rate_source", "") or "").strip() or "n/a"
        )
        capture_stats_mode = (
            str(estimate_inputs.get("stats_mode", "") or "").strip() or "n/a"
        )
        capture_sample = int(estimate_inputs.get("stats_sample_line_count", 0) or 0)
        capture_total = int(estimate_inputs.get("stats_sample_total_lines", 0) or 0)
        capture_sample_exec = int(
            estimate_inputs.get("stats_sample_executable_lines", 0) or 0
        )
        capture_sample_motion = int(
            estimate_inputs.get("stats_sample_motion_lines", 0) or 0
        )
        capture_total_exec = int(
            estimate_inputs.get("stats_executable_total_lines", 0) or 0
        )
        capture_total_motion = int(
            estimate_inputs.get("stats_motion_total_lines", 0) or 0
        )
        capture_confidence = (
            str(estimate_inputs.get("estimate_confidence", "") or "").strip()
            or "provisional"
        )
        capture_scale = float(estimate_inputs.get("stats_sample_scale", 1.0) or 1.0)
        lines.append(
            "- Estimator captured inputs: "
            f"stage={capture_stage}, rate_source={capture_rate_source}, "
            f"stats_mode={capture_stats_mode}, sample={capture_sample:,}/{capture_total:,}, "
            f"sample_exec={capture_sample_exec:,}, sample_motion={capture_sample_motion:,}, "
            f"total_exec={capture_total_exec:,}, total_motion={capture_total_motion:,}, "
            f"scale={capture_scale:.2f}, confidence={capture_confidence}"
        )
        modal_ctx = estimate_inputs.get("modal_context")
        if isinstance(modal_ctx, dict):
            lines.append(
                "- Estimator modal context: "
                f"units={str(modal_ctx.get('units', '') or 'n/a')}, "
                f"distance={str(modal_ctx.get('distance_mode', '') or 'n/a')}, "
                f"feed={str(modal_ctx.get('feed_mode', '') or 'n/a')}, "
                f"sampled_lines={int(modal_ctx.get('sampled_lines', 0) or 0):,}"
            )
        motion_settings = estimate_inputs.get("grbl_motion_settings")
        if isinstance(motion_settings, dict):
            parts: list[str] = []
            for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
                raw = motion_settings.get(key)
                if raw is None:
                    parts.append(f"{key}=n/a")
                else:
                    try:
                        parts.append(f"{key}={float(raw):.3f}")
                    except (TypeError, ValueError):
                        parts.append(f"{key}=n/a")
            lines.append("- Estimator GRBL settings: " + ", ".join(parts))
    al_source_path = str(metrics.get("auto_level_source_path", "") or "").strip()
    al_source_exists = bool(metrics.get("auto_level_source_exists", False))
    al_source_hash = str(metrics.get("auto_level_source_hash", "") or "").strip()
    al_source_total_lines = int(metrics.get("auto_level_source_total_lines", 0) or 0)
    source_parts = [
        f"exists={al_source_exists}",
        f"lines={al_source_total_lines:,}",
    ]
    if al_source_hash:
        source_parts.append(f"hash={al_source_hash}")
    if al_source_path:
        source_parts.append(f"path={al_source_path}")
    lines.append("- Auto-level source snapshot: " + ", ".join(source_parts))
    prereq_snapshot = metrics.get("auto_level_prereq_snapshot")
    if isinstance(prereq_snapshot, dict):
        stage = str(prereq_snapshot.get("stage", "") or "n/a")
        ready = bool(prereq_snapshot.get("bounds_ready", False))
        width_mm = float(prereq_snapshot.get("xy_width_mm", 0.0) or 0.0)
        height_mm = float(prereq_snapshot.get("xy_height_mm", 0.0) or 0.0)
        grid_ok = bool(prereq_snapshot.get("probe_grid_applicable", False))
        lines.append(
            "- Auto-level prereq snapshot: "
            f"stage={stage}, bounds_ready={ready}, "
            f"size_mm={width_mm:.3f}x{height_mm:.3f}, "
            f"grid_applicable={grid_ok}"
        )
    stream_queue_depth = metrics.get("stream_outstanding_queue_depth")
    stream_pending_item = metrics.get("stream_pending_item")
    stream_resume_depth = metrics.get("stream_resume_preamble_depth")
    stream_buf_used = metrics.get("stream_buf_used_bytes")
    stream_parts: list[str] = []
    if stream_queue_depth is not None:
        stream_parts.append(f"queued_lines={int(stream_queue_depth)}")
    if stream_pending_item is not None:
        stream_parts.append(f"pending_item={bool(stream_pending_item)}")
    if stream_resume_depth is not None:
        stream_parts.append(f"resume_preamble={int(stream_resume_depth)}")
    if stream_buf_used is not None:
        stream_parts.append(f"buf_used={int(stream_buf_used)}B")
    if stream_parts:
        lines.append("- Stream read-ahead footprint: " + ", ".join(stream_parts))
    serial_tail = metrics.get("serial_activity_tail")
    if isinstance(serial_tail, list):
        lines.append(f"- Serial activity tail entries: {len(serial_tail)}")
    return lines


def open_runtime_telemetry(app) -> None:
    existing = getattr(app, "_runtime_telemetry_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing runtime telemetry window", exc)
    win = tk.Toplevel(app)
    app._runtime_telemetry_window = win
    app._runtime_telemetry_after_id = None
    win.title("Runtime telemetry")
    win.minsize(600, 360)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container, text="Runtime telemetry", font=("TkDefaultFont", 12, "bold")
    ).pack(anchor="w")
    ttk.Label(
        container,
        text="Live worker/queue counters. Refreshes every second while this window is open.",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    text = tk.Text(container, wrap="none", height=14, font=("TkFixedFont", 10))
    text.pack(fill="both", expand=True)
    text.configure(state="disabled")
    last_rendered = {"text": None}
    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))

    def _render() -> None:
        metrics = _runtime_metrics(app)
        if metrics:
            lines = _format_runtime_metrics(
                metrics, include_samples=True, sample_limit=12
            )
            body = "\n".join(lines) if lines else "Runtime telemetry unavailable."
        else:
            body = "Runtime telemetry unavailable."
        if body == last_rendered["text"]:
            return
        last_rendered["text"] = body
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", body)
        text.configure(state="disabled")

    def _schedule_refresh() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            app._runtime_telemetry_after_id = win.after(
                RUNTIME_TELEMETRY_REFRESH_MS,
                _on_refresh_timer,
            )
        except Exception as exc:
            _log_suppressed("Failed scheduling runtime telemetry refresh", exc)

    def _on_refresh_timer() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        _render()
        _schedule_refresh()

    def _on_close() -> None:
        after_id = getattr(app, "_runtime_telemetry_after_id", None)
        if after_id is not None:
            try:
                win.after_cancel(after_id)
            except Exception:
                pass
        app._runtime_telemetry_after_id = None
        app._runtime_telemetry_window = None
        win.destroy()

    ttk.Button(btn_row, text="Refresh now", command=_render).pack(side="left")
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)
    _render()
    _schedule_refresh()
    center_window(win, app)


def _record_preflight_decision(
    app: Any,
    *,
    decision: str,
    failures: list[str],
    warnings: list[str],
) -> None:
    job_path = str(getattr(app, "_last_gcode_path", "") or "")
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "decision": str(decision),
        "job_path": job_path,
        "job_name": os.path.basename(job_path) if job_path else "",
        "job_hash": _safe_job_hash(app),
        "stream_state": str(getattr(app, "_stream_state", "") or ""),
        "streaming_mode": bool(getattr(app, "_gcode_streaming_mode", False)),
        "failure_count": int(len(failures)),
        "warning_count": int(len(warnings)),
        "failures": list(failures),
        "warnings": list(warnings),
    }
    try:
        history = getattr(app, "_preflight_run_decisions", None)
        if not isinstance(history, list):
            history = []
            setattr(app, "_preflight_run_decisions", history)
        history.append(record)
        overflow = len(history) - PREFLIGHT_DECISION_HISTORY_LIMIT
        if overflow > 0:
            del history[:overflow]
    except Exception as exc:
        _log_suppressed("Failed recording preflight run decision", exc)


def _resolve_checklist_items(app, name: str, fallback: list[str]) -> list[str]:
    path = find_named_checklist(app, name)
    if not path:
        return fallback
    items = load_checklist_items(path)
    if items is None:
        return fallback
    return cast(list[str], items)


def _resolve_checklist_items_any(
    app, names: list[str], fallback: list[str]
) -> list[str]:
    for name in names:
        path = find_named_checklist(app, name)
        if not path:
            continue
        items = load_checklist_items(path)
        if items is not None:
            return cast(list[str], items)
    return fallback


def open_release_checklist(app):
    existing = getattr(app, "_release_checklist_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing release checklist window", exc)
    win = tk.Toplevel(app)
    app._release_checklist_window = win
    win.title("Release checklist")
    win.minsize(560, 380)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(
        container, text="Release checklist", font=("TkDefaultFont", 12, "bold")
    )
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this quick pass before release to confirm the critical GRBL workflows.",
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    items = _resolve_checklist_items(app, "release", CHECKLIST_ITEMS)
    text = tk.Text(container, wrap="word", height=12)
    text.pack(fill="both", expand=True)
    if items:
        text.insert("end", "\n".join(f"- {item}" for item in items))
    else:
        text.insert("end", "Checklist file is empty.")
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close():
        app._release_checklist_window = None
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)


def open_run_checklist(app):
    existing = getattr(app, "_run_checklist_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing run checklist window", exc)
    win = tk.Toplevel(app)
    app._run_checklist_window = win
    win.title("Start Job checklist")
    win.minsize(520, 320)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(
        container, text="Start Job checklist", font=("TkDefaultFont", 12, "bold")
    )
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this checklist before starting a job to reduce surprises.",
        wraplength=480,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    items = _resolve_checklist_items_any(app, ["start-job", "run"], RUN_CHECKLIST_ITEMS)
    text = tk.Text(container, wrap="word", height=10)
    text.pack(fill="both", expand=True)
    if items:
        text.insert("end", "\n".join(f"- {item}" for item in items))
    else:
        text.insert("end", "Checklist file is empty.")
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close():
        app._run_checklist_window = None
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)


def _format_validation_summary(report) -> list[str]:
    if report is None:
        return []
    summary = []
    if getattr(report, "long_line_count", 0):
        summary.append(f"Overlong lines: {report.long_line_count}")
    unsupported_axes = getattr(report, "unsupported_axes", {})
    if unsupported_axes:
        axes = ", ".join(f"{k} x{v}" for k, v in unsupported_axes.items())
        summary.append(f"Unsupported axes: {axes}")
    unsupported_g = getattr(report, "unsupported_g_codes", {})
    if unsupported_g:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_g.items())
        summary.append(f"Unsupported G-codes: {codes}")
    unsupported_m = getattr(report, "unsupported_m_codes", {})
    if unsupported_m:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_m.items())
        summary.append(f"Unsupported M-codes: {codes}")
    grbl_warnings = getattr(report, "grbl_warnings", {})
    if grbl_warnings:
        warnings = ", ".join(f"{k} x{v}" for k, v in grbl_warnings.items())
        summary.append(f"GRBL warnings: {warnings}")
    unsupported_words = getattr(report, "unsupported_words", {})
    if unsupported_words:
        words = ", ".join(f"{k} x{v}" for k, v in unsupported_words.items())
        summary.append(f"Unknown words: {words}")
    hazards = sorted(getattr(report, "modal_hazards", set()))
    if hazards:
        summary.append(f"Modal hazards: {', '.join(hazards)}")
    if getattr(report, "line_issue_count", 0):
        summary.append(f"Line issues: {report.line_issue_count}")
    return summary


def _get_bounds(app: Any):
    parse_result = getattr(app, "_last_parse_result", None)
    bounds = getattr(parse_result, "bounds", None) if parse_result else None
    if not bounds:
        quick_bounds = getattr(app, "_gcode_bounds_box", None)
        if isinstance(quick_bounds, dict):
            try:
                bounds = (
                    float(quick_bounds.get("min_x", 0.0) or 0.0),
                    float(quick_bounds.get("max_x", 0.0) or 0.0),
                    float(quick_bounds.get("min_y", 0.0) or 0.0),
                    float(quick_bounds.get("max_y", 0.0) or 0.0),
                    float(quick_bounds.get("min_z", 0.0) or 0.0),
                    float(quick_bounds.get("max_z", 0.0) or 0.0),
                )
            except Exception:
                bounds = None
    if not bounds or len(bounds) < 6:
        return None
    return bounds


def _get_travel_limits(app: Any) -> dict[str, float]:
    data = (
        getattr(getattr(app, "settings_controller", None), "_settings_data", {}) or {}
    )
    out: dict[str, float] = {}
    for key, axis in (("$130", "x"), ("$131", "y"), ("$132", "z")):
        raw = data.get(key)
        if not raw:
            continue
        try:
            out[axis] = float(raw[0])
        except Exception:
            continue
    return out


def _is_incremental_hazard_only(report: Any) -> bool:
    if report is None:
        return False
    if int(getattr(report, "line_issue_count", 0)) <= 0:
        return False
    if int(getattr(report, "long_line_count", 0)) > 0:
        return False
    if getattr(report, "unsupported_axes", None):
        return False
    if getattr(report, "unsupported_words", None):
        return False
    if getattr(report, "unsupported_g_codes", None):
        return False
    if getattr(report, "unsupported_m_codes", None):
        return False
    if getattr(report, "grbl_warnings", None):
        return False
    hazards = set(getattr(report, "modal_hazards", set()) or set())
    expected = {"G91 (incremental distance mode)"}
    if hazards != expected:
        return False
    line_issues = list(getattr(report, "line_issues", []) or [])
    if not line_issues:
        return False
    for issue in line_issues:
        line_text = str(getattr(issue, "line", "")).strip().upper()
        if line_text != "G91":
            return False
        issue_texts = tuple(str(text) for text in getattr(issue, "issues", tuple()))
        if issue_texts != ("Modal hazard: G91 (incremental distance mode)",):
            return False
    return True


def evaluate_run_preflight(app: Any) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    path = getattr(app, "_last_gcode_path", None)
    has_job = bool(path)
    if not has_job:
        try:
            has_job = bool(getattr(app.gview, "lines_count", 0))
        except Exception:
            has_job = False
    if not has_job:
        failures.append("No G-code job is loaded.")
        return failures, warnings

    if bool(getattr(app, "_alarm_locked", False)):
        failures.append("Controller is in Alarm state. Clear alarm before running.")
    if bool(getattr(app, "_homing_in_progress", False)):
        failures.append("Homing is currently active. Wait until homing finishes.")
    if not bool(getattr(app, "_grbl_ready", False)):
        failures.append("GRBL is not ready yet. Wait for startup/status sync.")
    if not bool(getattr(app, "_status_seen", False)):
        failures.append("No live status has been received yet.")

    bounds = _get_bounds(app)
    if not bounds:
        failures.append("Job bounds are unavailable (wait for parsing to complete).")
    else:
        minx, maxx, miny, maxy, minz, maxz = bounds
        span_x = max(0.0, float(maxx) - float(minx))
        span_y = max(0.0, float(maxy) - float(miny))
        span_z = max(0.0, float(maxz) - float(minz))
        travel = _get_travel_limits(app)
        if travel:
            if "x" in travel and span_x > travel["x"] + 1e-6:
                failures.append(
                    f"X span {span_x:.3f} mm exceeds machine travel $130={travel['x']:.3f} mm."
                )
            if "y" in travel and span_y > travel["y"] + 1e-6:
                failures.append(
                    f"Y span {span_y:.3f} mm exceeds machine travel $131={travel['y']:.3f} mm."
                )
            if "z" in travel and span_z > travel["z"] + 1e-6:
                failures.append(
                    f"Z span {span_z:.3f} mm exceeds machine travel $132={travel['z']:.3f} mm."
                )
        else:
            warnings.append("Machine travel settings ($130/$131/$132) are unavailable.")

    streaming_mode = bool(getattr(app, "_gcode_streaming_mode", False))
    validate_streaming = False
    try:
        validate_streaming = bool(app.validate_streaming_gcode.get())
    except Exception:
        validate_streaming = False
    if streaming_mode and not validate_streaming:
        failures.append("Streaming validation is disabled for this large file.")

    report = getattr(app, "_gcode_validation_report", None)
    if report is None:
        if streaming_mode:
            if validate_streaming:
                failures.append(
                    "Validation report is unavailable (streaming validation failed/skipped)."
                )
        else:
            failures.append("Validation report is unavailable.")
    else:
        if _is_incremental_hazard_only(report):
            warnings.append(
                "Validation note: incremental mode (G91) is used; confirm this is intentional."
            )
        elif getattr(report, "line_issue_count", 0) > 0:
            failures.append(
                f"Validation found issues on {int(getattr(report, 'line_issue_count', 0))} line(s)."
            )
        if getattr(report, "grbl_warnings", None):
            failures.append("Validation reported GRBL incompatibility warnings.")
    return failures, warnings


def run_preflight_gate(app: Any) -> bool:
    failures, warnings = evaluate_run_preflight(app)
    if failures:
        message = "Run blocked by preflight safety gate:\n" + "\n".join(
            f"- {item}" for item in failures
        )
        if warnings:
            message += "\n\nWarnings:\n" + "\n".join(f"- {item}" for item in warnings)
        proceed = bool(
            messagebox.askyesno(
                "Preflight gate",
                message
                + "\n\nContinue anyway?\n"
                + "Choose Yes to override the preflight gate and start the job.",
            )
        )
        if proceed:
            _record_preflight_decision(
                app,
                decision="override",
                failures=failures,
                warnings=warnings,
            )
            try:
                app.ui_q.put(
                    (
                        "log",
                        "[preflight] Override accepted; starting despite gate failures.",
                    )
                )
                for item in failures:
                    app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
                for item in warnings:
                    app.ui_q.put(("log", f"[preflight] warning: {item}"))
            except Exception as exc:
                _log_suppressed(
                    "Failed writing preflight override details to UI log queue", exc
                )
            try:
                app.status.config(text="Preflight overridden: starting job")
            except Exception as exc:
                _log_suppressed(
                    "Failed updating status text after preflight override", exc
                )
            return True
        _record_preflight_decision(
            app,
            decision="blocked",
            failures=failures,
            warnings=warnings,
        )
        try:
            app.ui_q.put(("log", "[preflight] Override declined; run canceled."))
            for item in failures:
                app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
            for item in warnings:
                app.ui_q.put(("log", f"[preflight] warning: {item}"))
        except Exception as exc:
            _log_suppressed(
                "Failed writing preflight blocked decision details to UI log queue", exc
            )
        try:
            app.status.config(text="Run blocked: preflight gate failed")
        except Exception as exc:
            _log_suppressed(
                "Failed updating status text when preflight blocks run", exc
            )
        return False
    if warnings:
        try:
            app.ui_q.put(("log", "[preflight] " + "; ".join(warnings)))
        except Exception as exc:
            _log_suppressed("Failed writing preflight warnings to UI log queue", exc)
    return True


def run_preflight_check(app) -> None:
    path = getattr(app, "_last_gcode_path", None)
    if not path:
        messagebox.showinfo("Preflight check", "Load a G-code file first.")
        return
    failures, warnings = evaluate_run_preflight(app)
    report = getattr(app, "_gcode_validation_report", None)
    issues: list[str] = []
    if failures:
        issues.extend(failures)
    if warnings:
        issues.extend(warnings)
    if report is not None:
        issues.extend(_format_validation_summary(report))
    if issues:
        title = "Preflight check"
        prefix = "Review before running:\n"
        if failures:
            title = "Preflight check (fail)"
            prefix = "Blocking issues found:\n"
        messagebox.showwarning(
            title,
            prefix + "\n".join(f"- {item}" for item in issues),
        )
        return
    messagebox.showinfo("Preflight check", "No issues detected.")


def _build_performance_report_text(app: Any) -> str:
    def _retention_snapshot_lines() -> list[str]:
        metrics = _runtime_metrics(app)
        if not metrics:
            return []
        formatted = _format_runtime_metrics(metrics, include_samples=False)
        picks = [
            line
            for line in formatted
            if (
                "G-code storage mode:" in line
                or "G-code line-cache policy:" in line
                or "G-code retained footprint:" in line
                or "File-backed source index:" in line
                or "Stream read-ahead footprint:" in line
            )
        ]
        if not picks:
            return []
        return ["", "Retention snapshot:"] + picks

    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        build_report = getattr(perf_monitor, "build_report_snapshot", None)
        if callable(build_report):
            try:
                report = str(build_report() or "").strip()
                if report:
                    extra = _retention_snapshot_lines()
                    if extra:
                        return report + "\n" + "\n".join(extra)
                    return report
            except Exception as exc:
                _log_suppressed(
                    "Failed building performance monitor report snapshot", exc
                )

    lines: list[str] = []
    lines.append("=== Simple Sender Performance Snapshot ===")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    metrics = _runtime_metrics(app)
    if metrics:
        lines.extend(_format_runtime_metrics(metrics, include_samples=False))
    else:
        lines.append("Runtime telemetry unavailable.")
    return "\n".join(lines)


def apply_performance_test_preset(app) -> None:
    try:
        app._status_perf_metrics_enabled = True
    except Exception as exc:
        _log_suppressed(
            "Failed enabling status perf-metric capture in diagnostics preset", exc
        )
    _set_var_value(app, "performance_profile_enabled", True)
    _set_var_value(app, "performance_leak_watch_enabled", False)
    _set_var_value(app, "performance_mode", True)
    _set_var_value(app, "gui_logging_enabled", False)
    _set_var_value(app, "status_poll_interval", PERF_TEST_STATUS_POLL_INTERVAL)

    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        settings["performance_profile_enabled"] = True
        settings["performance_leak_watch_enabled"] = False
        settings["performance_mode"] = True
        settings["gui_logging_enabled"] = False
        settings["status_poll_interval"] = PERF_TEST_STATUS_POLL_INTERVAL

    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed(
                "Failed saving settings for diagnostics perf-test preset", exc
            )
            messagebox.showerror(
                "Diagnostics preset", f"Failed to save settings:\n{exc}"
            )
            return
    try:
        app.ui_q.put(
            ("log", "[diagnostics] Performance test preset applied (restart required).")
        )
    except Exception as exc:
        _log_suppressed("Failed queueing diagnostics preset status log", exc)
    messagebox.showinfo(
        "Diagnostics preset",
        (
            "Performance test preset applied.\n\n"
            "- Runtime performance profiling: ON\n"
            "- Leak-watch snapshots: OFF\n"
            "- Performance mode: ON\n"
            "- GUI logging: OFF\n"
            f"- Status poll interval: {PERF_TEST_STATUS_POLL_INTERVAL:.2f}s\n\n"
            "Restart the app before the next run for clean benchmark numbers."
        ),
    )


def save_performance_report_to_logs(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"simple_sender_performance_report_{timestamp}.txt"
    path = get_log_dir() / filename
    report = _build_performance_report_text(app)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_suppressed("Failed creating logs directory for performance report", exc)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write(report)
            outfile.write("\n")
        messagebox.showinfo("Save performance report", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror(
            "Save performance report", f"Failed to write report:\n{exc}"
        )


def _build_session_diagnostics_lines(app: Any) -> list[str]:
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
    try:
        lines.append(f"Kasa status: {format_kasa_status_line(app)}")
    except Exception as exc:
        _log_suppressed("Failed appending Kasa status line to session diagnostics", exc)
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
    lines.append(f"G-code total lines: {getattr(app, '_gcode_total_lines', 0)}")
    viewer_mode = str(getattr(app, "_gcode_viewer_mode", "") or "").strip()
    if viewer_mode:
        lines.append(f"G-code viewer mode: {viewer_mode}")
        lines.append(
            "G-code viewer policy lines: "
            f"{int(getattr(app, '_gcode_viewer_policy_line_count', 0) or 0)}"
        )
        lines.append(
            "G-code viewer sample lines: "
            f"{int(getattr(app, '_gcode_viewer_sample_line_count', 0) or 0)}"
        )
        lines.append(
            "G-code viewer virtualization threshold: "
            f"{int(getattr(app, '_gcode_viewer_virtualization_threshold', 0) or 0)}"
        )
        lines.append(
            "G-code viewer virtual window: "
            f"{int(getattr(app, '_gcode_viewer_virtual_window', 0) or 0)}"
        )
        chunk_size = getattr(app, "_gcode_viewer_chunk_size", None)
        lines.append(
            "G-code viewer chunk size: "
            + ("n/a" if chunk_size is None else str(int(chunk_size)))
        )
        lines.append(
            "G-code viewer sample_only: "
            f"{bool(getattr(app, '_gcode_viewer_sample_only', False))}"
        )
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip() or "none"
    load_mode = str(getattr(app, "_gcode_load_mode", "") or "").strip() or "n/a"
    index_mode = str(getattr(app, "_gcode_index_mode", "") or "").strip() or "n/a"
    line_count_known = bool(getattr(app, "_gcode_source_line_count_known", True))
    time_to_ready_ms = getattr(app, "_gcode_time_to_stream_ready_ms", None)
    time_to_popup_ms = getattr(app, "_gcode_time_to_popup_close_ms", None)
    cap_lines, cap_profile = _effective_line_cache_cap_lines(app)
    cap_hit = bool(getattr(app, "_gcode_full_line_cache_cap_hit", False))
    sample_cap = int(getattr(app, "_gcode_sample_line_cap", 0) or 0)
    retained_lines = int(getattr(app, "_gcode_retained_line_count", 0) or 0)
    if retained_lines <= 0:
        retained = getattr(app, "_last_gcode_lines", None)
        try:
            retained_lines = int(len(retained)) if retained is not None else 0
        except Exception:
            retained_lines = 0
    viewer_window_lines = _viewer_window_line_count(getattr(app, "gview", None))
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
        "G-code retained lines/window: " f"{retained_lines:,}/{viewer_window_lines:,}"
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
    sample_interval = int(getattr(app, "_gcode_prepare_sample_interval_lines", 0) or 0)
    sample_max = int(getattr(app, "_gcode_prepare_sample_max_lines", 0) or 0)
    prep_exec_total = int(getattr(app, "_gcode_prepare_executable_total_lines", 0) or 0)
    prep_motion_total = int(getattr(app, "_gcode_prepare_motion_total_lines", 0) or 0)
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
    stats_sample_motion = int(getattr(app, "_gcode_stats_sample_motion_lines", 0) or 0)
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
    report_summary = _format_validation_summary(report)
    if report_summary:
        lines.append("Validation summary:")
        lines.extend(f"- {item}" for item in report_summary)
        lines.append("")
    metrics = _runtime_metrics(app)
    if metrics:
        lines.append("Runtime telemetry:")
        lines.extend(
            _format_runtime_metrics(metrics, include_samples=True, sample_limit=20)
        )
        lines.append("")
    decisions = getattr(app, "_preflight_run_decisions", None)
    if isinstance(decisions, list) and decisions:
        lines.append("Preflight run decisions:")
        for entry in decisions[-50:]:
            if not isinstance(entry, dict):
                continue
            stamp = str(entry.get("timestamp", "") or "")
            decision = str(entry.get("decision", "") or "")
            job_name = str(entry.get("job_name", "") or "")
            job_hash = str(entry.get("job_hash", "") or "")
            failure_count = int(entry.get("failure_count", 0) or 0)
            warning_count = int(entry.get("warning_count", 0) or 0)
            summary = (
                f"- {stamp} | decision={decision or 'unknown'}"
                f" | job={job_name or '<unknown>'}"
                f" | hash={job_hash or 'n/a'}"
                f" | failures={failure_count}"
                f" | warnings={warning_count}"
            )
            lines.append(summary)
            failures = entry.get("failures", [])
            if isinstance(failures, list):
                for item in failures:
                    lines.append(f"  FAIL: {item}")
            warnings = entry.get("warnings", [])
            if isinstance(warnings, list):
                for item in warnings:
                    lines.append(f"  WARN: {item}")
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
                stamp = datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
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


def _find_named_log(log_files: list[Path], filename: str) -> Path | None:
    target = str(filename or "").strip().lower()
    if not target:
        return None
    for path in log_files:
        try:
            if path.name.strip().lower() == target:
                return path
        except Exception:
            continue
    return None


def _read_log_tail_text(path: Path, *, max_bytes: int) -> str:
    cap = max(1, int(max_bytes))
    try:
        size = int(path.stat().st_size)
    except Exception:
        return ""
    if size <= 0:
        return ""
    start = max(0, size - cap)
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            raw = handle.read(size - start)
    except Exception as exc:
        _log_suppressed("Failed reading bounded log tail for diagnostics bundle", exc)
        return ""
    if not raw:
        return ""
    if start > 0:
        newline_idx = raw.find(b"\n")
        if newline_idx >= 0:
            raw = raw[newline_idx + 1 :]
    text = raw.decode("utf-8", errors="replace")
    if start <= 0:
        return text
    return (
        f"# BOUNDED TAIL EXPORT (last {cap:,} bytes)\n"
        f"# Source: {path}\n"
        f"# Total file size: {size:,} bytes\n\n" + text
    )


def _parse_serial_log_timestamp(line: str) -> datetime | None:
    text = str(line or "")
    if len(text) < 23:
        return None
    stamp = text[:23]
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        return None


def _build_recent_serial_window_from_log(
    serial_log_path: Path,
    *,
    window_minutes: int = 10,
    max_lines: int = 8000,
    min_lines: int = 1200,
) -> str:
    lines_tail: deque[str] = deque(maxlen=max_lines)
    try:
        with open(serial_log_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                lines_tail.append(raw.rstrip("\r\n"))
    except Exception as exc:
        _log_suppressed(
            "Failed reading serial.log for recent streaming window export", exc
        )
        return ""
    if not lines_tail:
        return ""
    tail_lines = list(lines_tail)
    newest_ts = None
    for line in reversed(tail_lines):
        ts = _parse_serial_log_timestamp(line)
        if ts is not None:
            newest_ts = ts
            break
    if newest_ts is None:
        selected = tail_lines[-min(min_lines, len(tail_lines)) :]
    else:
        cutoff = newest_ts.timestamp() - max(60, int(window_minutes) * 60)
        selected = []
        for line in tail_lines:
            ts = _parse_serial_log_timestamp(line)
            if ts is None:
                continue
            if ts.timestamp() >= cutoff:
                selected.append(line)
        if len(selected) < min_lines:
            selected = tail_lines[-min(min_lines, len(tail_lines)) :]
    if not selected:
        return ""
    header = [
        "# Simple Sender serial activity window",
        f"# Source: {serial_log_path}",
        f"# Exported: {datetime.now().isoformat(timespec='seconds')}",
        f"# Lines: {len(selected)}",
        "",
    ]
    return "\n".join(header + selected) + "\n"


def _collect_streaming_bundle_artifacts(
    runtime_metrics: dict[str, Any],
    log_files: list[Path],
) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    serial_tail = runtime_metrics.get("serial_activity_tail")
    if isinstance(serial_tail, list) and serial_tail:
        artifacts["streaming/serial_activity_tail.json"] = (
            _json_dump(serial_tail) + "\n"
        )
        window_lines = ["# Simple Sender serial activity tail", ""]
        for entry in serial_tail:
            if not isinstance(entry, dict):
                continue
            stamp = str(entry.get("timestamp", "") or "")
            direction = str(entry.get("dir", "") or "")
            line = str(entry.get("line", "") or "")
            window_lines.append(f"{stamp} [{direction}] {line}")
        artifacts["streaming/serial_activity_tail.log"] = "\n".join(window_lines) + "\n"
    serial_log = _find_named_log(log_files, "serial.log")
    if serial_log is not None:
        window_text = _build_recent_serial_window_from_log(serial_log)
        if window_text:
            artifacts["streaming/serial_recent_window.log"] = window_text
    perf_sample_trace = runtime_metrics.get("perf_sample_trace")
    if isinstance(perf_sample_trace, list) and perf_sample_trace:
        artifacts["streaming/perf_sample_trace.json"] = (
            _json_dump(perf_sample_trace) + "\n"
        )
    return artifacts


def _collect_diagnostics_bundle_payload(app: Any) -> dict[str, Any]:
    session_text = "\n".join(_build_session_diagnostics_lines(app)) + "\n"
    perf_text = _build_performance_report_text(app).strip() + "\n"
    runtime_metrics = _runtime_metrics(app)
    runtime_metrics_json = _json_dump(runtime_metrics) + "\n"
    connection_timeline_json = (
        _json_dump(list(getattr(app, "_connection_timeline", []) or [])) + "\n"
    )
    system_info_text = _build_system_info_text(app)
    settings_snapshot_json = _json_dump(getattr(app, "settings", {}) or {}) + "\n"
    settings_path = _resolved_settings_path(app)
    macro_assets = discover_macro_assets(app)
    log_dir = get_log_dir()
    try:
        log_candidates = list(log_dir.iterdir())
    except Exception as exc:
        _log_suppressed("Failed enumerating log files for diagnostics bundle", exc)
        log_candidates = []
    log_files = sorted(
        (
            candidate
            for candidate in log_candidates
            if candidate.is_file()
            and (
                candidate.name.endswith(".log")
                or ".log." in candidate.name
                or candidate.name.startswith("simple_sender_performance_report_")
                or candidate.name.startswith("simple_sender_diagnostics_")
            )
        ),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )[: max(1, int(DIAG_BUNDLE_LOG_MAX_FILES))]
    streaming_artifacts = _collect_streaming_bundle_artifacts(
        runtime_metrics, log_files
    )
    bundle_manifest: dict[str, Any] = {
        "kind": "simple_sender_diagnostics_bundle",
        "created": datetime.now().isoformat(timespec="seconds"),
        "version": str(
            getattr(getattr(app, "version_var", None), "get", lambda: "")() or ""
        ),
        "files": {
            "session_diagnostics": True,
            "performance_report": True,
            "runtime_metrics": True,
            "connection_timeline": True,
            "system_info": True,
            "runtime_settings_snapshot": True,
            "settings_file": bool(settings_path is not None),
            "log_count": 0,
            "macro_asset_count": 0,
            "streaming_artifact_count": len(streaming_artifacts),
        },
    }
    return {
        "session_text": session_text,
        "perf_text": perf_text,
        "runtime_metrics_json": runtime_metrics_json,
        "connection_timeline_json": connection_timeline_json,
        "system_info_text": system_info_text,
        "settings_snapshot_json": settings_snapshot_json,
        "settings_path": settings_path,
        "macro_assets": macro_assets,
        "log_files": log_files,
        "streaming_artifacts": streaming_artifacts,
        "bundle_manifest": bundle_manifest,
    }


def _write_diagnostics_bundle_archive(out_path: Path, payload: dict[str, Any]) -> None:
    bundle_manifest = dict(payload.get("bundle_manifest", {}) or {})
    files_section = dict(bundle_manifest.get("files", {}) or {})
    bundle_manifest["files"] = files_section
    settings_path = payload.get("settings_path")
    macro_assets = list(payload.get("macro_assets", []) or [])
    log_files = list(payload.get("log_files", []) or [])
    streaming_artifacts = dict(payload.get("streaming_artifacts", {}) or {})
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "session_diagnostics.txt", str(payload.get("session_text", ""))
        )
        archive.writestr("performance_report.txt", str(payload.get("perf_text", "")))
        archive.writestr(
            "runtime_metrics.json", str(payload.get("runtime_metrics_json", ""))
        )
        archive.writestr(
            "connection_timeline.json", str(payload.get("connection_timeline_json", ""))
        )
        archive.writestr("system_info.txt", str(payload.get("system_info_text", "")))
        archive.writestr(
            "settings/runtime_settings_snapshot.json",
            str(payload.get("settings_snapshot_json", "")),
        )
        if isinstance(settings_path, Path):
            try:
                archive.write(settings_path, arcname="settings/settings.json")
            except Exception as exc:
                _log_suppressed(
                    "Failed adding settings file to diagnostics bundle", exc
                )
        macro_added = 0
        for source, name in macro_assets:
            try:
                archive.write(source, arcname=f"macros/{os.path.basename(name)}")
                macro_added += 1
            except Exception as exc:
                _log_suppressed(
                    "Failed adding macro/checklist asset to diagnostics bundle", exc
                )
        log_added = 0
        for log_path in log_files:
            try:
                bounded_text = _read_log_tail_text(
                    log_path, max_bytes=DIAG_BUNDLE_LOG_TAIL_MAX_BYTES
                )
                archive.writestr(f"logs/{log_path.name}", bounded_text)
                log_added += 1
            except Exception as exc:
                _log_suppressed("Failed adding log file to diagnostics bundle", exc)
        streaming_added = 0
        for arcname, content in streaming_artifacts.items():
            try:
                archive.writestr(str(arcname), str(content or ""))
                streaming_added += 1
            except Exception as exc:
                _log_suppressed(
                    "Failed adding streaming artifact to diagnostics bundle", exc
                )
        files_section["log_count"] = log_added
        files_section["macro_asset_count"] = macro_added
        files_section["streaming_artifact_count"] = streaming_added
        archive.writestr("manifest.json", _json_dump(bundle_manifest))


def export_diagnostics_bundle(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_bundle_{timestamp}.zip"
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Export diagnostics bundle",
        defaultextension=".zip",
        initialfile=default_name,
        filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
    )
    if not path:
        return
    out_path = Path(path)
    if out_path.parent:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            _log_suppressed(
                "Failed creating export directory for diagnostics bundle", exc
            )

    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed(
                "Failed saving settings before diagnostics-bundle export", exc
            )

    payload = _collect_diagnostics_bundle_payload(app)
    use_background_export = bool(getattr(app, "_diagnostics_bundle_async_export", True))
    if use_background_export and callable(getattr(app, "after", None)):
        if bool(getattr(app, "_diagnostics_bundle_export_inflight", False)):
            messagebox.showinfo(
                "Export diagnostics bundle",
                "A diagnostics bundle export is already running.",
            )
            return
        app._diagnostics_bundle_export_inflight = True
        try:
            app.ui_q.put(("log", "[diagnostics] Exporting diagnostics bundle..."))
        except Exception:
            pass

        def _complete_export(error: Exception | None = None) -> None:
            app._diagnostics_bundle_export_inflight = False
            if error is None:
                messagebox.showinfo(
                    "Export diagnostics bundle", f"Saved to:\n{out_path}"
                )
                return
            messagebox.showerror(
                "Export diagnostics bundle", f"Failed to create bundle:\n{error}"
            )

        after = getattr(app, "after")

        def _export_worker() -> None:
            error: Exception | None = None
            try:
                _write_diagnostics_bundle_archive(out_path, payload)
            except Exception as exc:
                error = exc
            try:
                after(0, lambda: _complete_export(error))
            except Exception as exc:
                _log_suppressed(
                    "Failed posting diagnostics bundle completion callback", exc
                )
                _complete_export(error)

        try:
            worker = threading.Thread(
                target=_export_worker,
                name="diagnostics-bundle-export",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            app._diagnostics_bundle_export_inflight = False
            _log_suppressed("Failed starting diagnostics bundle export thread", exc)

    try:
        _write_diagnostics_bundle_archive(out_path, payload)
        messagebox.showinfo("Export diagnostics bundle", f"Saved to:\n{out_path}")
    except Exception as exc:
        messagebox.showerror(
            "Export diagnostics bundle", f"Failed to create bundle:\n{exc}"
        )


def export_session_diagnostics(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_{timestamp}.txt"
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Export diagnostics",
        defaultextension=".txt",
        initialfile=default_name,
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
    )
    if not path:
        return
    lines = _build_session_diagnostics_lines(app)
    dir_name = os.path.dirname(path)
    if dir_name:
        try:
            os.makedirs(dir_name, exist_ok=True)
        except Exception as exc:
            _log_suppressed(
                "Failed creating export directory for diagnostics report", exc
            )
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write("\n".join(lines))
        messagebox.showinfo("Export diagnostics", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror(
            "Export diagnostics", f"Failed to write diagnostics:\n{exc}"
        )
