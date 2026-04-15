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
) -> bool:
    accepted = False
    try:
        accepted = bool(
            app.accessory_router.request_outlet_state(
                int(outlet_id),
                bool(is_on),
                source=str(source or "kasa"),
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
    log_message: LogMessage,
) -> bool:
    return _request_outlet_state(
        app,
        outlet_id=int(outlet_id),
        is_on=bool(is_on),
        source=str(source or "stream_vacuum"),
        failure_context="Failed issuing vacuum outlet command to Kasa router",
        skipped_message=(
            f"VACUUM_{'ON' if is_on else 'OFF'} request skipped for outlet {int(outlet_id)}."
        ),
        log_message=log_message,
    )


def _request_vacuum_off_with_delay(
    app,
    *,
    outlet_id: int,
    source: str,
    delay_s: float,
    log_message: LogMessage,
) -> bool:
    delay = max(0.0, float(delay_s))
    source_text = str(source or "stream_vacuum_off")
    if delay <= 0.0 or source_text.startswith("app_"):
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            log_message=log_message,
        )
    delay_ms = int(round(delay * 1000.0))
    if delay_ms <= 0:
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            log_message=log_message,
        )
    cancel_pending_vacuum_off_delay(app)
    after = getattr(app, "after", None)
    if not callable(after):
        cancel_pending_vacuum_off_delay(app)
        return _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=source_text,
            log_message=log_message,
        )

    def _run_delayed_off() -> None:
        _clear_pending_vacuum_off_delay_state(app)
        _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=False,
            source=f"{source_text}_delayed",
            log_message=log_message,
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
) -> None:
    if not hasattr(app, "accessory_router"):
        return
    settings = settings_snapshot(app)
    if not bool(settings.get("kasa_enabled", False)):
        return
    if not bool(settings.get("vacuum_enabled", False)):
        return
    device_identifier = str(settings.get("kasa_device_identifier", "") or "").strip()
    if not device_identifier:
        return
    outlet_count = _outlet_count(settings)
    outlet_id = _selected_outlet(settings, "vacuum_outlet", 1)
    if outlet_id > outlet_count:
        log_message(
            app,
            f"VACUUM_{'ON' if is_on else 'OFF'} ignored: outlet {outlet_id} unavailable.",
        )
        return
    if bool(is_on):
        cancel_pending_vacuum_off_delay(app)
        _request_vacuum_state(
            app,
            outlet_id=int(outlet_id),
            is_on=True,
            source="stream_vacuum_on",
            log_message=log_message,
        )
        return
    _request_vacuum_off_with_delay(
        app,
        outlet_id=int(outlet_id),
        source="stream_vacuum_off",
        delay_s=max(0.0, float(settings.get("vacuum_off_delay_sec", 0.0) or 0.0)),
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
            )
        if accepted:
            remaining_active.discard(int(outlet_id))
    app._kasa_job_active_outlets = remaining_active
    app._kasa_job_running = bool(remaining_active)

