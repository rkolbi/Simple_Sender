from datetime import datetime, timedelta
import os
import time
import tkinter as tk
from tkinter import messagebox, ttk
import tkinter.font as tkfont

from simple_sender.ui.gcode_stats import format_duration
from simple_sender.ui.popup_utils import center_window
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


def toggle_performance(app):
    current = bool(app.performance_mode.get())
    new_val = not current
    app.performance_mode.set(new_val)
    try:
        app.btn_performance_mode.config(
            text="Performance: On" if new_val else "Performance: Off"
        )
    except Exception:
        pass
    if not new_val:
        app.streaming_controller.flush_console()
    app._apply_status_poll_profile()


def toggle_console_pos_status(app):
    current = bool(app.console_positions_enabled.get())
    new_val = not current
    app.console_positions_enabled.set(new_val)
    app.console_status_enabled.set(new_val)
    if hasattr(app, "btn_console_pos"):
        app.btn_console_pos.config(text="Pos/Status: On" if new_val else "Pos/Status: Off")
    app.streaming_controller.render_console()


def toggle_unit_mode(app):
    if app._stream_state in ("running", "paused"):
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
    if app._stream_state in ("running", "paused"):
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

    details_window = {"win": None}

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
