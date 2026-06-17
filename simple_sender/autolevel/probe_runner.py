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

from dataclasses import dataclass
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time
from typing import Any, Callable, cast

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.probe_controller import ProbeReport

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


@dataclass(frozen=True)
class ProbeRunSettings:
    safe_z: float = 5.0
    probe_depth: float = 3.0
    probe_feed: float = 100.0
    retract_z: float = 2.0
    settle_time: float = 0.0
    probe_timeout: float = 10.0
    idle_timeout: float = 30.0


class AutoLevelProbeRunner:
    def __init__(self, app: Any):
        self.app = app
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._running: bool = False

    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        grid: ProbeGrid,
        height_map: HeightMap,
        settings: ProbeRunSettings | None = None,
        *,
        on_point: Callable[[int, int, float], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_done: Callable[[bool, str | None], None] | None = None,
    ) -> bool:
        if self._running:
            return False
        if not self.app.grbl.is_connected():
            self._log("[autolevel] Probe blocked: not connected.")
            return False
        if self.app.grbl.is_streaming():
            self._log("[autolevel] Probe blocked: stop streaming first.")
            return False
        if getattr(self.app, "_alarm_locked", False):
            self._log("[autolevel] Probe blocked: clear alarm first.")
            return False
        settings = settings or ProbeRunSettings()
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(grid, height_map, settings, on_point, on_progress, on_done),
            daemon=True,
        )
        self._running = True
        self._thread.start()
        return True

    def _run(
        self,
        grid: ProbeGrid,
        height_map: HeightMap,
        settings: ProbeRunSettings,
        on_point: Callable[[int, int, float], None] | None,
        on_progress: Callable[[int, int], None] | None,
        on_done: Callable[[bool, str | None], None] | None,
    ) -> None:
        ok = False
        reason = None
        force_g90_on_exit = False
        prev_units, prev_distance = self._snapshot_modal_state()
        try:
            self.app.probe_controller.clear()
            if not self._send_and_wait("G21", settings.idle_timeout):
                reason = "Failed to set units."
                return
            if not self._send_and_wait("G90", settings.idle_timeout):
                reason = "Failed to set distance mode."
                return
            total = len(grid.points)
            for idx, (x, y) in enumerate(grid.points):
                if self._cancel.is_set():
                    reason = "Cancelled."
                    return
                if not self._probe_point(x, y, settings, height_map):
                    reason = "Probe failed."
                    force_g90_on_exit = True
                    return
                if on_point:
                    try:
                        indices = height_map.index_for(x, y)
                        if indices:
                            ix, iy = indices
                            z_val = height_map.get_index(ix, iy)
                            if z_val is not None:
                                on_point(ix, iy, z_val)
                    except Exception as exc:
                        _log_suppressed("Auto-level on_point callback raised", exc)
                if on_progress:
                    try:
                        on_progress(idx + 1, total)
                    except Exception as exc:
                        _log_suppressed("Auto-level on_progress callback raised", exc)
            ok = True
        except Exception as exc:
            reason = reason or f"Error: {exc}"
            self._log(f"[autolevel] Probe run failed: {exc}")
        finally:
            try:
                restore_failures = self._restore_modal_state(
                    prev_units,
                    prev_distance,
                    settings.idle_timeout,
                )
            except Exception as exc:
                self._log(f"[autolevel] Modal restore failed: {exc}")
            else:
                if restore_failures:
                    failed_text = ", ".join(restore_failures)
                    self._log(f"[autolevel] Modal restore incomplete: {failed_text}")
            if force_g90_on_exit:
                try:
                    self._force_g90_restore()
                except Exception as exc:
                    self._log(f"[autolevel] Force G90 restore failed: {exc}")
            self._running = False
            if on_done:
                try:
                    on_done(ok, reason)
                except Exception as exc:
                    _log_suppressed("Auto-level on_done callback raised", exc)

    def _snapshot_modal_state(self) -> tuple[str | None, str | None]:
        try:
            with self.app.macro_executor.macro_vars() as macro_vars:
                units = str(macro_vars.get("units") or "")
                distance = str(macro_vars.get("distance") or "")
            return units or None, distance or None
        except Exception:
            return None, None

    def _restore_modal_state(
        self,
        units: str | None,
        distance: str | None,
        timeout: float,
    ) -> list[str]:
        failed: list[str] = []
        if units:
            if not self._send_and_wait(units, timeout):
                failed.append(units)
        if distance:
            if not self._send_and_wait(distance, timeout):
                failed.append(distance)
        return failed

    def _force_g90_restore(self) -> None:
        if not self.app.grbl.is_connected():
            self._queue_force_g90("disconnected")
            return
        if getattr(self.app, "_alarm_locked", False):
            self._queue_force_g90("alarm")
            return
        if self.app.grbl.is_streaming():
            self._queue_force_g90("streaming")
            return
        try:
            accepted = self.app.grbl.send_immediate("G90", source="autolevel")
        except Exception:
            self._queue_force_g90("send failed")
            return
        if accepted is False:
            self._queue_force_g90("send rejected")

    def _queue_force_g90(self, reason: str) -> None:
        if getattr(self.app, "_pending_force_g90", False):
            return
        setattr(self.app, "_pending_force_g90", True)
        self._log(f"[autolevel] Pending G90 restore ({reason}).")

    def _probe_point(
        self,
        x: float,
        y: float,
        settings: ProbeRunSettings,
        height_map: HeightMap,
    ) -> bool:
        if not self._send_and_wait(f"G0 Z{settings.safe_z:.3f}", settings.idle_timeout):
            return False
        if not self._send_and_wait(f"G0 X{x:.3f} Y{y:.3f}", settings.idle_timeout):
            return False
        if settings.settle_time > 0:
            dwell = max(0.0, settings.settle_time)
            if not self._send_and_wait(f"G4 P{dwell:.3f}", settings.idle_timeout):
                return False
        if not self._send_and_wait("G91", settings.idle_timeout):
            return False
        depth = abs(settings.probe_depth)
        if depth <= 0:
            return False
        self.app.probe_controller.clear()
        probe_seq = self._probe_report_sequence()
        if not self._send_and_wait(
            f"G38.2 Z-{depth:.3f} F{settings.probe_feed:.3f}",
            settings.probe_timeout,
        ):
            return False
        report = self._wait_for_probe_report(settings.probe_timeout, seq=probe_seq)
        if report is None or not report.ok:
            return False
        if not height_map.set_point(x, y, report.z):
            return False
        if not self._send_and_wait(f"G0 Z{settings.retract_z:.3f}", settings.idle_timeout):
            return False
        if not self._send_and_wait("G90", settings.idle_timeout):
            return False
        return True

    def _probe_report_sequence(self) -> int:
        controller = getattr(self.app, "probe_controller", None)
        sequence = getattr(controller, "sequence", None)
        if callable(sequence):
            try:
                return int(sequence())
            except Exception:
                return 0
        return 0

    def _wait_for_probe_report(self, timeout_s: float, *, seq: int | None = None) -> ProbeReport | None:
        controller = getattr(self.app, "probe_controller", None)
        if controller is not None and hasattr(controller, "wait_for_report_change"):
            wait_seq = self._probe_report_sequence() if seq is None else int(seq)
            try:
                report = controller.wait_for_report_change(
                    wait_seq,
                    timeout_s,
                    cancel_event=self._cancel,
                )
            except Exception:
                report = None
            if report is not None:
                return cast(ProbeReport, report)
            return None
        start = time.monotonic()
        while True:
            if self._cancel.is_set():
                return None
            report = self.app.probe_controller.last_report()
            if report is not None:
                return cast(ProbeReport, report)
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                return None
            wait_s = 0.2 if not timeout_s else min(0.2, max(0.0, timeout_s - elapsed))
            self._wait_for_status_signal(wait_s)

    def _send_and_wait(self, command: str, timeout_s: float) -> bool:
        if self._cancel.is_set():
            return False
        try:
            accepted = self.app.grbl.send_immediate(command, source="autolevel")
        except Exception:
            self._log(f"[autolevel] Command send failed: {command}")
            return False
        if accepted is False:
            self._log(
                f"[autolevel] Command rejected before send: {command} ({self._rejection_reason()})."
            )
            return False
        completed = self.app.grbl.wait_for_manual_completion(timeout_s=timeout_s)
        if not completed:
            return False
        return self._wait_for_idle(timeout_s)

    def _rejection_reason(self) -> str:
        try:
            if not self.app.grbl.is_connected():
                return "controller disconnected"
        except Exception:
            pass
        if bool(getattr(self.app, "_alarm_locked", False)):
            return "controller alarm is active"
        try:
            if self.app.grbl.is_streaming():
                return "controller is busy streaming"
        except Exception:
            pass
        return "controller rejected the command"

    def _wait_for_idle(self, timeout_s: float) -> bool:
        start = time.monotonic()
        seen_busy = False
        while True:
            if self._cancel.is_set():
                return False
            state = str(getattr(self.app, "_machine_state_text", "")).strip().lower()
            is_idle = state.startswith("idle")
            if not is_idle:
                seen_busy = True
            elif is_idle and (seen_busy or (time.monotonic() - start) > 0.2):
                return True
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                return False
            wait_s = 0.5
            if timeout_s:
                wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
            self._wait_for_status_signal(wait_s)

    def _wait_for_status_signal(self, wait_s: float) -> None:
        wait_s = max(0.0, float(wait_s))
        if wait_s <= 0:
            return
        status_evt = getattr(self.app, "_status_update_event", None)
        if isinstance(status_evt, threading.Event):
            try:
                signaled = bool(status_evt.wait(wait_s))
                if signaled:
                    status_evt.clear()
                return
            except Exception:
                pass
        time.sleep(max(0.0, min(wait_s, 0.05)))

    def _log(self, message: str) -> None:
        try:
            self.app.ui_q.put(("log", message))
        except Exception as exc:
            _log_suppressed("Failed queueing auto-level probe runner log message", exc)
