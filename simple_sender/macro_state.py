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
"""Macro state helpers shared by the macro executor."""

from __future__ import annotations

from contextlib import contextmanager
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time
from typing import Any, Callable

from simple_sender.utils.constants import RT_STATUS

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_MACRO_WAIT_SLICE_S = 0.05
_MACRO_IDLE_WAIT_SLICE_S = 0.1
_MACRO_STATUS_REQUERY_INTERVAL_S = 0.1


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _wait_for_event_or_sleep(
    app,
    event_attr: str,
    wait_s: float,
    *,
    fallback_s: float,
) -> None:
    wait_s = max(0.0, float(wait_s))
    if wait_s <= 0:
        return
    evt = getattr(app, event_attr, None)
    if isinstance(evt, threading.Event):
        try:
            signaled = bool(evt.wait(wait_s))
            if signaled:
                evt.clear()
            return
        except Exception as exc:
            _log_suppressed(f"Failed waiting on thread event {event_attr}", exc)
    time.sleep(max(0.0, min(wait_s, fallback_s)))


def _refresh_macro_poll_profile(app) -> None:
    applier = getattr(app, "_apply_status_poll_profile", None)
    if callable(applier):
        try:
            applier()
        except Exception as exc:
            _log_suppressed("Failed applying macro fast-poll profile", exc)


@contextmanager
def macro_fast_poll_scope(app, counter_attr: str):
    attr_name = str(counter_attr or "").strip()
    if not attr_name:
        yield
        return
    try:
        current = int(getattr(app, attr_name, 0) or 0)
    except Exception:
        current = 0
    try:
        setattr(app, attr_name, current + 1)
    except Exception as exc:
        _log_suppressed(f"Failed incrementing macro fast-poll counter {attr_name}", exc)
    _refresh_macro_poll_profile(app)
    try:
        yield
    finally:
        try:
            current = int(getattr(app, attr_name, 0) or 0)
        except Exception:
            current = 0
        try:
            setattr(app, attr_name, max(0, current - 1))
        except Exception as exc:
            _log_suppressed(f"Failed decrementing macro fast-poll counter {attr_name}", exc)
        _refresh_macro_poll_profile(app)


def macro_wait_for_idle(
    *,
    app,
    grbl,
    ui_q,
    timeout_s: float = 30.0,
    cancel_event: threading.Event | None = None,
) -> None:
    if not grbl.is_connected():
        return
    with macro_fast_poll_scope(app, "_macro_critical_fast_poll_count"):
        try:
            grbl.send_realtime(RT_STATUS)
        except Exception as exc:
            _log_suppressed("Failed sending initial macro %wait status query", exc)
        start = time.monotonic()
        seen_busy = False
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            if not grbl.is_connected():
                return
            state = str(app._machine_state_text).strip()
            is_idle = state.upper().startswith("IDLE")
            if getattr(app, "_homing_in_progress", False):
                is_idle = False
            stream_active = bool(grbl.is_streaming())
            allow_paused_tool_change = bool(
                stream_active
                and bool(getattr(grbl, "_paused", False))
                and bool(getattr(grbl, "_stream_tool_change_active", False))
            )
            if (not stream_active) or allow_paused_tool_change:
                if not is_idle:
                    seen_busy = True
                elif is_idle and (seen_busy or (time.monotonic() - start) > 0.2):
                    return
            elapsed = max(0.0, time.monotonic() - start)
            if timeout_s and elapsed > timeout_s:
                ui_q.put(("log", "[macro] %wait timeout"))
                return
            wait_s = _MACRO_IDLE_WAIT_SLICE_S
            if timeout_s:
                wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
            _wait_for_event_or_sleep(
                app,
                "_status_update_event",
                wait_s,
                fallback_s=_MACRO_WAIT_SLICE_S,
            )


def macro_wait_for_status(
    *,
    app,
    grbl,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    timeout_s: float = 1.0,
    cancel_event: threading.Event | None = None,
) -> bool:
    with macro_fast_poll_scope(app, "_macro_critical_fast_poll_count"):
        start = time.monotonic()
        next_query_ts = start
        with macro_vars_lock:
            seq = int(macro_vars.get("_status_coords_seq", 0) or 0)
        status_evt = getattr(app, "_status_coords_update_event", None)
        if isinstance(status_evt, threading.Event):
            try:
                status_evt.clear()
            except Exception as exc:
                _log_suppressed("Failed clearing coordinate-update event before waiting", exc)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return False
            with macro_vars_lock:
                now_seq = int(macro_vars.get("_status_coords_seq", 0) or 0)
            if now_seq != seq:
                return True
            now_mono = time.monotonic()
            if now_mono >= next_query_ts:
                try:
                    grbl.send_realtime(RT_STATUS)
                except Exception as exc:
                    _log_suppressed("Failed sending macro %update realtime status query", exc)
                next_query_ts = now_mono + _MACRO_STATUS_REQUERY_INTERVAL_S
            elapsed = max(0.0, now_mono - start)
            if timeout_s and elapsed > timeout_s:
                ui_q.put(("log", "[macro] %update timeout"))
                return False
            wait_s = _MACRO_WAIT_SLICE_S
            if timeout_s:
                wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
            _wait_for_event_or_sleep(
                app,
                "_status_coords_update_event",
                wait_s,
                fallback_s=_MACRO_WAIT_SLICE_S,
            )


def macro_wait_for_modal(
    *,
    app,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    seq: int | None = None,
    timeout_s: float = 1.0,
    cancel_event: threading.Event | None = None,
) -> bool:
    start = time.monotonic()
    if seq is None:
        with macro_vars_lock:
            seq = int(macro_vars.get("_modal_seq", 0) or 0)
    modal_evt = getattr(app, "_modal_update_event", None)
    if isinstance(modal_evt, threading.Event):
        try:
            modal_evt.clear()
        except Exception as exc:
            _log_suppressed("Failed clearing modal-update event before waiting", exc)
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        with macro_vars_lock:
            now_seq = int(macro_vars.get("_modal_seq", 0) or 0)
        if now_seq != seq:
            return True
        elapsed = max(0.0, time.monotonic() - start)
        if timeout_s and elapsed > timeout_s:
            ui_q.put(("log", "[macro] $G modal update timeout"))
            return False
        wait_s = _MACRO_WAIT_SLICE_S
        if timeout_s:
            wait_s = min(wait_s, max(0.0, timeout_s - elapsed))
        _wait_for_event_or_sleep(
            app,
            "_modal_update_event",
            wait_s,
            fallback_s=_MACRO_WAIT_SLICE_S,
        )


def snapshot_macro_state(
    *,
    macro_vars: dict[str, Any],
    macro_vars_lock,
) -> dict[str, str]:
    with macro_vars_lock:
        def pick(key: str) -> str:
            value = macro_vars.get(key, "")
            return str(value) if value is not None else ""

        return {
            "WCS": pick("WCS"),
            "plane": pick("plane"),
            "units": pick("units"),
            "distance": pick("distance"),
            "feedmode": pick("feedmode"),
            "spindle": pick("spindle"),
            "coolant": pick("coolant"),
        }


def macro_force_mm(
    *,
    app,
    macro_send: Callable[[str], Any],
    macro_vars: dict[str, Any],
    macro_vars_lock,
) -> None:
    macro_send("G21")
    try:
        app._set_unit_mode("mm")
    except Exception as exc:
        _log_suppressed("Failed syncing app unit mode to mm during macro force-mm", exc)
    with macro_vars_lock:
        macro_vars["units"] = "G21"


def macro_restore_units(
    *,
    app,
    grbl,
    ui_q,
    macro_send: Callable[[str], Any],
    macro_vars: dict[str, Any],
    macro_vars_lock,
    state: dict[str, str] | None,
    log_prefix: str = "[macro]",
    restore_label: str = "unit restore",
) -> None:
    if not state:
        return
    units = state.get("units", "")
    if not units:
        return
    if not grbl.is_connected() or getattr(app, "_alarm_locked", False):
        ui_q.put(
            (
                "log",
                f"{str(log_prefix).strip() or '[macro]'} Skipped {restore_label} due to disconnect/alarm.",
            )
        )
        return
    macro_send(units)
    try:
        app._set_unit_mode("mm" if units.upper() == "G21" else "inch")
    except Exception as exc:
        _log_suppressed("Failed syncing app unit mode during macro unit restore", exc)
    with macro_vars_lock:
        macro_vars["units"] = units


def macro_restore_state(
    *,
    app,
    grbl,
    ui_q,
    macro_send: Callable[[str], Any],
    macro_vars: dict[str, Any],
    macro_vars_lock,
    state: dict[str, str] | None,
    log_prefix: str = "[macro]",
    restore_label: str = "STATE_RETURN",
    restored_message: str = "restored modal state.",
) -> bool:
    prefix = str(log_prefix).strip() or "[macro]"
    label = str(restore_label or "STATE_RETURN").strip() or "STATE_RETURN"
    if not state:
        ui_q.put(("log", f"{prefix} {label} skipped: no saved state."))
        return False
    if not grbl.is_connected() or getattr(app, "_alarm_locked", False):
        ui_q.put(("log", f"{prefix} {label} skipped due to disconnect/alarm."))
        return False
    tokens = [
        state.get("WCS", ""),
        state.get("plane", ""),
        state.get("units", ""),
        state.get("distance", ""),
        state.get("feedmode", ""),
        state.get("spindle", ""),
        state.get("coolant", ""),
    ]
    tokens = [tok for tok in tokens if tok]
    if tokens:
        macro_send(" ".join(tokens))
    units = state.get("units", "")
    if units:
        try:
            app._set_unit_mode("mm" if units.upper() == "G21" else "inch")
        except Exception as exc:
            _log_suppressed("Failed syncing app unit mode during macro state restore", exc)
        with macro_vars_lock:
            macro_vars["units"] = units
    ui_q.put(("log", f"{prefix} {label} {restored_message}"))
    return True

