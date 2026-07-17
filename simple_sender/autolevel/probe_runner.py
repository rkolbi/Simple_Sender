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

from dataclasses import dataclass, field
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time
from typing import Any, Callable, cast

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.probe_controller import ProbeReport
from simple_sender.types import AutoLevelInstallationTicket, AutoLevelWorkflowLease

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


@dataclass(frozen=True, slots=True)
class AutoLevelCancellationIdentity:
    connection_generation: int
    serial_port: object | None = field(default=None, repr=False, compare=False)
    recovery_epoch: int = 0
    workflow_id: int = 0
    active_command_id: int = 0
    cancellation_request_id: int = 0
    command_may_be_executing: bool = False


class AutoLevelProbeRunner:
    def __init__(self, app: Any):
        self.app = app
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._running: bool = False
        self._state_lock = threading.Lock()
        self._workflow_seq = 0
        self._command_seq = 0
        self._cancel_seq = 0
        self._workflow_id = 0
        self._active_command_id = 0
        self._command_may_be_executing = False
        self._connection_generation = 0
        self._serial_port: object | None = None
        self._recovery_epoch = 0
        self._workflow_lease: AutoLevelWorkflowLease | None = None
        self._cancellation_identity: AutoLevelCancellationIdentity | None = None

    def is_running(self) -> bool:
        return self._running

    def cancellation_identity(self) -> AutoLevelCancellationIdentity | None:
        with self._state_lock:
            return self._cancellation_identity

    def cancel(self) -> bool:
        self._cancel.set()
        with self._state_lock:
            if self._cancellation_identity is not None:
                return True
            if not self._running:
                return False
            self._cancel_seq += 1
            identity = AutoLevelCancellationIdentity(
                connection_generation=int(self._connection_generation),
                serial_port=self._serial_port,
                recovery_epoch=int(self._recovery_epoch),
                workflow_id=int(self._workflow_id),
                active_command_id=int(self._active_command_id),
                cancellation_request_id=int(self._cancel_seq),
                command_may_be_executing=bool(self._command_may_be_executing),
            )
            self._cancellation_identity = identity
        self._log(
            "[autolevel] Cancellation requested; closing workflow transmission and stopping the controller."
        )
        with self._state_lock:
            lease = self._workflow_lease
        requester = getattr(self.app.grbl, "cancel_auto_level_lease", None)
        if not callable(requester) or lease is None:
            self._log(
                "[autolevel] Controller stop transaction was unavailable; machine state must be treated as uncertain."
            )
            return False
        accepted = bool(
            requester(
                lease,
                "Auto-Level was canceled while probe or positioning motion could still be active.",
            )
        )
        if not accepted:
            self._log(
                "[autolevel] Cancellation stop transaction was rejected as stale or uncertain."
            )
        return accepted

    def start(
        self,
        grid: ProbeGrid,
        height_map: HeightMap,
        settings: ProbeRunSettings | None = None,
        *,
        on_point: Callable[[int, int, float], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_done: Callable[
            [
                AutoLevelWorkflowLease,
                AutoLevelInstallationTicket | None,
                bool,
                str | None,
            ],
            None,
        ]
        | None = None,
        on_lease_acquired: Callable[[AutoLevelWorkflowLease], None] | None = None,
        result_installation_required: bool = True,
    ) -> bool:
        if self._running:
            return False
        lease_getter = getattr(self.app.grbl, "acquire_auto_level_lease", None)
        if not callable(lease_getter):
            self._log("[autolevel] Probe blocked: exclusive controller workflow admission is unavailable.")
            return False
        try:
            lease, unavailable_reason = lease_getter()
        except Exception:
            self._log("[autolevel] Probe blocked: controller workflow lease could not be acquired.")
            return False
        if not isinstance(lease, AutoLevelWorkflowLease):
            detail = str(unavailable_reason or "controller is busy or not trusted")
            self._log(f"[autolevel] Probe blocked: Auto-Level lease unavailable ({detail}).")
            return False
        if getattr(self.app, "_alarm_locked", False):
            self.app.grbl.fail_auto_level_lease(
                lease,
                "Auto-Level lease retired because the UI reports an active alarm.",
                require_recovery=False,
            )
            self._log("[autolevel] Probe blocked: clear alarm first.")
            return False
        if not self.app.grbl.activate_auto_level_lease(lease):
            self._log(
                "[autolevel] Probe blocked: controller work changed while the exclusive lease was acquired."
            )
            return False
        settings = settings or ProbeRunSettings()
        self._cancel.clear()
        with self._state_lock:
            self._workflow_seq += 1
            self._workflow_id = int(self._workflow_seq)
            self._active_command_id = 0
            self._command_may_be_executing = False
            self._workflow_lease = lease
            self._connection_generation = int(lease.connection_generation)
            self._recovery_epoch = int(lease.recovery_epoch)
            self._serial_port = lease.serial_port
            self._cancellation_identity = None
        if on_lease_acquired is not None:
            try:
                on_lease_acquired(lease)
            except Exception as exc:
                self.app.grbl.fail_auto_level_lease(
                    lease,
                    "Auto-Level lease handoff failed before the probe thread started.",
                    require_recovery=False,
                )
                with self._state_lock:
                    if self._workflow_lease is lease:
                        self._workflow_lease = None
                _log_suppressed("Auto-level lease acquisition callback raised", exc)
                return False
        self._thread = threading.Thread(
            target=self._run,
            args=(
                lease,
                grid,
                height_map,
                settings,
                on_point,
                on_progress,
                on_done,
                bool(result_installation_required),
            ),
            daemon=True,
        )
        self._running = True
        try:
            self._thread.start()
        except Exception:
            self._running = False
            self.app.grbl.fail_auto_level_lease(
                lease,
                "Auto-Level probe thread could not start.",
                require_recovery=False,
            )
            raise
        return True

    def _run(
        self,
        lease: AutoLevelWorkflowLease,
        grid: ProbeGrid,
        height_map: HeightMap,
        settings: ProbeRunSettings,
        on_point: Callable[[int, int, float], None] | None,
        on_progress: Callable[[int, int], None] | None,
        on_done: Callable[
            [
                AutoLevelWorkflowLease,
                AutoLevelInstallationTicket | None,
                bool,
                str | None,
            ],
            None,
        ]
        | None,
        result_installation_required: bool = True,
    ) -> None:
        ok = False
        reason = None
        installation_ticket: AutoLevelInstallationTicket | None = None
        restoration_confirmed = False
        force_g90_on_exit = False
        prev_units, prev_distance = self._snapshot_modal_state()
        try:
            self.app.probe_controller.clear()
            if not self._send_and_wait(lease, "G21", settings.idle_timeout):
                reason = "Failed to set units."
                return
            if not self._send_and_wait(lease, "G90", settings.idle_timeout):
                reason = "Failed to set distance mode."
                return
            total = len(grid.points)
            for idx, (x, y) in enumerate(grid.points):
                if self._cancel.is_set():
                    reason = "Cancelled."
                    return
                if not self._probe_point(lease, x, y, settings, height_map):
                    reason = "Probe failed."
                    force_g90_on_exit = True
                    return
                if on_point:
                    if not self._workflow_identity_current(lease):
                        reason = "Auto-Level lease retired before point publication."
                        return
                    try:
                        indices = height_map.index_for(x, y)
                        if indices:
                            ix, iy = indices
                            z_val = height_map.get_index(ix, iy)
                            if z_val is not None:
                                on_point(ix, iy, z_val)
                    except Exception as exc:
                        _log_suppressed("Auto-level on_point callback raised", exc)
                    if not self._workflow_identity_current(lease):
                        reason = "Auto-Level lease retired during point publication."
                        return
                if on_progress:
                    if not self._workflow_identity_current(lease):
                        reason = "Auto-Level lease retired before progress publication."
                        return
                    try:
                        on_progress(idx + 1, total)
                    except Exception as exc:
                        _log_suppressed("Auto-level on_progress callback raised", exc)
                    if not self._workflow_identity_current(lease):
                        reason = "Auto-Level lease retired during progress publication."
                        return
            ok = self._workflow_identity_current(lease)
            if not ok:
                reason = "Auto-Level lease retired before final success."
        except Exception as exc:
            reason = reason or f"Error: {exc}"
            self._log(f"[autolevel] Probe run failed: {exc}")
        finally:
            if self._cancel.is_set():
                ok = False
                reason = "Cancelled; controller recovery is required."
                self._log(
                    "[autolevel] Modal restoration was skipped because cancellation retired controller-state trust."
                )
            else:
                try:
                    restore_failures = self._restore_modal_state(
                        lease,
                        prev_units,
                        prev_distance,
                        settings.idle_timeout,
                    )
                except Exception as exc:
                    ok = False
                    reason = f"Modal restoration failed: {exc}"
                    self.app.grbl.fail_auto_level_lease(
                        lease,
                        reason,
                        require_recovery=True,
                    )
                    self._log(f"[autolevel] Modal restore failed: {exc}")
                else:
                    if restore_failures:
                        failed_text = ", ".join(restore_failures)
                        self._log(f"[autolevel] Modal restore incomplete: {failed_text}")
                        ok = False
                        reason = f"Modal restoration failed: {failed_text}."
                        self.app.grbl.fail_auto_level_lease(
                            lease,
                            reason,
                            require_recovery=True,
                        )
                    else:
                        restoration_confirmed = True
                if force_g90_on_exit:
                    try:
                        self._force_g90_restore(lease)
                    except Exception as exc:
                        ok = False
                        reason = f"Modal restoration failed: {exc}"
                        self.app.grbl.fail_auto_level_lease(
                            lease,
                            reason,
                            require_recovery=True,
                        )
                        self._log(f"[autolevel] Force G90 restore failed: {exc}")
                    else:
                        restoration_confirmed = True
            if ok and not self._workflow_identity_current(lease):
                ok = False
                reason = "Auto-Level lease retired before completion."
            completed = self.app.grbl.complete_auto_level_lease(
                lease,
                success=ok,
                reason=str(reason or ""),
            )
            if not completed:
                ok = False
                reason = reason or "Auto-Level lease retired before completion."
            elif ok and result_installation_required:
                installation_ticket = self.app.grbl.authorize_auto_level_installation(
                    lease,
                    restoration_confirmed=bool(restoration_confirmed),
                )
                if installation_ticket is None:
                    ok = False
                    reason = "Auto-Level map installation authorization was rejected."
                    self.app.grbl.retire_auto_level_completion(lease, reason)
            elif ok:
                retired = self.app.grbl.retire_auto_level_completion(
                    lease,
                    "Auto-Level test result completed without active map installation.",
                )
                if not retired:
                    ok = False
                    reason = "Auto-Level test result ownership was retired before completion."
            with self._state_lock:
                self._command_may_be_executing = False
                if self._workflow_lease is lease:
                    self._workflow_lease = None
            self._running = False
            if on_done:
                try:
                    on_done(lease, installation_ticket, ok, reason)
                except Exception as exc:
                    if installation_ticket is not None:
                        self.app.grbl.abort_auto_level_installation(
                            installation_ticket,
                            "Auto-Level result callback failed before map installation.",
                        )
                    _log_suppressed("Auto-level on_done callback raised", exc)
            elif installation_ticket is not None:
                self.app.grbl.abort_auto_level_installation(
                    installation_ticket,
                    "Auto-Level result had no map-installation callback.",
                )

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
        lease: AutoLevelWorkflowLease,
        units: str | None,
        distance: str | None,
        timeout: float,
    ) -> list[str]:
        failed: list[str] = []
        if units:
            if not self._workflow_identity_current(lease) or not self._send_and_wait(
                lease, units, timeout
            ):
                failed.append(units)
        if distance:
            if not self._workflow_identity_current(lease) or not self._send_and_wait(
                lease, distance, timeout
            ):
                failed.append(distance)
        return failed

    def _force_g90_restore(self, lease: AutoLevelWorkflowLease) -> None:
        if self._cancel.is_set():
            return
        if not self._workflow_identity_current(lease):
            raise RuntimeError("workflow lease retired before G90 restoration")
        if getattr(self.app, "_alarm_locked", False):
            raise RuntimeError("controller alarm blocked G90 restoration")
        if self.app.grbl.is_streaming():
            raise RuntimeError("controller streaming state blocked G90 restoration")
        if not self._send_and_wait(lease, "G90", 30.0):
            raise RuntimeError("G90 restoration was not acknowledged and confirmed Idle")

    def _probe_point(
        self,
        lease: AutoLevelWorkflowLease,
        x: float,
        y: float,
        settings: ProbeRunSettings,
        height_map: HeightMap,
    ) -> bool:
        if not self._send_and_wait(lease, f"G0 Z{settings.safe_z:.3f}", settings.idle_timeout):
            return False
        if not self._send_and_wait(lease, f"G0 X{x:.3f} Y{y:.3f}", settings.idle_timeout):
            return False
        if settings.settle_time > 0:
            dwell = max(0.0, settings.settle_time)
            if not self._send_and_wait(lease, f"G4 P{dwell:.3f}", settings.idle_timeout):
                return False
        if not self._send_and_wait(lease, "G91", settings.idle_timeout):
            return False
        depth = abs(settings.probe_depth)
        if depth <= 0:
            return False
        self.app.probe_controller.clear()
        probe_seq = self._probe_report_sequence()
        if not self._send_and_wait(
            lease,
            f"G38.2 Z-{depth:.3f} F{settings.probe_feed:.3f}",
            settings.probe_timeout,
        ):
            return False
        report = self._wait_for_probe_report(lease, settings.probe_timeout, seq=probe_seq)
        if self._cancel.is_set() or report is None or not report.ok:
            return False
        if not height_map.set_point(x, y, report.z):
            return False
        if not self._send_and_wait(lease, f"G0 Z{settings.retract_z:.3f}", settings.idle_timeout):
            return False
        if not self._send_and_wait(lease, "G90", settings.idle_timeout):
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

    def _wait_for_probe_report(
        self,
        lease: AutoLevelWorkflowLease,
        timeout_s: float,
        *,
        seq: int | None = None,
    ) -> ProbeReport | None:
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
                return cast(ProbeReport, report) if self._workflow_identity_current(lease) else None
            return None
        start = time.monotonic()
        while True:
            if self._cancel.is_set() or not self._workflow_identity_current(lease):
                return None
            report = self.app.probe_controller.last_report()
            if report is not None:
                return cast(ProbeReport, report)
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                return None
            wait_s = 0.2 if not timeout_s else min(0.2, max(0.0, timeout_s - elapsed))
            self._wait_for_status_signal(wait_s)

    def _send_and_wait(
        self,
        lease: AutoLevelWorkflowLease,
        command: str,
        timeout_s: float,
    ) -> bool:
        if self._cancel.is_set() or not self._workflow_identity_current(lease):
            return False
        with self._state_lock:
            self._command_seq += 1
            command_id = int(self._command_seq)
            self._active_command_id = command_id
            self._command_may_be_executing = False
            current_lease = self._workflow_lease
        if current_lease is not lease:
            return False
        try:
            _generation, _recovery_epoch, status_seq, _state = (
                self.app.grbl.controller_status_observation()
            )
        except Exception:
            return False
        try:
            tracker = self.app.grbl.send_immediate_tracked(
                command,
                source="autolevel",
                expected_auto_level_lease=lease,
            )
        except Exception:
            self._log(f"[autolevel] Command send failed: {command}")
            return False
        if tracker is None:
            self._log(
                f"[autolevel] Command rejected before send: {command} ({self._rejection_reason()})."
            )
            return False
        with self._state_lock:
            if self._active_command_id == command_id:
                self._command_may_be_executing = True
        completed = tracker.wait(timeout_s=timeout_s)
        if (
            not completed
            or not bool(tracker.success)
            or self._cancel.is_set()
            or not self._workflow_identity_current(lease)
        ):
            return False
        idle = self._wait_for_idle(lease, timeout_s, after_sequence=status_seq)
        if idle:
            with self._state_lock:
                if self._active_command_id == command_id:
                    self._command_may_be_executing = False
        return bool(idle and not self._cancel.is_set())

    def _workflow_identity_current(self, lease: AutoLevelWorkflowLease) -> bool:
        with self._state_lock:
            current_lease = self._workflow_lease
        if current_lease is not lease:
            return False
        checker = getattr(self.app.grbl, "auto_level_lease_current", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(lease))
        except Exception:
            return False

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

    def _wait_for_idle(
        self,
        lease: AutoLevelWorkflowLease,
        timeout_s: float,
        *,
        after_sequence: int,
    ) -> bool:
        start = time.monotonic()
        while True:
            if self._cancel.is_set() or not self._workflow_identity_current(lease):
                return False
            try:
                generation, recovery_epoch, sequence, state = (
                    self.app.grbl.controller_status_observation()
                )
            except Exception:
                return False
            if (
                int(generation) != int(lease.connection_generation)
                or int(recovery_epoch) != int(lease.recovery_epoch)
            ):
                return False
            if int(sequence) > int(after_sequence) and str(state).strip().lower().startswith(
                "idle"
            ):
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
