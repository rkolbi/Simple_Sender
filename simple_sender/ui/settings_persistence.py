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

import logging
import os
import sys
from typing import cast

from simple_sender import tool_measurement
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import DEFAULT_SPINDLE_RPM, STATUS_POLL_DEFAULT
from simple_sender.utils.exceptions import SettingsLoadError, SettingsSaveError
from simple_sender.kasa_accessory import validate_outlet_mapping

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def load_settings(app) -> dict:
    settings_path = str(
        getattr(app, "settings_path", "")
        or getattr(getattr(app, "_settings_store", None), "filepath", "")
    )
    file_exists = False
    file_size = 0
    if settings_path:
        try:
            file_exists = os.path.exists(settings_path)
            file_size = os.path.getsize(settings_path) if file_exists else 0
        except Exception as exc:
            _log_suppressed("Failed reading settings file metadata before load", exc)
    logger.info(
        "Settings load begin: path=%s exists=%s size_bytes=%s",
        settings_path or "<unknown>",
        file_exists,
        file_size,
    )
    try:
        app._settings_load_warning_message = ""
    except Exception:
        pass
    try:
        loaded = app._settings_store.load()
        if not loaded:
            logger.info("No settings file found; using defaults.")
        logger.info(
            "Settings load complete: path=%s parse_success=%s",
            settings_path or "<unknown>",
            bool(loaded),
        )
        app._settings_store.validate()
        repaired_keys = list(getattr(app._settings_store, "last_load_repaired_keys", []) or [])
        if repaired_keys:
            warning = (
                "Some settings values were invalid and were repaired to defaults for this session.\n\n"
                f"Repaired keys: {', '.join(repaired_keys)}\n\n"
                f"Original settings file was left unchanged at:\n{settings_path or '<unknown>'}"
            )
            try:
                app._settings_load_warning_message = warning
            except Exception:
                pass
    except SettingsLoadError as exc:
        logger.error(f"Failed to load settings: {exc}")
        app._settings_store.reset_to_defaults()
        warning = (
            "Settings could not be loaded, so defaults were used for this session.\n\n"
            f"{exc}\n\n"
            f"Original settings file was left unchanged at:\n{settings_path or '<unknown>'}"
        )
        try:
            app._settings_load_warning_message = warning
        except Exception:
            pass
    except Exception as exc:
        logger.error(f"Unexpected error loading settings: {exc}")
        app._settings_store.reset_to_defaults()
        warning = (
            "Settings could not be loaded, so defaults were used for this session.\n\n"
            f"{exc}\n\n"
            f"Original settings file was left unchanged at:\n{settings_path or '<unknown>'}"
        )
        try:
            app._settings_load_warning_message = warning
        except Exception:
            pass
    return cast(dict, app._settings_store.data)


def _settings_save_blocked_until_restart(app) -> bool:
    try:
        return bool(getattr(app, "_settings_save_blocked_until_restart", False))
    except Exception:
        return False


def _notify_settings_save_blocked(app) -> None:
    message = str(
        getattr(
            app,
            "_settings_save_block_message",
            (
                "Restart required before saving more settings changes. "
                "Imported settings on disk are being preserved until restart."
            ),
        )
        or ""
    ).strip()
    if not message:
        message = (
            "Restart required before saving more settings changes. "
            "Imported settings on disk are being preserved until restart."
        )
    try:
        status = getattr(app, "status", None)
        if status is not None and hasattr(status, "config"):
            status.config(text="Settings restart required before saving changes")
    except Exception as exc:
        _log_suppressed("Failed updating status for deferred settings save", exc)
    if bool(getattr(app, "_settings_save_block_log_emitted", False)):
        return
    try:
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            ui_q.put(("log", f"[settings] {message}"))
    except Exception as exc:
        _log_suppressed("Failed logging deferred settings save", exc)
    try:
        app._settings_save_block_log_emitted = True
    except Exception:
        pass


def _safe_float(app, var, default, label: str) -> float:
    try:
        return float(var.get())
    except Exception:
        try:
            fallback = float(default)
        except Exception:
            fallback = 0.0
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
        return fallback


def _safe_int(app, var, default, label: str) -> int:
    try:
        return int(var.get())
    except Exception:
        try:
            fallback = int(default)
        except Exception:
            fallback = 0
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
        return fallback


def _read_nonnegative_int_setting_value(
    app,
    *,
    attr_name: str,
    key: str,
    default: int,
    label: str,
) -> int:
    value = _read_setting_value(app, attr_name=attr_name, key=key, fallback=default)
    try:
        if isinstance(value, bool):
            parsed = int(value)
        elif isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value.strip())
        else:
            raise TypeError("unsupported int setting value")
    except Exception:
        parsed = int(default)
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {parsed}."))
    return int(max(0, parsed))


def _read_nonnegative_float_setting(
    app,
    *,
    attr_name: str,
    key: str,
    default: float,
    label: str,
) -> float:
    var = getattr(app, attr_name, None)
    fallback = app.settings.get(key, DEFAULT_SETTINGS.get(key, default))
    if var is None:
        value = fallback
    else:
        value = _safe_float(app, var, fallback, label)
    return max(0.0, float(value))


def _read_nonnegative_float_or_default_setting(
    app,
    *,
    attr_name: str,
    key: str,
    default: float,
    label: str,
) -> float:
    var = getattr(app, attr_name, None)
    fallback = app.settings.get(key, DEFAULT_SETTINGS.get(key, default))
    if var is None:
        value = fallback
    else:
        value = _safe_float(app, var, fallback, label)
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if parsed < 0.0:
        return float(default)
    return parsed


def _read_positive_float_setting(
    app,
    *,
    attr_name: str,
    key: str,
    default: float,
    label: str,
) -> float:
    var = getattr(app, attr_name, None)
    fallback = app.settings.get(key, DEFAULT_SETTINGS.get(key, default))
    if var is None:
        value = fallback
    else:
        value = _safe_float(app, var, fallback, label)
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if parsed <= 0.0:
        return float(default)
    return parsed


def _read_choice_setting(
    app,
    *,
    attr_name: str,
    key: str,
    default: str,
    allowed: tuple[str, ...],
) -> str:
    fallback = str(app.settings.get(key, DEFAULT_SETTINGS.get(key, default)) or "").strip().lower()
    if fallback not in allowed:
        fallback = str(default).strip().lower()
    value = fallback
    var = getattr(app, attr_name, None)
    if var is not None:
        try:
            value = str(var.get() or "").strip().lower()
        except Exception:
            value = fallback
    if value not in allowed:
        value = fallback
    if value not in allowed and allowed:
        value = str(allowed[0])
    return str(value)


def _build_motion_and_connection_settings(app, last_port: str) -> dict[str, object]:
    jog_dro_smoothing_mode = _read_choice_setting(
        app,
        attr_name="jog_dro_smoothing_mode",
        key="jog_dro_smoothing_mode",
        default="off",
        allowed=("off", "ui_jog_only", "all_jog"),
    )
    return {
        "last_port": str(last_port or ""),
        "unit_mode": app.unit_mode.get(),
        "step_xy": _safe_float(
            app,
            app.step_xy,
            app.settings.get("step_xy", DEFAULT_SETTINGS.get("step_xy", 1.0)),
            "step XY",
        ),
        "step_z": _safe_float(
            app,
            app.step_z,
            app.settings.get("step_z", DEFAULT_SETTINGS.get("step_z", 1.0)),
            "step Z",
        ),
        "jog_feed_xy": _safe_float(
            app,
            app.jog_feed_xy,
            app.settings.get(
                "jog_feed_xy", DEFAULT_SETTINGS.get("jog_feed_xy", 4000.0)
            ),
            "jog feed XY",
        ),
        "jog_feed_z": _safe_float(
            app,
            app.jog_feed_z,
            app.settings.get("jog_feed_z", DEFAULT_SETTINGS.get("jog_feed_z", 500.0)),
            "jog feed Z",
        ),
        "jog_dro_smoothing_mode": jog_dro_smoothing_mode,
        "last_gcode_dir": app.settings.get("last_gcode_dir", ""),
        "window_geometry": app.geometry(),
        "status_poll_interval": _safe_float(
            app,
            app.status_poll_interval,
            app.settings.get(
                "status_poll_interval",
                DEFAULT_SETTINGS.get("status_poll_interval", STATUS_POLL_DEFAULT),
            ),
            "status interval",
        ),
        "status_query_failure_limit": _safe_int(
            app,
            app.status_query_failure_limit,
            app.settings.get(
                "status_query_failure_limit",
                DEFAULT_SETTINGS.get("status_query_failure_limit", 3),
            ),
            "status failure limit",
        ),
        "homing_watchdog_enabled": bool(app.homing_watchdog_enabled.get()),
        "homing_watchdog_timeout": _safe_float(
            app,
            app.homing_watchdog_timeout,
            app.settings.get(
                "homing_watchdog_timeout",
                DEFAULT_SETTINGS.get("homing_watchdog_timeout", 60.0),
            ),
            "homing watchdog timeout",
        ),
        "all_stop_mode": app.all_stop_mode.get(),
        "training_wheels": bool(app.training_wheels.get()),
        "stop_joystick_hold_on_focus_loss": bool(app.stop_hold_on_focus_loss.get()),
        "streaming_line_threshold": _safe_int(
            app,
            app.streaming_line_threshold,
            app.settings.get(
                "streaming_line_threshold",
                DEFAULT_SETTINGS.get("streaming_line_threshold", 0),
            ),
            "streaming line threshold",
        ),
        "ultra_large_size_threshold_mb": _safe_int(
            app,
            app.ultra_large_size_threshold_mb,
            app.settings.get(
                "ultra_large_size_threshold_mb",
                DEFAULT_SETTINGS.get("ultra_large_size_threshold_mb", 0),
            ),
            "ultra-large threshold (MB)",
        ),
        "reconnect_on_open": bool(app.reconnect_on_open.get()),
        "fullscreen_on_startup": bool(app.fullscreen_on_startup.get()),
    }


def _prune_unknown_keys(data: dict[str, object]) -> dict[str, object]:
    known_keys = set(DEFAULT_SETTINGS.keys())
    return {key: value for key, value in data.items() if key in known_keys}


def _setting_default(key: str, fallback: object) -> object:
    return DEFAULT_SETTINGS.get(key, fallback)


def _read_setting_value(app, *, attr_name: str, key: str, fallback: object) -> object:
    if hasattr(app, attr_name):
        var = getattr(app, attr_name)
        getter = getattr(var, "get", None)
        if callable(getter):
            try:
                return getter()
            except Exception as exc:
                _log_suppressed(f"Failed reading settings variable {attr_name}", exc)
    return app.settings.get(key, _setting_default(key, fallback))


def _read_bool_setting_value(app, *, attr_name: str, key: str, fallback: bool) -> bool:
    return bool(_read_setting_value(app, attr_name=attr_name, key=key, fallback=fallback))


def _read_string_setting_value(
    app,
    *,
    attr_name: str,
    key: str,
    fallback: str,
    strip: bool = False,
    lower: bool = False,
) -> str:
    value = str(_read_setting_value(app, attr_name=attr_name, key=key, fallback=fallback) or "")
    if strip:
        value = value.strip()
    if lower:
        value = value.lower()
    return value


def _build_ui_settings(
    app,
    *,
    tooltip_timeout_value: float,
    grbl_popup_dedupe_value: float,
    pos_status_enabled: bool,
) -> dict[str, object]:
    linux_file_dialog_default_path = _read_string_setting_value(
        app,
        attr_name="linux_file_dialog_default_path",
        key="linux_file_dialog_default_path",
        fallback="/root/CNC_Jobs",
        strip=True,
    )
    if sys.platform.startswith("linux") and not linux_file_dialog_default_path:
        linux_file_dialog_default_path = str(
            DEFAULT_SETTINGS.get("linux_file_dialog_default_path", "/root/CNC_Jobs")
            or "/root/CNC_Jobs"
        ).strip() or "/root/CNC_Jobs"
    return {
        "tooltips_enabled": bool(app.tooltip_enabled.get()),
        "tooltip_timeout_sec": tooltip_timeout_value,
        "numeric_keypad_enabled": _read_bool_setting_value(
            app,
            attr_name="numeric_keypad_enabled",
            key="numeric_keypad_enabled",
            fallback=True,
        ),
        "developer_options_enabled": _read_bool_setting_value(
            app,
            attr_name="developer_options_enabled",
            key="developer_options_enabled",
            fallback=False,
        ),
        "gui_logging_enabled": bool(app.gui_logging_enabled.get()),
        "pi_profile_enabled": _read_bool_setting_value(
            app,
            attr_name="pi_profile_enabled",
            key="pi_profile_enabled",
            fallback=False,
        ),
        "pi_profile_prompt_shown": bool(
            app.settings.get(
                "pi_profile_prompt_shown",
                DEFAULT_SETTINGS.get("pi_profile_prompt_shown", False),
            )
        ),
        "error_dialogs_enabled": bool(app.error_dialogs_enabled.get()),
        "grbl_popup_enabled": _read_bool_setting_value(
            app,
            attr_name="grbl_popup_enabled",
            key="grbl_popup_enabled",
            fallback=True,
        ),
        "grbl_popup_dedupe_sec": grbl_popup_dedupe_value,
        "performance_mode": bool(app.performance_mode.get()),
        "performance_profile_enabled": _read_bool_setting_value(
            app,
            attr_name="performance_profile_enabled",
            key="performance_profile_enabled",
            fallback=False,
        ),
        "performance_leak_watch_enabled": _read_bool_setting_value(
            app,
            attr_name="performance_leak_watch_enabled",
            key="performance_leak_watch_enabled",
            fallback=False,
        ),
        "performance_profile_log_path": _read_string_setting_value(
            app,
            attr_name="performance_profile_log_path",
            key="performance_profile_log_path",
            fallback="",
            strip=True,
        ),
        "theme": app.selected_theme.get(),
        "ui_scale": (
            _safe_float(
                app,
                app.ui_scale,
                app.settings.get("ui_scale", _setting_default("ui_scale", 1.0)),
                "ui scale",
            )
            if hasattr(app, "ui_scale")
            else app.settings.get("ui_scale", _setting_default("ui_scale", 1.0))
        ),
        "linux_file_dialog_scale": (
            _safe_float(
                app,
                app.linux_file_dialog_scale,
                app.settings.get(
                    "linux_file_dialog_scale",
                    _setting_default("linux_file_dialog_scale", 1.0),
                ),
                "Linux file dialog scale",
            )
            if hasattr(app, "linux_file_dialog_scale")
            else app.settings.get(
                "linux_file_dialog_scale",
                _setting_default("linux_file_dialog_scale", 1.0),
            )
        ),
        "linux_file_dialog_default_path": linux_file_dialog_default_path,
        "scrollbar_width": _read_string_setting_value(
            app,
            attr_name="scrollbar_width",
            key="scrollbar_width",
            fallback="wide",
            strip=True,
            lower=True,
        ),
        "touch_scroll_mode": _read_string_setting_value(
            app,
            attr_name="touch_scroll_mode",
            key="touch_scroll_mode",
            fallback="thumb_and_swipe",
            strip=True,
            lower=True,
        ),
        "console_positions_enabled": pos_status_enabled,
        "show_resume_from_button": bool(app.show_resume_from_button.get()),
        "show_recover_button": bool(app.show_recover_button.get()),
        "show_endstop_indicator": bool(app.show_endstop_indicator.get()),
        "show_probe_indicator": bool(app.show_probe_indicator.get()),
        "show_hold_indicator": bool(app.show_hold_indicator.get()),
        "show_logs_button": _read_bool_setting_value(
            app,
            attr_name="show_logs_button",
            key="show_logs_button",
            fallback=False,
        ),
        "show_raw_grbl_button": _read_bool_setting_value(
            app,
            attr_name="show_raw_grbl_button",
            key="show_raw_grbl_button",
            fallback=False,
        ),
        "show_checklists_button": _read_bool_setting_value(
            app,
            attr_name="show_checklists_button",
            key="show_checklists_button",
            fallback=True,
        ),
        "show_quick_tips_button": bool(app.show_quick_tips_button.get()),
        "show_quick_keys_button": bool(app.show_quick_keys_button.get()),
        "show_quick_alo_button": bool(app.show_quick_alo_button.get()),
        "show_quick_vac_button": bool(app.show_quick_vac_button.get()),
        "show_quick_light_button": bool(app.show_quick_light_button.get()),
        "show_quick_release_button": bool(app.show_quick_release_button.get()),
        "error_dialog_interval": app._error_dialog_interval,
        "error_dialog_burst_window": app._error_dialog_burst_window,
        "error_dialog_burst_limit": app._error_dialog_burst_limit,
        "job_completion_popup": bool(app.job_completion_popup.get()),
        "job_completion_beep": bool(app.job_completion_beep.get()),
        "spindle_control_rpm": _read_nonnegative_int_setting_value(
            app,
            attr_name="spindle_rpm_var",
            key="spindle_control_rpm",
            default=int(DEFAULT_SPINDLE_RPM),
            label="spindle control RPM",
        ),
    }


def _build_estimation_and_bindings_settings(app) -> dict[str, object]:
    hold_miss_var = getattr(app, "joystick_hold_miss_limit", None)
    hold_miss_fallback = app.settings.get(
        "joystick_hold_miss_limit",
        DEFAULT_SETTINGS.get("joystick_hold_miss_limit", 2),
    )
    if hold_miss_var is None:
        hold_miss_limit = int(hold_miss_fallback)
    else:
        hold_miss_limit = _safe_int(
            app,
            hold_miss_var,
            hold_miss_fallback,
            "joystick hold release sensitivity",
        )
    if hold_miss_limit < 1:
        hold_miss_limit = 1
    if hold_miss_limit > 8:
        hold_miss_limit = 8
    return {
        "fallback_rapid_rate": app.fallback_rapid_rate.get().strip(),
        "estimate_factor": _safe_float(
            app,
            app.estimate_factor,
            app.settings.get(
                "estimate_factor", DEFAULT_SETTINGS.get("estimate_factor", 1.0)
            ),
            "estimate factor",
        ),
        "estimate_rate_x": app.estimate_rate_x_var.get().strip(),
        "estimate_rate_y": app.estimate_rate_y_var.get().strip(),
        "estimate_rate_z": app.estimate_rate_z_var.get().strip(),
        "keyboard_bindings_enabled": bool(app.keyboard_bindings_enabled.get()),
        "joystick_bindings_enabled": bool(app.joystick_bindings_enabled.get()),
        "joystick_safety_enabled": bool(app.joystick_safety_enabled.get()),
        "joystick_hold_miss_limit": int(hold_miss_limit),
        "dry_run_sanitize_stream": bool(app.dry_run_sanitize_stream.get()),
        "joystick_bindings": dict(app._joystick_bindings),
        "joystick_safety_binding": (
            dict(app._joystick_safety_binding) if app._joystick_safety_binding else None
        ),
        "current_line_mode": app.current_line_mode.get(),
        "key_bindings": dict(app._key_bindings),
    }


def _build_macro_and_autolevel_settings(
    app,
    *,
    macro_line_timeout_value: float,
    macro_total_timeout_value: float,
    macro_probe_z_value: float,
    macro_probe_margin_value: float,
    xyz_plate_thickness_value: float,
    xyz_plate_min_safe_probe_distance_value: float,
    xyz_plate_x_offset_value: float,
    xyz_plate_y_offset_value: float,
    xyz_plate_side_clearance_distance_value: float,
    xyz_plate_z_rough_probe_speed_value: float,
    xyz_plate_z_reprobe_speed_value: float,
    xyz_plate_z_fine_probe_speed_value: float,
    xyz_plate_xy_rough_probe_speed_value: float,
    xyz_plate_xy_fine_probe_speed_value: float,
    xyz_plate_probe_dwell_value: float,
    bit_setter_x_value: float,
    bit_setter_y_value: float,
    bit_setter_rough_probe_speed_value: float,
    bit_setter_fine_probe_speed_value: float,
    bit_setter_probe_dwell_value: float,
) -> dict[str, object]:
    disable_macro_timeouts_var = getattr(app, "disable_macro_timeouts", None)
    if disable_macro_timeouts_var is not None:
        disable_macro_timeouts = bool(disable_macro_timeouts_var.get())
    else:
        disable_macro_timeouts = bool(
            getattr(app, "settings", {}).get(
                "disable_macro_timeouts",
                DEFAULT_SETTINGS.get("disable_macro_timeouts", False),
            )
        )
    return {
        "auto_level_enabled": bool(app.auto_level_enabled.get()),
        "show_autolevel_overlay": bool(app.show_autolevel_overlay.get()),
        "macros_allow_python": bool(app.macros_allow_python.get()),
        "disable_macro_timeouts": disable_macro_timeouts,
        "macro_line_timeout_sec": macro_line_timeout_value,
        "macro_total_timeout_sec": macro_total_timeout_value,
        "macro_probe_z_location": macro_probe_z_value,
        "macro_probe_safety_margin": macro_probe_margin_value,
        "xyz_plate_thickness": xyz_plate_thickness_value,
        "xyz_plate_min_safe_probe_distance": xyz_plate_min_safe_probe_distance_value,
        "xyz_plate_x_offset": xyz_plate_x_offset_value,
        "xyz_plate_y_offset": xyz_plate_y_offset_value,
        "xyz_plate_side_clearance_distance": xyz_plate_side_clearance_distance_value,
        "xyz_plate_z_rough_probe_speed": xyz_plate_z_rough_probe_speed_value,
        "xyz_plate_z_reprobe_speed": xyz_plate_z_reprobe_speed_value,
        "xyz_plate_z_fine_probe_speed": xyz_plate_z_fine_probe_speed_value,
        "xyz_plate_xy_rough_probe_speed": xyz_plate_xy_rough_probe_speed_value,
        "xyz_plate_xy_fine_probe_speed": xyz_plate_xy_fine_probe_speed_value,
        "xyz_plate_probe_dwell": xyz_plate_probe_dwell_value,
        "bit_setter_x": bit_setter_x_value,
        "bit_setter_y": bit_setter_y_value,
        "bit_setter_rough_probe_speed": bit_setter_rough_probe_speed_value,
        "bit_setter_fine_probe_speed": bit_setter_fine_probe_speed_value,
        "bit_setter_probe_dwell": bit_setter_probe_dwell_value,
        "zeroing_persistent": bool(app.zeroing_persistent.get()),
        "auto_level_settings": dict(getattr(app, "auto_level_settings", {})),
        "auto_level_job_prefs": dict(getattr(app, "auto_level_job_prefs", {})),
        "auto_level_presets": dict(getattr(app, "auto_level_presets", {})),
    }


def _read_outlet_setting(
    app, *, attr_name: str, key: str, default: int, label: str
) -> int:
    var = getattr(app, attr_name, None)
    fallback = app.settings.get(key, DEFAULT_SETTINGS.get(key, default))
    if var is None:
        value = fallback
    else:
        value = _safe_int(app, var, fallback, label)
    try:
        outlet = int(value)
    except Exception:
        outlet = int(default)
    if outlet not in (1, 2):
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {default}."))
        outlet = int(default)
    return outlet


def _coerce_outlet_setting(value: object, *, default: int) -> int:
    raw_value: str | bytes | bytearray | int | float
    if isinstance(value, (str, bytes, bytearray, int, float)):
        raw_value = value
    else:
        return int(default)
    try:
        outlet = int(raw_value)
    except Exception:
        return int(default)
    if outlet not in (1, 2):
        return int(default)
    return outlet


def _coerce_nonnegative_float_setting(value: object, *, default: float) -> float:
    raw_value: str | bytes | bytearray | int | float
    if isinstance(value, (str, bytes, bytearray, int, float)):
        raw_value = value
    else:
        return float(default)
    try:
        parsed = float(raw_value)
    except Exception:
        return float(default)
    return max(0.0, float(parsed))


def _build_kasa_settings(app) -> dict[str, object]:
    if not sys.platform.startswith("linux"):
        existing = dict(DEFAULT_SETTINGS)
        if isinstance(getattr(app, "settings", None), dict):
            existing.update(app.settings)
        return {
            "kasa_enabled": bool(existing.get("kasa_enabled", DEFAULT_SETTINGS["kasa_enabled"])),
            "kasa_device_identifier": str(
                existing.get(
                    "kasa_device_identifier",
                    DEFAULT_SETTINGS["kasa_device_identifier"],
                )
                or ""
            ).strip(),
            "vacuum_enabled": bool(
                existing.get("vacuum_enabled", DEFAULT_SETTINGS["vacuum_enabled"])
            ),
            "vacuum_off_delay_sec": max(
                0.0,
                _coerce_nonnegative_float_setting(
                    existing.get(
                        "vacuum_off_delay_sec",
                        DEFAULT_SETTINGS["vacuum_off_delay_sec"],
                    ),
                    default=float(DEFAULT_SETTINGS["vacuum_off_delay_sec"]),
                ),
            ),
            "vacuum_outlet": _coerce_outlet_setting(
                existing.get("vacuum_outlet", DEFAULT_SETTINGS["vacuum_outlet"]),
                default=1,
            ),
            "light_enabled": bool(
                existing.get("light_enabled", DEFAULT_SETTINGS["light_enabled"])
            ),
            "light_outlet": _coerce_outlet_setting(
                existing.get("light_outlet", DEFAULT_SETTINGS["light_outlet"]),
                default=2,
            ),
        }

    kasa_enabled_var = getattr(app, "kasa_enabled", None)
    kasa_enabled = (
        bool(kasa_enabled_var.get()) if kasa_enabled_var is not None else False
    )
    device_identifier_var = getattr(app, "kasa_device_identifier", None)
    device_identifier = (
        str(device_identifier_var.get() or "").strip()
        if device_identifier_var is not None
        else str(app.settings.get("kasa_device_identifier", "") or "").strip()
    )
    vacuum_enabled_var = getattr(app, "vacuum_enabled", None)
    vacuum_enabled = (
        bool(vacuum_enabled_var.get()) if vacuum_enabled_var is not None else False
    )
    vacuum_off_delay_sec = _read_nonnegative_float_setting(
        app,
        attr_name="vacuum_off_delay_sec",
        key="vacuum_off_delay_sec",
        default=0.0,
        label="Vacuum off delay",
    )
    light_enabled_var = getattr(app, "light_enabled", None)
    light_enabled = (
        bool(light_enabled_var.get()) if light_enabled_var is not None else False
    )
    vacuum_outlet = _read_outlet_setting(
        app,
        attr_name="vacuum_outlet",
        key="vacuum_outlet",
        default=1,
        label="Vacuum outlet",
    )
    light_outlet = _read_outlet_setting(
        app,
        attr_name="light_outlet",
        key="light_outlet",
        default=2,
        label="Spindle Light outlet",
    )
    valid, message = validate_outlet_mapping(
        vacuum_enabled=vacuum_enabled,
        vacuum_outlet=vacuum_outlet,
        light_enabled=light_enabled,
        light_outlet=light_outlet,
    )
    if not valid:
        light_outlet = 1 if vacuum_outlet == 2 else 2
        app.ui_q.put(
            (
                "log",
                f"[settings] {message} Auto-adjusted Spindle Light to Outlet {light_outlet}.",
            )
        )
    return {
        "kasa_enabled": kasa_enabled,
        "kasa_device_identifier": device_identifier,
        "vacuum_enabled": vacuum_enabled,
        "vacuum_off_delay_sec": float(vacuum_off_delay_sec),
        "vacuum_outlet": vacuum_outlet,
        "light_enabled": light_enabled,
        "light_outlet": light_outlet,
    }


def save_settings(app):
    if _settings_save_blocked_until_restart(app):
        _notify_settings_save_blocked(app)
        return
    app._apply_error_dialog_settings()
    app._on_status_failure_limit_change()
    app._on_homing_watchdog_change()

    try:
        settings_dir = os.path.dirname(str(getattr(app, "settings_path", "") or ""))
        if settings_dir:
            os.makedirs(settings_dir, exist_ok=True)
    except Exception as exc:
        logger.exception("Failed to create settings directory: %s", exc)

    pos_status_enabled = bool(app.console_positions_enabled.get())
    tooltip_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="tooltip_timeout_sec",
        key="tooltip_timeout_sec",
        default=10.0,
        label="tooltip timeout",
    )
    macro_line_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_line_timeout_sec",
        key="macro_line_timeout_sec",
        default=0.0,
        label="macro line timeout",
    )
    macro_total_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_total_timeout_sec",
        key="macro_total_timeout_sec",
        default=0.0,
        label="macro total timeout",
    )
    macro_probe_z_value = (
        _safe_float(
            app,
            getattr(app, "macro_probe_z_location", None),
            app.settings.get(
                "macro_probe_z_location",
                DEFAULT_SETTINGS.get("macro_probe_z_location", -5.0),
            ),
            "macro probe Z start",
        )
        if getattr(app, "macro_probe_z_location", None) is not None
        else float(
            app.settings.get(
                "macro_probe_z_location",
                DEFAULT_SETTINGS.get("macro_probe_z_location", -5.0),
            )
        )
    )
    macro_probe_margin_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_probe_safety_margin",
        key="macro_probe_safety_margin",
        default=3.0,
        label="macro probe safety margin",
    )
    xyz_plate_thickness_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_thickness",
        key="xyz_plate_thickness",
        default=tool_measurement.DEFAULT_XYZ_PLATE_THICKNESS_MM,
        label="XYZ plate thickness",
    )
    xyz_plate_min_safe_probe_distance_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_min_safe_probe_distance",
        key="xyz_plate_min_safe_probe_distance",
        default=tool_measurement.DEFAULT_XYZ_PLATE_MIN_SAFE_PROBE_DISTANCE_MM,
        label="XYZ plate min safe probe distance",
    )
    xyz_plate_x_offset_value = (
        _safe_float(
            app,
            getattr(app, "xyz_plate_x_offset", None),
            app.settings.get(
                "xyz_plate_x_offset",
                DEFAULT_SETTINGS.get(
                    "xyz_plate_x_offset",
                    tool_measurement.DEFAULT_XYZ_PLATE_X_OFFSET_MM,
                ),
            ),
            "XYZ plate X offset",
        )
        if getattr(app, "xyz_plate_x_offset", None) is not None
        else float(
            app.settings.get(
                "xyz_plate_x_offset",
                DEFAULT_SETTINGS.get(
                    "xyz_plate_x_offset",
                    tool_measurement.DEFAULT_XYZ_PLATE_X_OFFSET_MM,
                ),
            )
        )
    )
    xyz_plate_y_offset_value = (
        _safe_float(
            app,
            getattr(app, "xyz_plate_y_offset", None),
            app.settings.get(
                "xyz_plate_y_offset",
                DEFAULT_SETTINGS.get(
                    "xyz_plate_y_offset",
                    tool_measurement.DEFAULT_XYZ_PLATE_Y_OFFSET_MM,
                ),
            ),
            "XYZ plate Y offset",
        )
        if getattr(app, "xyz_plate_y_offset", None) is not None
        else float(
            app.settings.get(
                "xyz_plate_y_offset",
                DEFAULT_SETTINGS.get(
                    "xyz_plate_y_offset",
                    tool_measurement.DEFAULT_XYZ_PLATE_Y_OFFSET_MM,
                ),
            )
        )
    )
    xyz_plate_side_clearance_distance_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_side_clearance_distance",
        key="xyz_plate_side_clearance_distance",
        default=tool_measurement.DEFAULT_XYZ_PLATE_SIDE_CLEARANCE_DISTANCE_MM,
        label="XYZ plate side clearance distance",
    )
    xyz_plate_z_rough_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_z_rough_probe_speed",
        key="xyz_plate_z_rough_probe_speed",
        default=tool_measurement.DEFAULT_XYZ_PLATE_Z_ROUGH_PROBE_FEED_MM_MIN,
        label="XYZ plate Z rough probe speed",
    )
    xyz_plate_z_reprobe_speed_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_z_reprobe_speed",
        key="xyz_plate_z_reprobe_speed",
        default=tool_measurement.DEFAULT_XYZ_PLATE_Z_REPROBE_FEED_MM_MIN,
        label="XYZ plate Z re-probe speed",
    )
    xyz_plate_z_fine_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_z_fine_probe_speed",
        key="xyz_plate_z_fine_probe_speed",
        default=tool_measurement.DEFAULT_XYZ_PLATE_Z_FINE_PROBE_FEED_MM_MIN,
        label="XYZ plate Z fine probe speed",
    )
    xyz_plate_xy_rough_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_xy_rough_probe_speed",
        key="xyz_plate_xy_rough_probe_speed",
        default=tool_measurement.DEFAULT_XYZ_PLATE_XY_ROUGH_PROBE_FEED_MM_MIN,
        label="XYZ plate XY rough probe speed",
    )
    xyz_plate_xy_fine_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="xyz_plate_xy_fine_probe_speed",
        key="xyz_plate_xy_fine_probe_speed",
        default=tool_measurement.DEFAULT_XYZ_PLATE_XY_FINE_PROBE_FEED_MM_MIN,
        label="XYZ plate XY fine probe speed",
    )
    xyz_plate_probe_dwell_value = _read_nonnegative_float_or_default_setting(
        app,
        attr_name="xyz_plate_probe_dwell",
        key="xyz_plate_probe_dwell",
        default=tool_measurement.DEFAULT_XYZ_PLATE_PROBE_DWELL_S,
        label="XYZ plate probe dwell",
    )
    bit_setter_x_value = (
        _safe_float(
            app,
            getattr(app, "bit_setter_x", None),
            app.settings.get(
                "bit_setter_x",
                DEFAULT_SETTINGS.get(
                    "bit_setter_x", tool_measurement.DEFAULT_BIT_SETTER_X_MM
                ),
            ),
            "bit setter X",
        )
        if getattr(app, "bit_setter_x", None) is not None
        else float(
            app.settings.get(
                "bit_setter_x",
                DEFAULT_SETTINGS.get(
                    "bit_setter_x", tool_measurement.DEFAULT_BIT_SETTER_X_MM
                ),
            )
        )
    )
    bit_setter_y_value = (
        _safe_float(
            app,
            getattr(app, "bit_setter_y", None),
            app.settings.get(
                "bit_setter_y",
                DEFAULT_SETTINGS.get(
                    "bit_setter_y", tool_measurement.DEFAULT_BIT_SETTER_Y_MM
                ),
            ),
            "bit setter Y",
        )
        if getattr(app, "bit_setter_y", None) is not None
        else float(
            app.settings.get(
                "bit_setter_y",
                DEFAULT_SETTINGS.get(
                    "bit_setter_y", tool_measurement.DEFAULT_BIT_SETTER_Y_MM
                ),
            )
        )
    )
    bit_setter_rough_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="bit_setter_rough_probe_speed",
        key="bit_setter_rough_probe_speed",
        default=tool_measurement.DEFAULT_TOOL_PROBE_ROUGH_FEED_MM_MIN,
        label="bit setter rough probe speed",
    )
    bit_setter_fine_probe_speed_value = _read_positive_float_setting(
        app,
        attr_name="bit_setter_fine_probe_speed",
        key="bit_setter_fine_probe_speed",
        default=tool_measurement.DEFAULT_TOOL_PROBE_FINE_FEED_MM_MIN,
        label="bit setter fine probe speed",
    )
    bit_setter_probe_dwell_value = _read_nonnegative_float_or_default_setting(
        app,
        attr_name="bit_setter_probe_dwell",
        key="bit_setter_probe_dwell",
        default=tool_measurement.DEFAULT_TOOL_PROBE_DWELL_S,
        label="bit setter probe dwell",
    )
    grbl_popup_dedupe_value = _read_nonnegative_float_setting(
        app,
        attr_name="grbl_popup_dedupe_sec",
        key="grbl_popup_dedupe_sec",
        default=3.0,
        label="GRBL popup dedupe",
    )

    previous_settings = getattr(app, "settings", None)
    previous_store_data = getattr(getattr(app, "_settings_store", None), "data", None)
    data = dict(app.settings) if isinstance(app.settings, dict) else {}
    last_port = ""
    try:
        last_port = getattr(app, "_auto_reconnect_last_port", "") or ""
    except Exception:
        last_port = ""
    if not last_port:
        try:
            last_port = app.current_port.get()
        except Exception:
            last_port = ""
    data.update(_build_motion_and_connection_settings(app, str(last_port or "")))
    data.update(
        _build_ui_settings(
            app,
            tooltip_timeout_value=tooltip_timeout_value,
            grbl_popup_dedupe_value=grbl_popup_dedupe_value,
            pos_status_enabled=pos_status_enabled,
        )
    )
    data.update(_build_estimation_and_bindings_settings(app))
    data.update(
        _build_macro_and_autolevel_settings(
            app,
            macro_line_timeout_value=macro_line_timeout_value,
            macro_total_timeout_value=macro_total_timeout_value,
            macro_probe_z_value=float(macro_probe_z_value),
            macro_probe_margin_value=macro_probe_margin_value,
            xyz_plate_thickness_value=xyz_plate_thickness_value,
            xyz_plate_min_safe_probe_distance_value=xyz_plate_min_safe_probe_distance_value,
            xyz_plate_x_offset_value=float(xyz_plate_x_offset_value),
            xyz_plate_y_offset_value=float(xyz_plate_y_offset_value),
            xyz_plate_side_clearance_distance_value=xyz_plate_side_clearance_distance_value,
            xyz_plate_z_rough_probe_speed_value=xyz_plate_z_rough_probe_speed_value,
            xyz_plate_z_reprobe_speed_value=xyz_plate_z_reprobe_speed_value,
            xyz_plate_z_fine_probe_speed_value=xyz_plate_z_fine_probe_speed_value,
            xyz_plate_xy_rough_probe_speed_value=xyz_plate_xy_rough_probe_speed_value,
            xyz_plate_xy_fine_probe_speed_value=xyz_plate_xy_fine_probe_speed_value,
            xyz_plate_probe_dwell_value=xyz_plate_probe_dwell_value,
            bit_setter_x_value=float(bit_setter_x_value),
            bit_setter_y_value=float(bit_setter_y_value),
            bit_setter_rough_probe_speed_value=bit_setter_rough_probe_speed_value,
            bit_setter_fine_probe_speed_value=bit_setter_fine_probe_speed_value,
            bit_setter_probe_dwell_value=bit_setter_probe_dwell_value,
        )
    )
    data.update(_build_kasa_settings(app))
    data = _prune_unknown_keys(data)
    app.settings = data
    app._settings_store.data = app.settings
    try:
        app._settings_store.save()
        settings_path = str(
            getattr(app, "settings_path", "")
            or getattr(getattr(app, "_settings_store", None), "filepath", "")
        )
        file_mtime = None
        if settings_path:
            try:
                file_mtime = os.path.getmtime(settings_path)
            except Exception as exc:
                _log_suppressed("Failed reading settings mtime after save", exc)
        logger.info(
            "Settings save complete: path=%s keys=%s mtime=%s",
            settings_path or "<unknown>",
            len(data),
            file_mtime if file_mtime is not None else "n/a",
        )
    except SettingsSaveError as exc:
        app.settings = previous_settings if isinstance(previous_settings, dict) else {}
        app._settings_store.data = (
            previous_store_data
            if isinstance(previous_store_data, dict)
            else app.settings
        )
        try:
            app.ui_q.put(("log", f"[settings] Save failed: {exc}"))
            app.status.config(text="Settings save failed")
        except Exception as log_exc:
            _log_suppressed(
                "Failed reporting SettingsSaveError to UI queue/status", log_exc
            )
        raise
    except Exception as exc:
        app.settings = previous_settings if isinstance(previous_settings, dict) else {}
        app._settings_store.data = (
            previous_store_data
            if isinstance(previous_store_data, dict)
            else app.settings
        )
        wrapped_exc = SettingsSaveError(str(exc) or "Unexpected settings save error")
        try:
            app.ui_q.put(("log", f"[settings] Save failed: {wrapped_exc}"))
            app.status.config(text="Settings save failed")
        except Exception as log_exc:
            _log_suppressed(
                "Failed reporting unexpected settings-save error to UI queue/status",
                log_exc,
            )
        raise wrapped_exc from exc
