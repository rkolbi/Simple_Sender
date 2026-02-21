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
from typing import Any, Callable, cast

# GUI imports
import tkinter as tk
from simple_sender.ui.alarm_state import format_alarm_message, set_alarm_lock
from simple_sender.ui.all_stop import all_stop_action, all_stop_gcode_label
from simple_sender.ui.controls.jog_controls import (
    apply_safe_mode_profile,
    on_jog_feed_change_xy,
    on_jog_feed_change_z,
    set_step_xy,
    set_step_z,
    set_unit_mode,
    unit_toggle_label,
    update_unit_toggle_display,
    validate_jog_feed_var,
)
from simple_sender.ui.dialogs import show_alarm_recovery
from simple_sender.ui.dro import dro_row, dro_value_row
from simple_sender.ui.events import set_streaming_lock
from simple_sender.ui.manual_controls import set_manual_controls_enabled
from simple_sender.ui.override_controls import (
    handle_override_slider_change,
    normalize_override_slider_value,
    on_feed_override_slider,
    on_spindle_override_slider,
    refresh_override_info,
    send_override_delta,
    set_feed_override_slider_value,
    set_override_scale,
    set_spindle_override_slider_value,
)
from simple_sender.ui.status.state_display import (
    on_all_stop_mode_change,
    on_current_line_mode_change,
    sync_all_stop_mode_combo,
    sync_current_line_mode_combo,
    update_current_highlight,
)
from simple_sender.ui.ui_actions import start_homing
from simple_sender.ui.ui_actions import toggle_unit_mode
from simple_sender.ui.zeroing_actions import (
    goto_zero,
    on_zeroing_mode_change,
    refresh_zeroing_ui,
    zero_all,
    zero_x,
    zero_y,
    zero_z,
)

class ControlsMixin:
    def _unit_toggle_label(self, mode: str | None = None) -> str:
        return str(unit_toggle_label(self, mode))

    def _normalize_override_slider_value(
        self, raw_value: Any, minimum: int = 50, maximum: int = 150
    ) -> Any:
        return normalize_override_slider_value(
            raw_value, minimum=minimum, maximum=maximum
        )

    def _set_override_scale(self, scale_attr: str, value: float, lock_attr: str) -> None:
        set_override_scale(self, scale_attr, value, lock_attr)

    def _handle_override_slider_change(
        self,
        raw_value: Any,
        last_attr: str,
        scale_attr: str,
        lock_attr: str,
        display_var: tk.StringVar,
        plus_cmd: Callable[[], Any],
        minus_cmd: Callable[[], Any],
    ) -> None:
        handle_override_slider_change(
            self,
            raw_value,
            last_attr,
            scale_attr,
            lock_attr,
            display_var,
            plus_cmd,
            minus_cmd,
        )

    def _on_feed_override_slider(self, raw_value: Any) -> None:
        on_feed_override_slider(self, raw_value)

    def _on_spindle_override_slider(self, raw_value: Any) -> None:
        on_spindle_override_slider(self, raw_value)

    def _send_override_delta(
        self, delta: float, plus_cmd: Callable[[], Any], minus_cmd: Callable[[], Any]
    ) -> None:
        send_override_delta(self, delta, plus_cmd, minus_cmd)

    def _set_feed_override_slider_value(self, value: float) -> None:
        set_feed_override_slider_value(self, value)

    def _set_spindle_override_slider_value(self, value: float) -> None:
        set_spindle_override_slider_value(self, value)

    def _refresh_override_info(self) -> None:
        refresh_override_info(self)

    def _dro_value_row(self, parent: tk.Widget, axis: str, var: tk.StringVar, **kwargs: Any) -> None:
        dro_value_row(self, parent, axis, var, **kwargs)

    def _dro_row(
        self,
        parent: tk.Widget,
        axis: str,
        var: tk.StringVar,
        zero_cmd: Callable[[], Any],
        **kwargs: Any,
    ) -> Any:
        return dro_row(self, parent, axis, var, zero_cmd, **kwargs)

    def _set_unit_mode(self, mode: str) -> None:
        set_unit_mode(self, mode)

    def _update_unit_toggle_display(self) -> None:
        update_unit_toggle_display(self)

    def _start_homing(self) -> None:
        start_homing(self)

    def _set_step_xy(self, value: float) -> None:
        set_step_xy(self, value)

    def _set_step_z(self, value: float) -> None:
        set_step_z(self, value)

    def _apply_safe_mode_profile(self) -> None:
        apply_safe_mode_profile(self)

    def _set_manual_controls_enabled(self, enabled: bool) -> None:
        set_manual_controls_enabled(self, enabled)

    def _set_streaming_lock(self, locked: bool) -> None:
        set_streaming_lock(self, locked)

    def _format_alarm_message(self, message: str | None) -> str:
        return str(format_alarm_message(message))

    def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
        app = cast(Any, self)
        if locked:
            app._stop_macro_status()
        set_alarm_lock(self, locked, message)
        app.machine_state.set(app._machine_state_text)
        app._update_state_highlight(app._machine_state_text)
        app._apply_status_poll_profile()

    def _show_alarm_recovery(self) -> None:
        show_alarm_recovery(self)

    def _sync_all_stop_mode_combo(self) -> None:
        sync_all_stop_mode_combo(self)

    def _on_all_stop_mode_change(self, _event: Any | None = None) -> None:
        on_all_stop_mode_change(self, _event)

    def _sync_current_line_mode_combo(self) -> None:
        sync_current_line_mode_combo(self)

    def _on_current_line_mode_change(self, _event: Any | None = None) -> None:
        on_current_line_mode_change(self, _event)

    def _update_current_highlight(self) -> None:
        update_current_highlight(self)

    def _all_stop_action(self) -> None:
        all_stop_action(self)

    def _all_stop_gcode_label(self) -> str:
        return str(all_stop_gcode_label(self))

    def _validate_jog_feed_var(self, var: tk.DoubleVar, fallback_default: float) -> None:
        validate_jog_feed_var(self, var, fallback_default)

    def _on_jog_feed_change_xy(self, _event: Any | None = None) -> None:
        on_jog_feed_change_xy(self, _event)

    def _on_jog_feed_change_z(self, _event: Any | None = None) -> None:
        on_jog_feed_change_z(self, _event)

    def _refresh_zeroing_ui(self) -> None:
        refresh_zeroing_ui(self)

    def _on_zeroing_mode_change(self) -> None:
        on_zeroing_mode_change(self)

    def zero_x(self) -> None:
        zero_x(self)

    def zero_y(self) -> None:
        zero_y(self)

    def zero_z(self) -> None:
        zero_z(self)

    def zero_all(self) -> None:
        zero_all(self)

    def goto_zero(self) -> None:
        goto_zero(self)

    def _toggle_unit_mode(self) -> None:
        toggle_unit_mode(self)
