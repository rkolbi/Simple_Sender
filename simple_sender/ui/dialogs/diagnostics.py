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

import json
import os
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, cast

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
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
        except Exception:
            pass
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
        except Exception:
            pass
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
        if getattr(report, "line_issue_count", 0) > 0:
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
            try:
                app.ui_q.put(("log", "[preflight] Override accepted; starting despite gate failures."))
                for item in failures:
                    app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
            except Exception:
                pass
            try:
                app.status.config(text="Preflight overridden: starting job")
            except Exception:
                pass
            return True
        try:
            app.status.config(text="Run blocked: preflight gate failed")
        except Exception:
            pass
        return False
    if warnings:
        try:
            app.ui_q.put(("log", "[preflight] " + "; ".join(warnings)))
        except Exception:
            pass
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
    path = filedialog.asksaveasfilename(
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
    last_status = getattr(app, "_last_status_raw", "")
    if last_status:
        lines.append("Last status:")
        lines.append(last_status.strip())
        lines.append("")
    history = getattr(app, "_status_history", [])
    if history:
        lines.append("Recent status history:")
        for ts, raw in history[-50:]:
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
        except Exception:
            pass
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write("\n".join(lines))
        messagebox.showinfo("Export diagnostics", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Export diagnostics", f"Failed to write diagnostics:\n{exc}")
