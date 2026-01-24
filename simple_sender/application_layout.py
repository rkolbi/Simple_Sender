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


def _app_module(instance):
    return sys.modules[instance.__class__.__module__]


class LayoutMixin:
    def _build_toolbar(self):
        _app_module(self).build_toolbar(self)

    def _build_main(self):
        _app_module(self).build_main_layout(self)
        self._ensure_tooltips()

    def _show_release_checklist(self):
        _app_module(self).open_release_checklist(self)

    def _show_run_checklist(self):
        _app_module(self).open_run_checklist(self)

    def _run_preflight_check(self):
        _app_module(self).run_preflight_check(self)

    def _export_session_diagnostics(self):
        _app_module(self).export_session_diagnostics(self)

    def _position_all_stop_offset(self, event=None):
        _app_module(self).position_all_stop_offset(self, event)

    def _ensure_tooltips(self):
        _app_module(self).ensure_tooltips(self)
