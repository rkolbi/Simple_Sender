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
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


@dataclass(frozen=True)
class ProbeReport:
    x: float
    y: float
    z: float
    ok: bool
    raw: str

    def as_namespace(self) -> SimpleNamespace:
        return SimpleNamespace(x=self.x, y=self.y, z=self.z, ok=self.ok, raw=self.raw)


class ProbeController:
    def __init__(self, app: Any):
        self.app = app
        self._last_report: ProbeReport | None = None
        self._callbacks: list[Callable[[ProbeReport], None]] = []
        self._seq: int = 0
        self._report_event = threading.Event()

    def last_report(self) -> ProbeReport | None:
        return self._last_report

    def sequence(self) -> int:
        return self._seq

    def clear(self) -> None:
        self._last_report = None
        self._report_event.clear()
        try:
            with self.app.macro_executor.macro_vars() as macro_vars:
                macro_vars["PRB"] = None
        except Exception as exc:
            _log_suppressed("Failed clearing PRB macro variable in probe controller", exc)

    def register_callback(self, callback: Callable[[ProbeReport], None]) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[ProbeReport], None]) -> None:
        try:
            self._callbacks.remove(callback)
        except ValueError:
            return

    def handle_rx_line(self, raw: str) -> None:
        report = self._parse_probe_report(raw)
        if report is None:
            return
        self._last_report = report
        self._seq += 1
        self._report_event.set()
        try:
            with self.app.macro_executor.macro_vars() as macro_vars:
                macro_vars["prbx"] = report.x
                macro_vars["prby"] = report.y
                macro_vars["prbz"] = report.z
                macro_vars["PRB"] = report.as_namespace()
        except Exception as exc:
            _log_suppressed("Failed updating probe macro variables from PRB report", exc)
        for callback in list(self._callbacks):
            try:
                callback(report)
            except Exception as exc:
                _log_suppressed("Probe callback raised while handling PRB report", exc)

    def wait_for_report_change(
        self,
        seq: int,
        timeout_s: float,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProbeReport | None:
        start = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return None
            if self._seq != seq:
                return self._last_report
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                return None
            wait_s = 1.0
            if timeout_s:
                wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
            signaled = self._report_event.wait(wait_s)
            if signaled:
                self._report_event.clear()

    def _parse_probe_report(self, raw: str) -> ProbeReport | None:
        line = raw.strip()
        if not (line.startswith("[PRB:") and line.endswith("]")):
            return None
        payload = line[5:-1]
        if ":" not in payload:
            return None
        coords_part, ok_part = payload.rsplit(":", 1)
        coords = coords_part.split(",")
        if len(coords) < 3:
            return None
        try:
            x = float(coords[0])
            y = float(coords[1])
            z = float(coords[2])
        except Exception:
            return None
        success = ok_part.strip() == "1"
        return ProbeReport(x=x, y=y, z=z, ok=success, raw=line)
