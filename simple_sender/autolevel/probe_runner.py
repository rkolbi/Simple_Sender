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
import threading
import time
from typing import Callable

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.autolevel.height_map import HeightMap


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
    def __init__(self, app):
        self.app = app
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._running = False

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
        try:
            self.app.probe_controller.clear()
            prev_units, prev_distance = self._snapshot_modal_state()
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
                    except Exception:
                        pass
                if on_progress:
                    try:
                        on_progress(idx + 1, total)
                    except Exception:
                        pass
            ok = True
        finally:
            self._restore_modal_state(prev_units, prev_distance, settings.idle_timeout)
            if force_g90_on_exit:
                self._force_g90_restore()
            self._running = False
            if on_done:
                try:
                    on_done(ok, reason)
                except Exception:
                    pass

    def _snapshot_modal_state(self) -> tuple[str | None, str | None]:
        try:
            with self.app.macro_executor.macro_vars() as macro_vars:
                units = str(macro_vars.get("units") or "")
                distance = str(macro_vars.get("distance") or "")
            return units or None, distance or None
        except Exception:
            return None, None

    def _restore_modal_state(self, units: str | None, distance: str | None, timeout: float) -> None:
        if units:
            self._send_and_wait(units, timeout)
        if distance:
            self._send_and_wait(distance, timeout)

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
            self.app.grbl.send_immediate("G90", source="autolevel")
        except Exception:
            self._queue_force_g90("send failed")

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
        if not self._send_and_wait(
            f"G38.2 Z-{depth:.3f} F{settings.probe_feed:.3f}",
            settings.probe_timeout,
        ):
            return False
        report = self._wait_for_probe_report(settings.probe_timeout)
        if report is None or not report.ok:
            return False
        if not height_map.set_point(x, y, report.z):
            return False
        if not self._send_and_wait(f"G0 Z{settings.retract_z:.3f}", settings.idle_timeout):
            return False
        if not self._send_and_wait("G90", settings.idle_timeout):
            return False
        return True

    def _wait_for_probe_report(self, timeout_s: float) -> object | None:
        start = time.time()
        while True:
            if self._cancel.is_set():
                return None
            report = self.app.probe_controller.last_report()
            if report is not None:
                return report
            if timeout_s and (time.time() - start) > timeout_s:
                return None
            time.sleep(0.02)

    def _send_and_wait(self, command: str, timeout_s: float) -> bool:
        if self._cancel.is_set():
            return False
        try:
            self.app.grbl.send_immediate(command, source="autolevel")
        except Exception:
            return False
        completed = self.app.grbl.wait_for_manual_completion(timeout_s=timeout_s)
        if not completed:
            return False
        return self._wait_for_idle(timeout_s)

    def _wait_for_idle(self, timeout_s: float) -> bool:
        start = time.time()
        seen_busy = False
        while True:
            if self._cancel.is_set():
                return False
            state = str(getattr(self.app, "_machine_state_text", "")).strip().lower()
            is_idle = state.startswith("idle")
            if not is_idle:
                seen_busy = True
            elif is_idle and (seen_busy or (time.time() - start) > 0.2):
                return True
            if timeout_s and (time.time() - start) > timeout_s:
                return False
            time.sleep(0.05)

    def _log(self, message: str) -> None:
        try:
            self.app.ui_q.put(("log", message))
        except Exception:
            pass
