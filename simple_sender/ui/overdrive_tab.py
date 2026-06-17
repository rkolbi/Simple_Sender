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

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.override_controls import send_override_realtime
from simple_sender.ui.theme_helpers import bind_touch_scale_theme, touch_scale_metrics
from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_keypad import attach_numeric_keypad
from simple_sender.ui.widgets_common import attach_log_gcode, set_kb_id
from simple_sender.utils.constants import (
    DEFAULT_SPINDLE_RPM,
    RT_SO_RESET,
)


def _coerce_non_negative_int(raw_value, fallback: int) -> int:
    try:
        value = int(round(float(raw_value)))
    except Exception:
        return int(fallback)
    return max(0, int(value))


def _spindle_control_rpm_from_settings(app) -> int:
    settings = getattr(app, "settings", {})
    if not isinstance(settings, dict):
        return int(DEFAULT_SPINDLE_RPM)
    return _coerce_non_negative_int(settings.get("spindle_control_rpm", DEFAULT_SPINDLE_RPM), DEFAULT_SPINDLE_RPM)


def _save_spindle_control_rpm_setting(app) -> int:
    current = _spindle_control_rpm_from_settings(app)
    var = getattr(app, "spindle_rpm_var", None)
    if var is None:
        return int(current)
    try:
        raw = var.get()
    except Exception:
        raw = str(current)
    rpm = _coerce_non_negative_int(raw, current)
    try:
        var.set(str(rpm))
    except Exception:
        pass
    settings = getattr(app, "settings", None)
    if not isinstance(settings, dict):
        return int(rpm)
    settings["spindle_control_rpm"] = int(rpm)
    return int(rpm)


def _current_spindle_rpm(app) -> int:
    try:
        with app.macro_executor.macro_vars() as macro_vars:
            return _coerce_non_negative_int(macro_vars.get("curspindle", 0), 0)
    except Exception:
        return 0


def _current_spindle_modal(app) -> str:
    try:
        with app.macro_executor.macro_vars() as macro_vars:
            token = str(macro_vars.get("spindle", "M5") or "M5").strip().upper()
    except Exception:
        token = "M5"
    if token not in {"M3", "M4", "M5"}:
        return "M5"
    return token


def _running_spindle_command_modal(app) -> str | None:
    rpm = _current_spindle_rpm(app)
    if rpm <= 0:
        return None
    modal = _current_spindle_modal(app)
    if modal in {"M3", "M4"}:
        return modal
    return "M3"


def _stream_busy(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if state in {"running", "paused"}:
        return True
    grbl = getattr(app, "grbl", None)
    checker = getattr(grbl, "is_streaming", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return False


def _report_spindle_rpm_blocked_while_streaming(app, rpm: int) -> None:
    status_text = f"Spindle RPM saved: {rpm} RPM; Apply RPM is blocked while streaming."
    try:
        app.status.config(text=status_text)
    except Exception:
        pass
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put((
                "log",
                "[spindle] Apply RPM saved the default RPM but did not send a speed command "
                "because a job is streaming. Use Spindle Override for in-job speed changes.",
            ))
        except Exception:
            pass


def _apply_spindle_rpm(app) -> None:
    rpm = _save_spindle_control_rpm_setting(app)
    modal = _running_spindle_command_modal(app)
    if modal is None:
        return
    if _stream_busy(app):
        _report_spindle_rpm_blocked_while_streaming(app, rpm)
        return

    command = f"{modal} S{rpm}"

    def _command() -> None:
        if not send_override_realtime(app, RT_SO_RESET, label="Spindle override reset"):
            return
        try:
            app._set_spindle_override_slider_value(100)
        except Exception:
            pass
        accepted = bool(app.grbl.send_immediate(command))
        if accepted:
            try:
                app.status.config(text=f"Spindle speed updated: {rpm} RPM")
            except Exception:
                pass
            return
        try:
            app.status.config(text=f"Spindle speed update rejected: controller did not accept {command}")
        except Exception:
            pass
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("log", f"[spindle] Controller rejected {command}; spindle speed remains unchanged."))
            except Exception:
                pass

    app._confirm_and_run("Apply spindle RPM", _command)


def _run_spindle_on(app) -> None:
    rpm = _save_spindle_control_rpm_setting(app)
    def _command() -> None:
        accepted = bool(app.grbl.spindle_on(rpm))
        if accepted:
            return
        try:
            app.status.config(text=f"Spindle ON rejected: controller did not accept M3 S{rpm}")
        except Exception:
            pass
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("log", f"[spindle] Controller rejected M3 S{rpm}; spindle remains unchanged."))
            except Exception:
                pass

    app._confirm_and_run("Spindle ON", _command)


def _run_spindle_off(app) -> None:
    def _command() -> None:
        accepted = bool(app.grbl.spindle_off())
        if accepted:
            return
        try:
            app.status.config(text="Spindle OFF rejected: controller did not accept M5")
        except Exception:
            pass
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("log", "[spindle] Controller rejected M5; spindle remains unchanged."))
            except Exception:
                pass

    app._confirm_and_run("Spindle OFF", _command)


def _override_scale_metrics(app) -> tuple[int, int]:
    return touch_scale_metrics(app)


def _build_override_section(
    app,
    parent,
    *,
    title: str,
    scale_attr: str,
    value_label_attr: str,
    display_var,
    slider_command,
) -> None:
    section = ttk.Labelframe(parent, text=title, padding=8)
    section.pack(fill="x", pady=(0, 10))
    control_row = ttk.Frame(section)
    control_row.pack(fill="x")
    slider_host = ttk.Frame(control_row)
    slider_host.pack(side="left", fill="x", expand=True, pady=(2, 2))
    scale = tk.Scale(
        slider_host,
        from_=10,
        to=200,
        orient="horizontal",
        command=slider_command,
    )
    bind_touch_scale_theme(app, scale)
    scale.pack(fill="x")
    setattr(app, scale_attr, scale)
    scale.set(100)
    value_label = ttk.Label(control_row, textvariable=display_var)
    value_label.pack(side="right", padx=(10, 0))
    setattr(app, value_label_attr, value_label)


def build_overdrive_tab(app, parent):
    content = ttk.Frame(parent)
    content.pack(fill="both", expand=True)
    app.overdrive_scrollbar = None

    _build_override_section(
        app,
        content,
        title="Feed Override",
        scale_attr="feed_override_scale",
        value_label_attr="feed_override_value_label",
        display_var=app.feed_override_display,
        slider_command=app._on_feed_override_slider,
    )

    _build_override_section(
        app,
        content,
        title="Spindle Override",
        scale_attr="spindle_override_scale",
        value_label_attr="spindle_override_value_label",
        display_var=app.spindle_override_display,
        slider_command=app._on_spindle_override_slider,
    )

    spindle_frame = ttk.Labelframe(content, text="Spindle Control", padding=8)
    spindle_frame.pack(fill="x", pady=(0, 10))
    spindle_btn_row = ttk.Frame(spindle_frame)
    spindle_btn_row.pack(fill="x")
    app.btn_spindle_on = ttk.Button(
        spindle_btn_row,
        text="Spindle ON",
        command=lambda: _run_spindle_on(app),
    )
    set_kb_id(app.btn_spindle_on, "spindle_on")
    app.btn_spindle_on.pack(side="left", padx=(0, 6))
    app._manual_controls.append(app.btn_spindle_on)
    apply_tooltip(app.btn_spindle_on, "Turn spindle on at the configured RPM.")
    attach_log_gcode(app.btn_spindle_on, lambda: f"M3 S{_save_spindle_control_rpm_setting(app)}")

    app.btn_spindle_off = ttk.Button(
        spindle_btn_row,
        text="Spindle OFF",
        command=lambda: _run_spindle_off(app),
    )
    set_kb_id(app.btn_spindle_off, "spindle_off")
    app.btn_spindle_off.pack(side="left")
    app._manual_controls.append(app.btn_spindle_off)
    apply_tooltip(app.btn_spindle_off, "Turn spindle off.")
    attach_log_gcode(app.btn_spindle_off, "M5")

    app.spindle_current_rpm_var = tk.StringVar(value=str(_current_spindle_rpm(app)))
    current_row = ttk.Frame(spindle_frame)
    current_row.pack(fill="x", pady=(8, 4))
    ttk.Label(current_row, text="Current spindle speed:").pack(side="left")
    ttk.Label(current_row, textvariable=app.spindle_current_rpm_var).pack(side="left", padx=(8, 2))
    ttk.Label(current_row, text="RPM").pack(side="left")

    app.spindle_rpm_var = tk.StringVar(value=str(_spindle_control_rpm_from_settings(app)))
    rpm_row = ttk.Frame(spindle_frame)
    rpm_row.pack(fill="x", pady=(2, 0))
    ttk.Label(rpm_row, text="Spindle RPM:").pack(side="left")
    app.spindle_rpm_entry = ttk.Entry(rpm_row, textvariable=app.spindle_rpm_var, width=10)
    app.spindle_rpm_entry.pack(side="left", padx=(8, 6))
    attach_numeric_keypad(
        app.spindle_rpm_entry,
        allow_decimal=False,
        allow_negative=False,
        allow_empty=False,
    )
    app.spindle_rpm_entry.bind("<Return>", lambda _event: _save_spindle_control_rpm_setting(app))
    app.spindle_rpm_entry.bind("<FocusOut>", lambda _event: _save_spindle_control_rpm_setting(app))
    app.btn_spindle_rpm_apply = ttk.Button(
        rpm_row,
        text="Apply RPM",
        command=lambda: _apply_spindle_rpm(app),
    )
    app.btn_spindle_rpm_apply.pack(side="left")
    apply_tooltip(
        app.spindle_rpm_entry,
        "Set the RPM used by Spindle ON.",
    )
    apply_tooltip(
        app.btn_spindle_rpm_apply,
        "Apply the RPM when not streaming, or save it for Spindle ON.",
    )

    tools_frame = ttk.Labelframe(content, text="Tools", padding=8)
    tools_frame.pack(fill="x", pady=(0, 10))
    app.btn_spoilboard = ttk.Button(
        tools_frame,
        text="Spoilboard",
        command=lambda: app._confirm_and_run("Spoilboard Generator", app._show_spoilboard_generator_dialog),
    )
    set_kb_id(app.btn_spoilboard, "spoilboard_generator")
    app.btn_spoilboard.pack(side="left")
    app._manual_controls.append(app.btn_spoilboard)
    app._offline_controls.add(app.btn_spoilboard)
    apply_tooltip(app.btn_spoilboard, "Generate spoilboard surfacing G-code.")

    app._set_feed_override_slider_value(100)
    app._set_spindle_override_slider_value(100)
