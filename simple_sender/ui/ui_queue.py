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

import queue
import threading
import time
from collections import OrderedDict, deque

from simple_sender.utils.constants import (
    UI_EVENT_QUEUE_MAXSIZE,
    UI_EVENT_QUEUE_DROP_NOTICE_INTERVAL,
)
from simple_sender.types import AppProtocol, UiEvent

UI_QUEUE_DRAIN_INTERVAL_MS = 50


class UiEventQueue:
    _LOW_PRIORITY_KINDS = {"log_rx", "log_tx"}
    _COALESCE_KINDS = {
        "buffer_fill",
        "gcode_load_progress",
        "gcode_acked",
        "gcode_sent",
        "progress",
        "status",
        "throughput",
    }

    def __init__(
        self,
        maxsize: int = UI_EVENT_QUEUE_MAXSIZE,
        *,
        drop_notice_interval: float = UI_EVENT_QUEUE_DROP_NOTICE_INTERVAL,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._drop_notice_interval = float(drop_notice_interval)
        self._high: deque[UiEvent] = deque()
        self._low: deque[UiEvent] = deque()
        self._coalesced: OrderedDict[str, UiEvent] = OrderedDict()
        self._lock = threading.Lock()
        self._drop_counts: dict[str, int] = {}
        self._last_drop_notice = 0.0

    def put(self, item: UiEvent, block: bool = True, timeout: float | None = None) -> None:
        _ = block, timeout
        kind = item[0]
        with self._lock:
            if self._is_high_priority(item, kind):
                self._high.append(item)
                return
            if kind in self._COALESCE_KINDS:
                if kind in self._coalesced:
                    self._coalesced.move_to_end(kind)
                self._coalesced[kind] = item
                return
            if len(self._low) >= self._maxsize:
                self._record_drop(kind)
                return
            self._low.append(item)

    def put_nowait(self, item: UiEvent) -> None:
        self.put(item, block=False)

    def get_nowait(self) -> UiEvent:
        with self._lock:
            if self._high:
                return self._high.popleft()
            if self._coalesced:
                _, item = self._coalesced.popitem(last=False)
                return item
            if self._low:
                return self._low.popleft()
        raise queue.Empty

    def empty(self) -> bool:
        with self._lock:
            return not (self._high or self._coalesced or self._low)

    def qsize(self) -> int:
        with self._lock:
            return len(self._high) + len(self._coalesced) + len(self._low)

    def pop_drop_summary(self, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._drop_counts:
                return None
            if (now - self._last_drop_notice) < self._drop_notice_interval:
                return None
            total = sum(self._drop_counts.values())
            parts = [f"{kind}={count}" for kind, count in sorted(self._drop_counts.items())]
            self._drop_counts = {}
            self._last_drop_notice = now
        return f"[ui] Dropped {total} low-priority log event(s): " + ", ".join(parts)

    def _record_drop(self, kind: str) -> None:
        self._drop_counts[kind] = self._drop_counts.get(kind, 0) + 1

    def _is_high_priority(self, item: UiEvent, kind: str) -> bool:
        if kind in self._COALESCE_KINDS:
            return False
        if kind not in self._LOW_PRIORITY_KINDS:
            return True
        if item[0] == "log_rx":
            return self._is_critical_log_rx(item[1])
        return False

    @staticmethod
    def _is_critical_log_rx(line: str) -> bool:
        if not line:
            return False
        upper = line.upper()
        if "ALARM" in upper or "ERROR" in upper:
            return True
        if upper.startswith("GRBL"):
            return True
        if line.startswith("[GC:") or line.startswith("[PRB:") or line.startswith("$13="):
            return True
        if line.startswith("$") and "=" in line:
            return True
        if "[MSG" in upper:
            return True
        return False


def drain_ui_queue(app: AppProtocol) -> None:
    for _ in range(100):
        try:
            evt = app.ui_q.get_nowait()
        except queue.Empty:
            break
        try:
            app._handle_evt(evt)
        except Exception as exc:
            app._log_exception("UI event error", exc)
    if hasattr(app.ui_q, "pop_drop_summary"):
        try:
            summary = app.ui_q.pop_drop_summary()
        except Exception:
            summary = None
        if summary and hasattr(app, "streaming_controller"):
            try:
                app.streaming_controller.handle_log(summary)
            except Exception:
                pass
    if app._closing:
        return
    if hasattr(app, "_sync_tool_reference_label"):
        app._sync_tool_reference_label()
    app._maybe_auto_reconnect()
    app.after(UI_QUEUE_DRAIN_INTERVAL_MS, app._drain_ui_queue)
