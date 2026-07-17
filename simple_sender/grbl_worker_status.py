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

import logging
import math
import re
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time
from collections import deque
from dataclasses import replace
from typing import Any, cast, TYPE_CHECKING

from simple_sender.types import (
    AlarmEvent,
    ConnectionScopedEvent,
    GcodeAckedEvent,
    GrblWorkerState,
    ProgressBytesEvent,
    ProgressEvent,
    ReadyEvent,
    NormalSessionPhase,
    RecoveryPhase,
    RecoveryStateSnapshot,
    SettingsDumpDoneEvent,
    StatusEvent,
    StreamCompletionEofEvent,
    StreamErrorEvent,
    StreamStateEvent,
    StartupBannerOwnership,
    StartupBannerPhase,
)
from simple_sender.status_coordinates import (
    parse_exact_status_xyz,
    parse_status_coordinate_fields,
)

from .utils.constants import (
    RT_STATUS,
    RX_BUFFER_SIZE,
    RX_OK_SUMMARY_INTERVAL,
    RX_STATUS_LOG_INTERVAL,
    WATCHDOG_ALARM_DISCONNECT_TIMEOUT,
    WATCHDOG_DISCONNECT_TIMEOUT,
    WATCHDOG_RX_TIMEOUT,
    WATCHDOG_READY_ARM_GRACE,
)
from .utils.validation import validate_interval


logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_WATCHDOG_READY_ARM_GRACE_S = float(WATCHDOG_READY_ARM_GRACE)
_MANUAL_MOTION_STATUS_INTERVAL_S = 0.1
_STATUS_INTERVAL_WAKE_SLICE_S = 0.05
_STATUS_WAIT_OVERSHOOT_RECENT_WINDOW_S = 60.0
_STATUS_WAIT_OVERSHOOT_LOG_THRESHOLD_MS = 250.0


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _annotate_alarm(message: str) -> str:
    from . import grbl_worker as grbl_worker_mod

    return cast(str, grbl_worker_mod.annotate_grbl_alarm(message))


class GrblWorkerStatusMixin(GrblWorkerState):
    if TYPE_CHECKING:
        ser: Any | None

        def startup_banner_ownership(self) -> StartupBannerOwnership | None: ...

        def _consume_startup_banner_owner_locked(
            self,
            *,
            generation: int,
            serial_port: object,
        ) -> bool: ...

        def _timeout_startup_banner_session(
            self,
            *,
            generation: int,
            serial_port: object,
            observed_owner: StartupBannerOwnership,
        ) -> bool: ...

    def _resolve_modal_query_tracker(self, line: str) -> None:
        if not str(line or "").startswith("[GC:"):
            return
        tracker = None
        with self._stream_lock:
            if self._stream_line_queue:
                queued_item = self._stream_line_queue[0]
                if not bool(getattr(queued_item, "is_gcode", False)):
                    command = str(getattr(queued_item, "line", "") or "").strip().upper()
                    if command == "$G":
                        tracker = getattr(queued_item, "manual_tracker", None)
        if tracker is None:
            return
        self._resolve_manual_tracker(tracker, success=True)
        wake_evt = getattr(self, "_tx_activity_evt", None)
        if wake_evt is not None:
            try:
                wake_evt.set()
            except Exception as exc:
                _log_suppressed("Failed signaling TX activity after modal-query response", exc)

    def _manual_motion_status_active(
        self,
        *,
        now: float | None = None,
        state_token: str | None = None,
    ) -> bool:
        if self._streaming or self._paused:
            return False
        token = str(
            state_token
            if state_token is not None
            else getattr(self, "_last_status_state_token", "")
            or ""
        ).strip().lower()
        if token.startswith(("jog", "hold")):
            return True
        busy_fn = getattr(self, "manual_queue_busy", None)
        if callable(busy_fn):
            try:
                if bool(busy_fn()):
                    return True
            except Exception as exc:
                _log_suppressed("Failed checking manual queue busy state", exc)
        if now is None:
            now = time.time()
        try:
            until_ts = float(getattr(self, "_manual_motion_status_grace_until_ts", 0.0) or 0.0)
        except Exception:
            until_ts = 0.0
        return until_ts > float(now)

    def _update_manual_motion_session_state(
        self,
        active: bool,
        *,
        now: float | None = None,
        source: str = "",
    ) -> bool:
        now_ts = float(time.time() if now is None else now)
        is_active = bool(active)
        session_active = bool(
            getattr(self, "_manual_motion_status_session_active", False)
        )
        if is_active and (not session_active):
            self._manual_motion_status_session_active = True
            self._manual_motion_status_session_count = int(
                getattr(self, "_manual_motion_status_session_count", 0) or 0
            ) + 1
            self._manual_motion_status_session_start_ts = now_ts
            self._manual_motion_status_session_last_start_ts = now_ts
            self._manual_motion_status_session_last_change_ts = now_ts
            self._manual_motion_status_session_last_source = str(source or "")
            # Reset interval baselines on session transitions so cross-session
            # idle gaps are not counted as manual-motion cadence outliers.
            self._manual_motion_status_query_last_ts = 0.0
            self._manual_motion_status_rx_last_ts = 0.0
        elif (not is_active) and session_active:
            self._manual_motion_status_session_active = False
            self._manual_motion_status_session_start_ts = 0.0
            self._manual_motion_status_session_last_end_ts = now_ts
            self._manual_motion_status_session_last_change_ts = now_ts
            self._manual_motion_status_session_last_source = str(source or "")
        return bool(getattr(self, "_manual_motion_status_session_active", False))

    def _record_manual_motion_query_interval(self, now: float) -> None:
        prev_ts = float(getattr(self, "_manual_motion_status_query_last_ts", 0.0) or 0.0)
        self._manual_motion_status_query_last_ts = float(now)
        if prev_ts <= 0.0 or now <= prev_ts:
            return
        interval_ms = max(0.0, (float(now) - prev_ts) * 1000.0)
        sample_count = int(getattr(self, "_manual_motion_status_query_count", 0) or 0) + 1
        prev_avg = float(getattr(self, "_manual_motion_status_query_interval_avg_ms", 0.0) or 0.0)
        self._manual_motion_status_query_count = sample_count
        self._manual_motion_status_query_interval_avg_ms = (
            ((prev_avg * max(0, sample_count - 1)) + interval_ms) / float(sample_count)
        )
        self._manual_motion_status_query_interval_max_ms = max(
            float(getattr(self, "_manual_motion_status_query_interval_max_ms", 0.0) or 0.0),
            interval_ms,
        )

    def _record_manual_motion_rx_interval(self, now: float) -> None:
        prev_ts = float(getattr(self, "_manual_motion_status_rx_last_ts", 0.0) or 0.0)
        self._manual_motion_status_rx_last_ts = float(now)
        if prev_ts <= 0.0 or now <= prev_ts:
            return
        interval_ms = max(0.0, (float(now) - prev_ts) * 1000.0)
        sample_count = int(getattr(self, "_manual_motion_status_rx_count", 0) or 0) + 1
        prev_avg = float(getattr(self, "_manual_motion_status_rx_interval_avg_ms", 0.0) or 0.0)
        self._manual_motion_status_rx_count = sample_count
        self._manual_motion_status_rx_interval_avg_ms = (
            ((prev_avg * max(0, sample_count - 1)) + interval_ms) / float(sample_count)
        )
        self._manual_motion_status_rx_interval_max_ms = max(
            float(getattr(self, "_manual_motion_status_rx_interval_max_ms", 0.0) or 0.0),
            interval_ms,
        )

    def _record_status_wait_sample(
        self,
        *,
        requested_s: float,
        actual_s: float,
        reason: str,
    ) -> None:
        requested = max(0.0, float(requested_s))
        actual = max(0.0, float(actual_s))
        overshoot_ms = max(0.0, (actual - requested) * 1000.0)
        sample_count = int(getattr(self, "_status_wait_sample_count", 0) or 0) + 1
        prev_requested_avg = float(
            getattr(self, "_status_wait_requested_avg_s", 0.0) or 0.0
        )
        prev_actual_avg = float(getattr(self, "_status_wait_actual_avg_s", 0.0) or 0.0)
        self._status_wait_sample_count = sample_count
        self._status_wait_requested_avg_s = (
            ((prev_requested_avg * max(0, sample_count - 1)) + requested)
            / float(sample_count)
        )
        self._status_wait_actual_avg_s = (
            ((prev_actual_avg * max(0, sample_count - 1)) + actual)
            / float(sample_count)
        )
        self._status_wait_overshoot_max_ms = max(
            float(getattr(self, "_status_wait_overshoot_max_ms", 0.0) or 0.0),
            overshoot_ms,
        )
        normalized_reason = str(reason or "unknown").strip().lower() or "unknown"
        self._status_wait_last_reason = normalized_reason
        self._status_wait_last_requested_s = requested
        self._status_wait_last_actual_s = actual
        self._status_wait_last_overshoot_ms = overshoot_ms
        reason_counts = getattr(self, "_status_wait_reason_counts", None)
        if not isinstance(reason_counts, dict):
            reason_counts = {}
            self._status_wait_reason_counts = reason_counts
        reason_counts[normalized_reason] = int(reason_counts.get(normalized_reason, 0) or 0) + 1
        trace = getattr(self, "_status_wait_trace", None)
        sample_ts = float(time.time())
        sample = {
            "ts": sample_ts,
            "requested_s": requested,
            "actual_s": actual,
            "overshoot_ms": overshoot_ms,
            "reason": normalized_reason,
        }
        if isinstance(trace, list | deque):
            try:
                trace.append(sample)
            except Exception:
                trace = deque([sample], maxlen=120)
                self._status_wait_trace = trace
            else:
                if isinstance(trace, list) and len(trace) > 120:
                    del trace[:-120]
        else:
            trace = deque([sample], maxlen=120)
            self._status_wait_trace = trace
        recent_max_ms = 0.0
        try:
            recent_window_s = float(
                getattr(
                    self,
                    "_status_wait_overshoot_recent_window_s",
                    _STATUS_WAIT_OVERSHOOT_RECENT_WINDOW_S,
                )
                or _STATUS_WAIT_OVERSHOOT_RECENT_WINDOW_S
            )
        except Exception:
            recent_window_s = _STATUS_WAIT_OVERSHOOT_RECENT_WINDOW_S
        recent_window_s = max(1.0, recent_window_s)
        recent_cutoff_ts = sample_ts - recent_window_s
        if isinstance(trace, list | deque):
            for entry in trace:
                if not isinstance(entry, dict):
                    continue
                try:
                    if float(entry.get("ts", 0.0) or 0.0) < recent_cutoff_ts:
                        continue
                    recent_max_ms = max(
                        recent_max_ms,
                        float(entry.get("overshoot_ms", 0.0) or 0.0),
                    )
                except Exception:
                    continue
        else:
            recent_max_ms = max(recent_max_ms, overshoot_ms)
        self._status_wait_overshoot_recent_max_ms = recent_max_ms
        if overshoot_ms >= float(_STATUS_WAIT_OVERSHOOT_LOG_THRESHOLD_MS):
            state_token = str(getattr(self, "_last_status_state_token", "") or "")
            stream_state = (
                "streaming"
                if bool(getattr(self, "_streaming", False))
                else "non_streaming"
            )
            logger.info(
                "[status/wait] Overshoot spike: overshoot_ms=%.2f requested_ms=%.2f actual_ms=%.2f "
                "reason=%s state=%s stream=%s",
                float(overshoot_ms),
                float(requested * 1000.0),
                float(actual * 1000.0),
                normalized_reason,
                state_token or "unknown",
                stream_state,
            )

    def _wait_status_interval(self, stop_evt: threading.Event, interval_s: float) -> bool:
        interval = max(0.0, float(interval_s))
        changed_evt = getattr(self, "_status_interval_changed_evt", None)
        started = time.monotonic()
        if not isinstance(changed_evt, threading.Event) or interval <= 0.25:
            stopped = bool(stop_evt.wait(interval))
            elapsed = max(0.0, time.monotonic() - started)
            self._record_status_wait_sample(
                requested_s=interval,
                actual_s=elapsed,
                reason="stop" if stopped else "timeout",
            )
            return stopped
        deadline = time.monotonic() + interval
        while not stop_evt.is_set():
            if changed_evt.is_set():
                changed_evt.clear()
                elapsed = max(0.0, time.monotonic() - started)
                self._record_status_wait_sample(
                    requested_s=interval,
                    actual_s=elapsed,
                    reason="interval_changed",
                )
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                elapsed = max(0.0, time.monotonic() - started)
                self._record_status_wait_sample(
                    requested_s=interval,
                    actual_s=elapsed,
                    reason="timeout",
                )
                return False
            if stop_evt.wait(min(_STATUS_INTERVAL_WAKE_SLICE_S, remaining)):
                elapsed = max(0.0, time.monotonic() - started)
                self._record_status_wait_sample(
                    requested_s=interval,
                    actual_s=elapsed,
                    reason="stop",
                )
                return True
        elapsed = max(0.0, time.monotonic() - started)
        self._record_status_wait_sample(
            requested_s=interval,
            actual_s=elapsed,
            reason="stop",
        )
        return True

    def set_status_poll_interval(self, interval: float) -> None:
        """Set status polling interval.
        
        Args:
            interval: Polling interval in seconds
            
        Raises:
            ValueError: If interval is invalid
        """
        interval = validate_interval(interval, min_val=0.01)
        
        changed = True
        with self._status_interval_lock:
            prev = float(getattr(self, "_status_poll_interval", interval))
            self._status_poll_interval = interval
            changed = abs(prev - interval) > 1e-9

        if changed:
            logger.debug(f"Status poll interval set to {interval}s")
            changed_evt = getattr(self, "_status_interval_changed_evt", None)
            if isinstance(changed_evt, threading.Event):
                try:
                    changed_evt.set()
                except Exception as exc:
                    _log_suppressed("Failed signaling status interval change event", exc)

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
    
    def _mark_ready(self, *, generation: int | None = None) -> None:
        """Mark GRBL as ready (banner received)."""
        if generation is None:
            generation = self.connection_generation()
        if not self._session_is_current(int(generation)) or self.recovery_required():
            return
        if not self._ready:
            self._ready = True
            self._watchdog_ready_armed = False
            self._watchdog_ready_ts = time.time()
            self._connect_started_ts = 0.0
            self.ui_q.put(
                ReadyEvent(
                    True,
                    generation=int(generation),
                    recovery_epoch=self.recovery_epoch(),
                )
            )
            logger.info(
                "Communication Ready: generation=%s recovery_epoch=%s",
                generation,
                self.recovery_epoch(),
            )

    def _watchdog_enforced(self, now: float) -> bool:
        if self._streaming or self._paused:
            return True
        if not self._ready:
            return False
        if bool(getattr(self, "_watchdog_ready_armed", False)):
            return True
        ready_ts = float(getattr(self, "_watchdog_ready_ts", 0.0) or 0.0)
        if ready_ts <= 0.0:
            return True
        if (now - ready_ts) < _WATCHDOG_READY_ARM_GRACE_S:
            return False
        self._watchdog_ready_armed = True
        return True
    
    def _safe_ui_put(self, *args, context: str = "operation") -> None:
        """Safely put item on UI queue with error logging.
        
        Wraps ui_q.put() to ensure worker thread continues even if UI queue fails.
        
        Args:
            *args: Arguments to pass to ui_q.put()
            context: Description of operation for error logging
        """
        try:
            self.ui_q.put(*args)
        except Exception as e:
            logger.error(f"Failed to send UI event during {context}: {e}")

    def _emit_ui_log_rx(self, line: str, *, context: str = "rx line") -> None:
        text = str(line or "").strip()
        if not text:
            return
        should_forward = True
        checker = getattr(self, "_should_forward_log_rx_line", None)
        if callable(checker):
            try:
                should_forward = bool(checker(text))
            except Exception as exc:
                _log_suppressed("Failed evaluating UI RX-log forwarding rule", exc)
                should_forward = True
        if not should_forward:
            return
        with self._stream_lock:
            event = ConnectionScopedEvent(
                ("log_rx", text),
                generation=int(self._connection_generation),
                stream_epoch=int(self._stream_token),
                recovery_epoch=int(self._recovery_epoch),
            )
        self._safe_ui_put(event, context=context)

    def _status_log_due(self, now: float) -> bool:
        interval = float(getattr(self, "_status_log_interval", RX_STATUS_LOG_INTERVAL))
        if self._streaming or self._paused:
            interval = max(interval, 1.0)
        else:
            interval = max(interval, 0.5)
        last = float(getattr(self, "_last_status_log_ts", 0.0))
        if (now - last) >= interval:
            self._last_status_log_ts = now
            self._status_log_interval = interval
            return True
        return False

    def _note_ok_log(self, now: float) -> str | None:
        interval = float(getattr(self, "_ok_log_interval", RX_OK_SUMMARY_INTERVAL))
        last = float(getattr(self, "_last_ok_log_ts", 0.0))
        count = int(getattr(self, "_ok_log_count", 0) or 0) + 1
        if last <= 0:
            self._last_ok_log_ts = now
            self._ok_log_interval = interval
            self._ok_log_count = count
            return None
        if (now - last) >= interval:
            self._ok_log_count = 0
            self._last_ok_log_ts = now
            self._ok_log_interval = interval
            return f"OK x{count}"
        self._ok_log_count = count
        return None

    def _flush_ok_log(self, now: float) -> str | None:
        count = int(getattr(self, "_ok_log_count", 0) or 0)
        if count <= 0:
            return None
        interval = float(getattr(self, "_ok_log_interval", RX_OK_SUMMARY_INTERVAL))
        last = float(getattr(self, "_last_ok_log_ts", 0.0))
        if last <= 0:
            self._last_ok_log_ts = now
            self._ok_log_interval = interval
            return None
        if (now - last) >= interval:
            self._ok_log_count = 0
            self._last_ok_log_ts = now
            self._ok_log_interval = interval
            return f"OK x{count}"
        return None

    def suspend_watchdog(self, seconds: float, reason: str | None = None) -> None:
        """Suspend watchdog checks for a duration."""
        try:
            seconds = float(seconds)
        except Exception:
            return
        if seconds <= 0:
            return
        now = time.time()
        until = now + seconds
        current = float(getattr(self, "_watchdog_ignore_until", 0.0))
        if until <= current:
            return
        self._watchdog_ignore_until = until
        self._watchdog_ignore_reason = reason

    def set_homing_watchdog_settings(self, enabled: bool, timeout: float) -> None:
        """Configure homing watchdog suspension behavior."""
        self._homing_watchdog_enabled = bool(enabled)
        try:
            timeout = float(timeout)
        except Exception:
            timeout = 0.0
        if timeout < 0:
            timeout = 0.0
        self._homing_watchdog_timeout = timeout

    def clear_watchdog_ignore(self, reason: str | None = None) -> None:
        """Clear an active watchdog suspension."""
        current = getattr(self, "_watchdog_ignore_reason", None)
        if reason is None or current == reason:
            self._watchdog_ignore_until = 0.0
            self._watchdog_ignore_reason = None
    
    def _handle_alarm(self, message: str, *, generation: int | None = None) -> None:
        """Handle alarm state.
        
        Args:
            message: Alarm message from GRBL
        """
        message = _annotate_alarm(message)
        logger.warning(f"GRBL ALARM: {message}")
        
        # Log to console (safe)
        self._safe_ui_put(("log", f"[ALARM] {message}"), context="alarm logging")
        
        if not self._alarm_active:
            self._alarm_active = True
        
        if generation is None:
            generation = self.connection_generation()
        with self._stream_lock:
            self._invalidate_auto_level_map_locked(
                "GRBL alarm invalidated the installed Auto-Level map."
            )
            recovery_active = bool(self._recovery_state.required)
            execution_uncertain = bool(
                self._streaming
                or self._paused
                or self._execution_pending is not None
                or self._auto_level_lease_blocks_ordinary_locked()
            )
        if recovery_active:
            self._retire_active_recovery_for_alarm(
                "GRBL alarm invalidated the active recovery transaction and all collected evidence.",
                generation=int(generation),
            )
        elif execution_uncertain:
            self._enter_recovery_required(
                "GRBL alarm occurred before controller Idle confirmed execution completion.",
                generation=int(generation),
                attempt_controller_stop=False,
            )
        self._abort_writes.set()
        with self._stream_lock:
            if not self._recovery_state.required:
                self._stream_token += 1
            self._streaming = False
            self._paused = False
            self._execution_pending = None
            stream_epoch = int(self._stream_token)
            recovery_epoch = int(self._recovery_epoch)
        self._reset_stream_buffer()
        
        # Emit buffer state (wrapped to handle failures)
        try:
            self._emit_buffer_fill()
        except Exception as e:
            logger.error(f"Failed to emit buffer state during alarm: {e}")
        
        # Notify UI of alarm state (safe)
        self._safe_ui_put(
            StreamStateEvent(
                "alarm",
                message,
                generation=int(generation),
                stream_epoch=stream_epoch,
                recovery_epoch=recovery_epoch,
            ),
            context="alarm state",
        )
        
        self._clear_outgoing()
        
        # Emit alarm event (safe)
        self._safe_ui_put(
            AlarmEvent(
                message,
                generation=int(generation),
                recovery_epoch=self.recovery_epoch(),
            ),
            context="alarm event",
        )

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================
    
    def _handle_rx_line(
        self,
        line: str,
        *,
        session_generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        # Serial response ownership, recovery latching, and ACK mutation are one
        # admission transaction. Connection replacement also takes this lock.
        with self._write_lock:
            self._handle_rx_line_admitted(
                line,
                session_generation=session_generation,
                serial_port=serial_port,
            )

    def _handle_rx_line_admitted(
        self,
        line: str,
        *,
        session_generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        """Handle received line from GRBL.
        
        Args:
            line: Line received from GRBL
        """
        if session_generation is None:
            session_generation = self.connection_generation()
        if not self._session_is_current(int(session_generation), serial_port):
            logger.warning(
                "Ignoring RX line from stale generation %s (current %s): %s",
                session_generation,
                self.connection_generation(),
                line,
            )
            return
        now = time.time()
        self._last_rx_ts = now
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0

        # Parse status reports
        status_claimed = self._status_report_claimed(line)
        is_status = self._valid_status_envelope(line)

        line_lower = line.lower()
        self._log_rx_line(line)
        if self._settings_dump_active and line.startswith("$") and "=" in line:
            self._settings_dump_seen = True
        self._resolve_modal_query_tracker(line)
        sync_response_consumed = False
        with self._stream_lock:
            transaction = self._recovery_sync_transaction
            if (
                transaction is not None
                and int(transaction.connection_generation) == int(session_generation)
                and int(transaction.recovery_epoch) == int(self._recovery_epoch)
                and (
                    self._recovery_state.phase
                    is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                    or (
                        transaction.purpose == "normal_session"
                        and self._normal_session_state.required
                        and not self._recovery_state.required
                    )
                )
            ):
                if status_claimed and not is_status:
                    self._invalidate_recovery_sync_evidence_locked(
                        clear_position=True
                    )
                self._record_recovery_modal_locked(line)
                self._record_recovery_parameter_locked(line)
                sync_response_consumed = self._finish_recovery_sync_response_locked(
                    line_lower
                )
            elif (
                status_claimed
                and not is_status
                and self._recovery_snapshot is not None
                and (
                    self._recovery_state.required
                    or self._normal_session_state.required
                )
            ):
                self._recovery_snapshot = replace(
                    self._recovery_snapshot,
                    machine_position=None,
                    position_source="unknown",
                )
                self._approved_normal_session_snapshot = None
                self._refresh_recovery_trust_locked()
        if line_lower == "ok":
            ok_summary = self._note_ok_log(now)
            if ok_summary:
                self._emit_ui_log_rx(ok_summary, context="ok summary")
            if getattr(self, "_settings_dump_active", False):
                if getattr(self, "_settings_dump_seen", False):
                    self._settings_dump_active = False
                    self._settings_dump_seen = False
                    self._settings_dump_started_ts = 0.0
                    self.clear_watchdog_ignore("settings_dump")
                    self._safe_ui_put(
                        SettingsDumpDoneEvent(
                            generation=int(session_generation),
                            recovery_epoch=self.recovery_epoch(),
                        ),
                        context="settings dump",
                    )
                    self._emit_ui_log_rx("ok", context="settings ok")
        else:
            ok_summary = self._flush_ok_log(now)
            if ok_summary:
                self._emit_ui_log_rx(ok_summary, context="ok summary")
            if (not is_status) or self._status_log_due(now):
                self._emit_ui_log_rx(line, context="rx line")
        
        # GRBL banner
        if line_lower.startswith("grbl"):
            logger.info(
                "GRBL startup banner received: generation=%s serial_id=%s banner=%s",
                session_generation,
                "none" if serial_port is None else f"0x{id(serial_port):x}",
                line,
            )
            with self._stream_lock:
                if "grblhal" in line_lower:
                    self._extended_wcs_supported = True
                    if self._recovery_snapshot is not None:
                        self._recovery_snapshot = replace(
                            self._recovery_snapshot,
                            extended_wcs_supported=True,
                        )
            attempt = self._reset_attempt
            matching_attempt = bool(
                attempt is not None
                and int(attempt.connection_generation) == int(session_generation)
                and int(attempt.recovery_epoch) == int(self._recovery_epoch)
            )
            if (
                attempt is not None
                and int(attempt.connection_generation) == int(session_generation)
                and not matching_attempt
            ):
                # The retired attempt cannot confirm the current recovery,
                # but the banner is still a fresh controller reset
                # observation. No evidence gathered after that old attempt
                # may survive it.
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                if self.recovery_required():
                    self._observe_fresh_reset_during_recovery(
                        generation=int(session_generation),
                        reason=(
                            "A GRBL startup banner associated with a retired reset attempt "
                            "was observed during recovery; all recovery evidence was retired."
                        ),
                    )
                    return
            if matching_attempt and attempt is not None:
                if self.recovery_required():
                    self._confirm_recovery_reset_banner(attempt)
                    return
                self._cancel_reset_attempt_timer_locked()
                self._reset_attempt = None
                self._abort_writes.clear()
                self._mark_ready(generation=int(session_generation))
                self.begin_normal_session_initialization(
                    generation=int(session_generation)
                )
                return
            if self._streaming or self._paused or self._execution_pending is not None:
                self._enter_recovery_required(
                    "Unexpected GRBL startup banner interrupted the active job; controller state was reset.",
                    generation=int(session_generation),
                    attempt_controller_stop=False,
                    controller_reset_observed=True,
                )
                return
            if self.recovery_required():
                self._observe_fresh_reset_during_recovery(
                    generation=int(session_generation),
                    reason=(
                        "A new GRBL startup banner was observed during recovery; "
                        "all previously gathered recovery evidence was retired."
                    ),
                )
                return
            with self._connection_lock:
                admitted_serial = serial_port if serial_port is not None else self.ser
            expected = bool(
                admitted_serial is not None
                and self._consume_startup_banner_owner_locked(
                    generation=int(session_generation),
                    serial_port=admitted_serial,
                )
            )
            if not expected:
                self._enter_recovery_required(
                    "An unowned GRBL startup banner reset the controller; all machine-state trust was retired.",
                    generation=int(session_generation),
                    attempt_controller_stop=False,
                    controller_reset_observed=True,
                )
                return
            # A generation-owned initial banner on a clean connection is the
            # normal startup handshake. It establishes communication readiness,
            # but does not manufacture homing, offset, TLO, setup, spindle, or
            # coolant trust.
            self._abort_writes.clear()
            self._mark_ready(generation=int(session_generation))
            self.begin_normal_session_initialization(
                generation=int(session_generation)
            )
            return

        if sync_response_consumed:
            return
        
        # Alarm detection
        if line_lower.startswith("alarm:"):
            self._handle_alarm(line, generation=int(session_generation))
            return
        
        if "[msg:" in line_lower and "reset to continue" in line_lower:
            self._handle_alarm(line, generation=int(session_generation))
            return

        # An execution rejection is latched before any queue, RX capacity, or
        # completion ownership is released. TX cannot pass this point because
        # RX owns _write_lock.
        with self._stream_lock:
            execution_error_active = bool(
                self._streaming or self._paused or self._execution_pending is not None
        )
        if line_lower.startswith("error") and execution_error_active:
            with self._stream_lock:
                err_idx: int | None
                err_line: str | None
                rejected_item = self._stream_line_queue[0] if self._stream_line_queue else None
                execution_pending = self._execution_pending
                final_completion_pending = bool(
                    execution_pending is not None and rejected_item is None
                )
                if rejected_item is not None:
                    err_idx = rejected_item.idx
                    err_line = rejected_item.line
                elif execution_pending is not None:
                    err_idx = int(execution_pending.last_acked_index)
                    try:
                        err_line = self._gcode[err_idx] if err_idx >= 0 else None
                    except (IndexError, TypeError):
                        err_line = None
                else:
                    err_idx = None
                    err_line = None
            logger.error("GRBL error: %s", line)
            msg = self._format_stream_error(line, err_idx, err_line)
            recovery = self._enter_recovery_required(
                (
                    f"GRBL reported {line} before job completion was committed; buffered execution is uncertain."
                    if final_completion_pending
                    else f"GRBL rejected a streamed command ({line}); buffered execution is uncertain."
                ),
                generation=int(session_generation),
                uncertain_start_index=err_idx,
                attempt_controller_stop=True,
            )
            self.ui_q.put(
                StreamErrorEvent(
                    msg,
                    err_idx,
                    err_line,
                    self._gcode_name,
                    generation=int(session_generation),
                )
            )
            detail = (
                f"Recovery required: GRBL reported {line} while "
                f"{'final completion was pending' if final_completion_pending else 'streaming'}. "
                "Normal Resume is blocked; reset recovery and machine-state verification are required. "
                f"Hold sent={recovery.hold_sent}; reset sent={recovery.reset_sent}."
            )
            self.ui_q.put(("log", f"[stream error] {detail} {msg}"))
            return
        
        # Command acknowledgment
        if line_lower == "ok" or line_lower.startswith("error"):
            if line_lower.startswith("error") and getattr(self, "_settings_dump_active", False):
                self._settings_dump_active = False
                self._settings_dump_seen = False
                self._settings_dump_started_ts = 0.0
                self.clear_watchdog_ignore("settings_dump")
            ack_index = None
            ack_line_idx = None
            ack_line_text = None
            ack_byte_offset = None
            stream_file_size_bytes = 0
            err_idx = None
            err_line = None
            err_source = None
            manual_tracker = None
            stream_error_active = False
            stale_ack_item = False
            ack_stream_epoch = None
            ack_recovery_epoch = None

            with self._stream_lock:
                if self._stream_line_queue:
                    queued_item = self._stream_line_queue[0]
                    legacy_unstamped = bool(
                        int(getattr(queued_item, "connection_generation", 0)) == 0
                        and int(getattr(queued_item, "stream_epoch", 0)) == 0
                        and int(getattr(queued_item, "recovery_epoch", 0)) == 0
                    )
                    owned = bool(
                        legacy_unstamped
                        or (
                            int(queued_item.connection_generation) == int(session_generation)
                            and int(queued_item.stream_epoch) == int(self._stream_token)
                            and int(queued_item.recovery_epoch) == int(self._recovery_epoch)
                        )
                    )
                    if not owned:
                        stale_ack_item = True
                    else:
                        queued_item = self._stream_line_queue.popleft()
                else:
                    queued_item = None
                if queued_item is not None and not stale_ack_item:
                    ack_stream_epoch = int(getattr(queued_item, "stream_epoch", 0) or 0)
                    ack_recovery_epoch = int(getattr(queued_item, "recovery_epoch", 0) or 0)
                    if ack_stream_epoch == 0 and ack_recovery_epoch == 0:
                        ack_stream_epoch = int(self._stream_token)
                        ack_recovery_epoch = int(self._recovery_epoch)
                    self._stream_buf_used = max(0, self._stream_buf_used - queued_item.line_len)
                    queued_ts = float(getattr(queued_item, "queued_ts", 0.0) or 0.0)
                    if queued_ts > 0:
                        self._record_ack_latency(max(0.0, (now - queued_ts) * 1000.0))
                    if line_lower.startswith("error"):
                        err_source = getattr(queued_item, "manual_source", None)
                    manual_tracker = getattr(queued_item, "manual_tracker", None)

                    if line_lower.startswith("error"):
                        err_idx = queued_item.idx
                        err_line = queued_item.line
                        stream_error_active = bool(self._streaming or self._paused)
                    elif queued_item.is_gcode and self._streaming:
                        self._ack_index += 1
                        ack_index = self._ack_index
                        ack_line_idx = queued_item.idx
                        ack_line_text = queued_item.line
                        queued_end_offset = getattr(queued_item, "file_end_offset", None)
                        if queued_end_offset is not None:
                            try:
                                queued_end = max(0, int(queued_end_offset))
                            except Exception:
                                queued_end = 0
                            if queued_end > int(getattr(self, "_ack_byte_offset", 0) or 0):
                                self._ack_byte_offset = queued_end
                            ack_byte_offset = int(getattr(self, "_ack_byte_offset", 0) or 0)
                            stream_file_size_bytes = int(
                                getattr(self, "_stream_file_size_bytes", 0) or 0
                            )

            if stale_ack_item:
                if self._streaming or self._paused or self._execution_pending is not None:
                    self._enter_recovery_required(
                        "An acknowledgment targeted a retired stream or recovery epoch.",
                        generation=int(session_generation),
                        attempt_controller_stop=True,
                    )
                return
            
            self._emit_buffer_fill()
            if manual_tracker is not None:
                if line_lower.startswith("error"):
                    self._resolve_manual_tracker(
                        manual_tracker,
                        success=False,
                        error=line,
                    )
                else:
                    self._resolve_manual_tracker(manual_tracker, success=True)
            wake_evt = getattr(self, "_tx_activity_evt", None)
            if wake_evt is not None:
                try:
                    wake_evt.set()
                except Exception as exc:
                    _log_suppressed("Failed signaling TX activity after ACK", exc)
            
            # Report progress
            if ack_index is not None:
                self.ui_q.put(
                    GcodeAckedEvent(
                        int(ack_index),
                        generation=int(session_generation),
                        stream_epoch=ack_stream_epoch,
                        recovery_epoch=ack_recovery_epoch,
                    )
                )
                self.ui_q.put(
                    ProgressEvent(
                        int(ack_index) + 1,
                        len(self._gcode),
                        generation=int(session_generation),
                        stream_epoch=ack_stream_epoch,
                        recovery_epoch=ack_recovery_epoch,
                    )
                )
                if ack_line_idx is not None:
                    try:
                        ack_idx_int = int(ack_line_idx)
                    except Exception:
                        ack_idx_int = None
                    if ack_idx_int is not None:
                        ack_text = str(ack_line_text or "")
                        self._live_current_acked = (ack_idx_int, ack_text)
                        self._live_acked_ring.append((ack_idx_int, ack_text))
                if ack_byte_offset is not None and stream_file_size_bytes > 0:
                    self.ui_q.put(
                        ProgressBytesEvent(
                            min(int(ack_byte_offset), int(stream_file_size_bytes)),
                            int(stream_file_size_bytes),
                            generation=int(session_generation),
                            stream_epoch=ack_stream_epoch,
                            recovery_epoch=ack_recovery_epoch,
                        )
                    )

            if line_lower == "ok":
                if ack_line_idx is not None:
                    self._maybe_pause_after_ack(ack_line_idx)

            # Treat active-stream errors as terminal error-holds so a rejected
            # command cannot be silently skipped by normal Resume.
            if line_lower.startswith("error"):
                logger.error(f"GRBL error: {line}")
                if stream_error_active:
                    if err_idx == self._pause_after_idx:
                        self._pause_after_idx = None
                        self._pause_after_reason = None
                    msg = self._format_stream_error(line, err_idx, err_line)
                    recovery = self._enter_recovery_required(
                        f"GRBL rejected a streamed command ({line}); buffered execution is uncertain.",
                        generation=int(session_generation),
                        uncertain_start_index=err_idx,
                        attempt_controller_stop=True,
                    )
                    self.ui_q.put(
                        StreamErrorEvent(
                            msg,
                            err_idx,
                            err_line,
                            self._gcode_name,
                            generation=int(session_generation),
                        )
                    )
                    detail = (
                        f"Recovery required: GRBL reported {line} while streaming. "
                        "Normal Resume is blocked; reset recovery and machine-state verification are required. "
                        f"Hold sent={recovery.hold_sent}; reset sent={recovery.reset_sent}."
                    )
                    self.ui_q.put(("log", f"[stream error] {detail} {msg}"))
                else:
                    source = err_source if err_source else self._last_manual_source
                    self.ui_q.put(("manual_error", line, source))
        
        # Status report
        if is_status:
            # Communication readiness is owned by the generation's startup
            # banner. Status telemetry received before that banner is useful,
            # but cannot consume startup ownership or make commands ready.
            startup_owner = self._startup_banner_owner
            if (
                startup_owner is None
                or startup_owner.phase is StartupBannerPhase.CONSUMED
            ):
                self._mark_ready(generation=int(session_generation))
            parts = line.strip("<>").split("|")
            state = parts[0] if parts else ""
            self._last_status_state_token = str(state or "")
            self._status_observation_seq += 1
            manual_motion_active = self._manual_motion_status_active(
                now=now,
                state_token=state,
            )
            manual_motion_active = self._update_manual_motion_session_state(
                manual_motion_active,
                now=now,
                source="rx_status",
            )
            if manual_motion_active:
                self._record_manual_motion_rx_interval(now)
            else:
                self._manual_motion_status_grace_until_ts = 0.0
            state_lower = str(state or "").strip().lower()
            self._observe_controller_suspension_status(
                state,
                generation=int(session_generation),
                serial_port=serial_port,
            )
            completed_pending = None
            normal_session_changed = False
            with self._stream_lock:
                if (
                    (
                        self._recovery_state.phase
                        is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED
                        or (
                            self._normal_session_state.required
                            and not self._recovery_state.required
                        )
                    )
                    and self._recovery_snapshot is not None
                ):
                    coordinates_valid, mpos, wpos, wco = (
                        self._parse_trusted_status_coordinates(parts[1:])
                    )
                    if not coordinates_valid:
                        normal_homing_invalidated = bool(
                            self._normal_session_state.homing_started
                        )
                        self._invalidate_status_coordinate_evidence_locked()
                        normal_session_changed = bool(
                            normal_session_changed or normal_homing_invalidated
                        )
                    if (
                        coordinates_valid
                        and mpos is None
                        and wpos is not None
                        and wco is not None
                    ):
                        derived_mpos = (
                            float(wpos[0]) + float(wco[0]),
                            float(wpos[1]) + float(wco[1]),
                            float(wpos[2]) + float(wco[2]),
                        )
                        if all(math.isfinite(value) for value in derived_mpos):
                            mpos = derived_mpos
                        else:
                            self._recovery_snapshot = replace(
                                self._recovery_snapshot,
                                machine_position=None,
                                position_source="unknown",
                            )
                    if (
                        coordinates_valid
                        and mpos is not None
                        and self._recovery_snapshot.position_source == "unknown"
                    ):
                        first_position = self._recovery_snapshot.machine_position is None
                        self._recovery_snapshot = replace(
                            self._recovery_snapshot,
                            machine_position=mpos,
                        )
                        if first_position:
                            logger.info(
                                "Current-session position evidence received: generation=%s "
                                "recovery_epoch=%s mpos=%s provenance=unestablished",
                                session_generation,
                                self._recovery_epoch,
                                mpos,
                            )
                    if self._recovery_state.homing_started and state_lower.startswith("home"):
                        self._recovery_state = replace(
                            self._recovery_state,
                            homing_seen=True,
                        )
                    elif (
                        self._recovery_state.homing_started
                        and state_lower.startswith("idle")
                        and self._recovery_state.homing_seen
                        and self._recovery_snapshot.machine_position is not None
                    ):
                        self._recovery_snapshot = replace(
                            self._recovery_snapshot,
                            position_source="homed",
                        )
                        self.clear_watchdog_ignore("homing")
                    if (
                        self._normal_session_state.homing_started
                        and state_lower.startswith("home")
                    ):
                        self._normal_session_state = replace(
                            self._normal_session_state,
                            homing_seen=True,
                        )
                        normal_session_changed = True
                    elif (
                        self._normal_session_state.homing_started
                        and state_lower.startswith("idle")
                        and self._normal_session_state.homing_seen
                        and self._recovery_snapshot.machine_position is not None
                    ):
                        self._recovery_snapshot = replace(
                            self._recovery_snapshot,
                            position_source="homed",
                        )
                        self.clear_watchdog_ignore("homing")
                        self._normal_session_ready_locked()
                        normal_session_changed = True
                    self._refresh_recovery_trust_locked()
            if normal_session_changed:
                self._emit_normal_session_state()
            if state_lower.startswith("idle"):
                with self._stream_lock:
                    pending = self._execution_pending
                    if (
                        pending is not None
                        and int(pending.connection_generation) == int(session_generation)
                        and int(pending.stream_epoch) == int(self._stream_token)
                        and int(pending.recovery_epoch) == int(self._recovery_epoch)
                        and not self._recovery_state.required
                        and not self._suspension_blocks_tx_locked()
                    ):
                        if not pending.completion_published:
                            completed_pending = pending
                            self._execution_pending = replace(
                                pending,
                                completion_published=True,
                            )
            if completed_pending is not None:
                if completed_pending.file_size_bytes > 0:
                    self.ui_q.put(
                        ProgressBytesEvent(
                            completed_pending.file_size_bytes,
                            completed_pending.file_size_bytes,
                            generation=completed_pending.connection_generation,
                            stream_epoch=completed_pending.stream_epoch,
                            recovery_epoch=completed_pending.recovery_epoch,
                        )
                    )
                self.ui_q.put(
                    StreamCompletionEofEvent(
                        completed_pending.verified_eof,
                        completed_pending.total_lines,
                        completed_pending.total_lines_known,
                        completed_pending.last_acked_index,
                        completed_pending.send_index,
                        generation=completed_pending.connection_generation,
                        stream_epoch=completed_pending.stream_epoch,
                        recovery_epoch=completed_pending.recovery_epoch,
                    )
                )
                self.ui_q.put(
                    StreamStateEvent(
                        "done",
                        None,
                        generation=completed_pending.connection_generation,
                        stream_epoch=completed_pending.stream_epoch,
                        recovery_epoch=completed_pending.recovery_epoch,
                    )
                )
            if not state_lower.startswith(("jog", "hold")):
                self._jog_cancel_inflight = False
            
            # Check for alarm in status
            if state.lower().startswith("alarm"):
                if not self._alarm_active:
                    self._handle_alarm(state)
            elif self._alarm_active:
                self._alarm_active = False
                # Recovery owns its write gate. A status transition must never
                # release ordinary writes quarantined by a recovery epoch.
                if not self.recovery_required():
                    self._abort_writes.clear()
            
            # Parse buffer info
            for part in parts:
                if part.startswith("Bf:"):
                    try:
                        _, rx_free_text = part[3:].split(",", 1)
                        rx_free = int(rx_free_text.strip())
                        if rx_free < 0:
                            rx_free = 0
                        with self._stream_lock:
                            busy = (
                                self._stream_buf_used > 0
                                or self._stream_line_queue
                                or self._stream_pending_item is not None
                                or self._manual_pending_item is not None
                                or self._resume_preamble
                            )
                            if busy:
                                continue
                            capacity = rx_free + self._stream_buf_used
                            if capacity < RX_BUFFER_SIZE:
                                capacity = RX_BUFFER_SIZE
                            self._rx_window = capacity
                        self._emit_buffer_fill()
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse Bf field: {e}")
            
            self.ui_q.put(
                StatusEvent(
                    line,
                    generation=int(session_generation),
                    recovery_epoch=self.recovery_epoch(),
                )
            )
    
    def _status_loop(
        self,
        stop_evt: threading.Event,
        generation: int | None = None,
        serial_port: object | None = None,
    ) -> None:
        """Status polling thread - periodically requests status.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        if generation is None:
            generation = self.connection_generation()
        if serial_port is None:
            serial_port = getattr(self, "ser", None)
        logger.debug("Status thread started for generation %s", generation)
        
        try:
            while not stop_evt.is_set():
                if not self._session_is_current(int(generation), serial_port):
                    break
                # Snapshot connection state/time once per loop to avoid races
                # between repeated is_connected() checks in the same iteration.
                now = time.time()
                connected = bool(self.is_connected())
                if connected:
                    connect_started_ts = float(getattr(self, "_connect_started_ts", 0.0))
                    startup_owner = self.startup_banner_ownership()
                    if (
                        connect_started_ts
                        and (not self._ready)
                        and startup_owner is not None
                        and startup_owner.phase is StartupBannerPhase.PENDING
                        and int(startup_owner.connection_generation) == int(generation)
                        and startup_owner.serial_port is serial_port
                        and now >= float(startup_owner.deadline)
                    ):
                        if self._timeout_startup_banner_session(
                            generation=int(generation),
                            serial_port=serial_port,
                            observed_owner=startup_owner,
                        ):
                            stop_evt.set()
                            break
                    watchdog_ignore_until = float(getattr(self, "_watchdog_ignore_until", 0.0))
                    watchdog_ignored = watchdog_ignore_until and (now < watchdog_ignore_until)
                    settings_dump_active = bool(
                        getattr(self, "_settings_dump_active", False)
                    )
                    if settings_dump_active:
                        started_ts = float(
                            getattr(self, "_settings_dump_started_ts", 0.0) or 0.0
                        )
                        max_ignore_s = float(
                            getattr(
                                self,
                                "_settings_dump_watchdog_max_ignore_s",
                                getattr(self, "_settings_dump_watchdog_timeout", 0.0),
                            )
                            or 0.0
                        )
                        elapsed = (now - started_ts) if started_ts > 0.0 else 0.0
                        if max_ignore_s <= 0.0 or elapsed <= max_ignore_s:
                            watchdog_ignored = True
                        else:
                            self._settings_dump_active = False
                            self._settings_dump_seen = False
                            self._settings_dump_started_ts = 0.0
                            self.clear_watchdog_ignore("settings_dump")
                            try:
                                self.ui_q.put(
                                    (
                                        "log",
                                        "[watchdog] Settings dump grace expired; watchdog re-enabled.",
                                    )
                                )
                            except Exception as exc:
                                _log_suppressed(
                                    "Failed queueing settings-dump watchdog-expiry log",
                                    exc,
                                )
                    idle = now - self._last_rx_ts
                    watchdog_enforced = self._watchdog_enforced(now)
                    if self._alarm_active:
                        if (
                            idle >= WATCHDOG_ALARM_DISCONNECT_TIMEOUT
                            and (self._streaming or self._ready)
                            and watchdog_enforced
                            and not watchdog_ignored
                        ):
                            self._signal_disconnect(
                                "[status/watchdog-alarm] Connection watchdog timeout (alarm)",
                                generation=int(generation),
                                serial_port=serial_port,
                            )
                            stop_evt.set()
                            break
                    else:
                        if (
                            idle >= WATCHDOG_RX_TIMEOUT
                            and (self._streaming or self._paused or self._ready)
                            and watchdog_enforced
                            and not self._watchdog_paused
                            and not watchdog_ignored
                        ):
                            if self._streaming and not self._paused:
                                self._pause_stream(reason="connection watchdog")
                            self._watchdog_paused = True
                            self._watchdog_trip_ts = now
                            try:
                                self.ui_q.put(("log", "[watchdog] No RX from GRBL; pausing stream."))
                            except Exception as exc:
                                _log_suppressed("Failed queueing watchdog pause log message", exc)
                        if (
                            idle >= WATCHDOG_DISCONNECT_TIMEOUT
                            and (self._streaming or self._ready)
                            and watchdog_enforced
                            and not watchdog_ignored
                        ):
                            self._signal_disconnect(
                                "[status/watchdog] Connection watchdog timeout",
                                generation=int(generation),
                                serial_port=serial_port,
                            )
                            stop_evt.set()
                            break
                if connected:
                    try:
                        manual_motion_active = self._manual_motion_status_active(now=now)
                        manual_motion_active = self._update_manual_motion_session_state(
                            manual_motion_active,
                            now=now,
                            source="status_loop_query",
                        )
                        if manual_motion_active:
                            self._record_manual_motion_query_interval(now)
                        self.send_realtime(
                            RT_STATUS, expected_generation=int(generation)
                        )
                        self._status_query_failures = 0
                    except Exception as e:
                        logger.error(f"Status query error: {e}")
                        self.ui_q.put(("log", f"[status query error] {e}"))
                        self._emit_exception("Status query error", e)
                        ready = bool(getattr(self, "_ready", False))
                        if not ready:
                            # During startup handshake GRBL can briefly reject writes while the
                            # controller is still resetting. Let the startup timeout decide
                            # disconnects instead of flapping the UI connection state.
                            self._status_query_failures = 0
                            if stop_evt.wait(self._status_query_backoff_base):
                                break
                            continue
                        self._status_query_failures += 1
                        try:
                            self.ui_q.put((
                                "log",
                                f"[status] Query failed ({self._status_query_failures}/{self._status_query_failure_limit})",
                            ))
                        except Exception as exc:
                            _log_suppressed("Failed queueing status-query failure log message", exc)
                        if self._status_query_failures >= self._status_query_failure_limit:
                            self._signal_disconnect(
                                f"[status/query] Status query error: {e}",
                                generation=int(generation),
                                serial_port=serial_port,
                            )
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
                wait_now = time.time()
                manual_motion_active = self._manual_motion_status_active(now=wait_now)
                manual_motion_active = self._update_manual_motion_session_state(
                    manual_motion_active,
                    now=wait_now,
                    source="status_loop_wait",
                )
                if manual_motion_active:
                    interval = min(float(interval), float(_MANUAL_MOTION_STATUS_INTERVAL_S))

                # Wait for interval or stop signal
                if self._wait_status_interval(stop_evt, interval):
                    break
        
        except Exception as e:
            logger.error(f"Status thread error: {e}", exc_info=True)
            self._emit_exception("Status thread error", e)
            self._signal_disconnect(
                f"[status/thread] Status thread error: {e}",
                generation=int(generation),
                serial_port=serial_port,
            )
            stop_evt.set()
        
        finally:
            logger.debug("Status thread stopped")
    @staticmethod
    def _recovery_xyz(text: str) -> tuple[float, float, float] | None:
        return cast(
            tuple[float, float, float] | None,
            parse_exact_status_xyz(text),
        )

    @classmethod
    def _parse_trusted_status_coordinates(
        cls,
        fields: list[str],
    ) -> tuple[
        bool,
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
    ]:
        """Parse one XYZ status frame without last-value-wins ambiguity.

        Simple Sender supports exactly three machine axes. Duplicate recognized
        fields, unsupported axis counts, non-finite values, and inconsistent
        simultaneous MPos/WPos/WCO evidence invalidate the whole frame.
        """
        evidence = parse_status_coordinate_fields(fields)
        return evidence.valid, evidence.mpos, evidence.wpos, evidence.wco

    @staticmethod
    def _status_report_claimed(line: str) -> bool:
        return str(line or "").lstrip().startswith("<")

    @staticmethod
    def _valid_status_envelope(line: str) -> bool:
        text = str(line or "")
        return bool(
            text == text.strip()
            and text.startswith("<")
            and text.endswith(">")
            and "<" not in text[1:]
            and ">" not in text[:-1]
        )

    def _invalidate_recovery_sync_evidence_locked(
        self,
        *,
        clear_position: bool = False,
    ) -> None:
        """Retire every value owned by the current malformed sync response."""
        transaction = self._recovery_sync_transaction
        snapshot = self._recovery_snapshot
        if transaction is None or snapshot is None:
            return
        self._recovery_sync_transaction = replace(transaction, invalid=True)
        self._approved_recovery_snapshot = None
        self._approved_recovery_completed_state = None
        self._approved_recovery_finalization_identity = None
        self._approved_normal_session_snapshot = None
        self._recovery_snapshot = RecoveryStateSnapshot(
            connection_generation=int(snapshot.connection_generation),
            recovery_epoch=int(snapshot.recovery_epoch),
            sync_transaction_id=transaction.transaction_id,
            gc_request_id=transaction.gc_request_id,
            parameters_request_id=transaction.parameters_request_id,
            machine_position=(None if clear_position else snapshot.machine_position),
            position_source=(
                "unknown" if clear_position else snapshot.position_source
            ),
            extended_wcs_supported=bool(snapshot.extended_wcs_supported),
        )
        self._refresh_recovery_trust_locked()

    def _invalidate_status_coordinate_evidence_locked(self) -> None:
        """Retire all position evidence owned by a malformed status frame."""
        snapshot = self._recovery_snapshot
        if snapshot is None:
            return
        self._approved_recovery_snapshot = None
        self._approved_recovery_completed_state = None
        self._approved_recovery_finalization_identity = None
        self._approved_normal_session_snapshot = None
        self._recovery_snapshot = replace(
            snapshot,
            machine_position=None,
            position_source="unknown",
        )
        if self._recovery_state.homing_started:
            self._recovery_state = replace(
                self._recovery_state,
                homing_started=False,
                homing_seen=False,
            )
        if self._normal_session_state.homing_started:
            self._normal_session_state = replace(
                self._normal_session_state,
                homing_started=False,
                homing_seen=False,
                phase=NormalSessionPhase.POSITION_REQUIRED,
                reason=(
                    "Malformed status coordinates retired the normal-session "
                    "homing evidence; home again or verify position explicitly."
                ),
            )
        self._refresh_recovery_trust_locked()

    def _derive_recovery_wco_locked(self) -> None:
        snapshot = self._recovery_snapshot
        if snapshot is None:
            return
        active = snapshot.offset_for(snapshot.active_wcs)
        if active is None or snapshot.g92 is None or snapshot.tlo is None:
            return
        derived = (
            float(active[0]) + float(snapshot.g92[0]),
            float(active[1]) + float(snapshot.g92[1]),
            float(active[2]) + float(snapshot.g92[2]) + float(snapshot.tlo),
        )
        if not all(math.isfinite(value) for value in derived):
            self._invalidate_recovery_sync_evidence_locked()
            return
        self._recovery_snapshot = replace(snapshot, work_coordinate_offset=derived)

    def _record_recovery_modal_locked(self, line: str) -> bool:
        transaction = self._recovery_sync_transaction
        snapshot = self._recovery_snapshot
        if (
            transaction is None
            or snapshot is None
            or transaction.phase != "gc"
        ):
            return False
        if not re.match(r"^\s*\[\s*GC\s*:", line, flags=re.IGNORECASE):
            return False
        if transaction.invalid:
            return True
        if (
            line != line.strip()
            or re.fullmatch(r"\[GC:[^\[\]\r\n]*\]", line, flags=re.IGNORECASE)
            is None
        ):
            self._invalidate_recovery_sync_evidence_locked()
            return True
        payload = line.split(":", 1)[1][:-1]
        if ":" in payload or "," in payload:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        modal_units = snapshot.modal_units
        distance_mode = snapshot.distance_mode
        plane = snapshot.plane
        feed_mode = snapshot.feed_mode
        arc_distance_mode = snapshot.arc_distance_mode
        active_wcs = snapshot.active_wcs
        motion_mode = snapshot.motion_mode
        spindle_mode = snapshot.spindle_mode
        coolant_mode = snapshot.coolant_mode
        coolant_modes = list(snapshot.coolant_modes)
        feed_rate = snapshot.feed_rate
        spindle_speed = snapshot.spindle_speed
        tool_number = snapshot.tool_number
        selected_tool_number = snapshot.selected_tool_number
        current_tool_number = snapshot.current_tool_number
        tlo_mode = snapshot.tlo_mode
        if transaction.gc_payload_seen:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        seen: dict[str, object] = {}
        invalid = False

        def assign(group: str, value: object) -> bool:
            nonlocal invalid
            previous = seen.get(group)
            if previous is not None and previous != value:
                invalid = True
                return False
            seen[group] = value
            return True

        for token in payload.split():
            upper = token.upper()
            if upper in {"G20", "G21"}:
                if assign("units", upper):
                    modal_units = upper
            elif upper in {"G90", "G91"}:
                if assign("distance", upper):
                    distance_mode = upper
            elif upper in {"G17", "G18", "G19"}:
                if assign("plane", upper):
                    plane = upper
            elif upper in {"G93", "G94"}:
                if assign("feed_mode", upper):
                    feed_mode = upper
            elif upper in {"G90.1", "G91.1"}:
                if assign("arc_distance", upper):
                    arc_distance_mode = upper
            elif upper in {"G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3"}:
                if assign("wcs", upper):
                    active_wcs = upper
            elif upper in {"G0", "G1", "G2", "G3", "G38.2", "G38.3", "G38.4", "G38.5", "G80"}:
                if assign("motion", upper):
                    motion_mode = upper
            elif upper in {"M3", "M4", "M5"}:
                if assign("spindle", upper):
                    spindle_mode = upper
            elif upper == "M9":
                if any(value in {"M7", "M8"} for value in coolant_modes):
                    invalid = True
                if assign("coolant_off", upper):
                    coolant_mode = upper
                    coolant_modes = ["M9"]
            elif upper in {"M7", "M8"}:
                if coolant_modes == ["M9"] or "coolant_off" in seen:
                    invalid = True
                    continue
                if upper not in coolant_modes:
                    coolant_modes.append(upper)
                coolant_mode = "+".join(coolant_modes)
            elif upper in {"G43", "G43.1", "G49"}:
                if assign("tlo", upper):
                    tlo_mode = upper
            elif upper.startswith("F"):
                try:
                    parsed_feed = float(upper[1:])
                except ValueError:
                    invalid = True
                else:
                    if not math.isfinite(parsed_feed):
                        invalid = True
                    elif assign("feed", parsed_feed):
                        feed_rate = parsed_feed
            elif upper.startswith("S"):
                try:
                    parsed_speed = float(upper[1:])
                except ValueError:
                    invalid = True
                else:
                    if not math.isfinite(parsed_speed):
                        invalid = True
                    elif assign("speed", parsed_speed):
                        spindle_speed = parsed_speed
            elif upper.startswith("T"):
                if not upper[1:].isdigit():
                    invalid = True
                else:
                    parsed_tool = int(upper[1:])
                    if assign("tool", parsed_tool):
                        tool_number = parsed_tool
                        selected_tool_number = parsed_tool
                        current_tool_number = parsed_tool
        arc_provenance = None
        if arc_distance_mode is not None:
            arc_provenance = "controller_report"
        else:
            # Supported GRBL 1.1-family controllers use incremental IJK arc
            # offsets when the mode is omitted from $G. Bind that inference to
            # this exact synchronization request instead of treating omission
            # as an implicitly complete report.
            arc_distance_mode = "G91.1"
            arc_provenance = "supported_grbl_fixed_incremental_arc"
        if invalid:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        self._recovery_snapshot = replace(
            snapshot,
            modal_units=modal_units,
            distance_mode=distance_mode,
            plane=plane,
            feed_mode=feed_mode,
            arc_distance_mode=arc_distance_mode,
            arc_distance_mode_provenance=arc_provenance,
            arc_distance_mode_transaction_id=transaction.transaction_id,
            arc_distance_mode_request_id=transaction.gc_request_id,
            active_wcs=active_wcs,
            motion_mode=motion_mode,
            spindle_mode=spindle_mode,
            coolant_mode=coolant_mode,
            coolant_modes=tuple(coolant_modes),
            feed_rate=feed_rate,
            spindle_speed=spindle_speed,
            tool_number=tool_number,
            selected_tool_number=selected_tool_number,
            current_tool_number=current_tool_number,
            # GRBL 1.1 reports one T word in $G. Preserve both semantic slots,
            # while recording that this dialect did not report them distinctly.
            tool_state_distinct=False,
            tool_state_transaction_id=(
                transaction.transaction_id if tool_number is not None else None
            ),
            tool_state_request_id=(
                transaction.gc_request_id if tool_number is not None else None
            ),
            tlo_mode=tlo_mode,
            tlo_mode_transaction_id=(
                transaction.transaction_id if tlo_mode is not None else None
            ),
            tlo_mode_request_id=(
                transaction.gc_request_id if tlo_mode is not None else None
            ),
        )
        self._recovery_sync_transaction = replace(
            transaction,
            gc_payload_seen=True,
            invalid=bool(transaction.invalid),
        )
        return True

    def _record_recovery_parameter_locked(self, line: str) -> bool:
        transaction = self._recovery_sync_transaction
        snapshot = self._recovery_snapshot
        if transaction is None or snapshot is None or transaction.phase != "parameters":
            return False
        claim = re.match(
            r"^\s*\[\s*([A-Z0-9.]+)\s*:",
            line,
            flags=re.IGNORECASE,
        )
        if claim is None:
            return False
        label = claim.group(1).upper()
        recognized = {
            "G54",
            "G55",
            "G56",
            "G57",
            "G58",
            "G59",
            "G59.1",
            "G59.2",
            "G59.3",
            "G28",
            "G30",
            "G92",
            "TLO",
            "PRB",
        }
        if label not in recognized:
            return False
        if transaction.invalid:
            return True
        envelope = re.fullmatch(
            r"\[([A-Z0-9.]+):([^\[\]\r\n]*)\]",
            line,
            flags=re.IGNORECASE,
        )
        if line != line.strip() or envelope is None:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        envelope_label = envelope.group(1).upper()
        raw = envelope.group(2)
        if envelope_label != label:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        if label in transaction.parameter_reports:
            self._invalidate_recovery_sync_evidence_locked()
            return True
        reports = tuple(dict.fromkeys((*transaction.parameter_reports, label)))
        changed = False
        if label in {"G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3"}:
            xyz = self._recovery_xyz(raw)
            if xyz is not None:
                if label in {"G59.1", "G59.2", "G59.3"}:
                    self._extended_wcs_supported = True
                    snapshot = replace(snapshot, extended_wcs_supported=True)
                offsets = dict(snapshot.wcs_offsets)
                offsets[label] = xyz
                self._recovery_snapshot = replace(
                    snapshot,
                    wcs_offsets=tuple(sorted(offsets.items())),
                )
                changed = True
            else:
                self._invalidate_recovery_sync_evidence_locked()
                return True
        elif label == "G92":
            xyz = self._recovery_xyz(raw)
            if xyz is not None:
                self._recovery_snapshot = replace(snapshot, g92=xyz)
                changed = True
            else:
                self._invalidate_recovery_sync_evidence_locked()
                return True
        elif label == "TLO":
            try:
                raw_tlo = raw.strip()
                if not raw_tlo or "," in raw_tlo:
                    raise ValueError("TLO report must contain exactly one scalar")
                tlo = float(raw_tlo)
            except ValueError:
                tlo = None
            if tlo is not None and math.isfinite(tlo):
                self._recovery_snapshot = replace(
                    snapshot,
                    tlo=tlo,
                    tlo_value_transaction_id=transaction.transaction_id,
                    tlo_value_request_id=transaction.parameters_request_id,
                )
                changed = True
            else:
                self._invalidate_recovery_sync_evidence_locked()
                return True
        elif label in {"G28", "G30"}:
            if self._recovery_xyz(raw) is None:
                self._invalidate_recovery_sync_evidence_locked()
                return True
        elif label == "PRB":
            probe_parts = raw.rsplit(":", 1)
            if (
                len(probe_parts) != 2
                or self._recovery_xyz(probe_parts[0]) is None
                or probe_parts[1].strip() not in {"0", "1"}
            ):
                self._invalidate_recovery_sync_evidence_locked()
                return True
        if changed:
            self._derive_recovery_wco_locked()
        current = self._recovery_sync_transaction
        if current is not None and not current.invalid:
            self._recovery_sync_transaction = replace(
                current,
                parameter_reports=reports,
            )
        return True

    def _finish_recovery_sync_response_locked(self, line_lower: str) -> bool:
        transaction = self._recovery_sync_transaction
        snapshot = self._recovery_snapshot
        if transaction is None or snapshot is None:
            return False
        normal_session = transaction.purpose == "normal_session"
        if line_lower.startswith("error"):
            self._invalidate_recovery_sync_evidence_locked()
            snapshot = self._recovery_snapshot
            assert snapshot is not None
            self._recovery_sync_transaction = None
            self._recovery_sync_generation = None
            self._recovery_sync_epoch = None
            self._recovery_snapshot = replace(
                snapshot,
                gc_complete=False,
                parameters_complete=False,
            )
            self._refresh_recovery_trust_locked()
            if normal_session:
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.FAILED,
                    reason=(
                        "Controller rejected normal-session state synchronization; "
                        "job admission remains blocked."
                    ),
                )
                self._emit_normal_session_state()
            self.ui_q.put(
                (
                    "log",
                    "[session] Controller state synchronization was rejected."
                    if normal_session
                    else "[recovery] Controller state synchronization was rejected.",
                )
            )
            return True
        if line_lower != "ok":
            return False
        if transaction.phase == "gc":
            if (
                transaction.invalid
                or not transaction.gc_payload_seen
                or not snapshot.gc_values_complete()
            ):
                self._invalidate_recovery_sync_evidence_locked()
                self._recovery_sync_transaction = None
                self._recovery_sync_generation = None
                self._recovery_sync_epoch = None
                if normal_session:
                    self._normal_session_state = replace(
                        self._normal_session_state,
                        phase=NormalSessionPhase.FAILED,
                        reason="Incomplete $G response; job admission remains blocked.",
                    )
                    self._emit_normal_session_state()
                self.ui_q.put(
                    (
                        "log",
                        "[session] Incomplete $G response; state remains untrusted."
                        if normal_session
                        else "[recovery] Incomplete $G response; state remains untrusted.",
                    )
                )
                return True
            self._recovery_snapshot = replace(snapshot, gc_complete=True)
            self._recovery_sync_transaction = replace(transaction, phase="parameters")
            self._refresh_recovery_trust_locked()
            logger.info(
                "Normal-session $G completed: generation=%s recovery_epoch=%s "
                "transaction_id=%s request_id=%s",
                transaction.connection_generation,
                transaction.recovery_epoch,
                transaction.transaction_id,
                transaction.gc_request_id,
            )
            return True
        self._derive_recovery_wco_locked()
        snapshot = self._recovery_snapshot
        assert snapshot is not None
        if (
            snapshot.tlo_mode is None
            and snapshot.tlo is not None
            and abs(float(snapshot.tlo)) <= 1e-9
            and snapshot.sync_transaction_id == transaction.transaction_id
        ):
            # GRBL 1.1 omits inactive G49 from some $G reports. A complete $G
            # response plus an exact zero TLO report establishes the inactive mode.
            snapshot = replace(
                snapshot,
                tlo_mode="G49",
                tlo_mode_transaction_id=transaction.transaction_id,
                tlo_mode_request_id=transaction.gc_request_id,
            )
            self._recovery_snapshot = snapshot
        complete = bool(
            not transaction.invalid and snapshot.parameter_values_complete()
        )
        snapshot = replace(snapshot, parameters_complete=complete)
        if (
            normal_session
            and complete
            and snapshot.spindle_mode == "M5"
            and snapshot.coolant_modes == ("M9",)
        ):
            # The generation-owned startup reset plus exact current-session
            # M5/M9 controller evidence establishes a conservative controller
            # output state for ordinary initialization. Physical accessory
            # verification remains stricter during execution recovery.
            snapshot = replace(
                snapshot,
                spindle_verified=True,
                spindle_verified_mode="M5",
                coolant_verified=True,
                coolant_verified_modes=("M9",),
                spindle_verification_transaction_id=snapshot.sync_transaction_id,
                coolant_verification_transaction_id=snapshot.sync_transaction_id,
            )
        self._recovery_snapshot = snapshot
        if not complete:
            self._invalidate_recovery_sync_evidence_locked()
        self._recovery_sync_transaction = None
        self._recovery_sync_generation = None
        self._recovery_sync_epoch = None
        self._refresh_recovery_trust_locked()
        if not complete:
            if normal_session:
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.FAILED,
                    reason="Incomplete $# response; job admission remains blocked.",
                )
            self.ui_q.put(
                (
                    "log",
                    "[session] Incomplete $# response; state remains untrusted."
                    if normal_session
                    else "[recovery] Incomplete $# response; state remains untrusted.",
                )
            )
        elif normal_session:
            if not self._normal_session_ready_locked():
                self._normal_session_state = replace(
                    self._normal_session_state,
                    phase=NormalSessionPhase.POSITION_REQUIRED,
                    reason=(
                        "Controller state is synchronized. Home the machine or explicitly "
                        "accept the physically checked current position to become Job Ready."
                    ),
                )
                logger.info(
                    "Normal-session $# completed; explicit position establishment required: "
                    "generation=%s recovery_epoch=%s transaction_id=%s request_id=%s "
                    "snapshot_id=0x%x",
                    transaction.connection_generation,
                    transaction.recovery_epoch,
                    transaction.transaction_id,
                    transaction.parameters_request_id,
                    id(snapshot),
                )
        if normal_session:
            self._emit_normal_session_state()
        return True
