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
from typing import Any

from simple_sender.ui import joystick_hold
from simple_sender.ui import input_bindings_joystick as joystick_bindings
from simple_sender.ui.widgets import StopSignButton, VirtualHoldButton
from simple_sender.utils.constants import CLEAR_ICON


def update_keyboard_live_status(app, label: str | None = None) -> None:
    if not hasattr(app, "keyboard_live_status"):
        return
    mods = []
    for keysym in app._kb_mod_keys_down:
        name = app._kb_mod_keysyms.get(keysym, keysym)
        if name in ("Ctrl", "Shift", "Alt") and name not in mods:
            mods.append(name)
    mod_order = ("Ctrl", "Shift", "Alt")
    ordered_mods = [m for m in mod_order if m in mods]
    if label:
        text = f"Keyboard: {label}"
    elif ordered_mods:
        text = f"Keyboard: {'+'.join(ordered_mods)} held"
    else:
        text = "Keyboard: idle"
    app.keyboard_live_status.set(text)

def apply_keyboard_bindings(app):
    for seq in ("<KeyPress>", "<KeyRelease>"):
        if seq in app._bound_key_sequences:
            app.unbind_all(seq)
    if app._bound_key_sequences:
        app._bound_key_sequences.clear()
    app._kb_mod_keys_down.clear()
    app._key_sequence_map = {}
    app._kb_conflicts = set()
    for btn in app._collect_buttons():
        binding_id = app._button_binding_id(btn)
        if binding_id in app._key_bindings:
            label = app._normalize_key_label(str(app._key_bindings.get(binding_id, "")).strip())
            if not label:
                continue
            is_custom = True
        else:
            label = app._default_key_for_button(btn)
            if not label:
                continue
            is_custom = False
        seq = app._key_sequence_tuple(label)
        if not seq:
            continue
        conflict_seq = app._sequence_conflict(seq, app._key_sequence_map)
        if conflict_seq:
            other_btn = app._key_sequence_map.get(conflict_seq)
            other_id = app._button_binding_id(other_btn) if other_btn else ""
            if binding_id in app._key_bindings:
                app._key_bindings[binding_id] = ""
            app._kb_conflicts.add(binding_id)
            if other_id:
                if other_id in app._key_bindings:
                    app._key_bindings[other_id] = ""
                app._kb_conflicts.add(other_id)
                app._key_sequence_map.pop(conflict_seq, None)
            continue
        app._key_sequence_map[seq] = btn
    app._refresh_keyboard_table()
    if not bool(app.keyboard_bindings_enabled.get()):
        app._clear_key_sequence_buffer()
        return
    app._bound_key_sequences.add("<KeyPress>")
    app._bound_key_sequences.add("<KeyRelease>")
    app.bind_all("<KeyPress>", app._on_key_sequence, add="+")
    app.bind_all("<KeyRelease>", app._on_key_modifier_release, add="+")

def refresh_keyboard_table(app):
    if not hasattr(app, "kb_table"):
        return
    app.kb_table.delete(*app.kb_table.get_children())
    app.kb_table.tag_configure("conflict", background="#f7d6d6")
    app._kb_item_to_button = {}
    app._joystick_binding_map.clear()
    for btn in app._collect_buttons():
        binding_id = app._button_binding_id(btn)
        label = app._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        if tip:
            label = f"{label} - {tip}"
        axis = app._button_axis_name(btn)
        key = app._keyboard_key_for_button(btn)
        if not key:
            key = "None"
        joystick_label = "None"
        binding = app._joystick_bindings.get(binding_id)
        if binding:
            display = app._joystick_binding_display(binding)
            if display:
                joystick_label = display
            tuple_key = app._joystick_binding_key(binding)
            if tuple_key:
                app._joystick_binding_map[tuple_key] = btn
        tags = ("conflict",) if binding_id in app._kb_conflicts else ()
        item = app.kb_table.insert(
            "",
            "end",
            values=(label, axis, key, joystick_label, f"{CLEAR_ICON}  Remove/Clear Binding"),
            tags=tags,
        )
        app._kb_item_to_button[item] = btn

def create_virtual_hold_buttons(app) -> list[VirtualHoldButton]:
    buttons: list[VirtualHoldButton] = []
    for label, binding_id, axis, direction in joystick_hold.JOYSTICK_HOLD_DEFINITIONS:
        buttons.append(VirtualHoldButton(f"{label} (Hold)", binding_id, axis, direction))
    return buttons

def collect_buttons(app) -> list:
    buttons = []
    seen = set()

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, (ttk.Button, tk.Button, StopSignButton)):
                if child not in seen:
                    seen.add(child)
                    buttons.append(child)
            walk(child)

    walk(app)
    if app._virtual_hold_buttons:
        buttons.extend(app._virtual_hold_buttons)
    buttons.sort(key=app._button_label)
    return buttons

def button_label(app, btn) -> str:
    label = ""
    try:
        label = btn.cget("text")
    except Exception:
        label = ""
    if not label:
        label = getattr(btn, "_text", "")
    if not label:
        label = getattr(btn, "_label", "")
    if not label:
        label = btn.winfo_name()
    label = label.replace("\n", " ").strip()
    if label.startswith("!"):
        tooltip = getattr(btn, "_tooltip_text", "")
        kb_id = getattr(btn, "_kb_id", "")
        meta = tooltip or kb_id or label
        label = f"{btn.winfo_class()} ({meta})"
    return label

def keyboard_key_for_button(app, btn) -> str:
    binding_id = app._button_binding_id(btn)
    if binding_id in app._kb_conflicts:
        return ""
    if binding_id in app._key_bindings:
        return app._normalize_key_label(str(app._key_bindings.get(binding_id, "")).strip())
    return app._default_key_for_button(btn)

def joystick_binding_display(app, binding: dict[str, Any]) -> str:
    return joystick_bindings.joystick_binding_display(app, binding)

def joystick_binding_key(app, binding: dict[str, Any]):
    return joystick_bindings.joystick_binding_key(app, binding)

def button_axis_name(app, btn) -> str:
    return joystick_bindings.button_axis_name(app, btn)

def button_binding_id(app, btn) -> str:
    kb_id = getattr(btn, "_kb_id", "")
    if kb_id:
        return kb_id
    label = app._button_label(btn)
    tip = getattr(btn, "_tooltip_text", "")
    name = btn.winfo_name()
    return f"{label}|{tip}|{name}"

def find_binding_conflict(app, target_btn, label: str):
    seq = app._key_sequence_tuple(label)
    if not seq:
        return None
    for btn in app._collect_buttons():
        if btn is target_btn:
            continue
        other_seq = app._key_sequence_tuple(app._keyboard_key_for_button(btn))
        if other_seq and app._sequence_conflict_pair(seq, other_seq):
            return btn
    return None

def default_key_for_button(app, btn) -> str:
    if btn is getattr(app, "btn_jog_cancel", None):
        return "Space"
    if btn is getattr(app, "btn_all_stop", None):
        return "Enter"
    return ""

def on_kb_table_double_click(app, event):
    if not hasattr(app, "kb_table"):
        return
    row = app.kb_table.identify_row(event.y)
    col = app.kb_table.identify_column(event.x)
    if not row or col != "#3":
        return
    app._start_kb_edit(row, col)

def on_kb_table_click(app, event):
    if not hasattr(app, "kb_table"):
        return
    row = app.kb_table.identify_row(event.y)
    col = app.kb_table.identify_column(event.x)
    if not row:
        return
    if col == "#3":
        app._start_kb_edit(row, col)
        return
    if col == "#4":
        app._start_joystick_capture(row)
        return
    if col != "#5":
        return
    btn = app._kb_item_to_button.get(row)
    if btn is None:
        return
    binding_id = app._button_binding_id(btn)
    app._key_bindings[binding_id] = ""
    if binding_id in app._joystick_bindings:
        app._joystick_bindings.pop(binding_id, None)
    app._apply_keyboard_bindings()

def start_kb_edit(app, row, col):
    bbox = app.kb_table.bbox(row, col)
    if not bbox:
        return
    if app._kb_edit is not None:
        try:
            app._kb_edit.destroy()
        except Exception:
            pass
        app._kb_edit = None
    x, y, w, h = bbox
    value = app.kb_table.set(row, "key")
    entry = ttk.Entry(app.kb_table)
    entry.place(x=x, y=y, width=w, height=h)
    entry.insert(0, "Press keys...")
    app._kb_edit_state[entry] = {
        "prev": "" if value == "None" else value,
        "placeholder": True,
        "seq": [],
        "after_id": None,
    }
    entry.focus()
    entry.bind("<KeyPress>", lambda e: app._kb_capture_key(e, row, entry))
    entry.bind("<FocusOut>", lambda e: app._commit_kb_edit(row, entry))
    app._kb_edit = entry

def start_joystick_capture(app, row):
    return joystick_bindings.start_joystick_capture(app, row)

def cancel_joystick_capture(app):
    joystick_bindings.cancel_joystick_capture(app)

def start_joystick_safety_capture(app):
    return joystick_bindings.start_joystick_safety_capture(app)

def cancel_joystick_safety_capture(app):
    joystick_bindings.cancel_joystick_safety_capture(app)

def clear_joystick_safety_binding(app):
    joystick_bindings.clear_joystick_safety_binding(app)

def on_joystick_safety_toggle(app):
    joystick_bindings.on_joystick_safety_toggle(app)

def joystick_binding_from_event(app, key):
    return joystick_bindings.joystick_binding_from_event(app, key)

def kb_capture_key(app, event, row, entry):
    state = app._kb_edit_state.get(entry)
    if state is None:
        return "break"
    if event.keysym in ("Escape",):
        try:
            entry.destroy()
        except Exception:
            pass
        app._kb_edit_state.pop(entry, None)
        app._kb_edit = None
        return "break"
    if event.keysym in ("BackSpace", "Delete"):
        app._commit_kb_edit(row, entry, label_override="")
        return "break"
    label = app._event_to_binding_label(event)
    if not label:
        return "break"
    seq = state["seq"]
    if len(seq) >= 3:
        return "break"
    seq.append(label)
    state["placeholder"] = False
    entry.delete(0, "end")
    entry.insert(0, " ".join(seq))
    after_id = state.get("after_id")
    if after_id is not None:
        entry.after_cancel(after_id)
    if len(seq) >= 3:
        app._commit_kb_edit(row, entry, label_override=" ".join(seq))
        return "break"
    state["after_id"] = entry.after(
        int(app._key_sequence_timeout * 1000),
        lambda: app._commit_kb_edit(row, entry, label_override=" ".join(seq)),
    )
    return "break"

def commit_kb_edit(app, row, entry, label_override: str | None = None):
    if app._kb_edit is None:
        return
    state = app._kb_edit_state.pop(entry, None)
    if label_override is None:
        try:
            new_val = entry.get()
        except Exception:
            new_val = ""
    else:
        new_val = label_override
    try:
        after_id = state.get("after_id") if state else None
        if after_id is not None:
            entry.after_cancel(after_id)
        entry.destroy()
    except Exception:
        pass
    app._kb_edit = None
    placeholder = state.get("placeholder") if state else False
    if label_override is None and placeholder:
        if new_val.strip() == "Press keys...":
            return
    btn = app._kb_item_to_button.get(row)
    if btn is None:
        return
    label = app._normalize_key_label(new_val)
    binding_id = app._button_binding_id(btn)
    app._key_bindings[binding_id] = label
    app._apply_keyboard_bindings()

