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
from typing import Any, Callable, cast
from simple_sender.ui.app_commands import (
    ensure_serial_available,
    open_gcode,
    pause_job,
    refresh_ports,
    resume_job,
    run_job,
    start_connect_worker,
    start_disconnect_worker,
    stop_job,
    toggle_connect,
)
from simple_sender.ui.console import (
    clear_console_log,
    save_console_log,
    send_console,
    setup_console_tags,
)
from simple_sender.ui.grbl_lifecycle import (
    handle_auto_reconnect_failure,
    maybe_auto_reconnect,
)
from simple_sender.ui.grbl_settings.info import load_grbl_setting_info
from simple_sender.ui.grbl_settings.requests import request_settings_dump
from simple_sender.ui.ui_actions import (
    confirm_and_run,
    require_grbl_connection,
    run_if_connected,
    send_manual,
)
from simple_sender.ui.kasa_actions import (
    discover_kasa_devices,
    handle_outgoing_gcode_line,
    handle_stream_vacuum_directive,
    handle_stream_spindle_state,
    kasa_settings_snapshot,
    log_kasa_message,
    on_kasa_command_result,
    on_kasa_device_selected,
    on_kasa_mapping_change,
    on_kasa_master_change,
    refresh_kasa_controls_state,
    refresh_kasa_outlet_list,
    start_job_accessories,
    stop_job_accessories,
    toggle_kasa_light_quick,
    toggle_kasa_vacuum_quick,
    test_kasa_outlet,
)
from simple_sender.ui.tool_change_actions import handle_stream_tool_change
from simple_sender.types import StreamToolChangeIdentity

class ActionsMixin:
    def refresh_ports(self, auto_connect: bool = False) -> None:
        refresh_ports(self, auto_connect)

    def toggle_connect(self):
        return toggle_connect(self)

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

    def _request_settings_dump(self) -> bool:
        return bool(request_settings_dump(self))

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

    def _send_manual(self, command: str, source: str) -> bool:
        return bool(send_manual(self, command, source))

    def _kasa_settings_snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], kasa_settings_snapshot(self))

    def _log_kasa_message(self, message: str) -> None:
        log_kasa_message(self, message)

    def _on_kasa_command_result(self, result) -> None:
        on_kasa_command_result(self, result)

    def _on_kasa_master_change(self) -> None:
        on_kasa_master_change(self)

    def _on_kasa_mapping_change(self, changed: str | None = None) -> None:
        on_kasa_mapping_change(self, changed)

    def _on_kasa_device_selected(self, event=None) -> None:
        on_kasa_device_selected(self, event)

    def _discover_kasa_devices(self) -> None:
        discover_kasa_devices(self)

    def _refresh_kasa_outlet_list(self) -> None:
        refresh_kasa_outlet_list(self)

    def _test_kasa_outlet(self, outlet_id: int, on: bool) -> None:
        test_kasa_outlet(self, outlet_id, on)

    def _refresh_kasa_controls_state(self) -> None:
        refresh_kasa_controls_state(self)

    def _handle_outgoing_gcode_line(
        self,
        line: str,
        source: str,
        *,
        line_index: int | None = None,
    ) -> None:
        handle_outgoing_gcode_line(self, line, source, line_index=line_index)

    def _handle_stream_spindle_state(self, is_on: bool) -> None:
        handle_stream_spindle_state(self, is_on)

    def _handle_stream_vacuum_directive(
        self,
        is_on: bool,
        *,
        line_index: int | None = None,
        requires_confirmation: bool = False,
    ) -> bool:
        return bool(
            handle_stream_vacuum_directive(
                self,
                is_on,
                line_index=line_index,
                requires_confirmation=requires_confirmation,
            )
        )

    def _handle_stream_tool_change(
        self,
        tool_name: str,
        identity: StreamToolChangeIdentity,
        *,
        line_index: int | None = None,
    ) -> None:
        handle_stream_tool_change(
            self,
            tool_name,
            identity,
            line_index=line_index,
        )

    def _start_job_accessories(self, source: str = "job_run") -> None:
        start_job_accessories(self, source=source)

    def _stop_job_accessories(self, source: str = "job_stop") -> None:
        stop_job_accessories(self, source=source)

    def _toggle_kasa_vacuum_quick(self) -> None:
        toggle_kasa_vacuum_quick(self)

    def _toggle_kasa_light_quick(self) -> None:
        toggle_kasa_light_quick(self)
