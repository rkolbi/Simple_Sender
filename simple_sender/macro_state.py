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

import time
from typing import Any, Callable

from simple_sender.utils.constants import RT_STATUS


def macro_wait_for_idle(
    *,
    app,
    grbl,
    ui_q,
    timeout_s: float = 30.0,
) -> None:
    if not grbl.is_connected():
        return
    start = time.time()
    seen_busy = False
    while True:
        if not grbl.is_connected():
            return
        state = str(app._machine_state_text).strip()
        is_idle = state.upper().startswith("IDLE")
        if getattr(app, "_homing_in_progress", False):
            is_idle = False
        if not grbl.is_streaming():
            if not is_idle:
                seen_busy = True
            elif is_idle and (seen_busy or (time.time() - start) > 0.2):
                return
        if timeout_s and (time.time() - start) > timeout_s:
            ui_q.put(("log", "[macro] %wait timeout"))
            return
        time.sleep(0.1)


def macro_wait_for_status(
    *,
    grbl,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    timeout_s: float = 1.0,
) -> bool:
    start = time.time()
    with macro_vars_lock:
        seq = int(macro_vars.get("_status_seq", 0) or 0)
    grbl.send_realtime(RT_STATUS)
    while True:
        with macro_vars_lock:
            now_seq = int(macro_vars.get("_status_seq", 0) or 0)
        if now_seq != seq:
            return True
        if timeout_s and (time.time() - start) > timeout_s:
            ui_q.put(("log", "[macro] %update timeout"))
            return False
        time.sleep(0.05)


def macro_wait_for_modal(
    *,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    seq: int | None = None,
    timeout_s: float = 1.0,
) -> bool:
    start = time.time()
    if seq is None:
        with macro_vars_lock:
            seq = int(macro_vars.get("_modal_seq", 0) or 0)
    while True:
        with macro_vars_lock:
            now_seq = int(macro_vars.get("_modal_seq", 0) or 0)
        if now_seq != seq:
            return True
        if timeout_s and (time.time() - start) > timeout_s:
            ui_q.put(("log", "[macro] $G modal update timeout"))
            return False
        time.sleep(0.05)


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
    except Exception:
        pass
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
) -> None:
    if not state:
        return
    units = state.get("units", "")
    if not units:
        return
    if not grbl.is_connected() or getattr(app, "_alarm_locked", False):
        ui_q.put(("log", "[macro] Skipped unit restore due to disconnect/alarm."))
        return
    macro_send(units)
    try:
        app._set_unit_mode("mm" if units.upper() == "G21" else "inch")
    except Exception:
        pass
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
) -> bool:
    if not state:
        ui_q.put(("log", "[macro] STATE_RETURN skipped: no saved state."))
        return False
    if not grbl.is_connected() or getattr(app, "_alarm_locked", False):
        ui_q.put(("log", "[macro] STATE_RETURN skipped due to disconnect/alarm."))
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
        except Exception:
            pass
        with macro_vars_lock:
            macro_vars["units"] = units
    ui_q.put(("log", "[macro] STATE_RETURN restored modal state."))
    return True
