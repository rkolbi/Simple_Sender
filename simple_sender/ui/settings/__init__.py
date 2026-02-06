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

"""Settings UI package exports."""

from .dialog import build_app_settings_tab
from .scroll import (
    bind_app_settings_mousewheel,
    bind_app_settings_touch_scroll,
    on_app_settings_mousewheel,
    on_app_settings_touch_end,
    on_app_settings_touch_move,
    on_app_settings_touch_start,
    unbind_app_settings_mousewheel,
    unbind_app_settings_touch_scroll,
    update_app_settings_scrollregion,
)
from .sections import (
    build_auto_level_section,
    build_diagnostics_section,
    build_error_dialogs_section,
    build_estimation_section,
    build_viewer_section,
    build_interface_section,
    build_jogging_section,
    build_keyboard_shortcuts_section,
    build_macros_section,
    build_power_section,
    build_safety_aids_section,
    build_safety_section,
    build_status_polling_section,
    build_theme_section,
    build_toolpath_settings_section,
    build_zeroing_section,
)

__all__ = [
    "build_app_settings_tab",
    "bind_app_settings_mousewheel",
    "bind_app_settings_touch_scroll",
    "on_app_settings_mousewheel",
    "on_app_settings_touch_start",
    "on_app_settings_touch_move",
    "on_app_settings_touch_end",
    "unbind_app_settings_mousewheel",
    "unbind_app_settings_touch_scroll",
    "update_app_settings_scrollregion",
    "build_auto_level_section",
    "build_diagnostics_section",
    "build_error_dialogs_section",
    "build_estimation_section",
    "build_viewer_section",
    "build_interface_section",
    "build_jogging_section",
    "build_keyboard_shortcuts_section",
    "build_macros_section",
    "build_power_section",
    "build_safety_aids_section",
    "build_safety_section",
    "build_status_polling_section",
    "build_theme_section",
    "build_toolpath_settings_section",
    "build_zeroing_section",
]
