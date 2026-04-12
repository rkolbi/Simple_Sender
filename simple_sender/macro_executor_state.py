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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import threading
import time
from typing import Any, Callable, TypedDict

from simple_sender.macro_state import (
    macro_fast_poll_scope,
    macro_force_mm,
    macro_restore_state,
    macro_restore_units,
    macro_wait_for_idle,
    macro_wait_for_modal,
    macro_wait_for_status,
    snapshot_macro_state,
)
from simple_sender import tool_measurement
from simple_sender.types import MacroExecutorState

_TOOL_CHANGE_RETRY_TRIGGER_SPREAD_MM = 0.05
_TOOL_CHANGE_RETRY_FINE_PROBE_FEED_MM_MIN = 100.0


class _ToolProbeMeasurementKwargs(TypedDict):
    macro_send: Callable[[str], Any]
    probe_controller: Any
    cancel_event: Any
    probe_distance_mm: float
    rapid_feed_mm_min: float
    dwell_s: float
    spread_tolerance_mm: float
    log: Callable[[str], Any] | None


class MacroStateMixin(MacroExecutorState):
    def _macro_log(self, message: str) -> None:
        self.ui_q.put(("log", f"[macro][tool] {message}"))

    def _workflow_log(self, message: str) -> None:
        self.ui_q.put(("log", f"[workflow][tool] {message}"))

    def _macro_wait_for_idle(self, timeout_s: float = 30.0):
        macro_wait_for_idle(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            timeout_s=timeout_s,
            cancel_event=getattr(self, "_alarm_event", None),
        )

    def _macro_wait_for_status(self, timeout_s: float = 1.0) -> bool:
        return macro_wait_for_status(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            timeout_s=timeout_s,
            cancel_event=getattr(self, "_alarm_event", None),
        )

    def _macro_wait_for_modal(self, seq: int | None = None, timeout_s: float = 1.0) -> bool:
        return macro_wait_for_modal(
            app=self.app,
            ui_q=self.ui_q,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            seq=seq,
            timeout_s=timeout_s,
            cancel_event=getattr(self, "_alarm_event", None),
        )

    def _snapshot_macro_state(self) -> dict[str, str]:
        return snapshot_macro_state(
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
        )

    def _macro_force_mm(self):
        macro_force_mm(
            app=self.app,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
        )

    def _macro_restore_units(self):
        macro_restore_units(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
        )

    def _macro_restore_state(self) -> bool:
        restored = macro_restore_state(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
        )
        self._macro_state_restored = restored
        return restored

    def _workflow_restore_units(self):
        macro_restore_units(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
            log_prefix="[workflow]",
            restore_label="unit restore",
        )

    def _workflow_restore_state(self) -> bool:
        restored = macro_restore_state(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
            log_prefix="[workflow]",
            restore_label="Modal state restore",
        )
        self._macro_state_restored = restored
        return restored

    def measure_tool_probe_machine_z(
        self,
        *,
        probe_distance_mm: float,
        rapid_feed_mm_min: float,
        spread_tolerance_mm: float = tool_measurement.DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM,
        measurement_label: str = "Tool measurement",
        allow_tool_change_retry: bool = False,
        log=None,
    ) -> tool_measurement.ToolProbeMeasurement:
        label = str(measurement_label)
        retry_enabled = bool(allow_tool_change_retry)
        cycle_settings = tool_measurement.tool_probe_cycle_settings(self.app)
        active_log = log if callable(log) else self._macro_log
        common_kwargs: _ToolProbeMeasurementKwargs = {
            "macro_send": self._macro_send,
            "probe_controller": getattr(self.app, "probe_controller", None),
            "cancel_event": getattr(self, "_alarm_event", None),
            "probe_distance_mm": float(probe_distance_mm),
            "rapid_feed_mm_min": float(rapid_feed_mm_min),
            "dwell_s": float(cycle_settings.dwell_s),
            "spread_tolerance_mm": float(spread_tolerance_mm),
            "log": active_log,
        }
        try:
            return tool_measurement.collect_high_precision_tool_probe_measurement(
                measurement_label=label,
                fine_probe_feed_mm_min=float(cycle_settings.fine_probe_feed_mm_min),
                **common_kwargs,
            )
        except tool_measurement.ToolProbeSpreadExceededError as exc:
            if not retry_enabled:
                raise
            if exc.spread_mm <= _TOOL_CHANGE_RETRY_TRIGGER_SPREAD_MM:
                raise
            active_log(
                (
                    f"{label} first-round spread={exc.spread_mm:0.4f} mm exceeded "
                    f"retry threshold {_TOOL_CHANGE_RETRY_TRIGGER_SPREAD_MM:0.4f} mm."
                )
            )
            active_log(
                (
                    f"{label} retry starting: one-time fallback pass with reduced fine probe speed "
                    f"{min(cycle_settings.fine_probe_feed_mm_min, _TOOL_CHANGE_RETRY_FINE_PROBE_FEED_MM_MIN):0.0f} mm/min."
                )
            )
            retry_label = f"{label} retry"
            try:
                retry_result = tool_measurement.collect_high_precision_tool_probe_measurement(
                    measurement_label=retry_label,
                    fine_probe_feed_mm_min=min(
                        float(cycle_settings.fine_probe_feed_mm_min),
                        _TOOL_CHANGE_RETRY_FINE_PROBE_FEED_MM_MIN,
                    ),
                    **common_kwargs,
                )
            except tool_measurement.ToolProbeSpreadExceededError as retry_exc:
                active_log(
                    (
                        f"{retry_label} final result: rejected under normal rules; "
                        f"spread={retry_exc.spread_mm:0.4f} mm exceeded "
                        f"tolerance={retry_exc.tolerance_mm:0.4f} mm."
                    )
                )
                raise
            active_log(
                (
                    f"{retry_label} final result: accepted under normal rules; "
                    f"spread={retry_result.spread_mm:0.4f} mm."
                )
            )
            return retry_result

    def _parse_timeout(self, cmd_parts: list[str], default: float) -> float:
        if len(cmd_parts) > 1:
            try:
                value = float(cmd_parts[1])
            except Exception:
                value = default
            if value > 0:
                return value
        return default

    def _wait_for_connection_state(self, target: bool, timeout_s: float = 10.0) -> bool:
        with macro_fast_poll_scope(self.app, "_macro_critical_fast_poll_count"):
            start = time.monotonic()
            cancel_event = getattr(self, "_alarm_event", None)
            conn_evt = getattr(self.app, "_connection_state_event", None)
            if isinstance(conn_evt, threading.Event):
                try:
                    conn_evt.clear()
                except Exception:
                    pass
            while True:
                if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                    return False
                if getattr(self.app, "_closing", False):
                    return False
                if bool(getattr(self.app, "connected", False)) is target:
                    return True
                elapsed = max(0.0, time.monotonic() - start)
                if timeout_s and elapsed > timeout_s:
                    return False
                wait_s = 0.05
                if timeout_s:
                    wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
                if isinstance(conn_evt, threading.Event):
                    try:
                        signaled = bool(conn_evt.wait(wait_s))
                        if signaled:
                            conn_evt.clear()
                        continue
                    except Exception:
                        pass
                time.sleep(max(0.0, min(wait_s, 0.05)))

    def _wait_for_grbl_ready_state(self, timeout_s: float = 10.0) -> bool:
        with macro_fast_poll_scope(self.app, "_macro_critical_fast_poll_count"):
            start = time.monotonic()
            cancel_event = getattr(self, "_alarm_event", None)
            conn_evt = getattr(self.app, "_connection_state_event", None)
            status_evt = getattr(self.app, "_status_update_event", None)
            while True:
                if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                    return False
                if getattr(self.app, "_closing", False):
                    return False
                if bool(getattr(self.app, "connected", False)) and bool(
                    getattr(self.app, "_grbl_ready", False)
                ):
                    return True
                elapsed = max(0.0, time.monotonic() - start)
                if timeout_s and elapsed > timeout_s:
                    return False
                wait_s = 0.05
                if timeout_s:
                    wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
                signaled = False
                for evt in (status_evt, conn_evt):
                    if not isinstance(evt, threading.Event):
                        continue
                    try:
                        signaled = bool(evt.wait(wait_s))
                    except Exception:
                        signaled = False
                    if signaled:
                        try:
                            evt.clear()
                        except Exception:
                            pass
                        break
                if not signaled:
                    time.sleep(max(0.0, min(wait_s, 0.05)))

    def _wait_for_gcode_load_result(self, token: int, timeout_s: float = 120.0) -> bool:
        start = time.monotonic()
        cancel_event = getattr(self, "_alarm_event", None)
        result_evt = getattr(self.app, "_gcode_load_result_event", None)
        while True:
            if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                return False
            if getattr(self.app, "_closing", False):
                return False
            try:
                result_token = int(
                    getattr(self.app, "_gcode_load_last_result_token", -1) or -1
                )
            except Exception:
                result_token = -1
            result_success = getattr(self.app, "_gcode_load_last_result_success", None)
            if result_token == int(token) and result_success is not None:
                return bool(result_success)
            try:
                active_token = int(getattr(self.app, "_gcode_load_token", 0) or 0)
            except Exception:
                active_token = 0
            if active_token > int(token) and not bool(
                getattr(self.app, "_gcode_loading", False)
            ):
                return False
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                return False
            wait_s = 0.05
            if timeout_s:
                wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
            if isinstance(result_evt, threading.Event):
                try:
                    signaled = bool(result_evt.wait(wait_s))
                except Exception:
                    signaled = False
                if signaled:
                    try:
                        result_evt.clear()
                    except Exception:
                        pass
                    continue
            time.sleep(max(0.0, min(wait_s, 0.05)))
