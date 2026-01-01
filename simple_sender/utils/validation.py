"""Validation utilities for Simple Sender.

This module provides validation functions for various input types,
ensuring data integrity throughout the application.
"""

from typing import Optional, Tuple

from .constants import GRBL_SETTING_LIMITS, GRBL_NON_NUMERIC_SETTINGS
from .exceptions import InvalidParameterError, InvalidRangeError


def validate_feed_rate(feed: float) -> float:
    """Validate feed rate value.
    
    Args:
        feed: Feed rate in mm/min or inches/min
        
    Returns:
        The validated feed rate
        
    Raises:
        InvalidParameterError: If feed rate is invalid
    """
    try:
        feed = float(feed)
    except (TypeError, ValueError):
        raise InvalidParameterError("feed_rate", feed, "must be numeric")
    
    if feed <= 0:
        raise InvalidParameterError("feed_rate", feed, "must be positive")
    
    return feed


def validate_unit_mode(unit_mode: str) -> str:
    """Validate unit mode.
    
    Args:
        unit_mode: Unit mode string ("mm" or "inch")
        
    Returns:
        The validated unit mode
        
    Raises:
        InvalidParameterError: If unit mode is invalid
    """
    if unit_mode not in ("mm", "inch"):
        raise InvalidParameterError("unit_mode", unit_mode, "must be 'mm' or 'inch'")
    
    return unit_mode


def validate_grbl_setting(
    setting_id: int,
    value: str
) -> Tuple[int, float]:
    """Validate a GRBL setting value.
    
    Args:
        setting_id: GRBL setting ID (e.g., 110 for X max rate)
        value: Setting value as string
        
    Returns:
        Tuple of (setting_id, validated_value)
        
    Raises:
        InvalidParameterError: If setting ID or value is invalid
        InvalidRangeError: If value is out of valid range
    """
    # Check if setting ID is recognized
    if setting_id not in GRBL_SETTING_LIMITS:
        raise InvalidParameterError("setting_id", setting_id, "unknown GRBL setting")
    
    # Allow non-numeric settings (if any)
    if setting_id in GRBL_NON_NUMERIC_SETTINGS:
        return setting_id, value
    
    # Validate numeric value
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        raise InvalidParameterError(
            f"setting_{setting_id}",
            value,
            "must be numeric"
        )
    
    # Check range
    min_val, max_val = GRBL_SETTING_LIMITS[setting_id]
    if not (min_val <= numeric_value <= max_val):
        raise InvalidRangeError(numeric_value, min_val, max_val)
    
    return setting_id, numeric_value


def validate_port_name(port: str) -> str:
    """Validate serial port name.
    
    Args:
        port: Serial port name (e.g., "COM3" or "/dev/ttyUSB0")
        
    Returns:
        The validated port name
        
    Raises:
        InvalidParameterError: If port name is invalid
    """
    if not port or not isinstance(port, str):
        raise InvalidParameterError("port", port, "must be non-empty string")
    
    port = port.strip()
    if not port:
        raise InvalidParameterError("port", port, "must be non-empty")
    
    return port


def validate_baud_rate(baud: int) -> int:
    """Validate baud rate.
    
    Args:
        baud: Baud rate value
        
    Returns:
        The validated baud rate
        
    Raises:
        InvalidParameterError: If baud rate is invalid
    """
    try:
        baud = int(baud)
    except (TypeError, ValueError):
        raise InvalidParameterError("baud_rate", baud, "must be integer")
    
    # Common baud rates for GRBL
    valid_baud_rates = [9600, 19200, 38400, 57600, 115200, 230400]
    if baud not in valid_baud_rates:
        raise InvalidParameterError(
            "baud_rate",
            baud,
            f"must be one of {valid_baud_rates}"
        )
    
    return baud


def validate_interval(interval: float, min_val: float = 0.0) -> float:
    """Validate time interval.
    
    Args:
        interval: Time interval in seconds
        min_val: Minimum allowed value (default 0.0)
        
    Returns:
        The validated interval
        
    Raises:
        InvalidParameterError: If interval is invalid
    """
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        raise InvalidParameterError("interval", interval, "must be numeric")
    
    if interval < min_val:
        raise InvalidParameterError(
            "interval",
            interval,
            f"must be >= {min_val}"
        )
    
    return interval


def validate_line_index(
    index: int,
    max_index: Optional[int] = None
) -> int:
    """Validate G-code line index.
    
    Args:
        index: Line index (0-based)
        max_index: Maximum valid index (optional)
        
    Returns:
        The validated index
        
    Raises:
        InvalidParameterError: If index is invalid
    """
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise InvalidParameterError("line_index", index, "must be integer")
    
    if index < 0:
        raise InvalidParameterError("line_index", index, "must be non-negative")
    
    if max_index is not None and index > max_index:
        raise InvalidRangeError(index, 0, max_index)
    
    return index


def validate_rpm(rpm: int, min_rpm: int = 0, max_rpm: int = 100000) -> int:
    """Validate spindle RPM value.
    
    Args:
        rpm: Spindle RPM
        min_rpm: Minimum valid RPM (default 0)
        max_rpm: Maximum valid RPM (default 100000)
        
    Returns:
        The validated RPM
        
    Raises:
        InvalidParameterError: If RPM is invalid
        InvalidRangeError: If RPM is out of range
    """
    try:
        rpm = int(rpm)
    except (TypeError, ValueError):
        raise InvalidParameterError("rpm", rpm, "must be integer")
    
    if not (min_rpm <= rpm <= max_rpm):
        raise InvalidRangeError(rpm, min_rpm, max_rpm)
    
    return rpm


def validate_coordinate(value: float, axis: str) -> float:
    """Validate coordinate value.
    
    Args:
        value: Coordinate value
        axis: Axis name (for error messages)
        
    Returns:
        The validated coordinate
        
    Raises:
        InvalidParameterError: If coordinate is invalid
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise InvalidParameterError(
            f"{axis}_coordinate",
            value,
            "must be numeric"
        )
    
    return value


def validate_zoom(zoom: float, min_zoom: float = 0.2, max_zoom: float = 5.0) -> float:
    """Validate zoom level.
    
    Args:
        zoom: Zoom level
        min_zoom: Minimum valid zoom (default 0.2)
        max_zoom: Maximum valid zoom (default 5.0)
        
    Returns:
        The validated zoom level
        
    Raises:
        InvalidParameterError: If zoom is invalid
        InvalidRangeError: If zoom is out of range
    """
    try:
        zoom = float(zoom)
    except (TypeError, ValueError):
        raise InvalidParameterError("zoom", zoom, "must be numeric")
    
    if not (min_zoom <= zoom <= max_zoom):
        raise InvalidRangeError(zoom, min_zoom, max_zoom)
    
    return zoom
