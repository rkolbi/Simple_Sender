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

from __future__ import annotations

import queue
import math
import threading
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from typing import Any, Callable, Iterator, Protocol, Sequence, TypeAlias, overload
from typing import Literal

AfterId: TypeAlias = str | int


class BoolVarLike(Protocol):
    def get(self) -> bool: ...


class IntVarLike(Protocol):
    def set(self, value: int) -> None: ...


class StrVarLike(Protocol):
    def set(self, value: str) -> None: ...


class GcodeViewLike(Protocol):
    lines_count: int

    def clear(self) -> None: ...
    def set_live_window(
        self,
        past_lines: list[tuple[int, str]],
        current_line: tuple[int, str] | None,
        next_lines: list[tuple[int, str]],
        *,
        next_buffered_count: int = 0,
    ) -> None: ...


class LineSource(Protocol):
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[str]: ...

    @overload
    def __getitem__(self, idx: int) -> str: ...

    @overload
    def __getitem__(self, idx: slice) -> list[str]: ...


class AppProtocol(Protocol):
    _ui_throttle_ms: int
    console_positions_enabled: BoolVarLike
    performance_mode: BoolVarLike
    gui_logging_enabled: BoolVarLike
    gview: GcodeViewLike
    _last_sent_index: int
    _last_acked_index: int

    def after(self, ms: int, func: Callable[[], Any]) -> AfterId: ...
    def after_cancel(self, after_id: AfterId) -> None: ...
    def bind_class(
        self,
        class_name: str,
        sequence: str,
        func: Callable[..., Any],
        add: str | None = None,
    ) -> Any: ...
    def _update_live_estimate(self, done: int, total: int) -> None: ...
    def _maybe_notify_job_completion(self, done: int, total: int) -> None: ...
    def _format_throughput(self, bps: float) -> str: ...

    def __getattr__(self, name: str) -> Any: ...

@dataclass(frozen=True, slots=True)
class StreamQueueItem:
    line_len: int
    is_gcode: bool
    idx: int | None
    line: str
    manual_source: str | None = None
    queued_ts: float = 0.0
    file_end_offset: int | None = None
    manual_tracker: "ManualCommandResultTracker | None" = None
    connection_generation: int = 0
    stream_epoch: int = 0
    recovery_epoch: int = 0
    tool_change_identity: "StreamToolChangeIdentity | None" = field(
        default=None, repr=False, compare=False
    )
    auto_level_lease: "AutoLevelWorkflowLease | None" = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class StreamPendingItem:
    line: str
    is_gcode: bool
    idx: int | None
    file_end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class ManualPendingItem:
    line: str
    payload: bytes
    line_len: int
    source: str | None = None
    tracker: "ManualCommandResultTracker | None" = None
    connection_generation: int = 0
    stream_epoch: int = 0
    recovery_epoch: int = 0
    tool_change_identity: "StreamToolChangeIdentity | None" = field(
        default=None, repr=False, compare=False
    )
    auto_level_lease: "AutoLevelWorkflowLease | None" = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class WorkIdentity:
    """Immutable ownership for work admitted into a worker queue."""

    connection_generation: int
    stream_epoch: int
    recovery_epoch: int


@dataclass(frozen=True, slots=True)
class StreamToolChangeIdentity:
    """Exact stream/source ownership for one active tool-change directive."""

    connection_generation: int
    serial_port: object = field(repr=False, compare=False)
    stream_epoch: int
    recovery_epoch: int
    source_identity: "GcodeSourceIdentity"
    directive_index: int | None = None
    request_token: int = 0


class AutoLevelLeasePhase(str, Enum):
    AVAILABLE = "available"
    ACQUIRED = "acquired"
    ACTIVE = "active"
    CANCEL_REQUESTED = "cancel_requested"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETION_READY = "completion_ready"
    INSTALLING = "installing"
    INSTALLED = "installed"
    INSTALLATION_FAILED = "installation_failed"
    FAILED = "failed"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class AutoLevelWorkflowLease:
    """Opaque ownership for one exclusive Auto-Level controller workflow."""

    connection_generation: int
    serial_port: object = field(repr=False, compare=False)
    stream_epoch: int
    recovery_epoch: int
    workflow_token: int
    recovery_phase: "RecoveryPhase"
    alarm_active: bool
    machine_trust: "MachineTrustState"
    communication_ready: bool
    normal_session_required: bool
    source_identity: "GcodeSourceIdentity"
    execution_busy: bool
    coordinate_context_epoch: int = 0


@dataclass(frozen=True, slots=True)
class AutoLevelMapProvenance:
    """Exact controller/source context that owns one installed height map."""

    connection_generation: int
    serial_port: object = field(repr=False, compare=False)
    stream_epoch: int
    recovery_epoch: int
    source_identity: "GcodeSourceIdentity"
    workflow_token: int
    installation_id: int
    coordinate_context_epoch: int
    machine_trust: "MachineTrustState"


@dataclass(frozen=True, slots=True)
class AutoLevelInstallationTicket:
    """Opaque, single-use authorization for one Auto-Level map installation."""

    lease: AutoLevelWorkflowLease = field(repr=False, compare=False)
    connection_generation: int = 0
    serial_port: object = field(default=None, repr=False, compare=False)
    stream_epoch: int = 0
    recovery_epoch: int = 0
    source_identity: "GcodeSourceIdentity | None" = field(
        default=None, repr=False, compare=False
    )
    alarm_active: bool = False
    machine_trust: "MachineTrustState" = field(
        default_factory=lambda: MachineTrustState.untrusted()
    )
    restoration_confirmed: bool = False
    installation_id: int = 0
    coordinate_context_epoch: int = 0
    provenance: AutoLevelMapProvenance | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class AutoLevelLeaseState:
    lease: AutoLevelWorkflowLease | None = field(default=None, repr=False, compare=False)
    phase: AutoLevelLeasePhase = AutoLevelLeasePhase.AVAILABLE
    reason: str = ""
    installation_ticket: AutoLevelInstallationTicket | None = field(
        default=None, repr=False, compare=False
    )


class GcodeSourcePhase(str, Enum):
    """Worker-owned lifecycle for an exact G-code source transaction."""

    CLEARED = "cleared"
    RESERVED = "reserved"
    COMMITTED = "committed"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class GcodeSourceIdentity:
    """Immutable ownership for the exact G-code source admitted by the worker."""

    source_id: int
    connection_generation: int
    stream_epoch: int
    recovery_epoch: int
    name: str | None = None
    cleared: bool = False
    transaction_id: int = 0
    ui_load_generation: int = 0
    snapshot_sha256: str = ""
    snapshot_size_bytes: int = 0
    validated_line_count: int = 0


@dataclass(frozen=True, slots=True)
class GcodeSourceAdmission:
    """Result of one atomic worker-side source replacement request."""

    accepted: bool
    identity: GcodeSourceIdentity
    reason: str = ""


@dataclass(slots=True)
class ManualCommandResultTracker:
    command_id: int
    command: str
    source: str | None = None
    success: bool | None = None
    error: str | None = None
    completed: bool = False
    tool_change_identity: StreamToolChangeIdentity | None = field(
        default=None, repr=False, compare=False
    )
    auto_level_lease: AutoLevelWorkflowLease | None = field(
        default=None, repr=False, compare=False
    )
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def resolve(self, *, success: bool, error: str | None = None) -> None:
        self.success = bool(success)
        self.error = str(error or "").strip() or None
        self.completed = True
        self._event.set()

    def wait(self, timeout_s: float = 0.0) -> bool:
        timeout = float(timeout_s)
        if timeout <= 0.0:
            return bool(self._event.wait())
        return bool(self._event.wait(timeout))


class RecoveryPhase(str, Enum):
    NORMAL = "normal"
    RESET_REQUIRED = "reset_required"
    RESET_SENT_AWAITING_BANNER = "reset_sent_awaiting_banner"
    RESET_CONFIRMED_STATE_UNTRUSTED = "reset_confirmed_state_untrusted"
    RECOVERY_COMPLETE = "recovery_complete"


class ControllerSuspensionPhase(str, Enum):
    """Worker-owned state for controller-confirmed stream suspension."""

    NONE = "none"
    APPLICATION_HOLD_REQUESTED = "application_hold_requested"
    APPLICATION_HOLD_CONFIRMED = "application_hold_confirmed"
    EXTERNAL_HOLD = "external_hold"
    SAFETY_DOOR = "safety_door"
    RESUME_REQUESTED = "resume_requested"
    RESUME_CONFIRMED = "resume_confirmed"
    UNCERTAIN = "uncertain"
    RETIRED = "retired"


class NormalSessionPhase(str, Enum):
    """Worker-owned trust initialization for a clean controller session."""

    INACTIVE = "inactive"
    SYNCHRONIZING = "synchronizing"
    POSITION_REQUIRED = "position_required"
    SNAPSHOT_INSTALL_PENDING = "snapshot_install_pending"
    READY = "ready"
    FAILED = "failed"


class StartupBannerPhase(str, Enum):
    """One terminal outcome for a connection-owned startup handshake."""

    PENDING = "pending"
    CONSUMED = "consumed"
    TIMED_OUT = "timed_out"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class StartupBannerOwnership:
    connection_generation: int
    serial_port: object = field(repr=False, compare=False)
    created_at: float = 0.0
    deadline: float = 0.0
    phase: StartupBannerPhase = StartupBannerPhase.PENDING
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResetAttempt:
    attempt_id: int
    connection_generation: int
    recovery_epoch: int
    purpose: str
    armed_at: float
    deadline: float
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryActionIdentity:
    """Immutable ownership captured by one operator recovery surface."""

    connection_generation: int
    recovery_epoch: int
    reset_attempt_id: int | None = None


@dataclass(frozen=True, slots=True)
class RecoveryFinalizationIdentity:
    """Single-use ownership for one approved recovery snapshot handoff."""

    finalization_id: int
    action_identity: RecoveryActionIdentity
    connection_generation: int
    stream_epoch: int
    recovery_epoch: int


CoordinateTuple: TypeAlias = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class RecoverySyncTransaction:
    """One ordered ``$G`` then ``$#`` recovery synchronization transaction."""

    transaction_id: int
    gc_request_id: int
    parameters_request_id: int
    connection_generation: int
    recovery_epoch: int
    purpose: str = "recovery"
    phase: str = "gc"
    gc_payload_seen: bool = False
    parameter_reports: tuple[str, ...] = ()
    invalid: bool = False


@dataclass(frozen=True, slots=True)
class NormalSessionInitializationState:
    """Communication-ready session state that has not yet become job-ready."""

    phase: NormalSessionPhase = NormalSessionPhase.INACTIVE
    reason: str = ""
    connection_generation: int = 0
    recovery_epoch: int = 0
    homing_started: bool = False
    homing_seen: bool = False

    @property
    def required(self) -> bool:
        return self.phase in {
            NormalSessionPhase.SYNCHRONIZING,
            NormalSessionPhase.POSITION_REQUIRED,
            NormalSessionPhase.SNAPSHOT_INSTALL_PENDING,
            NormalSessionPhase.FAILED,
        }

    @property
    def action_identity(self) -> RecoveryActionIdentity:
        return RecoveryActionIdentity(
            connection_generation=int(self.connection_generation),
            recovery_epoch=int(self.recovery_epoch),
        )


@dataclass(frozen=True, slots=True)
class RecoveryStateSnapshot:
    """Value-bound machine state gathered for one exact recovery epoch."""

    connection_generation: int
    recovery_epoch: int
    sync_transaction_id: int | None = None
    gc_request_id: int | None = None
    parameters_request_id: int | None = None
    gc_complete: bool = False
    parameters_complete: bool = False
    active_wcs: str | None = None
    wcs_offsets: tuple[tuple[str, CoordinateTuple], ...] = ()
    work_coordinate_offset: CoordinateTuple | None = None
    g92: CoordinateTuple | None = None
    modal_units: str | None = None
    distance_mode: str | None = None
    plane: str | None = None
    feed_mode: str | None = None
    arc_distance_mode: str | None = None
    arc_distance_mode_provenance: str | None = None
    arc_distance_mode_transaction_id: int | None = None
    arc_distance_mode_request_id: int | None = None
    motion_mode: str | None = None
    spindle_mode: str | None = None
    coolant_mode: str | None = None
    coolant_modes: tuple[str, ...] = ()
    feed_rate: float | None = None
    spindle_speed: float | None = None
    tool_number: int | None = None
    selected_tool_number: int | None = None
    current_tool_number: int | None = None
    tool_state_distinct: bool = False
    tool_state_transaction_id: int | None = None
    tool_state_request_id: int | None = None
    tlo: float | None = None
    tlo_mode: str | None = None
    tlo_mode_transaction_id: int | None = None
    tlo_mode_request_id: int | None = None
    tlo_value_transaction_id: int | None = None
    tlo_value_request_id: int | None = None
    extended_wcs_supported: bool = False
    machine_position: CoordinateTuple | None = None
    position_source: str = "unknown"
    spindle_verified: bool = False
    coolant_verified: bool = False
    spindle_verified_mode: str | None = None
    coolant_verified_modes: tuple[str, ...] = ()
    spindle_verification_transaction_id: int | None = None
    coolant_verification_transaction_id: int | None = None

    def offset_for(self, wcs: str | None) -> CoordinateTuple | None:
        target = str(wcs or "").upper()
        for name, values in self.wcs_offsets:
            if str(name).upper() == target:
                return values
        return None

    def modal_values_complete(self) -> bool:
        return bool(
            self.gc_values_complete()
            and self._tlo_mode_provenance_complete()
        )

    def gc_values_complete(self) -> bool:
        return bool(
            self.active_wcs
            and self.modal_units
            and self.distance_mode
            and self.plane
            and self.feed_mode
            and self.arc_distance_mode in {"G90.1", "G91.1"}
            and self.arc_distance_mode_provenance
            and self.arc_distance_mode_transaction_id == self.sync_transaction_id
            and self.arc_distance_mode_request_id == self.gc_request_id
            and self.motion_mode
            and self.spindle_mode
            and self.coolant_mode
            and self.coolant_modes
            and self.feed_rate is not None
            and math.isfinite(float(self.feed_rate))
            and self.spindle_speed is not None
            and math.isfinite(float(self.spindle_speed))
            and self.selected_tool_number is not None
            and self.current_tool_number is not None
            and self.sync_transaction_id is not None
            and self.gc_request_id is not None
            and self.tool_state_transaction_id == self.sync_transaction_id
            and self.tool_state_request_id == self.gc_request_id
        )

    def _tlo_mode_provenance_complete(self) -> bool:
        return bool(
            self.tlo_mode in {"G43", "G43.1", "G49"}
            and self.sync_transaction_id is not None
            and self.gc_request_id is not None
            and self.tlo_mode_transaction_id == self.sync_transaction_id
            and self.tlo_mode_request_id == self.gc_request_id
        )

    def _tlo_value_provenance_complete(self) -> bool:
        return bool(
            self.tlo is not None
            and self.sync_transaction_id is not None
            and self.parameters_request_id is not None
            and self.tlo_value_transaction_id == self.sync_transaction_id
            and self.tlo_value_request_id == self.parameters_request_id
        )

    def tlo_state_complete(self) -> bool:
        if not self._tlo_mode_provenance_complete() or not self._tlo_value_provenance_complete():
            return False
        if self.tlo_mode == "G49":
            return bool(self.tlo is not None and abs(float(self.tlo)) <= 1e-9)
        return self.tlo_mode in {"G43", "G43.1"}

    def accessory_verification_complete(self) -> bool:
        return bool(
            self.spindle_mode == "M5"
            and self.spindle_verified
            and self.spindle_verified_mode == self.spindle_mode
            and self.spindle_verification_transaction_id == self.sync_transaction_id
            and self.coolant_modes == ("M9",)
            and self.coolant_verified
            and self.coolant_verified_modes == self.coolant_modes
            and self.coolant_verification_transaction_id == self.sync_transaction_id
        )

    def parameter_values_complete(self) -> bool:
        required_offsets = {"G54", "G55", "G56", "G57", "G58", "G59"}
        if self.extended_wcs_supported:
            required_offsets.update({"G59.1", "G59.2", "G59.3"})
        reported_offsets = {str(name).upper() for name, _values in self.wcs_offsets}
        finite_offsets = all(
            all(math.isfinite(float(value)) for value in values)
            for _name, values in self.wcs_offsets
        )
        finite_wco = bool(
            self.work_coordinate_offset is not None
            and all(
                math.isfinite(float(value))
                for value in self.work_coordinate_offset
            )
        )
        finite_g92 = bool(
            self.g92 is not None
            and all(math.isfinite(float(value)) for value in self.g92)
        )
        return bool(
            required_offsets.issubset(reported_offsets)
            and finite_offsets
            and self.offset_for(self.active_wcs) is not None
            and finite_wco
            and finite_g92
            and self.tlo is not None
            and math.isfinite(float(self.tlo))
            and self.tlo_state_complete()
        )

    def to_trust_state(self, *, setup_tool_reference: bool = False) -> "MachineTrustState":
        modal = bool(self.gc_complete and self.modal_values_complete())
        parameters = bool(self.parameters_complete and self.parameter_values_complete())
        position = bool(
            self.machine_position is not None
            and all(math.isfinite(float(value)) for value in self.machine_position)
            and self.position_source in {"homed", "operator_accepted"}
        )
        return MachineTrustState(
            machine_position=position,
            position_source=self.position_source if position else "unknown",
            work_coordinates=parameters,
            active_wcs=modal and self.offset_for(self.active_wcs) is not None,
            g92=parameters and self.g92 is not None,
            modal_state=modal,
            tool_length_offset=parameters and self.tlo_state_complete(),
            setup_tool_reference=bool(setup_tool_reference),
            spindle_state=bool(self.accessory_verification_complete()),
            coolant_state=bool(self.accessory_verification_complete()),
        )


@dataclass(frozen=True, slots=True)
class MachineTrustState:
    machine_position: bool = True
    position_source: str = "normal"
    work_coordinates: bool = True
    active_wcs: bool = True
    g92: bool = True
    modal_state: bool = True
    tool_length_offset: bool = True
    setup_tool_reference: bool = True
    spindle_state: bool = True
    coolant_state: bool = True

    @classmethod
    def untrusted(cls) -> "MachineTrustState":
        return cls(
            machine_position=False,
            position_source="unknown",
            work_coordinates=False,
            active_wcs=False,
            g92=False,
            modal_state=False,
            tool_length_offset=False,
            setup_tool_reference=False,
            spindle_state=False,
            coolant_state=False,
        )

    def missing_for_new_job(self) -> tuple[str, ...]:
        checks = (
            ("machine position", self.machine_position),
            ("work coordinates/WCO", self.work_coordinates),
            ("active WCS", self.active_wcs),
            ("G92", self.g92),
            ("modal state", self.modal_state),
            ("tool-length compensation", self.tool_length_offset),
            ("spindle state", self.spindle_state),
            ("coolant state", self.coolant_state),
        )
        return tuple(name for name, trusted in checks if not trusted)


@dataclass(frozen=True, slots=True)
class ExecutionPendingState:
    connection_generation: int
    stream_epoch: int
    recovery_epoch: int
    verified_eof: bool
    total_lines: int
    total_lines_known: bool
    last_acked_index: int
    send_index: int
    file_size_bytes: int
    completion_published: bool = False


@dataclass(frozen=True, slots=True)
class ControllerSuspensionState:
    """Exact ownership for one stream's controller suspension transaction."""

    phase: ControllerSuspensionPhase = ControllerSuspensionPhase.NONE
    connection_generation: int = 0
    serial_port: object | None = field(default=None, repr=False, compare=False)
    stream_epoch: int = 0
    recovery_epoch: int = 0
    request_id: int = 0
    controller_state: str = ""
    tx_admission_closed: bool = False
    operator_ack_required: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionRecoveryState:
    phase: RecoveryPhase = RecoveryPhase.NORMAL
    reason: str = ""
    connection_generation: int = 0
    uncertain_start_index: int | None = None
    uncertain_end_index: int | None = None
    hold_sent: bool = False
    reset_sent: bool = False
    machine_may_be_executing: bool = False
    recovery_epoch: int = 0
    stream_epoch: int = 0
    reset_attempt_id: int | None = None
    reset_timed_out: bool = False
    homing_started: bool = False
    homing_seen: bool = False

    @property
    def required(self) -> bool:
        return self.phase is not RecoveryPhase.NORMAL

    @property
    def action_identity(self) -> RecoveryActionIdentity:
        return RecoveryActionIdentity(
            connection_generation=int(self.connection_generation),
            recovery_epoch=int(self.recovery_epoch),
            reset_attempt_id=self.reset_attempt_id,
        )


@dataclass(frozen=True, slots=True)
class WorkflowAdmissionSnapshot:
    """Atomic controller identity and admission state for a manual workflow."""

    connection_generation: int
    serial_port: object | None = field(default=None, repr=False, compare=False)
    stream_epoch: int = 0
    recovery_epoch: int = 0
    recovery_phase: RecoveryPhase = RecoveryPhase.NORMAL
    connected: bool = False
    communication_ready: bool = False
    normal_session_required: bool = True
    execution_busy: bool = True
    alarm_active: bool = False
    missing_trust: tuple[str, ...] = ()

    @property
    def admissible(self) -> bool:
        return bool(
            self.serial_port is not None
            and self.connected
            and self.communication_ready
            and self.recovery_phase is RecoveryPhase.NORMAL
            and not self.normal_session_required
            and not self.execution_busy
            and not self.alarm_active
            and not self.missing_trust
        )


class GrblWorkerState:
    ui_q: Any
    ser: Any

    _streaming: bool
    _paused: bool
    _stream_lock: Any
    _write_lock: Any
    _connection_lock: Any
    _connection_lifecycle_lock: Any
    _stream_token: int
    _recovery_epoch: int
    _execution_pending: ExecutionPendingState | None
    _status_observation_seq: int
    _suspension_state: ControllerSuspensionState
    _suspension_request_seq: int
    _suspension_confirmation_timer: threading.Timer | None
    _suspension_confirmation_timeout_s: float
    _suspension_pause_reason: str | None
    _stream_line_queue: deque[StreamQueueItem]
    _stream_pending_item: StreamPendingItem | None
    _manual_pending_item: ManualPendingItem | None
    _live_acked_ring: deque[tuple[int, str]]
    _live_current_acked: tuple[int, str] | None
    _live_pending_window: deque[tuple[int, str]]
    _resume_preamble: deque[str]
    _pause_after_idx: int | None
    _pause_after_reason: str | None
    _stream_tool_change_pending: StreamPendingItem | None
    _stream_tool_change_name: str
    _stream_tool_change_active: bool
    _stream_tool_change_identity: StreamToolChangeIdentity | None
    _stream_tool_change_request_seq: int
    _send_index: int
    _ack_index: int
    _stream_start_index: int
    _ack_byte_offset: int
    _stream_file_size_bytes: int
    _stream_buf_used: int
    _rx_window: int
    _connection_generation: int
    _startup_banner_owner: StartupBannerOwnership | None
    _recovery_state: ExecutionRecoveryState
    _normal_session_state: NormalSessionInitializationState
    _machine_trust: MachineTrustState
    _auto_level_installation_seq: int
    _auto_level_coordinate_context_epoch: int
    _auto_level_installed_provenance: AutoLevelMapProvenance | None
    _recovery_snapshot: RecoveryStateSnapshot | None
    _approved_recovery_snapshot: RecoveryStateSnapshot | None
    _approved_recovery_completed_state: ExecutionRecoveryState | None
    _approved_recovery_finalization_identity: RecoveryFinalizationIdentity | None
    _recovery_finalization_seq: int
    _approved_normal_session_snapshot: RecoveryStateSnapshot | None
    _recovery_sync_transaction: RecoverySyncTransaction | None
    _reset_attempt: ResetAttempt | None
    _recovery_sync_generation: int | None
    _recovery_sync_epoch: int | None

    _outgoing_q: queue.Queue[str]
    _manual_source_queue: deque[str | None]
    _manual_tracker_queue: deque[ManualCommandResultTracker | None]
    _manual_identity_queue: deque[WorkIdentity]
    _manual_command_id_seq: int
    _purge_jog_queue: threading.Event
    _abort_writes: threading.Event
    _manual_queue_drop_count: int
    _manual_queue_drop_total: int
    _manual_queue_last_drop_notice_ts: float

    _ready: bool
    _alarm_active: bool
    _settings_dump_active: bool
    _settings_dump_seen: bool
    _last_manual_source: str | None

    _watchdog_paused: bool
    _watchdog_trip_ts: float
    _watchdog_ignore_until: float
    _watchdog_ignore_reason: str | None
    _watchdog_ready_armed: bool
    _watchdog_ready_ts: float
    _homing_watchdog_enabled: bool
    _homing_watchdog_timeout: float

    _dry_run_sanitize: bool
    _last_rx_ts: float

    _status_interval_lock: threading.Lock
    _status_poll_interval: float
    _status_query_failures: int
    _status_query_failure_limit: int
    _status_query_backoff_base: float
    _status_query_backoff_max: float

    _status_log_interval: float
    _last_status_log_ts: float
    _ok_log_interval: float
    _last_ok_log_ts: float
    _ok_log_count: int

    _gcode: Sequence[str]
    _gcode_payload_cache: Sequence[bytes | None] | None
    _gcode_pause_reason_cache: Sequence[str | None] | None
    _gcode_spindle_state_cache: Sequence[bool | None] | None
    _gcode_name: str | None
    _gcode_source_seq: int
    _gcode_source_transaction_seq: int
    _gcode_source_identity: GcodeSourceIdentity
    _gcode_source_phase: GcodeSourcePhase
    _extended_wcs_supported: bool
    _tx_lines_per_sec: float
    _ok_latency_ms_last: float
    _ok_latency_ms_avg: float
    _ok_latency_sample_count: int
    _tx_loop_cycles: int
    _tx_loop_idle_cycles: int
    _tx_loop_active_cycles: int
    _tx_loop_idle_wait_total_s: float

    def is_connected(self) -> bool:
        raise NotImplementedError

    def send_realtime(self, command: bytes, *, expected_generation: int | None = None) -> bool:
        raise NotImplementedError

    def recovery_required(self) -> bool:
        raise NotImplementedError

    def suspension_state(self) -> ControllerSuspensionState:
        raise NotImplementedError

    def auto_level_lease_blocks_ordinary(self) -> bool:
        raise NotImplementedError

    def controller_status_observation(self) -> tuple[int, int, int, str]:
        raise NotImplementedError

    def acquire_auto_level_lease(self) -> tuple[AutoLevelWorkflowLease | None, str]:
        raise NotImplementedError

    def activate_auto_level_lease(self, lease: AutoLevelWorkflowLease) -> bool:
        raise NotImplementedError

    def auto_level_lease_current(self, lease: AutoLevelWorkflowLease) -> bool:
        raise NotImplementedError

    def complete_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        success: bool,
        reason: str = "",
    ) -> bool:
        raise NotImplementedError

    def authorize_auto_level_installation(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        restoration_confirmed: bool,
    ) -> AutoLevelInstallationTicket | None:
        raise NotImplementedError

    def finalize_auto_level_installation(
        self,
        ticket: AutoLevelInstallationTicket,
        commit: Callable[[AutoLevelMapProvenance], bool],
    ) -> AutoLevelMapProvenance | None:
        raise NotImplementedError

    def abort_auto_level_installation(
        self,
        ticket: AutoLevelInstallationTicket,
        reason: str,
    ) -> bool:
        raise NotImplementedError

    def auto_level_map_provenance_current(
        self,
        provenance: AutoLevelMapProvenance | None,
    ) -> bool:
        raise NotImplementedError

    def discard_auto_level_map(
        self,
        provenance: AutoLevelMapProvenance,
        reason: str,
    ) -> bool:
        raise NotImplementedError

    def retire_auto_level_completion(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
    ) -> bool:
        raise NotImplementedError

    def fail_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
        *,
        require_recovery: bool,
    ) -> bool:
        raise NotImplementedError

    def cancel_auto_level_lease(
        self,
        lease: AutoLevelWorkflowLease,
        reason: str,
    ) -> bool:
        raise NotImplementedError

    def _suspension_identity_current_locked(
        self,
        state: ControllerSuspensionState | None = None,
    ) -> bool:
        raise NotImplementedError

    def _suspension_blocks_tx_locked(self) -> bool:
        raise NotImplementedError

    def _retire_suspension_locked(self, controller_state: str = "") -> None:
        raise NotImplementedError

    def _retire_startup_banner_owner_locked(self, reason: str) -> None:
        raise NotImplementedError

    def _quarantine_execution_locked(self) -> None:
        raise NotImplementedError

    def _recovery_action_matches_locked(
        self,
        identity: RecoveryActionIdentity,
        *,
        require_reset_attempt: bool = True,
    ) -> bool:
        raise NotImplementedError

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
        raise NotImplementedError

    def _arm_suspension_confirmation_timer_locked(
        self,
        state: ControllerSuspensionState,
    ) -> None:
        raise NotImplementedError

    def _observe_controller_suspension_status(
        self,
        state_token: str,
        *,
        generation: int,
        serial_port: object | None,
    ) -> None:
        raise NotImplementedError

    def normal_session_initialization_required(self) -> bool:
        raise NotImplementedError

    def begin_normal_session_initialization(self, *, generation: int) -> bool:
        raise NotImplementedError

    def _normal_session_ready_locked(self) -> bool:
        raise NotImplementedError

    def _emit_normal_session_state(self) -> None:
        raise NotImplementedError

    def job_start_eligibility(
        self,
        source_identity: GcodeSourceIdentity | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        raise NotImplementedError

    def recovery_state(self) -> ExecutionRecoveryState:
        raise NotImplementedError

    def recovery_epoch(self) -> int:
        raise NotImplementedError

    def _start_reset_attempt(
        self,
        *,
        purpose: str,
        expected_identity: RecoveryActionIdentity | None = None,
    ) -> bool:
        raise NotImplementedError

    def _confirm_recovery_reset_banner(self, attempt: ResetAttempt) -> None:
        raise NotImplementedError

    def _cancel_reset_attempt_timer_locked(self) -> None:
        raise NotImplementedError

    def _refresh_recovery_trust_locked(self) -> None:
        raise NotImplementedError

    def _observe_fresh_reset_during_recovery(
        self,
        *,
        generation: int,
        reason: str,
    ) -> ExecutionRecoveryState:
        raise NotImplementedError

    def _retire_active_recovery_for_alarm(
        self,
        reason: str,
        *,
        generation: int,
    ) -> ExecutionRecoveryState:
        raise NotImplementedError

    def _notify_recovery_safety(self, state: ExecutionRecoveryState) -> None:
        raise NotImplementedError

    def _emit_recovery_state(self, state: ExecutionRecoveryState) -> None:
        raise NotImplementedError

    def stream_epoch(self) -> int:
        raise NotImplementedError

    def _current_work_identity_locked(self) -> WorkIdentity:
        raise NotImplementedError

    def _work_identity_current_locked(self, identity: WorkIdentity) -> bool:
        raise NotImplementedError

    def _stream_tool_change_identity_current_locked(
        self,
        expected_identity: StreamToolChangeIdentity | None,
    ) -> bool:
        raise NotImplementedError

    def stream_tool_change_identity_current(
        self,
        expected_identity: StreamToolChangeIdentity | None,
    ) -> bool:
        raise NotImplementedError

    def current_stream_tool_change_identity(self) -> StreamToolChangeIdentity | None:
        raise NotImplementedError

    def _tool_change_macro_admission_allowed_locked(
        self,
        source: str | None,
        expected_identity: StreamToolChangeIdentity | None,
    ) -> bool:
        raise NotImplementedError

    def _auto_level_lease_identity_current_locked(
        self,
        lease: AutoLevelWorkflowLease,
        *,
        phases: set[AutoLevelLeasePhase] | None = None,
    ) -> bool:
        raise NotImplementedError

    def _auto_level_lease_blocks_ordinary_locked(self) -> bool:
        raise NotImplementedError

    def _stream_workflow_pause_reason_locked(self) -> str | None:
        raise NotImplementedError

    def workflow_admission_snapshot(self) -> WorkflowAdmissionSnapshot:
        raise NotImplementedError

    def workflow_admission_snapshot_current(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        raise NotImplementedError

    def _workflow_admission_snapshot_current_locked(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        raise NotImplementedError

    def _workflow_admission_identity_current_locked(
        self,
        snapshot: WorkflowAdmissionSnapshot,
    ) -> bool:
        raise NotImplementedError

    def _set_cleared_source_locked(self, *, name: str | None = None) -> GcodeSourceIdentity:
        raise NotImplementedError

    def connection_generation(self) -> int:
        raise NotImplementedError

    def _session_is_current(self, generation: int, serial_port: object | None = None) -> bool:
        raise NotImplementedError

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
        raise NotImplementedError

    def _clear_recovery_after_successful_reset(self) -> None:
        raise NotImplementedError

    def suspend_watchdog(self, seconds: float, reason: str | None = None) -> None:
        raise NotImplementedError

    def _suspend_homing_watchdog(self, *, reason: str = "homing") -> None:
        raise NotImplementedError

    def reset(self, emit_state: bool = True) -> bool:
        raise NotImplementedError

    def hold(self) -> bool:
        raise NotImplementedError

    def resume(self) -> bool:
        raise NotImplementedError

    def _reset_stream_buffer(self) -> None:
        raise NotImplementedError

    def _clear_outgoing(self) -> None:
        raise NotImplementedError

    def _emit_buffer_fill(self) -> None:
        raise NotImplementedError

    def _next_manual_command_id(self) -> int:
        raise NotImplementedError

    def _resolve_manual_tracker(
        self,
        tracker: ManualCommandResultTracker | None,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        raise NotImplementedError

    def _enqueue_manual_command(
        self,
        command: str,
        source: str | None,
        *,
        tracker: ManualCommandResultTracker | None = None,
        identity: WorkIdentity | None = None,
    ) -> bool:
        raise NotImplementedError

    def _emit_exception(self, context: str, exc: BaseException) -> None:
        raise NotImplementedError

    def _signal_disconnect(
        self,
        reason: str | None = None,
        *,
        generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        raise NotImplementedError

    def clear_watchdog_ignore(self, reason: str | None = None) -> None:
        raise NotImplementedError

    def _log_rx_line(self, line: str) -> None:
        raise NotImplementedError

    def _should_forward_log_rx_line(self, line: str) -> bool:
        raise NotImplementedError

    def _maybe_pause_after_ack(self, idx: int | None) -> None:
        raise NotImplementedError

    def _pause_stream(self, reason: str | None = None) -> bool | None:
        raise NotImplementedError

    def _format_stream_error(self, raw_error: str, idx: int | None, line_text: str | None) -> str:
        raise NotImplementedError

    def _write_line(
        self,
        line: str,
        payload: bytes | None = None,
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
        raise NotImplementedError

    def _record_tx_bytes(self, count: int) -> None:
        raise NotImplementedError

    def _record_tx_line(self) -> None:
        raise NotImplementedError

    def _record_ack_latency(self, latency_ms: float) -> None:
        raise NotImplementedError

    def _invalidate_auto_level_map_locked(self, reason: str) -> None:
        raise NotImplementedError

    def _encode_line_payload(self, line: str) -> bytes:
        raise NotImplementedError

    def complete_stream_tool_change(
        self,
        expected_identity: StreamToolChangeIdentity,
        success: bool,
        reason: str | None = None,
    ) -> bool:
        raise NotImplementedError


class MacroExecutorState:
    app: Any
    grbl: Any
    ui_q: Any

    _macro_lock: threading.Lock
    _alarm_event: threading.Event
    _alarm_notified: bool
    _manual_error_event: threading.Event
    _manual_error_message: str
    _current_macro_line: str
    _workflow_command_identity: threading.local

    _macro_vars_lock: threading.Lock
    _macro_vars: dict[str, Any]
    _macro_local_vars: dict[str, Any]

    _macro_state_restored: bool
    _macro_saved_state: dict[str, str] | None
    _last_macro_run_success: bool | None

    def macro_path(self, index: int) -> str | None:
        raise NotImplementedError

    def cancel_macro(self, reason: str | None = None) -> bool:
        raise NotImplementedError

    def _macro_send(self, command: str, *, wait_for_idle: bool = True) -> None:
        raise NotImplementedError

    def _parse_timeout(self, cmd_parts: list[str], default: float) -> float:
        raise NotImplementedError

    def _wait_for_connection_state(self, target: bool, timeout_s: float = 10.0) -> bool:
        raise NotImplementedError

    def _wait_for_grbl_ready_state(self, timeout_s: float = 10.0) -> bool:
        raise NotImplementedError

    def _wait_for_gcode_load_result(self, token: int, timeout_s: float = 120.0) -> bool:
        raise NotImplementedError

    def _macro_wait_for_idle(self, timeout_s: float = 30.0) -> None:
        raise NotImplementedError

    def _macro_wait_for_status(self, timeout_s: float = 1.0) -> bool:
        raise NotImplementedError

    def _macro_wait_for_modal(self, seq: int | None = None, timeout_s: float = 1.0) -> bool:
        raise NotImplementedError

    def _snapshot_macro_state(self) -> dict[str, str]:
        raise NotImplementedError

    def _macro_force_mm(self) -> None:
        raise NotImplementedError

    def _macro_restore_units(self) -> None:
        raise NotImplementedError

    def _macro_restore_state(self) -> bool:
        raise NotImplementedError

    def _workflow_restore_units(self) -> None:
        raise NotImplementedError

    def _workflow_restore_state(self) -> bool:
        raise NotImplementedError

    def _parse_macro_prompt(
        self,
        line: str,
        macro_vars: dict[str, Any] | None = None,
    ) -> tuple[str, str, list[str], str, dict[str, str | None]]:
        raise NotImplementedError

    def _format_macro_message(self, text: str) -> str:
        raise NotImplementedError

    def _bcnc_compile_line(self, line: str) -> Any:
        raise NotImplementedError

    def _bcnc_evaluate_line(self, compiled: Any) -> Any:
        raise NotImplementedError

    def _execute_command(self, line: str, raw_line: str | None = None) -> Any:
        raise NotImplementedError

    def _strip_prompt_tokens(self, line: str) -> str:
        raise NotImplementedError

    def _macro_eval_globals(self) -> dict:
        raise NotImplementedError

    def _macro_exec_globals(self) -> dict:
        raise NotImplementedError

UiCallResultQueue: TypeAlias = queue.Queue[tuple[bool, Any]]
UiCallCancelToken: TypeAlias = threading.Event
UiCallStartQueue: TypeAlias = queue.Queue[bool]
UiPromptResultQueue: TypeAlias = queue.Queue[str]


class _TupleCompatibleUiEvent(Sequence[Any]):
    def as_tuple(self) -> tuple[Any, ...]:
        raise NotImplementedError

    def __getitem__(self, index: int | slice) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return len(self.as_tuple())

    def __iter__(self) -> Iterator[Any]:
        return iter(self.as_tuple())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _TupleCompatibleUiEvent):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False

    def __hash__(self) -> int:
        return hash(self.as_tuple())


@dataclass(frozen=True, slots=True)
class ConnectionEvent(_TupleCompatibleUiEvent):
    connected: bool
    port: str | None
    generation: int | None = None

    def as_tuple(self) -> tuple[str, bool, str | None]:
        return ("conn", self.connected, self.port)


@dataclass(frozen=True, slots=True)
class ReadyEvent(_TupleCompatibleUiEvent):
    is_ready: bool
    generation: int | None = None
    recovery_epoch: int | None = None
    reset_attempt_id: int | None = None

    def as_tuple(self) -> tuple[str, bool]:
        return ("ready", self.is_ready)


@dataclass(frozen=True, slots=True)
class AlarmEvent(_TupleCompatibleUiEvent):
    message: str
    generation: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, str]:
        return ("alarm", self.message)


@dataclass(frozen=True, slots=True)
class StatusEvent(_TupleCompatibleUiEvent):
    line: str
    generation: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, str]:
        return ("status", self.line)


@dataclass(frozen=True, slots=True)
class SettingsDumpDoneEvent(_TupleCompatibleUiEvent):
    generation: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str]:
        return ("settings_dump_done",)


@dataclass(frozen=True, slots=True)
class GcodeSentEvent(_TupleCompatibleUiEvent):
    idx: int
    line: str
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, int, str]:
        return ("gcode_sent", self.idx, self.line)


@dataclass(frozen=True, slots=True)
class GcodeAckedEvent(_TupleCompatibleUiEvent):
    idx: int
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, int]:
        return ("gcode_acked", self.idx)


@dataclass(frozen=True, slots=True)
class ProgressEvent(_TupleCompatibleUiEvent):
    done: int
    total: int
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, int, int]:
        return ("progress", self.done, self.total)


@dataclass(frozen=True, slots=True)
class ProgressBytesEvent(_TupleCompatibleUiEvent):
    acked_offset: int
    file_size_bytes: int
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, int, int]:
        return ("progress_bytes", self.acked_offset, self.file_size_bytes)


@dataclass(frozen=True, slots=True, eq=False)
class StreamStateEvent(_TupleCompatibleUiEvent):
    state: str
    detail: Any | None
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None
    reset_attempt_id: int | None = None

    def as_tuple(self) -> tuple[str, str, Any | None]:
        return ("stream_state", self.state, self.detail)


@dataclass(frozen=True, slots=True, eq=False)
class StreamCompletionEofEvent(_TupleCompatibleUiEvent):
    verified_eof: bool
    total_lines: int
    total_lines_known: bool
    last_acked_index: int
    send_index: int
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, bool, int, bool, int, int]:
        return (
            "stream_completion_eof",
            self.verified_eof,
            self.total_lines,
            self.total_lines_known,
            self.last_acked_index,
            self.send_index,
        )


@dataclass(frozen=True, slots=True, eq=False)
class StreamInterruptedEvent(_TupleCompatibleUiEvent):
    was_streaming: bool
    reason: str | None = None
    generation: int | None = None

    def as_tuple(self) -> tuple[str, bool, str | None]:
        return ("stream_interrupted", self.was_streaming, self.reason)


@dataclass(frozen=True, slots=True, eq=False)
class StreamErrorEvent(_TupleCompatibleUiEvent):
    message: str
    err_idx: int | None
    err_line: str | None
    gcode_name: str | None
    generation: int | None = None

    def as_tuple(self) -> tuple[str, str, int | None, str | None, str | None]:
        return (
            "stream_error",
            self.message,
            self.err_idx,
            self.err_line,
            self.gcode_name,
        )


@dataclass(frozen=True, slots=True, eq=False)
class StreamPauseReasonEvent(_TupleCompatibleUiEvent):
    reason: str
    generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None

    def as_tuple(self) -> tuple[str, str]:
        return ("stream_pause_reason", self.reason)


@dataclass(frozen=True, slots=True, eq=False)
class RecoveryRequiredEvent(_TupleCompatibleUiEvent):
    state: ExecutionRecoveryState

    @property
    def generation(self) -> int:
        return int(self.state.connection_generation)

    @property
    def recovery_epoch(self) -> int:
        return int(self.state.recovery_epoch)

    @property
    def stream_epoch(self) -> int:
        return int(self.state.stream_epoch)

    @property
    def reset_attempt_id(self) -> int | None:
        return self.state.reset_attempt_id

    def as_tuple(self) -> tuple[str, ExecutionRecoveryState]:
        return ("recovery_required", self.state)


@dataclass(frozen=True, slots=True, eq=False)
class RecoveryCompleteEvent(_TupleCompatibleUiEvent):
    """Install one approved value-bound snapshot in the UI atomically."""

    completed_state: ExecutionRecoveryState
    snapshot: RecoveryStateSnapshot
    finalization_identity: RecoveryFinalizationIdentity
    generation: int
    stream_epoch: int
    recovery_epoch: int

    def as_tuple(self) -> tuple[str, ExecutionRecoveryState, RecoveryStateSnapshot]:
        return ("recovery_complete", self.completed_state, self.snapshot)


@dataclass(frozen=True, slots=True, eq=False)
class NormalSessionInitializationEvent(_TupleCompatibleUiEvent):
    """Project worker-owned communication-ready/job-not-ready state into the UI."""

    state: NormalSessionInitializationState
    snapshot: RecoveryStateSnapshot | None
    generation: int
    recovery_epoch: int

    def as_tuple(
        self,
    ) -> tuple[str, NormalSessionInitializationState, RecoveryStateSnapshot | None]:
        return ("normal_session_initialization", self.state, self.snapshot)


@dataclass(frozen=True, slots=True, eq=False)
class ConnectionScopedEvent(_TupleCompatibleUiEvent):
    """Attach a connection generation to a legacy tuple-shaped UI event."""

    payload: tuple[Any, ...]
    generation: int
    stream_epoch: int | None = None
    recovery_epoch: int | None = None
    reset_attempt_id: int | None = None

    def as_tuple(self) -> tuple[Any, ...]:
        return self.payload


UiLifecycleEvent: TypeAlias = (
    ConnectionEvent
    | ReadyEvent
    | AlarmEvent
    | StatusEvent
    | SettingsDumpDoneEvent
)

UiStreamingProgressEvent: TypeAlias = (
    GcodeSentEvent
    | GcodeAckedEvent
    | ProgressEvent
    | ProgressBytesEvent
)

UiStreamingControlEvent: TypeAlias = (
    StreamStateEvent
    | StreamCompletionEofEvent
    | StreamInterruptedEvent
    | StreamErrorEvent
    | StreamPauseReasonEvent
    | RecoveryRequiredEvent
    | RecoveryCompleteEvent
    | NormalSessionInitializationEvent
)

UiEvent = (
    UiLifecycleEvent
    | UiStreamingProgressEvent
    | UiStreamingControlEvent
    | ConnectionScopedEvent
    | tuple[Literal["conn"], bool, str | None]
    | tuple[
        Literal["auto_level_map_invalidated"],
        AutoLevelMapProvenance,
        str,
    ]
    | tuple[Literal["ui_call"], Callable[..., Any], tuple[Any, ...], dict[str, Any], UiCallResultQueue]
    | tuple[
        Literal["ui_call"],
        Callable[..., Any],
        tuple[Any, ...],
        dict[str, Any],
        UiCallResultQueue,
        UiCallCancelToken,
    ]
    | tuple[
        Literal["ui_call"],
        Callable[..., Any],
        tuple[Any, ...],
        dict[str, Any],
        UiCallResultQueue,
        UiCallCancelToken,
        UiCallStartQueue,
    ]
    | tuple[Literal["ui_post"], Callable[..., Any], tuple[Any, ...], dict[str, Any]]
    | tuple[Literal["macro_prompt"], str, str, list[str], str, UiPromptResultQueue]
    | tuple[Literal["gcode_load_progress"], int, int, int, str]
    | tuple[Literal["gcode_loaded"], int, str, list[str], str | None, bool, Any | None]
    | tuple[
        Literal["gcode_loaded_stream"],
        int,
        str,
        Any,
        list[str],
        str | None,
        int | None,
        Any | None,
        bool,
    ]
    | tuple[
        Literal["gcode_load_invalid"],
        int,
        str,
        int,
        int | None,
        int | None,
        int | None,
    ]
    | tuple[
        Literal["gcode_load_invalid"],
        int,
        str,
        int,
        int | None,
        int | None,
        int | None,
        int | None,
    ]
    | tuple[Literal["gcode_load_invalid_command"], int, str, int | None, str | None]
    | tuple[Literal["gcode_load_error"], int, str, str]
    | tuple[Literal["log"], str]
    | tuple[Literal["manual_queue_drop"], int, int]
    | tuple[Literal["log_tx"], str]
    | tuple[Literal["log_rx"], str]
    | tuple[Literal["settings_dump_done"]]
    | tuple[Literal["manual_error"], str, str | None]
    | tuple[Literal["ready"], bool]
    | tuple[Literal["recovery_required"], ExecutionRecoveryState]
    | tuple[Literal["alarm"], str]
    | tuple[Literal["status"], str]
    | tuple[Literal["buffer_fill"], int, int, int]
    | tuple[Literal["throughput"], float]
    | tuple[Literal["stream_state"], str, Any | None]
    | tuple[Literal["stream_completion_eof"], bool, int, bool, int, int]
    | tuple[Literal["stream_interrupted"], bool, str | None]
    | tuple[Literal["stream_error"], str, int | None, str | None, str | None]
    | tuple[Literal["stream_pause_reason"], str]
    | tuple[Literal["stream_vacuum_directive"], bool]
    | tuple[Literal["stream_vacuum_directive"], bool, int | None]
    | tuple[Literal["stream_vacuum_directive"], bool, int | None, bool]
    | tuple[
        Literal["stream_tool_change"],
        int | None,
        str,
        StreamToolChangeIdentity,
    ]
    | tuple[Literal["spindle_state"], bool, int | None]
    | tuple[Literal["gcode_sent"], int, str]
    | tuple[Literal["gcode_acked"], int]
    | tuple[Literal["progress"], int, int]
    | tuple[Literal["progress_bytes"], int, int]
)
