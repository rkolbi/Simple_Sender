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
    JOYSTICK_HOLD_MAX_ELAPSED_MULTIPLIER,
    JOYSTICK_HOLD_MISS_LIMIT,
    JOYSTICK_HOLD_POLL_INTERVAL_MS,
    JOYSTICK_HOLD_REPEAT_MS,
    JOYSTICK_HOLD_MIN_DISTANCE,
)

logger = logging.getLogger(__name__)
JOYSTICK_HOLD_MAP = {binding_id: (axis, direction) for _, binding_id, axis, direction in JOYSTICK_HOLD_DEFINITIONS}


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
    app._send_hold_jog()


def send_hold_jog(app):
    binding_id = app._active_joystick_hold_binding
    if not binding_id:
        return
    if not app.joystick_bindings_enabled.get():
        stop_hold(app, binding_id)
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
                app._joystick_hold_last_ts = time.monotonic()
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
    now = time.monotonic()
    last_ts = getattr(app, "_joystick_hold_last_ts", None)
    if last_ts is None:
        elapsed = JOYSTICK_HOLD_REPEAT_MS / 1000.0
    else:
        elapsed = max(0.0, now - last_ts)
    app._joystick_hold_last_ts = now
    max_elapsed = (JOYSTICK_HOLD_REPEAT_MS / 1000.0) * JOYSTICK_HOLD_MAX_ELAPSED_MULTIPLIER
    if elapsed > max_elapsed:
        elapsed = max_elapsed
    distance = (feed / 60.0) * elapsed
    if distance <= 0:
        stop_hold(app)
        return
    distance = max(distance, JOYSTICK_HOLD_MIN_DISTANCE)
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
    app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)


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
