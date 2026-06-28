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
from simple_sender.ui.kasa_state import (
    OUTLET_LABELS,
    format_kasa_status_line as _format_kasa_status_line,
    kasa_settings_snapshot as _kasa_settings_snapshot,
    kasa_status_snapshot as _kasa_status_snapshot,
    kasa_supported as _kasa_supported_for_platform,
    outlet_id_from_label as _outlet_id_from_label,
    outlet_label as _outlet_label,
    read_var_value as _read_var_value,
)
from simple_sender.ui import kasa_runtime as _kasa_runtime

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _kasa_supported() -> bool:
    return _kasa_supported_for_platform(sys.platform)


def _kasa_helper_platform() -> str:
    return "linux" if _kasa_supported() else str(sys.platform or "")


def outlet_label(outlet_id: int) -> str:
    return _outlet_label(outlet_id)


def outlet_id_from_label(label: str, default: int) -> int:
    return _outlet_id_from_label(label, default)


def kasa_settings_snapshot(app) -> dict[str, Any]:
    return _kasa_settings_snapshot(app, platform=_kasa_helper_platform())


def kasa_status_snapshot(app) -> dict[str, Any]:
    return _kasa_status_snapshot(app, platform=_kasa_helper_platform())


def format_kasa_status_line(app) -> str:
    return _format_kasa_status_line(app, platform=_kasa_helper_platform())


def _set_stringvar_if_changed(var: Any, value: str) -> bool:
    if var is None:
        return False
    getter = getattr(var, "get", None)
    setter = getattr(var, "set", None)
    if not callable(setter):
        return False
    current = None
    if callable(getter):
        try:
            current = str(getter() or "")
        except Exception:
            current = None
    text = str(value or "")
    if current == text:
        return False
    try:
        setter(text)
        return True
    except Exception:
        return False


def refresh_kasa_status_line(app) -> None:
    line = format_kasa_status_line(app)
    setattr(app, "_kasa_status_line_cached", line)
    _set_stringvar_if_changed(getattr(app, "kasa_status_line_var", None), line)


def _kasa_quick_ready(app) -> bool:
    settings = kasa_settings_snapshot(app)
    if not bool(settings.get("kasa_enabled", False)):
        return False
    return bool(str(settings.get("kasa_device_identifier", "") or "").strip())


def _refresh_kasa_quick_ui(app) -> None:
    try:
        if hasattr(app, "_refresh_kasa_quick_toggle_text"):
            app._refresh_kasa_quick_toggle_text()
    except Exception as exc:
        _log_suppressed("Failed refreshing Kasa quick-toggle text", exc)
    try:
        if hasattr(app, "_update_quick_button_visibility"):
            app._update_quick_button_visibility()
    except Exception as exc:
        _log_suppressed("Failed refreshing Kasa quick-button visibility", exc)
    try:
        refresh_kasa_status_line(app)
    except Exception as exc:
        _log_suppressed("Failed refreshing Kasa status line", exc)


def _set_kasa_quick_state(app, *, vacuum: bool | None = None, light: bool | None = None) -> None:
    if vacuum is not None:
        try:
            app._kasa_vacuum_quick_on = bool(vacuum)
        except Exception as exc:
            _log_suppressed("Failed updating Kasa vacuum quick-toggle state", exc)
    if light is not None:
        try:
            app._kasa_light_quick_on = bool(light)
        except Exception as exc:
            _log_suppressed("Failed updating Kasa light quick-toggle state", exc)
    _refresh_kasa_quick_ui(app)


def _sync_kasa_quick_state_from_result(app, result: OutletCommandResult) -> None:
    source = str(getattr(result, "source", "") or "").strip().lower()
    if source == "quick_vac":
        _set_kasa_quick_state(
            app,
            vacuum=bool(result.on) if bool(result.success) else (not bool(result.on)),
        )
        return
    if source == "quick_light":
        _set_kasa_quick_state(
            app,
            light=bool(result.on) if bool(result.success) else (not bool(result.on)),
        )
        return
    if not bool(result.success):
        return
    settings = kasa_settings_snapshot(app)
    vacuum_enabled = bool(settings.get("vacuum_enabled", False))
    light_enabled = bool(settings.get("light_enabled", False))
    try:
        outlet_count = max(1, int(settings.get("kasa_outlet_count", 2) or 2))
    except Exception:
        outlet_count = 2
    if outlet_count < 2:
        light_enabled = False
    vacuum_outlet = 1 if int(settings.get("vacuum_outlet", 1) or 1) == 1 else 2
    light_outlet = 1 if int(settings.get("light_outlet", 2) or 2) == 1 else 2
    updated = False
    if vacuum_enabled and int(result.outlet_id) == vacuum_outlet:
        _set_kasa_quick_state(app, vacuum=bool(result.on))
        updated = True
    if light_enabled and int(result.outlet_id) == light_outlet:
        _set_kasa_quick_state(app, light=bool(result.on))
        updated = True
    if not updated:
        _refresh_kasa_quick_ui(app)


def _sync_kasa_job_state_from_result(app, result: OutletCommandResult) -> None:
    source = str(getattr(result, "source", "") or "").strip().lower()
    if not source.startswith("job_"):
        return
    try:
        outlet = int(result.outlet_id)
    except Exception:
        return
    active = set(int(value) for value in getattr(app, "_kasa_job_active_outlets", set()) or set())
    pending_off = set(
        int(value) for value in getattr(app, "_kasa_job_pending_off_outlets", set()) or set()
    )
    if bool(result.on):
        if bool(result.success):
            active.add(outlet)
        else:
            active.discard(outlet)
        pending_off.discard(outlet)
    else:
        pending_off.discard(outlet)
        if bool(result.success):
            active.discard(outlet)
        else:
            active.add(outlet)
            log_kasa_message(
                app,
                f"Job accessory OFF failed for outlet {outlet}; tracking remains active until a confirmed OFF succeeds.",
            )
    app._kasa_job_active_outlets = active
    app._kasa_job_pending_off_outlets = pending_off
    app._kasa_job_running = bool(active)


def _complete_confirmed_stream_vacuum_directive(app, result: OutletCommandResult) -> None:
    source = str(getattr(result, "source", "") or "").strip().lower()
    if source not in {"stream_vacuum_on", "stream_vacuum_off", "stream_vacuum_off_delayed"}:
        return
    setting = getattr(app, "kasa_confirm_stream_directives", False)
    getter = getattr(setting, "get", None)
    required = bool(getter()) if callable(getter) else bool(setting)
    if not required:
        return
    grbl = getattr(app, "grbl", None)
    completer = getattr(grbl, "complete_stream_vacuum_directive", None)
    if not callable(completer):
        return
    if bool(result.success):
        completer(True, None)
        return
    command = "VACUUM_ON" if bool(result.on) else "VACUUM_OFF"
    detail = str(result.error or "").strip()
    completer(False, f"{command} failed: {detail}" if detail else f"{command} failed.")


def _quick_toggle_request(app, *, channel: str) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        _set_kasa_quick_state(app, vacuum=False, light=False)
        return
    settings = kasa_settings_snapshot(app)
    if not bool(settings.get("kasa_enabled", False)):
        _set_kasa_quick_state(app, vacuum=False, light=False)
        return
    if not str(settings.get("kasa_device_identifier", "") or "").strip():
        log_kasa_message(app, "No Kasa device selected for quick toggle.")
        _refresh_kasa_quick_ui(app)
        return
    try:
        outlet_count = max(1, int(settings.get("kasa_outlet_count", 2) or 2))
    except Exception:
        outlet_count = 2
    if channel == "vacuum":
        enabled = bool(settings.get("vacuum_enabled", False))
        if not enabled:
            _cancel_pending_vacuum_off_delay(app)
            log_kasa_message(app, "Vacuum quick toggle ignored (Vacuum mapping disabled).")
            _set_kasa_quick_state(app, vacuum=False)
            return
        outlet_id = 1 if int(settings.get("vacuum_outlet", 1) or 1) == 1 else 2
        target_state = not bool(getattr(app, "_kasa_vacuum_quick_on", False))
        source = "quick_vac"
    else:
        enabled = bool(settings.get("light_enabled", False)) and outlet_count >= 2
        if not enabled:
            log_kasa_message(app, "Light quick toggle ignored (Spindle Light mapping disabled).")
            _set_kasa_quick_state(app, light=False)
            return
        outlet_id = 1 if int(settings.get("light_outlet", 2) or 2) == 1 else 2
        target_state = not bool(getattr(app, "_kasa_light_quick_on", False))
        source = "quick_light"
    if int(outlet_id) > outlet_count:
        log_kasa_message(app, f"Quick toggle ignored: outlet {outlet_id} is unavailable.")
        _refresh_kasa_quick_ui(app)
        return
    try:
        accepted = bool(
            app.accessory_router.request_outlet_state(
                int(outlet_id),
                bool(target_state),
                source=source,
            )
        )
    except Exception as exc:
        _log_suppressed("Failed issuing Kasa quick-toggle command", exc)
        return
    if not accepted:
        log_kasa_message(app, f"Quick toggle command skipped for outlet {outlet_id}.")
        _refresh_kasa_quick_ui(app)
        return
    if channel == "vacuum":
        if bool(target_state):
            _cancel_pending_vacuum_off_delay(app)
        _set_kasa_quick_state(app, vacuum=bool(target_state))
    else:
        _set_kasa_quick_state(app, light=bool(target_state))


def toggle_kasa_vacuum_quick(app) -> None:
    _quick_toggle_request(app, channel="vacuum")


def toggle_kasa_light_quick(app) -> None:
    _quick_toggle_request(app, channel="light")


def log_kasa_message(app, message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    try:
        app.ui_q.put(("log", f"[kasa] {text}"))
    except Exception as exc:
        _log_suppressed("Failed queueing Kasa log message to UI thread", exc)


def _post_kasa_ui_callback(app, callback, *, context: str) -> bool:
    if threading.current_thread() is threading.main_thread():
        callback()
        return True
    post_ui = getattr(app, "_post_ui_thread", None)
    if callable(post_ui):
        try:
            post_ui(callback)
            return True
        except Exception as exc:
            _log_suppressed(f"{context} via _post_ui_thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return True
        except Exception as exc:
            _log_suppressed(f"{context} via ui_q", exc)
    return False


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


def _set_kasa_failure_status(app, result: OutletCommandResult) -> None:
    status = getattr(app, "status", None)
    if status is None:
        return
    command = "ON" if bool(result.on) else "OFF"
    source = str(result.source or "kasa")
    text = f"Kasa {command} failed ({source}); dust collection state not confirmed"
    if result.line_index is not None:
        text += f" at stream line {int(result.line_index) + 1}"
    config = getattr(status, "config", None)
    if callable(config):
        try:
            config(text=text)
            return
        except Exception as exc:
            _log_suppressed("Failed updating status after Kasa command failure", exc)
    setter = getattr(status, "set", None)
    if callable(setter):
        try:
            setter(text)
        except Exception as exc:
            _log_suppressed("Failed setting status after Kasa command failure", exc)


def on_kasa_command_result(app, result: OutletCommandResult) -> None:
    def _apply() -> None:
        _set_outlet_status(app, result)
        _sync_kasa_job_state_from_result(app, result)
        _sync_kasa_quick_state_from_result(app, result)
        _complete_confirmed_stream_vacuum_directive(app, result)
        if not result.success:
            detail = (
                " dust collection state not confirmed; CNC job may continue."
                f" outlet={result.outlet_id} command={'ON' if result.on else 'OFF'} source={result.source}"
            )
            if result.line_index is not None:
                detail += f" line_index={int(result.line_index)}"
            if result.device_identifier:
                detail += f" device={result.device_identifier}"
            if result.device_ip:
                detail += f" ip={result.device_ip}"
            if result.failure_kind:
                detail += f" failure_kind={result.failure_kind}"
            if result.attempts is not None and result.max_attempts is not None:
                detail += f" attempts={int(result.attempts)}/{int(result.max_attempts)}"
            if result.timeout_s is not None:
                detail += f" timeout={float(result.timeout_s):.1f}s"
            if result.elapsed_s is not None:
                detail += f" elapsed={float(result.elapsed_s):.3f}s"
            connectivity = dict(result.connectivity or {})
            for key in sorted(connectivity):
                value = str(connectivity.get(key, "") or "").strip()
                if value:
                    detail += f" {key}={value.replace(chr(10), ' | ')}"
            if result.error:
                detail += f" error={result.error}"
            log_kasa_message(app, f"Warning:{detail}")
            _set_kasa_failure_status(app, result)

    posted = _post_kasa_ui_callback(
        app,
        _apply,
        context="Failed posting Kasa command result to UI thread",
    )
    if not posted:
        logger.warning(
            "Dropped Kasa command result before UI reconcile: outlet=%s command=%s source=%s success=%s error=%s",
            int(result.outlet_id),
            "ON" if bool(result.on) else "OFF",
            str(getattr(result, "source", "") or ""),
            bool(result.success),
            str(getattr(result, "error", "") or ""),
        )


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
            "kasa_confirm_stream_directives_check",
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
        refresh_kasa_status_line(app)
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
        getattr(app, "kasa_confirm_stream_directives_check", None),
        "normal" if enabled and vacuum_enabled else "disabled",
    )
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
    refresh_kasa_status_line(app)


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
    snapshot = kasa_settings_snapshot(app)
    if not bool(snapshot.get("vacuum_enabled", False)):
        _cancel_pending_vacuum_off_delay(app)
        try:
            app._kasa_vacuum_quick_on = False
        except Exception as exc:
            _log_suppressed("Failed clearing Kasa vacuum quick state after mapping change", exc)
    if not bool(snapshot.get("light_enabled", False)):
        try:
            app._kasa_light_quick_on = False
        except Exception as exc:
            _log_suppressed("Failed clearing Kasa light quick state after mapping change", exc)
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    refresh_kasa_controls_state(app)
    _refresh_kasa_quick_ui(app)


def on_kasa_master_change(app) -> None:
    if not _kasa_supported():
        _cancel_pending_vacuum_off_delay(app)
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
        _set_kasa_quick_state(app, vacuum=False, light=False)
        refresh_kasa_controls_state(app)
        return
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    if not bool(app.kasa_enabled.get()):
        _cancel_pending_vacuum_off_delay(app)
        if hasattr(app, "kasa_validation_var"):
            app.kasa_validation_var.set("")
        _set_kasa_quick_state(app, vacuum=False, light=False)
    refresh_kasa_controls_state(app)
    _refresh_kasa_quick_ui(app)


def on_kasa_device_selected(app, _event=None) -> None:
    if not _kasa_supported():
        _cancel_pending_vacuum_off_delay(app)
        if hasattr(app, "kasa_device_identifier"):
            try:
                app.kasa_device_identifier.set("")
            except Exception as exc:
                _log_suppressed("Failed clearing Kasa device selection on non-Linux platform", exc)
        _set_kasa_quick_state(app, vacuum=False, light=False)
        refresh_kasa_controls_state(app)
        return
    option = str(app.kasa_device_choice.get() or "").strip()
    identifier = str(getattr(app, "_kasa_device_label_to_identifier", {}).get(option, "") or "").strip()
    if not identifier and option and "@" in option:
        identifier = option
    app.kasa_device_identifier.set(identifier)
    if hasattr(app, "accessory_router"):
        app.accessory_router.reset_debounce()
    if not identifier:
        _cancel_pending_vacuum_off_delay(app)
        _set_kasa_quick_state(app, vacuum=False, light=False)
    refresh_kasa_controls_state(app)
    _refresh_kasa_quick_ui(app)
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
        _set_kasa_quick_state(app, light=False)
    else:
        validate_kasa_outlet_mapping(app)
    refresh_kasa_controls_state(app)
    _refresh_kasa_quick_ui(app)


def _cancel_pending_vacuum_off_delay(app) -> None:
    _kasa_runtime.cancel_pending_vacuum_off_delay(app)


def discover_kasa_devices(app) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    if not bool(app.kasa_enabled.get()):
        return
    _kasa_runtime.discover_devices(
        app,
        on_success_ui=_handle_discovery_success,
        log_message=log_kasa_message,
    )


def refresh_kasa_outlet_list(app) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    if not bool(app.kasa_enabled.get()):
        return
    identifier = str(app.kasa_device_identifier.get() or "").strip()
    if not identifier:
        return
    _kasa_runtime.refresh_outlet_list(
        app,
        identifier,
        on_success_ui=_handle_outlet_list_success,
        log_message=log_kasa_message,
    )


def test_kasa_outlet(app, outlet_id: int, on: bool) -> None:
    if not _kasa_supported():
        refresh_kasa_controls_state(app)
        return
    _kasa_runtime.test_outlet(
        app,
        int(outlet_id),
        bool(on),
        log_message=log_kasa_message,
    )


def handle_outgoing_gcode_line(
    app,
    line: str,
    source: str,
    *,
    line_index: int | None = None,
) -> None:
    # Kasa automation is lifecycle-driven (job start/stop), not spindle-command driven.
    _ = app
    _ = line
    _ = source
    _ = line_index


def handle_stream_vacuum_directive(
    app,
    is_on: bool,
    *,
    line_index: int | None = None,
    requires_confirmation: bool = False,
) -> bool:
    if not _kasa_supported():
        command = "VACUUM_ON" if bool(is_on) else "VACUUM_OFF"
        line_text = f" at stream line {int(line_index) + 1}" if line_index is not None else ""
        log_kasa_message(app, f"{command}{line_text} ignored: Kasa control is available on Linux only.")
        return False
    accepted = _kasa_runtime.handle_stream_vacuum_directive(
        app,
        bool(is_on),
        settings_snapshot=kasa_settings_snapshot,
        log_message=log_kasa_message,
        line_index=line_index,
    )
    # Do not mutate UI state here; wait for AccessoryRouter command-result callback
    # so stream directives use worker execution + final UI reconciliation only.
    if bool(requires_confirmation) and not bool(accepted):
        log_kasa_message(
            app,
            "Stream is held because the Kasa directive could not be queued for confirmation.",
        )
    return bool(accepted)


def start_job_accessories(app, *, source: str = "job_run") -> None:
    if not _kasa_supported():
        return
    _kasa_runtime.start_job_accessories(
        app,
        settings_snapshot=kasa_settings_snapshot,
        log_message=log_kasa_message,
        source=source,
    )


def stop_job_accessories(app, *, source: str = "job_stop") -> None:
    if not _kasa_supported():
        return
    _kasa_runtime.stop_job_accessories(
        app,
        settings_snapshot=kasa_settings_snapshot,
        log_message=log_kasa_message,
        source=source,
    )


def handle_stream_spindle_state(app, is_on: bool) -> None:
    # Spindle events are intentionally ignored; Kasa automation follows job lifecycle.
    _ = app
    _ = is_on


__all__ = [
    "OUTLET_LABELS",
    "discover_kasa_devices",
    "format_kasa_status_line",
    "handle_outgoing_gcode_line",
    "handle_stream_vacuum_directive",
    "handle_stream_spindle_state",
    "kasa_status_snapshot",
    "kasa_settings_snapshot",
    "log_kasa_message",
    "on_kasa_command_result",
    "on_kasa_device_selected",
    "on_kasa_mapping_change",
    "on_kasa_master_change",
    "refresh_kasa_controls_state",
    "refresh_kasa_status_line",
    "refresh_kasa_outlet_list",
    "start_job_accessories",
    "stop_job_accessories",
    "toggle_kasa_light_quick",
    "toggle_kasa_vacuum_quick",
    "test_kasa_outlet",
    "validate_kasa_outlet_mapping",
]
