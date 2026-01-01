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

# ============================================================================
# GRBL BUFFER MANAGEMENT
# ============================================================================

RX_BUFFER_SIZE = 128
"""GRBL RX buffer size in bytes."""

RX_BUFFER_SAFETY = 8
"""Safety margin to prevent buffer overflow."""

RX_BUFFER_WINDOW = RX_BUFFER_SIZE - RX_BUFFER_SAFETY
"""Usable buffer window for streaming."""

MAX_LINE_LENGTH = 128
"""Maximum G-code line length (including newline)."""

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

GCODE_VIEWER_CHUNK_SIZE_SMALL = 200
"""Chunk size for small files (<1000 lines)."""

GCODE_VIEWER_CHUNK_SIZE_MEDIUM = 500
"""Chunk size for medium files (1000-10000 lines)."""

GCODE_VIEWER_CHUNK_SIZE_LARGE = 1000
"""Chunk size for large files (>10000 lines)."""

GCODE_VIEWER_SMALL_FILE_THRESHOLD = 1000
"""Line count threshold for small files."""

GCODE_VIEWER_LARGE_FILE_THRESHOLD = 10000
"""Line count threshold for large files."""

CLEAR_ICON = "X"
"""Icon/text for clear buttons."""

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

VIEW_3D_FAST_MODE_DURATION = 0.3
"""Duration to stay in fast mode after interaction (seconds)."""

VIEW_3D_MAX_SEGMENTS_FULL = 40000
"""Maximum segments to draw in full quality mode."""

VIEW_3D_MAX_SEGMENTS_INTERACTIVE = 5000
"""Maximum segments to draw during interaction."""

VIEW_3D_PREVIEW_TARGET = 1000
"""Target segment count for preview mode."""

VIEW_3D_FULL_PARSE_LIMIT = 20000
"""Line count threshold to use full parsing."""

# Arc detail levels
VIEW_3D_ARC_STEP_DEFAULT = math.pi / 18
VIEW_3D_ARC_STEP_FAST = math.pi / 12
VIEW_3D_ARC_STEP_LARGE = math.pi / 8

VIEW_3D_POSITION_MARKER_RADIUS = 4
"""Radius of position marker circle."""

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
MACRO_AUXPAT = re.compile(r"^(%[A-Za-z0-9]+)\b *(.*)$")
MACRO_CMDPAT = re.compile(r"([A-Za-z]+)")

# ============================================================================
# G-CODE PARSING CONSTANTS
# ============================================================================

PAREN_COMMENT_PAT = re.compile(r"\(.*?\)")
"""Pattern to match parenthesis comments."""

WORD_PAT = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")
"""Pattern to match G-code words."""

RESUME_WORD_PAT = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")
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
