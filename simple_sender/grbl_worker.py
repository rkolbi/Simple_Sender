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

"""GRBL serial communication worker.

This module handles all serial communication with GRBL controllers,
including connection management, G-code streaming, and status polling.
"""

import logging
import os
import queue
import re
import threading
import time
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import Any, Optional, Sequence, Tuple, TYPE_CHECKING, TypeAlias

from .types import ManualPendingItem, StreamPendingItem, StreamQueueItem
from .grbl_worker_commands import GrblWorkerCommandMixin
from .grbl_worker_connection import (
    GrblWorkerConnectionMixin,
    _serial_exception_type,
    _serial_timeout_exception_type,
)
from .grbl_worker_status import GrblWorkerStatusMixin
from .grbl_worker_streaming import GrblWorkerStreamingMixin
from .utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error

try:
    import serial
    from serial.tools import list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    list_ports = None
    SERIAL_AVAILABLE = False

if TYPE_CHECKING:
    from serial import Serial as _Serial
    from serial import SerialException as _SerialException
    from serial import SerialTimeoutException as _SerialTimeoutException
    SerialType: TypeAlias = _Serial
    SerialExceptionType: TypeAlias = _SerialException
    SerialTimeoutExceptionType: TypeAlias = _SerialTimeoutException
else:
    SerialType: TypeAlias = Any
    SerialExceptionType: TypeAlias = Exception
    SerialTimeoutExceptionType: TypeAlias = Exception

from .utils.constants import (
    RX_BUFFER_SIZE,
    BUFFER_EMIT_INTERVAL,
    TX_THROUGHPUT_WINDOW,
    TX_THROUGHPUT_EMIT_INTERVAL,
    TX_LOOP_IDLE_WAIT_S,
    MANUAL_COMMAND_QUEUE_MAXSIZE,
    MANUAL_QUEUE_DROP_NOTICE_INTERVAL,
    STATUS_POLL_DEFAULT,
    RT_RESUME,
    RT_JOG_CANCEL,
    THREAD_JOIN_TIMEOUT,
    WATCHDOG_HOMING_TIMEOUT,
    WATCHDOG_SETTINGS_DUMP_TIMEOUT,
    GCODE_LIVE_WINDOW_PAST_LINES,
    GCODE_LIVE_WINDOW_NEXT_LINES,
    GCODE_LIVE_WINDOW_REFRESH_MS,
)
from .utils.exceptions import SerialWriteError

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_RX_LOGGER = None
_RX_LOGGER_LOCK = threading.Lock()
_REEXPORTED_GRBL_ERROR_HELPERS = (annotate_grbl_alarm, annotate_grbl_error)
_REEXPORTED_REALTIME_CONSTANTS = (RT_RESUME, RT_JOG_CANCEL)
TX_LINE_RATE_WINDOW_S = 5.0
QUEUE_DEPTH_SNAPSHOT_MAX = 64
QUEUE_DEPTH_SNAPSHOT_INTERVAL_S = 0.2
SERIAL_ACTIVITY_HISTORY_MAX = 20000


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _format_realtime_for_log(command: bytes) -> str:
    if not command:
        return ""
    parts: list[str] = []
    for value in command:
        if 32 <= value <= 126:
            parts.append(chr(value))
        else:
            parts.append(f"0x{value:02X}")
    return " ".join(parts)


def _env_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value <= 0:
        return float(default)
    return float(value)


def _get_rx_logger():
    global _RX_LOGGER
    if _RX_LOGGER is not None:
        return _RX_LOGGER or None
    with _RX_LOGGER_LOCK:
        if _RX_LOGGER is not None:
            return _RX_LOGGER or None
        rx_logger = logging.getLogger("simple_sender.serial")
        path = os.getenv("SIMPLE_SENDER_RX_LOG_PATH")
        if path:
            try:
                max_bytes = int(os.getenv("SIMPLE_SENDER_RX_LOG_MAX_BYTES", "2097152"))
            except (TypeError, ValueError):
                max_bytes = 2097152
            try:
                backup_count = int(os.getenv("SIMPLE_SENDER_RX_LOG_BACKUPS", "5"))
            except (TypeError, ValueError):
                backup_count = 5
            try:
                handler_path = os.path.abspath(path)
                has_handler = False
                for handler in rx_logger.handlers:
                    if isinstance(handler, RotatingFileHandler):
                        if os.path.abspath(handler.baseFilename) == handler_path:
                            has_handler = True
                            break
                if not has_handler:
                    handler = RotatingFileHandler(
                        handler_path,
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding="utf-8",
                    )
                    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                    handler.setLevel(logging.INFO)
                    handler.set_name("simple_sender_rx_override")
                    rx_logger.addHandler(handler)
            except Exception as exc:
                logger.warning("Failed to initialize RX log file: %s", exc)
        _RX_LOGGER = rx_logger
        return rx_logger

_PAUSE_MCODE_MAP = {
    "0": "M0",
    "00": "M0",
    "1": "M1",
    "01": "M1",
    "6": "M6",
    "06": "M6",
}
_PAUSE_MCODE_PAT = re.compile(r"(?<![0-9])M(0|00|1|01|6|06)(?![0-9])")
_SANITIZE_TOKEN_PAT = re.compile(r"[A-Za-z][+-]?(?:\d+\.?\d*|\.\d+)", re.ASCII)
_DRY_RUN_M_CODES = {3, 4, 5, 6, 7, 8, 9}

class GrblWorker(
    GrblWorkerConnectionMixin,
    GrblWorkerCommandMixin,
    GrblWorkerStreamingMixin,
    GrblWorkerStatusMixin,
):
    """Manages serial communication with GRBL controller.
    
    This class handles:
    - Connection and disconnection
    - G-code streaming with buffer management
    - Status polling
    - Real-time command execution

    Thread lifecycle:
    - Threads (RX/TX/Status) are created and started in connect().
    - Threads exit when _stop_evt is set, or on serial errors via _signal_disconnect().
    - disconnect() sets _stop_evt, closes the serial port, joins threads, and clears refs.
    - Threads are daemon threads; disconnect() is still the canonical cleanup path.
    
    Thread-safe and can be used as a context manager for automatic cleanup.
    
    Example:
        with GrblWorker(ui_queue) as worker:
            worker.connect('COM3')
            worker.load_gcode(lines)
            worker.start_stream()
    """
    
    def __init__(self, ui_event_q: queue.Queue):
        """Initialize GRBL worker.
        
        Args:
            ui_event_q: Queue for sending events to the UI thread
        """
        self.ui_q = ui_event_q
        self.ser: Optional[SerialType] = None
        self._rx_logger = _get_rx_logger()
        
        # Worker threads
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        
        # Buffer management
        self._last_buffer_emit: Optional[Tuple[int, int, int]] = None
        self._last_buffer_emit_ts = 0.0
        
        # Streaming state
        self._gcode: Sequence[str] = []
        self._gcode_payload_cache: Sequence[bytes | None] | None = None
        self._gcode_pause_reason_cache: Sequence[str | None] | None = None
        self._gcode_spindle_state_cache: Sequence[bool | None] | None = None
        self._streaming = False
        self._paused = False
        self._send_index = 0  # next index to send
        self._ack_index = -1  # last acked index
        self._ack_byte_offset = 0
        self._stream_file_size_bytes = 0
        self._stream_buf_used = 0
        self._stream_line_queue: deque[StreamQueueItem] = deque()
        self._stream_pending_item: StreamPendingItem | None = None
        self._manual_pending_item: ManualPendingItem | None = None
        self._live_acked_ring: deque[tuple[int, str]] = deque(
            maxlen=max(1, int(GCODE_LIVE_WINDOW_PAST_LINES))
        )
        self._live_current_acked: tuple[int, str] | None = None
        self._live_pending_window: deque[tuple[int, str]] = deque(
            maxlen=max(1, int(GCODE_LIVE_WINDOW_NEXT_LINES))
        )
        self._live_window_refresh_s = max(
            0.02, float(GCODE_LIVE_WINDOW_REFRESH_MS) / 1000.0
        )
        self._live_window_last_emit_ts = 0.0
        self._live_window_dirty = False
        self._last_manual_source: str | None = None
        self._settings_dump_active = False
        self._settings_dump_seen = False
        self._settings_dump_started_ts = 0.0
        self._pause_after_idx: Optional[int] = None
        self._pause_after_reason: str | None = None
        self._stream_tool_change_pending: StreamPendingItem | None = None
        self._stream_tool_change_name: str = ""
        self._stream_tool_change_active = False
        self._resume_preamble: deque[str] = deque()
        self._rx_window = RX_BUFFER_SIZE
        self._stream_token = 0
        self._abort_writes = threading.Event()
        self._gcode_name: str | None = None
        
        # Throughput tracking
        self._tx_bytes_window: deque[Tuple[float, int]] = deque()
        self._last_tx_emit_ts = 0.0
        self._tx_line_ts_window: deque[float] = deque()
        self._tx_lines_per_sec = 0.0
        self._ok_latency_ms_last = 0.0
        self._ok_latency_ms_avg = 0.0
        self._ok_latency_sample_count = 0
        self._tx_loop_cycles = 0
        self._tx_loop_idle_cycles = 0
        self._tx_loop_active_cycles = 0
        self._tx_loop_idle_wait_total_s = 0.0
        self._queue_depth_snapshots: deque[tuple[float, int, int, int]] = deque(
            maxlen=QUEUE_DEPTH_SNAPSHOT_MAX
        )
        self._last_queue_depth_snapshot_ts = 0.0
        self._serial_activity_history: deque[tuple[float, str, str]] = deque(
            maxlen=SERIAL_ACTIVITY_HISTORY_MAX
        )
        
        # Command queue
        self._outgoing_q: queue.Queue[str] = queue.Queue(maxsize=MANUAL_COMMAND_QUEUE_MAXSIZE)
        self._manual_source_queue: deque[str | None] = deque()
        self._purge_jog_queue = threading.Event()
        self._manual_queue_drop_count = 0
        self._manual_queue_drop_total = 0
        self._manual_queue_last_drop_notice_ts = 0.0
        
        # Thread synchronization
        self._stream_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._status_interval_lock = threading.Lock()
        self._tx_activity_evt = threading.Event()
        self._status_interval_changed_evt = threading.Event()
        
        # State flags
        self._status_poll_interval = STATUS_POLL_DEFAULT
        self._last_status_state_token = ""
        self._manual_motion_status_grace_until_ts = 0.0
        self._manual_motion_status_query_last_ts = 0.0
        self._manual_motion_status_query_count = 0
        self._manual_motion_status_query_interval_avg_ms = 0.0
        self._manual_motion_status_query_interval_max_ms = 0.0
        self._manual_motion_status_rx_last_ts = 0.0
        self._manual_motion_status_rx_count = 0
        self._manual_motion_status_rx_interval_avg_ms = 0.0
        self._manual_motion_status_rx_interval_max_ms = 0.0
        self._manual_motion_status_session_active = False
        self._manual_motion_status_session_count = 0
        self._manual_motion_status_session_start_ts = 0.0
        self._manual_motion_status_session_last_start_ts = 0.0
        self._manual_motion_status_session_last_end_ts = 0.0
        self._manual_motion_status_session_last_change_ts = 0.0
        self._manual_motion_status_session_last_source = ""
        self._status_wait_sample_count = 0
        self._status_wait_requested_avg_s = 0.0
        self._status_wait_actual_avg_s = 0.0
        self._status_wait_overshoot_max_ms = 0.0
        self._status_wait_overshoot_recent_max_ms = 0.0
        self._status_wait_overshoot_recent_window_s = 60.0
        self._status_wait_last_reason = ""
        self._status_wait_last_requested_s = 0.0
        self._status_wait_last_actual_s = 0.0
        self._status_wait_last_overshoot_ms = 0.0
        self._status_wait_reason_counts: dict[str, int] = {}
        self._status_wait_trace: deque[dict[str, Any]] = deque(maxlen=120)
        self._jog_cancel_inflight = False
        self._jog_cancel_last_sent_ts = 0.0
        self._jog_cancel_retry_timeout_s = 1.5
        self._jog_cancel_debounce_s = 0.6
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._status_query_failure_limit = 3
        self._status_query_backoff_base = 0.2
        self._status_query_backoff_max = 2.0
        self._dry_run_sanitize = False
        self._last_rx_ts = time.time()
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._watchdog_ready_armed = False
        self._watchdog_ready_ts = 0.0
        self._homing_watchdog_enabled = True
        self._homing_watchdog_timeout = WATCHDOG_HOMING_TIMEOUT
        self._settings_dump_watchdog_timeout = WATCHDOG_SETTINGS_DUMP_TIMEOUT
        self._settings_dump_watchdog_max_ignore_s = max(
            float(WATCHDOG_SETTINGS_DUMP_TIMEOUT),
            90.0,
        )
        self._connect_started_ts = 0.0
        self._tx_loop_idle_wait_s = float(TX_LOOP_IDLE_WAIT_S)
        # Throttle high-frequency serial debug lines so logging I/O does not
        # steal CPU on low-power deployments.
        self._rx_status_log_interval_s = _env_positive_float(
            "SIMPLE_SENDER_RX_STATUS_LOG_INTERVAL_S",
            1.0,
        )
        self._rx_idle_status_log_interval_s = _env_positive_float(
            "SIMPLE_SENDER_RX_IDLE_STATUS_LOG_INTERVAL_S",
            4.0,
        )
        self._tx_status_query_log_interval_s = _env_positive_float(
            "SIMPLE_SENDER_TX_STATUS_QUERY_LOG_INTERVAL_S",
            1.0,
        )
        self._tx_idle_status_query_log_interval_s = _env_positive_float(
            "SIMPLE_SENDER_TX_IDLE_STATUS_QUERY_LOG_INTERVAL_S",
            4.0,
        )
        self._last_rx_status_log_ts = 0.0
        self._last_tx_status_query_log_ts = 0.0
        self._ui_rx_log_enabled = True

    def _serial_module(self):
        return serial

    def _serial_available(self) -> bool:
        return bool(SERIAL_AVAILABLE)

    def _list_ports_provider(self):
        return list_ports

    def _thread_join_timeout(self) -> float:
        return float(THREAD_JOIN_TIMEOUT)

    def _threading_module(self):
        return threading

    def _time_module(self):
        return time

    def _should_log_rx_line(self, line: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        lower = text.lower()
        if lower == "ok":
            # "ok" can be very high frequency while streaming.
            return False
        if text.startswith("<") and text.endswith(">"):
            interval_s = self._rx_status_log_interval_s
            manual_motion_active = False
            checker = getattr(self, "_manual_motion_status_active", None)
            if callable(checker):
                try:
                    manual_motion_active = bool(checker())
                except Exception:
                    manual_motion_active = False
            if manual_motion_active:
                interval_s = min(interval_s, 0.1)
            elif lower.startswith("<idle"):
                interval_s = max(interval_s, self._rx_idle_status_log_interval_s)
            now = time.monotonic()
            if (now - self._last_rx_status_log_ts) < interval_s:
                return False
            self._last_rx_status_log_ts = now
        return True

    @staticmethod
    def _is_critical_ui_log_rx(line: str) -> bool:
        if not line:
            return False
        upper = line.upper()
        if "ALARM" in upper or "ERROR" in upper:
            return True
        if upper.startswith("GRBL"):
            return True
        if line.startswith("[GC:") or line.startswith("[PRB:") or line.startswith("$13="):
            return True
        if line.startswith("$") and "=" in line:
            return True
        if "[MSG" in upper:
            return True
        return False

    def set_ui_rx_logging(self, enabled: bool) -> None:
        self._ui_rx_log_enabled = bool(enabled)

    def _should_forward_log_rx_line(self, line: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        if self._ui_rx_log_enabled:
            return True
        return self._is_critical_ui_log_rx(text)

    def _log_rx_line(self, line: str) -> None:
        if not line or (not self._should_log_rx_line(line)):
            return
        self._record_serial_activity("RX", line)
        rx_logger = self._rx_logger
        if not rx_logger:
            return
        try:
            rx_logger.info("RX %s", line)
        except Exception as exc:
            _log_suppressed("Failed writing RX serial line to debug logger", exc)

    def _should_log_tx_line(self, line: str) -> bool:
        text = str(line or "").strip().upper()
        if not text:
            return False
        if text == "RT ?":
            interval_s = self._tx_status_query_log_interval_s
            manual_motion_active = False
            checker = getattr(self, "_manual_motion_status_active", None)
            if callable(checker):
                try:
                    manual_motion_active = bool(checker())
                except Exception:
                    manual_motion_active = False
            if manual_motion_active:
                interval_s = min(interval_s, 0.1)
            elif self._ready and (not self._streaming) and (not self._paused):
                interval_s = max(interval_s, self._tx_idle_status_query_log_interval_s)
            now = time.monotonic()
            if (now - self._last_tx_status_query_log_ts) < interval_s:
                return False
            self._last_tx_status_query_log_ts = now
        return True

    def _log_tx_line(self, line: str) -> None:
        if not line or (not self._should_log_tx_line(line)):
            return
        self._record_serial_activity("TX", line)
        rx_logger = self._rx_logger
        if not rx_logger:
            return
        try:
            rx_logger.info("TX %s", line)
        except Exception as exc:
            _log_suppressed("Failed writing TX serial line to debug logger", exc)

    def _record_serial_activity(self, direction: str, line: str) -> None:
        try:
            text = str(line or "").strip()
            if not text:
                return
            self._serial_activity_history.append((time.time(), str(direction or "").upper(), text))
        except Exception as exc:
            _log_suppressed("Failed recording serial activity history entry", exc)

    def get_serial_activity_history(
        self,
        *,
        limit: int = 4000,
        since_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        try:
            max_items = max(1, int(limit))
        except Exception:
            max_items = 4000
        since_cutoff = None
        if since_seconds is not None:
            try:
                since_value = float(since_seconds)
                if since_value > 0:
                    since_cutoff = now - since_value
            except Exception:
                since_cutoff = None
        entries = list(self._serial_activity_history)
        if since_cutoff is not None:
            entries = [item for item in entries if float(item[0]) >= float(since_cutoff)]
        if len(entries) > max_items:
            entries = entries[-max_items:]
        result: list[dict[str, Any]] = []
        for ts, direction, text in entries:
            try:
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(ts)))
            except Exception:
                iso = ""
            result.append(
                {
                    "ts": float(ts),
                    "timestamp": iso,
                    "dir": str(direction or ""),
                    "line": str(text or ""),
                }
            )
        return result
    
    # ========================================================================
    # CONTEXT MANAGER SUPPORT
    # ========================================================================
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        """Context manager exit - ensures cleanup."""
        try:
            self.disconnect()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        return False  # Don't suppress exceptions
    
    def send_realtime(self, command: bytes) -> None:
        """Send real-time command (no newline).
        
        Real-time commands are processed immediately by GRBL without
        waiting for buffer space or acknowledgment.
        
        Args:
            command: Real-time command byte(s)
            
        Raises:
            SerialWriteError: If write fails
        """
        if not self.is_connected():
            logger.warning("Cannot send real-time command - not connected")
            return
        ser = self.ser
        if ser is None:
            raise SerialWriteError("Serial port not connected")
        serial_module = self._serial_module()
        timeout_exc = _serial_timeout_exception_type(serial_module)
        serial_exc = _serial_exception_type(serial_module)
        try:
            rendered = _format_realtime_for_log(command)
            if rendered:
                self._log_tx_line(f"RT {rendered}")
            with self._write_lock:
                total = 0
                length = len(command)
                while total < length:
                    written = ser.write(command[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise timeout_exc("Write returned 0 bytes")
                    total += written
        except timeout_exc as e:
            raise SerialWriteError(f"Write timeout: {e}") from e
        except serial_exc as e:
            raise SerialWriteError(f"Serial write error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            raise SerialWriteError(f"Unexpected error: {e}") from e
    
    def _clear_outgoing(self) -> None:
        """Clear the outgoing command queue."""
        with self._stream_lock:
            while True:
                try:
                    self._outgoing_q.get_nowait()
                except queue.Empty:
                    break
            self._manual_source_queue.clear()
            self._manual_pending_item = None
            self._manual_queue_drop_count = 0
            self._manual_queue_drop_total = 0
            self._manual_queue_last_drop_notice_ts = 0.0
        self._emit_buffer_fill()

    def _record_manual_queue_drop(self) -> None:
        """Track a dropped manual command and emit a rate-limited UI notice."""
        self._manual_queue_drop_count += 1
        self._manual_queue_drop_total += 1
        now = time.time()
        if (
            now - self._manual_queue_last_drop_notice_ts
            < MANUAL_QUEUE_DROP_NOTICE_INTERVAL
        ):
            return
        dropped = self._manual_queue_drop_count
        total = self._manual_queue_drop_total
        self._manual_queue_drop_count = 0
        self._manual_queue_last_drop_notice_ts = now
        self.ui_q.put(("manual_queue_drop", dropped, total))
        self.ui_q.put((
            "log",
            f"[manual queue] Dropped {dropped} command(s); queue is full (total {total}).",
        ))

    def _enqueue_manual_command(self, command: str, source: str | None) -> bool:
        """Queue a manual command without blocking worker locks."""
        try:
            self._outgoing_q.put_nowait(command)
        except queue.Full:
            self._record_manual_queue_drop()
            return False
        self._manual_source_queue.append(source)
        try:
            self._tx_activity_evt.set()
        except Exception as exc:
            _log_suppressed("Failed signaling TX activity after enqueueing manual command", exc)
        return True
    
    def _reset_stream_buffer(self) -> None:
        """Reset streaming buffer state."""
        with self._stream_lock:
            self._stream_buf_used = 0
            self._stream_line_queue.clear()
            self._stream_pending_item = None
            self._manual_pending_item = None
            self._live_acked_ring.clear()
            self._live_current_acked = None
            self._live_pending_window.clear()
            self._live_window_dirty = True
            self._live_window_last_emit_ts = 0.0
            self._manual_source_queue.clear()
            self._resume_preamble.clear()
            self._rx_window = RX_BUFFER_SIZE
            self._send_index = 0
            self._ack_index = -1
            self._ack_byte_offset = 0
            self._pause_after_idx = None
            self._pause_after_reason = None
            self._stream_tool_change_pending = None
            self._stream_tool_change_name = ""
            self._stream_tool_change_active = False
            self._tx_bytes_window.clear()
            self._last_tx_emit_ts = 0.0
            self._tx_line_ts_window.clear()
            self._tx_lines_per_sec = 0.0
        self._emit_live_gcode_window(force=True)

    @staticmethod
    def _safe_live_idx(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    def _emit_live_gcode_window(self, *, force: bool = False) -> None:
        """Emit bounded live Past/Current/Next window payload to UI queue."""
        now = time.monotonic()
        if not force:
            last_emit = float(getattr(self, "_live_window_last_emit_ts", 0.0) or 0.0)
            if (now - last_emit) < float(getattr(self, "_live_window_refresh_s", 0.125)):
                self._live_window_dirty = True
                return
        with self._stream_lock:
            past_lines = list(self._live_acked_ring)
            current_line = self._live_current_acked
            pending: list[tuple[int, str]] = []
            seen: set[int] = set()
            for item in self._stream_line_queue:
                if not bool(getattr(item, "is_gcode", False)):
                    continue
                idx = self._safe_live_idx(getattr(item, "idx", None))
                if idx is None:
                    continue
                if idx in seen:
                    continue
                seen.add(idx)
                pending.append((idx, str(getattr(item, "line", "") or "")))
                if len(pending) >= int(GCODE_LIVE_WINDOW_NEXT_LINES):
                    break
            pending_item = self._stream_pending_item
            if (
                pending_item is not None
                and bool(getattr(pending_item, "is_gcode", False))
                and len(pending) < int(GCODE_LIVE_WINDOW_NEXT_LINES)
            ):
                idx = self._safe_live_idx(getattr(pending_item, "idx", None))
                if idx is not None and idx not in seen:
                    pending.append((idx, str(getattr(pending_item, "line", "") or "")))
            self._live_pending_window.clear()
            self._live_pending_window.extend(pending)
            pending_depth = int(len(pending))
            acked_offset = max(0, int(getattr(self, "_ack_byte_offset", 0) or 0))
            file_size = max(0, int(getattr(self, "_stream_file_size_bytes", 0) or 0))
            last_acked_idx = (
                int(current_line[0])
                if isinstance(current_line, tuple) and len(current_line) >= 1
                else int(getattr(self, "_ack_index", -1) or -1)
            )
        progress_pct = 0.0
        if file_size > 0:
            acked_offset = min(acked_offset, file_size)
            progress_pct = max(
                0.0,
                min(100.0, (float(acked_offset) / float(file_size)) * 100.0),
            )
        payload = {
            "past_lines": past_lines,
            "current_line": current_line,
            "next_lines": pending,
            "next_buffered_count": pending_depth,
            "pending_depth": pending_depth,
            "last_acked_index": int(last_acked_idx),
            "acked_byte_offset": int(acked_offset),
            "file_size_bytes": int(file_size),
            "stream_progress_pct": float(progress_pct),
        }
        try:
            self.ui_q.put(("live_gcode_window", payload))
            self._live_window_last_emit_ts = now
            self._live_window_dirty = False
        except Exception as exc:
            _log_suppressed("Failed queueing live G-code window payload", exc)

    def _record_tx_line(self) -> None:
        now = time.time()
        self._tx_line_ts_window.append(now)
        cutoff = now - TX_LINE_RATE_WINDOW_S
        while self._tx_line_ts_window and self._tx_line_ts_window[0] < cutoff:
            self._tx_line_ts_window.popleft()
        if not self._tx_line_ts_window:
            self._tx_lines_per_sec = 0.0
            return
        oldest = self._tx_line_ts_window[0]
        span = max(0.25, now - oldest)
        self._tx_lines_per_sec = float(len(self._tx_line_ts_window)) / span

    def _record_ack_latency(self, latency_ms: float) -> None:
        if latency_ms < 0:
            return
        self._ok_latency_ms_last = float(latency_ms)
        self._ok_latency_sample_count += 1
        count = self._ok_latency_sample_count
        if count <= 1:
            self._ok_latency_ms_avg = float(latency_ms)
            return
        self._ok_latency_ms_avg = (
            (self._ok_latency_ms_avg * (count - 1)) + float(latency_ms)
        ) / count

    def _record_queue_depth_snapshot(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if (now - self._last_queue_depth_snapshot_ts) < QUEUE_DEPTH_SNAPSHOT_INTERVAL_S:
            return
        self._last_queue_depth_snapshot_ts = now
        def _safe_len(value: Any) -> int:
            try:
                return max(0, int(len(value)))
            except Exception:
                pass
            items = getattr(value, "items", None)
            if items is not None:
                try:
                    return max(0, int(len(items)))
                except Exception:
                    pass
            try:
                return 1 if bool(value) else 0
            except Exception:
                return 0
        with self._stream_lock:
            stream_depth = _safe_len(self._stream_line_queue)
            if self._stream_pending_item is not None:
                stream_depth += 1
            if self._manual_pending_item is not None:
                stream_depth += 1
        try:
            manual_depth = int(self._outgoing_q.qsize())
        except Exception:
            manual_depth = 0
        try:
            ui_depth = int(self.ui_q.qsize())
        except Exception:
            ui_depth = 0
        self._queue_depth_snapshots.append((now, stream_depth, manual_depth, ui_depth))

    def get_runtime_metrics(self) -> dict[str, Any]:
        now = time.time()
        # Keep line-rate fresh if queried between send events.
        if self._tx_line_ts_window:
            cutoff = now - TX_LINE_RATE_WINDOW_S
            while self._tx_line_ts_window and self._tx_line_ts_window[0] < cutoff:
                self._tx_line_ts_window.popleft()
            if self._tx_line_ts_window:
                span = max(0.25, now - self._tx_line_ts_window[0])
                self._tx_lines_per_sec = float(len(self._tx_line_ts_window)) / span
            else:
                self._tx_lines_per_sec = 0.0
        self._record_queue_depth_snapshot(now=now)
        snapshots = list(self._queue_depth_snapshots)
        queue_depth_last = {
            "stream": 0,
            "manual": 0,
            "ui": 0,
            "timestamp": "",
        }
        if snapshots:
            ts, stream_depth, manual_depth, ui_depth = snapshots[-1]
            queue_depth_last = {
                "stream": int(stream_depth),
                "manual": int(manual_depth),
                "ui": int(ui_depth),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
            }
        queue_depth_samples = [
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
                "stream": int(stream_depth),
                "manual": int(manual_depth),
                "ui": int(ui_depth),
            }
            for ts, stream_depth, manual_depth, ui_depth in snapshots[-20:]
        ]
        serial_activity_tail = self.get_serial_activity_history(limit=400, since_seconds=600.0)
        tx_loop_cycles = max(0, int(self._tx_loop_cycles))
        tx_loop_idle_cycles = max(0, int(self._tx_loop_idle_cycles))
        tx_loop_active_cycles = max(0, int(self._tx_loop_active_cycles))
        tx_loop_idle_ratio = 0.0
        if tx_loop_cycles > 0:
            tx_loop_idle_ratio = float(tx_loop_idle_cycles) / float(tx_loop_cycles)
        stream_file_size_bytes = max(0, int(getattr(self, "_stream_file_size_bytes", 0) or 0))
        acked_byte_offset = max(0, int(getattr(self, "_ack_byte_offset", 0) or 0))
        if stream_file_size_bytes > 0:
            acked_byte_offset = min(acked_byte_offset, stream_file_size_bytes)
            stream_progress_pct = max(
                0.0,
                min(100.0, (float(acked_byte_offset) / float(stream_file_size_bytes)) * 100.0),
            )
        else:
            stream_progress_pct = 0.0
        try:
            live_acked_count = int(len(self._live_acked_ring))
        except Exception:
            live_acked_count = 0
        try:
            live_pending_window_count = int(len(self._live_pending_window))
        except Exception:
            live_pending_window_count = 0
        with self._status_interval_lock:
            status_poll_interval_s = float(getattr(self, "_status_poll_interval", 0.0) or 0.0)
        live_current_acked_index = -1
        try:
            if self._live_current_acked is not None:
                live_current_acked_index = int(self._live_current_acked[0])
        except Exception:
            live_current_acked_index = -1
        return {
            "tx_lines_per_sec": float(self._tx_lines_per_sec),
            "ok_latency_ms_last": float(self._ok_latency_ms_last),
            "ok_latency_ms_avg": float(self._ok_latency_ms_avg),
            "ok_latency_samples": int(self._ok_latency_sample_count),
            "ack_latency_ms_last": float(self._ok_latency_ms_last),
            "ack_latency_ms_avg": float(self._ok_latency_ms_avg),
            "ack_latency_samples": int(self._ok_latency_sample_count),
            "tx_loop_cycles": tx_loop_cycles,
            "tx_loop_idle_cycles": tx_loop_idle_cycles,
            "tx_loop_active_cycles": tx_loop_active_cycles,
            "tx_loop_idle_wait_total_s": float(self._tx_loop_idle_wait_total_s),
            "tx_loop_idle_ratio": tx_loop_idle_ratio,
            "queue_depth_last": queue_depth_last,
            "queue_depth_samples": queue_depth_samples,
            "serial_activity_tail": serial_activity_tail,
            "stream_file_size_bytes": int(stream_file_size_bytes),
            "acked_byte_offset": int(acked_byte_offset),
            "stream_progress_pct": float(stream_progress_pct),
            "live_gcode_acked_ring_count": int(live_acked_count),
            "live_gcode_pending_window_count": int(live_pending_window_count),
            "live_gcode_current_acked_index": int(live_current_acked_index),
            "status_poll_interval_s": float(status_poll_interval_s),
            "status_poll_interval_effective_s": float(status_poll_interval_s),
            "manual_motion_status_query_count": int(self._manual_motion_status_query_count),
            "manual_motion_status_query_interval_avg_ms": float(
                self._manual_motion_status_query_interval_avg_ms
            ),
            "manual_motion_status_query_interval_max_ms": float(
                self._manual_motion_status_query_interval_max_ms
            ),
            "manual_motion_status_session_active": bool(
                self._manual_motion_status_session_active
            ),
            "manual_motion_status_session_count": int(
                self._manual_motion_status_session_count
            ),
            "manual_motion_status_session_start_ts": float(
                self._manual_motion_status_session_start_ts
            ),
            "manual_motion_status_session_last_start_ts": float(
                self._manual_motion_status_session_last_start_ts
            ),
            "manual_motion_status_session_last_end_ts": float(
                self._manual_motion_status_session_last_end_ts
            ),
            "manual_motion_status_session_last_change_ts": float(
                self._manual_motion_status_session_last_change_ts
            ),
            "manual_motion_status_session_last_source": str(
                self._manual_motion_status_session_last_source or ""
            ),
            "manual_motion_status_tx_query_count": int(self._manual_motion_status_query_count),
            "manual_motion_status_tx_query_interval_avg_ms": float(
                self._manual_motion_status_query_interval_avg_ms
            ),
            "manual_motion_status_tx_query_interval_max_ms": float(
                self._manual_motion_status_query_interval_max_ms
            ),
            "manual_motion_status_query_interval_ms": float(
                self._manual_motion_status_query_interval_avg_ms
            ),
            "manual_motion_status_rx_count": int(self._manual_motion_status_rx_count),
            "manual_motion_status_rx_interval_avg_ms": float(
                self._manual_motion_status_rx_interval_avg_ms
            ),
            "manual_motion_status_rx_interval_max_ms": float(
                self._manual_motion_status_rx_interval_max_ms
            ),
            "manual_motion_status_rx_status_count": int(self._manual_motion_status_rx_count),
            "manual_motion_status_rx_status_interval_avg_ms": float(
                self._manual_motion_status_rx_interval_avg_ms
            ),
            "manual_motion_status_rx_status_interval_max_ms": float(
                self._manual_motion_status_rx_interval_max_ms
            ),
            "manual_motion_status_rx_interval_ms": float(
                self._manual_motion_status_rx_interval_avg_ms
            ),
            "status_wait_sample_count": int(self._status_wait_sample_count),
            "status_wait_requested_avg_ms": float(
                self._status_wait_requested_avg_s * 1000.0
            ),
            "status_wait_actual_avg_ms": float(self._status_wait_actual_avg_s * 1000.0),
            "status_wait_overshoot_max_ms": float(
                self._status_wait_overshoot_max_ms
            ),
            "status_wait_overshoot_max_recent_ms": float(
                self._status_wait_overshoot_recent_max_ms
            ),
            "status_wait_overshoot_recent_window_s": float(
                self._status_wait_overshoot_recent_window_s
            ),
            "status_wait_overshoot_alert_threshold_ms": 250.0,
            "status_wait_overshoot_alert_recent": bool(
                float(self._status_wait_overshoot_recent_max_ms) >= 250.0
            ),
            "status_wait_overshoot_alert_lifetime": bool(
                float(self._status_wait_overshoot_max_ms) >= 250.0
            ),
            "status_wait_last_reason": str(self._status_wait_last_reason or ""),
            "status_wait_last_requested_ms": float(
                self._status_wait_last_requested_s * 1000.0
            ),
            "status_wait_last_actual_ms": float(
                self._status_wait_last_actual_s * 1000.0
            ),
            "status_wait_last_overshoot_ms": float(
                self._status_wait_last_overshoot_ms
            ),
            "status_wait_reason_counts": dict(self._status_wait_reason_counts),
            "status_wait_trace": list(self._status_wait_trace),
        }
    
    def _encode_line_payload(self, line: str) -> bytes:
        """Encode line for serial transmission.
        
        Args:
            line: G-code line
            
        Returns:
            Encoded bytes with newline
        """
        return (line.strip() + "\n").encode("ascii")

    def _write_line(
        self,
        line: str,
        payload: Optional[bytes] = None,
        *,
        allow_abort: bool = False,
    ) -> bool:
        """Write line to serial port.
        
        Args:
            line: Line content (for logging)
            payload: Pre-encoded payload (optional)
            allow_abort: Allow writes even if abort flag is set
            
        Returns:
            True if write succeeded, False otherwise
        """
        if not self.is_connected():
            return False
        serial_module = self._serial_module()
        timeout_exc = _serial_timeout_exception_type(serial_module)
        serial_exc = _serial_exception_type(serial_module)
        try:
            if payload is None:
                payload = self._encode_line_payload(line)

            self._log_tx_line(line)
            with self._write_lock:
                if self._abort_writes.is_set() and not allow_abort:
                    return False
                ser = self.ser
                if ser is None:
                    return False
                total = 0
                length = len(payload)
                while total < length:
                    written = ser.write(payload[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise timeout_exc("Write returned 0 bytes")
                    total += written

            return True

        except timeout_exc as e:
            logger.error(f"Write timeout: {e}")
            self.ui_q.put(("log", f"[write timeout] {e}"))
            if self.ser is not None:
                self._signal_disconnect(f"[tx/write-timeout] Serial write timeout: {e}")
            return False
            
        except serial_exc as e:
            logger.error(f"Serial write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.ser is not None:
                self._signal_disconnect(f"[tx/write] Serial write error: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.ser is not None:
                self._signal_disconnect(f"[tx/write] Unexpected write error: {e}")
            return False
    
    def _emit_buffer_fill(self) -> None:
        """Emit buffer fill status to UI."""
        with self._stream_lock:
            window = max(1, int(self._rx_window))
            used = max(0, int(self._stream_buf_used))
        
        if used > window:
            used = window
        
        pct = int(round((used / window) * 100))
        payload = (pct, used, window)
        
        # Rate limit updates
        now = time.time()
        if (payload == self._last_buffer_emit and
            (now - self._last_buffer_emit_ts) < BUFFER_EMIT_INTERVAL):
            return
        
        self._last_buffer_emit = payload
        self._last_buffer_emit_ts = now
        self.ui_q.put(("buffer_fill", pct, used, window))
        self._record_queue_depth_snapshot(now=now)
    
    def _record_tx_bytes(self, count: int) -> None:
        """Record transmitted bytes for throughput calculation.
        
        Args:
            count: Number of bytes transmitted
        """
        if count <= 0:
            return
        
        now = time.time()
        self._tx_bytes_window.append((now, count))
        
        # Remove old samples
        cutoff = now - TX_THROUGHPUT_WINDOW
        while self._tx_bytes_window and self._tx_bytes_window[0][0] < cutoff:
            self._tx_bytes_window.popleft()
        
        if not self._tx_bytes_window:
            return
        
        # Rate limit updates
        if (now - self._last_tx_emit_ts) < TX_THROUGHPUT_EMIT_INTERVAL:
            return
        
        # Calculate throughput
        span = max(0.1, now - self._tx_bytes_window[0][0])
        total = sum(b for _, b in self._tx_bytes_window)
        bps = total / span
        
        self._last_tx_emit_ts = now
        self.ui_q.put(("throughput", bps))
    
    # ========================================================================
    # WORKER THREAD LOOPS
    # ========================================================================
    
    def _rx_loop(self, stop_evt: threading.Event) -> None:
        """Receive thread - reads from GRBL and processes responses.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("RX thread started")
        buf = b""
        serial_module = self._serial_module()
        timeout_exc = _serial_timeout_exception_type(serial_module)
        serial_exc = _serial_exception_type(serial_module)

        def _safe_read_size(ser_obj: object) -> int:
            # Prefer draining available bytes when the backend exposes in_waiting,
            # but always clamp to a safe positive integer for serial.read(size).
            try:
                waiting = getattr(ser_obj, "in_waiting", None)
            except Exception:
                waiting = None
            if waiting is None:
                return 1
            try:
                waiting_int = int(waiting)
            except Exception:
                return 1
            return max(1, min(256, waiting_int))

        def _shutdown_in_progress() -> bool:
            shared_stop_evt = getattr(self, "_stop_evt", None)
            if shared_stop_evt is not None:
                try:
                    if shared_stop_evt.is_set():
                        return True
                except Exception:
                    return True
            current_ser = self.ser
            if current_ser is None:
                return True
            try:
                return not bool(getattr(current_ser, "is_open", False))
            except Exception:
                return True
        
        try:
            while not stop_evt.is_set():
                try:
                    self.is_connected()
                except Exception as e:
                    logger.error(f"RX thread error: {e}", exc_info=True)
                    self._emit_exception("RX thread error", e)
                    self._signal_disconnect(f"[rx/check] RX thread error: {e}")
                    stop_evt.set()
                    break
                ser = self.ser
                if ser is None:
                    time.sleep(0.05)
                    continue
                if hasattr(ser, "is_open") and not ser.is_open:
                    if not stop_evt.is_set():
                        self._signal_disconnect("[rx/port-closed] Serial port closed")
                        stop_evt.set()
                    break
                try:
                    chunk = ser.read(_safe_read_size(ser))
                except timeout_exc:
                    # Normal timeout - just continue
                    continue
                except serial_exc as e:
                    if _shutdown_in_progress():
                        logger.debug("RX loop serial read aborted during shutdown: %s", e)
                        break
                    logger.error(f"Serial read error: {e}")
                    self.ui_q.put(("log", f"[read error] {e}"))
                    self._signal_disconnect(f"[rx/read] Serial read error: {e}")
                    stop_evt.set()
                    break
                except Exception as e:
                    if _shutdown_in_progress():
                        logger.debug("RX loop read aborted during shutdown: %s", e)
                        break
                    logger.error(f"Unexpected read error: {e}")
                    self._signal_disconnect(f"[rx/read] Unexpected serial read error: {e}")
                    stop_evt.set()
                    break
                
                if not chunk:
                    continue
                self._last_rx_ts = time.time()
                self._watchdog_paused = False
                self._watchdog_trip_ts = 0.0
                buf += chunk
                
                # Process complete lines
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if line_str:
                        self._handle_rx_line(line_str)
        
        except Exception as e:
            if _shutdown_in_progress():
                logger.debug("RX thread exiting during shutdown: %s", e)
            else:
                logger.error(f"RX thread error: {e}", exc_info=True)
                self._emit_exception("RX thread error", e)
                self._signal_disconnect(f"[rx/thread] RX thread error: {e}")
                stop_evt.set()
        
        finally:
            logger.debug("RX thread stopped")
    
