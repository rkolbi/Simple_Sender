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

"""Constants and configuration values for Simple Sender.

This module centralizes all magic numbers, default values, and configuration
constants used throughout the application.
"""

import math
import re
from typing import Dict, Tuple, Set

# ============================================================================
# SERIAL COMMUNICATION CONSTANTS
# ============================================================================

BAUD_DEFAULT = 115200
"""Default baud rate for GRBL serial communication."""

STATUS_POLL_DEFAULT = 0.2
"""Default interval (seconds) between status queries."""

STATUS_POLL_IDLE = 0.5
"""Status poll interval when machine is idle."""

STATUS_POLL_RUNNING = 0.1
"""Status poll interval when machine is running."""

STATUS_POLL_INTERVAL_MIN = 0.05
"""Minimum allowed status poll interval (seconds)."""

STATUS_QUERY_FAILURE_LIMIT_DEFAULT = 3
"""Default status query failure limit before disconnect."""

STATUS_QUERY_FAILURE_LIMIT_MIN = 1
"""Minimum allowed status query failure limit."""

STATUS_QUERY_FAILURE_LIMIT_MAX = 10
"""Maximum allowed status query failure limit."""

# ============================================================================
# GRBL BUFFER MANAGEMENT
# ============================================================================

RX_BUFFER_SIZE = 128
"""GRBL RX buffer size in bytes."""

RX_BUFFER_SAFETY = 8
"""Safety margin to prevent buffer overflow."""

RX_BUFFER_WINDOW = RX_BUFFER_SIZE - RX_BUFFER_SAFETY
"""Usable buffer window for streaming."""

MAX_LINE_LENGTH = 80
"""Maximum G-code line length for GRBL 1.1h (including newline)."""

# ============================================================================
# GRBL REAL-TIME COMMAND BYTES
# ============================================================================

RT_RESET = b"\x18"
"""Ctrl-X soft reset."""

RT_STATUS = b"?"
"""Status report query."""

RT_HOLD = b"!"
"""Feed hold (pause)."""

RT_RESUME = b"~"
"""Cycle start / resume."""

RT_JOG_CANCEL = b"\x85"
"""Cancel jog command."""

# Feed override commands
RT_FO_RESET = b"\x90"
RT_FO_PLUS_10 = b"\x91"
RT_FO_MINUS_10 = b"\x92"

# Spindle override commands
RT_SO_RESET = b"\x99"
RT_SO_PLUS_10 = b"\x9A"
RT_SO_MINUS_10 = b"\x9B"

# ============================================================================
# UI CONSTANTS
# ============================================================================

MAX_CONSOLE_LINES = 5000
"""Maximum number of lines to keep in console."""

CONSOLE_BATCH_DELAY_MS = 50
"""Milliseconds to wait before flushing batched console updates."""

LINE_NUMBER_OFFSET = 1
"""Text widget line numbers are 1-indexed."""

UI_THREAD_CALL_DEFAULT_TIMEOUT = 5.0
"""Default timeout (seconds) for UI thread calls."""

UI_THREAD_CALL_POLL_INTERVAL = 0.2
"""Polling interval (seconds) while waiting for UI thread calls."""

UI_EVENT_QUEUE_MAXSIZE = 3000
"""Maximum number of low-priority UI events to buffer before dropping."""

UI_EVENT_QUEUE_DROP_NOTICE_INTERVAL = 1.0
"""Minimum seconds between UI drop summary log entries."""

GRBL_SETTINGS_WRITE_DELAY = 0.05
"""Delay between sending GRBL settings updates (seconds)."""

GCODE_VIEWER_CHUNK_SIZE_SMALL = 200
"""Chunk size for small files (<1000 lines)."""

GCODE_VIEWER_CHUNK_SIZE_MEDIUM = 500
"""Chunk size for medium files (1000-10000 lines)."""

GCODE_VIEWER_CHUNK_SIZE_LARGE = 1000
"""Chunk size for large files (>10000 lines)."""

GCODE_VIEWER_CHUNK_SIZE_LOAD_LARGE = 300
"""Chunk size for large load previews in the UI loader."""

GCODE_VIEWER_CHUNK_LOAD_THRESHOLD = 2000
"""Line count threshold for using the larger loader chunk size."""

GCODE_VIEWER_SMALL_FILE_THRESHOLD = 1000
"""Line count threshold for small files."""

GCODE_VIEWER_LARGE_FILE_THRESHOLD = 10000
"""Line count threshold for large files."""

GCODE_STREAMING_SIZE_THRESHOLD = 50 * 1024 * 1024
"""File size (bytes) above which streaming mode is used."""

GCODE_STREAMING_LINE_THRESHOLD = 250_000
"""Cleaned line count above which streaming mode is used."""

STREAMING_VALIDATION_PROMPT_LINES = 500_000
"""Cleaned line count above which streaming validation prompts for confirmation."""

STREAMING_VALIDATION_PROMPT_TIMEOUT = 120
"""Seconds to wait for streaming validation confirmation."""

GCODE_LOAD_PROGRESS_INTERVAL = 0.25
"""Minimum seconds between progress updates while loading/validating G-code."""

GCODE_STREAMING_PREVIEW_LINES = 2000
"""Preview lines shown when streaming from disk."""

GCODE_TOP_VIEW_STREAMING_SEGMENT_LIMIT = 50000
"""Maximum segments to keep for top view when streaming large files."""

CLEAR_ICON = "X"
"""Icon/text for clear buttons."""

TOOLTIP_DELAY_MS = 1000
"""Default tooltip delay (ms)."""

STOP_SIGN_CUT_RATIO = 0.29289321881345254
"""Cut ratio for the stop-sign octagon geometry."""

# Input bindings
JOYSTICK_POLL_INTERVAL_MS = 50
"""Joystick polling interval (ms)."""

JOYSTICK_DISCOVERY_INTERVAL_MS = 1000
"""Joystick discovery interval when disconnected (ms)."""

JOYSTICK_DISCOVERY_CONNECTED_INTERVAL_MS = 8000
"""Joystick discovery interval when connected (ms)."""

JOYSTICK_LIVE_STATUS_INTERVAL_MS = 200
"""Joystick live status update interval (ms)."""

JOYSTICK_CAPTURE_TIMEOUT_MS = 15000
"""Joystick capture timeout (ms)."""

JOYSTICK_LISTENING_TEXT = "Listening for joystick input..."
"""Status text for joystick capture mode."""

JOYSTICK_AXIS_THRESHOLD = 0.7
"""Threshold for joystick axis activation."""

JOYSTICK_AXIS_RELEASE_THRESHOLD = 0.2
"""Threshold for joystick axis release."""

JOYSTICK_HOLD_REPEAT_MS = 60
"""Repeat interval for joystick hold jogs (ms)."""

JOYSTICK_HOLD_POLL_INTERVAL_MS = 20
"""Polling interval for joystick hold input (ms)."""

JOYSTICK_HOLD_MISS_LIMIT = 2
"""Number of missed polls before releasing a joystick hold."""

JOYSTICK_HOLD_DEFINITIONS = [
    ("X-", "jog_hold_x_minus", "X", -1),
    ("X+", "jog_hold_x_plus", "X", 1),
    ("Y-", "jog_hold_y_minus", "Y", -1),
    ("Y+", "jog_hold_y_plus", "Y", 1),
    ("Z-", "jog_hold_z_minus", "Z", -1),
    ("Z+", "jog_hold_z_plus", "Z", 1),
]
"""Definitions for joystick hold bindings (label, id, axis, direction)."""

# Jogging presets
JOG_STEP_XY_VALUES = (0.1, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 400.0)
"""Default XY jog step values."""

JOG_STEP_Z_VALUES = (0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0)
"""Default Z jog step values."""

SAFE_JOG_FEED_XY = 1000.0
"""Safe-mode XY jog feed (mm/min)."""

SAFE_JOG_FEED_Z = 200.0
"""Safe-mode Z jog feed (mm/min)."""

SAFE_JOG_STEP_XY = 1.0
"""Safe-mode XY jog step (mm)."""

SAFE_JOG_STEP_Z = 0.1
"""Safe-mode Z jog step (mm)."""

JOG_FEED_EPSILON = 1e-9
"""Epsilon for detecting pure Z jog moves."""

JOG_PANEL_ALL_STOP_SIZE = 60
"""Size (pixels) of the All Stop button."""

JOG_PANEL_ALL_STOP_OFFSET_IN = 0.7
"""Offset in inches for the All Stop button alignment."""

JOG_PANEL_ALL_STOP_OFFSET_FALLBACK_PX = 96
"""Fallback pixel offset when inch conversion fails."""

# ============================================================================
# 3D VISUALIZATION CONSTANTS
# ============================================================================

VIEW_3D_DEFAULT_AZIMUTH = math.radians(45)
"""Default azimuth angle for 3D view."""

VIEW_3D_DEFAULT_ELEVATION = math.radians(30)
"""Default elevation angle for 3D view."""

VIEW_3D_DEFAULT_ZOOM = 1.0
"""Default zoom level for 3D view."""

VIEW_3D_ZOOM_MIN = 0.2
"""Minimum zoom level."""

VIEW_3D_ZOOM_MAX = 5.0
"""Maximum zoom level."""

VIEW_3D_ZOOM_STEP = 1.1
"""Zoom multiplier per scroll step."""

VIEW_3D_ELEVATION_LIMIT = math.pi / 2 - 0.1
"""Maximum elevation to prevent gimbal lock."""

VIEW_3D_DRAG_SENSITIVITY = 0.01
"""Mouse drag sensitivity for rotation."""

VIEW_3D_RENDER_INTERVAL = 0.1
"""Minimum time between renders (seconds)."""

VIEW_3D_STREAMING_RENDER_INTERVAL_DEFAULT = 0.25
"""Default render interval while streaming (seconds)."""

VIEW_3D_STREAMING_RENDER_INTERVAL_MIN = 0.05
"""Minimum render interval for streaming (seconds)."""

VIEW_3D_STREAMING_RENDER_INTERVAL_MAX = 2.0
"""Maximum render interval for streaming (seconds)."""

VIEW_3D_FAST_MODE_DURATION = 0.3
"""Duration to stay in fast mode after interaction (seconds)."""

VIEW_3D_MAX_SEGMENTS_FULL = 40000
"""Maximum segments to draw in full quality mode."""

VIEW_3D_MAX_SEGMENTS_INTERACTIVE = 5000
"""Maximum segments to draw during interaction."""

VIEW_3D_PREVIEW_TARGET = 1000
"""Target segment count for preview mode."""

VIEW_3D_LIGHTWEIGHT_PREVIEW_TARGET = 400
"""Target segment count for lightweight preview mode."""

VIEW_3D_FULL_PARSE_LIMIT = 20000
"""Line count threshold to use full parsing."""

# Arc step thresholds
VIEW_3D_ARC_STEP_FAST_THRESHOLD = 5000
"""Line count threshold for switching to the fast arc step."""

# Arc detail levels
VIEW_3D_ARC_STEP_DEFAULT = math.pi / 18
VIEW_3D_ARC_STEP_FAST = math.pi / 12
VIEW_3D_ARC_STEP_LARGE = math.pi / 8

VIEW_3D_DRAW_PERCENT_DEFAULT = 50
"""Default draw percent for toolpath rendering."""

VIEW_3D_POSITION_MARKER_RADIUS = 4
"""Radius of position marker circle."""

VIEW_3D_PERF_LOG_THRESHOLD = 0.05
"""Minimum duration (seconds) before logging toolpath timing."""

TOOLPATH_CANVAS_MARGIN = 20
"""Canvas margin (pixels) for toolpath views."""

TOOLPATH_OVERLAY_TEXT_MARGIN = 12
"""Overlay text margin (pixels) for toolpath views."""

TOOLPATH_ORIGIN_CROSS_SIZE = 6
"""Crosshair size (pixels) for origin marker."""

TOOLPATH_GRID_MAX_POINTS = 800
"""Maximum grid points to draw for auto-level overlay."""

TOOLPATH_GRID_POINT_RADIUS = 2
"""Radius (pixels) for auto-level grid points."""

TOOLPATH_PERFORMANCE_DEFAULT = 50.0
"""Default performance slider value (percent)."""

TOOLPATH_PERF_LIGHTWEIGHT_THRESHOLD = 40.0
"""Performance threshold below which lightweight mode is enabled."""

TOOLPATH_DRAW_PERCENT_MIN = 5
"""Minimum draw percent for toolpath rendering."""

TOOLPATH_FULL_LIMIT_DEFAULT = VIEW_3D_MAX_SEGMENTS_FULL
"""Default full render segment limit."""

TOOLPATH_FULL_LIMIT_MIN = VIEW_3D_MAX_SEGMENTS_INTERACTIVE
"""Minimum full render segment limit."""

TOOLPATH_INTERACTIVE_LIMIT_DEFAULT = VIEW_3D_MAX_SEGMENTS_INTERACTIVE
"""Default interactive render segment limit."""

TOOLPATH_INTERACTIVE_LIMIT_MIN = VIEW_3D_PREVIEW_TARGET
"""Minimum interactive render segment limit."""

TOOLPATH_ARC_DETAIL_MIN_DEG = 1.0
"""Minimum arc detail in degrees."""

TOOLPATH_ARC_DETAIL_MAX_DEG = 45.0
"""Maximum arc detail in degrees."""

TOOLPATH_ARC_DETAIL_DEFAULT_DEG = math.degrees(VIEW_3D_ARC_STEP_DEFAULT)
"""Default arc detail in degrees."""

TOOLPATH_STREAMING_RENDER_INTERVAL_DEFAULT = VIEW_3D_STREAMING_RENDER_INTERVAL_DEFAULT
"""Default streaming render interval (seconds)."""

TOOLPATH_STREAMING_RENDER_INTERVAL_MIN = VIEW_3D_STREAMING_RENDER_INTERVAL_MIN
"""Minimum streaming render interval (seconds)."""

TOOLPATH_STREAMING_RENDER_INTERVAL_MAX = VIEW_3D_STREAMING_RENDER_INTERVAL_MAX
"""Maximum streaming render interval (seconds)."""

TOOLPATH_ARC_DETAIL_REPARSE_DELAY_MS = 300
"""Delay before re-parsing toolpath after arc detail changes (ms)."""

JOYSTICK_HOLD_MIN_DISTANCE = 0.01
"""Minimum jog distance for joystick hold moves."""

# ============================================================================
# MACRO SYSTEM CONSTANTS
# ============================================================================

MACRO_PREFIXES = ("Macro-", "Maccro-")
"""Valid prefixes for macro files."""

MACRO_EXTS = ("", ".txt")
"""Valid extensions for macro files."""

MACRO_WAIT_TIMEOUT = 30.0
"""Default timeout for %wait command (seconds)."""

MACRO_WAIT_POLL_INTERVAL = 0.1
"""Polling interval for %wait command (seconds)."""

MACRO_STDEXPR = False
"""Use standard Python expressions instead of bracket notation."""

# Regular expressions
MACRO_GPAT = re.compile(r"[A-Za-z]\s*[-+]?\d+.*")
MACRO_AUXPAT = re.compile(r"^(%[A-Za-z0-9_-]+)\b *(.*)$")
MACRO_CMDPAT = re.compile(r"([A-Za-z]+)")

# ============================================================================
# AUTO-LEVEL CONSTANTS
# ============================================================================

AUTOLEVEL_SPACING_MIN = 0.01
"""Minimum spacing (mm) for auto-level grids."""

AUTOLEVEL_LARGE_MIN_AREA_DEFAULT = 10000.0
"""Default minimum area for the large auto-level preset (mm^2)."""

AUTOLEVEL_START_STATE_POLL_MS = 300
"""Polling interval (ms) for auto-level start readiness."""

# ============================================================================
# G-CODE PARSING CONSTANTS
# ============================================================================

PAREN_COMMENT_PAT = re.compile(r"\(.*?\)")
"""Pattern to match parenthesis comments."""

WORD_PAT = re.compile(r"([A-Z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
"""Pattern to match G-code words."""

RESUME_WORD_PAT = re.compile(r"([A-Z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
"""Pattern to parse G-code words for resume."""

# ============================================================================
# SETTINGS CONSTANTS
# ============================================================================

SETTINGS_FILENAME = "settings.json"
"""Filename for application settings."""

SETTINGS_BACKUP_SUFFIX = ".backup"
"""Suffix for settings backup file."""

SETTINGS_TEMP_SUFFIX = ".tmp"
"""Suffix for temporary settings file during write."""

# ============================================================================
# STREAMING CONSTANTS
# ============================================================================

BUFFER_EMIT_INTERVAL = 0.1
"""Minimum interval between buffer fill updates (seconds)."""

TX_THROUGHPUT_WINDOW = 2.0
"""Time window for throughput calculation (seconds)."""

TX_THROUGHPUT_EMIT_INTERVAL = 0.5
"""Minimum interval between throughput updates (seconds)."""

STREAM_RECONNECT_DELAY = 0.5
"""Delay before attempting reconnect (seconds)."""

WATCHDOG_RX_TIMEOUT = 5.0
"""Seconds without RX before pausing streaming."""

WATCHDOG_DISCONNECT_TIMEOUT = 10.0
"""Seconds without RX before disconnecting."""

WATCHDOG_HOMING_TIMEOUT = 180.0
"""Seconds to suspend watchdog checks after issuing a homing cycle."""

WATCHDOG_ALARM_DISCONNECT_TIMEOUT = 60.0
"""Seconds without RX before disconnecting while in alarm state."""

RX_STATUS_LOG_INTERVAL = 0.2
"""Minimum seconds between status log entries in the UI console."""

RX_OK_SUMMARY_INTERVAL = 0.5
"""Minimum seconds between OK summary log entries in the UI console."""

# ============================================================================
# TIMING CONSTANTS
# ============================================================================

THREAD_JOIN_TIMEOUT = 0.5
"""Timeout when joining worker threads (seconds)."""

SERIAL_CONNECT_DELAY = 0.25
"""Delay after opening serial port (seconds)."""

SERIAL_TIMEOUT = 0.1
"""Serial read timeout (seconds)."""

SERIAL_WRITE_TIMEOUT = 0.5
"""Serial write timeout (seconds)."""

EVENT_QUEUE_TIMEOUT = 0.01
"""Timeout for queue operations (seconds)."""

UI_POLL_INTERVAL = 0.01
"""Main UI event loop polling interval (seconds)."""

# ============================================================================
# GRBL SETTINGS DESCRIPTIONS
# ============================================================================

GRBL_SETTING_DESC: Dict[int, str] = {
    0: "Step pulse, microseconds",
    1: "Step idle delay, milliseconds",
    2: "Step port invert mask",
    3: "Direction port invert mask",
    4: "Step enable invert",
    5: "Limit pins invert",
    6: "Probe pin invert",
    10: "Status report mask",
    11: "Junction deviation, mm",
    12: "Arc tolerance, mm",
    13: "Report inches",
    20: "Soft limits enable",
    21: "Hard limits enable",
    22: "Homing cycle enable",
    23: "Homing direction invert mask",
    24: "Homing locate feed rate, mm/min",
    25: "Homing search seek rate, mm/min",
    26: "Homing switch debounce, ms",
    27: "Homing switch pull-off, mm",
    30: "Max spindle speed, RPM",
    31: "Min spindle speed, RPM",
    32: "Laser mode enable",
    100: "X steps/mm",
    101: "Y steps/mm",
    102: "Z steps/mm",
    110: "X max rate, mm/min",
    111: "Y max rate, mm/min",
    112: "Z max rate, mm/min",
    120: "X accel, mm/sec^2",
    121: "Y accel, mm/sec^2",
    122: "Z accel, mm/sec^2",
    130: "X max travel, mm",
    131: "Y max travel, mm",
    132: "Z max travel, mm",
}

GRBL_SETTING_KEYS = sorted(GRBL_SETTING_DESC.keys())
"""Sorted list of all GRBL setting IDs."""

# ============================================================================
# GRBL SETTINGS LIMITS
# ============================================================================

GRBL_SETTING_LIMITS: Dict[int, Tuple[float, float]] = {
    0: (1, 1000),      # step pulse us
    1: (0, 255),       # step idle delay
    2: (0, 255),       # step port invert
    3: (0, 255),       # dir port invert
    4: (0, 1),         # step enable invert
    5: (0, 1),         # limit pins invert
    6: (0, 1),         # probe pin invert
    10: (0, 511),      # status report mask
    11: (0, 5),        # junction deviation
    12: (0, 5),        # arc tolerance
    13: (0, 1),        # report inches
    20: (0, 1),        # soft limits
    21: (0, 1),        # hard limits
    22: (0, 1),        # homing enable
    23: (0, 255),      # homing dir invert
    24: (0, 5000),     # homing feed
    25: (0, 5000),     # homing seek
    26: (0, 255),      # homing debounce
    27: (0, 50),       # homing pull-off
    30: (0, 100000),   # max spindle speed
    31: (0, 100000),   # min spindle speed
    32: (0, 1),        # laser mode
    100: (0, 2000),    # X steps/mm
    101: (0, 2000),    # Y steps/mm
    102: (0, 2000),    # Z steps/mm
    110: (0, 200000),  # X max rate
    111: (0, 200000),  # Y max rate
    112: (0, 200000),  # Z max rate
    120: (0, 20000),   # X accel
    121: (0, 20000),   # Y accel
    122: (0, 20000),   # Z accel
    130: (0, 2000),    # X max travel
    131: (0, 2000),    # Y max travel
    132: (0, 2000),    # Z max travel
}

GRBL_NON_NUMERIC_SETTINGS: Set[int] = set()
"""Settings that are allowed to be non-numeric (currently none)."""

# ============================================================================
# UI CHOICES
# ============================================================================

ALL_STOP_CHOICES = [
    ("Soft Reset (Ctrl-X)", "reset"),
    ("Stop Stream + Reset", "stop_reset"),
]

CURRENT_LINE_CHOICES = [
    ("Processing (acked)", "acked"),
    ("Sent (queued)", "sent"),
]

# ============================================================================
# COLOR CONSTANTS
# ============================================================================

COLOR_RAPID = "#8a8a8a"
"""Color for rapid moves in 3D view."""

COLOR_FEED = "#2c6dd2"
"""Color for feed moves in 3D view."""

COLOR_ARC = "#2aa876"
"""Color for arc moves in 3D view."""

COLOR_POSITION_MARKER = "#d64545"
"""Color for current position marker."""

# G-code viewer highlight colors (light/pastel for readability)
COLOR_GCODE_SENT = "#e5efff"
"""Background color for sent G-code lines."""

COLOR_GCODE_ACKED = "#e6f7ed"
"""Background color for acknowledged G-code lines."""

COLOR_GCODE_CURRENT = "#fff4d8"
"""Background color for current G-code line."""

COLOR_GCODE_TEXT = "#111111"
"""Text color for G-code viewer."""

COLOR_GCODE_BG = "#ffffff"
"""Background color for G-code viewer."""

# ============================================================================
# DEFAULT SPINDLE RPM
# ============================================================================

DEFAULT_SPINDLE_RPM = 12000
"""Default spindle RPM when turning on spindle."""

# ============================================================================
# ERROR MESSAGES
# ============================================================================

ERROR_PYSERIAL_MISSING = (
    "pyserial is required to connect to GRBL. "
    "Install pyserial (pip install pyserial) and restart the application."
)

ERROR_NOT_CONNECTED = "Not connected to GRBL"

ERROR_INVALID_FEED_RATE = "Feed rate must be positive, got {}"

ERROR_INVALID_UNIT_MODE = "Invalid unit mode: {}"

ERROR_MACRO_BUSY = "Another macro is running."
