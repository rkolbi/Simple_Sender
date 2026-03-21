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

from simple_sender.ui.scrollable_container import build_scrollable_container
from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_keypad import attach_numeric_keypad
from simple_sender.ui.widgets_common import attach_log_gcode, set_kb_id
from simple_sender.ui.overdrive_validation import (
    cancel_validation,
    copy_validation_report,
    export_validation_report,
    refresh_validation_controls,
    start_validation,
)
from simple_sender.utils.constants import (
    DEFAULT_SPINDLE_RPM,
    RT_FO_MINUS_10,
    RT_FO_PLUS_10,
    RT_FO_RESET,
    RT_SO_MINUS_10,
    RT_SO_PLUS_10,
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


def _run_spindle_on(app) -> None:
    rpm = _save_spindle_control_rpm_setting(app)
    app._confirm_and_run("Spindle ON", lambda: app.grbl.spindle_on(rpm))


def build_overdrive_tab(app, parent):
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True)
    scroll_container = build_scrollable_container(
        container,
        tk_module=tk,
        ttk_module=ttk,
        bind_mousewheel_support=True,
    )
    content = scroll_container.content

    _bool_var = getattr(tk, "BooleanVar", None)
    if not callable(_bool_var):
        _bool_var = getattr(tk, "StringVar", None)
    if not callable(_bool_var):
        class _FallbackVar:
            def __init__(self, value=True) -> None:
                self._value = bool(value)

            def get(self):
                return self._value

            def set(self, value) -> None:
                self._value = bool(value)

        _bool_var = _FallbackVar
    _int_var = getattr(tk, "IntVar", None)
    if not callable(_int_var):
        _int_var = getattr(tk, "StringVar", None)
    if not callable(_int_var):
        _int_var = _bool_var
    if not hasattr(app, "overdrive_validation_stop_on_first_error"):
        app.overdrive_validation_stop_on_first_error = _bool_var(value=True)
    if not hasattr(app, "validate_streaming_gcode"):
        app.validate_streaming_gcode = _bool_var(value=False)
    if not hasattr(app, "overdrive_validation_status_var"):
        app.overdrive_validation_status_var = tk.StringVar(value="Idle")
    if not hasattr(app, "overdrive_validation_summary_var"):
        app.overdrive_validation_summary_var = tk.StringVar(value="")
    if not hasattr(app, "overdrive_validation_progress_pct"):
        app.overdrive_validation_progress_pct = _int_var(value=0)
    if not hasattr(app, "_overdrive_validation_run_id"):
        app._overdrive_validation_run_id = 0
    if not hasattr(app, "_overdrive_validation_running"):
        app._overdrive_validation_running = False
    if not hasattr(app, "_overdrive_validation_cancel_event"):
        app._overdrive_validation_cancel_event = None
    if not hasattr(app, "_overdrive_validation_worker"):
        app._overdrive_validation_worker = None
    if not hasattr(app, "_overdrive_validation_last_result"):
        app._overdrive_validation_last_result = None
    if not hasattr(app, "_overdrive_validation_last_report_text"):
        app._overdrive_validation_last_report_text = ""
    if not hasattr(app, "_overdrive_validation_last_progress_pct"):
        app._overdrive_validation_last_progress_pct = -1.0

    validate_frame = ttk.Labelframe(content, text="Validate G-code File", padding=8)
    validate_frame.pack(fill="x", pady=(0, 10))

    validate_btn_row = ttk.Frame(validate_frame)
    validate_btn_row.pack(fill="x")
    app.btn_overdrive_validate = ttk.Button(
        validate_btn_row,
        text="Validate Loaded Job",
        command=lambda: start_validation(app),
    )
    app.btn_overdrive_validate.pack(side="left")
    app.btn_overdrive_validate_cancel = ttk.Button(
        validate_btn_row,
        text="Cancel",
        command=lambda: cancel_validation(app),
        state="disabled",
    )
    app.btn_overdrive_validate_cancel.pack(side="left", padx=(6, 0))
    app.btn_overdrive_validate_export = ttk.Button(
        validate_btn_row,
        text="Export report",
        command=lambda: export_validation_report(app),
        state="disabled",
    )
    app.btn_overdrive_validate_export.pack(side="left", padx=(6, 0))
    app.btn_overdrive_validate_copy = ttk.Button(
        validate_btn_row,
        text="Copy",
        command=lambda: copy_validation_report(app),
        state="disabled",
    )
    app.btn_overdrive_validate_copy.pack(side="left", padx=(6, 0))

    validate_opts_row = ttk.Frame(validate_frame)
    validate_opts_row.pack(fill="x", pady=(6, 0))
    checkbutton_cls = getattr(ttk, "Checkbutton", None)
    if not callable(checkbutton_cls):
        checkbutton_cls = getattr(ttk, "Button", None)
    if not callable(checkbutton_cls):
        class _CheckbuttonFallback:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def pack(self, **_kwargs) -> None:
                return None

        checkbutton_cls = _CheckbuttonFallback
    app.chk_overdrive_validate_strict = checkbutton_cls(
        validate_opts_row,
        text="Validate strict (full scan)",
        variable=app.validate_streaming_gcode,
    )
    app.chk_overdrive_validate_strict.pack(side="left")
    app.chk_overdrive_validate_stop_first = checkbutton_cls(
        validate_opts_row,
        text="Stop on first error",
        variable=app.overdrive_validation_stop_on_first_error,
    )
    app.chk_overdrive_validate_stop_first.pack(side="left", padx=(10, 0))

    progress_row = ttk.Frame(validate_frame)
    progress_row.pack(fill="x", pady=(6, 0))
    progressbar_cls = getattr(ttk, "Progressbar", None)
    if not callable(progressbar_cls):
        class _ProgressbarFallback:
            def __init__(self, *_args, **_kwargs) -> None:
                self.value = 0.0
                self.mode = "determinate"

            def pack(self, **_kwargs) -> None:
                return None

            def configure(self, **kwargs) -> None:
                if "value" in kwargs:
                    self.value = float(kwargs["value"])
                if "mode" in kwargs:
                    self.mode = str(kwargs["mode"])

            def start(self, _ms=0) -> None:
                return None

            def stop(self) -> None:
                return None

        progressbar_cls = _ProgressbarFallback
    app.overdrive_validation_progress_bar = progressbar_cls(
        progress_row, orient="horizontal", mode="determinate", maximum=100
    )
    app.overdrive_validation_progress_bar.pack(side="left", fill="x", expand=True)
    app.overdrive_validation_progress_label = ttk.Label(
        progress_row,
        textvariable=app.overdrive_validation_progress_pct,
        width=5,
        anchor="e",
    )
    app.overdrive_validation_progress_label.pack(side="right", padx=(8, 0))

    ttk.Label(
        validate_frame,
        textvariable=app.overdrive_validation_status_var,
        anchor="w",
    ).pack(fill="x", pady=(4, 0))
    ttk.Label(
        validate_frame,
        textvariable=app.overdrive_validation_summary_var,
        anchor="w",
        wraplength=650,
    ).pack(fill="x", pady=(2, 0))

    text_cls = getattr(tk, "Text", None)
    scrollbar_cls = getattr(ttk, "Scrollbar", None)
    if callable(text_cls) and callable(scrollbar_cls):
        validate_results_row = ttk.Frame(validate_frame)
        validate_results_row.pack(fill="both", expand=True, pady=(6, 0))
        app.overdrive_validation_results_text = text_cls(
            validate_results_row,
            height=8,
            wrap="word",
            state="disabled",
        )
        app.overdrive_validation_results_text.pack(side="left", fill="both", expand=True)
        validate_results_scroll = scrollbar_cls(
            validate_results_row,
            orient="vertical",
            command=app.overdrive_validation_results_text.yview,
        )
        validate_results_scroll.pack(side="right", fill="y")
        app.overdrive_validation_results_text.configure(
            yscrollcommand=validate_results_scroll.set
        )
    else:
        class _TextFallback:
            def __init__(self) -> None:
                self._text = ""

            def configure(self, **_kwargs) -> None:
                return None

            def delete(self, *_args) -> None:
                self._text = ""

            def insert(self, _idx, text) -> None:
                self._text = str(text)

        app.overdrive_validation_results_text = _TextFallback()

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
        command=lambda: app._confirm_and_run("Spindle OFF", app.grbl.spindle_off),
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
        command=lambda: _save_spindle_control_rpm_setting(app),
    )
    app.btn_spindle_rpm_apply.pack(side="left")
    apply_tooltip(
        app.spindle_rpm_entry,
        "Set the RPM used by Spindle ON.",
    )
    apply_tooltip(
        app.btn_spindle_rpm_apply,
        "Save the Spindle ON RPM.",
    )

    info_label = ttk.Label(content, textvariable=app.override_info_var, anchor="center")
    info_label.pack(fill="x", pady=(0, 4))
    note_label = ttk.Label(
        content,
        text="Note: GRBL 1.1h feed/spindle overrides move in 10% steps.",
        anchor="center",
        wraplength=520,
    )
    note_label.pack(fill="x", pady=(0, 10))

    feed_frame = ttk.Labelframe(content, text="Feed Override", padding=8)
    feed_frame.pack(fill="x", pady=(0, 10))
    feed_slider_row = ttk.Frame(feed_frame)
    feed_slider_row.pack(fill="x", pady=(0, 6))
    app.feed_override_scale = ttk.Scale(
        feed_slider_row,
        from_=10,
        to=200,
        orient="horizontal",
        command=app._on_feed_override_slider,
    )
    app.feed_override_scale.pack(side="left", fill="x", expand=True)
    app.feed_override_scale.set(100)
    ttk.Label(feed_slider_row, textvariable=app.feed_override_display).pack(side="right", padx=(10, 0))

    feed_btn_row = ttk.Frame(feed_frame)
    feed_btn_row.pack(fill="x")
    app.btn_fo_plus = ttk.Button(feed_btn_row, text="+10%", command=lambda: app.grbl.send_realtime(RT_FO_PLUS_10))
    set_kb_id(app.btn_fo_plus, "feed_override_plus_10")
    app.btn_fo_plus.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_fo_plus)
    app._override_controls.append(app.btn_fo_plus)
    apply_tooltip(app.btn_fo_plus, "Increase feed override by 10%.")
    attach_log_gcode(app.btn_fo_plus, "RT 0x91")

    app.btn_fo_minus = ttk.Button(feed_btn_row, text="-10%", command=lambda: app.grbl.send_realtime(RT_FO_MINUS_10))
    set_kb_id(app.btn_fo_minus, "feed_override_minus_10")
    app.btn_fo_minus.pack(side="left", expand=True, fill="x", padx=6)
    app._manual_controls.append(app.btn_fo_minus)
    app._override_controls.append(app.btn_fo_minus)
    apply_tooltip(app.btn_fo_minus, "Decrease feed override by 10%.")
    attach_log_gcode(app.btn_fo_minus, "RT 0x92")

    app.btn_fo_reset = ttk.Button(feed_btn_row, text="Reset", command=lambda: app.grbl.send_realtime(RT_FO_RESET))
    set_kb_id(app.btn_fo_reset, "feed_override_reset")
    app.btn_fo_reset.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_fo_reset)
    app._override_controls.append(app.btn_fo_reset)
    apply_tooltip(app.btn_fo_reset, "Reset feed override to 100%.")
    attach_log_gcode(app.btn_fo_reset, "RT 0x90")

    spindle_override_frame = ttk.Labelframe(content, text="Spindle Override", padding=8)
    spindle_override_frame.pack(fill="x", pady=(0, 10))
    spindle_slider_row = ttk.Frame(spindle_override_frame)
    spindle_slider_row.pack(fill="x", pady=(0, 6))
    app.spindle_override_scale = ttk.Scale(
        spindle_slider_row,
        from_=10,
        to=200,
        orient="horizontal",
        command=app._on_spindle_override_slider,
    )
    app.spindle_override_scale.pack(side="left", fill="x", expand=True)
    app.spindle_override_scale.set(100)
    ttk.Label(spindle_slider_row, textvariable=app.spindle_override_display).pack(side="right", padx=(10, 0))

    spindle_btn_row = ttk.Frame(spindle_override_frame)
    spindle_btn_row.pack(fill="x")
    app.btn_so_plus = ttk.Button(spindle_btn_row, text="+10%", command=lambda: app.grbl.send_realtime(RT_SO_PLUS_10))
    set_kb_id(app.btn_so_plus, "spindle_override_plus_10")
    app.btn_so_plus.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_so_plus)
    app._override_controls.append(app.btn_so_plus)
    apply_tooltip(app.btn_so_plus, "Increase spindle override by 10%.")
    attach_log_gcode(app.btn_so_plus, "RT 0x9A")

    app.btn_so_minus = ttk.Button(spindle_btn_row, text="-10%", command=lambda: app.grbl.send_realtime(RT_SO_MINUS_10))
    set_kb_id(app.btn_so_minus, "spindle_override_minus_10")
    app.btn_so_minus.pack(side="left", expand=True, fill="x", padx=6)
    app._manual_controls.append(app.btn_so_minus)
    app._override_controls.append(app.btn_so_minus)
    apply_tooltip(app.btn_so_minus, "Decrease spindle override by 10%.")
    attach_log_gcode(app.btn_so_minus, "RT 0x9B")

    app.btn_so_reset = ttk.Button(spindle_btn_row, text="Reset", command=lambda: app.grbl.send_realtime(RT_SO_RESET))
    set_kb_id(app.btn_so_reset, "spindle_override_reset")
    app.btn_so_reset.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_so_reset)
    app._override_controls.append(app.btn_so_reset)
    apply_tooltip(app.btn_so_reset, "Reset spindle override to 100%.")
    attach_log_gcode(app.btn_so_reset, "RT 0x99")
    app._set_feed_override_slider_value(100)
    app._set_spindle_override_slider_value(100)
    app._refresh_override_info()
    refresh_validation_controls(app)

