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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
from types import ModuleType
from typing import Any, cast

from simple_sender.ui import bindings as input_bindings
from simple_sender.ui.widgets import VirtualHoldButton


class InputBindingsMixin:
    def _toggle_keyboard_bindings(self):
        input_bindings.toggle_keyboard_bindings(self)

    def _toggle_joystick_bindings(self):
        input_bindings.toggle_joystick_bindings(self)

    def _on_keyboard_bindings_check(self):
        input_bindings.on_keyboard_bindings_check(self)

    def _refresh_joystick_toggle_text(self):
        input_bindings.refresh_joystick_toggle_text(self)

    def _refresh_joystick_safety_display(self):
        input_bindings.refresh_joystick_safety_display(self)

    def _update_joystick_polling_state(self):
        input_bindings.update_joystick_polling_state(self)

    def _restore_joystick_bindings_on_start(self):
        input_bindings.restore_joystick_bindings_on_start(self)

    def _get_pygame_module(self) -> ModuleType | None:
        return cast(ModuleType | None, input_bindings.get_pygame_module(self))

    def _discover_joysticks(self, py, count: int) -> list[str]:
        return cast(list[str], input_bindings.discover_joysticks(self, py, count))

    def _refresh_joystick_test_info(self):
        input_bindings.refresh_joystick_test_info(self)

    def _ensure_joystick_backend(self):
        return input_bindings.ensure_joystick_backend(self)

    def _start_joystick_polling(self):
        input_bindings.start_joystick_polling(self)

    def _stop_joystick_polling(self):
        input_bindings.stop_joystick_polling(self)

    def _ensure_joystick_polling_running(self):
        input_bindings.ensure_joystick_polling_running(self)

    def _poll_joystick_events(self):
        input_bindings.poll_joystick_events(self)

    def _describe_joystick_event(self, event) -> str | None:
        return cast(str | None, input_bindings.describe_joystick_event(self, event))

    def _set_joystick_event_status(self, text: str):
        input_bindings.set_joystick_event_status(self, text)

    def _handle_joystick_event(self, event):
        input_bindings.handle_joystick_event(self, event)

    def _is_virtual_hold_button(self, btn) -> bool:
        return bool(input_bindings.is_virtual_hold_button(self, btn))

    def _handle_joystick_button_release(self, key: tuple):
        input_bindings.handle_joystick_button_release(self, key)

    def _start_joystick_hold(self, binding_id: str):
        input_bindings.start_joystick_hold(self, binding_id)

    def _send_hold_jog(self):
        input_bindings.send_hold_jog(self)

    def _stop_joystick_hold(self, binding_id: str | None = None):
        input_bindings.stop_joystick_hold(self, binding_id)

    def _start_joystick_safety_capture(self):
        input_bindings.start_joystick_safety_capture(self)

    def _cancel_joystick_safety_capture(self):
        input_bindings.cancel_joystick_safety_capture(self)

    def _clear_joystick_safety_binding(self):
        input_bindings.clear_joystick_safety_binding(self)

    def _on_joystick_safety_toggle(self):
        input_bindings.on_joystick_safety_toggle(self)

    def _clear_duplicate_joystick_binding(self, key: tuple, keep_binding_id: str):
        input_bindings.clear_duplicate_joystick_binding(self, key, keep_binding_id)

    def _poll_joystick_states_from_hardware(self, py) -> bool:
        return bool(input_bindings.poll_joystick_states_from_hardware(self, py))

    def _reset_joystick_axis_state(self, joy_id, axis):
        input_bindings.reset_joystick_axis_state(self, joy_id, axis)

    def _reset_joystick_hat_state(self, joy_id, hat_index):
        input_bindings.reset_joystick_hat_state(self, joy_id, hat_index)

    def _apply_keyboard_bindings(self):
        input_bindings.apply_keyboard_bindings(self)

    def _refresh_keyboard_table(self):
        input_bindings.refresh_keyboard_table(self)

    def _create_virtual_hold_buttons(self) -> list[VirtualHoldButton]:
        return cast(list[VirtualHoldButton], input_bindings.create_virtual_hold_buttons(self))

    def _collect_buttons(self) -> list[Any]:
        return cast(list[Any], input_bindings.collect_buttons(self))

    def _button_label(self, btn) -> str:
        return str(input_bindings.button_label(self, btn))

    def _keyboard_key_for_button(self, btn) -> str:
        return str(input_bindings.keyboard_key_for_button(self, btn))

    def _joystick_binding_display(self, binding: dict[str, Any]) -> str:
        return str(input_bindings.joystick_binding_display(self, binding))

    def _joystick_binding_key(self, binding: dict[str, Any]):
        return input_bindings.joystick_binding_key(self, binding)

    def _button_axis_name(self, btn) -> str:
        return str(input_bindings.button_axis_name(self, btn))

    def _button_binding_id(self, btn) -> str:
        return str(input_bindings.button_binding_id(self, btn))

    def _find_binding_conflict(self, target_btn, label: str):
        return input_bindings.find_binding_conflict(self, target_btn, label)

    def _default_key_for_button(self, btn) -> str:
        return str(input_bindings.default_key_for_button(self, btn))

    def _on_kb_table_double_click(self, event):
        input_bindings.on_kb_table_double_click(self, event)

    def _on_kb_table_click(self, event):
        input_bindings.on_kb_table_click(self, event)

    def _start_kb_edit(self, row, col):
        input_bindings.start_kb_edit(self, row, col)

    def _start_joystick_capture(self, row):
        input_bindings.start_joystick_capture(self, row)

    def _cancel_joystick_capture(self):
        input_bindings.cancel_joystick_capture(self)

    def _joystick_binding_from_event(self, key):
        return input_bindings.joystick_binding_from_event(self, key)

    def _kb_capture_key(self, event, row, entry):
        input_bindings.kb_capture_key(self, event, row, entry)

    def _commit_kb_edit(self, row, entry, label_override: str | None = None):
        input_bindings.commit_kb_edit(self, row, entry, label_override)

    def _normalize_key_label(self, text: str) -> str:
        return str(input_bindings.normalize_key_label(self, text))

    def _normalize_key_chord(self, text: str) -> str:
        return str(input_bindings.normalize_key_chord(self, text))

    def _key_sequence_tuple(self, label: str) -> tuple[str, ...] | None:
        return cast(tuple[str, ...] | None, input_bindings.key_sequence_tuple(self, label))

    def _update_modifier_state(self, event, pressed: bool) -> bool:
        return bool(input_bindings.update_modifier_state(self, event, pressed))

    def _modifier_active(self, name: str, event_state: int | None = None) -> bool:
        return bool(input_bindings.modifier_active(self, name, event_state))

    def _event_to_binding_label(self, event) -> str:
        return str(input_bindings.event_to_binding_label(self, event))

    def _on_key_modifier_release(self, event):
        input_bindings.on_key_modifier_release(self, event)

    def _sequence_conflict_pair(self, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
        return bool(input_bindings.sequence_conflict_pair(self, seq_a, seq_b))

    def _sequence_conflict(self, seq: tuple[str, ...], existing: dict) -> Any:
        return input_bindings.sequence_conflict(self, seq, existing)

    def _on_key_sequence(self, event):
        input_bindings.on_key_sequence(self, event)

    def _clear_key_sequence_buffer(self):
        input_bindings.clear_key_sequence_buffer(self)

    def _keyboard_binding_allowed(self) -> bool:
        return bool(input_bindings.keyboard_binding_allowed(self))

    def _on_key_jog_stop(self, _event=None):
        input_bindings.on_key_jog_stop(self, _event)

    def _on_key_all_stop(self, _event=None):
        input_bindings.on_key_all_stop(self, _event)

    def _on_key_binding(self, btn):
        input_bindings.on_key_binding(self, btn)

    def _invoke_button(self, btn):
        input_bindings.invoke_button(self, btn)

    def _log_button_action(self, btn):
        input_bindings.log_button_action(self, btn)

