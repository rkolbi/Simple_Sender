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
import queue
import sys

# GUI imports
import tkinter as tk


def _app_module(instance):
    return sys.modules[instance.__class__.__module__]


class UiEventsMixin:
    def _on_app_focus_out(self, event=None):
        if not bool(self.stop_hold_on_focus_loss.get()):
            return
        try:
            self.after_idle(self._maybe_stop_joystick_hold_on_focus_loss)
        except Exception:
            self._maybe_stop_joystick_hold_on_focus_loss()

    def _maybe_stop_joystick_hold_on_focus_loss(self):
        if not bool(self.stop_hold_on_focus_loss.get()):
            return
        try:
            focus_widget = self.focus_get()
        except (tk.TclError, KeyError):
            return
        if focus_widget is None and getattr(self, "_active_joystick_hold_binding", None):
            self._stop_joystick_hold()

    def _show_macro_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        cancel_label: str,
        result_q: queue.Queue,
    ) -> None:
        _app_module(self).show_macro_prompt(self, title, message, choices, cancel_label, result_q)

    def _update_tab_visibility(self, nb=None):
        _app_module(self).update_tab_visibility(self, nb)

    def _update_app_settings_scrollregion(self):
        _app_module(self).update_app_settings_scrollregion(self)

    def _on_app_settings_mousewheel(self, event):
        _app_module(self).on_app_settings_mousewheel(self, event)

    def _bind_app_settings_mousewheel(self):
        _app_module(self).bind_app_settings_mousewheel(self)

    def _unbind_app_settings_mousewheel(self):
        _app_module(self).unbind_app_settings_mousewheel(self)

    def _on_app_settings_touch_start(self, event):
        _app_module(self).on_app_settings_touch_start(self, event)

    def _on_app_settings_touch_move(self, event):
        _app_module(self).on_app_settings_touch_move(self, event)

    def _on_app_settings_touch_end(self, event=None):
        _app_module(self).on_app_settings_touch_end(self, event)

    def _bind_app_settings_touch_scroll(self):
        _app_module(self).bind_app_settings_touch_scroll(self)

    def _unbind_app_settings_touch_scroll(self):
        _app_module(self).unbind_app_settings_touch_scroll(self)

    def _on_tab_changed(self, event):
        _app_module(self).on_tab_changed(self, event)

    def _on_auto_level_enabled_change(self):
        enabled = bool(self.auto_level_enabled.get())
        try:
            self.settings["auto_level_enabled"] = enabled
        except Exception:
            pass
        frame = getattr(self, "auto_level_frame", None)
        if frame is not None:
            try:
                if enabled:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception:
                pass
        if enabled:
            if getattr(self, "_last_gcode_lines", None) or getattr(self, "_gcode_source", None):
                self._set_job_button_mode("auto_level")
        else:
            self._set_job_button_mode("read_job")

    def _start_macro_status(self, name: str):
        text = (name or "Macro").strip() or "Macro"
        self._macro_status_text = f"Macro: {text}"
        self._macro_status_scroll_index = 0
        self._macro_status_active = True
        self._macro_status_width = getattr(self, "_machine_state_max_chars", 0) or (len("DISCONNECTED") + 2)
        self._cancel_state_flash()
        self._apply_state_fg("#00c853")
        self._update_macro_status_display()

    def _update_macro_status_display(self):
        if not self._macro_status_active:
            return
        width = int(getattr(self, "_macro_status_width", 0) or 0)
        text = self._macro_status_text
        if width <= 0:
            width = len(text)
        if len(text) <= width:
            display = text.ljust(width)
        else:
            gap = "   "
            scroll = text + gap
            start = self._macro_status_scroll_index % len(scroll)
            double = scroll + scroll
            display = double[start : start + width]
            self._macro_status_scroll_index += 1
        try:
            self.machine_state.set(display)
        except Exception:
            pass
        self._macro_status_after_id = self.after(200, self._update_macro_status_display)

    def _stop_macro_status(self):
        if not getattr(self, "_macro_status_active", False):
            return
        self._macro_status_active = False
        after_id = getattr(self, "_macro_status_after_id", None)
        if after_id:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._macro_status_after_id = None
        self.machine_state.set(self._machine_state_text)
        self._update_state_highlight(self._machine_state_text)

    def _on_resume_button_visibility_change(self):
        _app_module(self).on_resume_button_visibility_change(self)

    def _on_recover_button_visibility_change(self):
        _app_module(self).on_recover_button_visibility_change(self)

    def _update_resume_button_visibility(self):
        _app_module(self).update_resume_button_visibility(self)

    def _update_recover_button_visibility(self):
        _app_module(self).update_recover_button_visibility(self)

    def _set_job_button_mode(self, mode: str):
        _app_module(self).update_job_button_mode(self, mode)
