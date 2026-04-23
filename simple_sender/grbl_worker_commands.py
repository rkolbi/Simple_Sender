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

from simple_sender.types import (
    GrblWorkerState,
    ManualCommandResultTracker,
    ReadyEvent,
    StreamStateEvent,
)

from .utils.constants import (
    DEFAULT_SPINDLE_RPM,
    RT_RESET,
    RT_HOLD,
    RT_RESUME,
    RT_JOG_CANCEL,
    WATCHDOG_HOMING_TIMEOUT,
    WATCHDOG_SETTINGS_DUMP_TIMEOUT,
)
from .utils.exceptions import GrblNotConnectedException, SerialWriteError
from .utils.validation import validate_feed_rate, validate_unit_mode, validate_rpm


logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


class GrblWorkerCommandMixin(GrblWorkerState):
    def send_immediate_tracked(
        self,
        command: str,
        *,
        source: str | None = None,
    ) -> ManualCommandResultTracker | None:
        """Send a manual command and return a completion tracker when accepted."""
        if not self.is_connected():
            logger.warning("Cannot send command - not connected")
            return None

        allow_stream_paused_macro = bool(
            source == "macro"
            and self._streaming
            and self._paused
            and bool(getattr(self, "_stream_tool_change_active", False))
        )
        if self._streaming and not allow_stream_paused_macro:
            logger.warning("Cannot send immediate command during streaming")
            try:
                self.ui_q.put(("log", f"[manual blocked] {command.strip()} (streaming active)"))
            except Exception as exc:
                _log_suppressed("Failed queueing blocked manual-command log while streaming", exc)
            return None

        command = command.strip()
        if not command:
            return None
        command_source = str(source or "").strip() or "manual"
        self._last_manual_source = command_source
        cmd_upper = command.upper()
        if cmd_upper.startswith("$J="):
            self._mark_manual_motion_status_grace()

        if self._alarm_active:
            if not (cmd_upper.startswith("$X") or cmd_upper.startswith("$H")):
                logger.warning(f"Command '{command}' blocked during alarm")
                return None

        if cmd_upper.startswith("$H"):
            try:
                if getattr(self, "_homing_watchdog_enabled", True):
                    timeout = float(getattr(self, "_homing_watchdog_timeout", WATCHDOG_HOMING_TIMEOUT))
                    if timeout > 0:
                        self.suspend_watchdog(timeout, reason="homing")
                        try:
                            self.ui_q.put(("log", f"[watchdog] Homing grace {timeout:g}s"))
                        except Exception as exc:
                            _log_suppressed("Failed queueing watchdog homing-grace log", exc)
            except Exception as exc:
                _log_suppressed("Failed configuring watchdog homing grace window", exc)
        elif cmd_upper == "$$":
            try:
                timeout = float(getattr(self, "_settings_dump_watchdog_timeout", WATCHDOG_SETTINGS_DUMP_TIMEOUT))
            except Exception:
                timeout = WATCHDOG_SETTINGS_DUMP_TIMEOUT
            self._settings_dump_active = True
            self._settings_dump_seen = False
            self._settings_dump_started_ts = time.time()
            if timeout > 0:
                try:
                    self.suspend_watchdog(timeout, reason="settings_dump")
                    try:
                        self.ui_q.put(("log", f"[watchdog] Settings dump grace {timeout:g}s"))
                    except Exception as exc:
                        _log_suppressed("Failed queueing watchdog settings-dump grace log", exc)
                except Exception as exc:
                    _log_suppressed("Failed configuring watchdog settings-dump grace window", exc)

        tracker = ManualCommandResultTracker(
            command_id=int(self._next_manual_command_id()),
            command=command,
            source=command_source,
        )
        with self._stream_lock:
            accepted = self._enqueue_manual_command(
                command,
                command_source,
                tracker=tracker,
            )
        if not accepted:
            return None
        return tracker

    def _mark_manual_motion_status_grace(self, duration_s: float = 2.0) -> None:
        try:
            duration = max(0.1, float(duration_s))
        except Exception:
            duration = 2.0
        now = time.time()
        until_ts = now + duration
        try:
            current_until = float(getattr(self, "_manual_motion_status_grace_until_ts", 0.0) or 0.0)
        except Exception:
            current_until = 0.0
        if until_ts > current_until:
            self._manual_motion_status_grace_until_ts = float(until_ts)
        changed_evt = getattr(self, "_status_interval_changed_evt", None)
        if isinstance(changed_evt, threading.Event):
            try:
                changed_evt.set()
            except Exception as exc:
                _log_suppressed("Failed signaling status thread after manual-motion grace update", exc)

    def send_immediate(self, command: str, *, source: str | None = None) -> bool:
        """Send command immediately (bypasses streaming).
        
        Used for manual console commands and UI buttons.
        Respects alarm state - only allows $X and $H during alarm.
        
        Args:
            command: G-code or GRBL command to send
        """
        return self.send_immediate_tracked(command, source=source) is not None
    
    def unlock(self) -> bool:
        """Send unlock command ($X) to clear alarm state."""
        return bool(self.send_immediate("$X"))
    
    def home(self) -> bool:
        """Send home command ($H) to run homing cycle."""
        return bool(self.send_immediate("$H"))
    
    def reset(self, emit_state: bool = True) -> bool:
        """Send soft reset (Ctrl-X).
        
        Immediately halts all motion and resets GRBL state.
        
        Args:
            emit_state: Whether to emit stream_state event
        """
        self._abort_writes.set()
        try:
            accepted = self.send_realtime(RT_RESET)
        except SerialWriteError as exc:
            logger.error(f"Reset failed: {exc}")
            self.ui_q.put(("log", f"[reset failed] {exc}"))
            self._abort_writes.clear()
            return False
        if accepted is False:
            self.ui_q.put(("log", "[reset failed] Ctrl-X was not sent."))
            self._abort_writes.clear()
            return False
        # Reset local state
        self._ready = False
        self._alarm_active = False
        self._jog_cancel_inflight = False
        self._jog_cancel_last_sent_ts = 0.0
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._watchdog_ready_armed = False
        self._watchdog_ready_ts = 0.0
        self._settings_dump_active = False
        self._settings_dump_seen = False
        self._settings_dump_started_ts = 0.0
        was_streaming = self._streaming or self._paused
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = False
            self._paused = False
        self._reset_stream_buffer()
        self._clear_outgoing()
        self._emit_buffer_fill()
        self.ui_q.put(ReadyEvent(False))
        if emit_state and was_streaming:
            self.ui_q.put(StreamStateEvent("stopped", None))
        self._abort_writes.clear()
        return True
    
    def hold(self) -> bool:
        """Send feed hold command (!) to pause motion."""
        accepted = self.send_realtime(RT_HOLD)
        if accepted is False:
            return False
        self._mark_manual_motion_status_grace(duration_s=2.5)
        return True
    
    def resume(self) -> bool:
        """Send cycle start command (~) to resume motion."""
        accepted = self.send_realtime(RT_RESUME)
        if accepted is False:
            return False
        self._mark_manual_motion_status_grace(duration_s=1.5)
        return True
    
    def spindle_on(self, rpm: int = DEFAULT_SPINDLE_RPM) -> bool:
        """Turn spindle on at specified RPM.
        
        Args:
            rpm: Spindle speed in RPM (default: 12000)
            
        Raises:
            ValueError: If RPM is invalid
        """
        rpm = validate_rpm(rpm)
        return bool(self.send_immediate(f"M3 S{rpm}"))

    def spindle_off(self) -> bool:
        """Turn spindle off."""
        return bool(self.send_immediate("M5"))
    
    def jog_cancel(self) -> bool:
        """Cancel active jog command."""
        now = time.time()
        inflight = bool(getattr(self, "_jog_cancel_inflight", False))
        last_sent = float(getattr(self, "_jog_cancel_last_sent_ts", 0.0) or 0.0)
        resend_after = max(0.1, float(getattr(self, "_jog_cancel_retry_timeout_s", 1.5) or 1.5))
        debounce_s = max(0.05, float(getattr(self, "_jog_cancel_debounce_s", 0.6) or 0.6))
        if inflight and (now - last_sent) < debounce_s:
            return False
        if inflight and (now - last_sent) < resend_after:
            return False
        accepted = self.send_realtime(RT_JOG_CANCEL)
        if accepted is False:
            return False
        self._mark_manual_motion_status_grace(duration_s=1.5)
        self._jog_cancel_inflight = True
        self._jog_cancel_last_sent_ts = float(now)
        return True

    def cancel_pending_jogs(self) -> None:
        """Remove queued jog commands from the manual queue."""
        self._purge_jog_queue.set()
        self._emit_buffer_fill()

    def manual_queue_busy(self) -> bool:
        """Return True when manual commands are still queued/pending."""
        with self._stream_lock:
            if self._manual_pending_item is not None:
                return True
            for queue_item in self._stream_line_queue:
                if not queue_item.is_gcode:
                    return True
            if self._manual_source_queue or self._manual_tracker_queue:
                return True
        try:
            return int(self._outgoing_q.qsize()) > 0
        except Exception:
            try:
                return not bool(self._outgoing_q.empty())
            except Exception:
                return False

    def manual_queue_backpressure(self) -> bool:
        """Return True when manual queue is blocked by buffer limits."""
        with self._stream_lock:
            return self._manual_pending_item is not None or self._outgoing_q.full()
    
    def jog(
        self,
        dx: float,
        dy: float,
        dz: float,
        feed: float,
        unit_mode: str,
        *,
        source: str | None = None,
    ) -> bool:
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
        # Keep fast status polling active through the expected duration of this jog.
        try:
            travel = (
                (float(dx) * float(dx))
                + (float(dy) * float(dy))
                + (float(dz) * float(dz))
            ) ** 0.5
            expected_s = (travel / float(feed)) * 60.0 if feed > 0.0 else 0.0
        except Exception:
            expected_s = 0.0
        grace_s = max(2.0, min(180.0, float(expected_s) + 2.0))
        self._mark_manual_motion_status_grace(duration_s=grace_s)
        cmd_source = source if source else "jog"
        return bool(self.send_immediate(cmd, source=cmd_source))
    
    # ========================================================================
    # G-CODE STREAMING
    # ========================================================================
    
