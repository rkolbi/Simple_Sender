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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import os
import queue
import re
import threading
import time
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import Any, Callable, Optional, Sequence, Tuple, TYPE_CHECKING, TypeAlias, cast

from .types import (
    AutoLevelLeasePhase,
    AutoLevelLeaseState,
    AutoLevelInstallationTicket,
    AutoLevelMapProvenance,
    AutoLevelWorkflowLease,
    ControllerSuspensionPhase,
    ControllerSuspensionState,
    ExecutionPendingState,
    ExecutionRecoveryState,
    GcodeSourceIdentity,
    GcodeSourcePhase,
    MachineTrustState,
    ManualCommandResultTracker,
    ManualPendingItem,
    NormalSessionInitializationEvent,
    NormalSessionInitializationState,
    NormalSessionPhase,
    ReadyEvent,
    RecoveryActionIdentity,
    RecoveryCompleteEvent,
    RecoveryFinalizationIdentity,
    RecoveryPhase,
    RecoveryRequiredEvent,
    RecoveryStateSnapshot,
    RecoverySyncTransaction,
    ResetAttempt,
    StreamPendingItem,
    StreamQueueItem,
    StreamStateEvent,
    StreamToolChangeIdentity,
    StartupBannerOwnership,
    StartupBannerPhase,
    WorkflowAdmissionSnapshot,
    WorkIdentity,
)
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
    RT_HOLD,
    RT_RESET,
    RT_RESUME,
    RT_JOG_CANCEL,
    RT_STATUS,
    THREAD_JOIN_TIMEOUT,
    WATCHDOG_HOMING_TIMEOUT,
    WATCHDOG_SETTINGS_DUMP_TIMEOUT,
    GCODE_LIVE_WINDOW_PAST_LINES,
    GCODE_LIVE_WINDOW_NEXT_LINES,
)
from .utils.exceptions import SerialWriteError

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_RX_LOGGER = None
_RX_LOGGER_LOCK = threading.Lock()
TX_LINE_RATE_WINDOW_S = 5.0
# Keep a short rolling window for diagnostics without growing runtime memory.
QUEUE_DEPTH_SNAPSHOT_MAX = 64
# Sample often enough to show short bursts while staying cheap on low-power
# systems.
QUEUE_DEPTH_SNAPSHOT_INTERVAL_S = 0.2
# Retain a generous recent serial tail for diagnostics exports while keeping
# long unattended sessions from accumulating multi-megabyte string history.
SERIAL_ACTIVITY_HISTORY_MAX = 10000
CONTROLLER_TX_ORIGIN_HISTORY_MAX = 200

__all__ = [
    "RT_JOG_CANCEL",
    "RT_RESUME",
    "annotate_grbl_alarm",
    "annotate_grbl_error",
]


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


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
        """Initialize worker state, queues, and bounded diagnostics storage.

        The constructor only seeds state used by the connection, RX/TX/status,
        streaming, and diagnostics paths. Thread creation still happens later in
        ``connect()`` so object construction remains side-effect light.
        """
        self.ui_q = ui_event_q
        self.ser: Optional[SerialType] = None
        self._rx_logger = _get_rx_logger()

        # Worker thread placeholders and shared shutdown signaling.
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._connection_lock = threading.RLock()
        self._connection_lifecycle_lock = threading.RLock()
        self._connection_generation = 0
        self._startup_banner_owner: StartupBannerOwnership | None = None

        # Serial/buffer bookkeeping used by the TX and RX coordination paths.
        self._last_buffer_emit: Optional[Tuple[int, int, int]] = None
        self._last_buffer_emit_ts = 0.0

        # Streaming state, pending items, and live-window caches.
        self._gcode: Sequence[str] = []
        self._gcode_payload_cache: Sequence[bytes | None] | None = None
        self._gcode_pause_reason_cache: Sequence[str | None] | None = None
        self._gcode_spindle_state_cache: Sequence[bool | None] | None = None
        self._streaming = False
        self._paused = False
        self._send_index = 0  # next index to send
        self._ack_index = -1  # last acked index
        self._stream_start_index = 0
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
        self._last_manual_source: str | None = None
        self._settings_dump_active = False
        self._settings_dump_seen = False
        self._settings_dump_started_ts = 0.0
        self._pause_after_idx: Optional[int] = None
        self._pause_after_reason: str | None = None
        self._stream_tool_change_pending: StreamPendingItem | None = None
        self._stream_tool_change_name: str = ""
        self._stream_tool_change_active = False
        self._stream_tool_change_identity: StreamToolChangeIdentity | None = None
        self._stream_tool_change_request_seq = 0
        self._stream_vacuum_confirmation_required = False
        self._stream_vacuum_pending: Optional[StreamPendingItem] = None
        self._stream_vacuum_pending_on = False
        self._resume_preamble: deque[str] = deque()
        self._rx_window = RX_BUFFER_SIZE
        self._stream_token = 0
        self._recovery_epoch = 0
        self._execution_pending: ExecutionPendingState | None = None
        self._suspension_state = ControllerSuspensionState()
        self._suspension_request_seq = 0
        self._suspension_confirmation_timer: threading.Timer | None = None
        self._suspension_confirmation_timeout_s = 3.0
        self._suspension_pause_reason: str | None = None
        self._abort_writes = threading.Event()
        self._recovery_state = ExecutionRecoveryState()
        self._normal_session_state = NormalSessionInitializationState()
        self._machine_trust = MachineTrustState()
        self._auto_level_lease_seq = 0
        self._auto_level_lease_state = AutoLevelLeaseState()
        self._auto_level_installation_seq = 0
        self._auto_level_coordinate_context_epoch = 0
        self._auto_level_installed_provenance: AutoLevelMapProvenance | None = None
        self._recovery_snapshot: RecoveryStateSnapshot | None = None
        self._approved_recovery_snapshot: RecoveryStateSnapshot | None = None
        self._approved_recovery_completed_state: ExecutionRecoveryState | None = None
        self._approved_recovery_finalization_identity: RecoveryFinalizationIdentity | None = None
        self._recovery_finalization_seq = 0
        self._approved_normal_session_snapshot: RecoveryStateSnapshot | None = None
        self._recovery_sync_transaction: RecoverySyncTransaction | None = None
        self._recovery_sync_seq = 0
        self._reset_attempt_seq = 0
        self._reset_attempt: ResetAttempt | None = None
        self._reset_attempt_timer: threading.Timer | None = None
        self._reset_banner_timeout_s = 5.0
        self._recovery_sync_generation: int | None = None
        self._recovery_sync_epoch: int | None = None
        self._gcode_name: str | None = None
        self._gcode_source_seq = 0
        self._gcode_source_transaction_seq = 0
        self._gcode_source_identity = GcodeSourceIdentity(
            source_id=0,
            connection_generation=0,
            stream_epoch=0,
            recovery_epoch=0,
            cleared=True,
        )
        self._gcode_source_phase = GcodeSourcePhase.CLEARED
        self._extended_wcs_supported = False
        self._recovery_safety_hook: Any | None = None

        # Throughput, queue-depth, and serial-activity diagnostics.
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
        self._controller_tx_origin_history: deque[dict[str, Any]] = deque(
            maxlen=CONTROLLER_TX_ORIGIN_HISTORY_MAX
        )

        # Manual command queue and associated drop/backpressure accounting.
        self._outgoing_q: queue.Queue[str] = queue.Queue(maxsize=MANUAL_COMMAND_QUEUE_MAXSIZE)
        self._manual_source_queue: deque[str | None] = deque()
        self._manual_tracker_queue: deque[ManualCommandResultTracker | None] = deque()
        self._manual_identity_queue: deque[WorkIdentity] = deque()
        self._manual_command_id_seq = 0
        self._purge_jog_queue = threading.Event()
        self._manual_queue_drop_count = 0
        self._manual_queue_drop_total = 0
        self._manual_queue_last_drop_notice_ts = 0.0

        # Cross-thread coordination primitives shared by TX/status logic.
        self._stream_lock = threading.RLock()
        # Admission lock: recovery latching, queue/stream commits, serial writes,
        # and connection replacement serialize through this re-entrant lock.
        self._write_lock = threading.RLock()
        self._status_interval_lock = threading.Lock()
        self._tx_activity_evt = threading.Event()
        self._status_interval_changed_evt = threading.Event()

        # Runtime state flags and timing counters used across connection,
        # watchdog, and manual-motion status paths.
        self._status_poll_interval = STATUS_POLL_DEFAULT
        self._last_status_state_token = ""
        self._status_observation_seq = 0
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
        self._verbose_runtime_logging = False

    def connection_generation(self) -> int:
        with self._connection_lock:
            return int(self._connection_generation)

    def _session_is_current(self, generation: int, serial_port: object | None = None) -> bool:
        with self._connection_lock:
            if int(generation) != int(self._connection_generation):
                return False
            if serial_port is not None and self.ser is not serial_port:
                return False
            return True

    def startup_banner_ownership(self) -> StartupBannerOwnership | None:
        with self._write_lock:
            return self._startup_banner_owner

    def _retire_startup_banner_owner_locked(self, reason: str) -> None:
        owner = self._startup_banner_owner
        if owner is None or owner.phase is not StartupBannerPhase.PENDING:
            return
        self._startup_banner_owner = replace(
            owner,
            phase=StartupBannerPhase.RETIRED,
            reason=str(reason or "Startup handshake retired."),
        )
        logger.info(
            "Startup banner owner retired: generation=%s serial_id=0x%x reason=%s",
            owner.connection_generation,
            id(owner.serial_port),
            str(reason or "Startup handshake retired."),
        )

    def _consume_startup_banner_owner_locked(
        self,
        *,
        generation: int,
        serial_port: object,
    ) -> bool:
        owner = self._startup_banner_owner
        if (
            owner is None
            or owner.phase is not StartupBannerPhase.PENDING
            or int(owner.connection_generation) != int(generation)
            or owner.serial_port is not serial_port
            or not self._session_is_current(int(generation), serial_port)
        ):
            return False
        self._startup_banner_owner = replace(
            owner,
            phase=StartupBannerPhase.CONSUMED,
            reason="Generation-owned startup banner consumed.",
        )
        logger.info(
            "Startup banner owner consumed: generation=%s serial_id=0x%x",
            generation,
            id(serial_port),
        )
        return True

    def recovery_required(self) -> bool:
        with self._stream_lock:
            return bool(self._recovery_state.required)

    def recovery_state(self) -> ExecutionRecoveryState:
        with self._stream_lock:
            return self._recovery_state

    def suspension_state(self) -> ControllerSuspensionState:
        with self._stream_lock:
            return self._suspension_state

    def serial_port_identity(self) -> object | None:
        with self._connection_lock:
            return self.ser

    def controller_status_observation(self) -> tuple[int, int, int, str]:
        """Return generation, recovery epoch, monotonic status sequence, and state."""
        with self._write_lock:
            with self._stream_lock:
                return (
                    int(self._connection_generation),
                    int(self._recovery_epoch),
                    int(self._status_observation_seq),
                    str(self._last_status_state_token or ""),
                )

    def _auto_level_lease_blocks_ordinary_locked(self) -> bool:
        return self._auto_level_lease_state.phase in {
            AutoLevelLeasePhase.ACQUIRED,
            AutoLevelLeasePhase.ACTIVE,
            AutoLevelLeasePhase.CANCEL_REQUESTED,
            AutoLevelLeasePhase.RECOVERY_REQUIRED,
            AutoLevelLeasePhase.COMPLETION_READY,
            AutoLevelLeasePhase.INSTALLING,
            AutoLevelLeasePhase.INSTALLED,
        }

    def auto_level_lease_blocks_ordinary(self) -> bool:
        with self._stream_lock:
            return self._auto_level_lease_blocks_ordinary_locked()

    def _retire_auto_level_lease_locked(self, reason: str) -> None:
        state = self._auto_level_lease_state
        if state.lease is None and state.phase is AutoLevelLeasePhase.AVAILABLE:
            return
        self._auto_level_lease_state = AutoLevelLeaseState(
            lease=state.lease,
            phase=AutoLevelLeasePhase.RETIRED,
            reason=str(reason or "Auto-Level workflow retired."),
        )

    def _auto_level_acquisition_busy_reason_locked(self) -> str | None:
        if self._auto_level_lease_blocks_ordinary_locked():
            return "another Auto-Level workflow owns controller admission"
        if self._streaming or self._paused:
            return "a job stream is running or paused"
        if self._gcode_source_phase is GcodeSourcePhase.RESERVED:
            return "a G-code source installation transaction is pending"
        if self._execution_pending is not None:
            return "controller execution completion is pending"
        if self._stream_pending_item is not None or self._resume_preamble:
            return "stream work is pending"
        if self._stream_tool_change_pending is not None or self._stream_tool_change_active:
            return "a tool-change workflow is pending"
        if self._stream_vacuum_pending is not None:
            return "an accessory directive is pending"
        if self._manual_pending_item is not None or self._stream_line_queue:
            return "a controller command has reserved RX-buffer space"
        if (
            not self._outgoing_q.empty()
            or self._manual_source_queue
            or self._manual_tracker_queue
            or self._manual_identity_queue
        ):
            return "a manual or tracked command is queued"
        if self._reset_attempt is not None or self._recovery_sync_transaction is not None:
            return "reset or controller-state synchronization is active"
        if self._settings_dump_active:
            return "a controller settings transaction is active"
        if self._jog_cancel_inflight or self._manual_motion_status_active():
            return "jog or manual-motion state is active or uncertain"
        state_token = str(self._last_status_state_token or "").strip().lower()
        if state_token.startswith(("run", "jog", "hold", "door", "home")):
            return f"controller state is {state_token}"
        if self._abort_writes.is_set():
            return "ordinary command admission is closed"
        return None

    def acquire_auto_level_lease(self) -> tuple[AutoLevelWorkflowLease | None, str]:
        """Atomically acquire exclusive controller ownership for Auto-Level."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    ser = self.ser
                    if ser is None or not bool(getattr(ser, "is_open", False)):
                        return None, "controller is disconnected"
                    if not self._ready:
                        return None, "controller communication is not ready"
                    if self._recovery_state.phase is not RecoveryPhase.NORMAL:
                        return None, "execution recovery is required"
                    if self._normal_session_state.required:
                        return None, "normal-session initialization is incomplete"
                    if self._alarm_active:
                        return None, "controller alarm is active"
                    missing = self._machine_trust.missing_for_new_job()
                    if missing:
                        return None, "machine state is not trusted: " + ", ".join(missing)
                    busy_reason = self._auto_level_acquisition_busy_reason_locked()
                    if busy_reason:
                        return None, busy_reason
                    self._invalidate_auto_level_map_locked(
                        "A new Auto-Level workflow replaced the installed map."
                    )
                    self._auto_level_lease_seq += 1
                    lease = AutoLevelWorkflowLease(
                        connection_generation=int(self._connection_generation),
                        serial_port=ser,
                        stream_epoch=int(self._stream_token),
                        recovery_epoch=int(self._recovery_epoch),
                        workflow_token=int(self._auto_level_lease_seq),
                        recovery_phase=self._recovery_state.phase,
                        alarm_active=bool(self._alarm_active),
                        machine_trust=self._machine_trust,
                        communication_ready=bool(self._ready),
                        normal_session_required=bool(self._normal_session_state.required),
                        source_identity=self._gcode_source_identity,
                        execution_busy=False,
                        coordinate_context_epoch=int(
                            self._auto_level_coordinate_context_epoch
                        ),
                    )
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=AutoLevelLeasePhase.ACQUIRED,
                    )
                    return lease, ""

    def _auto_level_lease_identity_current_locked(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        phases: set[AutoLevelLeasePhase] | None = None,
    ) -> bool:
        state = self._auto_level_lease_state
        allowed = phases or {AutoLevelLeasePhase.ACTIVE}
        return bool(
            state.lease is lease
            and state.phase in allowed
            and int(lease.connection_generation) == int(self._connection_generation)
            and lease.serial_port is self.ser
            and bool(getattr(self.ser, "is_open", False))
            and int(lease.stream_epoch) == int(self._stream_token)
            and int(lease.recovery_epoch) == int(self._recovery_epoch)
            and self._recovery_state.phase is RecoveryPhase.NORMAL
            and not self._normal_session_state.required
            and bool(self._ready)
            and not self._alarm_active
            and not self._machine_trust.missing_for_new_job()
            and self._machine_trust == lease.machine_trust
            and self._gcode_source_identity is lease.source_identity
            and not lease.execution_busy
            and int(lease.coordinate_context_epoch)
            == int(self._auto_level_coordinate_context_epoch)
        )

    def _auto_level_has_foreign_work_locked(
        self,
        lease: AutoLevelWorkflowLease,
    ) -> bool:
        if self._streaming or self._paused or self._execution_pending is not None:
            return True
        if self._stream_pending_item is not None or self._resume_preamble:
            return True
        if self._stream_tool_change_pending is not None or self._stream_vacuum_pending is not None:
            return True
        pending = self._manual_pending_item
        if pending is not None and pending.auto_level_lease is not lease:
            return True
        for item in self._stream_line_queue:
            if item.auto_level_lease is not lease:
                return True
        queued_count = self._outgoing_q.qsize()
        if not (
            queued_count
            == len(self._manual_source_queue)
            == len(self._manual_tracker_queue)
            == len(self._manual_identity_queue)
        ):
            return True
        for tracker in self._manual_tracker_queue:
            if tracker is None or tracker.auto_level_lease is not lease:
                return True
        return bool(self._reset_attempt is not None or self._recovery_sync_transaction is not None)

    def _auto_level_has_any_command_work_locked(self) -> bool:
        return bool(
            self._streaming
            or self._paused
            or self._execution_pending is not None
            or self._stream_pending_item is not None
            or self._resume_preamble
            or self._stream_tool_change_pending is not None
            or self._stream_vacuum_pending is not None
            or self._manual_pending_item is not None
            or self._stream_line_queue
            or not self._outgoing_q.empty()
            or self._manual_source_queue
            or self._manual_tracker_queue
            or self._manual_identity_queue
            or self._reset_attempt is not None
            or self._recovery_sync_transaction is not None
        )

    def activate_auto_level_lease(self, lease: AutoLevelWorkflowLease) -> bool:
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if not self._auto_level_lease_identity_current_locked(
                        lease,
                        phases={AutoLevelLeasePhase.ACQUIRED},
                    ):
                        return False
                    if self._auto_level_has_foreign_work_locked(lease):
                        self._retire_auto_level_lease_locked(
                            "Controller work appeared while Auto-Level admission was being acquired."
                        )
                        return False
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=AutoLevelLeasePhase.ACTIVE,
                    )
                    return True

    def auto_level_lease_current(self, lease: AutoLevelWorkflowLease) -> bool:
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    return bool(
                        self._auto_level_lease_identity_current_locked(lease)
                        and not self._auto_level_has_foreign_work_locked(lease)
                    )

    def complete_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        success: bool,
        reason: str = "",
    ) -> bool:
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if not self._auto_level_lease_identity_current_locked(lease):
                        return False
                    if self._auto_level_has_any_command_work_locked():
                        return False
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=(
                            AutoLevelLeasePhase.COMPLETION_READY
                            if success
                            else AutoLevelLeasePhase.FAILED
                        ),
                        reason=str(reason or ""),
                    )
                    return True

    def authorize_auto_level_installation(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        restoration_confirmed: bool,
    ) -> AutoLevelInstallationTicket | None:
        """Create the exact single-use ticket for one staged map installation."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if not self._auto_level_lease_identity_current_locked(
                        lease,
                        phases={AutoLevelLeasePhase.COMPLETION_READY},
                    ):
                        return None
                    if (
                        not restoration_confirmed
                        or self._auto_level_has_any_command_work_locked()
                    ):
                        return None
                    self._auto_level_installation_seq += 1
                    installation_id = int(self._auto_level_installation_seq)
                    provenance = AutoLevelMapProvenance(
                        connection_generation=int(self._connection_generation),
                        serial_port=self.ser,
                        stream_epoch=int(self._stream_token),
                        recovery_epoch=int(self._recovery_epoch),
                        source_identity=self._gcode_source_identity,
                        workflow_token=int(lease.workflow_token),
                        installation_id=installation_id,
                        coordinate_context_epoch=int(
                            self._auto_level_coordinate_context_epoch
                        ),
                        machine_trust=self._machine_trust,
                    )
                    ticket = AutoLevelInstallationTicket(
                        lease=lease,
                        connection_generation=int(self._connection_generation),
                        serial_port=self.ser,
                        stream_epoch=int(self._stream_token),
                        recovery_epoch=int(self._recovery_epoch),
                        source_identity=self._gcode_source_identity,
                        alarm_active=bool(self._alarm_active),
                        machine_trust=self._machine_trust,
                        restoration_confirmed=True,
                        installation_id=installation_id,
                        coordinate_context_epoch=int(
                            self._auto_level_coordinate_context_epoch
                        ),
                        provenance=provenance,
                    )
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=AutoLevelLeasePhase.INSTALLING,
                        installation_ticket=ticket,
                    )
                    return ticket

    def _auto_level_installation_ticket_current_locked(
        self,
        ticket: AutoLevelInstallationTicket,
    ) -> bool:
        state = self._auto_level_lease_state
        lease = ticket.lease
        provenance = ticket.provenance
        return bool(
            state.phase is AutoLevelLeasePhase.INSTALLING
            and state.lease is lease
            and state.installation_ticket is ticket
            and provenance is not None
            and ticket.connection_generation == self._connection_generation
            and ticket.serial_port is self.ser
            and bool(getattr(self.ser, "is_open", False))
            and ticket.stream_epoch == self._stream_token
            and ticket.recovery_epoch == self._recovery_epoch
            and ticket.source_identity is self._gcode_source_identity
            and ticket.coordinate_context_epoch
            == self._auto_level_coordinate_context_epoch
            and ticket.restoration_confirmed
            and not ticket.alarm_active
            and ticket.machine_trust == self._machine_trust
            and not self._machine_trust.missing_for_new_job()
            and self._recovery_state.phase is RecoveryPhase.NORMAL
            and not self._normal_session_state.required
            and self._ready
            and not self._alarm_active
            and not self._auto_level_has_any_command_work_locked()
            and provenance.connection_generation == ticket.connection_generation
            and provenance.serial_port is ticket.serial_port
            and provenance.stream_epoch == ticket.stream_epoch
            and provenance.recovery_epoch == ticket.recovery_epoch
            and provenance.source_identity is ticket.source_identity
            and provenance.workflow_token == lease.workflow_token
            and provenance.installation_id == ticket.installation_id
            and provenance.coordinate_context_epoch
            == ticket.coordinate_context_epoch
            and provenance.machine_trust == ticket.machine_trust
        )

    def finalize_auto_level_installation(
        self,
        ticket: AutoLevelInstallationTicket,
        commit: Callable[[AutoLevelMapProvenance], bool],
    ) -> AutoLevelMapProvenance | None:
        """Atomically validate, publish plain map fields, and retire one ticket.

        ``commit`` must only assign non-Tk application data and must not block.
        """
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if not self._auto_level_installation_ticket_current_locked(ticket):
                        state = self._auto_level_lease_state
                        if (
                            state.phase is AutoLevelLeasePhase.INSTALLING
                            and state.lease is ticket.lease
                            and state.installation_ticket is ticket
                        ):
                            self._auto_level_lease_state = AutoLevelLeaseState(
                                lease=ticket.lease,
                                phase=AutoLevelLeasePhase.INSTALLATION_FAILED,
                                reason=(
                                    "Auto-Level map installation context changed before finalization."
                                ),
                                installation_ticket=ticket,
                            )
                            self._retire_auto_level_lease_locked(
                                "Auto-Level map installation context changed before finalization."
                            )
                        return None
                    provenance = ticket.provenance
                    assert provenance is not None
                    try:
                        committed = bool(commit(provenance))
                    except Exception:
                        committed = False
                    if not committed:
                        self._auto_level_lease_state = AutoLevelLeaseState(
                            lease=ticket.lease,
                            phase=AutoLevelLeasePhase.INSTALLATION_FAILED,
                            reason="UI staging commit failed.",
                            installation_ticket=ticket,
                        )
                        self._retire_auto_level_lease_locked(
                            "Auto-Level map installation failed before publication."
                        )
                        return None
                    self._auto_level_installed_provenance = provenance
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=ticket.lease,
                        phase=AutoLevelLeasePhase.INSTALLED,
                        installation_ticket=ticket,
                    )
                    self._retire_auto_level_lease_locked(
                        "Auto-Level map installation finalized."
                    )
                    return provenance

    def abort_auto_level_installation(
        self,
        ticket: AutoLevelInstallationTicket,
        reason: str,
    ) -> bool:
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    state = self._auto_level_lease_state
                    if (
                        state.phase is not AutoLevelLeasePhase.INSTALLING
                        or state.installation_ticket is not ticket
                        or state.lease is not ticket.lease
                    ):
                        return False
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=ticket.lease,
                        phase=AutoLevelLeasePhase.INSTALLATION_FAILED,
                        reason=str(reason or "Auto-Level map installation aborted."),
                        installation_ticket=ticket,
                    )
                    self._retire_auto_level_lease_locked(reason)
                    return True

    def auto_level_map_provenance_current(
        self,
        provenance: AutoLevelMapProvenance | None,
    ) -> bool:
        if provenance is None:
            return False
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    return bool(
                        self._auto_level_installed_provenance is provenance
                        and provenance.connection_generation
                        == self._connection_generation
                        and provenance.serial_port is self.ser
                        and bool(getattr(self.ser, "is_open", False))
                        and provenance.stream_epoch == self._stream_token
                        and provenance.recovery_epoch == self._recovery_epoch
                        and provenance.source_identity is self._gcode_source_identity
                        and provenance.coordinate_context_epoch
                        == self._auto_level_coordinate_context_epoch
                        and provenance.machine_trust == self._machine_trust
                        and not self._machine_trust.missing_for_new_job()
                        and not self._alarm_active
                        and self._recovery_state.phase is RecoveryPhase.NORMAL
                        and not self._normal_session_state.required
                        and self._ready
                    )

    def discard_auto_level_map(
        self,
        provenance: AutoLevelMapProvenance,
        reason: str,
    ) -> bool:
        """Retire the exact installed map when the UI intentionally discards it."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if self._auto_level_installed_provenance is not provenance:
                        return False
                    self._invalidate_auto_level_map_locked(reason)
                    return True

    def _invalidate_auto_level_map_locked(self, reason: str) -> None:
        installed_provenance = self._auto_level_installed_provenance
        self._auto_level_installed_provenance = None
        if installed_provenance is not None:
            self.ui_q.put(
                (
                    "auto_level_map_invalidated",
                    installed_provenance,
                    str(reason or "Auto-Level map ownership is no longer current."),
                )
            )

    def retire_auto_level_completion(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
    ) -> bool:
        """Retire an exact unconsumed result without authorizing map installation."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    if not self._auto_level_lease_identity_current_locked(
                        lease,
                        phases={
                            AutoLevelLeasePhase.COMPLETION_READY,
                            AutoLevelLeasePhase.INSTALLING,
                        },
                    ):
                        return False
                    self._retire_auto_level_lease_locked(reason)
                    return True

    def fail_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
        *,
        require_recovery: bool,
    ) -> bool:
        generation = int(lease.connection_generation)
        with self._write_lock:
            if not self._session_is_current(generation, lease.serial_port):
                with self._stream_lock:
                    if self._auto_level_lease_state.lease is lease:
                        self._retire_auto_level_lease_locked(reason)
                return False
            with self._stream_lock:
                if self._auto_level_lease_state.lease is not lease:
                    return False
                if require_recovery:
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=AutoLevelLeasePhase.RECOVERY_REQUIRED,
                        reason=str(reason or "Auto-Level state is uncertain."),
                    )
                else:
                    self._auto_level_lease_state = AutoLevelLeaseState(
                        lease=lease,
                        phase=AutoLevelLeasePhase.FAILED,
                        reason=str(reason or "Auto-Level failed."),
                    )
            if require_recovery:
                self._enter_recovery_required(
                    str(reason or "Auto-Level controller state is uncertain."),
                    generation=generation,
                    attempt_controller_stop=True,
                )
            return True

    def cancel_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
    ) -> bool:
        with self._write_lock:
            if not self._session_is_current(
                int(lease.connection_generation), lease.serial_port
            ):
                return False
            with self._stream_lock:
                if not self._auto_level_lease_identity_current_locked(lease):
                    return self._recovery_state.required
                self._auto_level_lease_state = AutoLevelLeaseState(
                    lease=lease,
                    phase=AutoLevelLeasePhase.CANCEL_REQUESTED,
                    reason=str(reason or "Auto-Level cancellation requested."),
                )
            self._enter_recovery_required(
                str(reason or "Auto-Level was canceled while motion may be active."),
                generation=int(lease.connection_generation),
                attempt_controller_stop=True,
            )
            return self.recovery_required()

    def _workflow_admission_snapshot_locked(self) -> WorkflowAdmissionSnapshot:
        ser = self.ser
        connected = bool(ser is not None and getattr(ser, "is_open", False))
        execution_busy = bool(
            self._streaming
            or self._paused
            or self._execution_pending is not None
            or self._manual_pending_item is not None
            or self._stream_line_queue
        )
        return WorkflowAdmissionSnapshot(
            connection_generation=int(self._connection_generation),
            serial_port=ser,
            stream_epoch=int(self._stream_token),
            recovery_epoch=int(self._recovery_epoch),
            recovery_phase=self._recovery_state.phase,
            connected=connected,
            communication_ready=bool(self._ready),
            normal_session_required=bool(self._normal_session_state.required),
            execution_busy=execution_busy,
            alarm_active=bool(self._alarm_active),
            missing_trust=tuple(self._machine_trust.missing_for_new_job()),
        )

    def workflow_admission_snapshot(self) -> WorkflowAdmissionSnapshot:
        """Capture one immutable workflow admission identity under worker locks."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    return self._workflow_admission_snapshot_locked()

    def _workflow_admission_snapshot_current_locked(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        current = self._workflow_admission_snapshot_locked()
        return bool(
            snapshot.admissible
            and current.admissible
            and int(snapshot.connection_generation) == int(current.connection_generation)
            and snapshot.serial_port is current.serial_port
            and int(snapshot.stream_epoch) == int(current.stream_epoch)
            and int(snapshot.recovery_epoch) == int(current.recovery_epoch)
        )

    def _workflow_admission_identity_current_locked(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        current = self._workflow_admission_snapshot_locked()
        return bool(
            snapshot.admissible
            and current.connected
            and current.communication_ready
            and current.recovery_phase is RecoveryPhase.NORMAL
            and not current.normal_session_required
            and int(snapshot.connection_generation) == int(current.connection_generation)
            and snapshot.serial_port is current.serial_port
            and int(snapshot.stream_epoch) == int(current.stream_epoch)
            and int(snapshot.recovery_epoch) == int(current.recovery_epoch)
        )

    def workflow_admission_snapshot_current(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        """Revalidate an exact workflow snapshot without retargeting a new session."""
        with self._write_lock:
            with self._connection_lock:
                with self._stream_lock:
                    return self._workflow_admission_identity_current_locked(snapshot)

    def _cancel_suspension_confirmation_timer_locked(self) -> None:
        timer = self._suspension_confirmation_timer
        self._suspension_confirmation_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception as exc:
                _log_suppressed("Failed canceling suspension confirmation timer", exc)

    def _suspension_identity_current_locked(
        self,
        state: ControllerSuspensionState | None = None,
    ) -> bool:
        current = self._suspension_state if state is None else state
        return bool(
            int(current.connection_generation) == int(self._connection_generation)
            and current.serial_port is self.ser
            and int(current.stream_epoch) == int(self._stream_token)
            and int(current.recovery_epoch) == int(self._recovery_epoch)
        )

    def _suspension_blocks_tx_locked(self) -> bool:
        state = self._suspension_state
        return bool(
            state.tx_admission_closed
            and self._suspension_identity_current_locked(state)
            and state.phase
            not in {
                ControllerSuspensionPhase.NONE,
                ControllerSuspensionPhase.RESUME_CONFIRMED,
                ControllerSuspensionPhase.RETIRED,
            }
        )

    def _retire_suspension_locked(self, controller_state: str = "") -> None:
        self._cancel_suspension_confirmation_timer_locked()
        previous = self._suspension_state
        self._suspension_state = ControllerSuspensionState(
            phase=ControllerSuspensionPhase.RETIRED,
            connection_generation=int(self._connection_generation),
            serial_port=self.ser,
            stream_epoch=int(self._stream_token),
            recovery_epoch=int(self._recovery_epoch),
            request_id=int(previous.request_id),
            controller_state=str(controller_state or previous.controller_state),
        )

    def _set_suspension_locked(
        self,
        phase: ControllerSuspensionPhase,
        *,
        request_id: int,
        controller_state: str = "",
        tx_admission_closed: bool,
        operator_ack_required: bool,
        serial_port: object | None = None,
    ) -> ControllerSuspensionState:
        if serial_port is None:
            serial_port = self.ser
        state = ControllerSuspensionState(
            phase=phase,
            connection_generation=int(self._connection_generation),
            serial_port=serial_port,
            stream_epoch=int(self._stream_token),
            recovery_epoch=int(self._recovery_epoch),
            request_id=int(request_id),
            controller_state=str(controller_state or ""),
            tx_admission_closed=bool(tx_admission_closed),
            operator_ack_required=bool(operator_ack_required),
        )
        self._suspension_state = state
        return state

    def _arm_suspension_confirmation_timer_locked(
        self,
        state: ControllerSuspensionState,
    ) -> None:
        self._cancel_suspension_confirmation_timer_locked()
        timer = threading.Timer(
            float(self._suspension_confirmation_timeout_s),
            self._suspension_confirmation_timeout,
            args=(state,),
        )
        timer.daemon = True
        self._suspension_confirmation_timer = timer
        timer.start()

    def _suspension_confirmation_timeout(
        self,
        expected: ControllerSuspensionState,
    ) -> None:
        reason = ""
        with self._write_lock:
            with self._stream_lock:
                current = self._suspension_state
                if (
                    int(current.request_id) != int(expected.request_id)
                    or int(current.connection_generation)
                    != int(expected.connection_generation)
                    or current.serial_port is not expected.serial_port
                    or int(current.stream_epoch) != int(expected.stream_epoch)
                    or int(current.recovery_epoch) != int(expected.recovery_epoch)
                    or not self._suspension_identity_current_locked(current)
                    or current.phase
                    not in {
                        ControllerSuspensionPhase.APPLICATION_HOLD_REQUESTED,
                        ControllerSuspensionPhase.RESUME_REQUESTED,
                    }
                    or self._recovery_state.required
                ):
                    return
                self._suspension_confirmation_timer = None
                self._suspension_state = replace(
                    current,
                    phase=ControllerSuspensionPhase.UNCERTAIN,
                    tx_admission_closed=True,
                    operator_ack_required=True,
                )
                reason = (
                    "GRBL did not confirm feed hold before timeout; execution state is uncertain."
                    if current.phase
                    is ControllerSuspensionPhase.APPLICATION_HOLD_REQUESTED
                    else "GRBL did not confirm leaving Hold/Door after Resume; execution state is uncertain."
                )
        if reason:
            self._enter_recovery_required(
                reason,
                generation=int(expected.connection_generation),
                attempt_controller_stop=True,
            )

    def request_workflow_recovery(
        self,
        reason: str,
        *,
        expected_generation: int,
        expected_serial_port: object,
        expected_recovery_epoch: int,
    ) -> bool:
        """Fail closed for an exact current workflow whose motion became uncertain."""
        with self._write_lock:
            if not self._session_is_current(
                int(expected_generation), expected_serial_port
            ):
                return False
            with self._stream_lock:
                if int(expected_recovery_epoch) != int(self._recovery_epoch):
                    return False
                if self._recovery_state.required:
                    return True
            self._enter_recovery_required(
                str(reason or "Workflow motion was canceled while execution was uncertain."),
                generation=int(expected_generation),
                attempt_controller_stop=True,
            )
            return self.recovery_required()

    def _observe_controller_suspension_status(
        self,
        state_token: str,
        *,
        generation: int,
        serial_port: object | None,
    ) -> None:
        """Admit one current-session Hold/Door/exit observation."""
        token = str(state_token or "").strip()
        lower = token.lower()
        emitted_state: str | None = None
        emitted_detail: str | None = None
        confirmed_pause_reason: str | None = None
        admitted_serial = self.ser if serial_port is None else serial_port
        if not self._session_is_current(int(generation), admitted_serial):
            return
        with self._stream_lock:
            active_execution = bool(
                self._streaming
                or self._paused
                or self._execution_pending is not None
            )
            if not active_execution or self._recovery_state.required:
                return
            current = self._suspension_state
            current_matches = self._suspension_identity_current_locked(current)
            request_id = int(current.request_id) if current_matches else 0
            if lower.startswith("door"):
                self._paused = True
                if (
                    current_matches
                    and current.phase is ControllerSuspensionPhase.RESUME_REQUESTED
                ):
                    self._suspension_state = replace(
                        current,
                        controller_state=token,
                        tx_admission_closed=True,
                        operator_ack_required=True,
                    )
                    emitted_state = "resume_requested"
                    emitted_detail = token
                else:
                    self._cancel_suspension_confirmation_timer_locked()
                    self._suspension_request_seq += 1
                    request_id = int(self._suspension_request_seq)
                    self._set_suspension_locked(
                        ControllerSuspensionPhase.SAFETY_DOOR,
                        request_id=request_id,
                        controller_state=token,
                        tx_admission_closed=True,
                        operator_ack_required=True,
                        serial_port=admitted_serial,
                    )
                    emitted_state = "door_suspended"
                    emitted_detail = token
            elif lower.startswith("hold"):
                self._paused = True
                if current_matches and current.phase is ControllerSuspensionPhase.RESUME_REQUESTED:
                    self._suspension_state = replace(
                        current,
                        controller_state=token,
                        tx_admission_closed=True,
                    )
                    return
                if (
                    current_matches
                    and current.phase
                    is ControllerSuspensionPhase.APPLICATION_HOLD_REQUESTED
                ):
                    self._cancel_suspension_confirmation_timer_locked()
                    self._set_suspension_locked(
                        ControllerSuspensionPhase.APPLICATION_HOLD_CONFIRMED,
                        request_id=request_id,
                        controller_state=token,
                        tx_admission_closed=True,
                        operator_ack_required=False,
                        serial_port=admitted_serial,
                    )
                    emitted_state = "paused"
                    emitted_detail = token
                    confirmed_pause_reason = self._suspension_pause_reason
                elif not (
                    current_matches
                    and current.phase
                    in {
                        ControllerSuspensionPhase.APPLICATION_HOLD_CONFIRMED,
                        ControllerSuspensionPhase.EXTERNAL_HOLD,
                    }
                ):
                    self._cancel_suspension_confirmation_timer_locked()
                    self._suspension_request_seq += 1
                    request_id = int(self._suspension_request_seq)
                    self._set_suspension_locked(
                        ControllerSuspensionPhase.EXTERNAL_HOLD,
                        request_id=request_id,
                        controller_state=token,
                        tx_admission_closed=True,
                        operator_ack_required=True,
                        serial_port=admitted_serial,
                    )
                    emitted_state = "external_hold"
                    emitted_detail = token
            elif (
                current_matches
                and current.phase is ControllerSuspensionPhase.RESUME_REQUESTED
                and lower.startswith(("run", "jog", "idle"))
            ):
                self._cancel_suspension_confirmation_timer_locked()
                workflow_pause_reason = self._stream_workflow_pause_reason_locked()
                self._paused = workflow_pause_reason is not None
                self._set_suspension_locked(
                    ControllerSuspensionPhase.RESUME_CONFIRMED,
                    request_id=request_id,
                    controller_state=token,
                    tx_admission_closed=False,
                    operator_ack_required=False,
                    serial_port=admitted_serial,
                )
                emitted_state = "paused" if workflow_pause_reason is not None else "running"
                emitted_detail = workflow_pause_reason or token
                confirmed_pause_reason = workflow_pause_reason
            else:
                return
            stream_epoch = int(self._stream_token)
            recovery_epoch = int(self._recovery_epoch)
        if emitted_state is not None:
            self.ui_q.put(
                StreamStateEvent(
                    emitted_state,
                    emitted_detail,
                    generation=int(generation),
                    stream_epoch=stream_epoch,
                    recovery_epoch=recovery_epoch,
                )
            )
            if confirmed_pause_reason:
                from .types import StreamPauseReasonEvent

                self.ui_q.put(
                    StreamPauseReasonEvent(
                        confirmed_pause_reason,
                        generation=int(generation),
                        stream_epoch=stream_epoch,
                        recovery_epoch=recovery_epoch,
                    )
                )
            self._signal_tx_activity()

    def recovery_epoch(self) -> int:
        with self._stream_lock:
            return int(self._recovery_epoch)

    def stream_epoch(self) -> int:
        with self._stream_lock:
            return int(self._stream_token)

    def machine_trust_state(self) -> MachineTrustState:
        with self._stream_lock:
            return self._machine_trust

    def normal_session_state(self) -> NormalSessionInitializationState:
        with self._stream_lock:
            return self._normal_session_state

    def normal_session_initialization_required(self) -> bool:
        with self._stream_lock:
            return bool(self._normal_session_state.required)

    def normal_session_action_identity(self) -> RecoveryActionIdentity:
        with self._stream_lock:
            return self._normal_session_state.action_identity

    def _normal_session_action_matches_locked(
        self,
        identity: RecoveryActionIdentity,
    ) -> bool:
        current = self._normal_session_state.action_identity
        return bool(
            self._normal_session_state.required
            and not self._recovery_state.required
            and int(identity.connection_generation) == int(current.connection_generation)
            and int(identity.recovery_epoch) == int(current.recovery_epoch)
        )

    def recovery_snapshot(self) -> RecoveryStateSnapshot | None:
        with self._stream_lock:
            return self._recovery_snapshot

    def recovery_action_identity(self) -> RecoveryActionIdentity:
        with self._stream_lock:
            return self._recovery_state.action_identity

    def _recovery_action_matches_locked(
        self,
        identity: RecoveryActionIdentity,
        *,
        require_reset_attempt: bool = True,
    ) -> bool:
        current = self._recovery_state.action_identity
        return bool(
            self._recovery_state.required
            and int(identity.connection_generation) == int(current.connection_generation)
            and int(identity.recovery_epoch) == int(current.recovery_epoch)
            and (
                not require_reset_attempt
                or identity.reset_attempt_id == current.reset_attempt_id
            )
        )

    def _refresh_recovery_trust_locked(self) -> None:
        snapshot = self._recovery_snapshot
        if snapshot is None:
            self._machine_trust = MachineTrustState.untrusted()
            return
        self._machine_trust = snapshot.to_trust_state(setup_tool_reference=False)

    def _emit_normal_session_state(self) -> None:
        with self._stream_lock:
            state = self._normal_session_state
            snapshot = self._recovery_snapshot
            generation = int(self._connection_generation)
            recovery_epoch = int(self._recovery_epoch)
        self.ui_q.put(
            NormalSessionInitializationEvent(
                state=state,
                snapshot=snapshot,
                generation=generation,
                recovery_epoch=recovery_epoch,
            )
        )

    def _normal_session_ready_locked(self) -> bool:
        self._refresh_recovery_trust_locked()
        if self._machine_trust.missing_for_new_job():
            return False
        snapshot = self._recovery_snapshot
        if snapshot is None:
            return False
        self._approved_normal_session_snapshot = snapshot
        self._normal_session_state = replace(
            self._normal_session_state,
            phase=NormalSessionPhase.SNAPSHOT_INSTALL_PENDING,
            reason=(
                "Current-session state is approved and is being installed before "
                "Job Ready can be entered."
            ),
        )
        logger.info(
            "Normal-session snapshot approved: generation=%s recovery_epoch=%s "
            "snapshot_id=0x%x transaction_id=%s position_source=%s",
            snapshot.connection_generation,
            snapshot.recovery_epoch,
            id(snapshot),
            snapshot.sync_transaction_id,
            snapshot.position_source,
        )
        return True

    def finalize_normal_session_snapshot_install(
        self,
        identity: RecoveryActionIdentity,
        snapshot: RecoveryStateSnapshot,
    ) -> bool:
        """Enter Job Ready only after the exact approved snapshot is installed."""
        with self._write_lock:
            connected = self.is_connected()
            with self._stream_lock:
                if (
                    self._normal_session_state.phase
                    is not NormalSessionPhase.SNAPSHOT_INSTALL_PENDING
                    or not self._normal_session_action_matches_locked(identity)
                    or self._approved_normal_session_snapshot is not snapshot
                    or self._recovery_snapshot is not snapshot
                    or int(snapshot.connection_generation)
                    != int(self._connection_generation)
                    or int(snapshot.recovery_epoch) != int(self._recovery_epoch)
                    or self._recovery_state.required
                    or not connected
                    or not self._ready
                ):
                    return False
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.READY,
                    reason=(
                        "Job Ready: the exact current-session machine-state snapshot "
                        "is installed."
                    ),
                )
                self._approved_normal_session_snapshot = None
                state = self._normal_session_state
                logger.info(
                    "Job Ready committed after snapshot acknowledgement: generation=%s "
                    "recovery_epoch=%s snapshot_id=0x%x",
                    snapshot.connection_generation,
                    snapshot.recovery_epoch,
                    id(snapshot),
                )
        self._emit_normal_session_state()
        return state.phase is NormalSessionPhase.READY

    def fail_normal_session_snapshot_install(
        self,
        identity: RecoveryActionIdentity,
        snapshot: RecoveryStateSnapshot,
        *,
        reason: str,
    ) -> bool:
        """Retire a failed UI handoff so only a fresh synchronization can retry."""
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._normal_session_state.phase
                    is not NormalSessionPhase.SNAPSHOT_INSTALL_PENDING
                    or not self._normal_session_action_matches_locked(identity)
                    or self._approved_normal_session_snapshot is not snapshot
                ):
                    return False
                self._approved_normal_session_snapshot = None
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.FAILED,
                    reason=str(reason or "Snapshot installation failed; synchronize again."),
                )
        self._emit_normal_session_state()
        return True

    def begin_normal_session_initialization(self, *, generation: int) -> bool:
        """Start clean-session trust initialization after the owned startup banner."""
        with self._write_lock:
            if not self._session_is_current(int(generation)):
                return False
            with self._stream_lock:
                if self._recovery_state.required:
                    return False
                self._normal_session_state = NormalSessionInitializationState(
                    phase=NormalSessionPhase.SYNCHRONIZING,
                    reason=(
                        "Communication is ready; controller state is being synchronized "
                        "before job admission."
                    ),
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                )
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._refresh_recovery_trust_locked()
                identity = self._normal_session_state.action_identity
        self._emit_normal_session_state()
        return self.request_normal_session_state_sync(identity)

    def request_normal_session_state_sync(
        self,
        identity: RecoveryActionIdentity,
    ) -> bool:
        """Request exact ``$G``/``$#`` evidence for one clean session identity."""
        with self._write_lock:
            with self._stream_lock:
                if (
                    not self._normal_session_action_matches_locked(identity)
                    or self._recovery_sync_transaction is not None
                    or self._normal_session_state.homing_started
                ):
                    return False
                generation = int(self._connection_generation)
                recovery_epoch = int(self._recovery_epoch)
                current_snapshot = self._recovery_snapshot
                if current_snapshot is None:
                    return False
                self._recovery_sync_seq += 1
                transaction_id = int(self._recovery_sync_seq)
                self._recovery_sync_seq += 1
                gc_request_id = int(self._recovery_sync_seq)
                self._recovery_sync_seq += 1
                parameters_request_id = int(self._recovery_sync_seq)
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=generation,
                    recovery_epoch=recovery_epoch,
                    sync_transaction_id=transaction_id,
                    gc_request_id=gc_request_id,
                    parameters_request_id=parameters_request_id,
                    machine_position=current_snapshot.machine_position,
                    position_source=current_snapshot.position_source,
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._recovery_sync_transaction = RecoverySyncTransaction(
                    transaction_id=transaction_id,
                    gc_request_id=gc_request_id,
                    parameters_request_id=parameters_request_id,
                    connection_generation=generation,
                    recovery_epoch=recovery_epoch,
                    purpose="normal_session",
                )
                logger.info(
                    "Normal-session synchronization started: generation=%s "
                    "recovery_epoch=%s transaction_id=%s gc_request_id=%s "
                    "parameters_request_id=%s snapshot_id=0x%x",
                    generation,
                    recovery_epoch,
                    transaction_id,
                    gc_request_id,
                    parameters_request_id,
                    id(self._recovery_snapshot),
                )
                self._recovery_sync_generation = generation
                self._recovery_sync_epoch = recovery_epoch
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.SYNCHRONIZING,
                    reason="Synchronizing current-session modal and coordinate state.",
                )
            accepted = bool(
                self._write_line(
                    "$G",
                    expected_generation=generation,
                    command_origin="GrblWorker.request_normal_session_state_sync",
                    caller_purpose="normal_session_sync_gc",
                    admission_class="normal_session_sync",
                    normal_action_identity=identity,
                )
                and self._write_line(
                    "$#",
                    expected_generation=generation,
                    command_origin="GrblWorker.request_normal_session_state_sync",
                    caller_purpose="normal_session_sync_parameters",
                    admission_class="normal_session_sync",
                    normal_action_identity=identity,
                )
            )
            if not accepted:
                with self._stream_lock:
                    if (
                        self._recovery_sync_generation == generation
                        and self._recovery_sync_epoch == recovery_epoch
                        and self._recovery_sync_transaction is not None
                        and self._recovery_sync_transaction.purpose == "normal_session"
                    ):
                        self._recovery_sync_generation = None
                        self._recovery_sync_epoch = None
                        self._recovery_sync_transaction = None
                        self._normal_session_state = replace(
                            self._normal_session_state,
                            phase=NormalSessionPhase.FAILED,
                            reason=(
                                "Normal-session state synchronization could not be sent; "
                                "job admission remains blocked."
                            ),
                        )
                        self._refresh_recovery_trust_locked()
        self._emit_normal_session_state()
        return accepted

    def accept_normal_session_position(
        self,
        identity: RecoveryActionIdentity,
    ) -> bool:
        """Accept a physically checked current-session position without claiming homing."""
        with self._write_lock:
            with self._stream_lock:
                if (
                    not self._normal_session_action_matches_locked(identity)
                    or self._normal_session_state.phase
                    is not NormalSessionPhase.POSITION_REQUIRED
                    or self._normal_session_state.homing_started
                ):
                    return False
                snapshot = self._recovery_snapshot
                if snapshot is None or snapshot.machine_position is None:
                    return False
                if not snapshot.parameters_complete or not snapshot.gc_complete:
                    return False
                self._recovery_snapshot = replace(
                    snapshot,
                    position_source="operator_accepted",
                )
                accepted = self._normal_session_ready_locked()
        self._emit_normal_session_state()
        return accepted

    def start_normal_session_homing(
        self,
        identity: RecoveryActionIdentity,
    ) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    not self._normal_session_action_matches_locked(identity)
                    or self._normal_session_state.phase
                    is not NormalSessionPhase.POSITION_REQUIRED
                    or self._normal_session_state.homing_started
                ):
                    return False
                generation = int(self._connection_generation)
                self._normal_session_state = replace(
                    self._normal_session_state,
                    homing_started=True,
                    homing_seen=False,
                    reason="Normal-session homing is in progress.",
                )
            self._suspend_homing_watchdog(reason="homing")
            accepted = bool(
                self._write_line(
                    "$H",
                    expected_generation=generation,
                    command_origin="GrblWorker.start_normal_session_homing",
                    caller_purpose="normal_session_homing",
                    admission_class="normal_session_initialization_homing",
                    normal_action_identity=identity,
                    homing_identity=identity,
                )
            )
            if not accepted:
                self.clear_watchdog_ignore("homing")
                with self._stream_lock:
                    self._normal_session_state = replace(
                        self._normal_session_state,
                        homing_started=False,
                        homing_seen=False,
                        phase=NormalSessionPhase.FAILED,
                        reason="Normal-session homing was not accepted.",
                    )
        self._emit_normal_session_state()
        return accepted

    def job_start_eligibility(
        self,
        source_identity: GcodeSourceIdentity | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        """Return the worker-authoritative reason a committed job may not start."""
        with self._stream_lock:
            missing: list[str] = []
            if source_identity != self._gcode_source_identity:
                missing.append("displayed source does not match worker source")
            if self._gcode_source_phase is not GcodeSourcePhase.COMMITTED:
                missing.append("G-code source is not committed")
            if self._gcode_source_identity.cleared or not self._gcode:
                missing.append("no committed G-code job")
            if self._recovery_state.required:
                missing.append("execution recovery is required")
            if self._normal_session_state.required:
                missing.append("normal-session initialization is incomplete")
            if self._execution_pending is not None:
                missing.append("controller Idle confirmation is pending")
            if not self._ready:
                missing.append("controller communication is not ready")
            missing.extend(self._machine_trust.missing_for_new_job())
            return (not missing, tuple(dict.fromkeys(missing)))

    def execution_pending(self) -> bool:
        with self._stream_lock:
            return self._execution_pending is not None

    def recovery_finalization_identity(self) -> RecoveryFinalizationIdentity | None:
        with self._stream_lock:
            return self._approved_recovery_finalization_identity

    def finalize_execution_completion(
        self,
        *,
        connection_generation: int,
        stream_epoch: int,
        recovery_epoch: int,
    ) -> bool:
        """Retire a completion only when the exact published event reaches the UI."""
        with self._write_lock:
            with self._stream_lock:
                pending = self._execution_pending
                if (
                    pending is None
                    or not pending.completion_published
                    or self._recovery_state.required
                    or int(connection_generation) != int(self._connection_generation)
                    or int(stream_epoch) != int(self._stream_token)
                    or int(recovery_epoch) != int(self._recovery_epoch)
                    or int(pending.connection_generation) != int(connection_generation)
                    or int(pending.stream_epoch) != int(stream_epoch)
                    or int(pending.recovery_epoch) != int(recovery_epoch)
                ):
                    return False
                self._execution_pending = None
                return True

    def _current_work_identity_locked(self) -> WorkIdentity:
        return WorkIdentity(
            connection_generation=int(self._connection_generation),
            stream_epoch=int(self._stream_token),
            recovery_epoch=int(self._recovery_epoch),
        )

    def current_work_identity(self) -> WorkIdentity:
        with self._stream_lock:
            return self._current_work_identity_locked()

    def work_identity_is_current(self, identity: WorkIdentity) -> bool:
        with self._stream_lock:
            return self._work_identity_current_locked(identity)

    def current_gcode_source_identity(self) -> GcodeSourceIdentity:
        with self._stream_lock:
            return self._gcode_source_identity

    def gcode_source_phase(self) -> GcodeSourcePhase:
        with self._stream_lock:
            return self._gcode_source_phase

    def gcode_source_is_current(
        self,
        identity: GcodeSourceIdentity,
        *,
        require_committed: bool = False,
    ) -> bool:
        with self._stream_lock:
            return bool(
                identity == self._gcode_source_identity
                and int(identity.connection_generation) == int(self._connection_generation)
                and int(identity.stream_epoch) == int(self._stream_token)
                and int(identity.recovery_epoch) == int(self._recovery_epoch)
                and (
                    not self._recovery_state.required
                    or (
                        identity.cleared
                        and self._recovery_state.phase
                        is RecoveryPhase.RECOVERY_COMPLETE
                    )
                )
                and (
                    not require_committed
                    or self._gcode_source_phase is GcodeSourcePhase.COMMITTED
                    or (
                        identity.cleared
                        and self._gcode_source_phase is GcodeSourcePhase.CLEARED
                    )
                )
            )

    def _set_cleared_source_locked(self, *, name: str | None = None) -> GcodeSourceIdentity:
        self._invalidate_auto_level_map_locked("G-code source was cleared.")
        self._gcode_source_seq += 1
        self._gcode_source_transaction_seq += 1
        identity = GcodeSourceIdentity(
            source_id=int(self._gcode_source_seq),
            connection_generation=int(self._connection_generation),
            stream_epoch=int(self._stream_token),
            recovery_epoch=int(self._recovery_epoch),
            name=name,
            cleared=True,
            transaction_id=int(self._gcode_source_transaction_seq),
        )
        self._gcode_source_identity = identity
        self._gcode_source_phase = GcodeSourcePhase.CLEARED
        self._gcode_name = None
        self._clear_gcode_send_cache()
        return identity

    def set_recovery_safety_hook(self, callback: Any | None) -> None:
        """Register the non-UI safety hook invoked after recovery latches."""
        with self._write_lock:
            self._recovery_safety_hook = callback

    def _work_identity_current_locked(self, identity: WorkIdentity) -> bool:
        return bool(
            int(identity.connection_generation) == int(self._connection_generation)
            and int(identity.stream_epoch) == int(self._stream_token)
            and int(identity.recovery_epoch) == int(self._recovery_epoch)
            and not self._recovery_state.required
            and not self._abort_writes.is_set()
        )

    def _cancel_reset_attempt_timer_locked(self) -> None:
        timer = self._reset_attempt_timer
        self._reset_attempt_timer = None
        if timer is not None:
            timer.cancel()

    def _emit_recovery_state(self, state: ExecutionRecoveryState) -> None:
        self.ui_q.put(
            ReadyEvent(
                False,
                generation=state.connection_generation,
                recovery_epoch=state.recovery_epoch,
                reset_attempt_id=state.reset_attempt_id,
            )
        )
        self.ui_q.put(
            StreamStateEvent(
                state.phase.value,
                state.reason,
                generation=state.connection_generation,
                stream_epoch=state.stream_epoch,
                recovery_epoch=state.recovery_epoch,
                reset_attempt_id=state.reset_attempt_id,
            )
        )
        self.ui_q.put(RecoveryRequiredEvent(state))

    def _notify_recovery_safety(self, state: ExecutionRecoveryState) -> None:
        callback = self._recovery_safety_hook
        if not callable(callback):
            return
        work_identity = WorkIdentity(
            connection_generation=int(state.connection_generation),
            stream_epoch=int(state.stream_epoch),
            recovery_epoch=int(state.recovery_epoch),
        )
        try:
            callback(state, work_identity, self.current_gcode_source_identity())
        except Exception as exc:
            self.ui_q.put(
                (
                    "log",
                    "[recovery] Accessory safety-OFF submission failed; "
                    f"accessory state remains unknown: {exc}",
                )
            )

    def _retire_active_recovery_for_alarm(
        self,
        reason: str,
        *,
        generation: int,
    ) -> ExecutionRecoveryState:
        """Retire every recovery proof after an alarm in the current session."""
        with self._write_lock:
            if not self._session_is_current(int(generation)):
                return self.recovery_state()
            with self._stream_lock:
                existing = self._recovery_state
                if not existing.required:
                    return existing
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                self._recovery_epoch += 1
                self._stream_token += 1
                self._retire_suspension_locked("controller reset during recovery")
                self._abort_writes.set()
                self._streaming = False
                self._paused = False
                self._gcode = []
                self._quarantine_execution_locked()
                self._set_cleared_source_locked()
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                self._recovery_sync_transaction = None
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._refresh_recovery_trust_locked()
                state = ExecutionRecoveryState(
                    phase=RecoveryPhase.RESET_REQUIRED,
                    reason=str(reason or existing.reason),
                    connection_generation=int(self._connection_generation),
                    uncertain_start_index=existing.uncertain_start_index,
                    uncertain_end_index=existing.uncertain_end_index,
                    machine_may_be_executing=True,
                    recovery_epoch=int(self._recovery_epoch),
                    stream_epoch=int(self._stream_token),
                )
                self._recovery_state = state
                self._ready = False
                self._retire_startup_banner_owner_locked(
                    "Startup handshake retired by controller reset during recovery."
                )
        self._emit_buffer_fill()
        self._notify_recovery_safety(state)
        self._emit_recovery_state(state)
        return state

    def _reset_attempt_timeout(self, attempt_id: int) -> None:
        state: ExecutionRecoveryState | None = None
        normal_generation: int | None = None
        with self._write_lock:
            with self._stream_lock:
                attempt = self._reset_attempt
                if attempt is None or int(attempt.attempt_id) != int(attempt_id):
                    return
                # Timeout and banner handling serialize on _write_lock. Retire
                # the marker here so a later banner is a fresh reset
                # observation, never a second outcome for this attempt.
                self._reset_attempt = None
                self._reset_attempt_timer = None
                if self._recovery_state.required:
                    state = replace(
                        self._recovery_state,
                        phase=RecoveryPhase.RESET_REQUIRED,
                        reset_attempt_id=None,
                        reset_timed_out=True,
                        machine_may_be_executing=True,
                    )
                    self._recovery_state = state
                else:
                    normal_generation = int(attempt.connection_generation)
            if normal_generation is not None:
                state = self._enter_recovery_required(
                    "GRBL reset banner was not received before timeout; controller state is uncertain.",
                    generation=normal_generation,
                    attempt_controller_stop=False,
                )
                with self._stream_lock:
                    state = replace(
                        self._recovery_state,
                        phase=RecoveryPhase.RESET_REQUIRED,
                        reset_attempt_id=None,
                        reset_timed_out=True,
                        machine_may_be_executing=True,
                    )
                    self._recovery_state = state
        if state is not None:
            self.ui_q.put(
                (
                    "log",
                    "[recovery] Reset confirmation timed out. Recovery remains locked; reconnect before attempting another reset.",
                )
            )
            self._emit_recovery_state(state)

    def _arm_reset_attempt_locked(self, *, purpose: str) -> ResetAttempt | None:
        if self._reset_attempt is not None:
            return None
        now = time.monotonic()
        self._reset_attempt_seq += 1
        attempt = ResetAttempt(
            attempt_id=int(self._reset_attempt_seq),
            connection_generation=int(self._connection_generation),
            recovery_epoch=int(self._recovery_epoch),
            purpose=str(purpose),
            armed_at=now,
            deadline=now + float(self._reset_banner_timeout_s),
        )
        self._reset_attempt = attempt
        return attempt

    def _start_reset_attempt(
        self,
        *,
        purpose: str,
        expected_identity: RecoveryActionIdentity | None = None,
    ) -> bool:
        state: ExecutionRecoveryState | None = None
        with self._write_lock:
            with self._stream_lock:
                if expected_identity is not None and not self._recovery_action_matches_locked(
                    expected_identity
                ):
                    return False
                if (
                    self._recovery_state.required
                    and self._recovery_state.phase is not RecoveryPhase.RESET_REQUIRED
                ):
                    self.ui_q.put(
                        (
                            "log",
                            "[reset] Recovery reset is not available in the current recovery phase.",
                        )
                    )
                    return False
                attempt = self._arm_reset_attempt_locked(purpose=purpose)
                if attempt is None:
                    self.ui_q.put(
                        ("log", "[reset] Another reset attempt is still awaiting its banner.")
                    )
                    return False
            try:
                accepted = bool(
                    self.send_realtime(
                        RT_RESET,
                        expected_generation=attempt.connection_generation,
                    )
                )
            except Exception as exc:
                accepted = False
                self.ui_q.put(("log", f"[reset failed] {exc}"))
            if not accepted:
                with self._stream_lock:
                    if self._reset_attempt == attempt:
                        self._reset_attempt = None
                    if self._recovery_state.required:
                        state = replace(
                            self._recovery_state,
                            phase=RecoveryPhase.RESET_REQUIRED,
                            reset_attempt_id=None,
                            reset_sent=False,
                            machine_may_be_executing=True,
                        )
                        self._recovery_state = state
                if state is not None:
                    self._emit_recovery_state(state)
                return False
            timer = threading.Timer(
                float(self._reset_banner_timeout_s),
                self._reset_attempt_timeout,
                args=(attempt.attempt_id,),
            )
            timer.daemon = True
            with self._stream_lock:
                if self._reset_attempt == attempt:
                    self._reset_attempt_timer = timer
                    if self._recovery_state.required:
                        state = replace(
                            self._recovery_state,
                            phase=RecoveryPhase.RESET_SENT_AWAITING_BANNER,
                            reset_attempt_id=attempt.attempt_id,
                            reset_timed_out=False,
                            reset_sent=True,
                            machine_may_be_executing=False,
                        )
                        self._recovery_state = state
                    timer.start()
                else:
                    # A re-entrant simulator or exceptionally fast controller
                    # may have delivered the matching banner during write().
                    state = None
        if state is not None:
            self._emit_recovery_state(state)
        return True

    def _confirm_recovery_reset_banner(self, attempt: ResetAttempt) -> None:
        with self._write_lock:
            with self._stream_lock:
                current = self._reset_attempt
                if current is None or current.attempt_id != attempt.attempt_id:
                    return
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._recovery_sync_transaction = None
                self._refresh_recovery_trust_locked()
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                state = replace(
                    self._recovery_state,
                    phase=RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED,
                    reset_attempt_id=attempt.attempt_id,
                    reset_timed_out=False,
                    reset_sent=True,
                    machine_may_be_executing=False,
                )
                self._recovery_state = state
                self._ready = False
        self.ui_q.put(
            (
                "log",
                "[recovery] GRBL reset confirmed. Machine position and setup remain untrusted; explicit recovery is required.",
            )
        )
        self._emit_recovery_state(state)

    def request_recovery_reset(self, identity: RecoveryActionIdentity) -> bool:
        return self._start_reset_attempt(
            purpose="recovery_explicit",
            expected_identity=identity,
        )

    def request_recovery_state_sync(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._recovery_sync_transaction is not None
                ):
                    return False
                generation = int(self._connection_generation)
                recovery_epoch = int(self._recovery_epoch)
                current_snapshot = self._recovery_snapshot
                if current_snapshot is None:
                    return False
                self._recovery_sync_seq += 1
                transaction_id = int(self._recovery_sync_seq)
                self._recovery_sync_seq += 1
                gc_request_id = int(self._recovery_sync_seq)
                self._recovery_sync_seq += 1
                parameters_request_id = int(self._recovery_sync_seq)
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=generation,
                    recovery_epoch=recovery_epoch,
                    sync_transaction_id=transaction_id,
                    gc_request_id=gc_request_id,
                    parameters_request_id=parameters_request_id,
                    machine_position=current_snapshot.machine_position,
                    position_source=current_snapshot.position_source,
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._recovery_sync_transaction = RecoverySyncTransaction(
                    transaction_id=transaction_id,
                    gc_request_id=gc_request_id,
                    parameters_request_id=parameters_request_id,
                    connection_generation=generation,
                    recovery_epoch=recovery_epoch,
                )
                self._recovery_sync_generation = generation
                self._recovery_sync_epoch = recovery_epoch
            accepted = bool(
                self._write_line(
                    "$G",
                    expected_generation=generation,
                    allow_recovery=True,
                )
                and self._write_line(
                    "$#",
                    expected_generation=generation,
                    allow_recovery=True,
                )
            )
            if not accepted:
                with self._stream_lock:
                    if (
                        self._recovery_sync_generation == generation
                        and self._recovery_sync_epoch == recovery_epoch
                    ):
                        self._recovery_sync_generation = None
                        self._recovery_sync_epoch = None
                        self._recovery_sync_transaction = None
                        self._recovery_snapshot = RecoveryStateSnapshot(
                            connection_generation=generation,
                            recovery_epoch=recovery_epoch,
                            machine_position=current_snapshot.machine_position,
                            position_source=current_snapshot.position_source,
                            extended_wcs_supported=bool(self._extended_wcs_supported),
                        )
                        self._refresh_recovery_trust_locked()
            return accepted

    def start_recovery_homing(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._alarm_active
                ):
                    return False
                generation = int(self._connection_generation)
                self._recovery_state = replace(
                    self._recovery_state,
                    homing_started=True,
                    homing_seen=False,
                )
                if self._recovery_snapshot is not None:
                    self._recovery_snapshot = replace(
                        self._recovery_snapshot,
                        machine_position=None,
                        position_source="unknown",
                    )
                    self._refresh_recovery_trust_locked()
                recovery_epoch = int(self._recovery_epoch)
            accepted = bool(self._write_line(
                "$H",
                expected_generation=generation,
                allow_recovery=True,
            ))
            if not accepted:
                with self._stream_lock:
                    if (
                        self._recovery_state.phase
                        is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                        and int(self._recovery_epoch) == recovery_epoch
                    ):
                        self._recovery_state = replace(
                            self._recovery_state,
                            homing_started=False,
                            homing_seen=False,
                        )
            return accepted

    def accept_recovery_position(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._recovery_snapshot is None
                    or self._recovery_snapshot.machine_position is None
                ):
                    return False
                self._recovery_snapshot = replace(
                    self._recovery_snapshot,
                    position_source="operator_accepted",
                )
                self._refresh_recovery_trust_locked()
        return True

    def acknowledge_recovery_spindle(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._recovery_snapshot is None
                    or not self._recovery_snapshot.gc_complete
                    or self._recovery_snapshot.spindle_mode != "M5"
                    or self._recovery_snapshot.sync_transaction_id is None
                ):
                    return False
                snapshot = self._recovery_snapshot
                self._recovery_snapshot = replace(
                    snapshot,
                    spindle_verified=True,
                    spindle_verified_mode=snapshot.spindle_mode,
                    spindle_verification_transaction_id=snapshot.sync_transaction_id,
                )
                self._refresh_recovery_trust_locked()
        return True

    def acknowledge_recovery_coolant(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._recovery_snapshot is None
                    or not self._recovery_snapshot.gc_complete
                    or self._recovery_snapshot.coolant_modes != ("M9",)
                    or self._recovery_snapshot.sync_transaction_id is None
                ):
                    return False
                snapshot = self._recovery_snapshot
                self._recovery_snapshot = replace(
                    snapshot,
                    coolant_verified=True,
                    coolant_verified_modes=snapshot.coolant_modes,
                    coolant_verification_transaction_id=snapshot.sync_transaction_id,
                )
                self._refresh_recovery_trust_locked()
        return True

    def acknowledge_recovery_accessories(self, identity: RecoveryActionIdentity) -> bool:
        with self._write_lock:
            with self._stream_lock:
                snapshot = self._recovery_snapshot
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or snapshot is None
                    or not snapshot.gc_complete
                    or snapshot.sync_transaction_id is None
                    or snapshot.spindle_mode != "M5"
                    or snapshot.coolant_modes != ("M9",)
                ):
                    return False
                self._recovery_snapshot = replace(
                    snapshot,
                    spindle_verified=True,
                    spindle_verified_mode="M5",
                    spindle_verification_transaction_id=snapshot.sync_transaction_id,
                    coolant_verified=True,
                    coolant_verified_modes=("M9",),
                    coolant_verification_transaction_id=snapshot.sync_transaction_id,
                )
                self._refresh_recovery_trust_locked()
        return True

    def mark_setup_tool_reference_trusted(self) -> None:
        with self._stream_lock:
            if not self._recovery_state.required:
                self._machine_trust = replace(
                    self._machine_trust,
                    setup_tool_reference=True,
                )

    def mark_tool_length_offset_trusted(self) -> None:
        with self._stream_lock:
            if not self._recovery_state.required:
                self._machine_trust = replace(
                    self._machine_trust,
                    tool_length_offset=True,
                )

    def complete_recovery(
        self,
        identity: RecoveryActionIdentity,
    ) -> tuple[bool, tuple[str, ...]]:
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase
                    is not RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or not self._recovery_action_matches_locked(identity)
                    or self._alarm_active
                ):
                    return False, ("reset confirmation",)
                snapshot = self._recovery_snapshot
                if snapshot is None or self._recovery_sync_transaction is not None:
                    return False, ("state synchronization",)
                self._refresh_recovery_trust_locked()
                missing = self._machine_trust.missing_for_new_job()
                if missing:
                    return False, missing
                completed = replace(
                    self._recovery_state,
                    phase=RecoveryPhase.RECOVERY_COMPLETE,
                )
                self._recovery_epoch += 1
                self._stream_token += 1
                self._retire_suspension_locked("recovery completion handoff")
                self._gcode = []
                self._quarantine_execution_locked()
                self._set_cleared_source_locked()
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                self._approved_recovery_snapshot = snapshot
                self._approved_recovery_completed_state = completed
                self._recovery_finalization_seq += 1
                finalization_identity = RecoveryFinalizationIdentity(
                    finalization_id=int(self._recovery_finalization_seq),
                    action_identity=identity,
                    connection_generation=int(self._connection_generation),
                    stream_epoch=int(self._stream_token),
                    recovery_epoch=int(self._recovery_epoch),
                )
                self._approved_recovery_finalization_identity = finalization_identity
                self._ready = False
                self._recovery_state = ExecutionRecoveryState(
                    phase=RecoveryPhase.RECOVERY_COMPLETE,
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    stream_epoch=int(self._stream_token),
                )
        self.ui_q.put(
            RecoveryCompleteEvent(
                completed_state=completed,
                snapshot=snapshot,
                finalization_identity=finalization_identity,
                generation=int(self._connection_generation),
                stream_epoch=int(self._stream_token),
                recovery_epoch=int(self._recovery_epoch),
            )
        )
        return True, ()

    def finalize_recovery_snapshot_install(
        self,
        snapshot: RecoveryStateSnapshot,
        finalization_identity: RecoveryFinalizationIdentity,
        *,
        connection_generation: int,
        recovery_epoch: int,
    ) -> bool:
        """Open ordinary admission only after the UI installs the approved values."""
        with self._write_lock:
            connected = self.is_connected()
            with self._stream_lock:
                approved_completed = self._approved_recovery_completed_state
                if (
                    self._recovery_state.phase is not RecoveryPhase.RECOVERY_COMPLETE
                    or int(connection_generation) != int(self._connection_generation)
                    or int(recovery_epoch) != int(self._recovery_epoch)
                    or self._approved_recovery_snapshot is not snapshot
                    or self._recovery_snapshot is not snapshot
                    or self._approved_recovery_finalization_identity
                    is not finalization_identity
                    or int(finalization_identity.connection_generation)
                    != int(self._connection_generation)
                    or int(finalization_identity.stream_epoch) != int(self._stream_token)
                    or int(finalization_identity.recovery_epoch) != int(self._recovery_epoch)
                    or approved_completed is None
                    or finalization_identity.action_identity
                    != approved_completed.action_identity
                    or self._alarm_active
                ):
                    return False
                self._recovery_state = ExecutionRecoveryState(
                    phase=RecoveryPhase.NORMAL,
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    stream_epoch=int(self._stream_token),
                )
                self._normal_session_state = NormalSessionInitializationState(
                    phase=NormalSessionPhase.READY,
                    reason="Recovery established current-session job trust.",
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                )
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._ready = bool(connected)
                self._abort_writes.clear()
        return True

    def retry_recovery_finalization(
        self,
        identity: RecoveryActionIdentity,
    ) -> bool:
        """Republish the exact approved handoff without reopening admission."""
        with self._write_lock:
            with self._stream_lock:
                if (
                    self._recovery_state.phase is not RecoveryPhase.RECOVERY_COMPLETE
                    or not self._recovery_action_matches_locked(
                        identity,
                        require_reset_attempt=False,
                    )
                    or self._approved_recovery_snapshot is None
                    or self._approved_recovery_completed_state is None
                    or self._approved_recovery_finalization_identity is None
                ):
                    return False
                snapshot = self._approved_recovery_snapshot
                completed = self._approved_recovery_completed_state
                finalization_identity = self._approved_recovery_finalization_identity
                generation = int(self._connection_generation)
                stream_epoch = int(self._stream_token)
                recovery_epoch = int(self._recovery_epoch)
        self.ui_q.put(
            RecoveryCompleteEvent(
                completed_state=completed,
                snapshot=snapshot,
                finalization_identity=finalization_identity,
                generation=generation,
                stream_epoch=stream_epoch,
                recovery_epoch=recovery_epoch,
            )
        )
        return True

    def _quarantine_execution_locked(self) -> None:
        """Discard work owned by the retired execution epoch.

        The admission/write lock and stream lock must both be held by the caller.
        """
        self._invalidate_auto_level_map_locked("Controller execution was quarantined.")
        lease_state = self._auto_level_lease_state
        if lease_state.lease is not None and self._auto_level_lease_blocks_ordinary_locked():
            self._auto_level_lease_state = AutoLevelLeaseState(
                lease=lease_state.lease,
                phase=AutoLevelLeasePhase.RECOVERY_REQUIRED,
                reason="Auto-Level execution was retired by controller recovery.",
            )
        for queued_item in self._stream_line_queue:
            self._resolve_manual_tracker(
                getattr(queued_item, "manual_tracker", None),
                success=False,
                error="Manual command was interrupted by execution recovery.",
            )
        self._resolve_manual_tracker(
            getattr(self._manual_pending_item, "tracker", None),
            success=False,
            error="Manual command was interrupted by execution recovery.",
        )
        self._resolve_queued_manual_trackers_locked(
            self._manual_tracker_queue,
            error="Manual command was interrupted by execution recovery.",
        )
        while True:
            try:
                self._outgoing_q.get_nowait()
            except queue.Empty:
                break
        self._manual_source_queue.clear()
        self._manual_tracker_queue.clear()
        self._manual_identity_queue.clear()
        self._manual_pending_item = None
        self._stream_line_queue.clear()
        self._stream_pending_item = None
        self._stream_buf_used = 0
        self._resume_preamble.clear()
        self._send_index = 0
        self._ack_index = -1
        self._ack_byte_offset = 0
        self._pause_after_idx = None
        self._pause_after_reason = None
        self._stream_tool_change_pending = None
        self._stream_tool_change_name = ""
        self._stream_tool_change_active = False
        self._stream_tool_change_identity = None
        self._stream_vacuum_pending = None
        self._stream_vacuum_pending_on = False
        self._live_acked_ring.clear()
        self._live_current_acked = None
        self._live_pending_window.clear()
        self._rx_window = RX_BUFFER_SIZE
        self._execution_pending = None

    def _observe_fresh_reset_during_recovery(
        self,
        *,
        generation: int,
        reason: str,
    ) -> ExecutionRecoveryState:
        """Retire all evidence and retain only a current reset observation."""
        with self._write_lock:
            if not self._session_is_current(int(generation)):
                return self.recovery_state()
            with self._stream_lock:
                existing = self._recovery_state
                if not existing.required:
                    return existing
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                self._recovery_epoch += 1
                self._stream_token += 1
                self._retire_suspension_locked("fresh reset observed during recovery")
                self._abort_writes.set()
                self._gcode = []
                self._quarantine_execution_locked()
                self._set_cleared_source_locked()
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                self._recovery_sync_transaction = None
                self._retire_startup_banner_owner_locked(
                    "Startup handshake retired by execution recovery."
                )
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._normal_session_state = NormalSessionInitializationState(
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                )
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=int(self._connection_generation),
                    recovery_epoch=int(self._recovery_epoch),
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._refresh_recovery_trust_locked()
                state = ExecutionRecoveryState(
                    phase=RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED,
                    reason=str(reason or existing.reason),
                    connection_generation=int(self._connection_generation),
                    uncertain_start_index=existing.uncertain_start_index,
                    uncertain_end_index=existing.uncertain_end_index,
                    reset_sent=True,
                    machine_may_be_executing=False,
                    recovery_epoch=int(self._recovery_epoch),
                    stream_epoch=int(self._stream_token),
                )
                self._recovery_state = state
                self._ready = False
        self._emit_buffer_fill()
        self._notify_recovery_safety(state)
        self._emit_recovery_state(state)
        return state

    def _enter_recovery_required(
        self,
        reason: str,
        *,
        generation: int | None = None,
        uncertain_start_index: int | None = None,
        uncertain_end_index: int | None = None,
        attempt_controller_stop: bool = True,
        controller_reset_observed: bool = False,
    ) -> ExecutionRecoveryState:
        detail = str(reason or "").strip() or "Controller execution state became uncertain."
        with self._write_lock:
            current_generation = self.connection_generation()
            event_generation = current_generation if generation is None else int(generation)
            if event_generation != current_generation:
                event_generation = current_generation
            with self._stream_lock:
                existing = self._recovery_state
                if existing.required:
                    return existing
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                outstanding = [
                    int(item.idx)
                    for item in self._stream_line_queue
                    if item.is_gcode and item.idx is not None
                ]
                sent_end = int(self._send_index) - 1
                stream_start = max(0, int(self._stream_start_index))
                if sent_end >= stream_start:
                    if uncertain_start_index is None:
                        uncertain_start_index = stream_start
                    else:
                        uncertain_start_index = min(
                            int(uncertain_start_index), stream_start
                        )
                if uncertain_end_index is None:
                    candidates = [sent_end, *outstanding]
                    candidates = [value for value in candidates if value >= 0]
                    uncertain_end_index = max(candidates) if candidates else None
                self._abort_writes.set()
                self._recovery_epoch += 1
                self._stream_token += 1
                self._retire_suspension_locked("execution recovery required")
                recovery_epoch = int(self._recovery_epoch)
                stream_epoch = int(self._stream_token)
                self._streaming = False
                self._paused = False
                self._gcode = []
                self._quarantine_execution_locked()
                self._set_cleared_source_locked()
                self._recovery_state = ExecutionRecoveryState(
                    phase=(
                        RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                        if controller_reset_observed
                        else RecoveryPhase.RESET_REQUIRED
                    ),
                    reason=detail,
                    connection_generation=current_generation,
                    uncertain_start_index=uncertain_start_index,
                    uncertain_end_index=uncertain_end_index,
                    machine_may_be_executing=not bool(controller_reset_observed),
                    recovery_epoch=recovery_epoch,
                    stream_epoch=stream_epoch,
                )
                self._recovery_snapshot = RecoveryStateSnapshot(
                    connection_generation=current_generation,
                    recovery_epoch=recovery_epoch,
                    extended_wcs_supported=bool(self._extended_wcs_supported),
                )
                self._approved_recovery_snapshot = None
                self._approved_recovery_completed_state = None
                self._approved_recovery_finalization_identity = None
                self._approved_normal_session_snapshot = None
                self._normal_session_state = NormalSessionInitializationState(
                    connection_generation=current_generation,
                    recovery_epoch=recovery_epoch,
                )
                self._recovery_sync_transaction = None
                self._retire_startup_banner_owner_locked(
                    "Startup handshake retired by execution recovery."
                )
                self._refresh_recovery_trust_locked()
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                initial_state = self._recovery_state

        self._notify_recovery_safety(initial_state)
        hold_sent = False
        reset_sent = bool(controller_reset_observed)
        if attempt_controller_stop and self.is_connected():
            try:
                hold_sent = bool(
                    self.send_realtime(RT_HOLD, expected_generation=current_generation)
                )
            except Exception as exc:
                self.ui_q.put(("log", f"[recovery] Feed hold could not be sent: {exc}"))
            reset_sent = self._start_reset_attempt(purpose="recovery")
        self._emit_buffer_fill()

        with self._write_lock:
            with self._stream_lock:
                if (
                    not self._recovery_state.required
                    or int(self._recovery_epoch) != recovery_epoch
                ):
                    return self._recovery_state
                state = ExecutionRecoveryState(
                    phase=(
                        self._recovery_state.phase
                        if reset_sent
                        else RecoveryPhase.RESET_REQUIRED
                    ),
                    reason=detail,
                    connection_generation=current_generation,
                    uncertain_start_index=uncertain_start_index,
                    uncertain_end_index=uncertain_end_index,
                    hold_sent=hold_sent,
                    reset_sent=reset_sent,
                    machine_may_be_executing=not reset_sent,
                    recovery_epoch=recovery_epoch,
                    stream_epoch=stream_epoch,
                    reset_attempt_id=(
                        self._reset_attempt.attempt_id
                        if self._reset_attempt is not None
                        else self._recovery_state.reset_attempt_id
                    ),
                )
                self._recovery_state = state
        if not reset_sent:
            self.ui_q.put(("log", "[recovery] WARNING: soft reset was not confirmed; the machine may still be executing buffered commands."))
        self.ui_q.put(("log", f"[recovery] {detail}"))
        self._emit_recovery_state(state)
        self._signal_tx_activity()
        return state

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

    def set_runtime_logging_mode(self, mode: str) -> None:
        self._verbose_runtime_logging = str(mode or "").strip().lower() == "verbose"

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
        if not self._verbose_runtime_logging:
            if text.startswith("RT "):
                return True
            if text.startswith("$") and (not text.startswith("$J=")):
                return True
            return False
        return True

    def _log_tx_line(self, line: str) -> None:
        if not line:
            return
        self._record_serial_activity("TX", line)
        if not self._should_log_tx_line(line):
            return
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
    
    def _quarantine_safety_realtime_write_failure(
        self,
        command: bytes,
        *,
        generation: int | None,
        serial_port: object | None,
        bytes_written: int,
    ) -> None:
        if command not in {RT_HOLD, RT_RESUME, RT_JOG_CANCEL}:
            return
        if generation is None or serial_port is None:
            return
        with self._write_lock:
            if not self._session_is_current(int(generation), serial_port):
                return
            with self._stream_lock:
                if self._recovery_state.required:
                    return
                active_execution = bool(
                    self._streaming
                    or self._paused
                    or self._execution_pending is not None
                    or self._manual_pending_item is not None
                    or self._stream_line_queue
                    or self._jog_cancel_inflight
                    or self._manual_motion_status_active()
                )
            if command != RT_JOG_CANCEL and not active_execution:
                return
            rendered = _format_realtime_for_log(command) or "realtime command"
            partial = " after partial transport acceptance" if bytes_written else ""
            self._enter_recovery_required(
                f"Safety-significant {rendered} write failed{partial}; controller execution state is uncertain.",
                generation=int(generation),
                attempt_controller_stop=True,
            )

    def send_realtime(self, command: bytes, *, expected_generation: int | None = None) -> bool:
        """Send real-time command (no newline).
        
        Real-time commands are processed immediately by GRBL without
        waiting for buffer space or acknowledgment.
        
        Args:
            command: Real-time command byte(s)
            
        Raises:
            SerialWriteError: If write fails
        """
        serial_module = self._serial_module()
        timeout_exc = _serial_timeout_exception_type(serial_module)
        serial_exc = _serial_exception_type(serial_module)
        admitted_generation: int | None = None
        admitted_serial: object | None = None
        total = 0
        with self._write_lock:
            try:
                current_generation = self.connection_generation()
                if expected_generation is None:
                    expected_generation = current_generation
                if not self._session_is_current(int(expected_generation)):
                    logger.warning("Ignoring realtime command from stale connection generation")
                    return False
                with self._stream_lock:
                    recovery_required = bool(self._recovery_state.required)
                    reset_attempt = self._reset_attempt
                    auto_level_active = self._auto_level_lease_blocks_ordinary_locked()
                if recovery_required and command not in {RT_HOLD, RT_RESET, RT_STATUS}:
                    self.ui_q.put(
                        ("log", "[recovery] Realtime command blocked until recovery is cleared.")
                    )
                    return False
                if auto_level_active and command not in {
                    RT_STATUS,
                    RT_HOLD,
                    RT_RESET,
                    RT_JOG_CANCEL,
                }:
                    self.ui_q.put(
                        (
                            "log",
                            "[autolevel] Realtime command blocked while Auto-Level owns controller admission.",
                        )
                    )
                    return False
                if command == RT_RESET and (
                    reset_attempt is None
                    or reset_attempt.timed_out
                    or int(reset_attempt.connection_generation)
                    != int(expected_generation)
                ):
                    self.ui_q.put(
                        (
                            "log",
                            "[reset] Unserialized Ctrl-X write was blocked; use the reset workflow.",
                        )
                    )
                    return False
                ser = self.ser
                if ser is None or not bool(getattr(ser, "is_open", False)):
                    logger.warning("Cannot send real-time command - not connected")
                    return False
                if not self._session_is_current(int(expected_generation), ser):
                    return False
                admitted_generation = int(expected_generation)
                admitted_serial = ser
                rendered = _format_realtime_for_log(command)
                if rendered:
                    self._log_tx_line(f"RT {rendered}")
                length = len(command)
                while total < length:
                    written = ser.write(command[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise timeout_exc("Write returned 0 bytes")
                    total += written
                return True
            except timeout_exc as e:
                self._quarantine_safety_realtime_write_failure(
                    command,
                    generation=admitted_generation,
                    serial_port=admitted_serial,
                    bytes_written=total,
                )
                raise SerialWriteError(f"Write timeout: {e}") from e
            except serial_exc as e:
                self._quarantine_safety_realtime_write_failure(
                    command,
                    generation=admitted_generation,
                    serial_port=admitted_serial,
                    bytes_written=total,
                )
                raise SerialWriteError(f"Serial write error: {e}") from e
            except Exception as e:
                logger.error(f"Unexpected write error: {e}")
                self._quarantine_safety_realtime_write_failure(
                    command,
                    generation=admitted_generation,
                    serial_port=admitted_serial,
                    bytes_written=total,
                )
                raise SerialWriteError(f"Unexpected error: {e}") from e
    
    def _clear_outgoing(self) -> None:
        """Clear the outgoing command queue."""
        with self._stream_lock:
            self._resolve_queued_manual_trackers_locked(
                self._manual_tracker_queue,
                error="Manual command was cleared before completion.",
            )
            while True:
                try:
                    self._outgoing_q.get_nowait()
                except queue.Empty:
                    break
            self._manual_source_queue.clear()
            self._manual_tracker_queue.clear()
            self._manual_identity_queue.clear()
            self._resolve_manual_tracker(
                getattr(self._manual_pending_item, "tracker", None),
                success=False,
                error="Manual command was cleared before completion.",
            )
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

    def _next_manual_command_id(self) -> int:
        with self._stream_lock:
            self._manual_command_id_seq = int(self._manual_command_id_seq) + 1
            return int(self._manual_command_id_seq)

    @staticmethod
    def _resolve_manual_tracker(
        tracker: ManualCommandResultTracker | None,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        if tracker is None or bool(getattr(tracker, "completed", False)):
            return
        tracker.resolve(success=bool(success), error=error)

    def _resolve_queued_manual_trackers_locked(
        self,
        trackers: deque[ManualCommandResultTracker | None],
        *,
        error: str,
    ) -> None:
        while trackers:
            tracker = trackers.popleft()
            self._resolve_manual_tracker(tracker, success=False, error=error)

    def _enqueue_manual_command(
        self,
        command: str,
        source: str | None,
        *,
        tracker: ManualCommandResultTracker | None = None,
        identity: WorkIdentity | None = None,
    ) -> bool:
        """Queue a manual command without blocking worker locks."""
        with self._stream_lock:
            tool_change_identity = getattr(tracker, "tool_change_identity", None)
            if tool_change_identity is not None and not (
                self._tool_change_macro_admission_allowed_locked(
                    source,
                    tool_change_identity,
                )
            ):
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error="Tool-change command lost exact workflow ownership before enqueue.",
                )
                return False
            auto_level_lease = getattr(tracker, "auto_level_lease", None)
            if auto_level_lease is not None and not (
                self._auto_level_lease_identity_current_locked(auto_level_lease)
            ):
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error="Auto-Level command lost exact workflow ownership before enqueue.",
                )
                return False
            if self._auto_level_lease_blocks_ordinary_locked() and auto_level_lease is None:
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error="Command was blocked by the active Auto-Level workflow lease.",
                )
                return False
        try:
            self._outgoing_q.put_nowait(command)
        except queue.Full:
            self._resolve_manual_tracker(
                tracker,
                success=False,
                error="Manual command queue is full.",
            )
            self._record_manual_queue_drop()
            return False
        self._manual_source_queue.append(source)
        self._manual_tracker_queue.append(tracker)
        self._manual_identity_queue.append(identity or self._current_work_identity_locked())
        try:
            self._tx_activity_evt.set()
        except Exception as exc:
            _log_suppressed("Failed signaling TX activity after enqueueing manual command", exc)
        return True
    
    def _reset_stream_buffer(self) -> None:
        """Reset streaming buffer state."""
        with self._stream_lock:
            for queued_item in self._stream_line_queue:
                self._resolve_manual_tracker(
                    getattr(queued_item, "manual_tracker", None),
                    success=False,
                    error="Manual command was interrupted before completion.",
                )
            self._stream_buf_used = 0
            self._stream_line_queue.clear()
            self._stream_pending_item = None
            self._stream_vacuum_pending = None
            self._stream_vacuum_pending_on = False
            self._resolve_manual_tracker(
                getattr(self._manual_pending_item, "tracker", None),
                success=False,
                error="Manual command was interrupted before completion.",
            )
            self._manual_pending_item = None
            self._live_acked_ring.clear()
            self._live_current_acked = None
            self._live_pending_window.clear()
            self._manual_source_queue.clear()
            self._resolve_queued_manual_trackers_locked(
                self._manual_tracker_queue,
                error="Manual command was interrupted before completion.",
            )
            self._manual_tracker_queue.clear()
            self._manual_identity_queue.clear()
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
            self._stream_tool_change_identity = None
            self._tx_bytes_window.clear()
            self._last_tx_emit_ts = 0.0
            self._tx_line_ts_window.clear()
            self._tx_lines_per_sec = 0.0

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
        """Return a bounded diagnostics snapshot for UI and export tooling."""

        now = time.time()
        # Refresh derived rates first so callers do not need to wait for another
        # send event to get an up-to-date snapshot.
        if self._tx_line_ts_window:
            cutoff = now - TX_LINE_RATE_WINDOW_S
            while self._tx_line_ts_window and self._tx_line_ts_window[0] < cutoff:
                self._tx_line_ts_window.popleft()
            if self._tx_line_ts_window:
                span = max(0.25, now - self._tx_line_ts_window[0])
                self._tx_lines_per_sec = float(len(self._tx_line_ts_window)) / span
            else:
                self._tx_lines_per_sec = 0.0
        # Capture a current queue-depth sample before formatting the diagnostic
        # payload that feeds the metrics dialog/export paths.
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
        controller_tx_origin_tail = self.get_controller_tx_origin_history(limit=50)
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
        with self._write_lock:
            with self._connection_lock:
                connection_generation = int(self._connection_generation)
                serial_port = self.ser
                communication_ready = bool(self._ready)
                startup_owner = self._startup_banner_owner
                with self._stream_lock:
                    recovery_epoch = int(self._recovery_epoch)
                    normal_state = self._normal_session_state
                    sync_transaction = self._recovery_sync_transaction
                    recovery_snapshot = self._recovery_snapshot
                    approved_snapshot = self._approved_normal_session_snapshot
        connection_state: dict[str, Any] = {
            "connection_generation": connection_generation,
            "serial_object_id": (
                "" if serial_port is None else f"0x{id(serial_port):x}"
            ),
            "communication_ready": communication_ready,
            "recovery_epoch": recovery_epoch,
            "startup_owner": None,
            "normal_session": {
                "phase": normal_state.phase.value,
                "required": bool(normal_state.required),
                "reason": str(normal_state.reason or ""),
                "connection_generation": int(normal_state.connection_generation),
                "recovery_epoch": int(normal_state.recovery_epoch),
            },
            "synchronization": None,
            "snapshot": None,
        }
        if startup_owner is not None:
            connection_state["startup_owner"] = {
                "phase": startup_owner.phase.value,
                "connection_generation": int(startup_owner.connection_generation),
                "serial_object_id": f"0x{id(startup_owner.serial_port):x}",
                "created_at": float(startup_owner.created_at),
                "deadline": float(startup_owner.deadline),
                "reason": str(startup_owner.reason or ""),
            }
        if sync_transaction is not None:
            connection_state["synchronization"] = {
                "transaction_id": int(sync_transaction.transaction_id),
                "gc_request_id": int(sync_transaction.gc_request_id),
                "parameters_request_id": int(sync_transaction.parameters_request_id),
                "connection_generation": int(sync_transaction.connection_generation),
                "recovery_epoch": int(sync_transaction.recovery_epoch),
                "purpose": str(sync_transaction.purpose),
                "phase": str(sync_transaction.phase),
                "gc_payload_seen": bool(sync_transaction.gc_payload_seen),
                "parameter_reports": list(sync_transaction.parameter_reports),
                "invalid": bool(sync_transaction.invalid),
            }
        if recovery_snapshot is not None:
            connection_state["snapshot"] = {
                "object_id": f"0x{id(recovery_snapshot):x}",
                "approved_for_install": approved_snapshot is recovery_snapshot,
                "connection_generation": int(recovery_snapshot.connection_generation),
                "recovery_epoch": int(recovery_snapshot.recovery_epoch),
                "sync_transaction_id": recovery_snapshot.sync_transaction_id,
                "gc_request_id": recovery_snapshot.gc_request_id,
                "parameters_request_id": recovery_snapshot.parameters_request_id,
                "gc_complete": bool(recovery_snapshot.gc_complete),
                "parameters_complete": bool(recovery_snapshot.parameters_complete),
                "machine_position": recovery_snapshot.machine_position,
                "position_source": str(recovery_snapshot.position_source),
            }
        # Return a stable diagnostics shape used by UI reporting, exports,
        # and tests.
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
            "controller_tx_origin_tail": controller_tx_origin_tail,
            "connection_state": connection_state,
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

    def _suspend_homing_watchdog(self, *, reason: str = "homing") -> None:
        try:
            if not getattr(self, "_homing_watchdog_enabled", True):
                return
            timeout = float(
                getattr(self, "_homing_watchdog_timeout", WATCHDOG_HOMING_TIMEOUT)
            )
            if timeout <= 0:
                return
            self.suspend_watchdog(timeout, reason=reason)
            try:
                self.ui_q.put(("log", f"[watchdog] Homing grace {timeout:g}s"))
            except Exception as exc:
                _log_suppressed("Failed queueing watchdog homing-grace log", exc)
        except Exception as exc:
            _log_suppressed("Failed configuring watchdog homing grace window", exc)

    @staticmethod
    def _safe_identity_dict(identity: Any) -> dict[str, int | None] | None:
        if identity is None:
            return None
        try:
            return {
                "connection_generation": int(identity.connection_generation),
                "recovery_epoch": int(identity.recovery_epoch),
                "reset_attempt_id": (
                    None
                    if getattr(identity, "reset_attempt_id", None) is None
                    else int(identity.reset_attempt_id)
                ),
            }
        except Exception:
            return None

    def _record_controller_tx_origin(
        self,
        line: str,
        *,
        origin: str,
        caller_purpose: str,
        admission_class: str,
        connection_generation: int,
        stream_epoch: int,
        recovery_epoch: int,
        queued_ts: float | None = None,
        tracked_command_identity: str | None = None,
        normal_action_identity: RecoveryActionIdentity | None = None,
        homing_identity: RecoveryActionIdentity | None = None,
    ) -> None:
        text = str(line or "").strip()
        if not text:
            return
        try:
            now = time.time()
            with self._stream_lock:
                normal_state = self._normal_session_state
                current_normal_identity = normal_state.action_identity
            entry: dict[str, Any] = {
                "command": text,
                "safe_command_class": (
                    "grbl_system_command"
                    if text.startswith("$")
                    else "program_or_manual_line"
                ),
                "origin": str(origin or "unknown"),
                "caller_purpose": str(caller_purpose or ""),
                "admission_class": str(admission_class or ""),
                "connection_generation": int(connection_generation),
                "stream_epoch": int(stream_epoch),
                "recovery_epoch": int(recovery_epoch),
                "normal_session_phase": normal_state.phase.value,
                "initialization_action_identity": self._safe_identity_dict(
                    current_normal_identity
                ),
                "homing_identity": self._safe_identity_dict(homing_identity),
                "tracked_command_identity": str(tracked_command_identity or ""),
                "queued_ts": None if queued_ts is None else float(queued_ts),
                "tx_ts": float(now),
            }
            if normal_action_identity is not None:
                entry["normal_action_identity"] = self._safe_identity_dict(
                    normal_action_identity
                )
            self._controller_tx_origin_history.append(entry)
            logger.info(
                "Controller TX admitted: command=%s origin=%s purpose=%s "
                "admission=%s generation=%s stream_epoch=%s recovery_epoch=%s "
                "normal_phase=%s tracked=%s",
                text,
                entry["origin"],
                entry["caller_purpose"],
                entry["admission_class"],
                entry["connection_generation"],
                entry["stream_epoch"],
                entry["recovery_epoch"],
                entry["normal_session_phase"],
                entry["tracked_command_identity"],
            )
        except Exception as exc:
            _log_suppressed("Failed recording controller TX origin", exc)

    def get_controller_tx_origin_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        try:
            max_items = max(1, int(limit))
        except Exception:
            max_items = 50
        entries = list(self._controller_tx_origin_history)
        if len(entries) > max_items:
            entries = entries[-max_items:]
        return [dict(entry) for entry in entries]

    @staticmethod
    def _line_invalidates_auto_level_coordinate_context(line: str) -> bool:
        code = re.sub(r"\([^)]*\)", " ", str(line or "").upper()).split(";", 1)[0]
        if code.strip().startswith(("$H", "$RST")):
            return True
        return bool(
            re.search(
                r"(?<![A-Z0-9.])(?:G10|G5[4-9](?:\.[123])?|G92(?:\.[123])?|G43(?:\.1)?|G49)(?![0-9.])",
                code,
            )
        )

    def _write_line(
        self,
        line: str,
        payload: Optional[bytes] = None,
        *,
        allow_abort: bool = False,
        allow_recovery: bool = False,
        expected_generation: int | None = None,
        expected_stream_epoch: int | None = None,
        expected_recovery_epoch: int | None = None,
        expected_tool_change_identity: StreamToolChangeIdentity | None = None,
        expected_auto_level_lease: AutoLevelWorkflowLease | None = None,
        command_origin: str = "unknown",
        caller_purpose: str = "",
        admission_class: str = "direct_write",
        queued_ts: float | None = None,
        tracked_command_identity: str | None = None,
        normal_action_identity: RecoveryActionIdentity | None = None,
        homing_identity: RecoveryActionIdentity | None = None,
    ) -> bool:
        """Write line to serial port.
        
        Args:
            line: Line content (for logging)
            payload: Pre-encoded payload (optional)
            allow_abort: Allow writes even if abort flag is set
            
        Returns:
            True if write succeeded, False otherwise
        """
        serial_module = self._serial_module()
        timeout_exc = _serial_timeout_exception_type(serial_module)
        serial_exc = _serial_exception_type(serial_module)
        ser = None
        write_generation = (
            self.connection_generation()
            if expected_generation is None
            else int(expected_generation)
        )
        try:
            if payload is None:
                payload = self._encode_line_payload(line)
            with self._write_lock:
                if not self._session_is_current(write_generation):
                    return False
                ser = self.ser
                if ser is None or not bool(getattr(ser, "is_open", False)):
                    return False
                if not self._session_is_current(write_generation, ser):
                    return False
                with self._stream_lock:
                    recovery_command_allowed = bool(
                        allow_recovery
                        and self._recovery_state.phase
                        is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                        and line.strip().upper() in {"$G", "$#", "$H"}
                    )
                    if self._recovery_state.required and not recovery_command_allowed:
                        return False
                    if (
                        self._suspension_blocks_tx_locked()
                        and not recovery_command_allowed
                    ):
                        return False
                    if (
                        self._abort_writes.is_set()
                        and not allow_abort
                        and not recovery_command_allowed
                    ):
                        return False
                    if (
                        expected_stream_epoch is not None
                        and int(expected_stream_epoch) != int(self._stream_token)
                    ):
                        return False
                    if (
                        expected_recovery_epoch is not None
                        and int(expected_recovery_epoch) != int(self._recovery_epoch)
                    ):
                        return False
                    if expected_tool_change_identity is not None and not (
                        self._tool_change_macro_admission_allowed_locked(
                            "macro",
                            expected_tool_change_identity,
                        )
                    ):
                        return False
                    if expected_auto_level_lease is not None and not (
                        self._auto_level_lease_identity_current_locked(
                            expected_auto_level_lease
                        )
                    ):
                        return False
                    if self._auto_level_lease_blocks_ordinary_locked() and (
                        expected_auto_level_lease is None
                        and not recovery_command_allowed
                    ):
                        return False
                    stream_epoch = int(self._stream_token)
                    recovery_epoch = int(self._recovery_epoch)
                self._record_controller_tx_origin(
                    line,
                    origin=command_origin,
                    caller_purpose=caller_purpose,
                    admission_class=admission_class,
                    connection_generation=int(write_generation),
                    stream_epoch=stream_epoch,
                    recovery_epoch=recovery_epoch,
                    queued_ts=queued_ts,
                    tracked_command_identity=tracked_command_identity,
                    normal_action_identity=normal_action_identity,
                    homing_identity=homing_identity,
                )
                self._log_tx_line(line)
                total = 0
                length = len(payload)
                while total < length:
                    written = ser.write(payload[total:])
                    if written is None:
                        written = 0
                    if written <= 0:
                        raise timeout_exc("Write returned 0 bytes")
                    total += written

                if self._line_invalidates_auto_level_coordinate_context(line):
                    with self._stream_lock:
                        self._auto_level_coordinate_context_epoch += 1
                        self._invalidate_auto_level_map_locked(
                            "WCS/G92/TLO context changed after map installation."
                        )

            return True

        except timeout_exc as e:
            logger.error(f"Write timeout: {e}")
            self.ui_q.put(("log", f"[write timeout] {e}"))
            if self.ser is not None:
                self._signal_disconnect(
                    f"[tx/write-timeout] Serial write timeout: {e}",
                    generation=write_generation,
                    serial_port=ser,
                )
            return False
            
        except serial_exc as e:
            logger.error(f"Serial write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.ser is not None:
                self._signal_disconnect(
                    f"[tx/write] Serial write error: {e}",
                    generation=write_generation,
                    serial_port=ser,
                )
            return False
            
        except Exception as e:
            logger.error(f"Unexpected write error: {e}")
            self.ui_q.put(("log", f"[write error] {e}"))
            if self.ser is not None:
                self._signal_disconnect(
                    f"[tx/write] Unexpected write error: {e}",
                    generation=write_generation,
                    serial_port=ser,
                )
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
    
    def _rx_loop(
        self,
        stop_evt: threading.Event,
        generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        """Receive thread - reads from GRBL and processes responses.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        if generation is None:
            generation = self.connection_generation()
        if serial_port is None:
            serial_port = self.ser
        logger.debug("RX thread started for generation %s", generation)
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
            if stop_evt.is_set():
                return True
            if not self._session_is_current(int(generation), serial_port):
                return True
            try:
                return not bool(getattr(serial_port, "is_open", False))
            except Exception:
                return True
        
        try:
            while not stop_evt.is_set():
                if not self._session_is_current(int(generation), serial_port):
                    break
                try:
                    self.is_connected()
                except Exception as e:
                    logger.error(f"RX thread error: {e}", exc_info=True)
                    self._emit_exception("RX thread error", e)
                    self._signal_disconnect(
                        f"[rx/check] RX thread error: {e}",
                        generation=int(generation),
                        serial_port=serial_port,
                    )
                    stop_evt.set()
                    break
                ser = cast(Any, serial_port)
                if ser is None:
                    time.sleep(0.05)
                    continue
                if hasattr(ser, "is_open") and not ser.is_open:
                    if not stop_evt.is_set():
                        self._signal_disconnect(
                            "[rx/port-closed] Serial port closed",
                            generation=int(generation),
                            serial_port=serial_port,
                        )
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
                    self._signal_disconnect(
                        f"[rx/read] Serial read error: {e}",
                        generation=int(generation),
                        serial_port=serial_port,
                    )
                    stop_evt.set()
                    break
                except Exception as e:
                    if _shutdown_in_progress():
                        logger.debug("RX loop read aborted during shutdown: %s", e)
                        break
                    logger.error(f"Unexpected read error: {e}")
                    self._signal_disconnect(
                        f"[rx/read] Unexpected serial read error: {e}",
                        generation=int(generation),
                        serial_port=serial_port,
                    )
                    stop_evt.set()
                    break
                
                if not chunk:
                    continue
                buf += chunk
                
                # Process complete lines
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_str = line.decode("utf-8", errors="replace").rstrip("\r")
                    if line_str.strip():
                        self._handle_rx_line(
                            line_str,
                            session_generation=int(generation),
                            serial_port=serial_port,
                        )
        
        except Exception as e:
            if _shutdown_in_progress():
                logger.debug("RX thread exiting during shutdown: %s", e)
            else:
                logger.error(f"RX thread error: {e}", exc_info=True)
                self._emit_exception("RX thread error", e)
                self._signal_disconnect(
                    f"[rx/thread] RX thread error: {e}",
                    generation=int(generation),
                    serial_port=serial_port,
                )
                stop_evt.set()
        
        finally:
            logger.debug("RX thread stopped")
    
