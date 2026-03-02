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
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, cast

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
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


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _safe_job_hash(app: Any) -> str:
    raw_hash = (
        getattr(app, "_gcode_hash", None)
        or getattr(app, "_last_parse_hash", None)
        or ""
    )
    return str(raw_hash).strip()


def _runtime_metrics(app: Any) -> dict[str, Any]:
    grbl = getattr(app, "grbl", None)
    getter = getattr(grbl, "get_runtime_metrics", None) if grbl is not None else None
    if not callable(getter):
        return {}
    try:
        raw = getter()
    except Exception as exc:
        _log_suppressed("Failed collecting runtime telemetry metrics", exc)
        return {}
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _format_runtime_metrics(
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int = 20,
) -> list[str]:
    if not metrics:
        return []
    lines: list[str] = []
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
    tx_loop_idle_wait_total_s = float(metrics.get("tx_loop_idle_wait_total_s", 0.0) or 0.0)
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
    ttk.Label(container, text="Runtime telemetry", font=("TkDefaultFont", 12, "bold")).pack(
        anchor="w"
    )
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
            lines = _format_runtime_metrics(metrics, include_samples=True, sample_limit=12)
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


def _resolve_checklist_items_any(app, names: list[str], fallback: list[str]) -> list[str]:
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
    title = ttk.Label(container, text="Release checklist", font=("TkDefaultFont", 12, "bold"))
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
    title = ttk.Label(container, text="Start Job checklist", font=("TkDefaultFont", 12, "bold"))
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
        top_view = getattr(getattr(app, "toolpath_panel", None), "top_view", None)
        bounds = getattr(top_view, "bounds", None) if top_view else None
    if not bounds or len(bounds) < 6:
        return None
    return bounds


def _get_travel_limits(app: Any) -> dict[str, float]:
    data = getattr(getattr(app, "settings_controller", None), "_settings_data", {}) or {}
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
        failures.append("Toolpath bounds are unavailable (wait for parsing / top view).")
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
                failures.append("Validation report is unavailable (streaming validation failed/skipped).")
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
        proceed = bool(messagebox.askyesno(
            "Preflight gate",
            message
            + "\n\nContinue anyway?\n"
            + "Choose Yes to override the preflight gate and start the job.",
        ))
        if proceed:
            _record_preflight_decision(
                app,
                decision="override",
                failures=failures,
                warnings=warnings,
            )
            try:
                app.ui_q.put(("log", "[preflight] Override accepted; starting despite gate failures."))
                for item in failures:
                    app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
                for item in warnings:
                    app.ui_q.put(("log", f"[preflight] warning: {item}"))
            except Exception as exc:
                _log_suppressed("Failed writing preflight override details to UI log queue", exc)
            try:
                app.status.config(text="Preflight overridden: starting job")
            except Exception as exc:
                _log_suppressed("Failed updating status text after preflight override", exc)
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
            _log_suppressed("Failed writing preflight blocked decision details to UI log queue", exc)
        try:
            app.status.config(text="Run blocked: preflight gate failed")
        except Exception as exc:
            _log_suppressed("Failed updating status text when preflight blocks run", exc)
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
    lines = []
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
    lines.append(f"G-code streaming mode: {getattr(app, '_gcode_streaming_mode', False)}")
    lines.append(f"G-code total lines: {getattr(app, '_gcode_total_lines', 0)}")
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
        lines.extend(_format_runtime_metrics(metrics, include_samples=True, sample_limit=20))
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
    dir_name = os.path.dirname(path)
    if dir_name:
        try:
            os.makedirs(dir_name, exist_ok=True)
        except Exception as exc:
            _log_suppressed("Failed creating export directory for diagnostics report", exc)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write("\n".join(lines))
        messagebox.showinfo("Export diagnostics", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Export diagnostics", f"Failed to write diagnostics:\n{exc}")
