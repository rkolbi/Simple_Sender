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
import time
from typing import Any

from simple_sender.utils.constants import (
    JOYSTICK_AXIS_RELEASE_THRESHOLD,
    JOYSTICK_AXIS_THRESHOLD,
    JOYSTICK_HOLD_DEFINITIONS,
    JOYSTICK_HOLD_MISS_LIMIT,
    JOYSTICK_HOLD_POLL_INTERVAL_MS,
    JOYSTICK_HOLD_REPEAT_MS,
    JOYSTICK_HOLD_MIN_DISTANCE,
)

logger = logging.getLogger(__name__)
JOYSTICK_HOLD_MAP = {binding_id: (axis, direction) for _, binding_id, axis, direction in JOYSTICK_HOLD_DEFINITIONS}
JOYSTICK_HOLD_LIMIT_MARGIN_MM = 0.25
JOYSTICK_HOLD_FALLBACK_DISTANCE_MM = 5000.0
JOYSTICK_HOLD_AXIS_LIMIT_KEYS = {"X": "$130", "Y": "$131", "Z": "$132"}
JOYSTICK_HOLD_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def is_virtual_hold_button(btn) -> bool:
    return getattr(btn, "_hold_axis", None) is not None


def hold_vector_for_binding(app, binding_id: str) -> tuple[str, int] | None:
    info = JOYSTICK_HOLD_MAP.get(binding_id)
    if not info:
        return None
    axis, direction = info
    return axis, direction


def jog_feed_for_axis(app, axis: str) -> float:
    feed = app.jog_feed_z.get() if axis == "Z" else app.jog_feed_xy.get()
    try:
        return float(feed)
    except Exception:
        return 0.0


def _to_mm(value: float, units: str) -> float:
    return float(value) * 25.4 if str(units).lower() == "inch" else float(value)


def _from_mm(value_mm: float, units: str) -> float:
    if str(units).lower() == "inch":
        return float(value_mm) / 25.4
    return float(value_mm)


def _axis_limit_mm(app, axis: str) -> float | None:
    key = JOYSTICK_HOLD_AXIS_LIMIT_KEYS.get(axis)
    if not key:
        return None
    controller = getattr(app, "settings_controller", None)
    if controller is None:
        return None
    raw_data = getattr(controller, "_settings_data", None)
    if not isinstance(raw_data, dict):
        return None
    value = raw_data.get(key)
    if not value or not isinstance(value, tuple):
        return None
    try:
        limit_mm = float(value[0])
    except Exception:
        return None
    if limit_mm <= 0:
        return None
    return limit_mm


def _axis_position_mm(app, axis: str) -> float | None:
    idx = JOYSTICK_HOLD_AXIS_INDEX.get(axis)
    if idx is None:
        return None
    pos = getattr(app, "_mpos_raw", None)
    if not isinstance(pos, (list, tuple)) or len(pos) <= idx:
        return None
    try:
        raw_pos = float(pos[idx])
    except Exception:
        return None
    report_units = getattr(app, "_report_units", None)
    if not report_units:
        try:
            report_units = app.unit_mode.get()
        except Exception:
            report_units = "mm"
    return _to_mm(raw_pos, str(report_units))


def max_hold_distance(app, axis: str, direction: int) -> float:
    """Return one-shot hold jog distance in the app's current unit mode."""
    unit_mode = "mm"
    try:
        unit_mode = str(app.unit_mode.get())
    except Exception:
        pass
    fallback = _from_mm(JOYSTICK_HOLD_FALLBACK_DISTANCE_MM, unit_mode)
    min_distance = max(float(JOYSTICK_HOLD_MIN_DISTANCE), 0.0001)
    limit_mm = _axis_limit_mm(app, axis)
    pos_mm = _axis_position_mm(app, axis)
    if limit_mm is None or pos_mm is None:
        return max(fallback, min_distance)
    if direction > 0:
        remaining_mm = limit_mm - pos_mm
    else:
        remaining_mm = pos_mm
    remaining_mm -= JOYSTICK_HOLD_LIMIT_MARGIN_MM
    if remaining_mm <= 0:
        return min_distance
    distance = _from_mm(remaining_mm, unit_mode)
    return max(float(distance), min_distance)


def _joystick_binding_pressed(app, binding: dict[str, Any] | None, *, release: bool = False) -> bool:
    if not binding:
        return False
    py = app._get_pygame_module()
    if py is not None:
        try:
            py.event.pump()
        except Exception:
            pass
    joy_id = binding.get("joy_id")
    joy = app._joystick_instances.get(joy_id)
    if joy is None:
        return False
    kind = binding.get("kind")
    try:
        if kind == "button":
            return bool(joy.get_button(binding.get("index")))
        if kind == "axis":
            idx = binding.get("index")
            direction = binding.get("direction")
            if idx is None or direction is None:
                return False
            value = float(joy.get_axis(idx))
            threshold = float(
                JOYSTICK_AXIS_RELEASE_THRESHOLD if release else JOYSTICK_AXIS_THRESHOLD
            )
            if direction == 1:
                return value >= threshold
            if direction == -1:
                return value <= -threshold
            return False
        if kind == "hat":
            idx = binding.get("index")
            expected = binding.get("value")
            if idx is None or expected is None:
                return False
            current = joy.get_hat(idx)
            if not isinstance(current, tuple):
                current = tuple(current) if isinstance(current, (list, tuple)) else (current, 0)
            if isinstance(expected, (list, tuple)):
                expected = tuple(expected)
            return bool(current == expected)
    except Exception:
        return False
    return False


def start_hold(app, binding_id: str):
    if not binding_id:
        return
    if app._active_joystick_hold_binding == binding_id:
        return
    stop_hold(app)
    hold_axis = hold_vector_for_binding(app, binding_id)
    if hold_axis is None:
        return
    app._active_joystick_hold_binding = binding_id
    app._joystick_hold_missed_polls = 0
    app._joystick_hold_last_ts = time.monotonic()
    app._joystick_hold_jog_sent = False
    app._send_hold_jog()


def send_hold_jog(app):
    binding_id = app._active_joystick_hold_binding
    if not binding_id:
        return
    if not app.joystick_bindings_enabled.get():
        stop_hold(app, binding_id)
        return
    if getattr(app, "_joystick_hold_jog_sent", False):
        return
    state_text = str(getattr(app, "_machine_state_text", "")).strip().lower()
    if state_text and not (state_text.startswith("idle") or state_text.startswith("jog")):
        stop_hold(app, binding_id)
        return
    try:
        if app.grbl.manual_queue_backpressure():
            app._joystick_hold_last_ts = time.monotonic()
            app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)
            return
    except Exception:
        try:
            if app.grbl.manual_queue_busy():
                app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)
                return
        except Exception:
            pass
    binding = app._joystick_bindings.get(binding_id)
    if not _joystick_binding_pressed(app, binding, release=True):
        missed = getattr(app, "_joystick_hold_missed_polls", 0) + 1
        app._joystick_hold_missed_polls = missed
        if missed >= JOYSTICK_HOLD_MISS_LIMIT:
            stop_hold(app, binding_id)
            return
        app._joystick_hold_last_ts = time.monotonic()
        app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)
        return
    app._joystick_hold_missed_polls = 0
    hold_axis = hold_vector_for_binding(app, binding_id)
    if hold_axis is None:
        stop_hold(app)
        return
    axis, direction = hold_axis
    feed = jog_feed_for_axis(app, axis)
    distance = max_hold_distance(app, axis, direction)
    if distance <= 0:
        stop_hold(app)
        return
    dx = dy = dz = 0.0
    if axis == "X":
        dx = direction * distance
    elif axis == "Y":
        dy = direction * distance
    elif axis == "Z":
        dz = direction * distance
    try:
        app.grbl.jog(dx, dy, dz, feed, app.unit_mode.get())
    except Exception:
        pass
    app._joystick_hold_jog_sent = True


def stop_hold(app, binding_id: str | None = None):
    if binding_id and app._active_joystick_hold_binding and binding_id != app._active_joystick_hold_binding:
        return
    if app._joystick_hold_after_id is not None:
        try:
            app.after_cancel(app._joystick_hold_after_id)
        except Exception:
            pass
        app._joystick_hold_after_id = None
    if app._active_joystick_hold_binding:
        try:
            app.grbl.jog_cancel()
        except Exception:
            pass
        try:
            app.grbl.cancel_pending_jogs()
        except Exception:
            pass
    app._active_joystick_hold_binding = None
    app._joystick_hold_missed_polls = 0
    app._joystick_hold_last_ts = None
    app._joystick_hold_jog_sent = False


def check_release(app):
    active = getattr(app, "_active_joystick_hold_binding", None)
    if not active:
        return
    if not app.joystick_bindings_enabled.get():
        stop_hold(app, active)
        return
    binding = app._joystick_bindings.get(active)
    if _joystick_binding_pressed(app, binding, release=True):
        app._joystick_hold_missed_polls = 0
        return
    missed = getattr(app, "_joystick_hold_missed_polls", 0) + 1
    app._joystick_hold_missed_polls = missed
    if missed >= JOYSTICK_HOLD_MISS_LIMIT:
        stop_hold(app, active)


def binding_pressed(app, binding: dict[str, Any] | None, *, release: bool = False) -> bool:
    return _joystick_binding_pressed(app, binding, release=release)
