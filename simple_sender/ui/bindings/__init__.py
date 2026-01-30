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

"""Input bindings package exports."""

from . import core as _core

messagebox = _core.messagebox
time = _core.time
PYGAME_AVAILABLE = _core.PYGAME_AVAILABLE
PYGAME_IMPORT_ERROR = _core.PYGAME_IMPORT_ERROR
update_joystick_test_status = _core.update_joystick_test_status
update_joystick_device_status = _core.update_joystick_device_status


def _sync_core() -> None:
    _core.messagebox = messagebox
    _core.time = time
    _core.PYGAME_AVAILABLE = PYGAME_AVAILABLE
    _core.update_joystick_test_status = update_joystick_test_status
    _core.update_joystick_device_status = update_joystick_device_status


def refresh_joystick_toggle_text(app):
    _sync_core()
    return _core.refresh_joystick_toggle_text(app)


def toggle_joystick_bindings(app):
    _sync_core()
    return _core.toggle_joystick_bindings(app)


def maybe_refresh_joystick_devices(app, py, *, force: bool = False, reason: str | None = None) -> bool:
    _sync_core()
    return _core.maybe_refresh_joystick_devices(app, py, force=force, reason=reason)


def on_key_sequence(app, event):
    _sync_core()
    return _core.on_key_sequence(app, event)


def on_keyboard_bindings_check(app):
    return _core.on_keyboard_bindings_check(app)


def toggle_keyboard_bindings(app):
    return _core.toggle_keyboard_bindings(app)


def apply_keyboard_bindings(app):
    return _core.apply_keyboard_bindings(app)


def refresh_keyboard_table(app):
    return _core.refresh_keyboard_table(app)


def update_keyboard_live_status(app, label: str | None = None) -> None:
    return _core.update_keyboard_live_status(app, label=label)


def refresh_joystick_test_info(app):
    return _core.refresh_joystick_test_info(app)


def refresh_joystick_safety_display(app):
    return _core.refresh_joystick_safety_display(app)


def update_joystick_polling_state(app):
    return _core.update_joystick_polling_state(app)


def restore_joystick_bindings_on_start(app):
    return _core.restore_joystick_bindings_on_start(app)


def get_pygame_module(app):
    return _core.get_pygame_module(app)


def discover_joysticks(app, py, count: int):
    return _core.discover_joysticks(app, py, count)


def ensure_joystick_backend(app):
    return _core.ensure_joystick_backend(app)


def start_joystick_polling(app):
    return _core.start_joystick_polling(app)


def stop_joystick_polling(app):
    return _core.stop_joystick_polling(app)


def ensure_joystick_polling_running(app):
    return _core.ensure_joystick_polling_running(app)


def poll_joystick_events(app):
    return _core.poll_joystick_events(app)


def describe_joystick_event(app, event):
    return _core.describe_joystick_event(app, event)


def set_joystick_event_status(app, text: str):
    return _core.set_joystick_event_status(app, text)


def handle_joystick_event(app, event):
    return _core.handle_joystick_event(app, event)


def is_virtual_hold_button(app, btn) -> bool:
    return _core.is_virtual_hold_button(app, btn)


def handle_joystick_button_release(app, key):
    return _core.handle_joystick_button_release(app, key)


def start_joystick_hold(app, binding_id: str):
    return _core.start_joystick_hold(app, binding_id)


def send_hold_jog(app):
    return _core.send_hold_jog(app)


def stop_joystick_hold(app, binding_id: str | None = None):
    return _core.stop_joystick_hold(app, binding_id)


def start_joystick_safety_capture(app):
    return _core.start_joystick_safety_capture(app)


def cancel_joystick_safety_capture(app):
    return _core.cancel_joystick_safety_capture(app)


def clear_joystick_safety_binding(app):
    return _core.clear_joystick_safety_binding(app)


def on_joystick_safety_toggle(app):
    return _core.on_joystick_safety_toggle(app)


def clear_duplicate_joystick_binding(app, key, keep_binding_id):
    return _core.clear_duplicate_joystick_binding(app, key, keep_binding_id)


def poll_joystick_states_from_hardware(app, py):
    return _core.poll_joystick_states_from_hardware(app, py)


def reset_joystick_axis_state(app, joy_id, axis):
    return _core.reset_joystick_axis_state(app, joy_id, axis)


def reset_joystick_hat_state(app, joy_id, hat_index):
    return _core.reset_joystick_hat_state(app, joy_id, hat_index)


def create_virtual_hold_buttons(app):
    return _core.create_virtual_hold_buttons(app)


def collect_buttons(app):
    return _core.collect_buttons(app)


def button_label(app, btn):
    return _core.button_label(app, btn)


def keyboard_key_for_button(app, btn):
    return _core.keyboard_key_for_button(app, btn)


def joystick_binding_display(app, binding):
    return _core.joystick_binding_display(app, binding)


def joystick_binding_key(app, binding):
    return _core.joystick_binding_key(app, binding)


def button_axis_name(app, btn):
    return _core.button_axis_name(app, btn)


def button_binding_id(app, btn):
    return _core.button_binding_id(app, btn)


def find_binding_conflict(app, target_btn, label):
    return _core.find_binding_conflict(app, target_btn, label)


def default_key_for_button(app, btn):
    return _core.default_key_for_button(app, btn)


def on_kb_table_double_click(app, event):
    return _core.on_kb_table_double_click(app, event)


def on_kb_table_click(app, event):
    return _core.on_kb_table_click(app, event)


def start_kb_edit(app, row, col):
    return _core.start_kb_edit(app, row, col)


def start_joystick_capture(app, row):
    return _core.start_joystick_capture(app, row)


def cancel_joystick_capture(app):
    return _core.cancel_joystick_capture(app)


def joystick_binding_from_event(app, key):
    return _core.joystick_binding_from_event(app, key)


def kb_capture_key(app, event, row, entry):
    return _core.kb_capture_key(app, event, row, entry)


def commit_kb_edit(app, row, entry, label_override):
    return _core.commit_kb_edit(app, row, entry, label_override)


def normalize_key_label(app, text: str) -> str:
    return _core.normalize_key_label(app, text)


def normalize_key_chord(app, text: str) -> str:
    return _core.normalize_key_chord(app, text)


def key_sequence_tuple(app, label: str):
    return _core.key_sequence_tuple(app, label)


def update_modifier_state(app, event, pressed: bool):
    return _core.update_modifier_state(app, event, pressed)


def modifier_active(app, name: str, event_state: int | None = None):
    return _core.modifier_active(app, name, event_state=event_state)


def event_to_binding_label(app, event):
    return _core.event_to_binding_label(app, event)


def on_key_modifier_release(app, event):
    return _core.on_key_modifier_release(app, event)


def sequence_conflict_pair(app, seq_a, seq_b):
    return _core.sequence_conflict_pair(app, seq_a, seq_b)


def sequence_conflict(app, seq, existing):
    return _core.sequence_conflict(app, seq, existing)


def clear_key_sequence_buffer(app):
    return _core.clear_key_sequence_buffer(app)


def keyboard_binding_allowed(app):
    return _core.keyboard_binding_allowed(app)


def on_key_jog_stop(app, event):
    return _core.on_key_jog_stop(app, event)


def on_key_all_stop(app, event):
    return _core.on_key_all_stop(app, event)


def on_key_binding(app, btn):
    return _core.on_key_binding(app, btn)


def invoke_button(app, btn):
    return _core.invoke_button(app, btn)


def log_button_action(app, btn):
    return _core.log_button_action(app, btn)

__all__ = [
    "PYGAME_AVAILABLE",
    "PYGAME_IMPORT_ERROR",
    "messagebox",
    "time",
    "apply_keyboard_bindings",
    "button_axis_name",
    "button_binding_id",
    "button_label",
    "cancel_joystick_capture",
    "cancel_joystick_safety_capture",
    "clear_duplicate_joystick_binding",
    "clear_joystick_safety_binding",
    "collect_buttons",
    "commit_kb_edit",
    "create_virtual_hold_buttons",
    "default_key_for_button",
    "describe_joystick_event",
    "discover_joysticks",
    "ensure_joystick_backend",
    "ensure_joystick_polling_running",
    "event_to_binding_label",
    "find_binding_conflict",
    "get_pygame_module",
    "handle_joystick_button_release",
    "handle_joystick_event",
    "invoke_button",
    "is_virtual_hold_button",
    "joystick_binding_display",
    "joystick_binding_from_event",
    "joystick_binding_key",
    "keyboard_binding_allowed",
    "keyboard_key_for_button",
    "key_sequence_tuple",
    "kb_capture_key",
    "log_button_action",
    "modifier_active",
    "normalize_key_chord",
    "normalize_key_label",
    "on_joystick_safety_toggle",
    "on_key_all_stop",
    "on_key_binding",
    "on_key_jog_stop",
    "on_key_modifier_release",
    "on_key_sequence",
    "on_kb_table_click",
    "on_kb_table_double_click",
    "on_keyboard_bindings_check",
    "maybe_refresh_joystick_devices",
    "poll_joystick_events",
    "poll_joystick_states_from_hardware",
    "refresh_joystick_safety_display",
    "refresh_joystick_test_info",
    "refresh_joystick_toggle_text",
    "refresh_keyboard_table",
    "reset_joystick_axis_state",
    "reset_joystick_hat_state",
    "restore_joystick_bindings_on_start",
    "send_hold_jog",
    "sequence_conflict",
    "sequence_conflict_pair",
    "start_joystick_capture",
    "start_joystick_hold",
    "start_joystick_polling",
    "start_joystick_safety_capture",
    "start_kb_edit",
    "stop_joystick_hold",
    "stop_joystick_polling",
    "toggle_joystick_bindings",
    "toggle_keyboard_bindings",
    "update_joystick_device_status",
    "update_keyboard_live_status",
    "update_joystick_polling_state",
    "update_joystick_test_status",
    "update_modifier_state",
    "clear_key_sequence_buffer",
    "set_joystick_event_status",
]
