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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import traceback
import threading
from dataclasses import replace
from typing import Any, TYPE_CHECKING

from simple_sender.types import (
    ConnectionEvent,
    ExecutionRecoveryState,
    GcodeSourcePhase,
    GrblWorkerState,
    NormalSessionInitializationState,
    NormalSessionPhase,
    ReadyEvent,
    RecoveryActionIdentity,
    RecoveryPhase,
    RecoveryStateSnapshot,
    StartupBannerOwnership,
    StartupBannerPhase,
    StreamInterruptedEvent,
    StreamStateEvent,
)

from .utils.constants import (
    BAUD_DEFAULT,
    SERIAL_CONNECT_DELAY,
    SERIAL_TIMEOUT,
    SERIAL_WRITE_TIMEOUT,
    GRBL_STARTUP_TIMEOUT,
)
from .utils.exceptions import SerialConnectionError
from .utils.validation import (
    validate_baud_rate,
    validate_port_name,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


class _FallbackSerialException(Exception):
    pass


class _FallbackSerialTimeout(Exception):
    pass


def _serial_exception_type(serial_module: Any | None):
    if serial_module is None:
        return _FallbackSerialException
    return getattr(serial_module, "SerialException", Exception)


def _serial_timeout_exception_type(serial_module: Any | None):
    if serial_module is None:
        return _FallbackSerialTimeout
    return getattr(serial_module, "SerialTimeoutException", Exception)


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
        def _rx_loop(
            self, stop_evt: threading.Event, generation: int, serial_port: object
        ) -> None: ...
        def _tx_loop(
            self,
            stop_evt: threading.Event,
            generation: int | None = None,
            serial_port: object | None = None,
        ) -> None: ...
        def _status_loop(
            self,
            stop_evt: threading.Event,
            generation: int | None = None,
            serial_port: object | None = None,
        ) -> None: ...
        def _serial_module(self) -> Any | None: ...
        def _serial_available(self) -> bool: ...
        def _list_ports_provider(self) -> Any | None: ...
        def _thread_join_timeout(self) -> float: ...
        def _threading_module(self) -> Any: ...
        def _time_module(self) -> Any: ...

    def list_ports(self) -> list[str]:
        """Get list of available serial ports.

        Returns:
            List of port device names
        """
        ports_provider = self._list_ports_provider()
        if not self._serial_available() or ports_provider is None:
            return []
        try:
            return [p.device for p in ports_provider.comports()]
        except Exception as exc:
            logger.warning("Failed to list serial ports: %s", exc)
            return []

    def connect(self, port: str, baud: int = BAUD_DEFAULT) -> None:
        with self._connection_lifecycle_lock:
            self._connect_session(port, baud)

    def _connect_session(self, port: str, baud: int = BAUD_DEFAULT) -> None:
        """Connect to GRBL controller.

        Args:
            port: Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            baud: Baud rate (default: 115200)

        Raises:
            SerialConnectionError: If connection fails
            ValueError: If parameters are invalid
        """
        if not self._serial_available():
            raise SerialConnectionError(
                "pyserial is required to connect to GRBL. "
                "Install with: pip install pyserial"
            )
        serial_module = self._serial_module()
        assert serial_module is not None
        serial_exc = _serial_exception_type(serial_module)
        threading_mod = self._threading_module()
        time_mod = self._time_module()

        # Validate inputs
        port = validate_port_name(port)
        baud = validate_baud_rate(baud)

        # Retire any prior connection, including a timed-out thread teardown.
        if self.is_connected() or any(
            thread is not None and thread.is_alive()
            for thread in (self._rx_thread, self._tx_thread, self._status_thread)
        ):
            disconnect_fn = getattr(self, "disconnect")
            try:
                disconnect_fn(
                    requested_by="connect",
                    reason=f"Replacing active connection for port={port}",
                )
            except TypeError:
                disconnect_fn()
            if any(
                thread is not None and thread.is_alive()
                for thread in (self._rx_thread, self._tx_thread, self._status_thread)
            ):
                raise SerialConnectionError(
                    "Prior GRBL session teardown is still running; reconnect is blocked."
                )

        rebased_recovery = None
        with self._stream_lock:
            prior_execution_uncertain = bool(
                self._recovery_state.required
                or self._auto_level_lease_blocks_ordinary_locked()
            )
        # Reset state
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    self._cancel_reset_attempt_timer_locked()
                    self._reset_attempt = None
                    self._recovery_sync_generation = None
                    self._recovery_sync_epoch = None
                    self._recovery_sync_transaction = None
                    self._connection_generation += 1
                    generation = int(self._connection_generation)
                    self._extended_wcs_supported = False
                    self._stop_evt = threading_mod.Event()
                    stop_evt = self._stop_evt
                    self._stream_token += 1
                    self._recovery_epoch += 1
                    self._retire_suspension_locked("connection generation replaced")
                    self._execution_pending = None
                    self._streaming = False
                    self._paused = False
                    self._gcode = []
                    self._quarantine_execution_locked()
                    self._set_cleared_source_locked()
                    self._approved_recovery_snapshot = None
                    self._approved_recovery_completed_state = None
                    self._approved_recovery_finalization_identity = None
                    self._approved_normal_session_snapshot = None
                    self._startup_banner_owner = None
                    self._normal_session_state = NormalSessionInitializationState(
                        connection_generation=generation,
                        recovery_epoch=int(self._recovery_epoch),
                    )
                    self._recovery_snapshot = RecoveryStateSnapshot(
                        connection_generation=generation,
                        recovery_epoch=int(self._recovery_epoch),
                    )
                    self._refresh_recovery_trust_locked()
                    if prior_execution_uncertain:
                        rebased_recovery = ExecutionRecoveryState(
                            phase=RecoveryPhase.RESET_REQUIRED,
                            reason=(
                                "A replacement connection followed unresolved execution "
                                "uncertainty and requires identity-bound recovery."
                            ),
                            connection_generation=generation,
                            recovery_epoch=int(self._recovery_epoch),
                            stream_epoch=int(self._stream_token),
                            machine_may_be_executing=True,
                        )
                        self._recovery_state = rebased_recovery
                        self._abort_writes.set()
                    else:
                        self._recovery_state = ExecutionRecoveryState(
                            phase=RecoveryPhase.NORMAL,
                            connection_generation=generation,
                            recovery_epoch=int(self._recovery_epoch),
                            stream_epoch=int(self._stream_token),
                        )
                        self._abort_writes.clear()
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._last_rx_ts = time_mod.time()
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._watchdog_ready_armed = False
        self._watchdog_ready_ts = 0.0

        opened_serial: Any | None = None
        local_threads: tuple[threading.Thread | None, ...] = (None, None, None)

        def _cleanup_failed_connect() -> None:
            stop_evt.set()
            if opened_serial is not None:
                try:
                    opened_serial.close()
                except Exception as cleanup_exc:
                    _log_suppressed("Failed closing serial port during connect cleanup", cleanup_exc)
            join_timeout = self._thread_join_timeout()
            for thread in local_threads:
                if thread and thread.is_alive():
                    try:
                        thread.join(timeout=join_timeout)
                    except Exception as cleanup_exc:
                        _log_suppressed("Failed joining worker thread during connect cleanup", cleanup_exc)
            with self._write_lock:
                owner = self._startup_banner_owner
                if (
                    owner is not None
                    and int(owner.connection_generation) == int(generation)
                    and owner.serial_port is opened_serial
                ):
                    self._retire_startup_banner_owner_locked(
                        "Startup handshake retired because connection setup failed."
                    )
            with self._connection_lock:
                if generation == self._connection_generation:
                    if (
                        self._rx_thread is local_threads[0]
                        and (local_threads[0] is None or not local_threads[0].is_alive())
                    ):
                        self._rx_thread = None
                    if (
                        self._tx_thread is local_threads[1]
                        and (local_threads[1] is None or not local_threads[1].is_alive())
                    ):
                        self._tx_thread = None
                    if (
                        self._status_thread is local_threads[2]
                        and (local_threads[2] is None or not local_threads[2].is_alive())
                    ):
                        self._status_thread = None
                    if self.ser is opened_serial:
                        self.ser = None
                    self._connect_started_ts = 0.0

        try:
            # Open serial port
            serial_port = serial_module.Serial(
                port,
                baudrate=baud,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_WRITE_TIMEOUT
            )
            opened_serial = serial_port
            logger.info(
                "Serial port opened: port=%s baud=%s generation=%s serial_id=0x%x",
                port,
                baud,
                generation,
                id(serial_port),
            )
            with self._connection_lock:
                if generation != self._connection_generation:
                    serial_port.close()
                    raise SerialConnectionError("Connection attempt was superseded.")
                self.ser = serial_port
            owner_created_at = time_mod.time()
            with self._write_lock:
                if not self._session_is_current(generation, serial_port):
                    raise SerialConnectionError("Connection attempt was superseded.")
                self._startup_banner_owner = StartupBannerOwnership(
                    connection_generation=generation,
                    serial_port=serial_port,
                    created_at=owner_created_at,
                    deadline=owner_created_at + float(GRBL_STARTUP_TIMEOUT),
                    phase=StartupBannerPhase.PENDING,
                )
                logger.info(
                    "Startup banner owner installed: generation=%s serial_id=0x%x "
                    "deadline=%.6f timeout_s=%.3f",
                    generation,
                    id(serial_port),
                    owner_created_at + float(GRBL_STARTUP_TIMEOUT),
                    float(GRBL_STARTUP_TIMEOUT),
                )
            ser = serial_port
            assert ser is not None
            self._connect_started_ts = time_mod.time()

            # Retire bytes from the previous port lifetime before the board's
            # connection-triggered reset. Clearing after the startup wait can
            # discard the generation-owned GRBL banner.
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except serial_exc as e:
                logger.warning(f"Failed to reset buffers: {e}")

            # Give GRBL time to reset (some boards reset on connection).
            time_mod.sleep(SERIAL_CONNECT_DELAY)

            if not self._session_is_current(generation, ser):
                raise SerialConnectionError("Connection attempt was superseded during startup.")

            # Start worker threads
            rx_thread = threading_mod.Thread(
                target=self._rx_loop,
                args=(stop_evt, generation, ser),
                daemon=True,
                name="GRBL-RX"
            )
            tx_thread = threading_mod.Thread(
                target=self._tx_loop,
                args=(stop_evt, generation, ser),
                daemon=True,
                name="GRBL-TX"
            )
            status_thread = threading_mod.Thread(
                target=self._status_loop,
                args=(stop_evt, generation, ser),
                daemon=True,
                name="GRBL-Status"
            )

            local_threads = (rx_thread, tx_thread, status_thread)
            with self._connection_lock:
                if generation != self._connection_generation or self.ser is not ser:
                    raise SerialConnectionError("Connection attempt was superseded before thread start.")
                self._rx_thread = rx_thread
                self._tx_thread = tx_thread
                self._status_thread = status_thread

            rx_thread.start()
            tx_thread.start()
            status_thread.start()

            self.ui_q.put(ConnectionEvent(True, port, generation=generation))
            if rebased_recovery is not None:
                self._notify_recovery_safety(rebased_recovery)
                self._emit_recovery_state(rebased_recovery)
            logger.info(f"Connected to {port} at {baud} baud")

        except serial_exc as e:
            _cleanup_failed_connect()
            raise SerialConnectionError(f"Failed to connect to {port}: {e}")
        except Exception as e:
            _cleanup_failed_connect()
            raise SerialConnectionError(f"Unexpected error connecting to {port}: {e}")

    def disconnect(self, *, requested_by: str = "api", reason: str | None = None) -> None:
        with self._connection_lifecycle_lock:
            self._disconnect_session(requested_by=requested_by, reason=reason)

    def disconnect_recovery(self, identity: RecoveryActionIdentity) -> bool:
        """Disconnect only for the exact recovery identity captured by the UI."""
        with self._connection_lifecycle_lock:
            return self._disconnect_session(
                requested_by="execution_recovery_dialog",
                reason=(
                    "operator disconnected serial during recovery; "
                    "serial disconnect is not an emergency stop"
                ),
                expected_recovery_identity=identity,
            )

    def _disconnect_session(
        self,
        *,
        requested_by: str = "api",
        reason: str | None = None,
        expected_recovery_identity: RecoveryActionIdentity | None = None,
    ) -> bool:
        """Disconnect from GRBL controller.

        Stops all worker threads and closes the serial port.
        Thread-safe and idempotent.
        """
        reason_text = str(reason or "").strip()
        requester = str(requested_by or "").strip() or "api"
        request_msg = f"Disconnect requested by {requester}"
        if reason_text:
            request_msg = f"{request_msg}: {reason_text}"
        recovery_state_to_emit = None
        with self._write_lock:
            if expected_recovery_identity is not None:
                with self._stream_lock:
                    if not self._recovery_action_matches_locked(
                        expected_recovery_identity
                    ):
                        logger.warning(
                            "Ignored stale identity-bound recovery disconnect"
                        )
                        return False
            logger.info(request_msg)
            try:
                self.ui_q.put(("log", f"[disconnect] {request_msg}"))
            except Exception as exc:
                _log_suppressed(
                    "Failed queueing disconnect-request log message to UI",
                    exc,
                )
            with self._connection_lock:
                generation = int(self._connection_generation)
                session_stop_evt = self._stop_evt
                active_serial = self.ser
                session_threads = (
                    self._rx_thread,
                    self._tx_thread,
                    self._status_thread,
                )
            with self._stream_lock:
                self._invalidate_auto_level_map_locked(
                    "Controller disconnect invalidated the installed Auto-Level map."
                )
                execution_active = bool(
                    self._streaming
                    or self._paused
                    or self._execution_pending is not None
                    or self._reset_attempt is not None
                    or self._stream_line_queue
                    or self._manual_pending_item is not None
                    or not self._outgoing_q.empty()
                    or self._auto_level_lease_blocks_ordinary_locked()
                )
            if execution_active and not self.recovery_required():
                self._enter_recovery_required(
                    f"Disconnect requested while controller execution was active or pending: {request_msg}",
                    generation=generation,
                    attempt_controller_stop=True,
                )
            self._retire_startup_banner_owner_locked(
                "Startup handshake retired by requested disconnect."
            )
            with self._stream_lock:
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                self._approved_normal_session_snapshot = None
                self._normal_session_state = NormalSessionInitializationState(
                    phase=NormalSessionPhase.INACTIVE,
                    reason="Controller session disconnected.",
                    connection_generation=generation,
                    recovery_epoch=int(self._recovery_epoch),
                )
                if self._recovery_state.required:
                    self._recovery_state = replace(
                        self._recovery_state,
                        phase=RecoveryPhase.RESET_REQUIRED,
                        reset_sent=False,
                        machine_may_be_executing=True,
                        reset_attempt_id=None,
                    )
                    recovery_state_to_emit = self._recovery_state
            # Close admission before allowing another command commit.
            session_stop_evt.set()
            with self._connection_lock:
                if generation == self._connection_generation and self.ser is active_serial:
                    self.ser = None

        with self._write_lock:
            # Reset only the retiring session's shared execution state while
            # connection replacement is excluded by the lifecycle lock.
            with self._stream_lock:
                self._retire_suspension_locked("controller disconnected")
                self._streaming = False
                self._paused = False
                self._execution_pending = None
                self._gcode = []
                if (
                    self._gcode_source_phase is GcodeSourcePhase.RESERVED
                    or not self._gcode_source_identity.cleared
                ):
                    self._set_cleared_source_locked()
                self._send_index = 0
                self._ack_index = -1
                self._ack_byte_offset = 0
                self._stream_file_size_bytes = 0
                self._reset_stream_buffer()
                self._last_buffer_emit = None
                self._last_buffer_emit_ts = 0.0
                self._clear_outgoing()
                self._emit_buffer_fill()

            self._ready = False
            self._alarm_active = False
            self._status_query_failures = 0
            self._settings_dump_active = False
            self._settings_dump_seen = False
            self._settings_dump_started_ts = 0.0
            self._watchdog_paused = False
            self._watchdog_trip_ts = 0.0
            self._watchdog_ignore_until = 0.0
            self._watchdog_ignore_reason = None
            self._watchdog_ready_armed = False
            self._watchdog_ready_ts = 0.0
            self._connect_started_ts = 0.0
            self._jog_cancel_inflight = False
            self._jog_cancel_last_sent_ts = 0.0

        # Notify UI
        recovery_epoch = self.recovery_epoch()
        self.ui_q.put(
            ReadyEvent(False, generation=generation, recovery_epoch=recovery_epoch)
        )
        if not self.recovery_required():
            self.ui_q.put(
                StreamStateEvent(
                    "stopped",
                    None,
                    generation=generation,
                    stream_epoch=self.stream_epoch(),
                    recovery_epoch=recovery_epoch,
                )
            )
        elif recovery_state_to_emit is not None:
            self._emit_recovery_state(recovery_state_to_emit)

        # Close serial port
        serial_exc = _serial_exception_type(self._serial_module())
        if active_serial:
            try:
                active_serial.close()
                logger.info("Serial port closed")
            except serial_exc as e:
                logger.error(f"Error closing serial port: {e}")
            except Exception as e:
                logger.error(f"Unexpected error closing serial port: {e}")
            finally:
                with self._connection_lock:
                    if generation == self._connection_generation and self.ser is active_serial:
                        self.ser = None

        # Wait for threads to finish
        join_timeout = self._thread_join_timeout()
        for thread in session_threads:
            if thread and thread.is_alive():
                thread.join(timeout=join_timeout)
                if thread.is_alive():
                    logger.warning(f"Thread {thread.name} did not terminate")

        with self._connection_lock:
            if generation == self._connection_generation:
                if (
                    self._rx_thread is session_threads[0]
                    and (session_threads[0] is None or not session_threads[0].is_alive())
                ):
                    self._rx_thread = None
                if (
                    self._tx_thread is session_threads[1]
                    and (session_threads[1] is None or not session_threads[1].is_alive())
                ):
                    self._tx_thread = None
                if (
                    self._status_thread is session_threads[2]
                    and (session_threads[2] is None or not session_threads[2].is_alive())
                ):
                    self._status_thread = None

        self.ui_q.put(ConnectionEvent(False, None, generation=generation))
        return True

    def is_connected(self) -> bool:
        """Check if connected to GRBL.

        Returns:
            True if connected and serial port is open
        """
        ser = self.ser
        if ser is None:
            return False
        try:
            return bool(getattr(ser, "is_open", False))
        except Exception as exc:
            _log_suppressed("Failed checking serial connection state", exc)
            return False

    def _emit_exception(self, context: str, exc: BaseException) -> None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            self.ui_q.put(("log", f"[worker] {context}: {exc}"))
            for ln in tb.splitlines():
                self.ui_q.put(("log", ln))
        except Exception as queue_exc:
            _log_suppressed("Failed queueing worker exception details", queue_exc)

    def _signal_disconnect(
        self,
        reason: str | None = None,
        *,
        generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        with self._connection_lifecycle_lock:
            with self._write_lock:
                self._signal_disconnect_session(
                    reason,
                    generation=generation,
                    serial_port=serial_port,
                )

    def _timeout_startup_banner_session(
        self,
        *,
        generation: int,
        serial_port: object,
        observed_owner: StartupBannerOwnership,
    ) -> bool:
        """Commit startup timeout only if the exact pending owner still exists."""
        hook = getattr(self, "_startup_timeout_before_revalidate_hook", None)
        if callable(hook):
            hook()
        with self._connection_lifecycle_lock:
            with self._write_lock:
                owner = self._startup_banner_owner
                if (
                    owner is not observed_owner
                    or owner.phase is not StartupBannerPhase.PENDING
                    or int(owner.connection_generation) != int(generation)
                    or owner.serial_port is not serial_port
                    or not self._session_is_current(int(generation), serial_port)
                    or self._ready
                ):
                    return False
                self._startup_banner_owner = replace(
                    owner,
                    phase=StartupBannerPhase.TIMED_OUT,
                    reason="No GRBL startup banner was received before timeout.",
                )
                logger.warning(
                    "Startup banner timeout: generation=%s serial_id=0x%x "
                    "deadline=%.6f reason=no current-session GRBL startup banner",
                    generation,
                    id(serial_port),
                    float(owner.deadline),
                )
                self._signal_disconnect_session(
                    "[status/startup] No GRBL greeting received",
                    generation=int(generation),
                    serial_port=serial_port,
                )
                return True

    def _signal_disconnect_session(
        self,
        reason: str | None = None,
        *,
        generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        """Signal an unexpected disconnect and reset internal state."""
        current_generation = self.connection_generation()
        event_generation = current_generation if generation is None else int(generation)
        with self._connection_lock:
            session_is_current = bool(
                event_generation == self._connection_generation
                and (serial_port is None or self.ser is serial_port)
            )
            active_serial = self.ser if session_is_current else None
            session_stop_evt = self._stop_evt if session_is_current else None
            if session_is_current:
                # Detach atomically so a replacement connection assigned after
                # this point cannot be closed by this session's teardown.
                self.ser = None
        if not session_is_current:
            detail = str(reason or "").strip() or "Stale connection thread reported a disconnect."
            logger.warning(
                "Ignoring stale disconnect report: event_generation=%s current_generation=%s reason=%s",
                event_generation,
                current_generation,
                detail,
            )
            return
        self._retire_startup_banner_owner_locked(
            "Startup handshake retired by unexpected disconnect."
        )
        recovery_state_to_emit = None
        with self._stream_lock:
            was_streaming = bool(
                self._streaming
                or self._paused
                or self._execution_pending is not None
                or self._reset_attempt is not None
                or self._stream_line_queue
                or self._manual_pending_item is not None
                or not self._outgoing_q.empty()
                or self._auto_level_lease_blocks_ordinary_locked()
            )
        detail = str(reason or "").strip() or "Unspecified disconnect trigger"
        logger.warning("Worker disconnect signaled: %s", detail)
        if was_streaming:
            self._enter_recovery_required(
                f"Serial connection was lost during streaming: {detail}",
                generation=current_generation,
                attempt_controller_stop=False,
            )
        with self._stream_lock:
            self._cancel_reset_attempt_timer_locked()
            self._reset_attempt = None
            self._recovery_sync_generation = None
            self._recovery_sync_epoch = None
            if self._recovery_state.required:
                self._recovery_state = replace(
                    self._recovery_state,
                    phase=RecoveryPhase.RESET_REQUIRED,
                    reset_sent=False,
                    machine_may_be_executing=True,
                    reset_attempt_id=None,
                )
                recovery_state_to_emit = self._recovery_state
        if session_stop_evt is not None:
            session_stop_evt.set()
        try:
            if active_serial is not None:
                try:
                    active_serial.close()
                except Exception as exc:
                    _log_suppressed("Failed closing serial port while signaling disconnect", exc)
        finally:
            active_serial = None
        with self._stream_lock:
            self._retire_suspension_locked("connection lost")
            self._streaming = False
            self._paused = False
            self._execution_pending = None
            self._gcode = []
            if (
                self._gcode_source_phase is GcodeSourcePhase.RESERVED
                or not self._gcode_source_identity.cleared
            ):
                self._set_cleared_source_locked()
            self._jog_cancel_inflight = False
            self._jog_cancel_last_sent_ts = 0.0
            self._ack_byte_offset = 0
            self._stream_file_size_bytes = 0
        self._ready = False
        self._alarm_active = False
        self._status_query_failures = 0
        self._settings_dump_active = False
        self._settings_dump_seen = False
        self._settings_dump_started_ts = 0.0
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._watchdog_ready_armed = False
        self._watchdog_ready_ts = 0.0
        self._connect_started_ts = 0.0
        self._reset_stream_buffer()
        self._clear_outgoing()
        with self._stream_lock:
            stream_epoch = int(self._stream_token)
            recovery_epoch = int(self._recovery_epoch)
        try:
            if recovery_state_to_emit is not None:
                self._emit_recovery_state(recovery_state_to_emit)
            if was_streaming:
                self.ui_q.put(
                    StreamInterruptedEvent(True, reason, generation=current_generation)
                )
            self.ui_q.put(
                ReadyEvent(
                    False,
                    generation=current_generation,
                    recovery_epoch=recovery_epoch,
                )
            )
            if not self.recovery_required():
                self.ui_q.put(
                    StreamStateEvent(
                        "stopped",
                        reason,
                        generation=current_generation,
                        stream_epoch=stream_epoch,
                        recovery_epoch=recovery_epoch,
                    )
                )
            self.ui_q.put(ConnectionEvent(False, None, generation=current_generation))
        except Exception as exc:
            _log_suppressed("Failed queueing disconnect state updates to UI", exc)
        try:
            self.ui_q.put(("log", f"[disconnect] {detail}"))
        except Exception as exc:
            _log_suppressed("Failed queueing disconnect reason log to UI", exc)
