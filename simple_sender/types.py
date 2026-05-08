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
import threading
from dataclasses import dataclass, field
from collections import deque
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


@dataclass(slots=True)
class ManualCommandResultTracker:
    command_id: int
    command: str
    source: str | None = None
    success: bool | None = None
    error: str | None = None
    completed: bool = False
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


class GrblWorkerState:
    ui_q: Any

    _streaming: bool
    _paused: bool
    _stream_lock: threading.Lock
    _stream_token: int
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
    _send_index: int
    _ack_index: int
    _ack_byte_offset: int
    _stream_file_size_bytes: int
    _stream_buf_used: int
    _rx_window: int

    _outgoing_q: queue.Queue[str]
    _manual_source_queue: deque[str | None]
    _manual_tracker_queue: deque[ManualCommandResultTracker | None]
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

    def send_realtime(self, command: bytes) -> bool:
        raise NotImplementedError

    def suspend_watchdog(self, seconds: float, reason: str | None = None) -> None:
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
    ) -> bool:
        raise NotImplementedError

    def _emit_exception(self, context: str, exc: BaseException) -> None:
        raise NotImplementedError

    def _signal_disconnect(self, reason: str | None = None) -> None:
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

    def _write_line(self, line: str, payload: bytes | None = None, *, allow_abort: bool = False) -> bool:
        raise NotImplementedError

    def _record_tx_bytes(self, count: int) -> None:
        raise NotImplementedError

    def _record_tx_line(self) -> None:
        raise NotImplementedError

    def _record_ack_latency(self, latency_ms: float) -> None:
        raise NotImplementedError

    def _encode_line_payload(self, line: str) -> bytes:
        raise NotImplementedError

    def complete_stream_tool_change(self, success: bool, reason: str | None = None) -> None:
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

    def as_tuple(self) -> tuple[str, bool, str | None]:
        return ("conn", self.connected, self.port)


@dataclass(frozen=True, slots=True)
class ReadyEvent(_TupleCompatibleUiEvent):
    is_ready: bool

    def as_tuple(self) -> tuple[str, bool]:
        return ("ready", self.is_ready)


@dataclass(frozen=True, slots=True)
class AlarmEvent(_TupleCompatibleUiEvent):
    message: str

    def as_tuple(self) -> tuple[str, str]:
        return ("alarm", self.message)


@dataclass(frozen=True, slots=True)
class StatusEvent(_TupleCompatibleUiEvent):
    line: str

    def as_tuple(self) -> tuple[str, str]:
        return ("status", self.line)


@dataclass(frozen=True, slots=True)
class SettingsDumpDoneEvent(_TupleCompatibleUiEvent):
    def as_tuple(self) -> tuple[str]:
        return ("settings_dump_done",)


@dataclass(frozen=True, slots=True)
class GcodeSentEvent(_TupleCompatibleUiEvent):
    idx: int
    line: str

    def as_tuple(self) -> tuple[str, int, str]:
        return ("gcode_sent", self.idx, self.line)


@dataclass(frozen=True, slots=True)
class GcodeAckedEvent(_TupleCompatibleUiEvent):
    idx: int

    def as_tuple(self) -> tuple[str, int]:
        return ("gcode_acked", self.idx)


@dataclass(frozen=True, slots=True)
class ProgressEvent(_TupleCompatibleUiEvent):
    done: int
    total: int

    def as_tuple(self) -> tuple[str, int, int]:
        return ("progress", self.done, self.total)


@dataclass(frozen=True, slots=True)
class ProgressBytesEvent(_TupleCompatibleUiEvent):
    acked_offset: int
    file_size_bytes: int

    def as_tuple(self) -> tuple[str, int, int]:
        return ("progress_bytes", self.acked_offset, self.file_size_bytes)


@dataclass(frozen=True, slots=True, eq=False)
class StreamStateEvent(_TupleCompatibleUiEvent):
    state: str
    detail: Any | None

    def as_tuple(self) -> tuple[str, str, Any | None]:
        return ("stream_state", self.state, self.detail)


@dataclass(frozen=True, slots=True, eq=False)
class StreamCompletionEofEvent(_TupleCompatibleUiEvent):
    verified_eof: bool
    total_lines: int
    total_lines_known: bool
    last_acked_index: int
    send_index: int

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

    def as_tuple(self) -> tuple[str, bool, str | None]:
        return ("stream_interrupted", self.was_streaming, self.reason)


@dataclass(frozen=True, slots=True, eq=False)
class StreamErrorEvent(_TupleCompatibleUiEvent):
    message: str
    err_idx: int | None
    err_line: str | None
    gcode_name: str | None

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

    def as_tuple(self) -> tuple[str, str]:
        return ("stream_pause_reason", self.reason)


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
)

UiEvent = (
    UiLifecycleEvent
    | UiStreamingProgressEvent
    | UiStreamingControlEvent
    | tuple[Literal["conn"], bool, str | None]
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
    | tuple[Literal["stream_tool_change"], int | None, str]
    | tuple[Literal["spindle_state"], bool, int | None]
    | tuple[Literal["gcode_sent"], int, str]
    | tuple[Literal["gcode_acked"], int]
    | tuple[Literal["progress"], int, int]
    | tuple[Literal["progress_bytes"], int, int]
)
