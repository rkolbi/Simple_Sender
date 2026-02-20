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
    SETTINGS_FILENAME,
    SETTINGS_BACKUP_SUFFIX,
    SETTINGS_TEMP_SUFFIX,
    WATCHDOG_HOMING_TIMEOUT,
)
from .exceptions import (
    SettingsLoadError,
    SettingsSaveError,
    SettingsValidationError,
)

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "3d_view_enabled": True,
    "3d_view_settings": {
        "azimuth": 0.7853981633974483,
        "elevation": 0.5235987755982988,
        "pan_x": 0.0,
        "pan_y": 0.0,
        "show_arc": False,
        "show_feed": True,
        "show_rapid": False,
        "zoom": 1.0,
    },
    "view_3d": {
        "azimuth": -5.87460183660256,
        "elevation": -1.1307963267948968,
        "pan_x": 0.0,
        "pan_y": 0.0,
        "zoom": 0.9999999999999998,
    },
    "active_profile": "",
    "all_stop_mode": "stop_reset",
    "auto_reconnect": False,
    "baud_rate": 115200,
    "console_positions_enabled": False,
    "current_line_mode": "sent",
    "default_spindle_rpm": 12000,
    "dry_run_sanitize_stream": False,
    "error_dialog_burst_limit": 3,
    "error_dialog_burst_window": 30.0,
    "error_dialog_interval": 2.0,
    "error_dialogs_enabled": True,
    "grbl_popup_enabled": True,
    "grbl_popup_auto_dismiss_sec": 12.0,
    "grbl_popup_dedupe_sec": 3.0,
    "estimate_factor": 1.1257575757575757,
    "estimate_fallback_rapid": 5000.0,
    "estimate_rate_x": "",
    "estimate_rate_y": "",
    "estimate_rate_z": "",
    "fallback_rapid_rate": "5000.0",
    "fullscreen_on_startup": True,
    "gui_logging_enabled": True,
    "job_completion_beep": False,
    "job_completion_popup": True,
    "joystick_safety_binding": None,
    "joystick_safety_enabled": False,
    "jog_feed": 1000.0,
    "jog_feed_xy": 4000.0,
    "jog_feed_z": 500.0,
    "jog_step": 1.0,
    "key_bindings": {},
    "keyboard_bindings_enabled": True,
    "last_gcode_dir": "",
    "last_port": "",
    "machine_profiles": [],
    "macros_allow_python": False,
    "macro_line_timeout_sec": 0.0,
    "macro_total_timeout_sec": 0.0,
    "macro_probe_z_location": -5.0,
    "macro_probe_safety_margin": 3.0,
    "max_recent_files": 10,
    "performance_mode": True,
    "recent_files": [],
    "reconnect_on_open": True,
    "render3d_enabled": True,
    "show_recover_button": False,
    "show_resume_from_button": False,
    "show_endstop_indicator": True,
    "show_probe_indicator": True,
    "show_hold_indicator": True,
    "auto_level_enabled": True,
    "show_quick_tips_button": True,
    "show_quick_3d_button": True,
    "show_quick_keys_button": True,
    "show_quick_alo_button": True,
    "show_quick_release_button": True,
    "status_poll_interval": 0.2,
    "status_query_failure_limit": 3,
    "homing_watchdog_enabled": True,
    "homing_watchdog_timeout": WATCHDOG_HOMING_TIMEOUT,
    "stop_joystick_hold_on_focus_loss": True,
    "step_xy": 400.0,
    "step_z": 1.0,
    "theme": "vista",
    "ui_scale": 1.5,
    "scrollbar_width": "wide",
    "toolpath_arc_detail_deg": 9.031746031746032,
    "toolpath_draw_percent": 82,
    "toolpath_full_limit": 33611,
    "toolpath_full_parse_limit": 0,
    "toolpath_interactive_limit": 4270,
    "toolpath_lightweight": False,
    "toolpath_low_power": False,
    "toolpath_performance": 81.74603174603175,
    "toolpath_quality": 100.0,
    "toolpath_renderer": "canvas",
    "toolpath_show_arc": True,
    "toolpath_show_feed": True,
    "toolpath_show_rapid": False,
    "toolpath_streaming_render_interval": 0.25,
    "tooltips_enabled": True,
    "tooltip_timeout_sec": 10.0,
    "numeric_keypad_enabled": True,
    "training_wheels": True,
    "unit_mode": "mm",
    "validate_streaming_gcode": True,
    "streaming_line_threshold": GCODE_STREAMING_LINE_THRESHOLD,
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

def _deep_merge_defaults(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
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
    
    return os.path.join(base, "SimpleSender")


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
        fallback_dir = os.path.join(os.path.expanduser("~"), ".simple_sender")
        chosen = _ensure_writable_dir(fallback_dir, "fallback")
    if chosen is None:
        temp_dir = os.path.join(tempfile.gettempdir(), "SimpleSender")
        chosen = _ensure_writable_dir(temp_dir, "temporary")
    if chosen is None:
        # Last resort - app directory (may still be read-only)
        base_dir = os.path.dirname(__file__)
        if not os.path.exists(base_dir) or not os.access(base_dir, os.W_OK):
            logger.warning("Settings directory is not writable; using %s anyway", base_dir)
        chosen = base_dir

    return os.path.join(chosen, SETTINGS_FILENAME)


class Settings:
    """Application settings manager.
    
    Handles loading, saving, and accessing application settings with
    atomic file operations and automatic backup.
    
    Example:
        settings = Settings()
        settings.load()
        settings.set("last_port", "COM3")
        settings.save()
    """
    
    def __init__(self, filepath: Optional[str] = None):
        """Initialize settings manager.
        
        Args:
            filepath: Optional custom settings file path
        """
        self.filepath = filepath or get_settings_path()
        self.data: Dict[str, Any] = self._get_defaults()
        logger.info(f"Settings file: {self.filepath}")
    
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
            return False
        
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)
            
            # Merge with defaults (in case new settings were added)
            defaults = self._get_defaults()
            self.data = _deep_merge_defaults(defaults, loaded_data)
            
            logger.info("Settings loaded successfully")
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in settings file: {e}")
            raise SettingsLoadError(f"Invalid JSON: {e}")
            
        except IOError as e:
            logger.error(f"Failed to read settings file: {e}")
            raise SettingsLoadError(f"Failed to read file: {e}")
            
        except Exception as e:
            logger.error(f"Unexpected error loading settings: {e}")
            raise SettingsLoadError(f"Unexpected error: {e}")
    
    def save(self) -> None:
        """Save settings to file atomically.
        
        Uses atomic file write with backup to prevent data loss.
        
        Raises:
            SettingsSaveError: If save fails
        """
        filepath = Path(self.filepath)
        temp_path = Path(str(filepath) + SETTINGS_TEMP_SUFFIX)
        backup_path = Path(str(filepath) + SETTINGS_BACKUP_SUFFIX)
        
        try:
            # Ensure directory exists
            filepath.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temporary file first
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            
            # Create backup of existing file
            if filepath.exists():
                try:
                    shutil.copy2(filepath, backup_path)
                except IOError as e:
                    logger.warning(f"Failed to create backup: {e}")
            
            # Atomic rename
            temp_path.replace(filepath)
            
            logger.info("Settings saved successfully")
            
        except IOError as e:
            logger.error(f"Failed to write settings: {e}")
            
            # Try to restore backup
            if backup_path.exists():
                try:
                    shutil.copy2(backup_path, filepath)
                    logger.info("Settings restored from backup")
                except IOError:
                    pass
            
            raise SettingsSaveError(f"Failed to save: {e}")
            
        except Exception as e:
            logger.error(f"Unexpected error saving settings: {e}")
            raise SettingsSaveError(f"Unexpected error: {e}")
            
        finally:
            # Clean up temp file
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get setting value.
        
        Args:
            key: Setting key (supports dot notation for nested keys)
            default: Default value if key not found
            
        Returns:
            Setting value or default
        """
        # Support nested keys like "3d_view_settings.zoom"
        keys = key.split(".")
        value = self.data
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """Set setting value.
        
        Args:
            key: Setting key (supports dot notation for nested keys)
            value: Value to set
        """
        # Support nested keys like "3d_view_settings.zoom"
        keys = key.split(".")
        
        if len(keys) == 1:
            self.data[key] = value
        else:
            # Navigate to nested dict
            current = self.data
            for k in keys[:-1]:
                if k not in current or not isinstance(current[k], dict):
                    current[k] = {}
                current = current[k]
            current[keys[-1]] = value
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings.
        
        Returns:
            Copy of all settings
        """
        return self.data.copy()
    
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
            valid_bauds = [9600, 19200, 38400, 57600, 115200, 230400]
            if baud not in valid_bauds:
                raise SettingsValidationError(f"Invalid baud rate: {baud}")
        
        if "status_poll_interval" in self.data:
            interval = self.data["status_poll_interval"]
            if not isinstance(interval, (int, float)) or interval <= 0:
                raise SettingsValidationError(f"Invalid poll interval: {interval}")
        
        if "unit_mode" in self.data:
            mode = self.data["unit_mode"]
            if mode not in ("mm", "inch"):
                raise SettingsValidationError(f"Invalid unit mode: {mode}")
        
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
    
    def get_recent_files(self) -> list:
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
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            logger.info(f"Settings exported to {filepath}")
        except IOError as e:
            raise SettingsSaveError(f"Failed to export: {e}")
    
    def import_from_file(self, filepath: str) -> None:
        """Import settings from a file.
        
        Args:
            filepath: Source file path
            
        Raises:
            SettingsLoadError: If import fails
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                imported_data = json.load(f)
            
            # Merge with defaults
            defaults = self._get_defaults()
            self.data = _deep_merge_defaults(defaults, imported_data)
            
            logger.info(f"Settings imported from {filepath}")
            
        except json.JSONDecodeError as e:
            raise SettingsLoadError(f"Invalid JSON: {e}")
        except IOError as e:
            raise SettingsLoadError(f"Failed to read file: {e}")
