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

from __future__ import annotations

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
from typing import Any, Callable

from simple_sender.kasa_accessory import DeviceInfo, OutletInfo, validate_outlet_mapping


logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()

SettingsSnapshot = Callable[[Any], dict[str, Any]]
LogMessage = Callable[[Any, str], None]
UiHandler = Callable[..., None]


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _post_ui(app, func: UiHandler, *args) -> None:
    try:
        app._post_ui_thread(func, *args)
    except Exception as exc:
        _log_suppressed("Failed posting Kasa callback to UI thread", exc)


def _outlet_count(settings: dict[str, Any]) -> int:
    try:
        return max(1, int(settings.get("kasa_outlet_count", 2) or 2))
    except Exception:
        return 2


def _selected_outlet(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        outlet_id = int(settings.get(key, default) or default)
    except Exception:
        outlet_id = default
    return 1 if outlet_id == 1 else 2


def _request_outlet_state(
    app,
    *,
    outlet_id: int,
    is_on: bool,
    source: str,
    failure_context: str,
    skipped_message: str,
    log_message: LogMessage,
    line_index: int | None = None,
    work_identity=None,
    source_identity=None,
    safety_priority: bool = False,
) -> bool:
    accepted = False
    try:
        request_kwargs: dict[str, Any] = {"source": str(source or "kasa")}
        grbl = getattr(app, "grbl", None)
        if work_identity is None:
            identity_getter = getattr(grbl, "current_work_identity", None)
            if callable(identity_getter):
                work_identity = identity_getter()
        if source_identity is None:
            source_getter = getattr(grbl, "current_gcode_source_identity", None)
            if callable(source_getter):
                source_identity = source_getter()
        recovery_checker = getattr(grbl, "recovery_required", None)
        recovery_required = bool(recovery_checker()) if callable(recovery_checker) else False
        safety_off = bool(safety_priority and not is_on)
        if recovery_required and not safety_off:
            log_message(app, "Accessory command blocked while Recovery Required is active.")
            return False
        if work_identity is None:
            return False
        current_values = (
            int(getattr(grbl, "connection_generation")()),
            int(getattr(grbl, "stream_epoch")()),
            int(getattr(grbl, "recovery_epoch")()),
        )
        captured_values = (
            int(work_identity.connection_generation),
            int(work_identity.stream_epoch),
            int(work_identity.recovery_epoch),
        )
        if captured_values != current_values:
            return False
        request_kwargs.update(
            connection_generation=captured_values[0],
            stream_epoch=captured_values[1],
            recovery_epoch=captured_values[2],
            source_id=int(getattr(source_identity, "source_id", 0) or 0),
            safety_priority=bool(safety_priority),
        )
        if line_index is not None:
            request_kwargs["line_index"] = int(line_index)
        accepted = bool(
            app.accessory_router.request_outlet_state(
                int(outlet_id),
                bool(is_on),
                **request_kwargs,
            )
        )
    except Exception as exc:
        _log_suppressed(failure_context, exc)
    if not accepted:
        log_message(app, skipped_message)
    return accepted


def _clear_pending_vacuum_off_delay_state(app) -> None:
    app._kasa_vacuum_off_delay_after_id = None
    app._kasa_vacuum_off_delay_outlet = None
    app._kasa_vacuum_off_delay_source = ""


def cancel_pending_vacuum_off_delay(app) -> None:
    after_id = getattr(app, "_kasa_vacuum_off_delay_after_id", None)
    if after_id is None:
        _clear_pending_vacuum_off_delay_state(app)
        return
    cancel = getattr(app, "after_cancel", None)
    if callable(cancel):
        try:
            cancel(after_id)
        except Exception as exc:
            _log_suppressed("Failed canceling delayed vacuum OFF callback", exc)
    _clear_pending_vacuum_off_delay_state(app)


def discover_devices(app, *, on_success_ui: UiHandler, log_message: LogMessage) -> None:
    def _on_success(devices: list[DeviceInfo]) -> None:
        _post_ui(app, on_success_ui, app, devices)

    def _on_error(exc: Exception) -> None:
        _post_ui(app, log_message, app, f"Discovery failed: {exc}")

    app.accessory_router.discover(on_success=_on_success, on_error=_on_error)


def refresh_outlet_list(
    app,
    identifier: str,
    *,
    on_success_ui: UiHandler,
    log_message: LogMessage,
) -> None:
    def _on_success(outlets: list[OutletInfo]) -> None:
        _post_ui(app, on_success_ui, app, outlets)

    def _on_error(exc: Exception) -> None:
        _post_ui(app, log_message, app, f"Outlet refresh failed: {exc}")

    app.accessory_router.list_outlets(
        str(identifier or "").strip(),
        on_success=_on_success,
        on_error=_on_error,
    )


def test_outlet(
    app,
    outlet_id: int,
    on: bool,
    *,
    log_message: LogMessage,
) -> None:
    _request_outlet_state(
        app,
        outlet_id=int(outlet_id),
        is_on=bool(on),
        source="test",
        failure_context="Failed issuing Kasa outlet test command",
        skipped_message=f"Test command skipped for outlet {int(outlet_id)}.",
        log_message=log_message,
    )


def _request_vacuum_state(
    app,
    *,
    outlet_id: int,
    is_on: bool,
    source: str,
    line_index: int | None = None,
    log_message: LogMessage,
    work_identity=None,
    source_identity=None,
    safety_priority: bool = False,
) -> bool:
    line_text = f" at stream line {int(line_index) + 1}" if line_index is not None else ""
    return _request_outlet_state(
        app,
        outlet_id=int(outlet_id),
        is_on=bool(is_on),
        source=str(source or "stream_vacuum"),
        line_index=line_index,
        failure_context="Failed issuing vacuum outlet command to Kasa router",
        skipped_message=(
            f"VACUUM_{'ON' if is_on else 'OFF'} request skipped for outlet {int(outlet_id)}{line_text}."
        ),
        log_message=log_message,
        work_identity=work_identity,
        source_identity=source_identity,
        safety_priority=bool(safety_priority),
    )


def _request_vacuum_off_with_delay(
    app,
    *,
    outlet_id: int,
    source: str,
    delay_s: float,
    line_index: int | None = None,
    log_message: LogMessage,
) -> bool:
    delay = max(0.0, float(delay_s))
    source_text = str(source or "stream_vacuum_off")
    safety_source = source_text in {
        "job_recovery_required",
        "job_all_stop",
        "job_reset",
        "app_exit",
    }
    if delay <= 0.0 or source_text.startswith("app_") or safety_source:
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            line_index=line_index,
            log_message=log_message,
            safety_priority=source_text == "job_recovery_required",
        )
    delay_ms = int(round(delay * 1000.0))
    if delay_ms <= 0:
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            line_index=line_index,
            log_message=log_message,
        )
    cancel_pending_vacuum_off_delay(app)
    grbl = getattr(app, "grbl", None)
    work_getter = getattr(grbl, "current_work_identity", None)
    source_getter = getattr(grbl, "current_gcode_source_identity", None)
    scheduled_work_identity = work_getter() if callable(work_getter) else None
    scheduled_source_identity = source_getter() if callable(source_getter) else None
    after = getattr(app, "after", None)
    if not callable(after):
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            line_index=line_index,
            log_message=log_message,
        )

    def _run_delayed_off() -> None:
        _clear_pending_vacuum_off_delay_state(app)
        _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=f"{source_text}_delayed",
            line_index=line_index,
            log_message=log_message,
            work_identity=scheduled_work_identity,
            source_identity=scheduled_source_identity,
        )

    try:
        after_id = after(delay_ms, _run_delayed_off)
    except Exception as exc:
        _log_suppressed("Failed scheduling delayed vacuum OFF callback", exc)
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            line_index=line_index,
            log_message=log_message,
        )
    app._kasa_vacuum_off_delay_after_id = after_id
    app._kasa_vacuum_off_delay_outlet = int(outlet_id)
    app._kasa_vacuum_off_delay_source = source_text
    log_message(
        app,
        f"VACUUM_OFF delayed by {delay:.1f}s for outlet {int(outlet_id)}.",
    )
    return True


def handle_stream_vacuum_directive(
    app,
    is_on: bool,
    *,
    settings_snapshot: SettingsSnapshot,
    log_message: LogMessage,
    line_index: int | None = None,
) -> bool:
    command = "VACUUM_ON" if bool(is_on) else "VACUUM_OFF"
    line_text = f" at stream line {int(line_index) + 1}" if line_index is not None else ""
    if not hasattr(app, "accessory_router"):
        log_message(app, f"{command}{line_text} ignored: Kasa accessory router is unavailable.")
        return False
    settings = settings_snapshot(app)
    if not bool(settings.get("kasa_enabled", False)):
        log_message(app, f"{command}{line_text} ignored: Kasa control is disabled.")
        return False
    if not bool(settings.get("vacuum_enabled", False)):
        log_message(app, f"{command}{line_text} ignored: vacuum control is disabled.")
        return False
    device_identifier = str(settings.get("kasa_device_identifier", "") or "").strip()
    if not device_identifier:
        log_message(app, f"{command}{line_text} ignored: no Kasa device is selected.")
        return False
    outlet_count = _outlet_count(settings)
    outlet_id = _selected_outlet(settings, "vacuum_outlet", 1)
    if outlet_id > outlet_count:
        log_message(
            app,
            f"VACUUM_{'ON' if is_on else 'OFF'} ignored: outlet {outlet_id} unavailable.",
        )
        return False
    if bool(is_on):
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=True,
            source="stream_vacuum_on",
            line_index=line_index,
            log_message=log_message,
        )
    return _request_vacuum_off_with_delay(
        app,
        outlet_id=int(outlet_id),
        source="stream_vacuum_off",
        delay_s=max(0.0, float(settings.get("vacuum_off_delay_sec", 0.0) or 0.0)),
        line_index=line_index,
        log_message=log_message,
    )


def _selected_job_outlets(
    settings: dict[str, Any],
    *,
    app,
    log_message: LogMessage,
) -> list[int]:
    if not bool(settings.get("kasa_enabled", False)):
        return []
    device_identifier = str(settings.get("kasa_device_identifier", "") or "").strip()
    if not device_identifier:
        return []
    outlet_count = _outlet_count(settings)
    vacuum_enabled = bool(settings.get("vacuum_enabled", False))
    light_enabled = bool(settings.get("light_enabled", False)) and outlet_count >= 2
    vacuum_outlet = _selected_outlet(settings, "vacuum_outlet", 1)
    light_outlet = _selected_outlet(settings, "light_outlet", 2)
    valid, message = validate_outlet_mapping(
        vacuum_enabled=vacuum_enabled,
        vacuum_outlet=vacuum_outlet,
        light_enabled=light_enabled,
        light_outlet=light_outlet,
    )
    if not valid:
        log_message(app, str(message or "Invalid Kasa outlet mapping."))
        return []
    outlets: list[int] = []
    if vacuum_enabled and vacuum_outlet <= outlet_count:
        outlets.append(vacuum_outlet)
    if light_enabled and light_outlet <= outlet_count and light_outlet not in outlets:
        outlets.append(light_outlet)
    return outlets


def _request_job_outlet_state(
    app,
    outlet_id: int,
    on: bool,
    *,
    source: str,
    log_message: LogMessage,
    safety_priority: bool = False,
) -> bool:
    return _request_outlet_state(
        app,
        outlet_id=int(outlet_id),
        is_on=bool(on),
        source=str(source or "job"),
        failure_context="Failed sending Kasa job-lifecycle outlet command",
        skipped_message=(
            f"Job accessory {'start' if on else 'stop'} rejected for outlet {int(outlet_id)}."
        ),
        log_message=log_message,
        safety_priority=bool(safety_priority),
    )


def start_job_accessories(
    app,
    *,
    settings_snapshot: SettingsSnapshot,
    log_message: LogMessage,
    source: str = "job_run",
) -> None:
    if not hasattr(app, "accessory_router"):
        return
    if bool(getattr(app, "_kasa_job_running", False)):
        return
    cancel_pending_vacuum_off_delay(app)
    settings = settings_snapshot(app)
    outlets = _selected_job_outlets(settings, app=app, log_message=log_message)
    active_outlets: set[int] = set()
    for outlet_id in outlets:
        if _request_job_outlet_state(
            app,
            int(outlet_id),
            True,
            source=source,
            log_message=log_message,
        ):
            active_outlets.add(int(outlet_id))
    app._kasa_job_active_outlets = active_outlets
    app._kasa_job_pending_off_outlets = set()
    app._kasa_job_running = bool(active_outlets)


def stop_job_accessories(
    app,
    *,
    settings_snapshot: SettingsSnapshot,
    log_message: LogMessage,
    source: str = "job_stop",
) -> None:
    if not hasattr(app, "accessory_router"):
        return
    settings = settings_snapshot(app)
    tracked = getattr(app, "_kasa_job_active_outlets", None)
    if isinstance(tracked, set) and tracked:
        outlet_ids = sorted(int(outlet) for outlet in tracked)
    else:
        outlet_ids = _selected_job_outlets(settings, app=app, log_message=log_message)
    vacuum_enabled = bool(settings.get("vacuum_enabled", False))
    vacuum_outlet = _selected_outlet(settings, "vacuum_outlet", 1)
    vacuum_off_delay_s = max(0.0, float(settings.get("vacuum_off_delay_sec", 0.0) or 0.0))
    remaining_active = set(int(outlet_id) for outlet_id in outlet_ids)
    pending_off = set(
        int(outlet) for outlet in getattr(app, "_kasa_job_pending_off_outlets", set()) or set()
    )
    for outlet_id in outlet_ids:
        if vacuum_enabled and int(outlet_id) == int(vacuum_outlet):
            accepted = _request_vacuum_off_with_delay(
                app,
                outlet_id=int(outlet_id),
                source=str(source or "job_stop"),
                delay_s=float(vacuum_off_delay_s),
                log_message=log_message,
            )
            if not accepted:
                log_message(app, f"Job accessory stop rejected for outlet {int(outlet_id)}.")
        else:
            accepted = _request_job_outlet_state(
                app,
                int(outlet_id),
                False,
                source=source,
                log_message=log_message,
                safety_priority=str(source or "") == "job_recovery_required",
            )
        if accepted:
            pending_off.add(int(outlet_id))
    app._kasa_job_active_outlets = remaining_active
    app._kasa_job_pending_off_outlets = pending_off
    app._kasa_job_running = bool(remaining_active)
