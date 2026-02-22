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

from datetime import datetime, timedelta
import os
import time
import tkinter as tk
from tkinter import messagebox, ttk
import tkinter.font as tkfont

from simple_sender.ui.gcode.stats import format_duration
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.gcode_validator import format_validation_details, format_validation_report


def toggle_tooltips(app):
    current = bool(app.tooltip_enabled.get())
    new_val = not current
    app.tooltip_enabled.set(new_val)
    app._refresh_tooltips_toggle_text()


def on_gui_logging_change(app):
    status = "enabled" if app.gui_logging_enabled.get() else "disabled"
    try:
        app.streaming_controller.handle_log(f"[settings] GUI logging {status}")
    except Exception:
        pass


def on_theme_change(app, *_):
    app._apply_theme(app.selected_theme.get())
    try:
        app._scrollbar_width_default = _style_scrollbar_width(getattr(app, "style", None))
    except Exception:
        pass
    try:
        app._apply_scrollbar_width()
    except Exception:
        pass


_UI_SCALE_NAMED_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkHeadingFont",
    "TkMenuFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)

_SCROLLBAR_WIDTHS = {
    "wide": 24,
    "wider": 32,
    "widest": 40,
}

def _style_scrollbar_width(style) -> int | None:
    if style is None:
        return None
    try:
        value = style.lookup("TScrollbar", "width")
    except Exception:
        return None
    try:
        return int(value)
    except Exception:
        return None

def _coerce_scrollbar_width(value, default: str = "wide") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
    else:
        normalized = str(value).strip().lower()
    if normalized == "narrow":
        return "default"
    if normalized in _SCROLLBAR_WIDTHS or normalized == "default":
        return normalized
    return default


def _coerce_ui_scale(value, default: float = 1.0) -> float:
    try:
        scale = float(value)
    except Exception:
        return default
    if scale <= 0:
        return default
    scale = max(0.5, min(3.0, scale))
    return round(scale, 2)


def _scaled_font_size(size: int, scale: float) -> int:
    sign = -1 if size < 0 else 1
    value = max(1, int(round(abs(size) * scale)))
    return sign * value


def _apply_scaled_named_fonts(app, scale: float) -> None:
    bases = getattr(app, "_ui_scale_named_font_bases", None)
    if bases is None:
        bases = {}
        for name in _UI_SCALE_NAMED_FONTS:
            try:
                size = int(tkfont.nametofont(name).cget("size"))
            except Exception:
                continue
            bases[name] = size
        app._ui_scale_named_font_bases = bases
    for name, base in bases.items():
        try:
            tkfont.nametofont(name).configure(size=_scaled_font_size(base, scale))
        except Exception:
            continue


def _apply_scaled_custom_fonts(app, scale: float) -> None:
    bases = getattr(app, "_ui_scale_custom_font_bases", None)
    if bases is None:
        bases = {}
        app._ui_scale_custom_font_bases = bases
    for key in ("icon_button_font", "tab_font", "home_button_font", "dro_value_font", "console_font"):
        font = getattr(app, key, None)
        if font is None:
            continue
        if key not in bases:
            try:
                bases[key] = int(font.cget("size"))
            except Exception:
                continue
        try:
            font.configure(size=_scaled_font_size(bases[key], scale))
        except Exception:
            continue


def apply_ui_scale(app, value: float | None = None) -> float:
    raw = value
    if raw is None:
        try:
            raw = app.ui_scale.get()
        except Exception:
            raw = 1.0
    scale = _coerce_ui_scale(raw, 1.0)
    try:
        app.tk.call("tk", "scaling", scale)
    except Exception:
        return scale
    _apply_scaled_named_fonts(app, scale)
    _apply_scaled_custom_fonts(app, scale)
    try:
        app.ui_scale.set(scale)
    except Exception:
        pass
    try:
        app.settings["ui_scale"] = scale
    except Exception:
        pass
    try:
        app.update_idletasks()
    except Exception:
        pass
    return scale


def apply_scrollbar_width(app, value: str | None = None) -> str:
    raw = value
    if raw is None:
        try:
            raw = app.scrollbar_width.get()
        except Exception:
            raw = "wide"
    choice = _coerce_scrollbar_width(raw, "wide")
    if choice == "default":
        width = getattr(app, "_scrollbar_width_default", None)
        if width is None:
            width = _style_scrollbar_width(getattr(app, "style", None))
        if width is None:
            width = 16
    else:
        width = _SCROLLBAR_WIDTHS.get(choice, _SCROLLBAR_WIDTHS["wide"])
    try:
        app.style.configure("TScrollbar", width=width)
        app.style.configure("Vertical.TScrollbar", width=width)
        app.style.configure("Horizontal.TScrollbar", width=width)
    except Exception:
        pass
    try:
        app.scrollbar_width.set(choice)
    except Exception:
        pass
    try:
        app.settings["scrollbar_width"] = choice
    except Exception:
        pass
    return choice


def on_scrollbar_width_change(app, _event=None):
    choice = apply_scrollbar_width(app)
    try:
        app.status.config(text=f"Scrollbar width: {choice}")
    except Exception:
        pass


def on_ui_scale_change(app, _event=None):
    scale = apply_ui_scale(app)
    try:
        app.status.config(text=f"UI scale: {scale:.2f}x")
    except Exception:
        pass
    try:
        app._save_settings()
    except Exception:
        pass


def toggle_performance(app):
    app.performance_mode.set(not bool(app.performance_mode.get()))
    on_performance_mode_change(app)


def on_performance_mode_change(app):
    new_val = bool(app.performance_mode.get())
    if not new_val:
        app.streaming_controller.flush_console()
    app._apply_status_poll_profile()
    try:
        app.status.config(text=f"Performance mode: {'On' if new_val else 'Off'}")
    except Exception:
        pass


def toggle_console_pos_status(app):
    current = bool(app.console_positions_enabled.get())
    new_val = not current
    app.console_positions_enabled.set(new_val)
    if hasattr(app, "btn_console_pos"):
        app.btn_console_pos.config(text="Pos/Status: On" if new_val else "Pos/Status: Off")
    app.streaming_controller.render_console()

def toggle_autolevel_overlay(app):
    current = bool(app.show_autolevel_overlay.get())
    app.show_autolevel_overlay.set(not current)
    on_autolevel_overlay_change(app)

def on_autolevel_overlay_change(app):
    show = bool(app.show_autolevel_overlay.get())
    grid = app._auto_level_grid if show else None
    try:
        app.toolpath_panel.set_autolevel_overlay(grid)
    except Exception:
        pass
    try:
        app.settings["show_autolevel_overlay"] = show
    except Exception:
        pass
    try:
        app._refresh_autolevel_overlay_button()
    except Exception:
        pass


def toggle_unit_mode(app):
    if app._stream_state in ("running", "paused") or bool(
        getattr(app, "_stream_done_pending_idle", False)
    ):
        try:
            app.status.config(text="Unit toggle disabled while streaming")
        except Exception:
            pass
        return
    new_mode = "inch" if app.unit_mode.get() == "mm" else "mm"
    if app.grbl.is_connected():
        gcode = "G20" if new_mode == "inch" else "G21"
        app._send_manual(gcode, "units")
    app._set_unit_mode(new_mode)

def start_homing(app):
    if not require_grbl_connection(app):
        return
    if app._stream_state in ("running", "paused") or bool(
        getattr(app, "_stream_done_pending_idle", False)
    ):
        try:
            app.status.config(text="Homing blocked while streaming")
        except Exception:
            pass
        return
    app._homing_in_progress = True
    app._homing_state_seen = False
    app._homing_start_ts = time.time()
    app._machine_state_text = "Home"
    app.machine_state.set("Homing")
    app._update_state_highlight("Homing")
    try:
        app.grbl.home()
    except Exception:
        app._homing_in_progress = False
        app._homing_state_seen = False

def confirm_and_run(app, label: str, func):
    try:
        need_confirm = bool(app.training_wheels.get())
    except Exception:
        need_confirm = False
    now = time.time()
    last_ts = app._confirm_last_time.get(label, 0.0)
    if need_confirm:
        if (now - last_ts) < app._confirm_debounce_sec:
            return
        if label in ("Run job", "Resume job"):
            if not _confirm_run_job(app, label):
                return
        else:
            if not messagebox.askyesno("Confirm", f"{label}?"):
                return
    app._confirm_last_time[label] = now
    func()


def _format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "n/a"
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{int(num_bytes)} B"


def _job_estimate_text(app) -> tuple[str, str, str]:
    stats = getattr(app, "_last_stats", None)
    if not stats:
        return "n/a", "n/a", "n/a"
    time_min = stats.get("time_min")
    rapid_min = stats.get("rapid_min")
    try:
        factor = app._estimate_factor_value()
    except Exception:
        factor = 1.0
    feed_only = "n/a"
    total = "n/a"
    finish_at = "n/a"
    if time_min is not None:
        seconds = int(round(time_min * factor * 60))
        feed_only = format_duration(seconds)
    if time_min is not None and rapid_min is not None:
        total_min = (time_min + rapid_min) * factor
        total_seconds = int(round(total_min * 60))
        total = format_duration(total_seconds)
        rate_source = getattr(app, "_last_rate_source", None)
        if rate_source in ("fallback", "profile", "estimate"):
            total = f"{total} ({rate_source})"
        finish_at = (datetime.now() + timedelta(minutes=total_min)).strftime("%Y-%m-%d %H:%M:%S")
    return feed_only, total, finish_at


def _confirm_run_job(app, label: str = "Run job") -> bool:
    path = getattr(app, "_last_gcode_path", None)
    name = ""
    if path:
        name = os.path.basename(path)
    else:
        name = getattr(app.grbl, "_gcode_name", "") or "Unknown"
    size = None
    if path and os.path.isfile(path):
        try:
            size = os.path.getsize(path)
        except Exception:
            size = None
    feed_only, total, finish_at = _job_estimate_text(app)

    dialog = tk.Toplevel(app)
    dialog.title(f"Confirm {label.lower()}")
    dialog.transient(app)
    dialog.resizable(False, False)
    dialog.configure(padx=20, pady=16)

    base_font = tkfont.nametofont("TkDefaultFont")
    title_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 4,
        weight="bold",
    )
    label_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 1,
        weight="bold",
    )
    value_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 1,
    )

    title = "Run job?" if label == "Run job" else "Resume job?"
    ttk.Label(dialog, text=title, font=title_font).grid(row=0, column=0, columnspan=2, sticky="w")

    rows = [
        ("File", name),
        ("Size", _format_bytes(size)),
        ("Est time (feed only)", feed_only),
        ("Est time (with rapids)", total),
        ("If started now, finishes at", finish_at),
    ]
    for idx, (label, value) in enumerate(rows, start=1):
        ttk.Label(dialog, text=f"{label}:", font=label_font).grid(
            row=idx, column=0, sticky="w", padx=(0, 12), pady=2
        )
        ttk.Label(dialog, text=value, font=value_font, wraplength=520).grid(
            row=idx, column=1, sticky="w", pady=2
        )
    report = getattr(app, "_gcode_validation_report", None)
    report_text = format_validation_report(report)
    report_row = len(rows) + 1
    ttk.Label(
        dialog,
        text=report_text,
        wraplength=520,
        justify="left",
    ).grid(row=report_row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    details_window: dict[str, tk.Toplevel | None] = {"win": None}

    def open_details():
        win = details_window.get("win")
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(dialog)
        details_window["win"] = win
        win.title("G-code validation details")
        win.transient(dialog)
        win.minsize(640, 420)
        container = ttk.Frame(win, padding=12)
        container.pack(fill="both", expand=True)
        text = tk.Text(container, wrap="word", height=18)
        vsb = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        text.insert("end", format_validation_details(report))
        text.configure(state="disabled")

        def close():
            details_window["win"] = None
            try:
                win.destroy()
            except Exception:
                pass

        btn_row = ttk.Frame(container)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btn_row, text="Close", command=close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)
        center_window(win, dialog)

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=report_row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
    result = {"ok": False}

    def accept():
        result["ok"] = True
        try:
            dialog.destroy()
        except Exception:
            pass

    def cancel():
        try:
            dialog.destroy()
        except Exception:
            pass

    confirm_label = "START"
    if report is not None and getattr(report, "line_issue_count", 0) > 0:
        ttk.Button(btn_frame, text="Details...", command=open_details).pack(
            side="left",
            padx=(0, 6),
        )
    ttk.Button(btn_frame, text=confirm_label, command=accept).pack(side="right", padx=(6, 0))
    ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side="right")
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    center_window(dialog, app)
    try:
        dialog.grab_set()
    except Exception:
        pass
    dialog.wait_window()
    return result["ok"]


def require_grbl_connection(app) -> bool:
    if not app.grbl.is_connected():
        messagebox.showwarning("Not connected", "Connect to GRBL first.")
        return False
    return True


def run_if_connected(app, func):
    if not require_grbl_connection(app):
        return
    func()


def send_manual(app, command: str, source: str):
    app.grbl.send_immediate(command, source=source)
