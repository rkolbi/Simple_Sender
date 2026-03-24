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

import re
from typing import Dict, Tuple, Set
from simple_sender.config.defaults import DEFAULT_APP_CONFIG
from simple_sender.utils.platform_detect import detect_raspberry_pi

# ============================================================================
# SERIAL COMMUNICATION CONSTANTS
# ============================================================================

BAUD_DEFAULT = 115200
"""Default baud rate for GRBL serial communication."""

STATUS_POLL_DEFAULT = DEFAULT_APP_CONFIG.status_polling.default_interval
"""Default interval (seconds) between status queries."""

STATUS_POLL_IDLE = DEFAULT_APP_CONFIG.status_polling.idle_interval
"""Status poll interval when machine is idle."""

STATUS_POLL_RUNNING = DEFAULT_APP_CONFIG.status_polling.running_interval
"""Status poll interval when machine is running."""

STATUS_POLL_INTERVAL_MIN = DEFAULT_APP_CONFIG.status_polling.min_interval
"""Minimum allowed status poll interval (seconds)."""

STATUS_QUERY_FAILURE_LIMIT_DEFAULT = (
    DEFAULT_APP_CONFIG.status_polling.failure_limit_default
)
"""Default status query failure limit before disconnect."""

STATUS_QUERY_FAILURE_LIMIT_MIN = DEFAULT_APP_CONFIG.status_polling.failure_limit_min
"""Minimum allowed status query failure limit."""

STATUS_QUERY_FAILURE_LIMIT_MAX = DEFAULT_APP_CONFIG.status_polling.failure_limit_max
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
RT_SO_PLUS_10 = b"\x9a"
RT_SO_MINUS_10 = b"\x9b"

# ============================================================================
# UI CONSTANTS
# ============================================================================

MAX_CONSOLE_LINES = 5000
"""Maximum number of lines to keep in console."""

CONSOLE_BATCH_DELAY_MS = 50
"""Milliseconds to wait before flushing batched console updates."""

CONSOLE_PENDING_BATCH_MAX = 2000
"""Maximum pending console entries buffered before forcing a full re-render."""

CONSOLE_MAX_BUFFER_BYTES = 1_500_000
"""Maximum estimated bytes retained by in-memory console history."""

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

UI_QUEUE_DRAIN_EVENT_LIMIT = 100
"""Maximum number of UI events drained per UI-queue tick."""

UI_QUEUE_DRAIN_TIME_BUDGET_MS = 8.0
"""Soft time budget (ms) for each UI-queue drain tick."""

UI_QUEUE_DRAIN_STALL_BUDGET_MS = 16.0
"""Threshold (ms) above which a UI-queue drain tick is considered a stall."""

UI_QUEUE_MAINTENANCE_INTERVAL_S = 0.25
"""Maintenance cadence while UI events are actively flowing."""

UI_QUEUE_IDLE_MAINTENANCE_INTERVAL_S = 1.25
"""Maintenance cadence while the UI queue is idle."""

UI_QUEUE_QUIET_IDLE_MAINTENANCE_INTERVAL_S = 4.0
"""Maintenance cadence during sustained connected-and-idle quiet runtime."""

UI_QUEUE_RECONNECT_CHECK_INTERVAL_S = 0.25
"""Auto-reconnect check cadence while UI events are actively flowing."""

UI_QUEUE_IDLE_RECONNECT_CHECK_INTERVAL_S = 1.25
"""Auto-reconnect check cadence while the UI queue is idle."""

GRBL_SETTINGS_WRITE_DELAY = 0.05
"""Delay between sending GRBL settings updates (seconds)."""

GCODE_LIVE_WINDOW_PAST_LINES = 500
"""Number of acknowledged lines retained in the live G-code Past window."""

GCODE_LIVE_WINDOW_NEXT_LINES = 500
"""Maximum number of pending lines retained in the live G-code Next window."""

GCODE_LIVE_WINDOW_LOOKAHEAD_LINES = 10
"""Number of upcoming lines rendered in the live G-code Look Ahead section."""

GCODE_LIVE_WINDOW_REFRESH_MS = 125
"""Maximum live G-code redraw cadence (ms) to avoid per-line UI churn."""

GCODE_STREAMING_SIZE_THRESHOLD = 50 * 1024 * 1024
"""File size (bytes) above which streaming mode is used."""

GCODE_ULTRA_LARGE_SIZE_THRESHOLD = 200 * 1024 * 1024
"""File size (bytes) above which ultra-large fast-load safeguards are forced."""

GCODE_ULTRA_LARGE_REQUIRED_FREE_MULTIPLIER = 3
"""Required free-space multiplier versus source file size for ultra-large loads."""

GCODE_ULTRA_LARGE_REQUIRED_FREE_MARGIN_BYTES = 256 * 1024 * 1024
"""Additional free-space margin required for ultra-large temp/working files."""

GCODE_STREAMING_LINE_THRESHOLD = DEFAULT_APP_CONFIG.gcode_cache.streaming_line_threshold
"""Cleaned line count above which streaming mode is used."""

GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT = DEFAULT_APP_CONFIG.gcode_cache.max_lines_default
"""Maximum cleaned lines retained in RAM for non-sample UI workflows."""

GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER = (
    DEFAULT_APP_CONFIG.gcode_cache.max_lines_low_power
)
"""Lower in-RAM full-line cap for low-power profiles (Pi-class hardware)."""

GCODE_IN_MEMORY_SEND_CACHE_THRESHOLD = (
    DEFAULT_APP_CONFIG.gcode_cache.in_memory_send_cache_threshold
)
"""Maximum line count for precomputing in-memory streaming send caches."""

GCODE_LOAD_PROGRESS_INTERVAL = 0.25
"""Minimum seconds between progress updates while loading/validating G-code."""

TEMP_FILE_BUFFER_SIZE = 64 * 1024
"""Buffered write size for temp G-code/autolevel files."""

GCODE_STATS_DEBOUNCE_MS = 75
"""Debounce window (ms) before launching background G-code stats calculation."""

# Keep only a small number of recent stats entries so repeated tab switches stay
# responsive without pinning many heavy parse results in memory.
GCODE_STATS_CACHE_MAX_ENTRIES = 16
"""Maximum cached G-code stats entries kept in memory."""

GCODE_STATS_FULL_SCAN_DELAY_THRESHOLD_LINES = 50_000
"""Line-count threshold for deferring heavy file-backed full-scan estimate work."""

GCODE_STATS_FULL_SCAN_DELAY_LARGE_MS = 1200
"""Launch delay (ms) for heavy file-backed full-scan estimate work."""

GCODE_STATS_FULL_SCAN_THROTTLE_LINES = 1024
"""Yield cadence (lines) for file-backed full-scan estimate parsing."""

GCODE_STATS_FULL_SCAN_THROTTLE_SLEEP_S = 0.001
"""Sleep duration (s) for periodic full-scan estimate yields."""

GCODE_STATS_FULL_SCAN_BACKGROUND_TAB_SLEEP_S = 0.004
"""Extra sleep duration (s) while parsing estimates away from the G-code tab."""

GCODE_STATS_SAMPLE_QUICK_MAX_LINES = 1000
"""Maximum sampled lines used for the immediate post-load baseline estimate pass."""

GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES = 2000
"""Minimum sampled executable lines before trusting high-ratio sampled scaling."""

GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES = 500
"""Minimum sampled motion lines before trusting sampled-motion scaling."""

GCODE_ESTIMATE_SAMPLE_MAX_SCALE = 64.0
"""Maximum allowed sampled estimate scale multiplier."""

GCODE_STATS_COOPERATIVE_YIELD_LINES = 512
"""Yield cadence (lines) for cooperative stats parsing to reduce UI-thread GIL contention."""

GCODE_STATS_COOPERATIVE_YIELD_SLEEP_S = 0.0
"""Yield sleep duration (s) for cooperative stats parsing (`0.0` yields the GIL)."""

GCODE_STREAMING_SAMPLE_LINES = 2000
"""Sample lines shown when streaming from disk."""

GCODE_PREP_SAMPLE_HEAD_LINES = 1200
"""Number of leading cleaned lines retained for sampled prepare-time analysis."""

GCODE_PREP_SAMPLE_TAIL_LINES = 600
"""Number of trailing cleaned lines retained for sampled prepare-time analysis."""

# Sample densely enough to preserve job-shape context while still capping
# prepare-time work on very large files.
GCODE_PREP_SAMPLE_INTERVAL_LINES = 250
"""Periodic sampling stride (cleaned lines) for prepare-time analysis."""

GCODE_PREP_SAMPLE_MAX_LINES = 5000
"""Hard cap on sampled lines retained for prepare-time top-view/stats analysis."""

GCODE_PREP_FAST_SCAN_MAX_LINES_DEFAULT = (
    DEFAULT_APP_CONFIG.gcode_cache.prep_fast_scan_max_lines_default
)
"""Default max raw lines scanned in fast prepare before background tasks."""

GCODE_PREP_FAST_SCAN_MAX_LINES_LOW_POWER = (
    DEFAULT_APP_CONFIG.gcode_cache.prep_fast_scan_max_lines_low_power
)
"""Low-power max raw lines scanned in fast prepare before background tasks."""

GCODE_PREP_STATS_SAMPLE_THRESHOLD_LINES = 100_000
"""Line-count threshold above which prepare-time stats use sampled lines."""

GCODE_OFFSET_INDEX_MAX_FILE_BYTES = 32 * 1024 * 1024
"""Maximum file size for building a full line-offset index during load."""

GCODE_OFFSET_INDEX_MAX_LINES = 250_000
"""Maximum cleaned-line estimate for immediate full offset indexing policy."""

GCODE_OFFSET_INDEX_SPARSE_MIN_LINES = 40_000
"""Minimum cleaned-line estimate before sparse index mode is selected."""

# A 256-line stride keeps resume/seek lookups reasonably fast without building a
# full offset table for larger jobs.
GCODE_OFFSET_INDEX_SPARSE_STRIDE_LINES = 256
"""Stride (cleaned lines) for sparse offset anchors."""

CLEAR_ICON = "X"
"""Icon/text for clear buttons."""

TOOLTIP_DELAY_MS = 1000
"""Default tooltip delay (ms)."""

TOOLTIP_TIMEOUT_DEFAULT = 10.0
"""Default tooltip display duration (seconds)."""

NOTEBOOK_TOOLTIP_POLL_INTERVAL_ACTIVE_MS = 120
"""Notebook-tab tooltip poll interval while a tooltip is active/pending."""

NOTEBOOK_TOOLTIP_POLL_INTERVAL_IDLE_MS = 450
"""Notebook-tab tooltip poll interval while idle."""

STOP_SIGN_CUT_RATIO = 0.29289321881345254
"""Cut ratio for the stop-sign octagon geometry."""

# Input bindings
JOYSTICK_POLL_INTERVAL_MS = 50
"""Joystick polling interval (ms)."""

JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS = 200
"""Maximum joystick polling interval (ms) when inputs are idle."""

JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS = 10
"""Per-tick joystick poll backoff step (ms) while idle."""

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

JOYSTICK_HOLD_DEADMAN_TIMEOUT_MS = 200
"""Maximum elapsed time between hold-release polls before forced jog stop."""

JOYSTICK_HOLD_FEED_HOLD_FALLBACK_DELAY_MS = 120
"""Delay before issuing a secondary jog-cancel fallback after release (ms)."""

JOYSTICK_HOLD_MAX_ELAPSED_MULTIPLIER = 3.0
"""Maximum multiple of the repeat interval used to compute hold jog distance."""

if detect_raspberry_pi():
    JOYSTICK_POLL_INTERVAL_MS = 20
    JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS = 160
    JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS = 8
    JOYSTICK_HOLD_REPEAT_MS = 30
    JOYSTICK_HOLD_POLL_INTERVAL_MS = 10
    JOYSTICK_HOLD_MISS_LIMIT = 5

JOYSTICK_HOLD_DEFINITIONS = [
    ("X-", "jog_hold_x_minus", "X", -1),
    ("X+", "jog_hold_x_plus", "X", 1),
    ("Y-", "jog_hold_y_minus", "Y", -1),
    ("Y+", "jog_hold_y_plus", "Y", 1),
    ("Z-", "jog_hold_z_minus", "Z", -1),
    ("Z+", "jog_hold_z_plus", "Z", 1),
]

# Jog DRO smoothing / interpolation mode
JOG_DRO_SMOOTHING_OFF = "off"
JOG_DRO_SMOOTHING_UI_JOG_ONLY = "ui_jog_only"
JOG_DRO_SMOOTHING_ALL_JOG = "all_jog"
JOG_DRO_SMOOTHING_CHOICES = (
    JOG_DRO_SMOOTHING_OFF,
    JOG_DRO_SMOOTHING_UI_JOG_ONLY,
    JOG_DRO_SMOOTHING_ALL_JOG,
)
"""Definitions for joystick hold bindings (label, id, axis, direction)."""

# Jogging presets
JOG_STEP_XY_VALUES = (
    0.1,
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    200.0,
    300.0,
    400.0,
    500.0,
    600.0,
    700.0,
    800.0,
    900.0,
    1000.0,
)
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

JOYSTICK_HOLD_MIN_DISTANCE = 0.01
"""Minimum jog distance for joystick hold moves."""

# ============================================================================
# MACRO SYSTEM CONSTANTS
# ============================================================================

MACRO_PREFIXES = ("Macro-",)
"""Valid prefixes for macro files."""

MACRO_EXTS = ("", ".txt")
"""Valid extensions for macro files."""

MACRO_WAIT_TIMEOUT = 30.0
"""Default timeout for %wait command (seconds)."""

MACRO_PROMPT_TIMEOUT = 120.0
"""Maximum seconds to wait for a macro prompt response before canceling."""

MACRO_WAIT_POLL_INTERVAL = 0.1
"""Polling interval for %wait command (seconds)."""

MACRO_LINE_TIMEOUT = 0.0
"""Default maximum execution time for a single macro line (seconds, 0 disables)."""

MACRO_TOTAL_TIMEOUT = 0.0
"""Default maximum execution time for an entire macro run (seconds, 0 disables)."""

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

MANUAL_COMMAND_QUEUE_MAXSIZE = 256
"""Maximum queued manual/immediate commands before backpressure drops new input."""

MANUAL_QUEUE_DROP_NOTICE_INTERVAL = 1.0
"""Minimum seconds between aggregated manual-queue drop notices."""

STREAM_RECONNECT_DELAY = 0.5
"""Delay before attempting reconnect (seconds)."""

WATCHDOG_RX_TIMEOUT = 5.0
"""Seconds without RX before pausing streaming."""

WATCHDOG_DISCONNECT_TIMEOUT = 10.0
"""Seconds without RX before disconnecting."""

WATCHDOG_HOMING_TIMEOUT = 180.0
"""Seconds to suspend watchdog checks after issuing a homing cycle."""

WATCHDOG_SETTINGS_DUMP_TIMEOUT = 30.0
"""Seconds to suspend watchdog checks while processing a GRBL ``$$`` settings dump."""

WATCHDOG_READY_ARM_GRACE = 30.0
"""Idle-only grace after GRBL becomes ready before watchdog disconnect logic is enforced."""

WATCHDOG_ALARM_DISCONNECT_TIMEOUT = 60.0
"""Seconds without RX before disconnecting while in alarm state."""

GRBL_STARTUP_TIMEOUT = 6.0
"""Seconds to wait for GRBL banner/status before disconnecting."""

RX_STATUS_LOG_INTERVAL = 1.0
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

TX_LOOP_IDLE_WAIT_S = 0.2
"""Idle wait for the TX loop when no stream/manual work is pending (seconds)."""

UI_POLL_INTERVAL = 0.01
"""Main UI event loop polling interval (seconds)."""

# Bound accessory work so misbehaving smart-plug traffic cannot grow an
# unbounded background queue.
KASA_TASK_QUEUE_MAXSIZE = 256
"""Maximum queued Kasa background tasks before new tasks are dropped."""

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
    0: (1, 1000),  # step pulse us
    1: (0, 255),  # step idle delay
    2: (0, 255),  # step port invert
    3: (0, 255),  # dir port invert
    4: (0, 1),  # step enable invert
    5: (0, 1),  # limit pins invert
    6: (0, 1),  # probe pin invert
    10: (0, 511),  # status report mask
    11: (0, 5),  # junction deviation
    12: (0, 5),  # arc tolerance
    13: (0, 1),  # report inches
    20: (0, 1),  # soft limits
    21: (0, 1),  # hard limits
    22: (0, 1),  # homing enable
    23: (0, 255),  # homing dir invert
    24: (0, 5000),  # homing feed
    25: (0, 5000),  # homing seek
    26: (0, 255),  # homing debounce
    27: (0, 50),  # homing pull-off
    30: (0, 100000),  # max spindle speed
    31: (0, 100000),  # min spindle speed
    32: (0, 1),  # laser mode
    100: (0, 2000),  # X steps/mm
    101: (0, 2000),  # Y steps/mm
    102: (0, 2000),  # Z steps/mm
    110: (0, 200000),  # X max rate
    111: (0, 200000),  # Y max rate
    112: (0, 200000),  # Z max rate
    120: (0, 20000),  # X accel
    121: (0, 20000),  # Y accel
    122: (0, 20000),  # Z accel
    130: (0, 2000),  # X max travel
    131: (0, 2000),  # Y max travel
    132: (0, 2000),  # Z max travel
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
    ("Machine (status/planner)", "machine"),
    ("Processing (acked)", "acked"),
    ("Sent (queued)", "sent"),
]

# ============================================================================
# COLOR CONSTANTS
# ============================================================================

COLOR_RAPID = "#8a8a8a"
"""Color for rapid moves."""

COLOR_FEED = "#2c6dd2"
"""Color for feed moves."""

COLOR_ARC = "#2aa876"
"""Color for arc moves."""

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
