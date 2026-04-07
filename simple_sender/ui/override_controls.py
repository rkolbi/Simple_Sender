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


def _report_override_feedback(app, *, status_text: str, log_text: str) -> None:
    status = getattr(app, "status", None)
    if status is not None:
        try:
            status.config(text=status_text)
        except Exception:
            pass
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("log", log_text))
        except Exception:
            pass


def send_override_realtime(app, command: bytes, *, label: str) -> bool:
    grbl = getattr(app, "grbl", None)
    if grbl is None or not grbl.is_connected():
        _report_override_feedback(
            app,
            status_text=f"{label} unavailable: connect to GRBL first",
            log_text=f"[override] {label} ignored: not connected.",
        )
        return False
    try:
        accepted = grbl.send_realtime(command)
    except Exception as exc:
        _report_override_feedback(
            app,
            status_text=f"{label} failed: {exc}",
            log_text=f"[override] {label} failed: {exc}",
        )
        return False
    if accepted is False:
        _report_override_feedback(
            app,
            status_text=f"{label} failed: realtime command was not sent",
            log_text=f"[override] {label} failed: realtime command was not sent.",
        )
        return False
    return True


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
    result = send_override_delta(app, delta, plus_cmd, minus_cmd)
    if result is False:
        return
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
    if delta == 0:
        return True
    step = 10
    while delta >= step:
        if not send_override_realtime(app, plus_cmd, label="Feed/spindle override"):
            return False
        delta -= step
    while delta <= -step:
        if not send_override_realtime(app, minus_cmd, label="Feed/spindle override"):
            return False
        delta += step
    return True


def set_feed_override_slider_value(app, value):
    app.feed_override_display.set(f"{value}%")
    app._feed_override_slider_last_position = value
    set_override_scale(app, "feed_override_scale", value, "_feed_override_slider_locked")


def set_spindle_override_slider_value(app, value):
    app.spindle_override_display.set(f"{value}%")
    app._spindle_override_slider_last_position = value
    set_override_scale(app, "spindle_override_scale", value, "_spindle_override_slider_locked")


def refresh_override_info(app):
    _ = app
    return None
