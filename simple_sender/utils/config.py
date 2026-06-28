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

"""Application settings management.

This module handles loading, saving, and managing application settings
with atomic file operations and automatic backup.
"""

import copy
import json
import os
import sys
import shutil
import logging
import tempfile
from typing import Dict, Any, Optional
from pathlib import Path

from .constants import (
    GCODE_STREAMING_LINE_THRESHOLD,
    GCODE_ULTRA_LARGE_SIZE_THRESHOLD,
    JOG_DRO_SMOOTHING_CHOICES,
    JOG_DRO_SMOOTHING_OFF,
    JOYSTICK_HOLD_MISS_LIMIT,
    SETTINGS_FILENAME,
    SETTINGS_BACKUP_SUFFIX,
    SETTINGS_TEMP_SUFFIX,
    WATCHDOG_HOMING_TIMEOUT,
)
from simple_sender import tool_measurement
from .exceptions import (
    SettingsLoadError,
    SettingsSaveError,
    SettingsValidationError,
)

logger = logging.getLogger(__name__)
_VALID_BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400)
_VALID_UNIT_MODES = ("mm", "inch")
_VALID_TOUCH_SCROLL_MODES = ("thumb_only", "thumb_and_swipe")
_THEME_SETTING_KEY = "theme"
_APP_DATA_DIR_NAME = "simple-sender-data"
_HIDDEN_FALLBACK_DIR_NAME = ".simple-sender-data"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "active_profile": "",
    "all_stop_mode": "stop_reset",
    "baud_rate": 115200,
    "console_positions_enabled": False,
    "dry_run_sanitize_stream": False,
    "developer_options_enabled": False,
    "error_dialog_burst_limit": 3,
    "error_dialog_burst_window": 30.0,
    "error_dialog_interval": 2.0,
    "error_dialogs_enabled": True,
    "grbl_popup_enabled": True,
    "grbl_popup_dedupe_sec": 3.0,
    "estimate_factor": 1.0,
    "estimate_rate_x": "",
    "estimate_rate_y": "",
    "estimate_rate_z": "",
    "fallback_rapid_rate": "5000.0",
    "fullscreen_on_startup": True,
    "gui_logging_enabled": True,
    "runtime_logging_mode": "Standard",
    "pi_profile_enabled": False,
    "pi_profile_prompt_shown": False,
    "job_completion_beep": False,
    "job_completion_popup": True,
    "joystick_safety_binding": None,
    "joystick_safety_normal_binding": None,
    "joystick_safety_slow_binding": None,
    "joystick_safety_enabled": False,
    "joystick_bindings_enabled": False,
    "joystick_bindings": {},
    "joystick_hold_miss_limit": int(JOYSTICK_HOLD_MISS_LIMIT),
    "jog_dro_smoothing_mode": JOG_DRO_SMOOTHING_OFF,
    "kasa_device_identifier": "",
    "kasa_enabled": False,
    "jog_feed_xy": 4000.0,
    "jog_feed_z": 500.0,
    "key_bindings": {},
    "keyboard_bindings_enabled": True,
    "last_gcode_dir": "",
    "last_port": "",
    "machine_profiles": [],
    "light_enabled": False,
    "light_outlet": 2,
    "macros_allow_python": False,
    "disable_macro_timeouts": False,
    "macro_line_timeout_sec": 120.0,
    "macro_total_timeout_sec": 900.0,
    "macro_probe_z_location": -5.0,
    "macro_probe_safety_margin": 3.0,
    "xyz_plate_thickness": tool_measurement.DEFAULT_XYZ_PLATE_THICKNESS_MM,
    "xyz_plate_min_safe_probe_distance": tool_measurement.DEFAULT_XYZ_PLATE_MIN_SAFE_PROBE_DISTANCE_MM,
    "xyz_plate_x_offset": tool_measurement.DEFAULT_XYZ_PLATE_X_OFFSET_MM,
    "xyz_plate_y_offset": tool_measurement.DEFAULT_XYZ_PLATE_Y_OFFSET_MM,
    "xyz_plate_side_clearance_distance": tool_measurement.DEFAULT_XYZ_PLATE_SIDE_CLEARANCE_DISTANCE_MM,
    "xyz_plate_z_rough_probe_speed": tool_measurement.DEFAULT_XYZ_PLATE_Z_ROUGH_PROBE_FEED_MM_MIN,
    "xyz_plate_z_reprobe_speed": tool_measurement.DEFAULT_XYZ_PLATE_Z_REPROBE_FEED_MM_MIN,
    "xyz_plate_z_fine_probe_speed": tool_measurement.DEFAULT_XYZ_PLATE_Z_FINE_PROBE_FEED_MM_MIN,
    "xyz_plate_xy_rough_probe_speed": tool_measurement.DEFAULT_XYZ_PLATE_XY_ROUGH_PROBE_FEED_MM_MIN,
    "xyz_plate_xy_fine_probe_speed": tool_measurement.DEFAULT_XYZ_PLATE_XY_FINE_PROBE_FEED_MM_MIN,
    "xyz_plate_probe_dwell": tool_measurement.DEFAULT_XYZ_PLATE_PROBE_DWELL_S,
    "bit_setter_x": tool_measurement.DEFAULT_BIT_SETTER_X_MM,
    "bit_setter_y": tool_measurement.DEFAULT_BIT_SETTER_Y_MM,
    "bit_setter_rough_probe_speed": tool_measurement.DEFAULT_TOOL_PROBE_ROUGH_FEED_MM_MIN,
    "bit_setter_fine_probe_speed": tool_measurement.DEFAULT_TOOL_PROBE_FINE_FEED_MM_MIN,
    "bit_setter_probe_dwell": tool_measurement.DEFAULT_TOOL_PROBE_DWELL_S,
    "max_recent_files": 10,
    "performance_mode": True,
    "performance_profile_enabled": True,
    "performance_profile_log_path": "",
    "performance_leak_watch_enabled": False,
    "recent_files": [],
    "reconnect_on_open": True,
    "startup_auto_connect_delay_s": 5.0,
    "show_recover_button": False,
    "show_resume_from_button": False,
    "show_top_toolbar_text": True,
    "show_endstop_indicator": True,
    "show_probe_indicator": True,
    "show_hold_indicator": True,
    "show_logs_button": False,
    "show_raw_grbl_button": False,
    "show_checklists_button": True,
    "auto_level_enabled": True,
    "show_quick_tips_button": True,
    "show_quick_keys_button": True,
    "show_quick_alo_button": True,
    "show_quick_vac_button": True,
    "show_quick_light_button": True,
    "show_quick_release_button": True,
    "status_poll_interval": 0.2,
    "status_query_failure_limit": 3,
    "homing_watchdog_enabled": True,
    "homing_watchdog_timeout": WATCHDOG_HOMING_TIMEOUT,
    "spindle_control_rpm": 12000,
    "stop_joystick_hold_on_focus_loss": True,
    "step_xy": 400.0,
    "step_z": 1.0,
    "theme": "simple_sender_gemini",
    "ui_scale": 1.5,
    "linux_file_dialog_scale": 1.4,
    "linux_file_dialog_default_path": "/root/CNC_Jobs",
    "scrollbar_width": "wide",
    "touch_scroll_mode": "thumb_and_swipe",
    "tooltips_enabled": True,
    "tooltip_timeout_sec": 10.0,
    "app_settings_view_mode": "basic",
    "app_settings_preload_enabled": False,
    "numeric_keypad_enabled": True,
    "training_wheels": True,
    "unit_mode": "mm",
    "vacuum_enabled": False,
    "kasa_confirm_stream_directives": False,
    "vacuum_off_delay_sec": 0.0,
    "vacuum_outlet": 1,
    "streaming_line_threshold": GCODE_STREAMING_LINE_THRESHOLD,
    "ultra_large_size_threshold_mb": max(
        0, int(GCODE_ULTRA_LARGE_SIZE_THRESHOLD) // (1024 * 1024)
    ),
    "window_geometry": "1194x864+261+83",
    "zeroing_persistent": False,
    "show_autolevel_overlay": True,
    "auto_level_settings": {
        "margin": 5.0,
        "base_spacing": 5.0,
        "min_spacing": 2.0,
        "max_spacing": 12.0,
        "max_points": None,
        "safe_z": 5.0,
        "probe_depth": 3.0,
        "probe_feed": 100.0,
        "retract_z": 2.0,
        "settle_time": 0.0,
        "path_order": "serpentine",
        "interpolation": "bicubic",
        "avoidance_areas": [
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""},
        ],
    },
    "auto_level_job_prefs": {
        "small_max_area": 2500.0,
        "large_min_area": 10000.0,
        "small": {"spacing": 3.0, "interpolation": "bicubic"},
        "large": {"spacing": 8.0, "interpolation": "bilinear"},
        "custom": {"spacing": 5.0, "interpolation": "bicubic"},
    },
    "auto_level_presets": {},
}


def _deep_merge_defaults(
    defaults: Dict[str, Any], loaded: Dict[str, Any]
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, default_val in defaults.items():
        if key in loaded:
            loaded_val = loaded[key]
            if isinstance(default_val, dict) and isinstance(loaded_val, dict):
                merged[key] = _deep_merge_defaults(default_val, loaded_val)
            else:
                merged[key] = loaded_val
        else:
            merged[key] = default_val
    for key, loaded_val in loaded.items():
        if key not in merged:
            merged[key] = loaded_val
    return merged


def _normalize_top_level_settings(
    loaded: Dict[str, Any],
    defaults: Dict[str, Any],
    *,
    repaired_keys_out: list[str] | None = None,
) -> Dict[str, Any]:
    if repaired_keys_out is None:
        repaired_keys: list[str] = []
    else:
        repaired_keys = repaired_keys_out
    pruned: Dict[str, Any] = {}
    for key, value in loaded.items():
        if key not in defaults:
            repaired_keys.append(str(key))
            continue
        pruned[key] = value
    return pruned


def _normalize_nonnegative_int_setting(
    value: Any,
    *,
    default: int,
) -> tuple[int, bool]:
    if isinstance(value, bool):
        return int(default), True
    if isinstance(value, int):
        return (value, False) if value >= 0 else (int(default), True)
    if isinstance(value, float):
        if value >= 0.0 and value.is_integer():
            return int(value), False
        return int(default), True
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except Exception:
            return int(default), True
        return (parsed, False) if parsed >= 0 else (int(default), True)
    return int(default), True


def _repair_invalid_settings(
    merged: Dict[str, Any], defaults: Dict[str, Any], *, repaired_keys_out: list[str] | None = None
) -> Dict[str, Any]:
    """Repair known invalid values to defaults so load can continue safely."""
    repaired = copy.deepcopy(merged)
    repaired_keys: list[str] = []

    baud = repaired.get("baud_rate")
    if baud not in _VALID_BAUD_RATES:
        repaired["baud_rate"] = defaults["baud_rate"]
        repaired_keys.append("baud_rate")

    interval = repaired.get("status_poll_interval")
    if not isinstance(interval, (int, float)) or interval <= 0:
        repaired["status_poll_interval"] = defaults["status_poll_interval"]
        repaired_keys.append("status_poll_interval")

    spindle_control_rpm, spindle_control_rpm_repaired = _normalize_nonnegative_int_setting(
        repaired.get("spindle_control_rpm"),
        default=int(defaults.get("spindle_control_rpm", DEFAULT_SETTINGS.get("spindle_control_rpm", 12000))),
    )
    repaired["spindle_control_rpm"] = int(spindle_control_rpm)
    if spindle_control_rpm_repaired:
        repaired_keys.append("spindle_control_rpm")

    mode = repaired.get("unit_mode")
    if mode not in _VALID_UNIT_MODES:
        repaired["unit_mode"] = defaults["unit_mode"]
        repaired_keys.append("unit_mode")

    jog_dro_smoothing_mode = str(repaired.get("jog_dro_smoothing_mode", "") or "").strip().lower()
    if jog_dro_smoothing_mode not in JOG_DRO_SMOOTHING_CHOICES:
        repaired["jog_dro_smoothing_mode"] = defaults.get(
            "jog_dro_smoothing_mode",
            JOG_DRO_SMOOTHING_OFF,
        )
        repaired_keys.append("jog_dro_smoothing_mode")

    touch_scroll_mode = str(repaired.get("touch_scroll_mode", "") or "").strip().lower()
    if touch_scroll_mode not in _VALID_TOUCH_SCROLL_MODES:
        repaired["touch_scroll_mode"] = defaults.get(
            "touch_scroll_mode", "thumb_and_swipe"
        )
        repaired_keys.append("touch_scroll_mode")

    theme = repaired.get(_THEME_SETTING_KEY)
    if not isinstance(theme, str) or not str(theme).strip():
        repaired[_THEME_SETTING_KEY] = defaults.get(_THEME_SETTING_KEY, "")
        repaired_keys.append(_THEME_SETTING_KEY)

    linux_file_dialog_scale = repaired.get("linux_file_dialog_scale")
    if isinstance(linux_file_dialog_scale, (int, float, str)):
        try:
            linux_file_dialog_scale_value = float(linux_file_dialog_scale)
        except Exception:
            linux_file_dialog_scale_value = None
    else:
        linux_file_dialog_scale_value = None
    if (
        linux_file_dialog_scale_value is None
        or linux_file_dialog_scale_value < 1.4
        or linux_file_dialog_scale_value > 3.0
    ):
        repaired["linux_file_dialog_scale"] = defaults.get("linux_file_dialog_scale", 1.4)
        repaired_keys.append("linux_file_dialog_scale")

    linux_file_dialog_default_path = repaired.get("linux_file_dialog_default_path")
    if not isinstance(linux_file_dialog_default_path, str) or not str(
        linux_file_dialog_default_path
    ).strip():
        repaired["linux_file_dialog_default_path"] = defaults.get(
            "linux_file_dialog_default_path",
            "/root/CNC_Jobs",
        )
        repaired_keys.append("linux_file_dialog_default_path")

    for key in ("bit_setter_x", "bit_setter_y"):
        raw_value = repaired.get(key)
        try:
            if raw_value is None:
                raise ValueError("missing")
            float(raw_value)
        except Exception:
            repaired[key] = defaults.get(key, DEFAULT_SETTINGS.get(key))
            repaired_keys.append(key)

    for key in ("bit_setter_rough_probe_speed", "bit_setter_fine_probe_speed"):
        raw_value = repaired.get(key)
        try:
            if raw_value is None:
                raise ValueError("missing")
            value = float(raw_value)
        except Exception:
            value = None
        if value is None or value <= 0.0:
            repaired[key] = defaults.get(key, DEFAULT_SETTINGS.get(key))
            repaired_keys.append(key)

    raw_bit_setter_probe_dwell = repaired.get("bit_setter_probe_dwell")
    try:
        if raw_bit_setter_probe_dwell is None:
            raise ValueError("missing")
        bit_setter_probe_dwell = float(raw_bit_setter_probe_dwell)
    except Exception:
        bit_setter_probe_dwell = None
    if bit_setter_probe_dwell is None or bit_setter_probe_dwell < 0.0:
        repaired["bit_setter_probe_dwell"] = defaults.get(
            "bit_setter_probe_dwell",
            DEFAULT_SETTINGS.get("bit_setter_probe_dwell"),
        )
        repaired_keys.append("bit_setter_probe_dwell")

    for key in ("xyz_plate_x_offset", "xyz_plate_y_offset"):
        raw_value = repaired.get(key)
        try:
            if raw_value is None:
                raise ValueError("missing")
            float(raw_value)
        except Exception:
            repaired[key] = defaults.get(key, DEFAULT_SETTINGS.get(key))
            repaired_keys.append(key)

    for key in (
        "xyz_plate_thickness",
        "xyz_plate_min_safe_probe_distance",
        "xyz_plate_side_clearance_distance",
        "xyz_plate_z_rough_probe_speed",
        "xyz_plate_z_reprobe_speed",
        "xyz_plate_z_fine_probe_speed",
        "xyz_plate_xy_rough_probe_speed",
        "xyz_plate_xy_fine_probe_speed",
    ):
        raw_value = repaired.get(key)
        try:
            if raw_value is None:
                raise ValueError("missing")
            value = float(raw_value)
        except Exception:
            value = None
        if value is None or value <= 0.0:
            repaired[key] = defaults.get(key, DEFAULT_SETTINGS.get(key))
            repaired_keys.append(key)

    raw_xyz_plate_probe_dwell = repaired.get("xyz_plate_probe_dwell")
    try:
        if raw_xyz_plate_probe_dwell is None:
            raise ValueError("missing")
        xyz_plate_probe_dwell = float(raw_xyz_plate_probe_dwell)
    except Exception:
        xyz_plate_probe_dwell = None
    if xyz_plate_probe_dwell is None or xyz_plate_probe_dwell < 0.0:
        repaired["xyz_plate_probe_dwell"] = defaults.get(
            "xyz_plate_probe_dwell",
            DEFAULT_SETTINGS.get("xyz_plate_probe_dwell"),
        )
        repaired_keys.append("xyz_plate_probe_dwell")

    if repaired_keys:
        if repaired_keys_out is not None:
            repaired_keys_out.extend(sorted(set(repaired_keys)))
        logger.warning(
            "Repaired invalid settings value(s): %s",
            ", ".join(sorted(set(repaired_keys))),
        )
    return repaired


def get_default_settings_dir() -> str:
    """Get default directory for settings storage.

    Returns:
        Path to settings directory
    """
    # Check environment variable first
    env_dir = os.getenv("SIMPLE_SENDER_CONFIG_DIR")
    if env_dir:
        return env_dir

    # Platform-specific defaults
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    else:
        base = os.getenv("XDG_CONFIG_HOME")

    if not base:
        base = os.path.expanduser("~")

    return os.path.join(base, _APP_DATA_DIR_NAME)


def get_settings_path() -> str:
    """Get path to settings file.

    Creates directory if it doesn't exist.
    Falls back to home directory or current directory if creation fails.

    Returns:
        Full path to settings file
    """
    base_dir = get_default_settings_dir()

    def _ensure_writable_dir(path: str, label: str) -> str | None:
        try:
            os.makedirs(path, exist_ok=True)
            if os.path.exists(path) and os.access(path, os.W_OK):
                return path
            raise OSError(f"{label} settings directory is not writable")
        except OSError as e:
            logger.warning("Failed to use %s settings directory: %s", label, e)
            return None

    chosen = _ensure_writable_dir(base_dir, "primary")
    if chosen is None:
        fallback_dir = os.path.join(os.path.expanduser("~"), _HIDDEN_FALLBACK_DIR_NAME)
        chosen = _ensure_writable_dir(fallback_dir, "fallback")
    if chosen is None:
        temp_dir = os.path.join(tempfile.gettempdir(), _APP_DATA_DIR_NAME)
        chosen = _ensure_writable_dir(temp_dir, "temporary")
    if chosen is None:
        # Last resort - app directory (may still be read-only)
        base_dir = os.path.dirname(__file__)
        if not os.path.exists(base_dir) or not os.access(base_dir, os.W_OK):
            logger.warning(
                "Settings directory is not writable; using %s anyway", base_dir
            )
        chosen = base_dir

    return os.path.join(chosen, SETTINGS_FILENAME)


class Settings:
    """Application settings manager.

    Handles loading, saving, and accessing application settings with
    atomic file operations and automatic backup.

    Example:
        settings = Settings()
        settings.load()
        settings.data["last_port"] = "COM3"
        settings.save()
    """

    def __init__(self, filepath: Optional[str] = None):
        """Initialize settings manager.

        Args:
            filepath: Optional custom settings file path
        """
        self.filepath = filepath or get_settings_path()
        self.data: Dict[str, Any] = self._get_defaults()
        self.last_load_repaired_keys: list[str] = []
        self.last_import_repaired_keys: list[str] = []
        logger.info("Settings file: %s", self.filepath)

    def _get_defaults(self) -> Dict[str, Any]:
        """Get default settings values.

        Returns:
            Dictionary of default settings
        """
        return copy.deepcopy(DEFAULT_SETTINGS)

    def load(self) -> bool:
        """Load settings from file.

        Returns:
            True if loaded successfully, False otherwise

        Note:
            On failure, default settings are used
        """
        if not os.path.exists(self.filepath):
            logger.info("No settings file found, using defaults")
            self.last_load_repaired_keys = []
            return False

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)
            if not isinstance(loaded_data, dict):
                raise SettingsLoadError("Settings root must be a JSON object")

            # Merge defaults and repair known invalid values.
            defaults = self._get_defaults()
            repaired_keys: list[str] = []
            normalized_loaded = _normalize_top_level_settings(
                loaded_data,
                defaults,
                repaired_keys_out=repaired_keys,
            )
            merged = _deep_merge_defaults(defaults, normalized_loaded)
            self.data = _repair_invalid_settings(
                merged,
                defaults,
                repaired_keys_out=repaired_keys,
            )
            self.last_load_repaired_keys = repaired_keys
            self.validate()

            logger.info("Settings loaded successfully")
            return True

        except SettingsValidationError as e:
            logger.error(
                "Invalid settings values while loading %s: %s",
                self.filepath,
                e,
            )
            raise SettingsLoadError(f"Invalid settings: {e}")

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in settings file %s: %s", self.filepath, e)
            raise SettingsLoadError(f"Invalid JSON: {e}")

        except IOError as e:
            logger.error("Failed to read settings file %s: %s", self.filepath, e)
            raise SettingsLoadError(f"Failed to read file: {e}")

        except SettingsLoadError:
            raise

        except Exception as e:
            logger.error(
                "Unexpected error loading settings from %s: %s",
                self.filepath,
                e,
            )
            raise SettingsLoadError(f"Unexpected error: {e}")

    def save(self) -> None:
        """Save settings to file atomically.

        Uses atomic file write with backup to prevent data loss.

        Raises:
            SettingsSaveError: If save fails
        """
        filepath = Path(self.filepath)
        backup_path = Path(str(filepath) + SETTINGS_BACKUP_SUFFIX)

        try:
            # Create backup of existing file
            if filepath.exists():
                try:
                    shutil.copy2(filepath, backup_path)
                except IOError as e:
                    logger.warning(
                        "Failed to create settings backup %s: %s",
                        backup_path,
                        e,
                    )
            self._write_json_atomically(filepath, self.data)

            logger.info("Settings saved successfully")

        except IOError as e:
            logger.error("Failed to write settings file %s: %s", filepath, e)

            # Try to restore backup
            if backup_path.exists():
                try:
                    shutil.copy2(backup_path, filepath)
                    logger.info("Settings restored from backup")
                except IOError as restore_exc:
                    logger.debug(
                        "Failed restoring settings backup after save error: %s",
                        restore_exc,
                        exc_info=restore_exc,
                    )

            raise SettingsSaveError(f"Failed to save: {e}")

        except Exception as e:
            logger.error("Unexpected error saving settings to %s: %s", filepath, e)
            raise SettingsSaveError(f"Unexpected error: {e}")

    def _write_json_atomically(self, filepath: Path, payload: Dict[str, Any]) -> None:
        temp_path: Path | None = None
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(filepath.parent),
                prefix=f"{filepath.name}.",
                suffix=SETTINGS_TEMP_SUFFIX,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(payload, temp_file, indent=2, sort_keys=True)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            assert temp_path is not None
            temp_path.replace(filepath)
            if os.name != "nt":
                try:
                    dir_fd = os.open(str(filepath.parent), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except Exception as fsync_exc:
                    logger.debug(
                        "Directory fsync skipped/failed for settings save: %s",
                        fsync_exc,
                        exc_info=fsync_exc,
                    )
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError as cleanup_exc:
                    logger.debug(
                        "Failed deleting temporary settings file: %s",
                        cleanup_exc,
                        exc_info=cleanup_exc,
                    )

    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults."""
        self.data = self._get_defaults()
        logger.info("Settings reset to defaults")

    def validate(self) -> bool:
        """Validate current settings.

        Returns:
            True if valid

        Raises:
            SettingsValidationError: If validation fails
        """
        # Validate data types
        if not isinstance(self.data, dict):
            raise SettingsValidationError("Settings must be a dictionary")

        # Validate specific settings
        if "baud_rate" in self.data:
            baud = self.data["baud_rate"]
            if baud not in _VALID_BAUD_RATES:
                raise SettingsValidationError(f"Invalid baud rate: {baud}")

        if "status_poll_interval" in self.data:
            interval = self.data["status_poll_interval"]
            if not isinstance(interval, (int, float)) or interval <= 0:
                raise SettingsValidationError(f"Invalid poll interval: {interval}")

        if "spindle_control_rpm" in self.data:
            spindle_control_rpm = self.data["spindle_control_rpm"]
            if (
                isinstance(spindle_control_rpm, bool)
                or not isinstance(spindle_control_rpm, int)
                or spindle_control_rpm < 0
            ):
                raise SettingsValidationError(
                    f"Invalid spindle control RPM: {spindle_control_rpm}"
                )

        if "unit_mode" in self.data:
            mode = self.data["unit_mode"]
            if mode not in _VALID_UNIT_MODES:
                raise SettingsValidationError(f"Invalid unit mode: {mode}")

        if _THEME_SETTING_KEY in self.data:
            theme = self.data[_THEME_SETTING_KEY]
            if not isinstance(theme, str) or not str(theme).strip():
                raise SettingsValidationError(f"Invalid theme: {theme}")

        return True

    def add_recent_file(self, filepath: str) -> None:
        """Add file to recent files list.

        Args:
            filepath: Path to add
        """
        recent = self.data.get("recent_files", [])

        # Remove if already exists
        if filepath in recent:
            recent.remove(filepath)

        # Add to beginning
        recent.insert(0, filepath)

        # Trim to max length
        max_recent = self.data.get("max_recent_files", 10)
        recent = recent[:max_recent]

        self.data["recent_files"] = recent

    def get_recent_files(self) -> list[str]:
        """Get list of recent files.

        Returns:
            List of recent file paths (existing files only)
        """
        recent = self.data.get("recent_files", [])
        # Filter to only existing files
        return [f for f in recent if os.path.exists(f)]

    def export_to_file(self, filepath: str) -> None:
        """Export settings to a different file.

        Args:
            filepath: Target file path

        Raises:
            SettingsSaveError: If export fails
        """
        try:
            self._write_json_atomically(Path(filepath), self.data)
            logger.info("Settings exported to %s", filepath)
        except IOError as e:
            raise SettingsSaveError(f"Failed to export: {e}")
        except Exception as e:
            raise SettingsSaveError(f"Unexpected export error: {e}")

    def import_from_file(self, filepath: str) -> list[str]:
        """Import settings from a file.

        Args:
            filepath: Source file path

        Raises:
            SettingsLoadError: If import fails
        """
        self.last_import_repaired_keys = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                imported_data = json.load(f)
            if not isinstance(imported_data, dict):
                raise SettingsLoadError("Settings root must be a JSON object")

            # Merge defaults and repair known invalid values.
            defaults = self._get_defaults()
            repaired_keys: list[str] = []
            normalized_imported = _normalize_top_level_settings(
                imported_data,
                defaults,
                repaired_keys_out=repaired_keys,
            )
            merged = _deep_merge_defaults(defaults, normalized_imported)
            self.data = _repair_invalid_settings(
                merged,
                defaults,
                repaired_keys_out=repaired_keys,
            )
            self.last_import_repaired_keys = repaired_keys
            self.validate()

            logger.info("Settings imported from %s", filepath)
            return list(repaired_keys)

        except SettingsValidationError as e:
            raise SettingsLoadError(f"Invalid settings: {e}")
        except json.JSONDecodeError as e:
            raise SettingsLoadError(f"Invalid JSON: {e}")
        except IOError as e:
            raise SettingsLoadError(f"Failed to read file: {e}")
