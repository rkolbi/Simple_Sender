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

"""Custom exceptions for Simple Sender.

This module defines specific exception types for different error conditions,
enabling better error handling and debugging throughout the application.
"""

from typing import Any, Optional


class SimpleSenderException(Exception):
    """Base exception for all Simple Sender errors."""
    pass


# ============================================================================
# SERIAL COMMUNICATION EXCEPTIONS
# ============================================================================

class SerialException(SimpleSenderException):
    """Base exception for serial communication errors."""
    pass


class SerialConnectionError(SerialException):
    """Failed to connect to serial port."""
    pass


class SerialDisconnectError(SerialException):
    """Unexpected disconnection from serial port."""
    pass


class SerialTimeoutError(SerialException):
    """Serial read or write operation timed out."""
    pass


class SerialWriteError(SerialException):
    """Failed to write data to serial port."""
    pass


class SerialReadError(SerialException):
    """Failed to read data from serial port."""
    pass


# ============================================================================
# GRBL EXCEPTIONS
# ============================================================================

class GrblException(SimpleSenderException):
    """Base exception for GRBL-related errors."""
    pass


class GrblNotConnectedException(GrblException):
    """Attempted operation while not connected to GRBL."""
    pass


class GrblAlarmException(GrblException):
    """GRBL is in alarm state."""
    
    def __init__(self, message: str, alarm_code: Optional[str] = None):
        super().__init__(message)
        self.alarm_code = alarm_code


class GrblErrorException(GrblException):
    """GRBL returned an error response."""
    
    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code


class GrblBufferOverflowException(GrblException):
    """Attempted to overfill GRBL's RX buffer."""
    pass


class GrblStreamingException(GrblException):
    """Error during G-code streaming."""
    pass


# ============================================================================
# G-CODE EXCEPTIONS
# ============================================================================

class GcodeException(SimpleSenderException):
    """Base exception for G-code related errors."""
    pass


class GcodeParseError(GcodeException):
    """Failed to parse G-code."""
    
    def __init__(
        self,
        message: str,
        line_number: Optional[int] = None,
        line_content: Optional[str] = None,
    ):
        super().__init__(message)
        self.line_number = line_number
        self.line_content = line_content


class GcodeValidationError(GcodeException):
    """G-code validation failed."""
    pass


class GcodeFileError(GcodeException):
    """Error reading or writing G-code file."""
    pass


# ============================================================================
# MACRO EXCEPTIONS
# ============================================================================

class MacroException(SimpleSenderException):
    """Base exception for macro errors."""
    pass


class MacroExecutionError(MacroException):
    """Error executing macro."""
    
    def __init__(self, message: str, line_number: Optional[int] = None):
        super().__init__(message)
        self.line_number = line_number


class MacroTimeoutError(MacroException):
    """Macro wait operation timed out."""
    pass


class MacroBusyError(MacroException):
    """Attempted to run macro while another is running."""
    pass


class MacroFileError(MacroException):
    """Error reading macro file."""
    pass


# ============================================================================
# SETTINGS EXCEPTIONS
# ============================================================================

class SettingsException(SimpleSenderException):
    """Base exception for settings errors."""
    pass


class SettingsLoadError(SettingsException):
    """Failed to load settings file."""
    pass


class SettingsSaveError(SettingsException):
    """Failed to save settings file."""
    pass


class SettingsValidationError(SettingsException):
    """Settings validation failed."""
    pass


# ============================================================================
# VALIDATION EXCEPTIONS
# ============================================================================

class ValidationException(SimpleSenderException):
    """Base exception for validation errors."""
    pass


class InvalidParameterError(ValidationException):
    """Invalid parameter value."""
    
    def __init__(self, parameter_name: str, value: Any, reason: Optional[str] = None):
        self.parameter_name = parameter_name
        self.value = value
        self.reason = reason
        
        message = f"Invalid value for '{parameter_name}': {value}"
        if reason:
            message += f" ({reason})"
        super().__init__(message)


class InvalidRangeError(ValidationException):
    """Value out of valid range."""
    
    def __init__(self, value, min_val, max_val):
        self.value = value
        self.min_val = min_val
        self.max_val = max_val
        
        message = f"Value {value} out of range [{min_val}, {max_val}]"
        super().__init__(message)
