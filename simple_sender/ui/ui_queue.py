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
UI_QUEUE_MAINTENANCE_INTERVAL_S = 0.25
UI_QUEUE_RECONNECT_CHECK_INTERVAL_S = 0.25
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


class UiEventQueue:
    _LOW_PRIORITY_KINDS = {"log_rx", "log_tx"}
    _HIGH_PRIORITY_LOG_KINDS = {"log", "log_rx"}
    _LOSSLESS_HIGH_PRIORITY_KINDS = {
        "alarm",
        "conn",
        "gcode_load_error",
        "gcode_load_invalid",
        "gcode_load_invalid_command",
        "gcode_loaded",
        "gcode_loaded_stream",
        "macro_prompt",
        "manual_error",
        "ready",
        "stream_error",
        "stream_interrupted",
        "stream_pause_reason",
        "stream_state",
        "ui_call",
        "ui_post",
    }
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
        high_priority_maxsize: int | None = None,
        high_priority_log_maxsize: int = UI_EVENT_QUEUE_MAXSIZE,
        drop_notice_interval: float = UI_EVENT_QUEUE_DROP_NOTICE_INTERVAL,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        if high_priority_maxsize is None:
            high_priority_maxsize = maxsize
        self._high_priority_maxsize = max(1, int(high_priority_maxsize))
        self._high_priority_hard_maxsize = max(self._high_priority_maxsize, self._maxsize * 4)
        self._high_priority_log_maxsize = max(1, int(high_priority_log_maxsize))
        self._drop_notice_interval = float(drop_notice_interval)
        self._high: deque[UiEvent] = deque()
        self._high_priority_log_count = 0
        self._low: deque[UiEvent] = deque()
        self._coalesced: OrderedDict[str, UiEvent] = OrderedDict()
        self._lock = threading.Lock()
        self._not_low_full = threading.Condition(self._lock)
        self._drop_counts: dict[str, int] = {}
        self._last_drop_notice = 0.0

    def put(self, item: UiEvent, block: bool = False, timeout: float | None = None) -> None:
        kind = item[0]
        with self._lock:
            if self._is_high_priority(item, kind):
                if not self._make_room_for_high_priority_locked(kind):
                    self._record_drop(kind)
                    return
                if kind in self._HIGH_PRIORITY_LOG_KINDS:
                    if self._high_priority_log_count >= self._high_priority_log_maxsize:
                        if self._drop_oldest_high_priority_log() is None:
                            self._record_drop(kind)
                            return
                        self._record_drop(kind)
                self._high.append(item)
                if kind in self._HIGH_PRIORITY_LOG_KINDS:
                    self._high_priority_log_count += 1
                return
            if kind in self._COALESCE_KINDS:
                if kind in self._coalesced:
                    self._coalesced.move_to_end(kind)
                self._coalesced[kind] = item
                return
            if len(self._low) >= self._maxsize:
                if not block:
                    self._record_drop(kind)
                    return
                deadline = None
                if timeout is not None:
                    timeout = max(0.0, float(timeout))
                    deadline = time.monotonic() + timeout
                while len(self._low) >= self._maxsize:
                    if deadline is not None:
                        remaining = max(0.0, deadline - time.monotonic())
                        if remaining <= 0:
                            self._record_drop(kind)
                            return
                        self._not_low_full.wait(timeout=remaining)
                    else:
                        self._not_low_full.wait()
            self._low.append(item)

    def put_nowait(self, item: UiEvent) -> None:
        self.put(item, block=False)

    def get_nowait(self) -> UiEvent:
        with self._lock:
            if self._high:
                item = self._high.popleft()
                if item[0] in self._HIGH_PRIORITY_LOG_KINDS and self._high_priority_log_count > 0:
                    self._high_priority_log_count -= 1
                return item
            if self._coalesced:
                _, item = self._coalesced.popitem(last=False)
                return item
            if self._low:
                item = self._low.popleft()
                self._not_low_full.notify()
                return item
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
        return f"[ui] Dropped {total} queued event(s): " + ", ".join(parts)

    def _record_drop(self, kind: str) -> None:
        self._drop_counts[kind] = self._drop_counts.get(kind, 0) + 1

    def _drop_oldest_high_priority_log(self) -> str | None:
        for idx, item in enumerate(self._high):
            kind = item[0]
            if kind not in self._HIGH_PRIORITY_LOG_KINDS:
                continue
            del self._high[idx]
            if self._high_priority_log_count > 0:
                self._high_priority_log_count -= 1
            return kind
        return None

    def _drop_oldest_high_priority(self) -> str | None:
        if not self._high:
            return None
        item = self._high.popleft()
        kind = item[0]
        if kind in self._HIGH_PRIORITY_LOG_KINDS and self._high_priority_log_count > 0:
            self._high_priority_log_count -= 1
        return kind

    def _drop_oldest_non_lossless_high_priority(self) -> str | None:
        for idx, item in enumerate(self._high):
            kind = item[0]
            if kind in self._LOSSLESS_HIGH_PRIORITY_KINDS:
                continue
            del self._high[idx]
            if kind in self._HIGH_PRIORITY_LOG_KINDS and self._high_priority_log_count > 0:
                self._high_priority_log_count -= 1
            return kind
        return None

    def _drop_oldest_low_priority(self) -> str | None:
        if not self._low:
            return None
        item = self._low.popleft()
        self._not_low_full.notify()
        return item[0]

    def _drop_oldest_coalesced(self) -> str | None:
        if not self._coalesced:
            return None
        kind, _item = self._coalesced.popitem(last=False)
        return kind

    def _make_room_for_high_priority_locked(self, incoming_kind: str) -> bool:
        if len(self._high) < self._high_priority_maxsize:
            return True
        incoming_is_lossless = incoming_kind in self._LOSSLESS_HIGH_PRIORITY_KINDS
        if incoming_is_lossless:
            dropped_kind = self._drop_oldest_non_lossless_high_priority()
            if dropped_kind is None:
                dropped_kind = self._drop_oldest_low_priority()
            if dropped_kind is None:
                dropped_kind = self._drop_oldest_coalesced()
            if dropped_kind is not None:
                self._record_drop(dropped_kind)
            elif len(self._high) >= self._high_priority_hard_maxsize:
                # Emergency bound: avoid unbounded growth if only lossless high-priority
                # events are being enqueued for an extended period.
                dropped_kind = self._drop_oldest_high_priority()
                if dropped_kind is not None:
                    self._record_drop(dropped_kind)
                else:
                    return False
            # Never drop existing critical events to make room for a critical event.
            # Allow temporary overflow if only critical events remain, bounded by
            # the emergency hard cap above.
            return True
        if incoming_kind in self._HIGH_PRIORITY_LOG_KINDS:
            if self._drop_oldest_high_priority_log() is not None:
                self._record_drop(incoming_kind)
                return True
            return False

        dropped_kind = self._drop_oldest_high_priority_log()
        if dropped_kind is None:
            dropped_kind = self._drop_oldest_low_priority()
        if dropped_kind is None:
            dropped_kind = self._drop_oldest_coalesced()
        if dropped_kind is None:
            dropped_kind = self._drop_oldest_non_lossless_high_priority()
        if dropped_kind is None:
            return False
        self._record_drop(dropped_kind)
        return True

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
    processed = 0
    try:
        for _ in range(100):
            try:
                evt = app.ui_q.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                app._handle_evt(evt)
            except Exception as exc:
                app._log_exception("UI event error", exc)
        if hasattr(app.ui_q, "pop_drop_summary"):
            try:
                summary = app.ui_q.pop_drop_summary()
            except Exception as exc:
                summary = None
                if hasattr(app, "_log_exception"):
                    try:
                        app._log_exception("UI queue drop-summary error", exc)
                    except Exception as log_exc:
                        _log_suppressed("Failed logging UI queue drop-summary error", log_exc)
                else:
                    _log_suppressed("UI queue drop-summary error", exc)
            if summary and hasattr(app, "streaming_controller"):
                try:
                    app.streaming_controller.handle_log(summary)
                except Exception as exc:
                    _log_suppressed("Failed logging UI queue drop-summary event to console", exc)
        if app._closing:
            return
        now = time.monotonic()
        maintenance_interval = max(
            0.0,
            float(getattr(app, "_ui_maintenance_interval_s", UI_QUEUE_MAINTENANCE_INTERVAL_S)),
        )
        last_maintenance = float(getattr(app, "_ui_maintenance_last_ts", 0.0) or 0.0)
        should_run_maintenance = processed > 0 or (now - last_maintenance) >= maintenance_interval
        if should_run_maintenance:
            setattr(app, "_ui_maintenance_last_ts", now)
            if hasattr(app, "_refresh_toolbar_action_focus"):
                try:
                    app._refresh_toolbar_action_focus()
                except Exception as exc:
                    _log_suppressed("Failed refreshing toolbar action focus", exc)
            if hasattr(app, "_update_quick_button_visibility"):
                try:
                    app._update_quick_button_visibility()
                except Exception as exc:
                    _log_suppressed("Failed updating quick-button visibility", exc)
            if hasattr(app, "_sync_tool_reference_label"):
                try:
                    app._sync_tool_reference_label()
                except Exception as exc:
                    app._log_exception("UI tool-reference sync error", exc)
        reconnect_interval = max(
            0.0,
            float(
                getattr(
                    app,
                    "_auto_reconnect_check_interval_s",
                    UI_QUEUE_RECONNECT_CHECK_INTERVAL_S,
                )
            ),
        )
        last_reconnect_check = float(getattr(app, "_auto_reconnect_check_ts", 0.0) or 0.0)
        should_check_reconnect = processed > 0 or (now - last_reconnect_check) >= reconnect_interval
        if should_check_reconnect:
            setattr(app, "_auto_reconnect_check_ts", now)
            try:
                app._maybe_auto_reconnect()
            except Exception as exc:
                app._log_exception("UI auto-reconnect check error", exc)
    finally:
        if app._closing:
            return
        try:
            app.after(UI_QUEUE_DRAIN_INTERVAL_MS, app._drain_ui_queue)
        except Exception as exc:
            try:
                app._log_exception("UI queue reschedule error", exc)
            except Exception as log_exc:
                _log_suppressed("Failed logging UI queue reschedule error", log_exc)
