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
from typing import Any, Callable



from simple_sender.ui.app_exports import (
    clear_console_log,
    confirm_and_run,
    ensure_serial_available,
    handle_auto_reconnect_failure,
    load_grbl_setting_info,
    maybe_auto_reconnect,
    open_gcode,
    pause_job,
    refresh_ports,
    request_settings_dump,
    require_grbl_connection,
    resume_job,
    run_if_connected,
    run_job,
    save_console_log,
    send_console,
    send_manual,
    setup_console_tags,
    start_connect_worker,
    start_disconnect_worker,
    stop_job,
    toggle_connect,
)

class ActionsMixin:
    def refresh_ports(self, auto_connect: bool = False) -> None:
        refresh_ports(self, auto_connect)

    def toggle_connect(self) -> None:
        toggle_connect(self)

    def _start_connect_worker(
        self,
        port: str,
        *,
        show_error: bool = True,
        on_failure: Callable[[Exception], Any] | None = None,
    ) -> None:
        start_connect_worker(self, port, show_error=show_error, on_failure=on_failure)

    def _start_disconnect_worker(self) -> None:
        start_disconnect_worker(self)

    def _ensure_serial_available(self) -> Any:
        return ensure_serial_available(
            self,
            bool(getattr(self, "_serial_available", False)),
            str(getattr(self, "_serial_import_error", "")),
        )

    def open_gcode(self) -> None:
        open_gcode(self)

    def run_job(self) -> None:
        run_job(self)

    def pause_job(self) -> None:
        pause_job(self)

    def resume_job(self) -> None:
        resume_job(self)

    def stop_job(self) -> None:
        stop_job(self)

    def _load_grbl_setting_info(self) -> None:
        load_grbl_setting_info(self, str(getattr(self, "_script_dir", "")))

    def _setup_console_tags(self) -> None:
        setup_console_tags(self)

    def _send_console(self) -> None:
        send_console(self)

    def _clear_console_log(self) -> None:
        clear_console_log(self)

    def _save_console_log(self) -> None:
        save_console_log(self)

    def _request_settings_dump(self) -> None:
        request_settings_dump(self)

    def _maybe_auto_reconnect(self) -> None:
        maybe_auto_reconnect(self)

    def _handle_auto_reconnect_failure(self, exc: Exception) -> None:
        handle_auto_reconnect_failure(self, exc)

    def _confirm_and_run(self, label: str, func: Callable[..., Any]) -> None:
        confirm_and_run(self, label, func)

    def _require_grbl_connection(self) -> bool:
        return bool(require_grbl_connection(self))

    def _run_if_connected(self, func: Callable[..., Any]) -> None:
        run_if_connected(self, func)

    def _send_manual(self, command: str, source: str) -> None:
        send_manual(self, command, source)
