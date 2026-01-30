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
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from simple_sender.ui.icons import (
    ICON_CONNECT,
    ICON_JOB_CLEAR,
    ICON_JOB_READ,
    ICON_AUTO_LEVEL,
    ICON_PAUSE,
    ICON_RECOVER,
    ICON_REFRESH,
    ICON_RESUME,
    ICON_RESUME_FROM,
    ICON_RUN,
    ICON_STOP,
    ICON_UNLOCK,
    icon_label,
)
from simple_sender.ui.widgets import apply_tooltip, attach_log_gcode, set_kb_id

def update_job_button_mode(app, mode: str) -> None:
    btn = getattr(app, "btn_open", None)
    if not btn:
        return
    auto_level_enabled = bool(getattr(app, "auto_level_enabled", True))
    mode = "auto_level" if str(mode).lower() in ("auto_level", "auto-level") else "read_job"
    if not auto_level_enabled:
        mode = "read_job"
    streaming = bool(getattr(app, "_gcode_streaming_mode", False))
    leveled_path = getattr(app, "_last_gcode_path", None)
    leveled = False
    if leveled_path:
        base = os.path.splitext(os.path.basename(leveled_path))[0]
        base_upper = base.upper()
        if base_upper.endswith("-AL"):
            leveled = True
        else:
            prefix, sep, suffix = base_upper.rpartition("-AL-")
            leveled = bool(prefix) and bool(sep) and suffix.isdigit()
    if (
        getattr(app, "_job_button_mode", None) == mode
        and getattr(app, "_job_button_streaming", None) == streaming
        and getattr(app, "_job_button_leveled", None) == leveled
    ):
        return
    if mode == "auto_level":
        btn.config(
            text=icon_label(ICON_AUTO_LEVEL, "Auto-Level"),
            command=lambda: app._confirm_and_run("Auto-Level", app._show_auto_level_dialog),
        )
        if leveled:
            reason = "Auto-level unavailable (already leveled)."
            tooltip = "Auto-level is already applied. Load the original file to re-level."
        else:
            reason = None
            tooltip = "Probe only the job bounds and build a height map."
        try:
            btn._disabled_reason = reason
        except Exception:
            pass
        apply_tooltip(btn, tooltip)
        set_kb_id(btn, "auto_level")
        try:
            app._offline_controls.add(btn)
        except Exception:
            pass
    else:
        btn.config(
            text=icon_label(ICON_JOB_READ, "Read Job"),
            command=app.open_gcode,
        )
        try:
            btn._disabled_reason = None
        except Exception:
            pass
        apply_tooltip(btn, "Load a G-code job for streaming (read-only).")
        set_kb_id(btn, "gcode_open")
        try:
            app._offline_controls.add(btn)
        except Exception:
            pass
    hint = getattr(app, "job_button_hint", None)
    if hint is not None:
        visible = bool(getattr(app, "_job_button_hint_visible", False))
        if leveled and mode == "auto_level":
            hint.config(text="Auto-Level disabled (already leveled)")
            apply_tooltip(hint, "Auto-level is already applied. Load the original file to re-level.")
            if not visible:
                hint.pack(side="left", padx=(6, 0), after=btn)
                try:
                    app._job_button_hint_visible = True
                except Exception:
                    pass
        elif visible:
            try:
                hint.pack_forget()
            except Exception:
                pass
            try:
                app._job_button_hint_visible = False
            except Exception:
                pass
    try:
        app._job_button_mode = mode
        app._job_button_streaming = streaming
        app._job_button_leveled = leveled
    except Exception:
        pass
    try:
        btn._force_disabled = bool(mode == "auto_level" and leveled)
    except Exception:
        pass
    if mode == "auto_level" and leveled:
        try:
            btn.config(state="disabled")
        except Exception:
            pass
    try:
        ready = (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and bool(getattr(app, "_status_seen", False))
            and not bool(getattr(app, "_alarm_locked", False))
        )
        app._set_manual_controls_enabled(ready)
    except Exception:
        pass


def on_resume_button_visibility_change(app):
    app.settings["show_resume_from_button"] = bool(app.show_resume_from_button.get())
    update_resume_button_visibility(app)


def on_recover_button_visibility_change(app):
    app.settings["show_recover_button"] = bool(app.show_recover_button.get())
    update_recover_button_visibility(app)


def update_resume_button_visibility(app):
    if not hasattr(app, "btn_resume_from"):
        return
    visible = bool(app.show_resume_from_button.get())
    if visible:
        if not app.btn_resume_from.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            before_widget = getattr(app, "btn_unlock_top", None)
            if before_widget and before_widget.winfo_exists():
                pack_kwargs["before"] = before_widget
            app.btn_resume_from.pack(**pack_kwargs)
    else:
        app.btn_resume_from.pack_forget()


def update_recover_button_visibility(app):
    if not hasattr(app, "btn_alarm_recover"):
        return
    visible = bool(app.show_recover_button.get())
    if visible:
        if not app.btn_alarm_recover.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            separator = getattr(app, "_recover_separator", None)
            if separator and separator.winfo_exists():
                pack_kwargs["before"] = separator
            app.btn_alarm_recover.pack(**pack_kwargs)
    else:
        app.btn_alarm_recover.pack_forget()


def build_toolbar(app):
    bar = ttk.Frame(app, padding=(8, 6, 0, 6))
    bar.pack(side="top", fill="x")

    ttk.Label(bar, text="Port:").pack(side="left")
    app.port_combo = ttk.Combobox(bar, width=18, textvariable=app.current_port, state="readonly")
    app.port_combo.pack(side="left", padx=(6, 4))

    app.btn_refresh = ttk.Button(
        bar,
        text=icon_label(ICON_REFRESH, "Refresh"),
        style=app.icon_button_style,
        command=app.refresh_ports,
    )
    set_kb_id(app.btn_refresh, "port_refresh")
    app.btn_refresh.pack(side="left", padx=(0, 10))
    apply_tooltip(app.btn_refresh, "Refresh the list of serial ports.")
    app.btn_conn = ttk.Button(
        bar,
        text=icon_label(ICON_CONNECT, "Connect"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Connect/Disconnect", app.toggle_connect),
    )
    set_kb_id(app.btn_conn, "port_connect")
    app.btn_conn.pack(side="left")
    apply_tooltip(app.btn_conn, "Connect or disconnect from the selected serial port.")
    attach_log_gcode(app.btn_conn, "")

    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

    app.btn_open = ttk.Button(
        bar,
        text=icon_label(ICON_JOB_READ, "Read Job"),
        style=app.icon_button_style,
        command=app.open_gcode,
    )
    set_kb_id(app.btn_open, "gcode_open")
    app.btn_open.pack(side="left")
    app._manual_controls.append(app.btn_open)
    app._offline_controls.add(app.btn_open)
    apply_tooltip(app.btn_open, "Load a G-code job for streaming (read-only).")
    app.job_button_hint = ttk.Label(bar, text="")
    try:
        app._job_button_hint_visible = False
    except Exception:
        pass
    try:
        app._job_button_mode = "read_job"
    except Exception:
        pass
    app.btn_clear = ttk.Button(
        bar,
        text=icon_label(ICON_JOB_CLEAR, "Clear Job"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Clear Job", app._clear_gcode),
    )
    set_kb_id(app.btn_clear, "gcode_clear")
    app.btn_clear.pack(side="left", padx=(6, 0))
    app._manual_controls.append(app.btn_clear)
    app._offline_controls.add(app.btn_clear)
    apply_tooltip(app.btn_clear, "Unload the current job and reset the viewer.")
    app.btn_run = ttk.Button(
        bar,
        text=icon_label(ICON_RUN, "Run"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Run job", app.run_job),
        state="disabled",
    )
    set_kb_id(app.btn_run, "job_run")
    app.btn_run.pack(side="left", padx=(8, 0))
    apply_tooltip(app.btn_run, "Start streaming the loaded G-code.")
    attach_log_gcode(app.btn_run, "Cycle Start")
    app.btn_pause = ttk.Button(
        bar,
        text=icon_label(ICON_PAUSE, "Pause"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Pause job", app.pause_job),
        state="disabled",
    )
    set_kb_id(app.btn_pause, "job_pause")
    app.btn_pause.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_pause, "Feed hold the running job.")
    attach_log_gcode(app.btn_pause, "!")
    app.btn_resume = ttk.Button(
        bar,
        text=icon_label(ICON_RESUME, "Resume"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Resume job", app.resume_job),
        state="disabled",
    )
    set_kb_id(app.btn_resume, "job_resume")
    app.btn_resume.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_resume, "Resume a paused job.")
    attach_log_gcode(app.btn_resume, "~")
    app.btn_stop = ttk.Button(
        bar,
        text=icon_label(ICON_STOP, "Stop/Reset"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Stop/Reset", app.stop_job),
        state="disabled",
    )
    set_kb_id(app.btn_stop, "job_stop_reset")
    app.btn_stop.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_stop, "Stop the job and soft reset GRBL.")
    attach_log_gcode(app.btn_stop, "Ctrl-X")
    app.btn_resume_from = ttk.Button(
        bar,
        text=icon_label(ICON_RESUME_FROM, "Resume From..."),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Resume from line", app._show_resume_dialog),
        state="disabled",
    )
    set_kb_id(app.btn_resume_from, "job_resume_from")
    app.btn_resume_from.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_resume_from, "Resume from a specific line with modal re-sync.")
    app.btn_unlock_top = ttk.Button(
        bar,
        text=icon_label(ICON_UNLOCK, "Unlock"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run(
            "Unlock ($X)", lambda: app._run_if_connected(app.grbl.unlock)
        ),
        state="disabled",
    )
    set_kb_id(app.btn_unlock_top, "unlock_top")
    app.btn_unlock_top.pack(side="left", padx=(6, 0))
    app._manual_controls.append(app.btn_unlock_top)
    apply_tooltip(app.btn_unlock_top, "Send $X to clear alarm (top-bar).")
    app.btn_alarm_recover = ttk.Button(
        bar,
        text=icon_label(ICON_RECOVER, "Recover"),
        style=app.icon_button_style,
        command=app._show_alarm_recovery,
        state="disabled",
    )
    set_kb_id(app.btn_alarm_recover, "alarm_recover")
    app.btn_alarm_recover.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_alarm_recover, "Show alarm recovery steps.")

    app._recover_separator = None

    app._update_resume_button_visibility()
    app._update_recover_button_visibility()

    # right side status
    base_font = tkfont.nametofont("TkDefaultFont")
    try:
        size = int(base_font.cget("size"))
    except Exception:
        size = 10
    style = ttk.Style()
    frame_style = bar.cget("style") or "TFrame"
    bar_bg = style.lookup(frame_style, "background") or app.cget("background")
    state_font = tkfont.Font(
        family=base_font.cget("family"),
        size=size + 2,
        weight=base_font.cget("weight"),
    )
    app.machine_state_label = tk.Label(
        bar,
        textvariable=app.machine_state,
        font=state_font,
        fg="#000000",
        bg=bar_bg,
        padx=20,
        pady=6,
    )
    app.machine_state_label.pack(side="right", fill="y")
    try:
        app._state_default_bg = app.machine_state_label.cget("background")
    except Exception:
        pass
    try:
        app._machine_state_max_chars = len("DISCONNECTED") + 2
    except Exception:
        pass

