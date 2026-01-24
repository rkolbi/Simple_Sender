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

from tkinter import ttk

from simple_sender.ui.widgets import apply_tooltip, attach_log_gcode, set_kb_id
from simple_sender.utils.constants import (
    DEFAULT_SPINDLE_RPM,
    RT_FO_MINUS_10,
    RT_FO_PLUS_10,
    RT_FO_RESET,
    RT_SO_MINUS_10,
    RT_SO_PLUS_10,
    RT_SO_RESET,
)


def build_overdrive_tab(app, parent):
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True)

    spindle_frame = ttk.Labelframe(container, text="Spindle Control", padding=8)
    spindle_frame.pack(fill="x", pady=(0, 10))
    app.btn_spindle_on = ttk.Button(
        spindle_frame,
        text="Spindle ON",
        command=lambda: app._confirm_and_run("Spindle ON", lambda: app.grbl.spindle_on(DEFAULT_SPINDLE_RPM)),
    )
    set_kb_id(app.btn_spindle_on, "spindle_on")
    app.btn_spindle_on.pack(side="left", padx=(0, 6))
    app._manual_controls.append(app.btn_spindle_on)
    apply_tooltip(app.btn_spindle_on, "Turn spindle on at default RPM.")
    attach_log_gcode(app.btn_spindle_on, f"M3 S{DEFAULT_SPINDLE_RPM}")

    app.btn_spindle_off = ttk.Button(
        spindle_frame,
        text="Spindle OFF",
        command=lambda: app._confirm_and_run("Spindle OFF", app.grbl.spindle_off),
    )
    set_kb_id(app.btn_spindle_off, "spindle_off")
    app.btn_spindle_off.pack(side="left")
    app._manual_controls.append(app.btn_spindle_off)
    apply_tooltip(app.btn_spindle_off, "Turn spindle off.")
    attach_log_gcode(app.btn_spindle_off, "M5")

    info_label = ttk.Label(container, textvariable=app.override_info_var, anchor="center")
    info_label.pack(fill="x", pady=(0, 4))
    note_label = ttk.Label(
        container,
        text="Note: GRBL 1.1h feed/spindle overrides move in 10% steps.",
        anchor="center",
        wraplength=520,
    )
    note_label.pack(fill="x", pady=(0, 10))

    feed_frame = ttk.Labelframe(container, text="Feed Override", padding=8)
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

    spindle_override_frame = ttk.Labelframe(container, text="Spindle Override", padding=8)
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

