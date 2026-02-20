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
import time
from tkinter import messagebox
from types import ModuleType
from typing import Any

from . import joystick as joystick_bindings
from .keys import (
    event_to_binding_label,
    key_sequence_tuple,
    modifier_active,
    normalize_key_chord,
    normalize_key_label,
    sequence_conflict,
    sequence_conflict_pair,
    update_modifier_state,
)
from .keyboard import (
    apply_keyboard_bindings,
    button_axis_name,
    button_binding_id,
    button_label,
    cancel_joystick_capture,
    cancel_joystick_safety_capture,
    clear_joystick_safety_binding,
    collect_buttons,
    commit_kb_edit,
    create_virtual_hold_buttons,
    default_key_for_button,
    find_binding_conflict,
    joystick_binding_display,
    joystick_binding_from_event,
    joystick_binding_key,
    kb_capture_key,
    keyboard_key_for_button,
    on_joystick_safety_toggle,
    on_kb_table_click,
    on_kb_table_double_click,
    refresh_keyboard_table,
    start_joystick_capture,
    start_joystick_safety_capture,
    start_kb_edit,
    update_keyboard_live_status,
)
from simple_sender.utils.constants import (
    JOYSTICK_DISCOVERY_CONNECTED_INTERVAL_MS,
    JOYSTICK_DISCOVERY_INTERVAL_MS,
    JOYSTICK_LIVE_STATUS_INTERVAL_MS,
)

logger = logging.getLogger(__name__)

PYGAME_IMPORT_ERROR = ""
pygame: ModuleType | None = None
PYGAME_AVAILABLE = False
try:
    import pygame as _pygame_module
except ImportError as exc:
    pygame = None
    PYGAME_IMPORT_ERROR = str(exc)
else:
    pygame = _pygame_module
    PYGAME_AVAILABLE = True

def toggle_keyboard_bindings(app):
    current = bool(app.keyboard_bindings_enabled.get())
    new_val = not current
    app.keyboard_bindings_enabled.set(new_val)
    app._refresh_keybindings_toggle_text()
    app._apply_keyboard_bindings()

def toggle_joystick_bindings(app):
    if not PYGAME_AVAILABLE:
        messagebox.showwarning(
            "Joystick bindings",
            "USB joystick support requires pygame. Install pygame and restart the application.",
        )
        return
    new_state = not bool(app.joystick_bindings_enabled.get())
    app.joystick_bindings_enabled.set(new_state)
    app._refresh_joystick_toggle_text()
    app._update_joystick_polling_state()

def on_keyboard_bindings_check(app):
    new_val = bool(app.keyboard_bindings_enabled.get())
    app._refresh_keybindings_toggle_text()
    app._apply_keyboard_bindings()

def refresh_joystick_toggle_text(app):
    if not hasattr(app, "btn_toggle_joystick_bindings"):
        return
    if not PYGAME_AVAILABLE:
        app.btn_toggle_joystick_bindings.config(text="Joystick support requires pygame", state="disabled")
        return
    text = (
        "Disable USB Joystick Bindings"
        if app.joystick_bindings_enabled.get()
        else "Enable USB Joystick Bindings"
    )
    app.btn_toggle_joystick_bindings.config(text=text, state="normal")
    app._refresh_joystick_test_info()

def update_joystick_polling_state(app):
    app._refresh_joystick_toggle_text()
    if not app.joystick_bindings_enabled.get():
        app._cancel_joystick_capture()
        app._stop_joystick_polling()
        app._stop_joystick_hold()
        app._joystick_safety_active = False
        if hasattr(app, "joystick_live_status"):
            app.joystick_live_status.set("Joystick state: disabled.")
        return
    if not app._ensure_joystick_backend():
        messagebox.showwarning(
            "Joystick bindings",
            "Failed to initialize the joystick backend. Check that pygame is installed and a joystick is connected.",
        )
        app.joystick_bindings_enabled.set(False)
        app._refresh_joystick_toggle_text()
        app._stop_joystick_polling()
        app._stop_joystick_hold()
        return
    app._start_joystick_polling()

def restore_joystick_bindings_on_start(app):
    if not getattr(app, "_joystick_auto_enable_requested", False):
        return
    if not hasattr(app, "btn_toggle_joystick_bindings"):
        app.after(100, app._restore_joystick_bindings_on_start)
        return
    app._joystick_auto_enable_requested = False
    if not app.joystick_bindings_enabled.get():
        return
    app._refresh_joystick_toggle_text()
    app._update_joystick_polling_state()

def get_pygame_module(app) -> ModuleType | None:
    if not PYGAME_AVAILABLE or pygame is None:
        return None
    return pygame

def discover_joysticks(app, py, count: int) -> list[str]:
    names: list[str] = []
    instances: dict[int, Any] = {}
    if count < 0:
        count = 0
    for idx in range(count):
        try:
            joy = py.joystick.Joystick(idx)
            joy.init()
            name = joy.get_name()
            instances[idx] = joy
        except Exception:
            name = f"Joystick {idx}"
        names.append(name)
    app._joystick_instances = instances
    app._joystick_button_poll_state.clear()
    app._joystick_axis_poll_state.clear()
    app._joystick_hat_poll_state.clear()
    app._joystick_axis_active.clear()
    app._joystick_hat_active.clear()
    return names

def update_joystick_test_status(app, count: int, names: list[str] | None = None) -> None:
    if not hasattr(app, "joystick_test_status"):
        return
    if count <= 0:
        app.joystick_test_status.set(
            "No joysticks detected. Plug in a controller to use joystick bindings."
        )
        return
    if names is None:
        names = [app._joystick_names.get(idx, f"Joystick {idx}") for idx in range(count)]
    lines = [f"{count} joystick(s) detected:"]
    for idx, name in enumerate(names):
        lines.append(f"- #{idx}: {name}")
    lines.append("Enable USB joystick bindings and press a button/axis/hat to map it.")
    app.joystick_test_status.set("\n".join(lines))

def update_joystick_device_status(app, count: int, reason: str | None = None) -> None:
    if not hasattr(app, "joystick_device_status"):
        return
    ts = time.strftime("%H:%M:%S")
    if reason:
        msg = f"Hot-plug: {reason} ({count} detected @ {ts})."
    elif count <= 0:
        msg = f"Hot-plug: no joystick detected (@ {ts})."
    else:
        msg = f"Hot-plug: {count} joystick(s) detected (@ {ts})."
    app.joystick_device_status.set(msg)

def update_joystick_live_status(app, py) -> None:
    if not hasattr(app, "joystick_live_status"):
        return
    now = time.monotonic()
    last = getattr(app, "_joystick_last_live_status", 0.0)
    if (now - last) < (JOYSTICK_LIVE_STATUS_INTERVAL_MS / 1000.0):
        return
    app._joystick_last_live_status = now
    if not app._joystick_instances:
        app.joystick_live_status.set("Joystick state: none detected.")
        return
    lines = []
    for joy_id, joy in sorted(app._joystick_instances.items()):
        try:
            axes_count = min(getattr(joy, "get_numaxes", lambda: 0)(), 4)
            axes = [f"{joy.get_axis(i):.2f}" for i in range(axes_count)]
            btn_count = getattr(joy, "get_numbuttons", lambda: 0)()
            pressed = [str(i) for i in range(btn_count) if joy.get_button(i)]
            pressed = pressed[:6]
            hat_count = getattr(joy, "get_numhats", lambda: 0)()
            hats = []
            for i in range(hat_count):
                value = joy.get_hat(i)
                if value != (0, 0):
                    hats.append(f"{i}:{value}")
            hats = hats[:4]
        except Exception:
            continue
        axes_text = ",".join(axes) if axes else "n/a"
        btn_text = ",".join(pressed) if pressed else "none"
        hat_text = ",".join(hats) if hats else "none"
        lines.append(f"Joy {joy_id}: axes[{axes_text}] buttons[{btn_text}] hats[{hat_text}]")
        if len(lines) >= 2:
            break
    if not lines:
        lines = ["Joystick state: unavailable."]
    app.joystick_live_status.set("\n".join(lines))

def refresh_joystick_test_info(app):
    if not hasattr(app, "joystick_test_status"):
        return
    py = app._get_pygame_module()
    if py is None:
        app.joystick_test_status.set("pygame is not installed. Install it to detect USB joysticks.")
        return
    try:
        py.init()
        py.joystick.init()
        count = py.joystick.get_count()
    except Exception as exc:
        app.joystick_test_status.set(f"Joystick init failed: {exc}")
        return
    if count <= 0:
        app._joystick_names = {}
        app._joystick_instances = {}
        app._joystick_device_count = 0
        update_joystick_test_status(app, 0)
        update_joystick_device_status(app, 0, reason="Refresh")
        return
    names = app._discover_joysticks(py, count)
    app._joystick_names = {idx: name for idx, name in enumerate(names)}
    app._joystick_device_count = count
    update_joystick_test_status(app, count, names)
    update_joystick_device_status(app, count, reason="Refresh")

def refresh_joystick_safety_display(app):
    if not hasattr(app, "joystick_safety_status"):
        return
    binding = getattr(app, "_joystick_safety_binding", None)
    label = "None"
    if binding:
        display = app._joystick_binding_display(binding)
        if display:
            label = display
    app.joystick_safety_status.set(f"Safety button: {label}")

def _joystick_safety_ready(app) -> bool:
    return joystick_bindings.joystick_safety_ready(app)

def ensure_joystick_backend(app):
    py = app._get_pygame_module()
    if py is None:
        return False
    if app._joystick_backend_ready:
        return True
    try:
        py.init()
        py.joystick.init()
        count = py.joystick.get_count()
        names = app._discover_joysticks(py, count)
        app._joystick_names = {idx: name for idx, name in enumerate(names)}
        app._joystick_device_count = count
        update_joystick_device_status(app, count, reason="Init")
        app._joystick_backend_ready = True
        return True
    except Exception as exc:
        logger.exception("Joystick backend initialization failed: %s", exc)
        return False

def maybe_refresh_joystick_devices(
    app,
    py,
    *,
    force: bool = False,
    reason: str | None = None,
) -> bool:
    now = time.monotonic()
    last_check = getattr(app, "_joystick_last_discovery", 0.0)
    last_count = getattr(app, "_joystick_device_count", None)
    if last_count is None:
        last_count = len(getattr(app, "_joystick_instances", {}) or {})
    interval_ms = (
        JOYSTICK_DISCOVERY_CONNECTED_INTERVAL_MS
        if last_count > 0
        else JOYSTICK_DISCOVERY_INTERVAL_MS
    )
    if not force and (now - last_check) < (interval_ms / 1000.0):
        return False
    app._joystick_last_discovery = now
    try:
        count = py.joystick.get_count()
    except Exception as exc:
        logger.exception("Joystick discovery failed: %s", exc)
        return False
    if not force:
        if count == last_count:
            return False
    names = app._discover_joysticks(py, count)
    app._joystick_names = {idx: name for idx, name in enumerate(names)}
    app._joystick_device_count = count
    update_joystick_test_status(app, count, names)
    update_joystick_device_status(app, count, reason=reason)
    return True

def start_joystick_polling(app):
    if app._joystick_poll_id is not None:
        return
    app._poll_joystick_events()

def stop_joystick_polling(app):
    if app._joystick_poll_id is not None:
        try:
            app.after_cancel(app._joystick_poll_id)
        except Exception:
            pass
        app._joystick_poll_id = None

def ensure_joystick_polling_running(app):
    if app._joystick_poll_id is None:
        app._start_joystick_polling()

def poll_joystick_events(app):
    return joystick_bindings.poll_joystick_events(
        app,
        maybe_refresh_joystick_devices=maybe_refresh_joystick_devices,
        update_joystick_live_status=update_joystick_live_status,
        joystick_safety_ready=_joystick_safety_ready,
    )

def describe_joystick_event(app, event) -> str | None:
    return joystick_bindings.describe_joystick_event(app, event)

def set_joystick_event_status(app, text: str):
    joystick_bindings.set_joystick_event_status(app, text)

def handle_joystick_event(app, event):
    if bool(getattr(app, "_screen_lock_active", False)):
        return None
    return joystick_bindings.handle_joystick_event(
        app,
        event,
        joystick_safety_ready=_joystick_safety_ready,
        set_joystick_event_status=set_joystick_event_status,
        describe_joystick_event=describe_joystick_event,
    )

def is_virtual_hold_button(app, btn) -> bool:
    return joystick_bindings.is_virtual_hold_button(app, btn)

def handle_joystick_button_release(app, key: tuple):
    return joystick_bindings.handle_joystick_button_release(app, key)

def start_joystick_hold(app, binding_id: str):
    joystick_bindings.start_joystick_hold(app, binding_id)

def send_hold_jog(app):
    joystick_bindings.send_hold_jog(app)

def stop_joystick_hold(app, binding_id: str | None = None):
    joystick_bindings.stop_joystick_hold(app, binding_id)

def clear_duplicate_joystick_binding(app, key: tuple, keep_binding_id: str):
    joystick_bindings.clear_duplicate_joystick_binding(app, key, keep_binding_id)

def poll_joystick_states_from_hardware(app, py) -> bool:
    return joystick_bindings.poll_joystick_states_from_hardware(app, py)

def reset_joystick_axis_state(app, joy_id, axis):
    joystick_bindings.reset_joystick_axis_state(app, joy_id, axis)

def reset_joystick_hat_state(app, joy_id, hat_index):
    joystick_bindings.reset_joystick_hat_state(app, joy_id, hat_index)

def on_key_modifier_release(app, event):
    app._update_modifier_state(event, pressed=False)
    update_keyboard_live_status(app)

def on_key_sequence(app, event):
    if not app._keyboard_binding_allowed():
        return
    label = app._event_to_binding_label(event)
    if not label:
        return
    update_keyboard_live_status(app, label)
    now = time.time()
    if now - app._key_sequence_last_time > app._key_sequence_timeout:
        app._key_sequence_buffer = []
    app._key_sequence_last_time = now
    app._key_sequence_buffer.append(label)
    if len(app._key_sequence_buffer) > 3:
        app._key_sequence_buffer = app._key_sequence_buffer[-3:]
    if app._key_sequence_after_id is not None:
        app.after_cancel(app._key_sequence_after_id)
        app._key_sequence_after_id = None
    seq = tuple(app._key_sequence_buffer)
    btn = app._key_sequence_map.get(seq)
    if btn is not None:
        app._key_sequence_buffer = []
        app._on_key_binding(btn)
        return
    app._key_sequence_after_id = app.after(
        int(app._key_sequence_timeout * 1000),
        app._clear_key_sequence_buffer,
    )

def clear_key_sequence_buffer(app):
    app._key_sequence_buffer = []
    if app._key_sequence_after_id is not None:
        try:
            app.after_cancel(app._key_sequence_after_id)
        except Exception:
            pass
    app._key_sequence_after_id = None

def keyboard_binding_allowed(app) -> bool:
    if bool(getattr(app, "_screen_lock_active", False)):
        return False
    if not bool(app.keyboard_bindings_enabled.get()):
        return False
    try:
        current_grab = app.grab_current()
    except Exception:
        current_grab = None
    if current_grab is not None:
        return False
    try:
        widget = app.focus_get()
    except Exception:
        return False
    if widget is None:
        return True
    try:
        if widget.winfo_toplevel() is not app:
            return False
    except Exception:
        return False
    cls = widget.winfo_class()
    if cls in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox"):
        return False
    return True

def on_key_jog_stop(app, _event=None):
    if not app._keyboard_binding_allowed():
        return
    try:
        if app.btn_jog_cancel.cget("state") == "disabled":
            return
    except Exception:
        return
    try:
        app._stop_joystick_hold()
    except Exception:
        pass
    app.grbl.jog_cancel()

def on_key_all_stop(app, _event=None):
    if not app._keyboard_binding_allowed():
        return
    try:
        if app.btn_all_stop.cget("state") == "disabled":
            return
    except Exception:
        return
    app._all_stop_action()

def on_key_binding(app, btn):
    if not app._keyboard_binding_allowed():
        return
    try:
        if btn.cget("state") == "disabled":
            return
    except Exception:
        return
    app._log_button_action(btn)
    app._invoke_button(btn)

def invoke_button(app, btn):
    if hasattr(btn, "invoke"):
        try:
            btn.invoke()
            return
        except Exception:
            pass
    try:
        cmd = btn.cget("command")
    except Exception:
        cmd = None
    if callable(cmd):
        cmd()

def log_button_action(app, btn):
    if not bool(app.gui_logging_enabled.get()):
        return
    label = app._button_label(btn)
    tip = getattr(btn, "_tooltip_text", "")
    gcode = ""
    try:
        getter = getattr(btn, "_log_gcode_get", None)
        if callable(getter):
            gcode = getter()
        elif isinstance(getter, str):
            gcode = getter
    except Exception:
        gcode = ""
    ts = time.strftime("%H:%M:%S")
    if tip and gcode:
        app.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
    elif tip:
        app.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip}")
    elif gcode:
        app.streaming_controller.log(f"[{ts}] Button: {label} | GCode: {gcode}")
    else:
        app.streaming_controller.log(f"[{ts}] Button: {label}")
