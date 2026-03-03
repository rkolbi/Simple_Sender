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

import sys

from tkinter import ttk
from typing import Any

from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_common import set_kb_id


def _bool_from_var(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    try:
        return bool(value.get())
    except Exception:
        return bool(value)


def _value_from_var(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return value.get()
    except Exception:
        return value


def _tab_label(app) -> str:
    nb = getattr(app, "notebook", None)
    if nb is None:
        return ""
    try:
        tab_id = nb.select()
        if not tab_id:
            return ""
        return str(nb.tab(tab_id, "text") or "")
    except Exception:
        return ""


def _has_loaded_job(app) -> bool:
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


def _stream_busy(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    return str(getattr(app, "_stream_state", "") or "").lower() in ("running", "paused")


def _kasa_quick_button_state(app) -> dict[str, tuple[bool, str | None]]:
    linux_supported = bool(sys.platform.startswith("linux"))
    if not linux_supported:
        reason = "Kasa quick controls are available on Linux only."
        return {
            "btn_toggle_kasa_vacuum": (False, reason),
            "btn_toggle_kasa_light": (False, reason),
        }

    kasa_enabled = _bool_from_var(getattr(app, "kasa_enabled", None), False)
    device_identifier = str(
        _value_from_var(getattr(app, "kasa_device_identifier", None), "") or ""
    ).strip()
    has_device = bool(device_identifier)
    try:
        outlet_count = max(1, int(getattr(app, "_kasa_outlet_count", 2) or 2))
    except Exception:
        outlet_count = 2
    vacuum_enabled = _bool_from_var(getattr(app, "vacuum_enabled", None), False)
    light_enabled = _bool_from_var(getattr(app, "light_enabled", None), False)

    if not kasa_enabled:
        reason = "Enable Kasa Plug control in App Settings."
        return {
            "btn_toggle_kasa_vacuum": (False, reason),
            "btn_toggle_kasa_light": (False, reason),
        }
    if not has_device:
        reason = "Select a Kasa device in App Settings."
        return {
            "btn_toggle_kasa_vacuum": (False, reason),
            "btn_toggle_kasa_light": (False, reason),
        }

    vac_state: tuple[bool, str | None] = (
        bool(vacuum_enabled),
        None if vacuum_enabled else "Enable Vacuum mapping in App Settings.",
    )
    light_state: tuple[bool, str | None]
    if outlet_count < 2:
        light_state = (False, "Selected Kasa device has one outlet.")
    else:
        light_state = (
            bool(light_enabled),
            None if light_enabled else "Enable Spindle Light mapping in App Settings.",
        )
    return {
        "btn_toggle_kasa_vacuum": vac_state,
        "btn_toggle_kasa_light": light_state,
    }


def _set_widget_state(widget: Any, state: str) -> None:
    if widget is None:
        return
    try:
        widget.config(state=state)
    except Exception:
        return


def _context_quick_visibility(app) -> dict[str, bool]:
    label = _tab_label(app).strip().lower()
    on_toolpath_tab = label in ("3d view", "top view")
    has_job = _has_loaded_job(app)
    connected = bool(getattr(app, "connected", False))
    alarm_locked = bool(getattr(app, "_alarm_locked", False))
    busy = _stream_busy(app)
    force_override = False
    checker = getattr(app, "_is_force_3d_override_enabled", None)
    if callable(checker):
        try:
            force_override = bool(checker())
        except Exception:
            force_override = False
    render_enabled = _bool_from_var(getattr(app, "render3d_enabled", None), True)
    render_blocked = bool(getattr(app, "_render3d_blocked", False))
    if force_override:
        render_enabled = True
        render_blocked = False
    autolevel_overlay_enabled = _bool_from_var(getattr(app, "show_autolevel_overlay", None), True)
    has_autolevel_grid = getattr(app, "_auto_level_grid", None) is not None
    linux_supported = bool(sys.platform.startswith("linux"))

    return {
        "btn_toggle_tips": True,
        "btn_toggle_keybinds": True,
        "btn_release_checklist": connected and not busy and not alarm_locked,
        "btn_toggle_3d": has_job and (on_toolpath_tab or (not render_enabled) or render_blocked),
        "btn_toggle_autolevel_overlay": autolevel_overlay_enabled or (has_autolevel_grid and on_toolpath_tab),
        "btn_toggle_kasa_vacuum": linux_supported,
        "btn_toggle_kasa_light": linux_supported,
    }


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
    app.btn_toggle_autolevel_overlay = ttk.Button(
        status_bar,
        text="ALO",
        command=app._toggle_autolevel_overlay,
    )
    set_kb_id(app.btn_toggle_autolevel_overlay, "toggle_autolevel_overlay")
    app.btn_toggle_autolevel_overlay.pack(side="right", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_autolevel_overlay,
        "Toggle auto-level overlay in the toolpath views.",
    )
    app.btn_toggle_kasa_vacuum = ttk.Button(
        status_bar,
        text="Vac",
        command=app._toggle_kasa_vacuum_quick,
    )
    set_kb_id(app.btn_toggle_kasa_vacuum, "toggle_kasa_vacuum_quick")
    app.btn_toggle_kasa_vacuum.pack(side="right", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_kasa_vacuum,
        "Toggle the configured Kasa Vacuum outlet.",
    )
    app.btn_toggle_kasa_light = ttk.Button(
        status_bar,
        text="Light",
        command=app._toggle_kasa_light_quick,
    )
    set_kb_id(app.btn_toggle_kasa_light, "toggle_kasa_light_quick")
    app.btn_toggle_kasa_light.pack(side="right", padx=(8, 0))
    apply_tooltip(
        app.btn_toggle_kasa_light,
        "Toggle the configured Kasa Spindle Light outlet.",
    )
    app.btn_release_checklist = ttk.Button(
        status_bar,
        text="Release",
        command=app._show_release_checklist,
    )
    set_kb_id(app.btn_release_checklist, "release_checklist")
    app.btn_release_checklist.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_release_checklist, "Open the release checklist.")
    app.btn_screen_lock = ttk.Button(
        status_bar,
        text="Lock",
        command=app._toggle_screen_lock,
    )
    set_kb_id(app.btn_screen_lock, "screen_lock")
    app.btn_screen_lock.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_screen_lock, "Lock/unlock the screen. When locked, only this button accepts input.")
    app._refresh_tooltips_toggle_text()
    app._refresh_render_3d_toggle_text()
    app._refresh_keybindings_toggle_text()
    app._refresh_autolevel_overlay_button()
    app._refresh_kasa_quick_toggle_text()
    app._refresh_screen_lock_toggle_text()
    app._quick_button_visibility_signature = None
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
        ("btn_toggle_autolevel_overlay", app.show_quick_alo_button),
        ("btn_toggle_kasa_vacuum", app.show_quick_vac_button),
        ("btn_toggle_kasa_light", app.show_quick_light_button),
        ("btn_release_checklist", app.show_quick_release_button),
    ]
    context_map = _context_quick_visibility(app)
    kasa_state = _kasa_quick_button_state(app)
    visibility_map = {
        attr: bool(_bool_from_var(var, True) and context_map.get(attr, True))
        for attr, var in buttons
    }
    signature = tuple(
        (
            attr,
            visibility_map[attr],
            kasa_state[attr][0] if attr in kasa_state else None,
            kasa_state[attr][1] if attr in kasa_state else None,
        )
        for attr, var in buttons
    )
    if signature == getattr(app, "_quick_button_visibility_signature", None):
        return
    app._quick_button_visibility_signature = signature
    for attr, _ in buttons:
        btn = getattr(app, attr, None)
        if btn:
            btn.pack_forget()
    for attr, var in buttons:
        btn = getattr(app, attr, None)
        if not btn:
            continue
        if visibility_map[attr]:
            btn.pack(side="right", padx=(8, 0))
    for attr, (enabled, reason) in kasa_state.items():
        btn = getattr(app, attr, None)
        if btn is None:
            continue
        _set_widget_state(btn, "normal" if enabled else "disabled")
        try:
            btn._disabled_reason = None if enabled else str(reason or "Unavailable in current state.")
        except Exception:
            pass


def on_quick_button_visibility_change(app):
    app.settings["show_quick_tips_button"] = bool(app.show_quick_tips_button.get())
    app.settings["show_quick_3d_button"] = bool(app.show_quick_3d_button.get())
    app.settings["show_quick_keys_button"] = bool(app.show_quick_keys_button.get())
    app.settings["show_quick_alo_button"] = bool(app.show_quick_alo_button.get())
    app.settings["show_quick_vac_button"] = bool(app.show_quick_vac_button.get())
    app.settings["show_quick_light_button"] = bool(app.show_quick_light_button.get())
    app.settings["show_quick_release_button"] = bool(app.show_quick_release_button.get())
    update_quick_button_visibility(app)
