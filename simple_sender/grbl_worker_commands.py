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

from simple_sender.types import GrblWorkerState

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
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


class GrblWorkerCommandMixin(GrblWorkerState):
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

    def send_immediate(self, command: str, *, source: str | None = None) -> None:
        """Send command immediately (bypasses streaming).
        
        Used for manual console commands and UI buttons.
        Respects alarm state - only allows $X and $H during alarm.
        
        Args:
            command: G-code or GRBL command to send
        """
        if not self.is_connected():
            logger.warning("Cannot send command - not connected")
            return
        
        if self._streaming:
            logger.warning("Cannot send immediate command during streaming")
            try:
                self.ui_q.put(("log", f"[manual blocked] {command.strip()} (streaming active)"))
            except Exception as exc:
                _log_suppressed("Failed queueing blocked manual-command log while streaming", exc)
            return
        
        if source:
            self._last_manual_source = str(source)
        elif not self._last_manual_source:
            self._last_manual_source = "manual"
        command_source = self._last_manual_source
        command = command.strip()
        if not command:
            return
        cmd_upper = command.upper()
        if cmd_upper.startswith("$J="):
            self._mark_manual_motion_status_grace()

        # During alarm, only allow unlock and home commands
        if self._alarm_active:
            if not (cmd_upper.startswith("$X") or cmd_upper.startswith("$H")):
                logger.warning(f"Command '{command}' blocked during alarm")
                return

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
        
        with self._stream_lock:
            self._enqueue_manual_command(command, command_source)
    
    def unlock(self) -> None:
        """Send unlock command ($X) to clear alarm state."""
        self.send_immediate("$X")
    
    def home(self) -> None:
        """Send home command ($H) to run homing cycle."""
        self.send_immediate("$H")
    
    def reset(self, emit_state: bool = True) -> None:
        """Send soft reset (Ctrl-X).
        
        Immediately halts all motion and resets GRBL state.
        
        Args:
            emit_state: Whether to emit stream_state event
        """
        self._abort_writes.set()
        try:
            self.send_realtime(RT_RESET)
        except SerialWriteError as exc:
            logger.error(f"Reset failed: {exc}")
            self.ui_q.put(("log", f"[reset failed] {exc}"))
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
        self.ui_q.put(("ready", False))
        if emit_state and was_streaming:
            self.ui_q.put(("stream_state", "stopped", None))
        self._abort_writes.clear()
    
    def hold(self) -> None:
        """Send feed hold command (!) to pause motion."""
        self._mark_manual_motion_status_grace(duration_s=2.5)
        self.send_realtime(RT_HOLD)
    
    def resume(self) -> None:
        """Send cycle start command (~) to resume motion."""
        self._mark_manual_motion_status_grace(duration_s=1.5)
        self.send_realtime(RT_RESUME)
    
    def spindle_on(self, rpm: int = DEFAULT_SPINDLE_RPM) -> None:
        """Turn spindle on at specified RPM.
        
        Args:
            rpm: Spindle speed in RPM (default: 12000)
            
        Raises:
            ValueError: If RPM is invalid
        """
        rpm = validate_rpm(rpm)
        self.send_immediate(f"M3 S{rpm}")
    
    def spindle_off(self) -> None:
        """Turn spindle off."""
        self.send_immediate("M5")
    
    def jog_cancel(self) -> None:
        """Cancel active jog command."""
        now = time.time()
        inflight = bool(getattr(self, "_jog_cancel_inflight", False))
        last_sent = float(getattr(self, "_jog_cancel_last_sent_ts", 0.0) or 0.0)
        resend_after = max(0.1, float(getattr(self, "_jog_cancel_retry_timeout_s", 1.5) or 1.5))
        debounce_s = max(0.05, float(getattr(self, "_jog_cancel_debounce_s", 0.6) or 0.6))
        if inflight and (now - last_sent) < debounce_s:
            return
        if inflight and (now - last_sent) < resend_after:
            return
        self._mark_manual_motion_status_grace(duration_s=1.5)
        self.send_realtime(RT_JOG_CANCEL)
        self._jog_cancel_inflight = True
        self._jog_cancel_last_sent_ts = float(now)

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
    ) -> None:
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
        self.send_immediate(cmd, source=cmd_source)
    
    # ========================================================================
    # G-CODE STREAMING
    # ========================================================================
    
