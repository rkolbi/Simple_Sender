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
from simple_sender.ui.app_exports import (
    apply_state_fg,
    build_led_panel,
    cancel_state_flash,
    ensure_state_label_width,
    on_autolevel_overlay_change,
    on_led_visibility_change,
    set_led_state,
    start_state_flash,
    toggle_autolevel_overlay,
    toggle_state_flash,
    update_led_panel,
    update_led_visibility,
    update_state_highlight,
)


class StateUiMixin:
    def _build_led_panel(self, parent):
        build_led_panel(self, parent)

    def _set_led_state(self, key, on):
        set_led_state(self, key, on)

    def _update_led_panel(self, endstop: bool, probe: bool, hold: bool):
        update_led_panel(self, endstop, probe, hold)

    def _update_led_visibility(self):
        update_led_visibility(self)

    def _on_led_visibility_change(self):
        on_led_visibility_change(self)

    def _on_autolevel_overlay_change(self):
        on_autolevel_overlay_change(self)

    def _toggle_autolevel_overlay(self):
        toggle_autolevel_overlay(self)

    def _apply_state_fg(self, color: str | None, fg: str | None = None):
        apply_state_fg(self, color, fg=fg)

    def _cancel_state_flash(self):
        cancel_state_flash(self)

    def _toggle_state_flash(self):
        toggle_state_flash(self)

    def _start_state_flash(self, color: str):
        start_state_flash(self, color)

    def _ensure_state_label_width(self, text: str | None):
        ensure_state_label_width(self, text)

    def _update_state_highlight(self, state: str | None):
        update_state_highlight(self, state)

