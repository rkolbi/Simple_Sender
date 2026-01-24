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
from typing import Any, Callable


def _app_module(instance):
    return sys.modules[instance.__class__.__module__]


class ActionsMixin:
    def refresh_ports(self, auto_connect: bool = False):
        _app_module(self).refresh_ports(self, auto_connect)

    def toggle_connect(self):
        _app_module(self).toggle_connect(self)

    def _start_connect_worker(
        self,
        port: str,
        *,
        show_error: bool = True,
        on_failure: Callable[[Exception], Any] | None = None,
    ):
        _app_module(self).start_connect_worker(self, port, show_error=show_error, on_failure=on_failure)

    def _start_disconnect_worker(self):
        _app_module(self).start_disconnect_worker(self)

    def _ensure_serial_available(self) -> bool:
        module = _app_module(self)
        return module.ensure_serial_available(self, module.serial is not None, module.SERIAL_IMPORT_ERROR)

    def open_gcode(self):
        _app_module(self).open_gcode(self)

    def run_job(self):
        _app_module(self).run_job(self)

    def pause_job(self):
        _app_module(self).pause_job(self)

    def resume_job(self):
        _app_module(self).resume_job(self)

    def stop_job(self):
        _app_module(self).stop_job(self)

    def _load_grbl_setting_info(self):
        module = _app_module(self)
        module.load_grbl_setting_info(self, module._SCRIPT_DIR)

    def _setup_console_tags(self):
        _app_module(self).setup_console_tags(self)

    def _send_console(self):
        _app_module(self).send_console(self)

    def _clear_console_log(self):
        _app_module(self).clear_console_log(self)

    def _save_console_log(self):
        _app_module(self).save_console_log(self)

    def _request_settings_dump(self):
        _app_module(self).request_settings_dump(self)

    def _maybe_auto_reconnect(self):
        _app_module(self).maybe_auto_reconnect(self)

    def _handle_auto_reconnect_failure(self, exc: Exception):
        _app_module(self).handle_auto_reconnect_failure(self, exc)

    def _confirm_and_run(self, label: str, func):
        _app_module(self).confirm_and_run(self, label, func)

    def _require_grbl_connection(self) -> bool:
        return _app_module(self).require_grbl_connection(self)

    def _run_if_connected(self, func):
        _app_module(self).run_if_connected(self, func)

    def _send_manual(self, command: str, source: str):
        _app_module(self).send_manual(self, command, source)
