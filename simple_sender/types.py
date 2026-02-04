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
    def mark_sent_upto(self, idx: int) -> None: ...
    def mark_acked_upto(self, idx: int) -> None: ...
    def highlight_current(self, idx: int) -> None: ...


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

    def _update_current_highlight(self) -> None: ...
    def _update_live_estimate(self, done: int, total: int) -> None: ...
    def _maybe_notify_job_completion(self, done: int, total: int) -> None: ...
    def _format_throughput(self, bps: float) -> str: ...

    def __getattr__(self, name: str) -> Any: ...

StreamQueueItem: TypeAlias = tuple[int, bool, int | None, str]
StreamPendingItem: TypeAlias = tuple[str, bool, int | None]
ManualPendingItem: TypeAlias = tuple[str, bytes, int]


class GrblWorkerState:
    ui_q: Any

    _streaming: bool
    _paused: bool
    _stream_lock: threading.Lock
    _stream_token: int
    _stream_line_queue: deque[StreamQueueItem]
    _stream_pending_item: StreamPendingItem | None
    _manual_pending_item: ManualPendingItem | None
    _resume_preamble: deque[str]
    _pause_after_idx: int | None
    _pause_after_reason: str | None
    _send_index: int
    _ack_index: int
    _stream_buf_used: int
    _rx_window: int

    _outgoing_q: queue.Queue[str]
    _purge_jog_queue: threading.Event
    _abort_writes: threading.Event

    _ready: bool
    _alarm_active: bool
    _settings_dump_active: bool
    _settings_dump_seen: bool
    _last_manual_source: str | None

    _watchdog_paused: bool
    _watchdog_trip_ts: float
    _watchdog_ignore_until: float
    _watchdog_ignore_reason: str | None
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
    _gcode_name: str | None

    def is_connected(self) -> bool:
        raise NotImplementedError

    def send_realtime(self, command: bytes) -> None:
        raise NotImplementedError

    def suspend_watchdog(self, seconds: float, reason: str | None = None) -> None:
        raise NotImplementedError

    def reset(self, emit_state: bool = True) -> None:
        raise NotImplementedError

    def hold(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def _reset_stream_buffer(self) -> None:
        raise NotImplementedError

    def _clear_outgoing(self) -> None:
        raise NotImplementedError

    def _emit_buffer_fill(self) -> None:
        raise NotImplementedError

    def _emit_exception(self, context: str, exc: BaseException) -> None:
        raise NotImplementedError

    def _signal_disconnect(self, reason: str | None = None) -> None:
        raise NotImplementedError

    def _log_rx_line(self, line: str) -> None:
        raise NotImplementedError

    def _maybe_pause_after_ack(self, idx: int | None) -> None:
        raise NotImplementedError

    def _pause_stream(self, reason: str | None = None) -> None:
        raise NotImplementedError

    def _format_stream_error(self, raw_error: str, idx: int | None, line_text: str | None) -> str:
        raise NotImplementedError

    def _write_line(self, line: str, payload: bytes | None = None, *, allow_abort: bool = False) -> bool:
        raise NotImplementedError

    def _record_tx_bytes(self, count: int) -> None:
        raise NotImplementedError

    def _encode_line_payload(self, line: str) -> bytes:
        raise NotImplementedError


class MacroExecutorState:
    app: Any
    grbl: Any
    ui_q: Any

    _macro_lock: threading.Lock
    _alarm_event: threading.Event
    _alarm_notified: bool
    _current_macro_line: str

    _macro_vars_lock: threading.Lock
    _macro_vars: dict[str, Any]
    _macro_local_vars: dict[str, Any]

    _macro_state_restored: bool
    _macro_saved_state: dict[str, str] | None

    def macro_path(self, index: int) -> str | None:
        raise NotImplementedError

    def _macro_send(self, command: str, *, wait_for_idle: bool = True) -> None:
        raise NotImplementedError

    def _parse_timeout(self, cmd_parts: list[str], default: float) -> float:
        raise NotImplementedError

    def _wait_for_connection_state(self, target: bool, timeout_s: float = 10.0) -> bool:
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
UiPromptResultQueue: TypeAlias = queue.Queue[str]
UiValidationResultQueue: TypeAlias = queue.Queue[bool]

UiEvent = (
    tuple[Literal["conn"], bool, str | None]
    | tuple[Literal["ui_call"], Callable[..., Any], tuple[Any, ...], dict[str, Any], UiCallResultQueue]
    | tuple[Literal["ui_post"], Callable[..., Any], tuple[Any, ...], dict[str, Any]]
    | tuple[Literal["macro_prompt"], str, str, list[str], str, UiPromptResultQueue]
    | tuple[Literal["gcode_load_progress"], int, int, int, str]
    | tuple[Literal["streaming_validation_prompt"], int, str, int, int, UiValidationResultQueue]
    | tuple[Literal["gcode_loaded"], int, str, list[str], str | None, bool, Any | None]
    | tuple[Literal["gcode_loaded_stream"], int, str, Any, list[str], str | None, int | None, Any | None]
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
    | tuple[Literal["stream_interrupted"], bool, str | None]
    | tuple[Literal["stream_error"], str, int | None, str | None, str | None]
    | tuple[Literal["stream_pause_reason"], str]
    | tuple[Literal["gcode_sent"], int, str]
    | tuple[Literal["gcode_acked"], int]
    | tuple[Literal["progress"], int, int]
)
