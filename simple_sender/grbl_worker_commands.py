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

from simple_sender.types import GrblWorkerState

from .utils.constants import (
    DEFAULT_SPINDLE_RPM,
    RT_RESET,
    RT_HOLD,
    RT_RESUME,
    RT_JOG_CANCEL,
    WATCHDOG_HOMING_TIMEOUT,
)
from .utils.exceptions import GrblNotConnectedException, SerialWriteError
from .utils.validation import validate_feed_rate, validate_unit_mode, validate_rpm


logger = logging.getLogger(__name__)


class GrblWorkerCommandMixin(GrblWorkerState):
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
            except Exception:
                pass
            return
        
        if source:
            self._last_manual_source = str(source)
        elif not self._last_manual_source:
            self._last_manual_source = "manual"
        command = command.strip()
        if not command:
            return
        cmd_upper = command.upper()

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
                        except Exception:
                            pass
            except Exception:
                pass
        
        self._outgoing_q.put(command)
    
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
        self._watchdog_paused = False
        self._watchdog_trip_ts = 0.0
        self._watchdog_ignore_until = 0.0
        self._watchdog_ignore_reason = None
        self._settings_dump_active = False
        self._settings_dump_seen = False
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
        self.send_realtime(RT_HOLD)
    
    def resume(self) -> None:
        """Send cycle start command (~) to resume motion."""
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
        self.send_realtime(RT_JOG_CANCEL)

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
            return self._manual_pending_item is not None
    
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
        cmd_source = source if source else "jog"
        self.send_immediate(cmd, source=cmd_source)
    
    # ========================================================================
    # G-CODE STREAMING
    # ========================================================================
    
