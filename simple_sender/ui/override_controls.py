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

from simple_sender.utils.constants import (
    RT_FO_MINUS_10,
    RT_FO_PLUS_10,
    RT_SO_MINUS_10,
    RT_SO_PLUS_10,
)


def normalize_override_slider_value(raw_value, minimum=10, maximum=200):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    value = max(minimum, min(maximum, value))
    rounded = int(round(value / 10.0)) * 10
    if rounded < minimum:
        rounded = minimum
    if rounded > maximum:
        rounded = maximum
    return rounded


def set_override_scale(app, scale_attr, value, lock_attr):
    scale = getattr(app, scale_attr, None)
    if not scale:
        return
    setattr(app, lock_attr, True)
    try:
        scale.set(value)
    finally:
        setattr(app, lock_attr, False)


def handle_override_slider_change(
    app,
    raw_value,
    last_attr,
    scale_attr,
    lock_attr,
    display_var,
    plus_cmd,
    minus_cmd,
):
    if getattr(app, lock_attr):
        return
    target = normalize_override_slider_value(raw_value)
    if target is None:
        return
    last = getattr(app, last_attr, 100)
    if target == last:
        return
    delta = target - last
    send_override_delta(app, delta, plus_cmd, minus_cmd)
    setattr(app, last_attr, target)
    display_var.set(f"{target}%")
    set_override_scale(app, scale_attr, target, lock_attr)


def on_feed_override_slider(app, raw_value):
    handle_override_slider_change(
        app,
        raw_value,
        "_feed_override_slider_last_position",
        "feed_override_scale",
        "_feed_override_slider_locked",
        app.feed_override_display,
        RT_FO_PLUS_10,
        RT_FO_MINUS_10,
    )


def on_spindle_override_slider(app, raw_value):
    handle_override_slider_change(
        app,
        raw_value,
        "_spindle_override_slider_last_position",
        "spindle_override_scale",
        "_spindle_override_slider_locked",
        app.spindle_override_display,
        RT_SO_PLUS_10,
        RT_SO_MINUS_10,
    )


def send_override_delta(app, delta, plus_cmd, minus_cmd):
    if not app.grbl.is_connected() or delta == 0:
        return
    step = 10
    while delta >= step:
        app.grbl.send_realtime(plus_cmd)
        delta -= step
    while delta <= -step:
        app.grbl.send_realtime(minus_cmd)
        delta += step


def set_feed_override_slider_value(app, value):
    app.feed_override_display.set(f"{value}%")
    app._feed_override_slider_last_position = value
    set_override_scale(app, "feed_override_scale", value, "_feed_override_slider_locked")


def set_spindle_override_slider_value(app, value):
    app.spindle_override_display.set(f"{value}%")
    app._spindle_override_slider_last_position = value
    set_override_scale(app, "spindle_override_scale", value, "_spindle_override_slider_locked")


def refresh_override_info(app):
    with app.macro_executor.macro_vars() as macro_vars:
        feed = macro_vars.get("OvFeed", 100)
        spindle = macro_vars.get("OvSpindle", 100)
    app.override_info_var.set(f"Overrides: Feed {feed}% | Spindle {spindle}%")
