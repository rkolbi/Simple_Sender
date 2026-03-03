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
    UI_QUEUE_DRAIN_EVENT_LIMIT,
    UI_QUEUE_DRAIN_STALL_BUDGET_MS,
    UI_QUEUE_DRAIN_TIME_BUDGET_MS,
    UI_EVENT_QUEUE_MAXSIZE,
    UI_EVENT_QUEUE_DROP_NOTICE_INTERVAL,
    UI_QUEUE_IDLE_MAINTENANCE_INTERVAL_S,
    UI_QUEUE_QUIET_IDLE_MAINTENANCE_INTERVAL_S,
    UI_QUEUE_IDLE_RECONNECT_CHECK_INTERVAL_S,
    UI_QUEUE_MAINTENANCE_INTERVAL_S,
    UI_QUEUE_RECONNECT_CHECK_INTERVAL_S,
)
from simple_sender.types import AppProtocol, UiEvent

UI_QUEUE_DRAIN_INTERVAL_MS = 50
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_LOW_IMPACT_UI_EVENT_KINDS = frozenset({"buffer_fill", "log_rx", "log_tx", "status", "throughput"})


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _stream_ui_busy(app: AppProtocol) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    return str(getattr(app, "_stream_state", "") or "").strip().lower() in {"running", "paused"}


def _connected_quiet_idle(app: AppProtocol) -> bool:
    try:
        if not bool(getattr(app, "connected", False)):
            return False
        if not bool(getattr(app, "_grbl_ready", False)):
            return False
        if _stream_ui_busy(app):
            return False
        if bool(getattr(app, "_gcode_loading", False)):
            return False
        if bool(getattr(app, "_gcode_parsing_active", False)):
            return False
        if bool(getattr(app, "_homing_in_progress", False)):
            return False
        if bool(getattr(app, "_alarm_locked", False)):
            return False
        if getattr(app, "_stats_pending_request", None):
            return False
        state = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
        if state and not state.startswith("idle"):
            return False
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None and hasattr(ui_q, "qsize"):
            try:
                if int(ui_q.qsize()) > 0:
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return False


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
        "spindle_state",
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
    pending = 0
    processed_low_impact_only = True
    max_event_elapsed_ms = 0.0
    max_event_kind = ""
    drain_start = time.perf_counter()
    try:
        event_limit = max(1, int(getattr(app, "_ui_queue_drain_event_limit", UI_QUEUE_DRAIN_EVENT_LIMIT)))
    except Exception:
        event_limit = int(UI_QUEUE_DRAIN_EVENT_LIMIT)
    try:
        time_budget_ms = float(
            getattr(app, "_ui_queue_drain_time_budget_ms", UI_QUEUE_DRAIN_TIME_BUDGET_MS)
        )
    except Exception:
        time_budget_ms = float(UI_QUEUE_DRAIN_TIME_BUDGET_MS)
    if time_budget_ms <= 0:
        time_budget_ms = float(UI_QUEUE_DRAIN_TIME_BUDGET_MS)
    time_budget_s = time_budget_ms / 1000.0
    try:
        for _ in range(event_limit):
            try:
                evt = app.ui_q.get_nowait()
            except queue.Empty:
                break
            processed += 1
            evt_kind = ""
            if isinstance(evt, tuple) and evt:
                evt_kind = str(evt[0] or "")
            if (not evt_kind) or (evt_kind not in _LOW_IMPACT_UI_EVENT_KINDS):
                processed_low_impact_only = False
            evt_start = time.perf_counter()
            try:
                app._handle_evt(evt)
            except Exception as exc:
                app._log_exception("UI event error", exc)
            evt_elapsed_ms = max(0.0, (time.perf_counter() - evt_start) * 1000.0)
            if evt_elapsed_ms > max_event_elapsed_ms:
                max_event_elapsed_ms = evt_elapsed_ms
                max_event_kind = evt_kind or "unknown"
            if (time.perf_counter() - drain_start) >= time_budget_s:
                break
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
        pending = 0
        try:
            pending = int(app.ui_q.qsize())
        except Exception:
            pending = 0
        queue_busy = (processed > 0) or (pending > 0)
        stream_busy = _stream_ui_busy(app)
        quiet_idle = (not queue_busy) and _connected_quiet_idle(app)
        if not stream_busy:
            maintenance_interval_key = (
                "_ui_maintenance_interval_s"
                if queue_busy
                else "_ui_maintenance_idle_interval_s"
            )
            maintenance_interval_default = UI_QUEUE_MAINTENANCE_INTERVAL_S
            if not queue_busy:
                maintenance_interval_default = float(
                    getattr(
                        app,
                        "_ui_maintenance_idle_interval_s",
                        UI_QUEUE_IDLE_MAINTENANCE_INTERVAL_S,
                    )
                )
            if quiet_idle:
                maintenance_interval_key = "_ui_maintenance_quiet_idle_interval_s"
                maintenance_interval_default = float(
                    getattr(
                        app,
                        "_ui_maintenance_quiet_idle_interval_s",
                        UI_QUEUE_QUIET_IDLE_MAINTENANCE_INTERVAL_S,
                    )
                )
            maintenance_interval = max(
                0.0,
                float(getattr(app, maintenance_interval_key, maintenance_interval_default)),
            )
            last_maintenance = float(getattr(app, "_ui_maintenance_last_ts", 0.0) or 0.0)
            should_run_maintenance = (
                last_maintenance <= 0.0
                or (now - last_maintenance) >= maintenance_interval
            )
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
            if not bool(getattr(app, "connected", False)):
                reconnect_interval_key = (
                    "_auto_reconnect_check_interval_s"
                    if queue_busy
                    else "_auto_reconnect_check_idle_interval_s"
                )
                reconnect_interval_default = UI_QUEUE_RECONNECT_CHECK_INTERVAL_S
                if not queue_busy:
                    reconnect_interval_default = float(
                        getattr(
                            app,
                            "_auto_reconnect_check_idle_interval_s",
                            UI_QUEUE_IDLE_RECONNECT_CHECK_INTERVAL_S,
                        )
                    )
                reconnect_interval = max(
                    0.0,
                    float(
                        getattr(
                            app,
                            reconnect_interval_key,
                            reconnect_interval_default,
                        )
                    ),
                )
                last_reconnect_check = float(getattr(app, "_auto_reconnect_check_ts", 0.0) or 0.0)
                should_check_reconnect = (
                    last_reconnect_check <= 0.0
                    or (now - last_reconnect_check) >= reconnect_interval
                )
                if should_check_reconnect:
                    setattr(app, "_auto_reconnect_check_ts", now)
                    try:
                        app._maybe_auto_reconnect()
                    except Exception as exc:
                        app._log_exception("UI auto-reconnect check error", exc)
    finally:
        try:
            elapsed_ms = max(0.0, (time.perf_counter() - drain_start) * 1000.0)
            app._ui_queue_drain_ticks = int(getattr(app, "_ui_queue_drain_ticks", 0)) + 1
            app._ui_queue_drain_events = int(getattr(app, "_ui_queue_drain_events", 0)) + int(processed)
            app._ui_queue_drain_max_ms = max(
                float(getattr(app, "_ui_queue_drain_max_ms", 0.0)),
                elapsed_ms,
            )
            stall_budget_ms = float(
                getattr(app, "_ui_queue_drain_stall_budget_ms", UI_QUEUE_DRAIN_STALL_BUDGET_MS)
            )
            if elapsed_ms > stall_budget_ms:
                app._ui_queue_drain_stall_count = int(
                    getattr(app, "_ui_queue_drain_stall_count", 0)
                ) + 1
            runtime_eligible = (
                bool(getattr(app, "connected", False))
                and bool(getattr(app, "_grbl_ready", False))
                and not bool(getattr(app, "_gcode_loading", False))
                and not bool(getattr(app, "_closing", False))
                and not bool(getattr(app, "_connecting", False))
                and not bool(getattr(app, "_disconnecting", False))
            )
            if runtime_eligible:
                app._ui_queue_drain_runtime_ticks = int(
                    getattr(app, "_ui_queue_drain_runtime_ticks", 0)
                ) + 1
                app._ui_queue_drain_runtime_events = int(
                    getattr(app, "_ui_queue_drain_runtime_events", 0)
                ) + int(processed)
                app._ui_queue_drain_runtime_max_ms = max(
                    float(getattr(app, "_ui_queue_drain_runtime_max_ms", 0.0)),
                    elapsed_ms,
                )
                if max_event_elapsed_ms > float(
                    getattr(app, "_ui_queue_drain_runtime_slowest_event_ms", 0.0) or 0.0
                ):
                    app._ui_queue_drain_runtime_slowest_event_ms = float(max_event_elapsed_ms)
                    app._ui_queue_drain_runtime_slowest_event_kind = str(max_event_kind or "unknown")
                if elapsed_ms > stall_budget_ms:
                    app._ui_queue_drain_runtime_stall_count = int(
                        getattr(app, "_ui_queue_drain_runtime_stall_count", 0)
                    ) + 1
        except Exception:
            pass
        if app._closing:
            return
        next_delay_ms = UI_QUEUE_DRAIN_INTERVAL_MS
        stream_busy = _stream_ui_busy(app)
        if pending > 0:
            try:
                setattr(app, "_ui_queue_idle_streak", 0)
            except Exception:
                pass
            if pending >= 500:
                next_delay_ms = 1
            elif pending >= 200:
                next_delay_ms = 5
            elif pending >= 50:
                next_delay_ms = 15
            else:
                next_delay_ms = UI_QUEUE_DRAIN_INTERVAL_MS
        elif (
            processed <= 0
            or ((not stream_busy) and processed_low_impact_only)
        ):
            try:
                idle_streak = int(getattr(app, "_ui_queue_idle_streak", 0) or 0) + 1
                setattr(app, "_ui_queue_idle_streak", idle_streak)
                idle_base_ms = int(
                    max(
                        UI_QUEUE_DRAIN_INTERVAL_MS,
                        getattr(app, "_ui_queue_idle_interval_ms", UI_QUEUE_DRAIN_INTERVAL_MS),
                    )
                )
                idle_max_ms = int(
                    max(
                        idle_base_ms,
                        getattr(app, "_ui_queue_idle_max_interval_ms", idle_base_ms),
                    )
                )
                idle_backoff_step_ms = int(
                    max(
                        1,
                        getattr(app, "_ui_queue_idle_backoff_step_ms", UI_QUEUE_DRAIN_INTERVAL_MS),
                    )
                )
                backoff_candidate_ms = UI_QUEUE_DRAIN_INTERVAL_MS + (
                    idle_streak * idle_backoff_step_ms
                )
                next_delay_ms = int(
                    min(
                        idle_max_ms,
                        max(idle_base_ms, backoff_candidate_ms),
                    )
                )
            except Exception:
                next_delay_ms = UI_QUEUE_DRAIN_INTERVAL_MS
        else:
            try:
                setattr(app, "_ui_queue_idle_streak", 0)
            except Exception:
                pass
        try:
            app.after(next_delay_ms, app._drain_ui_queue)
        except Exception as exc:
            try:
                app._log_exception("UI queue reschedule error", exc)
            except Exception as log_exc:
                _log_suppressed("Failed logging UI queue reschedule error", log_exc)
