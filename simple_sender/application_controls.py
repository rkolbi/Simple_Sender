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
import sys

# GUI imports
import tkinter as tk


def _app_module(instance):
    return sys.modules[instance.__class__.__module__]


class ControlsMixin:
    def _unit_toggle_label(self, mode: str | None = None) -> str:
        return _app_module(self).unit_toggle_label(self, mode)

    def _normalize_override_slider_value(self, raw_value, minimum=50, maximum=150):
        return _app_module(self).normalize_override_slider_value(raw_value, minimum=minimum, maximum=maximum)

    def _set_override_scale(self, scale_attr, value, lock_attr):
        _app_module(self).set_override_scale(self, scale_attr, value, lock_attr)

    def _handle_override_slider_change(
        self,
        raw_value,
        last_attr,
        scale_attr,
        lock_attr,
        display_var,
        plus_cmd,
        minus_cmd,
    ):
        _app_module(self).handle_override_slider_change(
            self,
            raw_value,
            last_attr,
            scale_attr,
            lock_attr,
            display_var,
            plus_cmd,
            minus_cmd,
        )

    def _on_feed_override_slider(self, raw_value):
        _app_module(self).on_feed_override_slider(self, raw_value)

    def _on_spindle_override_slider(self, raw_value):
        _app_module(self).on_spindle_override_slider(self, raw_value)

    def _send_override_delta(self, delta, plus_cmd, minus_cmd):
        _app_module(self).send_override_delta(self, delta, plus_cmd, minus_cmd)

    def _set_feed_override_slider_value(self, value):
        _app_module(self).set_feed_override_slider_value(self, value)

    def _set_spindle_override_slider_value(self, value):
        _app_module(self).set_spindle_override_slider_value(self, value)

    def _refresh_override_info(self):
        _app_module(self).refresh_override_info(self)

    def _dro_value_row(self, parent, axis, var):
        _app_module(self).dro_value_row(self, parent, axis, var)

    def _dro_row(self, parent, axis, var, zero_cmd):
        return _app_module(self).dro_row(self, parent, axis, var, zero_cmd)

    def _set_unit_mode(self, mode: str):
        _app_module(self).set_unit_mode(self, mode)

    def _update_unit_toggle_display(self):
        _app_module(self).update_unit_toggle_display(self)

    def _start_homing(self):
        _app_module(self).start_homing(self)

    def _set_step_xy(self, value: float):
        _app_module(self).set_step_xy(self, value)

    def _set_step_z(self, value: float):
        _app_module(self).set_step_z(self, value)

    def _apply_safe_mode_profile(self):
        _app_module(self).apply_safe_mode_profile(self)

    def _set_manual_controls_enabled(self, enabled: bool):
        _app_module(self).set_manual_controls_enabled(self, enabled)

    def _set_streaming_lock(self, locked: bool):
        _app_module(self).set_streaming_lock(self, locked)

    def _format_alarm_message(self, message: str | None) -> str:
        return _app_module(self).format_alarm_message(message)

    def _set_alarm_lock(self, locked: bool, message: str | None = None):
        if locked:
            self._stop_macro_status()
        _app_module(self).set_alarm_lock(self, locked, message)
        self.machine_state.set(self._machine_state_text)
        self._update_state_highlight(self._machine_state_text)
        self._apply_status_poll_profile()

    def _show_alarm_recovery(self):
        _app_module(self).show_alarm_recovery(self)

    def _sync_all_stop_mode_combo(self):
        _app_module(self).sync_all_stop_mode_combo(self)

    def _on_all_stop_mode_change(self, _event=None):
        _app_module(self).on_all_stop_mode_change(self, _event)

    def _sync_current_line_mode_combo(self):
        _app_module(self).sync_current_line_mode_combo(self)

    def _on_current_line_mode_change(self, _event=None):
        _app_module(self).on_current_line_mode_change(self, _event)

    def _update_current_highlight(self):
        _app_module(self).update_current_highlight(self)

    def _all_stop_action(self):
        _app_module(self).all_stop_action(self)

    def _all_stop_gcode_label(self):
        return _app_module(self).all_stop_gcode_label(self)

    def _validate_jog_feed_var(self, var: tk.DoubleVar, fallback_default: float):
        _app_module(self).validate_jog_feed_var(self, var, fallback_default)

    def _on_jog_feed_change_xy(self, _event=None):
        _app_module(self).on_jog_feed_change_xy(self, _event)

    def _on_jog_feed_change_z(self, _event=None):
        _app_module(self).on_jog_feed_change_z(self, _event)

    def _refresh_zeroing_ui(self):
        _app_module(self).refresh_zeroing_ui(self)

    def _on_zeroing_mode_change(self):
        _app_module(self).on_zeroing_mode_change(self)

    def zero_x(self):
        _app_module(self).zero_x(self)

    def zero_y(self):
        _app_module(self).zero_y(self)

    def zero_z(self):
        _app_module(self).zero_z(self)

    def zero_all(self):
        _app_module(self).zero_all(self)

    def goto_zero(self):
        _app_module(self).goto_zero(self)

    def _toggle_unit_mode(self):
        _app_module(self).toggle_unit_mode(self)
