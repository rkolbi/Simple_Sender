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
import threading
import time
from typing import cast

from simple_sender.types import GrblWorkerState

from .utils.constants import (
    EVENT_QUEUE_TIMEOUT,
    RT_STATUS,
    RX_BUFFER_SIZE,
    RX_OK_SUMMARY_INTERVAL,
    RX_STATUS_LOG_INTERVAL,
    WATCHDOG_ALARM_DISCONNECT_TIMEOUT,
    WATCHDOG_DISCONNECT_TIMEOUT,
    WATCHDOG_RX_TIMEOUT,
)
from .utils.validation import validate_interval


logger = logging.getLogger(__name__)


def _annotate_alarm(message: str) -> str:
    from . import grbl_worker as grbl_worker_mod

    return cast(str, grbl_worker_mod.annotate_grbl_alarm(message))


class GrblWorkerStatusMixin(GrblWorkerState):
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

    def _status_log_due(self, now: float) -> bool:
        interval = float(getattr(self, "_status_log_interval", RX_STATUS_LOG_INTERVAL))
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
        self._safe_ui_put(("stream_state", "alarm", message), context="alarm state")
        
        self._clear_outgoing()
        
        # Emit alarm event (safe)
        self._safe_ui_put(("alarm", message), context="alarm event")

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
        if line_lower == "ok":
            ok_summary = self._note_ok_log(now)
            if ok_summary:
                self._safe_ui_put(("log_rx", ok_summary), context="ok summary")
            if getattr(self, "_settings_dump_active", False):
                self._settings_dump_active = False
                self._safe_ui_put(("settings_dump_done",), context="settings dump")
                self._safe_ui_put(("log_rx", "ok"), context="settings ok")
        else:
            ok_summary = self._flush_ok_log(now)
            if ok_summary:
                self._safe_ui_put(("log_rx", ok_summary), context="ok summary")
            if (not is_status) or self._status_log_due(now):
                self.ui_q.put(("log_rx", line))
        
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
                    now = time.time()
                    watchdog_ignore_until = float(getattr(self, "_watchdog_ignore_until", 0.0))
                    watchdog_ignored = watchdog_ignore_until and (now < watchdog_ignore_until)
                    idle = now - self._last_rx_ts
                    if self._alarm_active:
                        if (
                            idle >= WATCHDOG_ALARM_DISCONNECT_TIMEOUT
                            and (self._streaming or self._ready)
                            and not watchdog_ignored
                        ):
                            self._signal_disconnect("Connection watchdog timeout (alarm)")
                            stop_evt.set()
                            break
                    else:
                        if (
                            idle >= WATCHDOG_RX_TIMEOUT
                            and (self._streaming or self._paused or self._ready)
                            and not self._watchdog_paused
                            and not watchdog_ignored
                        ):
                            if self._streaming and not self._paused:
                                self._pause_stream(reason="connection watchdog")
                            self._watchdog_paused = True
                            self._watchdog_trip_ts = now
                            try:
                                self.ui_q.put(("log", "[watchdog] No RX from GRBL; pausing stream."))
                            except Exception:
                                pass
                        if (
                            idle >= WATCHDOG_DISCONNECT_TIMEOUT
                            and (self._streaming or self._ready)
                            and not watchdog_ignored
                        ):
                            self._signal_disconnect("Connection watchdog timeout")
                            stop_evt.set()
                            break
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
