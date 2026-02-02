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
import traceback
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import Any, Callable, Optional, Sequence, Tuple, TYPE_CHECKING, TypeAlias

from .grbl_worker_commands import GrblWorkerCommandMixin
from .grbl_worker_status import GrblWorkerStatusMixin
from .grbl_worker_streaming import GrblWorkerStreamingMixin
from .utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error


class _FallbackSerialException(Exception):
    pass


class _FallbackSerialTimeout(Exception):
    pass

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
    BAUD_DEFAULT,
    RX_BUFFER_SIZE,
    SERIAL_CONNECT_DELAY,
    SERIAL_TIMEOUT,
    SERIAL_WRITE_TIMEOUT,
    THREAD_JOIN_TIMEOUT,
    BUFFER_EMIT_INTERVAL,
    TX_THROUGHPUT_WINDOW,
    TX_THROUGHPUT_EMIT_INTERVAL,
    STATUS_POLL_DEFAULT,
    RT_RESUME,
    RT_JOG_CANCEL,
    WATCHDOG_HOMING_TIMEOUT,
)
from .utils.exceptions import (
    SerialConnectionError,
    SerialWriteError,
)
from .utils.validation import (
    validate_port_name,
    validate_baud_rate,
)

logger = logging.getLogger(__name__)
_RX_LOGGER = None
_RX_LOGGER_LOCK = threading.Lock()


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
            except Exception:
                max_bytes = 2097152
            try:
                backup_count = int(os.getenv("SIMPLE_SENDER_RX_LOG_BACKUPS", "5"))
            except Exception:
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

def _serial_exception_type():
    if serial is None:
        return _FallbackSerialException
    return getattr(serial, "SerialException", Exception)

def _serial_timeout_exception_type():
    if serial is None:
        return _FallbackSerialTimeout
    return getattr(serial, "SerialTimeoutException", Exception)


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

class GrblWorker(GrblWorkerCommandMixin, GrblWorkerStreamingMixin, GrblWorkerStatusMixin):
    """Manages serial communication with GRBL controller.
    
    This class handles:
    - Connection and disconnection
    - G-code streaming with buffer management
    - Status polling
    - Real-time command execution
    
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
        self._streaming = False
        self._paused = False
        self._send_index = 0  # next index to send
        self._ack_index = -1  # last acked index
        self._stream_buf_used = 0
        self._stream_line_queue: deque[Tuple[int, bool, Optional[int], str]] = deque()
        self._stream_pending_item: Optional[Tuple[str, bool, Optional[int]]] = None
        self._manual_pending_item: Optional[Tuple[str, bytes, int]] = None
        self._last_manual_source: str | None = None
        self._settings_dump_active = False
        self._pause_after_idx: Optional[int] = None
        self._pause_after_reason: str | None = None
        self._resume_preamble: deque[str] = deque()
        self._rx_window = RX_BUFFER_SIZE
        self._stream_token = 0
        self._abort_writes = threading.Event()
        self._gcode_name: str | None = None
        
        # Throughput tracking
        self._tx_bytes_window: deque[Tuple[float, int]] = deque()
        self._last_tx_emit_ts = 0.0
        
        # Command queue
        self._outgoing_q: queue.Queue[str] = queue.Queue()
        self._purge_jog_queue = threading.Event()
        
        # Thread synchronization
        self._stream_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._status_interval_lock = threading.Lock()
        
        # State flags
        self._status_poll_interval = STATUS_POLL_DEFAULT
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
        self._homing_watchdog_enabled = True
        self._homing_watchdog_timeout = WATCHDOG_HOMING_TIMEOUT
        self._connect_started_ts = 0.0

    def _log_rx_line(self, line: str) -> None:
        if not line:
            return
        rx_logger = self._rx_logger
        if not rx_logger:
            return
        try:
            rx_logger.info("RX %s", line)
        except Exception:
            pass

    def _log_tx_line(self, line: str) -> None:
        if not line:
            return
        rx_logger = self._rx_logger
        if not rx_logger:
            return
        try:
            rx_logger.info("TX %s", line)
        except Exception:
            pass
    
    # ========================================================================
    # CONTEXT MANAGER SUPPORT
    # ========================================================================
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        try:
            self.disconnect()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        return False  # Don't suppress exceptions
    
    # ========================================================================
    # CONNECTION MANAGEMENT
    # ========================================================================
    
    def list_ports(self) -> list[str]:
        """Get list of available serial ports.
        
        Returns:
            List of port device names
        """
        if not SERIAL_AVAILABLE or list_ports is None:
            return []
        assert list_ports is not None
        return [p.device for p in list_ports.comports()]
    
    def connect(self, port: str, baud: int = BAUD_DEFAULT) -> None:
        """Connect to GRBL controller.
        
        Args:
            port: Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            baud: Baud rate (default: 115200)
            
        Raises:
            SerialConnectionError: If connection fails
            ValueError: If parameters are invalid
        """
        if not SERIAL_AVAILABLE:
            raise SerialConnectionError(
                "pyserial is required to connect to GRBL. "
                "Install with: pip install pyserial"
            )
        assert serial is not None
        serial_exc = _serial_exception_type()
        
        # Validate inputs
        port = validate_port_name(port)
        baud = validate_baud_rate(baud)
        
        # Disconnect if already connected
        if self.is_connected():
            self.disconnect()
        
        # Reset state
        self._stop_evt = threading.Event()
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._last_rx_ts = time.time()
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        
        try:
            # Open serial port
            self.ser = serial.Serial(
                port,
                baudrate=baud,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_WRITE_TIMEOUT
            )
            ser = self.ser
            assert ser is not None
            self._connect_started_ts = time.time()
            
            # Give GRBL time to reset (some boards reset on connection)
            time.sleep(SERIAL_CONNECT_DELAY)
            
            # Clear buffers
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except serial_exc as e:
                logger.warning(f"Failed to reset buffers: {e}")
            
            # Start worker threads
            stop_evt = self._stop_evt
            self._rx_thread = threading.Thread(
                target=self._rx_loop,
                args=(stop_evt,),
                daemon=True,
                name="GRBL-RX"
            )
            self._tx_thread = threading.Thread(
                target=self._tx_loop,
                args=(stop_evt,),
                daemon=True,
                name="GRBL-TX"
            )
            self._status_thread = threading.Thread(
                target=self._status_loop,
                args=(stop_evt,),
                daemon=True,
                name="GRBL-Status"
            )
            
            self._rx_thread.start()
            self._tx_thread.start()
            self._status_thread.start()
            
            self.ui_q.put(("conn", True, port))
            logger.info(f"Connected to {port} at {baud} baud")
            
        except serial_exc as e:
            self.ser = None
            self._connect_started_ts = 0.0
            raise SerialConnectionError(f"Failed to connect to {port}: {e}")
        except Exception as e:
            self.ser = None
            self._connect_started_ts = 0.0
            raise SerialConnectionError(f"Unexpected error connecting to {port}: {e}")
    
    def disconnect(self) -> None:
        """Disconnect from GRBL controller.
        
        Stops all worker threads and closes the serial port.
        Thread-safe and idempotent.
        """
        # Signal threads to stop
        self._stop_evt.set()
        
        # Reset streaming state
        self._streaming = False
        self._paused = False
        self._gcode = []
        self._send_index = 0
        self._ack_index = -1
        self._reset_stream_buffer()
        self._last_buffer_emit = None
        self._last_buffer_emit_ts = 0.0
        self._clear_outgoing()
        self._emit_buffer_fill()
        
        # Reset state flags
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._settings_dump_active = False
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._connect_started_ts = 0.0
        
        # Notify UI
        self.ui_q.put(("ready", False))
        self.ui_q.put(("stream_state", "stopped", None))
        
        # Close serial port
        serial_exc = _serial_exception_type()
        if self.ser:
            try:
                self.ser.close()
                logger.info("Serial port closed")
            except serial_exc as e:
                logger.error(f"Error closing serial port: {e}")
            except Exception as e:
                logger.error(f"Unexpected error closing serial port: {e}")
            finally:
                self.ser = None
        
        # Wait for threads to finish
        for thread in (self._rx_thread, self._tx_thread, self._status_thread):
            if thread and thread.is_alive():
                thread.join(timeout=THREAD_JOIN_TIMEOUT)
                if thread.is_alive():
                    logger.warning(f"Thread {thread.name} did not terminate")
        
        self._rx_thread = None
        self._tx_thread = None
        self._status_thread = None
        
        self.ui_q.put(("conn", False, None))
    
    def is_connected(self) -> bool:
        """Check if connected to GRBL.
        
        Returns:
            True if connected and serial port is open
        """
        return self.ser is not None and self.ser.is_open
    
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
        timeout_exc = _serial_timeout_exception_type()
        serial_exc = _serial_exception_type()
        try:
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
            raise SerialWriteError(f"Write timeout: {e}")
        except serial_exc as e:
            raise SerialWriteError(f"Serial write error: {e}")
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            raise SerialWriteError(f"Unexpected error: {e}")
    
    def _emit_exception(self, context: str, exc: BaseException) -> None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            self.ui_q.put(("log", f"[worker] {context}: {exc}"))
            for ln in tb.splitlines():
                self.ui_q.put(("log", ln))
        except Exception:
            pass

    def _signal_disconnect(self, reason: str | None = None) -> None:
        """Signal an unexpected disconnect and reset internal state."""
        was_streaming = self._streaming or self._paused
        self._stop_evt.set()
        try:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
        finally:
            self.ser = None
        self._streaming = False
        self._paused = False
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._settings_dump_active = False
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._connect_started_ts = 0.0
        self._reset_stream_buffer()
        self._clear_outgoing()
        try:
            if was_streaming:
                self.ui_q.put(("stream_interrupted", True, reason))
            self.ui_q.put(("ready", False))
            self.ui_q.put(("stream_state", "stopped", reason))
            self.ui_q.put(("conn", False, None))
        except Exception:
            pass
        if reason:
            try:
                self.ui_q.put(("log", f"[disconnect] {reason}"))
            except Exception:
                pass

    def _clear_outgoing(self) -> None:
        """Clear the outgoing command queue."""
        try:
            while True:
                self._outgoing_q.get_nowait()
        except queue.Empty:
            pass
        self._manual_pending_item = None
        self._emit_buffer_fill()
    
    def _reset_stream_buffer(self) -> None:
        """Reset streaming buffer state."""
        with self._stream_lock:
            self._stream_buf_used = 0
            self._stream_line_queue.clear()
            self._stream_pending_item = None
            self._manual_pending_item = None
            self._resume_preamble.clear()
            self._rx_window = RX_BUFFER_SIZE
            self._send_index = 0
            self._ack_index = -1
            self._pause_after_idx = None
            self._pause_after_reason = None
            self._tx_bytes_window.clear()
            self._last_tx_emit_ts = 0.0
    
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
        timeout_exc = _serial_timeout_exception_type()
        serial_exc = _serial_exception_type()
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
            if self.is_connected():
                self._signal_disconnect(f"Serial write timeout: {e}")
            return False
            
        except serial_exc as e:
            logger.error(f"Serial write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.is_connected():
                self._signal_disconnect(f"Serial write error: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.is_connected():
                self._signal_disconnect(f"Unexpected write error: {e}")
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
        timeout_exc = _serial_timeout_exception_type()
        serial_exc = _serial_exception_type()
        
        try:
            while not stop_evt.is_set():
                try:
                    self.is_connected()
                except Exception as e:
                    logger.error(f"RX thread error: {e}", exc_info=True)
                    self._emit_exception("RX thread error", e)
                    self._signal_disconnect(f"RX thread error: {e}")
                    stop_evt.set()
                    break
                ser = self.ser
                if ser is None:
                    time.sleep(0.05)
                    continue
                if hasattr(ser, "is_open") and not ser.is_open:
                    if not stop_evt.is_set():
                        self._signal_disconnect("Serial port closed")
                        stop_evt.set()
                    break
                try:
                    chunk = ser.read(256)
                except timeout_exc:
                    # Normal timeout - just continue
                    continue
                except serial_exc as e:
                    logger.error(f"Serial read error: {e}")
                    self.ui_q.put(("log", f"[read error] {e}"))
                    self._signal_disconnect(f"Serial read error: {e}")
                    stop_evt.set()
                    break
                except Exception as e:
                    logger.error(f"Unexpected read error: {e}")
                    self._signal_disconnect(f"Unexpected serial read error: {e}")
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
            logger.error(f"RX thread error: {e}", exc_info=True)
            self._emit_exception("RX thread error", e)
            self._signal_disconnect(f"RX thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("RX thread stopped")
    
