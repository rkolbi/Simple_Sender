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

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
from simple_sender.ui.popup_utils import center_window

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
    return items


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
    win.title("Run checklist")
    win.minsize(520, 320)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(container, text="Run checklist", font=("TkDefaultFont", 12, "bold"))
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this checklist before starting a job to reduce surprises.",
        wraplength=480,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    items = _resolve_checklist_items(app, "run", RUN_CHECKLIST_ITEMS)
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


def run_preflight_check(app) -> None:
    path = getattr(app, "_last_gcode_path", None)
    if not path:
        messagebox.showinfo("Preflight check", "Load a G-code file first.")
        return
    parse_result = getattr(app, "_last_parse_result", None)
    bounds = getattr(parse_result, "bounds", None) if parse_result else None
    if not bounds:
        top_view = getattr(getattr(app, "toolpath_panel", None), "top_view", None)
        bounds = getattr(top_view, "bounds", None) if top_view else None
    has_bounds = bool(bounds and len(bounds) >= 4 and (bounds[1] - bounds[0]) > 0)
    streaming_mode = bool(getattr(app, "_gcode_streaming_mode", False))
    validate_streaming = False
    try:
        validate_streaming = bool(app.validate_streaming_gcode.get())
    except Exception:
        validate_streaming = False
    report = getattr(app, "_gcode_validation_report", None)
    issues = []
    if not has_bounds:
        issues.append("Bounds are not ready (open Top View or wait for parsing).")
    if streaming_mode and not validate_streaming:
        issues.append("Streaming validation is disabled.")
    if report is None:
        if streaming_mode:
            if validate_streaming:
                issues.append("Validation report not available (skipped or failed).")
        else:
            issues.append("Validation report not available.")
    issues.extend(_format_validation_summary(report))
    if issues:
        messagebox.showwarning(
            "Preflight check",
            "Review before running:\n" + "\n".join(f"- {item}" for item in issues),
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
