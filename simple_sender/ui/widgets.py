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

"""Compatibility shim for legacy widget imports.

Deprecated as of 2026-02-23.
Planned removal target: v1.8.0 (no earlier than 2026-06-01).
Import from widgets_buttons/widgets_tooltips/widgets_keypad/widgets_common directly.
"""

from simple_sender.ui.widgets_buttons import StopSignButton, VirtualHoldButton
from simple_sender.ui.widgets_common import _resolve_widget_bg, attach_log_gcode, set_kb_id
from simple_sender.ui.widgets_keypad import (
    _center_modal,
    _open_numeric_keypad,
    _open_numeric_keypad_from_focus,
    _show_numeric_keypad,
    attach_numeric_keypad,
)
from simple_sender.ui.widgets_tooltips import (
    ToolTip,
    _clamp_tooltip_position,
    apply_tooltip,
    ensure_tooltips,
    set_tab_tooltip,
)

__all__ = [
    "StopSignButton",
    "VirtualHoldButton",
    "ToolTip",
    "_clamp_tooltip_position",
    "apply_tooltip",
    "set_tab_tooltip",
    "ensure_tooltips",
    "attach_numeric_keypad",
    "_open_numeric_keypad",
    "_open_numeric_keypad_from_focus",
    "_center_modal",
    "_show_numeric_keypad",
    "attach_log_gcode",
    "set_kb_id",
    "_resolve_widget_bg",
]

