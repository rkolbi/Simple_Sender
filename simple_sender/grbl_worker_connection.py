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

"""Connection management for the GRBL worker."""

from __future__ import annotations

import logging
import traceback
import threading
from typing import Any, TYPE_CHECKING

from simple_sender.types import GrblWorkerState

from .utils.constants import (
    BAUD_DEFAULT,
    SERIAL_CONNECT_DELAY,
    SERIAL_TIMEOUT,
    SERIAL_WRITE_TIMEOUT,
)
from .utils.exceptions import SerialConnectionError
from .utils.validation import (
    validate_baud_rate,
    validate_port_name,
)

logger = logging.getLogger(__name__)


class _FallbackSerialException(Exception):
    pass


class _FallbackSerialTimeout(Exception):
    pass


def _serial_exception_type():
    import simple_sender.grbl_worker as grbl_worker

    if grbl_worker.serial is None:
        return _FallbackSerialException
    return getattr(grbl_worker.serial, "SerialException", Exception)


def _serial_timeout_exception_type():
    import simple_sender.grbl_worker as grbl_worker

    if grbl_worker.serial is None:
        return _FallbackSerialTimeout
    return getattr(grbl_worker.serial, "SerialTimeoutException", Exception)


class GrblWorkerConnectionMixin(GrblWorkerState):
    """Connection lifecycle support for GRBL worker."""
    ser: Any | None
    _rx_thread: threading.Thread | None
    _tx_thread: threading.Thread | None
    _status_thread: threading.Thread | None
    _stop_evt: threading.Event
    _last_buffer_emit: tuple[int, int, int] | None
    _last_buffer_emit_ts: float
    _connect_started_ts: float
    if TYPE_CHECKING:
        def _rx_loop(self, stop_evt: threading.Event) -> None: ...
        def _tx_loop(self, stop_evt: threading.Event) -> None: ...
        def _status_loop(self, stop_evt: threading.Event) -> None: ...

    def list_ports(self) -> list[str]:
        """Get list of available serial ports.

        Returns:
            List of port device names
        """
        import simple_sender.grbl_worker as grbl_worker

        if not grbl_worker.SERIAL_AVAILABLE or grbl_worker.list_ports is None:
            return []
        assert grbl_worker.list_ports is not None
        return [p.device for p in grbl_worker.list_ports.comports()]

    def connect(self, port: str, baud: int = BAUD_DEFAULT) -> None:
        """Connect to GRBL controller.

        Args:
            port: Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            baud: Baud rate (default: 115200)

        Raises:
            SerialConnectionError: If connection fails
            ValueError: If parameters are invalid
        """
        import simple_sender.grbl_worker as grbl_worker

        if not grbl_worker.SERIAL_AVAILABLE:
            raise SerialConnectionError(
                "pyserial is required to connect to GRBL. "
                "Install with: pip install pyserial"
            )
        assert grbl_worker.serial is not None
        serial_exc = _serial_exception_type()
        threading = grbl_worker.threading
        time = grbl_worker.time

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
            self.ser = grbl_worker.serial.Serial(
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
        self._settings_dump_seen = False
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
        import simple_sender.grbl_worker as grbl_worker

        join_timeout = grbl_worker.THREAD_JOIN_TIMEOUT
        for thread in (self._rx_thread, self._tx_thread, self._status_thread):
            if thread and thread.is_alive():
                thread.join(timeout=join_timeout)
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
        self._settings_dump_seen = False
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
