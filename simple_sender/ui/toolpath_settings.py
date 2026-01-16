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
# SPDX-License-Identifier: GPL-3.0-or-later

import math
import tkinter as tk


def init_toolpath_settings(app):
    app._toolpath_full_limit_default = 40000
    app._toolpath_interactive_limit_default = 5000
    app._toolpath_full_limit_min = 5000
    app._toolpath_interactive_limit_min = 1000
    app._toolpath_performance_default = 50.0
    app._toolpath_arc_detail_min = 1.0
    app._toolpath_arc_detail_max = 45.0
    app._toolpath_arc_detail_default = math.degrees(math.pi / 18)
    app._toolpath_streaming_render_interval_default = 0.25
    try:
        saved_full = int(app.settings.get("toolpath_full_limit", app._toolpath_full_limit_default))
    except Exception:
        saved_full = app._toolpath_full_limit_default
    try:
        saved_interactive = int(
            app.settings.get("toolpath_interactive_limit", app._toolpath_interactive_limit_default)
        )
    except Exception:
        saved_interactive = app._toolpath_interactive_limit_default
    try:
        saved_arc = float(app.settings.get("toolpath_arc_detail_deg", app._toolpath_arc_detail_default))
    except Exception:
        saved_arc = app._toolpath_arc_detail_default
    saved_arc = max(app._toolpath_arc_detail_min, min(saved_arc, app._toolpath_arc_detail_max))
    app.toolpath_full_limit = tk.StringVar(value=str(saved_full))
    app.toolpath_interactive_limit = tk.StringVar(value=str(saved_interactive))
    app.toolpath_arc_detail = tk.DoubleVar(value=saved_arc)
    app.toolpath_lightweight = tk.BooleanVar(value=app.settings.get("toolpath_lightweight", False))
    try:
        streaming_interval = float(
            app.settings.get(
                "toolpath_streaming_render_interval",
                app._toolpath_streaming_render_interval_default,
            )
        )
    except Exception:
        streaming_interval = app._toolpath_streaming_render_interval_default
    streaming_interval = max(0.05, min(2.0, streaming_interval))
    app.toolpath_streaming_render_interval = tk.DoubleVar(value=streaming_interval)
    app._toolpath_arc_detail_value = tk.StringVar(value=f"{saved_arc:.1f}°")
    saved_draw_percent = app.settings.get("toolpath_draw_percent", None)
    saved_perf = app.settings.get("toolpath_performance", None)
    if saved_perf is None:
        perf_candidates = []
        if saved_draw_percent is not None:
            try:
                perf_candidates.append(float(saved_draw_percent))
            except Exception:
                pass
        if saved_full == 0 or saved_interactive == 0:
            perf_candidates.append(100.0)
        else:
            denom_full = app._toolpath_full_limit_default - app._toolpath_full_limit_min
            if denom_full > 0:
                perf_candidates.append(
                    (saved_full - app._toolpath_full_limit_min) / denom_full * 100.0
                )
            denom_interactive = (
                app._toolpath_interactive_limit_default - app._toolpath_interactive_limit_min
            )
            if denom_interactive > 0:
                perf_candidates.append(
                    (saved_interactive - app._toolpath_interactive_limit_min)
                    / denom_interactive * 100.0
                )
        denom_arc = app._toolpath_arc_detail_max - app._toolpath_arc_detail_min
        if denom_arc > 0:
            perf_candidates.append(
                (app._toolpath_arc_detail_max - saved_arc) / denom_arc * 100.0
            )
        if perf_candidates:
            saved_perf = sum(perf_candidates) / len(perf_candidates)
        else:
            saved_perf = app._toolpath_performance_default
    perf = app._clamp_toolpath_performance(saved_perf)
    app.toolpath_performance = tk.DoubleVar(value=perf)
    app._toolpath_performance_value = tk.StringVar(value=f"{perf:.0f}%")
    full_limit, interactive_limit, arc_detail, lightweight, draw_percent = app._toolpath_perf_values(perf)
    app._toolpath_draw_percent = draw_percent
    app.toolpath_full_limit = tk.StringVar(value=str(full_limit))
    app.toolpath_interactive_limit = tk.StringVar(value=str(interactive_limit))
    app.toolpath_arc_detail = tk.DoubleVar(value=arc_detail)
    app.toolpath_lightweight = tk.BooleanVar(value=lightweight)
    app._toolpath_arc_detail_value = tk.StringVar(value=f"{arc_detail:.1f}°")
    app._toolpath_arc_detail_reparse_after_id = None
    app._toolpath_arc_detail_reparse_delay = 300


def toggle_render_3d(app):
    current = bool(app.render3d_enabled.get())
    new_val = not current
    app.render3d_enabled.set(new_val)
    app._refresh_render_3d_toggle_text()
    app.toolpath_panel.set_enabled(new_val)
    if new_val and app._last_gcode_lines:
        app.toolpath_panel.set_gcode_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)

def toolpath_limit_value(app, raw, fallback):
    try:
        value = int(str(raw).strip())
    except Exception:
        value = fallback
    if value < 0:
        value = 0
    return value

def clamp_toolpath_performance(app, value):
    try:
        perf = float(value)
    except Exception:
        perf = app._toolpath_performance_default
    return max(0.0, min(100.0, perf))

def clamp_toolpath_streaming_render_interval(app, value):
    try:
        interval = float(value)
    except Exception:
        interval = app._toolpath_streaming_render_interval_default
    return max(0.05, min(2.0, interval))

def apply_toolpath_streaming_render_interval(app, _event=None):
    interval = app._clamp_toolpath_streaming_render_interval(
        app.toolpath_streaming_render_interval.get()
    )
    app.toolpath_streaming_render_interval.set(interval)
    app.toolpath_panel.set_streaming_render_interval(interval)

def toolpath_perf_values(app, perf: float):
    perf = app._clamp_toolpath_performance(perf)
    if perf >= 100.0:
        full_limit = 0
        interactive_limit = 0
    else:
        full_limit = int(round(
            app._toolpath_full_limit_min
            + (app._toolpath_full_limit_default - app._toolpath_full_limit_min) * (perf / 100.0)
        ))
        interactive_limit = int(round(
            app._toolpath_interactive_limit_min
            + (
                app._toolpath_interactive_limit_default - app._toolpath_interactive_limit_min
            ) * (perf / 100.0)
        ))
    arc_detail = (
        app._toolpath_arc_detail_max
        - (app._toolpath_arc_detail_max - app._toolpath_arc_detail_min) * (perf / 100.0)
    )
    lightweight = perf < 40.0
    draw_percent = max(5, int(round(perf)))
    return full_limit, interactive_limit, arc_detail, lightweight, draw_percent

def on_toolpath_performance_move(app, value):
    try:
        perf = float(value)
    except Exception:
        perf = app.toolpath_performance.get()
    perf = app._clamp_toolpath_performance(perf)
    app._toolpath_performance_value.set(f"{perf:.0f}%")

def on_toolpath_performance_key_release(app, event):
    if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
        app._apply_toolpath_performance()

def apply_toolpath_performance(app, _event=None):
    perf = app._clamp_toolpath_performance(app.toolpath_performance.get())
    app.toolpath_performance.set(perf)
    app._toolpath_performance_value.set(f"{perf:.0f}%")
    full_limit, interactive_limit, arc_detail, lightweight, draw_percent = app._toolpath_perf_values(perf)
    app._toolpath_draw_percent = draw_percent
    app.toolpath_full_limit.set(str(full_limit))
    app.toolpath_interactive_limit.set(str(interactive_limit))
    app.toolpath_arc_detail.set(arc_detail)
    app._toolpath_arc_detail_value.set(f"{arc_detail:.1f}°")
    app.toolpath_lightweight.set(lightweight)
    app.toolpath_panel.set_draw_limits(full_limit, interactive_limit)
    app.toolpath_panel.set_arc_detail(arc_detail)
    app.toolpath_panel.set_lightweight(lightweight)
    app.toolpath_panel.set_draw_percent(draw_percent)
    if app._last_gcode_lines:
        if app._stream_state in ("running", "paused"):
            app._toolpath_reparse_deferred = True
        else:
            app.toolpath_panel.reparse_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)

def apply_toolpath_draw_limits(app, _event=None):
    full = app._toolpath_limit_value(app.toolpath_full_limit.get(), app._toolpath_full_limit_default)
    interactive = app._toolpath_limit_value(
        app.toolpath_interactive_limit.get(), app._toolpath_interactive_limit_default
    )
    app.toolpath_full_limit.set(str(full))
    app.toolpath_interactive_limit.set(str(interactive))
    app.toolpath_panel.set_draw_limits(full, interactive)

def on_arc_detail_scale_move(app, value):
    try:
        deg = float(value)
    except Exception:
        deg = app.toolpath_arc_detail.get()
    app._toolpath_arc_detail_value.set(f"{deg:.1f}°")

def on_arc_detail_scale_key_release(app, event):
    if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
        app._apply_toolpath_arc_detail()

def clamp_arc_detail(app, value):
    try:
        deg = float(value)
    except Exception:
        deg = app._toolpath_arc_detail_default
    deg = max(app._toolpath_arc_detail_min, min(deg, app._toolpath_arc_detail_max))
    return deg

def apply_toolpath_arc_detail(app, _event=None):
    deg = app._clamp_arc_detail(app.toolpath_arc_detail.get())
    app.toolpath_arc_detail.set(deg)
    app._toolpath_arc_detail_value.set(f"{deg:.1f}°")
    app.toolpath_panel.set_arc_detail(deg)
    app._schedule_toolpath_arc_detail_reparse()

def schedule_toolpath_arc_detail_reparse(app):
    if app._toolpath_arc_detail_reparse_after_id:
        try:
            app.after_cancel(app._toolpath_arc_detail_reparse_after_id)
        except Exception:
            pass
    app._toolpath_arc_detail_reparse_after_id = app.after(
        app._toolpath_arc_detail_reparse_delay, app._run_toolpath_arc_detail_reparse
    )

def run_toolpath_arc_detail_reparse(app):
    app._toolpath_arc_detail_reparse_after_id = None
    if app._last_gcode_lines:
        if app._stream_state in ("running", "paused"):
            app._toolpath_reparse_deferred = True
            return
        app.toolpath_panel.reparse_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)

def on_toolpath_lightweight_change(app):
    app.toolpath_panel.set_lightweight(bool(app.toolpath_lightweight.get()))
    if app._last_gcode_lines:
        if app._stream_state in ("running", "paused"):
            app._toolpath_reparse_deferred = True
            return
        app.toolpath_panel.set_gcode_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)

def save_3d_view(app):
    view = app.toolpath_panel.get_view_state()
    if not view:
        return
    app.settings["view_3d"] = view
    app.status.config(text="3D view saved")

def load_3d_view(app, show_status: bool = True):
    view = app.settings.get("view_3d")
    if not view:
        return
    app.toolpath_panel.apply_view_state(view)
    if show_status:
        app.status.config(text="3D view loaded")
