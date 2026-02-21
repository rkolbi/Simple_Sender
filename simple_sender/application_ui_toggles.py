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
from simple_sender.ui.dialogs.error_dialogs_ui import (
    install_dialog_loggers,
    on_error_dialogs_enabled_change,
    toggle_error_dialogs,
)
from simple_sender.ui.screen_lock import (
    init_screen_lock_guard,
    on_screen_lock_event,
    on_screen_lock_widget_mapped,
    refresh_screen_lock_toggle_text,
    toggle_screen_lock,
)
from simple_sender.ui.status.bar import (
    on_quick_button_visibility_change,
    update_quick_button_visibility,
)
from simple_sender.ui.theme_helpers import (
    apply_theme,
    refresh_stop_button_backgrounds,
)
from simple_sender.ui.toggle_text import (
    refresh_autolevel_overlay_toggle_text,
    refresh_keybindings_toggle_text,
    refresh_tooltips_toggle_text,
)
from simple_sender.ui.ui_actions import (
    apply_scrollbar_width,
    apply_ui_scale,
    on_gui_logging_change,
    on_performance_mode_change,
    on_scrollbar_width_change,
    on_theme_change,
    on_ui_scale_change,
    toggle_console_pos_status,
    toggle_performance,
    toggle_tooltips,
)
from simple_sender.ui.led_panel import refresh_led_backgrounds


class UiTogglesMixin:
    def _refresh_tooltips_toggle_text(self):
        refresh_tooltips_toggle_text(self)

    def _refresh_keybindings_toggle_text(self):
        refresh_keybindings_toggle_text(self)

    def _refresh_autolevel_overlay_button(self):
        refresh_autolevel_overlay_toggle_text(self)

    def _refresh_screen_lock_toggle_text(self):
        refresh_screen_lock_toggle_text(self)

    def _update_quick_button_visibility(self):
        update_quick_button_visibility(self)

    def _on_quick_button_visibility_change(self):
        on_quick_button_visibility_change(self)

    def _toggle_tooltips(self):
        toggle_tooltips(self)

    def _toggle_screen_lock(self):
        toggle_screen_lock(self)

    def _init_screen_lock_guard(self):
        init_screen_lock_guard(self)

    def _on_screen_lock_event(self, event):
        return on_screen_lock_event(self, event)

    def _on_screen_lock_widget_mapped(self, event):
        return on_screen_lock_widget_mapped(self, event)

    def _apply_theme(self, theme: str):
        apply_theme(self, theme)

    def _on_gui_logging_change(self):
        on_gui_logging_change(self)

    def _on_performance_mode_change(self):
        on_performance_mode_change(self)

    def _on_theme_change(self, *_):
        on_theme_change(self)

    def _apply_ui_scale(self, value: float | None = None):
        return apply_ui_scale(self, value)

    def _on_ui_scale_change(self, _event=None):
        on_ui_scale_change(self, _event)

    def _apply_scrollbar_width(self, value: str | None = None):
        return apply_scrollbar_width(self, value)

    def _on_scrollbar_width_change(self, _event=None):
        on_scrollbar_width_change(self, _event)

    def _refresh_stop_button_backgrounds(self):
        refresh_stop_button_backgrounds(self)

    def _refresh_led_backgrounds(self):
        refresh_led_backgrounds(self)

    def _install_dialog_loggers(self):
        install_dialog_loggers(self)

    def _toggle_error_dialogs(self):
        toggle_error_dialogs(self)

    def _on_error_dialogs_enabled_change(self):
        on_error_dialogs_enabled_change(self)

    def _toggle_performance(self):
        toggle_performance(self)

    def _toggle_console_pos_status(self):
        toggle_console_pos_status(self)

