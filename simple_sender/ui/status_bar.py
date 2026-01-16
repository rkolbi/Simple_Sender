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
# SPDX-License-Identifier: GPL-3.0-or-later

from tkinter import ttk

from simple_sender.ui.widgets import apply_tooltip, set_kb_id


def _bool_from_var(value, default=True) -> bool:
    if value is None:
        return default
    try:
        return bool(value.get())
    except Exception:
        return bool(value)


def build_status_bar(app, before):
    # Status bar
    status_bar = ttk.Frame(app, padding=(8, 0, 8, 6))
    status_bar.pack(side="bottom", fill="x", before=before)
    app.status = ttk.Label(status_bar, text="Disconnected", anchor="w")
    app.status.pack(side="left", fill="x", expand=True)
    ttk.Label(status_bar, text="Progress").pack(side="right")
    app.progress_bar = ttk.Progressbar(
        status_bar,
        orient="horizontal",
        length=140,
        mode="determinate",
        maximum=100,
        variable=app.progress_pct,
        style="SimpleSender.Blue.Horizontal.TProgressbar",
    )
    app.progress_bar.pack(side="right", padx=(6, 12))
    app.buffer_bar = ttk.Progressbar(
        status_bar,
        orient="horizontal",
        length=120,
        mode="determinate",
        maximum=100,
        variable=app.buffer_fill_pct,
        style="SimpleSender.Blue.Horizontal.TProgressbar",
    )
    app.buffer_bar.pack(side="right", padx=(6, 0))
    app.error_dialog_status_label = ttk.Label(
        status_bar,
        textvariable=app.error_dialog_status_var,
        anchor="e",
    )
    app.error_dialog_status_label.pack(side="right", padx=(6, 0))
    apply_tooltip(
        app.error_dialog_status_label,
        "Shows when error dialogs are disabled or suppressed.",
    )
    ttk.Label(status_bar, textvariable=app.buffer_fill, anchor="e").pack(side="right")
    app.throughput_label = ttk.Label(
        status_bar,
        textvariable=app.throughput_var,
        anchor="e",
    )
    app.throughput_label.pack(side="right", padx=(6, 0))
    app._build_led_panel(status_bar)
    app.btn_toggle_tips = ttk.Button(
        status_bar,
        text="Tips",
        command=app._toggle_tooltips,
    )
    set_kb_id(app.btn_toggle_tips, "toggle_tooltips")
    app.btn_toggle_tips.pack(side="right", padx=(8, 0))
    app.btn_toggle_3d = ttk.Button(
        status_bar,
        text="3DR",
        command=app._toggle_render_3d,
    )
    set_kb_id(app.btn_toggle_3d, "toggle_render_3d")
    app.btn_toggle_3d.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_toggle_3d, "Toggle 3D toolpath rendering.")
    app.btn_toggle_keybinds = ttk.Button(
        status_bar,
        text="Keys",
        command=app._toggle_keyboard_bindings,
    )
    set_kb_id(app.btn_toggle_keybinds, "toggle_keybindings")
    app.btn_toggle_keybinds.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_toggle_keybinds, "Toggle keyboard shortcuts.")
    app.btn_release_checklist = ttk.Button(
        status_bar,
        text="Release",
        command=app._show_release_checklist,
    )
    set_kb_id(app.btn_release_checklist, "release_checklist")
    app.btn_release_checklist.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_release_checklist, "Open the release checklist.")
    app._refresh_tooltips_toggle_text()
    app._refresh_render_3d_toggle_text()
    app._refresh_keybindings_toggle_text()
    update_quick_button_visibility(app)
    app._on_error_dialogs_enabled_change()
    if getattr(app, "_state_default_bg", None) is None:
        try:
            app._state_default_bg = app.machine_state_label.cget("background")
        except Exception:
            app._state_default_bg = app.status.cget("background") if app.status else None
    app._update_state_highlight(app._machine_state_text)


def update_quick_button_visibility(app):
    buttons = [
        ("btn_toggle_tips", app.show_quick_tips_button),
        ("btn_toggle_3d", app.show_quick_3d_button),
        ("btn_toggle_keybinds", app.show_quick_keys_button),
        ("btn_release_checklist", app.show_quick_release_button),
    ]
    for attr, _ in buttons:
        btn = getattr(app, attr, None)
        if btn:
            btn.pack_forget()
    for attr, var in buttons:
        btn = getattr(app, attr, None)
        if not btn:
            continue
        if _bool_from_var(var, True):
            btn.pack(side="right", padx=(8, 0))


def on_quick_button_visibility_change(app):
    app.settings["show_quick_tips_button"] = bool(app.show_quick_tips_button.get())
    app.settings["show_quick_3d_button"] = bool(app.show_quick_3d_button.get())
    app.settings["show_quick_keys_button"] = bool(app.show_quick_keys_button.get())
    app.settings["show_quick_release_button"] = bool(app.show_quick_release_button.get())
    update_quick_button_visibility(app)

