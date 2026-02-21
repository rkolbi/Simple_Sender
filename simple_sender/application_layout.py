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
from simple_sender.ui.controls.toolbar import build_toolbar
from simple_sender.ui.dialogs.backup_bundle import (
    export_backup_bundle,
    import_backup_bundle,
)
from simple_sender.ui.dialogs.diagnostics import (
    export_session_diagnostics,
    open_release_checklist,
    open_run_checklist,
    run_preflight_check,
)
from simple_sender.ui.dialogs.logs import show_logs_dialog
from simple_sender.ui.dialogs.macro_manager import show_macro_manager
from simple_sender.ui.main_layout import build_main_layout
from simple_sender.ui.widgets import ensure_tooltips
from simple_sender.ui.all_stop import position_all_stop_offset

class LayoutMixin:
    def _build_toolbar(self):
        build_toolbar(self)

    def _build_main(self):
        build_main_layout(self)
        self._ensure_tooltips()

    def _show_release_checklist(self):
        open_release_checklist(self)

    def _show_run_checklist(self):
        open_run_checklist(self)

    def _run_preflight_check(self):
        run_preflight_check(self)

    def _export_session_diagnostics(self):
        export_session_diagnostics(self)

    def _export_backup_bundle(self):
        export_backup_bundle(self)

    def _import_backup_bundle(self):
        import_backup_bundle(self)

    def _open_macro_manager(self):
        show_macro_manager(self)

    def _show_logs_dialog(self):
        show_logs_dialog(self)

    def _position_all_stop_offset(self, event=None):
        position_all_stop_offset(self, event)

    def _ensure_tooltips(self):
        ensure_tooltips(self)
