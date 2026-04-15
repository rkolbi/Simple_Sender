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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any

from simple_sender.constants.messages import MachineStateMessages
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
from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_common import attach_log_gcode, set_kb_id

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_TOOLBAR_GROUP_LABEL_STYLE = "SimpleSender.ToolbarGroup.TLabel"
_TOOLBAR_GROUP_FOCUS_LABEL_STYLE = "SimpleSender.ToolbarGroupFocus.TLabel"
_TOOLBAR_FOCUS_BLUE = "#1565c0"


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _widget_exists(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return True


def _widget_mapped(widget: Any) -> bool:
    if not _widget_exists(widget):
        return False
    try:
        return bool(widget.winfo_ismapped())
    except Exception:
        return False


def _widget_enabled(widget: Any) -> bool:
    if not _widget_exists(widget):
        return False
    try:
        state = str(widget.cget("state")).strip().lower()
    except Exception:
        state = str(getattr(widget, "state", "")).strip().lower()
    return state != "disabled"


def _toolbar_has_job(app: Any) -> bool:
    gview = getattr(app, "gview", None)
    if gview is not None:
        try:
            if bool(getattr(gview, "lines_count", 0)):
                return True
        except Exception:
            pass
    if getattr(app, "_gcode_source", None) is not None:
        return True
    return bool(getattr(app, "_last_gcode_lines", None))


def _var_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception as exc:
            _log_suppressed("Failed reading tkinter variable boolean value", exc)
            return default
    return bool(value)


def _toolbar_focus_group(app: Any) -> str | None:
    stream_state = str(getattr(app, "_stream_state", "") or "").lower()
    connected = bool(getattr(app, "connected", False))
    grbl_ready = bool(getattr(app, "_grbl_ready", False))
    status_seen = bool(getattr(app, "_status_seen", False))
    alarm_locked = bool(getattr(app, "_alarm_locked", False))
    has_job = _toolbar_has_job(app)

    if alarm_locked:
        if _widget_enabled(getattr(app, "btn_unlock_top", None)) or _widget_enabled(
            getattr(app, "btn_alarm_recover", None)
        ):
            return "Recovery"
        return None
    if stream_state == "running":
        if _widget_enabled(getattr(app, "btn_pause", None)) or _widget_enabled(
            getattr(app, "btn_stop", None)
        ):
            return "Run"
        return None
    if stream_state == "paused":
        if _widget_enabled(getattr(app, "btn_resume", None)) or _widget_enabled(
            getattr(app, "btn_stop", None)
        ):
            return "Run"
        return None
    if not connected:
        if _widget_enabled(getattr(app, "btn_conn", None)):
            return "Connection"
        return None
    if not grbl_ready or not status_seen:
        return None
    if not has_job:
        if _widget_enabled(getattr(app, "btn_open", None)):
            return "Job"
        return None
    if _widget_enabled(getattr(app, "btn_run", None)) or _widget_enabled(
        getattr(app, "btn_resume_from", None)
    ):
        return "Run"
    if _widget_enabled(getattr(app, "btn_open", None)):
        return "Job"
    return None


def _ensure_toolbar_group_styles(app: Any) -> None:
    style_obj = getattr(app, "style", None)
    if style_obj is not None:
        style = style_obj
    else:
        try:
            style = ttk.Style()
        except Exception as exc:
            _log_suppressed("Failed creating ttk.Style for toolbar group styles", exc)
            return
    palette = getattr(app, "theme_palette", None) or {}
    try:
        label_style = str(getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE))
        focus_style = str(getattr(app, "toolbar_group_focus_label_style", _TOOLBAR_GROUP_FOCUS_LABEL_STYLE))
        default_fg = (
            palette.get("fg")
            or style.lookup("TLabel", "foreground")
            or "#000000"
        )
        default_bg = (
            palette.get("bg")
            or style.lookup("TLabel", "background")
            or style.lookup("TFrame", "background")
            or "#f0f0f0"
        )
        base_font = tkfont.nametofont("TkDefaultFont")
        focus_font = tkfont.Font(
            family=base_font.cget("family"),
            size=base_font.cget("size"),
            weight="bold",
        )
        style_signature = (
            label_style,
            focus_style,
            str(default_fg),
            str(default_bg),
            str(base_font.cget("family")),
            str(base_font.cget("size")),
        )
        if style_signature == getattr(app, "_toolbar_group_style_signature", None):
            return
        app._toolbar_group_focus_font = focus_font
        app.toolbar_group_label_style = label_style
        app.toolbar_group_focus_label_style = focus_style
        app._toolbar_group_style_signature = style_signature
        style.configure(
            label_style,
            foreground=default_fg,
            background=default_bg,
            font=base_font,
        )
        style.configure(
            focus_style,
            foreground=_TOOLBAR_FOCUS_BLUE,
            background=default_bg,
            font=focus_font,
        )
    except Exception as exc:
        _log_suppressed("Failed refreshing toolbar group styles", exc)


def _apply_toolbar_button_style(button: Any, style_name: str) -> None:
    if not _widget_exists(button):
        return
    try:
        button.config(style=style_name)
    except Exception as exc:
        _log_suppressed("Failed applying toolbar button style", exc)


def _apply_toolbar_group_style(label: Any, style_name: str) -> None:
    if not _widget_exists(label):
        return
    try:
        label.config(style=style_name)
    except Exception as exc:
        _log_suppressed("Failed applying toolbar group label style", exc)


def refresh_toolbar_action_focus(app) -> None:
    _ensure_toolbar_group_styles(app)
    default_style = str(getattr(app, "icon_button_style", "TButton"))
    tracked_buttons = [
        getattr(app, "btn_conn", None),
        getattr(app, "btn_open", None),
        getattr(app, "btn_run", None),
        getattr(app, "btn_pause", None),
        getattr(app, "btn_resume", None),
        getattr(app, "btn_resume_from", None),
        getattr(app, "btn_stop", None),
        getattr(app, "btn_unlock_top", None),
        getattr(app, "btn_alarm_recover", None),
    ]
    for btn in tracked_buttons:
        _apply_toolbar_button_style(btn, default_style)
    group_labels = getattr(app, "_toolbar_group_title_labels", {}) or {}
    default_group_style = str(
        getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE)
    )
    focus_group_style = str(
        getattr(app, "toolbar_group_focus_label_style", _TOOLBAR_GROUP_FOCUS_LABEL_STYLE)
    )
    focus_group = _toolbar_focus_group(app)
    signature = (
        default_style,
        default_group_style,
        focus_group_style,
        focus_group,
        tuple(sorted((str(name), id(label)) for name, label in group_labels.items())),
    )
    if signature == getattr(app, "_toolbar_focus_signature", None):
        return
    app._toolbar_focus_signature = signature
    for label in group_labels.values():
        _apply_toolbar_group_style(label, default_group_style)
    if focus_group:
        _apply_toolbar_group_style(group_labels.get(focus_group), focus_group_style)


def update_job_button_mode(app, mode: str) -> None:
    btn = getattr(app, "btn_open", None)
    if not btn:
        return
    auto_level_enabled = _var_bool(getattr(app, "auto_level_enabled", True), default=True)
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
    force_disabled = bool(mode == "auto_level" and leveled)
    if (
        getattr(app, "_job_button_mode", None) == mode
        and getattr(app, "_job_button_streaming", None) == streaming
        and getattr(app, "_job_button_leveled", None) == leveled
        and bool(getattr(btn, "_force_disabled", False)) == force_disabled
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
        except Exception as exc:
            _log_suppressed("Failed setting Auto-Level disabled reason on job button", exc)
        apply_tooltip(btn, tooltip)
        set_kb_id(btn, "auto_level")
        try:
            app._offline_controls.add(btn)
        except Exception as exc:
            _log_suppressed("Failed adding Auto-Level job button to offline controls", exc)
    else:
        btn.config(
            text=icon_label(ICON_JOB_READ, "Read Job"),
            command=app.open_gcode,
        )
        try:
            btn._disabled_reason = None
        except Exception as exc:
            _log_suppressed("Failed clearing disabled reason on Read Job button", exc)
        if not auto_level_enabled:
            apply_tooltip(
                btn,
                "Load a G-code job for streaming (Auto-Level disabled in Interface settings).",
            )
        else:
            apply_tooltip(btn, "Load a G-code job for streaming (read-only).")
        set_kb_id(btn, "gcode_open")
        try:
            app._offline_controls.add(btn)
        except Exception as exc:
            _log_suppressed("Failed adding Read Job button to offline controls", exc)
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
                except Exception as exc:
                    _log_suppressed("Failed marking job-button hint visible", exc)
        elif visible:
            try:
                hint.pack_forget()
            except Exception as exc:
                _log_suppressed("Failed hiding job-button hint", exc)
            try:
                app._job_button_hint_visible = False
            except Exception as exc:
                _log_suppressed("Failed marking job-button hint hidden", exc)
    try:
        app._job_button_mode = mode
        app._job_button_streaming = streaming
        app._job_button_leveled = leveled
    except Exception as exc:
        _log_suppressed("Failed caching job-button mode state", exc)
    try:
        btn._force_disabled = force_disabled
    except Exception as exc:
        _log_suppressed("Failed setting job-button force-disabled flag", exc)
    if force_disabled:
        try:
            btn.config(state="disabled")
        except Exception as exc:
            _log_suppressed("Failed disabling job button", exc)
    try:
        ready = (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and bool(getattr(app, "_status_seen", False))
            and not bool(getattr(app, "_alarm_locked", False))
        )
        app._set_manual_controls_enabled(ready)
    except Exception as exc:
        _log_suppressed("Failed refreshing manual controls after job-button mode update", exc)
    refresh_toolbar_action_focus(app)


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
        if not _widget_mapped(app.btn_resume_from):
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            app.btn_resume_from.pack(**pack_kwargs)
    else:
        app.btn_resume_from.pack_forget()
    refresh_toolbar_action_focus(app)


def update_recover_button_visibility(app):
    if not hasattr(app, "btn_alarm_recover"):
        return
    visible = bool(app.show_recover_button.get())
    if visible:
        if not _widget_mapped(app.btn_alarm_recover):
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            app.btn_alarm_recover.pack(**pack_kwargs)
    else:
        app.btn_alarm_recover.pack_forget()
    refresh_toolbar_action_focus(app)


def build_toolbar(app):
    bar = ttk.Frame(app, padding=(8, 6, 8, 6))
    bar.pack(side="top", fill="x")
    app.toolbar_bar = bar
    _ensure_toolbar_group_styles(app)

    def _build_group(parent, title: str):
        group = ttk.Frame(parent, padding=(0, 0, 8, 0))
        group.pack(side="left", fill="y")
        title_label = ttk.Label(
            group,
            text=title,
            style=str(getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE)),
        )
        title_label.pack(side="top", anchor="w")
        row = ttk.Frame(group)
        row.pack(side="top", anchor="w", pady=(2, 0))
        return group, row, title_label

    def _add_group_separator(parent):
        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=(0, 8), pady=(0, 2))

    groups = ttk.Frame(bar)
    groups.pack(side="left", fill="x", expand=True)

    _connection_group, connection_row, connection_label = _build_group(groups, "Connection")
    _add_group_separator(groups)
    _job_group, job_row, job_label = _build_group(groups, "Job")
    _add_group_separator(groups)
    _run_group, run_row, run_label = _build_group(groups, "Run")
    _add_group_separator(groups)
    _recovery_group, recovery_row, recovery_label = _build_group(groups, "Recovery")
    app._toolbar_connection_row = connection_row
    app._toolbar_job_row = job_row
    app._toolbar_run_row = run_row
    app._toolbar_recovery_row = recovery_row
    app._toolbar_group_title_labels = {
        "Connection": connection_label,
        "Job": job_label,
        "Run": run_label,
        "Recovery": recovery_label,
    }

    ttk.Label(connection_row, text="Port:").pack(side="left")
    app.port_combo = ttk.Combobox(connection_row, width=18, textvariable=app.current_port, state="readonly")
    app.port_combo.pack(side="left", padx=(6, 4))

    app.btn_refresh = ttk.Button(
        connection_row,
        text=icon_label(ICON_REFRESH, "Refresh"),
        style=app.icon_button_style,
        command=app.refresh_ports,
    )
    set_kb_id(app.btn_refresh, "port_refresh")
    app.btn_refresh.pack(side="left", padx=(0, 10))
    apply_tooltip(app.btn_refresh, "Refresh the list of serial ports.")
    app.btn_conn = ttk.Button(
        connection_row,
        text=icon_label(ICON_CONNECT, "Connect"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Connect/Disconnect", app.toggle_connect),
    )
    set_kb_id(app.btn_conn, "port_connect")
    app.btn_conn.pack(side="left")
    apply_tooltip(app.btn_conn, "Connect or disconnect from the selected serial port.")
    attach_log_gcode(app.btn_conn, "")

    app.btn_open = ttk.Button(
        job_row,
        text=icon_label(ICON_JOB_READ, "Read Job"),
        style=app.icon_button_style,
        command=app.open_gcode,
    )
    set_kb_id(app.btn_open, "gcode_open")
    app.btn_open.pack(side="left")
    app._manual_controls.append(app.btn_open)
    app._offline_controls.add(app.btn_open)
    apply_tooltip(app.btn_open, "Load a G-code job for streaming (read-only).")
    app.job_button_hint = ttk.Label(job_row, text="")
    try:
        app._job_button_hint_visible = False
    except Exception as exc:
        _log_suppressed("Failed initializing job-button hint visibility flag", exc)
    try:
        app._job_button_mode = "read_job"
    except Exception as exc:
        _log_suppressed("Failed initializing default job-button mode", exc)
    app.btn_clear = ttk.Button(
        job_row,
        text=icon_label(ICON_JOB_CLEAR, "Clear Job"),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Clear Job", app._clear_gcode),
    )
    set_kb_id(app.btn_clear, "gcode_clear")
    app.btn_clear.pack(side="left", padx=(6, 0))
    app._manual_controls.append(app.btn_clear)
    app._offline_controls.add(app.btn_clear)
    apply_tooltip(app.btn_clear, "Unload the current job and reset the live job state.")
    app.btn_run = ttk.Button(
        run_row,
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
        run_row,
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
        run_row,
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
        run_row,
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
        run_row,
        text=icon_label(ICON_RESUME_FROM, "Resume From..."),
        style=app.icon_button_style,
        command=lambda: app._confirm_and_run("Resume from line", app._show_resume_dialog),
        state="disabled",
    )
    set_kb_id(app.btn_resume_from, "job_resume_from")
    app.btn_resume_from.pack(side="left", padx=(6, 0))
    apply_tooltip(app.btn_resume_from, "Resume from a specific line with modal re-sync.")
    app.btn_unlock_top = ttk.Button(
        recovery_row,
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
        recovery_row,
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
    except Exception as exc:
        _log_suppressed("Failed reading toolbar base font size; using default", exc)
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
        fg=style.lookup("TLabel", "foreground") or "#000000",
        bg=bar_bg,
        padx=20,
        pady=6,
    )
    app.machine_state_label.pack(side="right", fill="y")
    try:
        app._state_default_bg = app.machine_state_label.cget("background")
    except Exception as exc:
        _log_suppressed("Failed caching default state-label background", exc)
    try:
        app._state_default_fg = app.machine_state_label.cget("foreground")
    except Exception as exc:
        _log_suppressed("Failed caching default state-label foreground", exc)
    try:
        app._machine_state_max_chars = len(MachineStateMessages.DISCONNECTED) + 2
        app.machine_state_label.config(width=app._machine_state_max_chars)
    except Exception as exc:
        _log_suppressed("Failed sizing machine-state label width", exc)
    try:
        app._ensure_state_label_width(app.machine_state.get())
    except Exception as exc:
        _log_suppressed("Failed enforcing machine-state label width", exc)
    refresh_toolbar_action_focus(app)


