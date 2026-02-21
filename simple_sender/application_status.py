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
from typing import Any, cast
from simple_sender.ui.dialogs.error_dialogs_ui import apply_error_dialog_settings
from simple_sender.ui.grbl_lifecycle import (
    apply_status_poll_profile,
    effective_status_poll_interval,
)
from simple_sender.ui.profiles.estimate_rates import (
    convert_estimate_rates,
    on_estimate_rates_change,
    update_estimate_rate_units_label,
    validate_estimate_rate_text,
)
from simple_sender.ui.status.polling import (
    on_homing_watchdog_change,
    on_status_failure_limit_change,
    on_status_interval_change,
)

class StatusMixin:
    def _on_fallback_rate_change(self, _event=None):
        app = cast(Any, self)
        if getattr(app, "_last_gcode_lines", None):
            app._update_gcode_stats(app._last_gcode_lines)

    def _on_status_interval_change(self, _event=None):
        on_status_interval_change(self, _event)

    def _on_status_failure_limit_change(self, _event=None):
        on_status_failure_limit_change(self, _event)

    def _on_homing_watchdog_change(self, _event=None):
        on_homing_watchdog_change(self, _event)

    def _apply_error_dialog_settings(self, _event=None):
        apply_error_dialog_settings(self, _event)

    def _effective_status_poll_interval(self) -> float:
        return float(effective_status_poll_interval(self))

    def _apply_status_poll_profile(self):
        apply_status_poll_profile(self)

    def _update_estimate_rate_units_label(self):
        update_estimate_rate_units_label(self)

    def _on_estimate_rates_change(self, _event=None):
        on_estimate_rates_change(self, _event)

    def _validate_estimate_rate_text(self, text: str) -> bool:
        return bool(validate_estimate_rate_text(text))

    def _convert_estimate_rates(self, old_units: str, new_units: str):
        convert_estimate_rates(self, old_units, new_units)
