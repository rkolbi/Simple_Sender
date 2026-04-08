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
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any, cast

from simple_sender import __version__ as SIMPLE_SENDER_PACKAGE_VERSION
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.kasa_actions import format_kasa_status_line, kasa_status_snapshot
from simple_sender.ui.macro_files import discover_macro_assets
from simple_sender.ui.pi_profile import PI_PROFILE_STATUS_POLL_INTERVAL
from simple_sender.ui.stream_completion import deferred_completion_wait_snapshot
from simple_sender.ui.tk_vars import safe_set_var_attr
from simple_sender.utils.constants import (
    GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT,
    GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER,
)
from simple_sender.utils.logging_config import get_log_dir
from simple_sender.utils.task_timing import record_task_timing
from .popup_utils import center_window
from .diagnostics_bundle import (
    collect_diagnostics_bundle_payload as _collect_diagnostics_bundle_payload_impl,
    write_diagnostics_bundle_archive as _write_diagnostics_bundle_archive_impl,
)
from .diagnostics_bundle_exporter import (
    build_recent_serial_window_from_log as _build_recent_serial_window_from_log_impl,
    collect_streaming_bundle_artifacts as _collect_streaming_bundle_artifacts_impl,
    find_named_log as _find_named_log,
    parse_serial_log_timestamp as _parse_serial_log_timestamp,
    write_bounded_log_tail_chunked as _write_bounded_log_tail_chunked_impl,
)
from .diagnostics_runtime_reporting import (
    format_mb as _format_mb,
    format_runtime_metrics as _format_runtime_metrics_impl,
)
from .diagnostics_report_text import (
    build_performance_report_text as _build_performance_report_text_impl,
)
from .diagnostics_performance_actions import (
    apply_performance_test_preset as _apply_performance_test_preset_impl,
    save_performance_report_to_logs as _save_performance_report_to_logs_impl,
)
from .diagnostics_session_text import (
    build_session_diagnostics_lines as _build_session_diagnostics_lines_impl,
)
from .diagnostics_runtime_display import (
    open_runtime_telemetry as _open_runtime_telemetry_impl,
)
from .diagnostics_checklists import (
    open_release_checklist as _open_release_checklist_impl,
    open_run_checklist as _open_run_checklist_impl,
)
from .diagnostics_report_export import (
    export_session_diagnostics as _export_session_diagnostics_impl,
)
from .diagnostics_bundle_export import (
    export_diagnostics_bundle as _export_diagnostics_bundle_impl,
)
from simple_sender.services.preflight_service import PreflightService

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
RUNTIME_TELEMETRY_REFRESH_MS = 1000
PERF_TEST_STATUS_POLL_INTERVAL = max(1.0, float(PI_PROFILE_STATUS_POLL_INTERVAL))
DIAG_BUNDLE_LOG_MAX_FILES = 12
DIAG_BUNDLE_LOG_TAIL_MAX_BYTES = 512_000
DIAG_BUNDLE_IO_CHUNK_BYTES = 64 * 1024
DIAGNOSTICS_SCHEMA_REV = "2026-03-07-telemetry-r2"


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _set_var_value(app: Any, attr_name: str, value: Any) -> None:
    safe_set_var_attr(app, attr_name, value, log_suppressed=_log_suppressed)


def _json_dump(obj: Any) -> str:
    def _default(value: Any):
        return str(value)

    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=_default)
    except Exception as exc:
        _log_suppressed("Failed serializing diagnostics JSON payload", exc)
        return "{}"


def _collect_build_info(app: Any) -> dict[str, Any]:
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    app_version = str(version_getter() if callable(version_getter) else "").strip()
    info: dict[str, Any] = {
        "schema_rev": DIAGNOSTICS_SCHEMA_REV,
        "package_version": str(SIMPLE_SENDER_PACKAGE_VERSION or "").strip(),
        "app_version": app_version,
        "build_commit": "",
        "build_source": "",
    }
    env_commit = str(os.environ.get("SIMPLE_SENDER_BUILD_COMMIT", "") or "").strip()
    if env_commit:
        info["build_commit"] = env_commit
        info["build_source"] = "env:SIMPLE_SENDER_BUILD_COMMIT"
        return info
    git_exe = str(shutil.which("git") or "").strip()
    if not git_exe:
        return info
    cwd = ""
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if not cwd:
        return info
    try:
        proc = subprocess.run(
            [git_exe, "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
            check=False,
        )
        commit = str(proc.stdout or "").strip()
        if proc.returncode == 0 and commit:
            info["build_commit"] = commit
            info["build_source"] = "git"
    except Exception as exc:
        _log_suppressed("Failed collecting git commit metadata for diagnostics", exc)
    return info


def _build_system_info_text(app: Any, *, build_info: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    lines.append("Simple Sender system snapshot")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    version_text = str(version_getter() if callable(version_getter) else "").strip()
    if version_text:
        lines.append(f"App version: {version_text}")
    if build_info is None:
        build_info = _collect_build_info(app)
    if isinstance(build_info, dict):
        schema_rev = str(build_info.get("schema_rev", "") or "").strip()
        if schema_rev:
            lines.append(f"Diagnostics schema: {schema_rev}")
        package_version = str(build_info.get("package_version", "") or "").strip()
        if package_version:
            lines.append(f"Package version: {package_version}")
        commit = str(build_info.get("build_commit", "") or "").strip()
        source = str(build_info.get("build_source", "") or "").strip()
        if commit:
            lines.append(f"Build commit: {commit}")
        if source:
            lines.append(f"Build source: {source}")
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


def _headless_live_state_line_estimate(gview: Any) -> int:
    if gview is None:
        return 0
    try:
        return int(getattr(gview, "lines_count", 0) or 0)
    except Exception:
        return 0


def _bounded_ssmeta(ssmeta: Any) -> dict[str, str]:
    if not isinstance(ssmeta, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key in sorted(ssmeta.keys())[:64]:
        key = str(raw_key or "").strip()
        if not key:
            continue
        value = str(ssmeta.get(raw_key, "") or "").strip()
        if len(value) > 256:
            value = f"{value[:253]}..."
        out[key] = value
    return out


_DEFAULT_PREFLIGHT_SERVICE = PreflightService()


def _format_validation_summary(report: Any) -> list[str]:
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
    bounds = _DEFAULT_PREFLIGHT_SERVICE.get_bounds(app)
    if bounds is None:
        return None
    return bounds.as_tuple()


def _get_travel_limits(app: Any) -> dict[str, float]:
    return _DEFAULT_PREFLIGHT_SERVICE.get_travel_limits(app).as_dict()


def _runtime_metrics(app: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    metrics["build_info"] = _collect_build_info(app)
    try:
        configured_poll = float(getattr(app, "status_poll_interval").get())
    except Exception:
        configured_poll = 0.0
    if configured_poll > 0.0:
        metrics["status_poll_interval_configured_s"] = float(configured_poll)
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
            if (
                "status_poll_interval_effective_s" not in metrics
                and "status_poll_interval_s" in metrics
            ):
                metrics["status_poll_interval_effective_s"] = float(
                    metrics.get("status_poll_interval_s", 0.0) or 0.0
                )
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
                        "idle_connected",
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
                            "Phase sampling populated for idle-connected and streaming."
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
    gview = getattr(app, "gview", None)
    if gview is not None:
        try:
            metrics["headless_live_state_line_estimate"] = _headless_live_state_line_estimate(gview)
        except Exception:
            metrics["headless_live_state_line_estimate"] = 0
    metrics["live_gcode_past_count"] = int(
        getattr(app, "_live_gcode_past_count", 0) or 0
    )
    metrics["live_gcode_current_count"] = int(
        getattr(app, "_live_gcode_current_count", 0) or 0
    )
    metrics["live_gcode_next_count"] = int(
        getattr(app, "_live_gcode_next_count", 0) or 0
    )
    metrics["live_gcode_pending_depth"] = int(
        getattr(app, "_live_gcode_pending_depth", 0) or 0
    )
    metrics["live_gcode_last_acked_index"] = int(
        getattr(app, "_live_gcode_last_acked_index", -1) or -1
    )
    metrics["live_gcode_last_acked_byte_offset"] = int(
        getattr(app, "_live_gcode_last_acked_byte_offset", 0) or 0
    )
    metrics["last_stream_error_message"] = str(
        getattr(app, "_last_stream_error_message", "") or ""
    )
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
    metrics["last_stream_error_hint"] = str(
        getattr(app, "_last_stream_error_hint", "") or ""
    )
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
        jog_mode = str(getattr(app, "settings", {}).get("jog_dro_smoothing_mode", "off") or "").strip().lower() or "off"
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
    executable_line_count = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    if executable_line_count <= 0:
        executable_line_count = int(
            getattr(app, "_gcode_prepare_executable_total_lines", 0) or 0
        )
    if executable_line_count <= 0:
        executable_line_count = int(getattr(app, "_gcode_total_lines", 0) or 0)
    executable_line_count_known = bool(
        getattr(app, "_gcode_executable_lines_known", False)
    )
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
    stream_file_size_bytes = int(
        getattr(
            app,
            "_stream_progress_file_size_bytes",
            metrics.get("stream_file_size_bytes", 0),
        )
        or 0
    )
    if stream_file_size_bytes <= 0:
        stream_file_size_bytes = int(file_size_bytes)
    acked_byte_offset = int(
        getattr(
            app,
            "_stream_acked_byte_offset",
            metrics.get("acked_byte_offset", 0),
        )
        or 0
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
        getattr(
            app,
            "_stream_progress_pct",
            metrics.get("stream_progress_pct", 0.0),
        )
        or 0.0
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
    metrics["file_size_bytes"] = int(stream_file_size_bytes if stream_file_size_bytes > 0 else file_size_bytes)
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
    metrics["ssmeta_present"] = bool(getattr(app, "_gcode_ssmeta_present", False))
    metrics["gcode_ssmeta"] = _bounded_ssmeta(getattr(app, "_gcode_ssmeta", None))
    metrics["dimensions_source"] = str(
        getattr(app, "_gcode_dimensions_source", "scan") or "scan"
    )
    metrics["units_source"] = str(getattr(app, "_gcode_units_source", "scan") or "scan")
    metrics["ssmeta_scan_reduced"] = bool(
        getattr(app, "_gcode_ssmeta_scan_reduced", False)
    )
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
    return cast(
        list[str],
        _format_runtime_metrics_impl(
            metrics,
            include_samples=include_samples,
            sample_limit=sample_limit,
        ),
    )
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
        ack_last = float(
            metrics.get(
                "ack_latency_ms_last",
                metrics.get("ok_latency_ms_last", 0.0),
            )
            or 0.0
        )
        ack_avg = float(
            metrics.get(
                "ack_latency_ms_avg",
                metrics.get("ok_latency_ms_avg", 0.0),
            )
            or 0.0
        )
        ack_samples = int(
            metrics.get(
                "ack_latency_samples",
                metrics.get("ok_latency_samples", 0),
            )
            or 0
        )
        lines.append(f"- TX lines/sec: {tx_lines_per_sec:.2f}")
        lines.append(
            f"- ACK latency ms: last={ack_last:.2f}, avg={ack_avg:.2f}, samples={ack_samples}"
        )
        configured_poll = float(
            metrics.get("status_poll_interval_configured_s", 0.0) or 0.0
        )
        effective_poll = float(
            metrics.get(
                "status_poll_interval_effective_s",
                metrics.get("status_poll_interval_s", 0.0),
            )
            or 0.0
        )
        if configured_poll > 0.0 and effective_poll > 0.0:
            lines.append(
                "- Status poll interval s: "
                f"configured={configured_poll:.3f}, effective={effective_poll:.3f}"
            )
        elif effective_poll > 0.0:
            lines.append(f"- Status poll interval s: {effective_poll:.3f}")
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
        for phase_name in ("idle_connected", "streaming"):
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
    hidden_contributors = metrics.get("perf_hidden_idle_contributors")
    if isinstance(hidden_contributors, list) and hidden_contributors:
        lines.append("- Hidden idle contributors (top):")
        for entry in hidden_contributors[:5]:
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{str(entry.get('source', '') or 'unknown')}: "
                f"samples={int(entry.get('active_samples', 0) or 0)}, "
                f"cpu_ms={float(entry.get('active_cpu_ms', 0.0) or 0.0):.2f}, "
                f"events={int(entry.get('task_count', 0) or 0)}, "
                f"task_ms={float(entry.get('task_ms', 0.0) or 0.0):.2f}"
            )
    outlier_total = metrics.get("perf_ui_queue_drain_outlier_total")
    outlier_entries = metrics.get("perf_ui_queue_drain_outliers")
    runtime_stall_total = metrics.get("perf_ui_queue_drain_runtime_stall_total")
    runtime_stall_entries = metrics.get("perf_ui_queue_drain_runtime_stalls")
    if isinstance(outlier_total, (int, float)) or (
        isinstance(outlier_entries, list) and outlier_entries
    ):
        lines.append(f"- UI queue outlier ticks captured: {int(outlier_total or 0)}")
    if isinstance(runtime_stall_total, (int, float)) or (
        isinstance(runtime_stall_entries, list) and runtime_stall_entries
    ):
        lines.append(
            f"- UI queue runtime stalls captured: {int(runtime_stall_total or 0)}"
        )
    if isinstance(runtime_stall_entries, list) and runtime_stall_entries:
        lines.append("- UI queue runtime stall details (recent):")
        for entry in runtime_stall_entries[-5:]:
            if not isinstance(entry, dict):
                continue
            tick_ms = float(entry.get("tick_ms", 0.0) or 0.0)
            events = int(entry.get("events_drained", 0) or 0)
            pending_after = int(entry.get("pending_after_tick", 0) or 0)
            slow_kind = str(entry.get("slowest_event_kind", "") or "unknown")
            slow_ms = float(entry.get("slowest_event_ms", 0.0) or 0.0)
            budget_ms = float(entry.get("stall_budget_ms", 0.0) or 0.0)
            lines.append(
                "  "
                f"tick={tick_ms:.2f} ms, budget={budget_ms:.2f} ms, "
                f"events={events}, pending={pending_after}, "
                f"slowest={slow_kind} ({slow_ms:.2f} ms)"
            )
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
    total_lines = int(metrics.get("gcode_total_lines", 0) or 0)
    total_lines_known = bool(metrics.get("gcode_total_lines_known", False))
    executable_lines = int(metrics.get("gcode_executable_lines", 0) or 0)
    executable_lines_known = bool(metrics.get("gcode_executable_lines_known", False))
    motion_lines = int(metrics.get("gcode_motion_lines", 0) or 0)
    motion_lines_known = bool(metrics.get("gcode_motion_lines_known", False))
    ssmeta_present = bool(metrics.get("ssmeta_present", False))
    dimensions_source = str(metrics.get("dimensions_source", "scan") or "scan")
    units_source = str(metrics.get("units_source", "scan") or "scan")
    ssmeta_scan_reduced = bool(metrics.get("ssmeta_scan_reduced", False))
    lines.append(
        "- Quick Assessment: "
        f"quick_scan_ms={quick_scan_ms:.2f}, "
        f"file_size_bytes={file_size_bytes:,}, "
        f"line_count={total_lines:,} ({'known' if total_lines_known else 'estimated'}), "
        f"estimated_job_time_sec={int(estimated_job_time_sec) if estimated_job_time_sec is not None else 'n/a'}, "
        f"estimate_confidence={estimate_confidence}, "
        f"dimensions_confidence={dimensions_confidence}, "
        f"bounds_confidence={bounds_confidence}"
    )
    lines.append(
        "- SSMETA: "
        f"{'present' if ssmeta_present else 'not_found'}, "
        f"dimensions_source={dimensions_source}, "
        f"units_source={units_source}, "
        f"scan_reduced={ssmeta_scan_reduced}"
    )
    lines.append(
        "- Line counters: "
        f"total={total_lines:,} ({'known' if total_lines_known else 'estimated'}), "
        f"executable={executable_lines:,} ({'known' if executable_lines_known else 'estimated'}), "
        f"motion={motion_lines:,} ({'known' if motion_lines_known else 'estimated'})"
    )
    stream_file_size_bytes = int(metrics.get("stream_file_size_bytes", 0) or 0)
    acked_byte_offset = int(metrics.get("acked_byte_offset", 0) or 0)
    stream_progress_pct = float(metrics.get("stream_progress_pct", 0.0) or 0.0)
    stream_done_pending_idle = bool(metrics.get("stream_done_pending_idle", False))
    stream_done_wait_active = bool(metrics.get("stream_done_wait_active", False))
    stream_done_wait_current_s = float(metrics.get("stream_done_wait_current_s", 0.0) or 0.0)
    stream_done_wait_last_s = float(metrics.get("stream_done_wait_last_s", 0.0) or 0.0)
    stream_done_wait_total_s = float(metrics.get("stream_done_wait_total_s", 0.0) or 0.0)
    stream_done_wait_count = int(metrics.get("stream_done_wait_count", 0) or 0)
    lines.append(
        "- Stream byte progress: "
        f"stream_progress_pct={stream_progress_pct:.1f}, "
        f"acked_byte_offset={acked_byte_offset:,}, "
        f"file_size_bytes={stream_file_size_bytes:,}"
    )
    if stream_done_wait_active or stream_done_wait_count > 0:
        lines.append(
            "- Deferred completion wait: "
            f"pending_idle={stream_done_pending_idle}, "
            f"active={stream_done_wait_active}, "
            f"count={stream_done_wait_count}, "
            f"current_s={stream_done_wait_current_s:.3f}, "
            f"last_s={stream_done_wait_last_s:.3f}, "
            f"total_s={stream_done_wait_total_s:.3f}"
        )
    live_past = int(metrics.get("live_gcode_past_count", 0) or 0)
    live_current = int(metrics.get("live_gcode_current_count", 0) or 0)
    live_next = int(metrics.get("live_gcode_next_count", 0) or 0)
    live_pending = int(metrics.get("live_gcode_pending_depth", 0) or 0)
    live_last_idx = int(metrics.get("live_gcode_last_acked_index", -1) or -1)
    live_last_offset = int(metrics.get("live_gcode_last_acked_byte_offset", 0) or 0)
    lines.append(
        "- Headless live G-code state: "
        f"past={live_past}, current={live_current}, next={live_next}, "
        f"pending_depth={live_pending}, last_acked_index={live_last_idx}, "
        f"last_acked_byte_offset={live_last_offset:,}"
    )
    jog_trace_count = int(metrics.get("jog_dro_trace_count", 0) or 0)
    jog_active = bool(metrics.get("jog_dro_interp_active", False))
    jog_stats = metrics.get("jog_dro_interp_stats")
    if isinstance(jog_stats, dict):
        sync_interval_avg_s = float(jog_stats.get("status_sync_interval_avg_s", 0.0) or 0.0)
        sync_interval_max_s = float(jog_stats.get("status_sync_interval_max_s", 0.0) or 0.0)
        horizon_avg_s = float(jog_stats.get("predict_horizon_avg_s", 0.0) or 0.0)
        horizon_max_s = float(jog_stats.get("predict_horizon_max_s", 0.0) or 0.0)
        lines.append(
            "- Jog DRO interpolation: "
            f"active={jog_active}, trace_samples={jog_trace_count}, "
            f"status_sync={int(jog_stats.get('status_sync_count', 0) or 0)}, "
            f"delta_abs_avg=({float(jog_stats.get('delta_abs_avg_x', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_avg_y', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_avg_z', 0.0) or 0.0):.4f}), "
            f"delta_abs_max=({float(jog_stats.get('delta_abs_max_x', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_max_y', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_max_z', 0.0) or 0.0):.4f}), "
            f"sync_interval_avg_s={sync_interval_avg_s:.3f}, "
            f"sync_interval_max_s={sync_interval_max_s:.3f}, "
            f"horizon_avg_s={horizon_avg_s:.3f}, "
            f"horizon_max_s={horizon_max_s:.3f}"
        )
    elif jog_trace_count > 0 or jog_active:
        lines.append(
            "- Jog DRO interpolation: "
            f"active={jog_active}, trace_samples={jog_trace_count}"
        )
    query_interval_avg = float(metrics.get("manual_motion_status_query_interval_avg_ms", 0.0) or 0.0)
    query_interval_max = float(metrics.get("manual_motion_status_query_interval_max_ms", 0.0) or 0.0)
    query_count = int(metrics.get("manual_motion_status_query_count", 0) or 0)
    rx_interval_avg = float(metrics.get("manual_motion_status_rx_interval_avg_ms", 0.0) or 0.0)
    rx_interval_max = float(metrics.get("manual_motion_status_rx_interval_max_ms", 0.0) or 0.0)
    rx_count = int(metrics.get("manual_motion_status_rx_count", 0) or 0)
    if query_count > 0 or rx_count > 0:
        lines.append(
            "- Manual-motion status cadence: "
            f"tx_status_query_interval_ms(avg/max)={query_interval_avg:.2f}/{query_interval_max:.2f} "
            f"(samples={query_count}), "
            f"rx_status_interval_ms(avg/max)={rx_interval_avg:.2f}/{rx_interval_max:.2f} "
            f"(samples={rx_count})"
        )
    session_count = int(metrics.get("manual_motion_status_session_count", 0) or 0)
    if session_count > 0:
        session_active = bool(metrics.get("manual_motion_status_session_active", False))
        last_source = str(
            metrics.get("manual_motion_status_session_last_source", "") or ""
        )
        lines.append(
            "- Manual-motion sessions: "
            f"count={session_count}, active={session_active}, "
            f"last_source={last_source or 'n/a'}"
        )
    status_wait_samples = int(metrics.get("status_wait_sample_count", 0) or 0)
    if status_wait_samples > 0:
        wait_avg_ms = float(metrics.get("status_wait_actual_avg_ms", 0.0) or 0.0)
        wait_requested_avg_ms = float(
            metrics.get("status_wait_requested_avg_ms", 0.0) or 0.0
        )
        wait_overshoot_max_lifetime_ms = float(
            metrics.get("status_wait_overshoot_max_ms", 0.0) or 0.0
        )
        wait_overshoot_recent_ms = float(
            metrics.get("status_wait_overshoot_max_recent_ms", 0.0)
            or wait_overshoot_max_lifetime_ms
        )
        wait_recent_window_s = float(
            metrics.get("status_wait_overshoot_recent_window_s", 60.0) or 60.0
        )
        wait_overshoot_alert_recent = bool(
            metrics.get("status_wait_overshoot_alert_recent", False)
        )
        wait_last_reason = str(metrics.get("status_wait_last_reason", "") or "")
        lines.append(
            "- Status loop wait telemetry: "
            f"requested_avg_ms={wait_requested_avg_ms:.2f}, "
            f"actual_avg_ms={wait_avg_ms:.2f}, "
            f"overshoot_max_recent_ms={wait_overshoot_recent_ms:.2f} "
            f"(window={wait_recent_window_s:.0f}s, lifetime={wait_overshoot_max_lifetime_ms:.2f}), "
            f"overshoot_alert_recent={wait_overshoot_alert_recent}, "
            f"last_reason={wait_last_reason or 'n/a'}, "
            f"samples={status_wait_samples}"
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
    live_state_estimate = metrics.get("headless_live_state_line_estimate")
    retention_parts: list[str] = []
    if retained is not None:
        retention_parts.append(f"retained_lines={int(retained):,}")
    if live_state_estimate is not None:
        retention_parts.append(
            f"headless_live_state_lines_est={int(live_state_estimate):,}"
        )
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
    _open_runtime_telemetry_impl(
        app,
        runtime_metrics=_runtime_metrics,
        format_runtime_metrics=_format_runtime_metrics,
        log_suppressed=_log_suppressed,
        record_task_timing=record_task_timing,
        center_window=center_window,
        refresh_ms=RUNTIME_TELEMETRY_REFRESH_MS,
    )


def open_release_checklist(app: Any) -> None:
    _open_release_checklist_impl(
        app,
        fallback_items=CHECKLIST_ITEMS,
        log_suppressed=_log_suppressed,
    )


def open_run_checklist(app: Any) -> None:
    _open_run_checklist_impl(
        app,
        fallback_items=RUN_CHECKLIST_ITEMS,
        log_suppressed=_log_suppressed,
    )


def evaluate_run_preflight(app: Any) -> tuple[list[str], list[str]]:
    result = PreflightService(
        get_bounds=_get_bounds,
        get_travel_limits=_get_travel_limits,
    ).validate_job(app)
    return list(result.failures), list(result.warnings)


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
    return cast(
        str,
        _build_performance_report_text_impl(
            app,
            runtime_metrics=_runtime_metrics,
            format_runtime_metrics=_format_runtime_metrics,
            log_suppressed=_log_suppressed,
        ),
    )


def apply_performance_test_preset(app) -> None:
    _apply_performance_test_preset_impl(
        app,
        set_var_value=_set_var_value,
        log_suppressed=_log_suppressed,
        showinfo=messagebox.showinfo,
        showerror=messagebox.showerror,
        perf_test_status_poll_interval=PERF_TEST_STATUS_POLL_INTERVAL,
    )


def save_performance_report_to_logs(app) -> None:
    _save_performance_report_to_logs_impl(
        app,
        get_log_dir=get_log_dir,
        build_performance_report_text=_build_performance_report_text,
        log_suppressed=_log_suppressed,
        showinfo=messagebox.showinfo,
        showerror=messagebox.showerror,
    )


def _build_session_diagnostics_lines(app: Any) -> list[str]:
    return cast(
        list[str],
        _build_session_diagnostics_lines_impl(
            app,
            format_kasa_status_line=format_kasa_status_line,
            log_suppressed=_log_suppressed,
            effective_line_cache_cap_lines=_effective_line_cache_cap_lines,
            headless_live_state_line_estimate=_headless_live_state_line_estimate,
            format_validation_summary=_format_validation_summary,
            runtime_metrics=_runtime_metrics,
            format_runtime_metrics=_format_runtime_metrics,
        ),
    )

def _write_bounded_log_tail_chunked(
    archive: zipfile.ZipFile,
    *,
    arcname: str,
    path: Path,
    max_bytes: int,
    chunk_bytes: int = DIAG_BUNDLE_IO_CHUNK_BYTES,
) -> None:
    _write_bounded_log_tail_chunked_impl(
        archive,
        arcname=arcname,
        path=path,
        max_bytes=max_bytes,
        chunk_bytes=chunk_bytes,
        log_suppressed=_log_suppressed,
    )


def _build_recent_serial_window_from_log(
    serial_log_path: Path,
    *,
    window_minutes: int = 10,
    max_lines: int = 8000,
    min_lines: int = 1200,
) -> str:
    return cast(
        str,
        _build_recent_serial_window_from_log_impl(
            serial_log_path,
            window_minutes=window_minutes,
            max_lines=max_lines,
            min_lines=min_lines,
            log_suppressed=_log_suppressed,
        ),
    )


def _collect_streaming_bundle_artifacts(
    runtime_metrics: dict[str, Any],
    log_files: list[Path],
) -> dict[str, str]:
    return cast(
        dict[str, str],
        _collect_streaming_bundle_artifacts_impl(
            runtime_metrics,
            log_files,
            json_dump=_json_dump,
            log_suppressed=_log_suppressed,
        ),
    )


def _collect_diagnostics_bundle_payload(app: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _collect_diagnostics_bundle_payload_impl(
            app,
            collect_build_info=_collect_build_info,
            build_session_diagnostics_lines=_build_session_diagnostics_lines,
            build_performance_report_text=_build_performance_report_text,
            runtime_metrics=_runtime_metrics,
            json_dump=_json_dump,
            build_system_info_text=_build_system_info_text,
            resolved_settings_path=_resolved_settings_path,
            discover_macro_assets=discover_macro_assets,
            get_log_dir=get_log_dir,
            collect_streaming_bundle_artifacts=_collect_streaming_bundle_artifacts,
            log_suppressed=_log_suppressed,
            bundle_log_max_files=DIAG_BUNDLE_LOG_MAX_FILES,
        ),
    )


def _write_diagnostics_bundle_archive(out_path: Path, payload: dict[str, Any]) -> None:
    _write_diagnostics_bundle_archive_impl(
        out_path,
        payload,
        json_dump=_json_dump,
        write_bounded_log_tail_chunked=_write_bounded_log_tail_chunked,
        log_suppressed=_log_suppressed,
        bundle_log_tail_max_bytes=DIAG_BUNDLE_LOG_TAIL_MAX_BYTES,
    )


def export_diagnostics_bundle(app) -> None:
    _export_diagnostics_bundle_impl(
        app,
        run_file_dialog=run_file_dialog,
        asksaveasfilename=filedialog.asksaveasfilename,
        collect_diagnostics_bundle_payload=_collect_diagnostics_bundle_payload,
        write_diagnostics_bundle_archive=_write_diagnostics_bundle_archive,
        log_suppressed=_log_suppressed,
        showinfo=messagebox.showinfo,
        showerror=messagebox.showerror,
        thread_cls=threading.Thread,
    )


def export_session_diagnostics(app) -> None:
    _export_session_diagnostics_impl(
        app,
        run_file_dialog=run_file_dialog,
        asksaveasfilename=filedialog.asksaveasfilename,
        build_session_diagnostics_lines=_build_session_diagnostics_lines,
        log_suppressed=_log_suppressed,
        showinfo=messagebox.showinfo,
        showerror=messagebox.showerror,
        thread_cls=threading.Thread,
    )

