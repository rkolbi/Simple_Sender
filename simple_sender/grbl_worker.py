"""GRBL serial communication worker.

This module handles all serial communication with GRBL controllers,
including connection management, G-code streaming, and status polling.
"""

import logging
import queue
import re
import threading
import time
import traceback
from collections import deque
from typing import Callable, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    list_ports = None
    SERIAL_AVAILABLE = False

from .utils.constants import (
    BAUD_DEFAULT,
    RX_BUFFER_SIZE,
    RX_BUFFER_SAFETY,
    MAX_LINE_LENGTH,
    RT_RESET,
    RT_STATUS,
    RT_HOLD,
    RT_RESUME,
    RT_JOG_CANCEL,
    SERIAL_CONNECT_DELAY,
    SERIAL_TIMEOUT,
    SERIAL_WRITE_TIMEOUT,
    THREAD_JOIN_TIMEOUT,
    BUFFER_EMIT_INTERVAL,
    TX_THROUGHPUT_WINDOW,
    TX_THROUGHPUT_EMIT_INTERVAL,
    STATUS_POLL_DEFAULT,
    EVENT_QUEUE_TIMEOUT,
    DEFAULT_SPINDLE_RPM,
)
from .utils.exceptions import (
    SerialConnectionError,
    SerialDisconnectError,
    SerialWriteError,
    SerialReadError,
    GrblNotConnectedException,
    GrblAlarmException,
)
from .utils.validation import (
    validate_port_name,
    validate_baud_rate,
    validate_feed_rate,
    validate_unit_mode,
    validate_interval,
    validate_rpm,
)

logger = logging.getLogger(__name__)

_PAUSE_MCODE_MAP = {
    "0": "M0",
    "00": "M0",
    "1": "M1",
    "01": "M1",
    "6": "M6",
    "06": "M6",
}
_PAUSE_MCODE_PAT = re.compile(r"(?<![0-9])M(0|00|1|01|6|06)(?![0-9])")


class GrblWorker:
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
        self.ser: Optional[serial.Serial] = None
        
        # Worker threads
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        
        # Buffer management
        self._last_buffer_emit: Optional[Tuple[int, int, int]] = None
        self._last_buffer_emit_ts = 0.0
        
        # Streaming state
        self._gcode: List[str] = []
        self._streaming = False
        self._paused = False
        self._send_index = 0  # next index to send
        self._ack_index = -1  # last acked index
        self._stream_buf_used = 0
        self._stream_line_queue: deque[Tuple[int, bool, Optional[int], str]] = deque()
        self._stream_pending_item: Optional[Tuple[str, bool, Optional[int]]] = None
        self._manual_pending_item: Optional[Tuple[str, bytes, int]] = None
        self._last_manual_source: str | None = None
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
    
    def list_ports(self) -> List[str]:
        """Get list of available serial ports.
        
        Returns:
            List of port device names
        """
        if not SERIAL_AVAILABLE or list_ports is None:
            return []
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
        
        try:
            # Open serial port
            self.ser = serial.Serial(
                port,
                baudrate=baud,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_WRITE_TIMEOUT
            )
            
            # Give GRBL time to reset (some boards reset on connection)
            time.sleep(SERIAL_CONNECT_DELAY)
            
            # Clear buffers
            try:
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
            except serial.SerialException as e:
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
            
        except serial.SerialException as e:
            self.ser = None
            raise SerialConnectionError(f"Failed to connect to {port}: {e}")
        except Exception as e:
            self.ser = None
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
        
        # Notify UI
        self.ui_q.put(("ready", False))
        self.ui_q.put(("stream_state", "stopped", None))
        
        # Close serial port
        if self.ser:
            try:
                self.ser.close()
                logger.info("Serial port closed")
            except serial.SerialException as e:
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
    
    def is_streaming(self) -> bool:
        """Check if currently streaming G-code.
        
        Returns:
            True if streaming is active
        """
        return self._streaming
    
    # ========================================================================
    # COMMAND EXECUTION
    # ========================================================================
    
    def send_immediate(self, command: str, *, source: str | None = None) -> None:
        """Send command immediately (bypasses streaming).
        
        Used for manual console commands and UI buttons.
        Respects alarm state - only allows $X and $H during alarm.
        
        Args:
            command: G-code or GRBL command to send
        """
        if not self.is_connected():
            logger.warning("Cannot send command - not connected")
            return
        
        if self._streaming:
            logger.warning("Cannot send immediate command during streaming")
            try:
                self.ui_q.put(("log", f"[manual blocked] {command.strip()} (streaming active)"))
            except Exception:
                pass
            return
        
        # During alarm, only allow unlock and home commands
        if self._alarm_active:
            cmd_upper = command.strip().upper()
            if not (cmd_upper.startswith("$X") or cmd_upper.startswith("$H")):
                logger.warning(f"Command '{command}' blocked during alarm")
                return
        
        if source:
            self._last_manual_source = str(source)
        elif not self._last_manual_source:
            self._last_manual_source = "manual"
        command = command.strip()
        if not command:
            return
        
        self._outgoing_q.put(command)
    
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
        
        try:
            with self._write_lock:
                total = 0
                length = len(command)
                while total < length:
                    written = self.ser.write(command[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise serial.SerialTimeoutException("Write returned 0 bytes")
                    total += written
        except serial.SerialTimeoutException as e:
            raise SerialWriteError(f"Write timeout: {e}")
        except serial.SerialException as e:
            raise SerialWriteError(f"Serial write error: {e}")
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            raise SerialWriteError(f"Unexpected error: {e}")
    
    def unlock(self) -> None:
        """Send unlock command ($X) to clear alarm state."""
        self.send_immediate("$X")
    
    def home(self) -> None:
        """Send home command ($H) to run homing cycle."""
        self.send_immediate("$H")
    
    def reset(self, emit_state: bool = True) -> None:
        """Send soft reset (Ctrl-X).
        
        Immediately halts all motion and resets GRBL state.
        
        Args:
            emit_state: Whether to emit stream_state event
        """
        self._abort_writes.set()
        try:
            self.send_realtime(RT_RESET)
        except SerialWriteError as exc:
            logger.error(f"Reset failed: {exc}")
            self.ui_q.put(("log", f"[reset failed] {exc}"))
        # Reset local state
        self._ready = False
        self._alarm_active = False
        was_streaming = self._streaming or self._paused
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = False
            self._paused = False
        self._reset_stream_buffer()
        self._clear_outgoing()
        self._emit_buffer_fill()
        self.ui_q.put(("ready", False))
        if emit_state and was_streaming:
            self.ui_q.put(("stream_state", "stopped", None))
        self._abort_writes.clear()
    
    def hold(self) -> None:
        """Send feed hold command (!) to pause motion."""
        self.send_realtime(RT_HOLD)
    
    def resume(self) -> None:
        """Send cycle start command (~) to resume motion."""
        self.send_realtime(RT_RESUME)
    
    def spindle_on(self, rpm: int = DEFAULT_SPINDLE_RPM) -> None:
        """Turn spindle on at specified RPM.
        
        Args:
            rpm: Spindle speed in RPM (default: 12000)
            
        Raises:
            ValueError: If RPM is invalid
        """
        rpm = validate_rpm(rpm)
        self.send_immediate(f"M3 S{rpm}")
    
    def spindle_off(self) -> None:
        """Turn spindle off."""
        self.send_immediate("M5")
    
    def jog_cancel(self) -> None:
        """Cancel active jog command."""
        self.send_realtime(RT_JOG_CANCEL)

    def cancel_pending_jogs(self) -> None:
        """Remove queued jog commands from the manual queue."""
        self._purge_jog_queue.set()
        self._emit_buffer_fill()

    def manual_queue_busy(self) -> bool:
        """Return True when manual commands are still queued/pending."""
        with self._stream_lock:
            if self._manual_pending_item is not None:
                return True
            for _, is_gcode, _, _ in self._stream_line_queue:
                if not is_gcode:
                    return True
        return False

    def manual_queue_backpressure(self) -> bool:
        """Return True when manual queue is blocked by buffer limits."""
        with self._stream_lock:
            return self._manual_pending_item is not None
    
    def jog(
        self,
        dx: float,
        dy: float,
        dz: float,
        feed: float,
        unit_mode: str
    ) -> None:
        """Execute incremental jog move.
        
        Args:
            dx: X distance (incremental)
            dy: Y distance (incremental)
            dz: Z distance (incremental)
            feed: Feed rate in mm/min or inches/min
            unit_mode: "mm" or "inch"
            
        Raises:
            GrblNotConnectedException: If not connected
            ValueError: If parameters are invalid
        """
        if not self.is_connected():
            raise GrblNotConnectedException("Cannot jog - not connected")
        
        # Validate inputs
        feed = validate_feed_rate(feed)
        unit_mode = validate_unit_mode(unit_mode)
        
        gunit = "G21" if unit_mode == "mm" else "G20"
        cmd = f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"
        self.send_immediate(cmd)
    
    # ========================================================================
    # G-CODE STREAMING
    # ========================================================================
    
    def load_gcode(self, lines: List[str], *, name: str | None = None) -> None:
        """Load G-code for streaming.
        
        Args:
            lines: List of G-code lines (already cleaned)
            name: Optional job name for error reporting
        """
        self._gcode = lines
        self._gcode_name = name
        self._streaming = False
        self._paused = False
        self._send_index = 0
        self._ack_index = -1
        self._reset_stream_buffer()
        self.ui_q.put(("stream_state", "loaded", len(lines)))
        logger.info(f"Loaded {len(lines)} lines of G-code")
    
    def start_stream(self) -> None:
        """Start streaming loaded G-code from beginning."""
        if not self.is_connected():
            logger.warning("Cannot start stream - not connected")
            return
        
        if not self._gcode:
            logger.warning("Cannot start stream - no G-code loaded")
            return
        
        self._clear_outgoing()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = True
            self._paused = False
        self._abort_writes.clear()
        self._reset_stream_buffer()
        self._emit_buffer_fill()
        self.ui_q.put(("stream_state", "running", None))
        logger.info("Started G-code streaming")
    
    def start_stream_from(
        self,
        start_index: int,
        preamble: Optional[List[str]] = None
    ) -> None:
        """Resume streaming from specific line.
        
        Args:
            start_index: Zero-based index to resume from
            preamble: Optional setup commands to send first (e.g., G90, G21)
        """
        if not self.is_connected():
            logger.warning("Cannot resume stream - not connected")
            return
        
        if not self._gcode:
            logger.warning("Cannot resume stream - no G-code loaded")
            return
        
        start_index = max(0, min(start_index, len(self._gcode) - 1))
        
        self._clear_outgoing()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = True
            self._paused = False
        self._abort_writes.clear()
        self._reset_stream_buffer()
        
        with self._stream_lock:
            self._send_index = start_index
            self._ack_index = start_index - 1
            
            if preamble:
                cleaned = [ln.strip() for ln in preamble if ln and ln.strip()]
                self._resume_preamble = deque(cleaned)
        
        self._emit_buffer_fill()
        self.ui_q.put(("progress", start_index, len(self._gcode)))
        self.ui_q.put(("stream_state", "running", None))
        logger.info(f"Resumed streaming from line {start_index}")
    
    def pause_stream(self) -> None:
        """Pause active stream (feed hold)."""
        self._pause_stream()
    
    def resume_stream(self) -> None:
        """Resume paused stream (cycle start)."""
        if self._streaming:
            try:
                self.resume()
            except SerialWriteError as exc:
                logger.error(f"Resume failed: {exc}")
                self.ui_q.put(("log", f"[resume failed] {exc}"))
                return
            self._paused = False
            self.ui_q.put(("stream_state", "running", None))
            logger.info("Stream resumed")

    def _pause_stream(self, reason: str | None = None) -> None:
        if not self._streaming:
            return
        if not self._paused:
            try:
                self.hold()
            except SerialWriteError as exc:
                logger.error(f"Pause failed: {exc}")
                self.ui_q.put(("log", f"[pause failed] {exc}"))
        self._paused = True
        self.ui_q.put(("stream_state", "paused", None))
        if reason:
            self.ui_q.put(("stream_pause_reason", reason))
            logger.info(f"Stream paused ({reason})")
        else:
            logger.info("Stream paused")
    
    def stop_stream(self) -> None:
        """Stop active stream and reset."""
        self.reset(emit_state=False)
        self.ui_q.put(("stream_state", "stopped", None))
        logger.info("Stream stopped")
    
    # ========================================================================
    # STATUS MANAGEMENT
    # ========================================================================
    
    def set_status_poll_interval(self, interval: float) -> None:
        """Set status polling interval.
        
        Args:
            interval: Polling interval in seconds
            
        Raises:
            ValueError: If interval is invalid
        """
        interval = validate_interval(interval, min_val=0.01)
        
        with self._status_interval_lock:
            self._status_poll_interval = interval

        logger.debug(f"Status poll interval set to {interval}s")

    def set_status_query_failure_limit(self, limit: int) -> None:
        """Set the number of consecutive status failures before disconnect.

        Args:
            limit: Positive integer failure limit
        """
        try:
            limit = int(limit)
        except Exception:
            limit = 3
        if limit < 1:
            limit = 1
        if limit > 10:
            limit = 10
        self._status_query_failure_limit = limit
        logger.debug(f"Status query failure limit set to {limit}")
    
    def _mark_ready(self) -> None:
        """Mark GRBL as ready (banner received)."""
        if not self._ready:
            self._ready = True
            self.ui_q.put(("ready", True))
            logger.info("GRBL ready")
    
    def _handle_alarm(self, message: str) -> None:
        """Handle alarm state.
        
        Args:
            message: Alarm message from GRBL
        """
        logger.warning(f"GRBL ALARM: {message}")
        
        # Log to console
        try:
            self.ui_q.put(("log", f"[ALARM] {message}"))
        except Exception as e:
            logger.error(f"Failed to log alarm: {e}")
        
        if not self._alarm_active:
            self._alarm_active = True
        
        # Stop streaming if active
        self._abort_writes.set()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = False
            self._paused = False
        self._reset_stream_buffer()
        self._emit_buffer_fill()
        self.ui_q.put(("stream_state", "alarm", message))
        self._clear_outgoing()
        self.ui_q.put(("alarm", message))
        self._abort_writes.clear()

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================
    
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
        self._reset_stream_buffer()
        self._clear_outgoing()
        try:
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
        return (line.strip() + "\n").encode("utf-8", errors="replace")

    def _pause_reason_for_line(self, line: str) -> str | None:
        if not line:
            return None
        match = _PAUSE_MCODE_PAT.search(line.upper())
        if not match:
            return None
        return _PAUSE_MCODE_MAP.get(match.group(1))

    def _maybe_pause_after_ack(self, idx: int | None) -> None:
        if idx is None:
            return
        if self._pause_after_idx is None or idx != self._pause_after_idx:
            return
        reason = self._pause_after_reason or "M0/M1/M6"
        self._pause_after_idx = None
        self._pause_after_reason = None
        self._pause_stream(reason=reason)
        self.ui_q.put(("log", f"[stream] Paused on {reason} at line {idx + 1}"))

    def _format_stream_error(
        self,
        raw_error: str,
        idx: int | None,
        line_text: str | None,
    ) -> str:
        parts = [raw_error]
        if idx is not None:
            if self._gcode_name:
                parts.append(f"{self._gcode_name} line {idx + 1}")
            else:
                parts.append(f"line {idx + 1}")
        if line_text:
            parts.append(line_text)
        return " | ".join(parts)
    
    def _write_line(
        self,
        line: str,
        payload: Optional[bytes] = None
    ) -> bool:
        """Write line to serial port.
        
        Args:
            line: Line content (for logging)
            payload: Pre-encoded payload (optional)
            
        Returns:
            True if write succeeded, False otherwise
        """
        if not self.is_connected():
            return False
        
        try:
            if payload is None:
                payload = self._encode_line_payload(line)

            with self._write_lock:
                if self._abort_writes.is_set():
                    return False
                total = 0
                length = len(payload)
                while total < length:
                    written = self.ser.write(payload[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise serial.SerialTimeoutException("Write returned 0 bytes")
                    total += written

            return True

        except serial.SerialTimeoutException as e:
            logger.error(f"Write timeout: {e}")
            self.ui_q.put(("log", f"[write timeout] {e}"))
            if self.is_connected():
                self._signal_disconnect(f"Serial write timeout: {e}")
            return False
            
        except serial.SerialException as e:
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
    
    def _tx_loop(self, stop_evt: threading.Event) -> None:
        """Transmit thread - handles streaming and command queue.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("TX thread started")
        
        try:
            while not stop_evt.is_set():
                if not self.is_connected():
                    time.sleep(0.05)
                    continue
                
                # Handle streaming
                if self._streaming and not self._paused:
                    self._process_stream_queue()

                # Handle manual commands with buffer pacing
                self._process_manual_queue()

                if stop_evt.wait(EVENT_QUEUE_TIMEOUT):
                    break
        
        except Exception as e:
            logger.error(f"TX thread error: {e}", exc_info=True)
            self._emit_exception("TX thread error", e)
            self._signal_disconnect(f"TX thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("TX thread stopped")
    
    def _process_stream_queue(self) -> None:
        """Process streaming queue - fill GRBL buffer."""
        while True:
            if not self._streaming or self._paused or self._abort_writes.is_set():
                break
            
            with self._stream_lock:
                if not self._streaming or self._paused or self._abort_writes.is_set():
                    break
                
                stream_token = self._stream_token

                if (
                    self._pause_after_idx is not None
                    and self._send_index > self._pause_after_idx
                ):
                    break

                # Get next item to send
                item = self._stream_pending_item
                if item is None:
                    if self._resume_preamble:
                        line = self._resume_preamble[0]
                        item = (line, False, None)
                    else:
                        if self._send_index >= len(self._gcode):
                            break
                        line = self._gcode[self._send_index].strip()
                        item = (line, True, self._send_index)
                
                line, is_gcode, idx = item
                if is_gcode and idx is not None and self._pause_after_idx is None:
                    reason = self._pause_reason_for_line(line)
                    if reason:
                        self._pause_after_idx = idx
                        self._pause_after_reason = reason
                payload = self._encode_line_payload(line)
                line_len = len(payload)
                
                # Check if it fits in buffer
                usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
                can_fit = (self._stream_buf_used + line_len) <= usable
                
                if not can_fit and self._stream_buf_used > 0:
                    # Wait for buffer space
                    self._stream_pending_item = item
                    break
                
                # Allow oversized line to prevent deadlock
                # (should never happen with GRBL's 80-char line limit)
                
                # Send the line
                self._stream_pending_item = None
                if is_gcode:
                    idx = self._send_index
                    self._send_index += 1
                
                self._stream_buf_used += line_len
                self._stream_line_queue.append((line_len, is_gcode, idx, line))
            
            # Write outside lock
            if (
                self._abort_writes.is_set()
                or stream_token != self._stream_token
                or not self._streaming
                or self._paused
            ):
                with self._stream_lock:
                    if is_gcode and self._send_index > 0:
                        self._send_index -= 1
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    self._stream_pending_item = None
                self._emit_buffer_fill()
                break

            if not self._write_line(line, payload):
                # Write failed - rollback and stop streaming
                with self._stream_lock:
                    if is_gcode and self._send_index > 0:
                        self._send_index -= 1
                    
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    
                    self._stream_pending_item = item
                
                self._emit_buffer_fill()
                if not self.is_connected():
                    break
                if not (self._abort_writes.is_set() or stream_token != self._stream_token):
                    self._streaming = False
                    self._paused = False
                    self.ui_q.put(("stream_state", "error", "Write failed"))
                break
            
            # Update state
            if not is_gcode and self._resume_preamble:
                self._resume_preamble.popleft()
            
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()
            
            if is_gcode:
                self.ui_q.put(("gcode_sent", idx, line))
        
        # Check if streaming is complete
        with self._stream_lock:
            send_index = self._send_index
            ack_index = self._ack_index
            pending = bool(
                self._stream_line_queue or
                self._stream_pending_item or
                self._resume_preamble
            )
        
        if (self._streaming and
            not pending and
            send_index >= len(self._gcode) and
            ack_index >= len(self._gcode) - 1):
            self._streaming = False
            self.ui_q.put(("stream_state", "done", None))
            logger.info("Streaming complete")

    def _process_manual_queue(self) -> None:
        """Process immediate command queue with buffer pacing."""
        if self._purge_jog_queue.is_set():
            self._purge_jog_queue.clear()
            try:
                pending = []
                while True:
                    pending.append(self._outgoing_q.get_nowait())
            except queue.Empty:
                pass
            kept = []
            for cmd in pending:
                if isinstance(cmd, str) and cmd.lstrip().upper().startswith("$J="):
                    continue
                kept.append(cmd)
            for cmd in kept:
                self._outgoing_q.put(cmd)
            if self._manual_pending_item is not None:
                line, _, _ = self._manual_pending_item
                if isinstance(line, str) and line.lstrip().upper().startswith("$J="):
                    self._manual_pending_item = None
        while True:
            if self._streaming or self._paused:
                return
            if not self.is_connected():
                return
            if self._abort_writes.is_set():
                return

            if self._manual_pending_item is not None:
                line, payload, line_len = self._manual_pending_item
            else:
                try:
                    line = self._outgoing_q.get_nowait()
                except queue.Empty:
                    return

                if self._alarm_active:
                    cmd_upper = line.strip().upper()
                    if not (cmd_upper.startswith("$X") or cmd_upper.startswith("$H")):
                        self._clear_outgoing()
                        continue

                line = line.strip()
                if not line:
                    continue
                payload = self._encode_line_payload(line)
                line_len = len(payload)

            if line_len > MAX_LINE_LENGTH:
                self.ui_q.put((
                    "log",
                    f"[manual] Line too long ({line_len} > {MAX_LINE_LENGTH}): {line}",
                ))
                self._manual_pending_item = None
                continue

            drop_for_buffer = False
            usable = None
            with self._stream_lock:
                usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
                if line_len > usable and self._stream_buf_used <= 0:
                    self._manual_pending_item = None
                    drop_for_buffer = True
                else:
                    can_fit = (self._stream_buf_used + line_len) <= usable

                    if not can_fit and self._stream_buf_used > 0:
                        self._manual_pending_item = (line, payload, line_len)
                        break

                    self._manual_pending_item = None
                    self._stream_buf_used += line_len
                    self._stream_line_queue.append((line_len, False, None, line))

            if drop_for_buffer:
                self.ui_q.put((
                    "log",
                    f"[manual] Line too long for buffer ({line_len} > {usable}): {line}",
                ))
                continue

            if self._abort_writes.is_set():
                with self._stream_lock:
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    self._manual_pending_item = None
                self._emit_buffer_fill()
                break

            if not self._write_line(line, payload):
                with self._stream_lock:
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    if self.is_connected():
                        self._manual_pending_item = (line, payload, line_len)
                    else:
                        self._manual_pending_item = None
                self._emit_buffer_fill()
                break

            self.ui_q.put(("log_tx", line))
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()

    def wait_for_manual_completion(self, timeout_s: float = 30.0) -> bool:
        """Block until manual/immediate commands finish."""
        start = time.time()
        while True:
            with self._stream_lock:
                pending = bool(self._stream_line_queue) or self._manual_pending_item is not None or bool(self._resume_preamble)
            if not pending:
                return True
            if timeout_s and (time.time() - start) > timeout_s:
                return False
            time.sleep(0.01)
    
    def _rx_loop(self, stop_evt: threading.Event) -> None:
        """Receive thread - reads from GRBL and processes responses.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("RX thread started")
        buf = b""
        
        try:
            while not stop_evt.is_set():
                if not self.is_connected():
                    time.sleep(0.05)
                    continue
                
                try:
                    chunk = self.ser.read(256)
                except serial.SerialTimeoutException:
                    # Normal timeout - just continue
                    continue
                except serial.SerialException as e:
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
    
    def _handle_rx_line(self, line: str) -> None:
        """Handle received line from GRBL.
        
        Args:
            line: Line received from GRBL
        """
        # Parse status reports
        is_status = line.startswith("<") and line.endswith(">")
        
        # Log to UI
        self.ui_q.put(("log_rx", line))
        
        line_lower = line.lower()
        
        # GRBL banner
        if line_lower.startswith("grbl"):
            self._mark_ready()
        
        # Alarm detection
        if line_lower.startswith("alarm:"):
            self._handle_alarm(line)
            return
        
        if "[msg:" in line_lower and "reset to continue" in line_lower:
            self._handle_alarm(line)
            return
        
        # Command acknowledgment
        if line_lower == "ok" or line_lower.startswith("error"):
            ack_index = None
            ack_line_idx = None
            ack_line_text = None
            err_idx = None
            err_line = None

            with self._stream_lock:
                if self._stream_line_queue:
                    line_len, is_gcode, idx, sent_line = self._stream_line_queue.popleft()
                    self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    
                    if is_gcode and self._streaming:
                        self._ack_index += 1
                        ack_index = self._ack_index
                        ack_line_idx = idx
                        ack_line_text = sent_line
                        if line_lower.startswith("error"):
                            err_idx = idx
                            err_line = sent_line
            
            self._emit_buffer_fill()
            
            # Report progress
            if ack_index is not None:
                self.ui_q.put(("gcode_acked", ack_index))
                self.ui_q.put(("progress", ack_index + 1, len(self._gcode)))
            
            if line_lower == "ok":
                if ack_line_idx is not None:
                    self._maybe_pause_after_ack(ack_line_idx)

            # Pause stream on error (gSender-style) with context.
            if line_lower.startswith("error"):
                logger.error(f"GRBL error: {line}")
                if self._streaming or self._paused:
                    if err_idx == self._pause_after_idx:
                        self._pause_after_idx = None
                        self._pause_after_reason = None
                    msg = self._format_stream_error(line, err_idx, err_line)
                    self._pause_stream(reason="error")
                    self.ui_q.put(("stream_error", msg, err_idx, err_line, self._gcode_name))
                    self.ui_q.put(("log", f"[stream error] {msg}"))
                else:
                    self.ui_q.put(("manual_error", line, self._last_manual_source))
        
        # Status report
        if is_status:
            self._mark_ready()
            parts = line.strip("<>").split("|")
            state = parts[0] if parts else ""
            
            # Check for alarm in status
            if state.lower().startswith("alarm"):
                if not self._alarm_active:
                    self._handle_alarm(state)
            elif self._alarm_active:
                self._alarm_active = False
            
            # Parse buffer info
            for part in parts:
                if part.startswith("Bf:"):
                    try:
                        _, rx_free = part[3:].split(",", 1)
                        rx_free = int(rx_free.strip())

                        with self._stream_lock:
                            if (
                                self._streaming
                                or self._paused
                                or self._stream_line_queue
                                or self._stream_pending_item
                                or self._manual_pending_item
                                or self._resume_preamble
                                or self._stream_buf_used
                            ):
                                continue
                            capacity = rx_free + self._stream_buf_used
                            if capacity < RX_BUFFER_SIZE:
                                capacity = RX_BUFFER_SIZE
                            self._rx_window = capacity

                        self._emit_buffer_fill()
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse Bf field: {e}")
            
            self.ui_q.put(("status", line))
    
    def _status_loop(self, stop_evt: threading.Event) -> None:
        """Status polling thread - periodically requests status.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("Status thread started")
        
        try:
            while not stop_evt.is_set():
                if self.is_connected():
                    try:
                        self.send_realtime(RT_STATUS)
                        self._status_query_failures = 0
                    except Exception as e:
                        logger.error(f"Status query error: {e}")
                        self.ui_q.put(("log", f"[status query error] {e}"))
                        self._emit_exception("Status query error", e)
                        self._status_query_failures += 1
                        try:
                            self.ui_q.put((
                                "log",
                                f"[status] Query failed ({self._status_query_failures}/{self._status_query_failure_limit})",
                            ))
                        except Exception:
                            pass
                        if self._status_query_failures >= self._status_query_failure_limit:
                            self._signal_disconnect(f"Status query error: {e}")
                            stop_evt.set()
                            break
                        backoff = min(
                            self._status_query_backoff_max,
                            self._status_query_backoff_base * self._status_query_failures,
                        )
                        if stop_evt.wait(backoff):
                            break
                
                # Get current interval
                with self._status_interval_lock:
                    interval = self._status_poll_interval
                
                # Wait for interval or stop signal
                if stop_evt.wait(interval):
                    break
        
        except Exception as e:
            logger.error(f"Status thread error: {e}", exc_info=True)
            self._emit_exception("Status thread error", e)
            self._signal_disconnect(f"Status thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("Status thread stopped")
