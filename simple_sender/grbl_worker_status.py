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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time
from collections import deque
from typing import cast

from simple_sender.types import (
    AlarmEvent,
    GcodeAckedEvent,
    GrblWorkerState,
    ProgressBytesEvent,
    ProgressEvent,
    ReadyEvent,
    SettingsDumpDoneEvent,
    StatusEvent,
    StreamErrorEvent,
    StreamStateEvent,
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
    GRBL_STARTUP_TIMEOUT,
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
    
    def _mark_ready(self) -> None:
        """Mark GRBL as ready (banner received)."""
        if not self._ready:
            self._ready = True
            self._watchdog_ready_armed = False
            self._watchdog_ready_ts = time.time()
            self._connect_started_ts = 0.0
            self.ui_q.put(ReadyEvent(True))
            logger.info("GRBL ready")

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
        self._safe_ui_put(("log_rx", text), context=context)

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
    
    def _handle_alarm(self, message: str) -> None:
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
        
        # Stop streaming if active
        self._abort_writes.set()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = False
            self._paused = False
        self._reset_stream_buffer()
        
        # Emit buffer state (wrapped to handle failures)
        try:
            self._emit_buffer_fill()
        except Exception as e:
            logger.error(f"Failed to emit buffer state during alarm: {e}")
        
        # Notify UI of alarm state (safe)
        self._safe_ui_put(StreamStateEvent("alarm", message), context="alarm state")
        
        self._clear_outgoing()
        
        # Emit alarm event (safe)
        self._safe_ui_put(AlarmEvent(message), context="alarm event")

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================
    
    def _handle_rx_line(self, line: str) -> None:
        """Handle received line from GRBL.
        
        Args:
            line: Line received from GRBL
        """
        now = time.time()
        self._last_rx_ts = now
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0

        # Parse status reports
        is_status = line.startswith("<") and line.endswith(">")

        line_lower = line.lower()
        self._log_rx_line(line)
        if self._settings_dump_active and line.startswith("$") and "=" in line:
            self._settings_dump_seen = True
        self._resolve_modal_query_tracker(line)
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
                    self._safe_ui_put(SettingsDumpDoneEvent(), context="settings dump")
                    self._emit_ui_log_rx("ok", context="settings ok")
        else:
            ok_summary = self._flush_ok_log(now)
            if ok_summary:
                self._emit_ui_log_rx(ok_summary, context="ok summary")
            if (not is_status) or self._status_log_due(now):
                self._emit_ui_log_rx(line, context="rx line")
        
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

            with self._stream_lock:
                if self._stream_line_queue:
                    queued_item = self._stream_line_queue.popleft()
                    self._stream_buf_used = max(0, self._stream_buf_used - queued_item.line_len)
                    queued_ts = float(getattr(queued_item, "queued_ts", 0.0) or 0.0)
                    if queued_ts > 0:
                        self._record_ack_latency(max(0.0, (now - queued_ts) * 1000.0))
                    if line_lower.startswith("error"):
                        err_source = getattr(queued_item, "manual_source", None)
                    manual_tracker = getattr(queued_item, "manual_tracker", None)
                    
                    if queued_item.is_gcode and self._streaming:
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
                        if line_lower.startswith("error"):
                            err_idx = queued_item.idx
                            err_line = queued_item.line
            
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
                self.ui_q.put(GcodeAckedEvent(int(ack_index)))
                self.ui_q.put(ProgressEvent(int(ack_index) + 1, len(self._gcode)))
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
                        )
                    )

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
                    self.ui_q.put(StreamErrorEvent(msg, err_idx, err_line, self._gcode_name))
                    self.ui_q.put(("log", f"[stream error] {msg}"))
                else:
                    source = err_source if err_source else self._last_manual_source
                    self.ui_q.put(("manual_error", line, source))
        
        # Status report
        if is_status:
            self._mark_ready()
            parts = line.strip("<>").split("|")
            state = parts[0] if parts else ""
            self._last_status_state_token = str(state or "")
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
            if not state_lower.startswith(("jog", "hold")):
                self._jog_cancel_inflight = False
            
            # Check for alarm in status
            if state.lower().startswith("alarm"):
                if not self._alarm_active:
                    self._handle_alarm(state)
            elif self._alarm_active:
                self._alarm_active = False
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
            
            self.ui_q.put(StatusEvent(line))
    
    def _status_loop(self, stop_evt: threading.Event) -> None:
        """Status polling thread - periodically requests status.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("Status thread started")
        
        try:
            while not stop_evt.is_set():
                # Snapshot connection state/time once per loop to avoid races
                # between repeated is_connected() checks in the same iteration.
                now = time.time()
                connected = bool(self.is_connected())
                if connected:
                    connect_started_ts = float(getattr(self, "_connect_started_ts", 0.0))
                    if (
                        connect_started_ts
                        and (not self._ready)
                        and (now - connect_started_ts) >= GRBL_STARTUP_TIMEOUT
                    ):
                        self._signal_disconnect("[status/startup] No GRBL greeting received")
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
                                "[status/watchdog-alarm] Connection watchdog timeout (alarm)"
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
                            self._signal_disconnect("[status/watchdog] Connection watchdog timeout")
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
                        self.send_realtime(RT_STATUS)
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
                            self._signal_disconnect(f"[status/query] Status query error: {e}")
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
            self._signal_disconnect(f"[status/thread] Status thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("Status thread stopped")

