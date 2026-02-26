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
import sys
import threading
import time
from typing import Any

from simple_sender.kasa_accessory import (
    DeviceInfo,
    OutletCommandResult,
    OutletInfo,
    validate_outlet_mapping,
)


OUTLET_LABELS = ("Outlet 1", "Outlet 2")
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _kasa_supported() -> bool:
    return sys.platform.startswith("linux")


def outlet_label(outlet_id: int) -> str:
    return f"Outlet {2 if int(outlet_id) == 2 else 1}"


def outlet_id_from_label(label: str, default: int) -> int:
    text = str(label or "").strip().lower()
    if text.endswith("2"):
        return 2
    if text.endswith("1"):
        return 1
    return 2 if int(default) == 2 else 1


def _read_var_value(app, attr_name: str, default: Any) -> Any:
    var = getattr(app, attr_name, None)
    if var is None:
        return default
    getter = getattr(var, "get", None)
    if not callable(getter):
        return default
    try:
        return getter()
    except Exception:
        return default


def kasa_settings_snapshot(app) -> dict[str, Any]:
    if not _kasa_supported():
        return {
            "kasa_enabled": False,
            "kasa_device_identifier": "",
            "kasa_outlet_count": 2,
            "vacuum_enabled": False,
            "vacuum_outlet": 1,
            "light_enabled": False,
            "light_outlet": 2,
        }
    vacuum_outlet = int(_read_var_value(app, "vacuum_outlet", 1))
    light_outlet = int(_read_var_value(app, "light_outlet", 2))
    if vacuum_outlet not in (1, 2):
        vacuum_outlet = 1
    if light_outlet not in (1, 2):
        light_outlet = 2
    return {
        "kasa_enabled": bool(_read_var_value(app, "kasa_enabled", False)),
        "kasa_device_identifier": (
            str(_read_var_value(app, "kasa_device_identifier", "") or "").strip()
        ),
        "kasa_outlet_count": max(1, int(getattr(app, "_kasa_outlet_count", 2) or 2)),
        "vacuum_enabled": bool(_read_var_value(app, "vacuum_enabled", False)),
        "vacuum_outlet": vacuum_outlet,
        "light_enabled": bool(_read_var_value(app, "light_enabled", False)),
        "light_outlet": light_outlet,
    }


def log_kasa_message(app, message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    try:
        app.ui_q.put(("log", f"[kasa] {text}"))
    except Exception as exc:
        _log_suppressed("Failed queueing Kasa log message to UI thread", exc)


def _set_widget_state(widget: Any, state: str) -> None:
    if widget is None:
        return
    try:
        widget.config(state=state)
    except Exception as exc:
        _log_suppressed("Failed updating Kasa widget state", exc)


def _coerce_outlet_setting(app, field_name: str, label_name: str, *, default: int) -> int:
    outlet_var = getattr(app, field_name, None)
    label_var = getattr(app, label_name, None)
    if outlet_var is None:
        return default
    try:
        value = int(outlet_var.get())
    except Exception:
        value = default
    if value not in (1, 2):
        value = default
        try:
            outlet_var.set(value)
        except Exception as exc:
            _log_suppressed("Failed normalizing Kasa outlet variable", exc)
    if label_var is not None:
        try:
            label_var.set(outlet_label(value))
        except Exception as exc:
            _log_suppressed("Failed syncing Kasa outlet label variable", exc)
    return value


def _apply_kasa_device_choice(app, discovered: list[DeviceInfo]) -> None:
    label_to_identifier: dict[str, str] = {}
    combo_values: list[str] = []
    for info in discovered:
        option = f"{info.label} ({info.ip}) - {info.outlet_count} outlets"
        label_to_identifier[option] = info.identifier
        combo_values.append(option)
    app._kasa_device_label_to_identifier = label_to_identifier
    if hasattr(app, "kasa_device_combo"):
        app.kasa_device_combo["values"] = tuple(combo_values)

    selected_identifier = str(app.kasa_device_identifier.get() or "").strip()
    selected_option = ""
    for option, identifier in label_to_identifier.items():
        if identifier == selected_identifier:
            selected_option = option
            break
    if not selected_option and combo_values:
        selected_option = combo_values[0]
        try:
            app.kasa_device_identifier.set(label_to_identifier[selected_option])
        except Exception as exc:
            _log_suppressed("Failed selecting first discovered Kasa device identifier", exc)
    if hasattr(app, "kasa_device_choice"):
        try:
            app.kasa_device_choice.set(selected_option)
        except Exception as exc:
            _log_suppressed("Failed syncing Kasa device combobox selection", exc)


def _set_outlet_status(app, result: OutletCommandResult) -> None:
    timestamp = time.strftime("%H:%M:%S")
    command = "ON" if result.on else "OFF"
    reachability = "Reachable" if result.success else "Not reachable"
    text = f"Last command: {command} @ {timestamp} ({reachability})"
    if result.outlet_id == 1 and hasattr(app, "kasa_outlet_1_status"):
        app.kasa_outlet_1_status.set(text)
    elif result.outlet_id == 2 and hasattr(app, "kasa_outlet_2_status"):
        app.kasa_outlet_2_status.set(text)


def on_kasa_command_result(app, result: OutletCommandResult) -> None:
    def _apply() -> None:
        _set_outlet_status(app, result)
        if not result.success:
            detail = f" outlet={result.outlet_id} command={'ON' if result.on else 'OFF'} source={result.source}"
            if result.error:
                detail += f" error={result.error}"
            log_kasa_message(app, f"Warning:{detail}")

    if threading.current_thread() is threading.main_thread():
        _apply()
        return
    try:
        app._post_ui_thread(_apply)
    except Exception as exc:
        _log_suppressed("Failed posting Kasa command result to UI thread", exc)


def refresh_kasa_controls_state(app) -> None:
    if not _kasa_supported():
        if hasattr(app, "kasa_validation_var"):
            try:
                app.kasa_validation_var.set("Kasa control is available on Linux only.")
            except Exception as exc:
                _log_suppressed("Failed updating Linux-only Kasa validation message", exc)
        for widget_name in (
            "kasa_enable_check",
            "btn_kasa_discover",
            "kasa_device_combo",
            "vacuum_check",
            "light_check",
            "vacuum_outlet_combo",
            "light_outlet_combo",
            "btn_kasa_refresh_outlets",
            "btn_kasa_outlet1_on",
            "btn_kasa_outlet1_off",
            "btn_kasa_outlet2_on",
            "btn_kasa_outlet2_off",
        ):
            _set_widget_state(getattr(app, widget_name, None), "disabled")
        return

    enabled = bool(_read_var_value(app, "kasa_enabled", False))
    has_device = bool(str(_read_var_value(app, "kasa_device_identifier", "") or "").strip())
    vacuum_enabled = bool(_read_var_value(app, "vacuum_enabled", False))
    light_enabled = bool(_read_var_value(app, "light_enabled", False))

    outlet_count = max(1, int(getattr(app, "_kasa_outlet_count", 2) or 2))
    has_dual_outlet = outlet_count >= 2

    _set_widget_state(getattr(app, "btn_kasa_discover", None), "normal" if enabled else "disabled")
    if hasattr(app, "kasa_device_combo"):
        combo_state = "readonly" if enabled and bool(app.kasa_device_combo["values"]) else "disabled"
        _set_widget_state(app.kasa_device_combo, combo_state)
    _set_widget_state(getattr(app, "vacuum_check", None), "normal" if enabled else "disabled")
    _set_widget_state(
        getattr(app, "light_check", None),
        "normal" if enabled and has_dual_outlet else "disabled",
    )
    _set_widget_state(
        getattr(app, "vacuum_outlet_combo", None),
        "readonly" if enabled and vacuum_enabled else "disabled",
    )
    _set_widget_state(
        getattr(app, "light_outlet_combo", None),
        "readonly" if enabled and light_enabled and has_dual_outlet else "disabled",
    )

    test_state = "normal" if enabled and has_device else "disabled"
    _set_widget_state(getattr(app, "btn_kasa_refresh_outlets", None), test_state)
    _set_widget_state(getattr(app, "btn_kasa_outlet1_on", None), test_state)
    _set_widget_state(getattr(app, "btn_kasa_outlet1_off", None), test_state)
    _set_widget_state(
        getattr(app, "btn_kasa_outlet2_on", None),
        test_state if has_dual_outlet else "disabled",
    )
    _set_widget_state(
        getattr(app, "btn_kasa_outlet2_off", None),
        test_state if has_dual_outlet else "disabled",
    )


def validate_kasa_outlet_mapping(app, *, changed: str | None = None) -> bool:
    if getattr(app, "_kasa_outlet_updating", False):
        return True

    vacuum_outlet = _coerce_outlet_setting(app, "vacuum_outlet", "vacuum_outlet_label", default=1)
    light_outlet = _coerce_outlet_setting(app, "light_outlet", "light_outlet_label", default=2)
    vacuum_enabled = bool(app.vacuum_enabled.get()) if hasattr(app, "vacuum_enabled") else False
    light_enabled = bool(app.light_enabled.get()) if hasattr(app, "light_enabled") else False
    outlet_count = max(1, int(getattr(app, "_kasa_outlet_count", 2) or 2))
    if outlet_count < 2:
        if hasattr(app, "light_enabled"):
            try:
                app.light_enabled.set(False)
            except Exception as exc:
                _log_suppressed("Failed disabling light control for single-outlet Kasa device", exc)
        if hasattr(app, "light_outlet"):
            try:
                app.light_outlet.set(1)
            except Exception as exc:
                _log_suppressed("Failed forcing light outlet to Outlet 1 for single-outlet Kasa device", exc)
        if hasattr(app, "light_outlet_label"):
            try:
                app.light_outlet_label.set("Outlet 1")
            except Exception as exc:
                _log_suppressed("Failed forcing light outlet label for single-outlet Kasa device", exc)
        msg = "Single-outlet Kasa device detected: Vacuum control only."
        if hasattr(app, "kasa_validation_var"):
            app.kasa_validation_var.set(msg)
        app._kasa_last_valid_outlets = (vacuum_outlet, 1)
        return True

    valid, message = validate_outlet_mapping(
        vacuum_enabled=vacuum_enabled,
        vacuum_outlet=vacuum_outlet,
        light_enabled=light_enabled,
        light_outlet=light_outlet,
    )
    if valid:
        if hasattr(app, "kasa_validation_var"):
            app.kasa_validation_var.set("")
        app._kasa_last_valid_outlets = (vacuum_outlet, light_outlet)
        return True

    last_vacuum, last_light = getattr(app, "_kasa_last_valid_outlets", (1, 2))
    app._kasa_outlet_updating = True
    try:
        if changed == "vacuum":
            app.vacuum_outlet.set(int(last_vacuum))
            app.vacuum_outlet_label.set(outlet_label(int(last_vacuum)))
        elif changed == "light":
            app.light_outlet.set(int(last_light))
            app.light_outlet_label.set(outlet_label(int(last_light)))
        else:
            fallback_light = 1 if int(vacuum_outlet) == 2 else 2
            app.light_outlet.set(fallback_light)
            app.light_outlet_label.set(outlet_label(fallback_light))
    finally:
        app._kasa_outlet_updating = False

    if hasattr(app, "kasa_validation_var"):
        app.kasa_validation_var.set(str(message or "Invalid Kasa outlet mapping."))
    log_kasa_message(app, str(message or "Invalid Kasa outlet mapping."))
    return False


def on_kasa_mapping_change(app, changed: str | None = None) -> None:
    validate_kasa_outlet_mapping(app, changed=changed)
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    refresh_kasa_controls_state(app)


def on_kasa_master_change(app) -> None:
    if not _kasa_supported():
        if hasattr(app, "kasa_enabled"):
            try:
                app.kasa_enabled.set(False)
            except Exception as exc:
                _log_suppressed("Failed forcing Kasa master toggle off on non-Linux platform", exc)
        if hasattr(app, "vacuum_enabled"):
            try:
                app.vacuum_enabled.set(False)
            except Exception as exc:
                _log_suppressed("Failed forcing Kasa vacuum toggle off on non-Linux platform", exc)
        if hasattr(app, "light_enabled"):
            try:
                app.light_enabled.set(False)
            except Exception as exc:
                _log_suppressed("Failed forcing Kasa light toggle off on non-Linux platform", exc)
        if hasattr(app, "kasa_device_identifier"):
            try:
                app.kasa_device_identifier.set("")
            except Exception as exc:
                _log_suppressed("Failed clearing Kasa device identifier on non-Linux platform", exc)
        refresh_kasa_controls_state(app)
        return
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    if not bool(app.kasa_enabled.get()):
        if hasattr(app, "kasa_validation_var"):
            app.kasa_validation_var.set("")
    refresh_kasa_controls_state(app)


def on_kasa_device_selected(app, _event=None) -> None:
    if not _kasa_supported():
        if hasattr(app, "kasa_device_identifier"):
            try:
                app.kasa_device_identifier.set("")
            except Exception as exc:
                _log_suppressed("Failed clearing Kasa device selection on non-Linux platform", exc)
        refresh_kasa_controls_state(app)
        return
    option = str(app.kasa_device_choice.get() or "").strip()
    identifier = str(getattr(app, "_kasa_device_label_to_identifier", {}).get(option, "") or "").strip()
    if not identifier and option and "@" in option:
        identifier = option
    app.kasa_device_identifier.set(identifier)
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    refresh_kasa_controls_state(app)
    if identifier:
        refresh_kasa_outlet_list(app)


def _handle_discovery_success(app, devices: list[DeviceInfo]) -> None:
    app._kasa_discovered_devices = list(devices)
    _apply_kasa_device_choice(app, list(devices))
    if hasattr(app, "kasa_outlet_info_var"):
        app.kasa_outlet_info_var.set(f"Discovered {len(devices)} device(s).")
    log_kasa_message(app, f"Discovered {len(devices)} Kasa device(s).")
    refresh_kasa_controls_state(app)


def _handle_outlet_list_success(app, outlets: list[OutletInfo]) -> None:
    app._kasa_outlets = list(outlets)
    app._kasa_outlet_count = max(1, len(outlets))
    if hasattr(app, "kasa_outlet_info_var"):
        app.kasa_outlet_info_var.set(f"Detected outlets: {len(outlets)}")
    if len(outlets) < 2:
        msg = "Single-outlet device detected. Vacuum will use Outlet 1; Spindle Light is disabled."
        if hasattr(app, "light_enabled"):
            try:
                app.light_enabled.set(False)
            except Exception as exc:
                _log_suppressed("Failed disabling light toggle after Kasa outlet refresh", exc)
        if hasattr(app, "light_outlet"):
            try:
                app.light_outlet.set(1)
            except Exception as exc:
                _log_suppressed("Failed forcing light outlet to 1 after Kasa outlet refresh", exc)
        if hasattr(app, "light_outlet_label"):
            try:
                app.light_outlet_label.set("Outlet 1")
            except Exception as exc:
                _log_suppressed("Failed forcing light outlet label after Kasa outlet refresh", exc)
        if hasattr(app, "kasa_validation_var"):
            app.kasa_validation_var.set(msg)
        log_kasa_message(app, msg)
    else:
        validate_kasa_outlet_mapping(app)
    refresh_kasa_controls_state(app)


def _post_ui(app, func, *args) -> None:
    try:
        app._post_ui_thread(func, *args)
    except Exception as exc:
        _log_suppressed("Failed posting Kasa callback to UI thread", exc)


def discover_kasa_devices(app) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    if not bool(app.kasa_enabled.get()):
        return

    def _on_success(devices: list[DeviceInfo]) -> None:
        _post_ui(app, _handle_discovery_success, app, devices)

    def _on_error(exc: Exception) -> None:
        _post_ui(app, log_kasa_message, app, f"Discovery failed: {exc}")

    app.accessory_router.discover(on_success=_on_success, on_error=_on_error)


def refresh_kasa_outlet_list(app) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    if not bool(app.kasa_enabled.get()):
        return
    identifier = str(app.kasa_device_identifier.get() or "").strip()
    if not identifier:
        return

    def _on_success(outlets: list[OutletInfo]) -> None:
        _post_ui(app, _handle_outlet_list_success, app, outlets)

    def _on_error(exc: Exception) -> None:
        _post_ui(app, log_kasa_message, app, f"Outlet refresh failed: {exc}")

    app.accessory_router.list_outlets(identifier, on_success=_on_success, on_error=_on_error)


def test_kasa_outlet(app, outlet_id: int, on: bool) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    accepted = app.accessory_router.request_outlet_state(
        int(outlet_id),
        bool(on),
        source="test",
    )
    if not accepted:
        log_kasa_message(app, f"Test command skipped for outlet {outlet_id}.")


def handle_outgoing_gcode_line(app, line: str, source: str) -> None:
    if not _kasa_supported():
        return
    if not hasattr(app, "accessory_router") or not hasattr(app, "spindle_command_detector"):
        return
    if not bool(app.kasa_enabled.get()):
        return
    state = app.spindle_command_detector.detect_state_change(str(line or ""))
    if state is None:
        return
    _ = source
    app.accessory_router.on_spindle_state_change(state)


__all__ = [
    "OUTLET_LABELS",
    "discover_kasa_devices",
    "handle_outgoing_gcode_line",
    "kasa_settings_snapshot",
    "log_kasa_message",
    "on_kasa_command_result",
    "on_kasa_device_selected",
    "on_kasa_mapping_change",
    "on_kasa_master_change",
    "refresh_kasa_controls_state",
    "refresh_kasa_outlet_list",
    "test_kasa_outlet",
    "validate_kasa_outlet_mapping",
]
