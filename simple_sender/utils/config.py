"""Application settings management.

This module handles loading, saving, and managing application settings
with atomic file operations and automatic backup.
"""

import json
import os
import sys
import shutil
import logging
from typing import Dict, Any, Optional
from pathlib import Path

from .constants import (
    SETTINGS_FILENAME,
    SETTINGS_BACKUP_SUFFIX,
    SETTINGS_TEMP_SUFFIX,
)
from .exceptions import (
    SettingsLoadError,
    SettingsSaveError,
    SettingsValidationError,
)

logger = logging.getLogger(__name__)


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
    
    try:
        os.makedirs(base_dir, exist_ok=True)
    except OSError as e:
        logger.warning(f"Failed to create settings directory: {e}")
        # Try fallback location
        fallback_dir = os.path.join(os.path.expanduser("~"), ".simple_sender")
        try:
            os.makedirs(fallback_dir, exist_ok=True)
            base_dir = fallback_dir
        except OSError:
            # Last resort - current directory
            base_dir = os.path.dirname(__file__)
    
    return os.path.join(base_dir, SETTINGS_FILENAME)


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
        return {
            "last_port": None,
            "baud_rate": 115200,
            "status_poll_interval": 0.2,
            "status_query_failure_limit": 3,
            "auto_reconnect": False,
            "training_wheels": True,
            "all_stop_mode": "stop_reset",
            "current_line_mode": "acked",
            "performance_mode": False,
            "gui_logging_enabled": True,
            "tooltips_enabled": True,
            "keybindings_enabled": True,
            "unit_mode": "mm",
            "jog_step": 1.0,
            "jog_feed": 1000.0,
            "default_spindle_rpm": 12000,
            "estimate_factor": 1.0,
            "estimate_fallback_rapid": 5000.0,
            "3d_view_enabled": True,
            "3d_view_settings": {
                "azimuth": 0.7853981633974483,  # 45 degrees
                "elevation": 0.5235987755982988,  # 30 degrees
                "zoom": 1.0,
                "pan_x": 0.0,
                "pan_y": 0.0,
                "show_rapid": False,
                "show_feed": True,
                "show_arc": False,
            },
            "machine_profiles": {},
            "window_geometry": None,
            "recent_files": [],
            "max_recent_files": 10,
            "error_dialog_interval": 2.0,
            "error_dialog_burst_window": 30.0,
            "error_dialog_burst_limit": 3,
            "error_dialogs_enabled": True,
            "macros_allow_python": False,
        }
    
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
            defaults.update(loaded_data)
            self.data = defaults
            
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
            defaults.update(imported_data)
            self.data = defaults
            
            logger.info(f"Settings imported from {filepath}")
            
        except json.JSONDecodeError as e:
            raise SettingsLoadError(f"Invalid JSON: {e}")
        except IOError as e:
            raise SettingsLoadError(f"Failed to read file: {e}")
