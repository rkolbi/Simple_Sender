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

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import logging
import math
import os
import threading
import time
from tkinter import filedialog, messagebox
from typing import Any

from simple_sender.gcode_parser import WORD_PAT, clean_gcode_line
from simple_sender.gcode_validator import (
    DETAIL_LINE_LIMIT,
    GcodeValidationLineIssue,
    GcodeValidationReport,
    GRBL_WARN_G_CODES,
    KNOWN_WORD_LETTERS,
    MAX_LINE_LENGTH,
    MODAL_HAZARDS,
    SUPPORTED_G_CODES,
    SUPPORTED_M_CODES,
    UNSUPPORTED_AXES,
)

logger = logging.getLogger(__name__)

_VALIDATION_PROGRESS_INTERVAL_S = 0.2
_QUICK_VALIDATION_LINE_LIMIT = 200_000
_WORD_FLOAT_PAT = WORD_PAT
_AXIS_WORDS = ("X", "Y", "Z")


@dataclass(slots=True)
class _ValidationScanResult:
    status: str
    path: str
    strict: bool
    stop_on_first_error: bool
    elapsed_ms: float
    file_size_bytes: int
    bytes_processed: int
    total_lines: int
    executable_lines: int
    motion_lines: int
    line_count_known: bool
    report: GcodeValidationReport
    issues: list[tuple[int, str]]
    warnings: list[str]
    errors: int
    warning_count: int
    estimated_job_time_sec: int | None
    estimate_confidence: str
    estimate_confidence_reasons: dict[str, bool]
    estimate_inputs_snapshot: dict[str, Any]
    bounds_box: dict[str, float] | None
    dimensions_confidence: str
    dimensions_confidence_reasons: dict[str, bool]
    autolevel_prereq_snapshot: dict[str, Any]


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_bool_setting(app: Any, attr_name: str, default: bool = False) -> bool:
    raw = getattr(app, attr_name, None)
    if raw is None:
        return bool(default)
    getter = getattr(raw, "get", None)
    if callable(getter):
        try:
            raw = getter()
        except Exception:
            return bool(default)
    return bool(raw)


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


def _resolve_rate_tuple(
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
    get_rapid = getattr(app, "_get_rapid_rates_for_estimate", None)
    if callable(get_rapid):
        try:
            rates = get_rapid()
            if isinstance(rates, tuple) and len(rates) == 3:
                rx = float(rates[0])
                ry = float(rates[1])
                rz = float(rates[2])
                if rx > 0.0 and ry > 0.0 and rz > 0.0:
                    return ((rx, ry, rz), "estimate")
        except Exception:
            pass
    fallback_getter = getattr(app, "_get_fallback_rapid_rate", None)
    if callable(fallback_getter):
        try:
            fallback = float(fallback_getter() or 0.0)
            if fallback > 0.0:
                return ((fallback, fallback, fallback), "fallback")
        except Exception:
            pass
    return ((2000.0, 2000.0, 500.0), "default")


def _is_motion_line(line: str) -> bool:
    text = str(line or "").strip().upper()
    if not text:
        return False
    if any(token in text for token in ("G0", "G1", "G2", "G3")):
        return True
    return any(axis in text for axis in ("X", "Y", "Z"))


def _format_code(letter: str, code: float) -> str:
    if abs(code - round(code)) < 1e-9:
        return f"{letter}{int(round(code))}"
    return f"{letter}{code:g}"


def _append_issue(issues: list[str], seen: set[str], message: str) -> None:
    if message in seen:
        return
    seen.add(message)
    issues.append(message)


def _current_job_path(app: Any) -> str:
    path = str(getattr(app, "_last_gcode_path", "") or "").strip()
    if path:
        return path
    source = getattr(app, "_gcode_source", None)
    return str(getattr(source, "path", "") or "").strip()


def _build_report_text(result: _ValidationScanResult) -> str:
    summary = [
        "Overdrive Validation Report",
        f"File: {os.path.basename(result.path)}",
        f"Path: {result.path}",
        f"Mode: {'strict' if result.strict else 'quick'}",
        f"Stop on first error: {result.stop_on_first_error}",
        f"Status: {result.status}",
        f"Elapsed: {result.elapsed_ms / 1000.0:.2f}s",
        f"Errors: {result.errors} | Warnings: {result.warning_count}",
        f"Lines: total={result.total_lines:,} executable={result.executable_lines:,} motion={result.motion_lines:,}",
        "",
    ]
    if result.issues:
        summary.append("Issues:")
        for line_no, issue in result.issues:
            summary.append(f"L{line_no}: {issue}")
    elif result.warnings:
        summary.append("Warnings:")
        for warning in result.warnings:
            summary.append(f"- {warning}")
    else:
        summary.append("No issues detected.")
    return "\n".join(summary)

def _set_text_if_changed(var: Any, value: str) -> None:
    if var is None:
        return
    getter = getattr(var, "get", None)
    setter = getattr(var, "set", None)
    if not callable(setter):
        return
    text = str(value or "")
    current = None
    if callable(getter):
        try:
            current = str(getter() or "")
        except Exception:
            current = None
    if current == text:
        return
    try:
        setter(text)
    except Exception:
        return


def _set_widget_state(widget: Any, state: str) -> None:
    if widget is None:
        return
    try:
        widget.config(state=state)
    except Exception:
        return


def _set_progress_if_changed(app: Any, pct: float) -> None:
    pct = max(0.0, min(100.0, float(pct)))
    last = float(getattr(app, "_overdrive_validation_last_progress_pct", -1.0))
    if abs(last - pct) < 0.05:
        return
    app._overdrive_validation_last_progress_pct = pct
    var = getattr(app, "overdrive_validation_progress_pct", None)
    if var is not None:
        try:
            var.set(int(round(pct)))
        except Exception:
            pass
    bar = getattr(app, "overdrive_validation_progress_bar", None)
    if bar is not None:
        try:
            bar.configure(value=float(pct), mode="determinate")
        except Exception:
            pass


def _post_ui(app: Any, callback, *args, **kwargs) -> None:
    try:
        app.ui_q.put(("ui_post", callback, args, kwargs))
    except Exception as exc:
        logger.debug("Failed queueing overdrive UI update: %s", exc, exc_info=exc)


def _apply_validation_metrics_to_job(app: Any, result: _ValidationScanResult) -> None:
    current_path = _current_job_path(app)
    if not current_path or os.path.normcase(current_path) != os.path.normcase(result.path):
        return

    app._gcode_file_size_bytes = int(max(0, result.file_size_bytes))
    app._gcode_file_line_count = int(max(0, result.total_lines))
    app._gcode_file_line_count_known = bool(result.line_count_known)

    app._gcode_total_lines = int(max(0, result.total_lines))
    app._gcode_total_lines_known = bool(result.line_count_known)
    app._gcode_executable_lines = int(max(0, result.executable_lines))
    app._gcode_executable_lines_known = bool(result.line_count_known)
    app._gcode_motion_lines = int(max(0, result.motion_lines))
    app._gcode_motion_lines_known = bool(result.line_count_known)

    app._gcode_prepare_executable_total_lines = int(max(0, result.executable_lines))
    app._gcode_prepare_motion_total_lines = int(max(0, result.motion_lines))

    if isinstance(result.bounds_box, dict):
        app._gcode_bounds_box = dict(result.bounds_box)
    app._gcode_bounds_confidence = str(result.dimensions_confidence)
    app._gcode_dimensions_confidence = str(result.dimensions_confidence)
    app._gcode_dimensions_confidence_reasons = dict(result.dimensions_confidence_reasons)

    app._gcode_estimated_job_time_sec = (
        int(result.estimated_job_time_sec)
        if result.estimated_job_time_sec is not None
        else None
    )
    app._gcode_estimate_confidence = str(result.estimate_confidence)
    app._gcode_estimate_confidence_reasons = dict(result.estimate_confidence_reasons)
    app._estimate_confidence = str(result.estimate_confidence)
    if result.estimated_job_time_sec is not None:
        app._loaded_estimate_total_min = float(result.estimated_job_time_sec) / 60.0
        app._loaded_estimate_source = "validation"
        app._gcode_estimate_replaced_quick = True
    app._estimate_inputs_snapshot = dict(result.estimate_inputs_snapshot)

    app._auto_level_prereq_snapshot = dict(result.autolevel_prereq_snapshot)
    app._auto_level_job_source_path = str(result.path)
    app._auto_level_job_total_lines = int(max(0, result.executable_lines))
    app._auto_level_job_hash = str(getattr(app, "_gcode_hash", "") or "")

    refresh = getattr(app, "_refresh_gcode_stats_display", None)
    if callable(refresh):
        try:
            refresh()
        except Exception as exc:
            logger.debug(
                "Failed refreshing G-code status after validation", exc_info=exc
            )


def _render_result_panel(app: Any, text: str) -> None:
    widget = getattr(app, "overdrive_validation_results_text", None)
    if widget is None:
        return
    try:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
    except Exception:
        return


def _refresh_controls_state(app: Any) -> None:
    running = bool(getattr(app, "_overdrive_validation_running", False))
    _set_widget_state(
        getattr(app, "btn_overdrive_validate", None),
        "disabled" if running else "normal",
    )
    _set_widget_state(
        getattr(app, "btn_overdrive_validate_cancel", None),
        "normal" if running else "disabled",
    )
    has_result = bool(getattr(app, "_overdrive_validation_last_result", None))
    _set_widget_state(
        getattr(app, "btn_overdrive_validate_export", None),
        "normal" if has_result else "disabled",
    )
    _set_widget_state(
        getattr(app, "btn_overdrive_validate_copy", None),
        "normal" if has_result else "disabled",
    )


def _apply_progress_ui(
    app: Any,
    run_id: int,
    *,
    bytes_processed: int,
    file_size_bytes: int,
    stage: str,
    errors: int,
    warnings: int,
) -> None:
    if int(run_id) != int(getattr(app, "_overdrive_validation_run_id", 0) or 0):
        return
    if not bool(getattr(app, "_overdrive_validation_running", False)):
        return

    if int(file_size_bytes) > 0:
        pct = (float(max(0, bytes_processed)) / float(file_size_bytes)) * 100.0
        _set_progress_if_changed(app, pct)
        status_text = f"Running... {pct:0.1f}% ({stage}) E:{errors} W:{warnings}"
    else:
        bar = getattr(app, "overdrive_validation_progress_bar", None)
        if bar is not None:
            try:
                bar.configure(mode="indeterminate")
                bar.start(25)
            except Exception:
                pass
        status_text = f"Running... ({stage}) E:{errors} W:{warnings}"
    _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), status_text)


def _apply_complete_ui(app: Any, run_id: int, result: _ValidationScanResult) -> None:
    if int(run_id) != int(getattr(app, "_overdrive_validation_run_id", 0) or 0):
        return

    app._overdrive_validation_running = False
    app._overdrive_validation_cancel_event = None
    app._overdrive_validation_worker = None

    bar = getattr(app, "overdrive_validation_progress_bar", None)
    if bar is not None:
        try:
            bar.stop()
        except Exception:
            pass

    if result.status == "done":
        _set_progress_if_changed(app, 100.0)
        _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), "Done")
    elif result.status == "canceled":
        _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), "Canceled")
    else:
        _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), "Failed")

    summary = (
        f"File: {os.path.basename(result.path)} | "
        f"Mode: {'strict' if result.strict else 'quick'} | "
        f"Errors: {result.errors} | Warnings: {result.warning_count} | "
        f"Elapsed: {result.elapsed_ms / 1000.0:.2f}s"
    )
    _set_text_if_changed(getattr(app, "overdrive_validation_summary_var", None), summary)

    report_text = _build_report_text(result)
    app._overdrive_validation_last_report_text = report_text
    app._overdrive_validation_last_result = result
    _render_result_panel(app, report_text)

    app._gcode_validation_report = result.report
    if result.status == "done":
        _apply_validation_metrics_to_job(app, result)

    app._last_validation_run = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": str(result.status),
        "mode": "strict" if bool(result.strict) else "quick",
        "stop_on_first_error": bool(result.stop_on_first_error),
        "errors": int(result.errors),
        "warnings": int(result.warning_count),
        "duration_ms": float(result.elapsed_ms),
        "file": os.path.basename(result.path),
        "file_size_bytes": int(result.file_size_bytes),
        "bytes_processed": int(result.bytes_processed),
        "metrics_upgraded": bool(result.status == "done"),
    }

    try:
        app.ui_q.put(
            (
                "log",
                "[overdrive] validation complete: "
                f"status={result.status} mode={'strict' if result.strict else 'quick'} "
                f"errors={result.errors} warnings={result.warning_count} "
                f"elapsed_ms={result.elapsed_ms:.1f}",
            )
        )
    except Exception:
        pass

    _refresh_controls_state(app)


def _run_validation_scan(
    app: Any,
    *,
    path: str,
    strict: bool,
    stop_on_first_error: bool,
    run_id: int,
    cancel_event: threading.Event,
) -> _ValidationScanResult:
    started_at = time.perf_counter()
    file_size_bytes = 0
    try:
        file_size_bytes = int(os.path.getsize(path))
    except OSError:
        file_size_bytes = 0

    long_lines: list[tuple[int, int]] = []
    long_line_count = 0
    unsupported_axes: Counter[str] = Counter()
    unsupported_words: Counter[str] = Counter()
    unsupported_g_codes: Counter[str] = Counter()
    unsupported_m_codes: Counter[str] = Counter()
    grbl_warnings: Counter[str] = Counter()
    modal_hazards: set[str] = set()
    line_issues: list[GcodeValidationLineIssue] = []
    line_issue_count = 0
    line_issues_truncated = False
    issues_for_ui: list[tuple[int, str]] = []

    total_lines = 0
    executable_lines = 0
    motion_lines = 0
    bytes_processed = 0
    reached_eof = False
    canceled = False
    quick_limit_hit = False
    saw_spindle_command = False

    motion_settings = _capture_motion_settings_snapshot(app)
    rapid_rates, rate_source = _resolve_rate_tuple(app, motion_settings)
    has_motion_settings = _has_complete_motion_settings(motion_settings)

    units_scale = 1.0
    distance_mode = "absolute"
    motion_mode = 1
    feed_mm_min = max(50.0, min(rapid_rates))

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
    sample_motion_distance_mm = 0.0
    sample_rapid_distance_mm = 0.0
    sample_motion_time_min = 0.0
    sample_rapid_time_min = 0.0

    last_progress_ts = 0.0

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        while True:
            if cancel_event.is_set():
                canceled = True
                break
            raw_line = handle.readline()
            if not raw_line:
                reached_eof = True
                break
            total_lines += 1
            try:
                bytes_processed = int(handle.tell())
            except Exception:
                bytes_processed = max(0, bytes_processed)

            cleaned = str(clean_gcode_line(raw_line) or "")
            if not cleaned:
                now = time.monotonic()
                if now - last_progress_ts >= _VALIDATION_PROGRESS_INTERVAL_S:
                    _post_ui(
                        app,
                        _apply_progress_ui,
                        app,
                        int(run_id),
                        bytes_processed=int(bytes_processed),
                        file_size_bytes=int(file_size_bytes),
                        stage="scan",
                        errors=int(line_issue_count),
                        warnings=int(sum(grbl_warnings.values()) + len(modal_hazards)),
                    )
                    last_progress_ts = now
                continue

            executable_lines += 1
            if _is_motion_line(cleaned):
                motion_lines += 1

            line = cleaned.strip().upper()
            if "M3" in line or "M4" in line or "M5" in line:
                saw_spindle_command = True

            line_len = len(cleaned.encode("utf-8")) + 1
            issues: list[str] = []
            seen_issue: set[str] = set()

            if line_len > MAX_LINE_LENGTH:
                long_line_count += 1
                if len(long_lines) < 5:
                    long_lines.append((total_lines, line_len))
                _append_issue(issues, seen_issue, f"Long line ({line_len} bytes)")

            if line.startswith("$"):
                _append_issue(issues, seen_issue, "Illegal GRBL system command in job ($...)")

            words = _WORD_FLOAT_PAT.findall(line)
            words_map: dict[str, float] = {}
            for letter, value in words:
                val = _safe_float(value)
                if val is None:
                    continue
                key = str(letter).upper()
                words_map[key] = float(val)
                if key in UNSUPPORTED_AXES:
                    unsupported_axes[key] += 1
                    _append_issue(issues, seen_issue, f"Unsupported axis {key}")
                if key not in KNOWN_WORD_LETTERS:
                    unsupported_words[key] += 1
                    _append_issue(issues, seen_issue, f"Unknown word letter {key}")
                if key == "G":
                    code = round(float(val), 3)
                    if code in MODAL_HAZARDS:
                        modal_hazards.add(MODAL_HAZARDS[code])
                    if code in GRBL_WARN_G_CODES:
                        grbl_warnings[GRBL_WARN_G_CODES[code]] += 1
                    if code not in SUPPORTED_G_CODES:
                        code_label = _format_code("G", code)
                        unsupported_g_codes[code_label] += 1
                        _append_issue(issues, seen_issue, f"Unsupported G-code {code_label}")
                elif key == "M":
                    code = float(val)
                    if abs(code - round(code)) > 1e-6:
                        code_label = f"M{value}"
                        unsupported_m_codes[code_label] += 1
                        _append_issue(issues, seen_issue, f"Unsupported M-code {code_label}")
                    else:
                        code_int = int(round(code))
                        if code_int not in SUPPORTED_M_CODES:
                            code_label = f"M{code_int}"
                            unsupported_m_codes[code_label] += 1
                            _append_issue(issues, seen_issue, f"Unsupported M-code {code_label}")

            if "F" in words_map and words_map["F"] > 0.0:
                feed_mm_min = max(1.0, float(words_map["F"]) * units_scale)
            if "G20" in line:
                units_scale = 25.4
            elif "G21" in line:
                units_scale = 1.0
            if "G90" in line:
                distance_mode = "absolute"
            elif "G91" in line:
                distance_mode = "relative"
            if "G0" in line:
                motion_mode = 0
            elif "G1" in line:
                motion_mode = 1
            elif "G2" in line:
                motion_mode = 2
            elif "G3" in line:
                motion_mode = 3

            prev_x, prev_y, prev_z = x, y, z
            moved = False
            for axis_name in _AXIS_WORDS:
                if axis_name not in words_map:
                    continue
                moved = True
                val_mm = float(words_map[axis_name]) * units_scale
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
            if moved:
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
                if dist_mm > 0.0:
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

            if issues:
                line_issue_count += 1
                if len(line_issues) < DETAIL_LINE_LIMIT:
                    line_issues.append(
                        GcodeValidationLineIssue(
                            total_lines,
                            cleaned,
                            tuple(issues),
                        )
                    )
                else:
                    line_issues_truncated = True
                for issue in issues:
                    if len(issues_for_ui) < 500:
                        issues_for_ui.append((int(total_lines), str(issue)))
                if stop_on_first_error:
                    break

            if (not strict) and executable_lines >= _QUICK_VALIDATION_LINE_LIMIT:
                quick_limit_hit = True
                break

            now = time.monotonic()
            if now - last_progress_ts >= _VALIDATION_PROGRESS_INTERVAL_S:
                _post_ui(
                    app,
                    _apply_progress_ui,
                    app,
                    int(run_id),
                    bytes_processed=int(bytes_processed),
                    file_size_bytes=int(file_size_bytes),
                    stage="scan",
                    errors=int(line_issue_count),
                    warnings=int(sum(grbl_warnings.values()) + len(modal_hazards)),
                )
                last_progress_ts = now

    if not saw_spindle_command:
        warning = "No spindle command (M3/M4/M5) detected."
        grbl_warnings[warning] += 1

    bounds_box = None
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

    line_count_known = bool(reached_eof and not canceled and not quick_limit_hit)
    dimensions_confidence = (
        "confident" if (line_count_known and bounds_box is not None) else "rough"
    )
    dimensions_confidence_reasons = {
        "sampled_scan": not bool(line_count_known),
        "scan_incomplete": not bool(line_count_known),
        "no_motion_lines_found": not bool(bounds_box),
    }

    total_time_min = max(0.0, sample_motion_time_min + sample_rapid_time_min)
    estimated_job_time_sec = int(round(total_time_min * 60.0)) if total_time_min > 0.0 else None
    estimate_confidence = (
        "confident"
        if (line_count_known and has_motion_settings and motion_lines > 0)
        else "provisional"
    )
    estimate_confidence_reasons = {
        "missing_grbl_settings": not bool(has_motion_settings),
        "sampled_scan": not bool(line_count_known),
        "scan_incomplete": not bool(line_count_known),
        "no_motion_lines_found": motion_lines <= 0,
    }

    now_ts = float(time.time())
    estimate_inputs_snapshot = {
        "captured_at_ts": now_ts,
        "capture_stage": "validation",
        "gcode_hash": str(getattr(app, "_gcode_hash", "") or ""),
        "stats_mode": "validation_strict" if strict else "validation_quick",
        "stats_sample_scale": 1.0,
        "stats_sample_line_count": int(executable_lines),
        "stats_sample_total_lines": int(total_lines),
        "stats_sample_executable_lines": int(executable_lines),
        "stats_sample_motion_lines": int(motion_lines),
        "stats_executable_total_lines": int(executable_lines),
        "stats_motion_total_lines": int(motion_lines),
        "rate_source": str(rate_source),
        "estimate_confidence": str(estimate_confidence),
        "estimated_job_time_sec": int(estimated_job_time_sec) if estimated_job_time_sec is not None else None,
        "grbl_motion_settings": dict(motion_settings),
        "rapid_rates_mm_min": (
            float(rapid_rates[0]),
            float(rapid_rates[1]),
            float(rapid_rates[2]),
        ),
        "sample_motion_distance_mm": float(max(0.0, sample_motion_distance_mm)),
        "sample_rapid_distance_mm": float(max(0.0, sample_rapid_distance_mm)),
        "sample_motion_time_min": float(max(0.0, sample_motion_time_min)),
        "sample_rapid_time_min": float(max(0.0, sample_rapid_time_min)),
    }

    autolevel_prereq_snapshot = {
        "stage": "validation",
        "prepared_at_ts": now_ts,
        "source_path": str(path),
        "source_exists": bool(path and os.path.isfile(path)),
        "source_hash": str(getattr(app, "_gcode_hash", "") or ""),
        "source_total_lines": int(max(0, executable_lines)),
        "bounds_ready": bool(bounds_box is not None),
        "bounds_confidence": str(dimensions_confidence),
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

    report = GcodeValidationReport(
        total_lines=int(total_lines),
        long_line_count=int(long_line_count),
        long_lines=list(long_lines),
        unsupported_axes=unsupported_axes,
        unsupported_words=unsupported_words,
        unsupported_g_codes=unsupported_g_codes,
        unsupported_m_codes=unsupported_m_codes,
        grbl_warnings=grbl_warnings,
        modal_hazards=modal_hazards,
        line_issue_count=int(line_issue_count),
        line_issues=line_issues,
        line_issues_truncated=bool(line_issues_truncated),
    )

    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    status = "canceled" if canceled else "done"
    if not canceled and not reached_eof and strict:
        status = "failed"

    warnings_text = [f"{msg} ({count})" for msg, count in grbl_warnings.most_common(20)]

    return _ValidationScanResult(
        status=status,
        path=str(path),
        strict=bool(strict),
        stop_on_first_error=bool(stop_on_first_error),
        elapsed_ms=float(elapsed_ms),
        file_size_bytes=int(max(0, file_size_bytes)),
        bytes_processed=int(max(0, bytes_processed)),
        total_lines=int(max(0, total_lines)),
        executable_lines=int(max(0, executable_lines)),
        motion_lines=int(max(0, motion_lines)),
        line_count_known=bool(line_count_known),
        report=report,
        issues=list(issues_for_ui),
        warnings=warnings_text,
        errors=int(line_issue_count),
        warning_count=int(sum(grbl_warnings.values()) + len(modal_hazards)),
        estimated_job_time_sec=estimated_job_time_sec,
        estimate_confidence=estimate_confidence,
        estimate_confidence_reasons=estimate_confidence_reasons,
        estimate_inputs_snapshot=estimate_inputs_snapshot,
        bounds_box=bounds_box,
        dimensions_confidence=dimensions_confidence,
        dimensions_confidence_reasons=dimensions_confidence_reasons,
        autolevel_prereq_snapshot=autolevel_prereq_snapshot,
    )

def start_validation(app: Any) -> None:
    if bool(getattr(app, "_overdrive_validation_running", False)):
        return
    path = _current_job_path(app)
    if not path or not os.path.isfile(path):
        messagebox.showwarning("Validate G-code", "Load a G-code job first.")
        return

    strict = _safe_bool_setting(app, "validate_streaming_gcode", default=False)
    stop_on_first = _safe_bool_setting(
        app,
        "overdrive_validation_stop_on_first_error",
        default=False,
    )

    run_id = int(getattr(app, "_overdrive_validation_run_id", 0) or 0) + 1
    app._overdrive_validation_run_id = run_id
    app._overdrive_validation_running = True
    app._overdrive_validation_cancel_event = threading.Event()
    app._overdrive_validation_last_progress_pct = 0.0

    _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), "Starting...")
    _set_text_if_changed(getattr(app, "overdrive_validation_summary_var", None), "")
    app._overdrive_validation_last_result = None
    app._overdrive_validation_last_report_text = ""
    _render_result_panel(app, "")
    _refresh_controls_state(app)
    _set_progress_if_changed(app, 0.0)

    try:
        app.ui_q.put(
            (
                "log",
                f"[overdrive] validation started: mode={'strict' if strict else 'quick'} "
                f"stop_on_first_error={stop_on_first} file={os.path.basename(path)}",
            )
        )
    except Exception:
        pass

    def _worker() -> None:
        try:
            result = _run_validation_scan(
                app,
                path=path,
                strict=strict,
                stop_on_first_error=stop_on_first,
                run_id=run_id,
                cancel_event=app._overdrive_validation_cancel_event,
            )
        except Exception as exc:
            logger.exception("Overdrive validation failed")
            result = _ValidationScanResult(
                status="failed",
                path=str(path),
                strict=bool(strict),
                stop_on_first_error=bool(stop_on_first),
                elapsed_ms=0.0,
                file_size_bytes=0,
                bytes_processed=0,
                total_lines=0,
                executable_lines=0,
                motion_lines=0,
                line_count_known=False,
                report=GcodeValidationReport(
                    total_lines=0,
                    long_line_count=0,
                    long_lines=[],
                    unsupported_axes=Counter(),
                    unsupported_words=Counter(),
                    unsupported_g_codes=Counter(),
                    unsupported_m_codes=Counter(),
                    grbl_warnings=Counter(),
                    modal_hazards=set(),
                    line_issue_count=0,
                    line_issues=[],
                    line_issues_truncated=False,
                ),
                issues=[(0, f"Validation failed: {exc}")],
                warnings=[],
                errors=1,
                warning_count=0,
                estimated_job_time_sec=None,
                estimate_confidence="provisional",
                estimate_confidence_reasons={
                    "missing_grbl_settings": True,
                    "sampled_scan": True,
                    "scan_incomplete": True,
                    "no_motion_lines_found": True,
                },
                estimate_inputs_snapshot={},
                bounds_box=None,
                dimensions_confidence="rough",
                dimensions_confidence_reasons={
                    "sampled_scan": True,
                    "scan_incomplete": True,
                    "no_motion_lines_found": True,
                },
                autolevel_prereq_snapshot={},
            )
        _post_ui(app, _apply_complete_ui, app, run_id, result)

    worker = threading.Thread(target=_worker, name="overdrive-validation", daemon=True)
    app._overdrive_validation_worker = worker
    worker.start()


def cancel_validation(app: Any) -> None:
    cancel_event = getattr(app, "_overdrive_validation_cancel_event", None)
    if cancel_event is None:
        return
    try:
        cancel_event.set()
    except Exception:
        return
    _set_text_if_changed(getattr(app, "overdrive_validation_status_var", None), "Canceling...")


def export_validation_report(app: Any) -> None:
    result = getattr(app, "_overdrive_validation_last_result", None)
    if not isinstance(result, _ValidationScanResult):
        messagebox.showinfo("Validation report", "No validation report available.")
        return
    path = filedialog.asksaveasfilename(
        title="Export validation report",
        defaultextension=".txt",
        initialfile=f"validation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
    )
    if not path:
        return
    report_text = str(getattr(app, "_overdrive_validation_last_report_text", "") or "")
    if not report_text:
        report_text = _build_report_text(result)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report_text)
    _set_text_if_changed(
        getattr(app, "overdrive_validation_status_var", None),
        f"Report exported: {os.path.basename(path)}",
    )


def copy_validation_report(app: Any) -> None:
    text = str(getattr(app, "_overdrive_validation_last_report_text", "") or "")
    if not text:
        return
    try:
        app.clipboard_clear()
        app.clipboard_append(text)
    except Exception:
        return
    _set_text_if_changed(
        getattr(app, "overdrive_validation_status_var", None),
        "Report copied to clipboard",
    )


def refresh_validation_controls(app: Any) -> None:
    _refresh_controls_state(app)
