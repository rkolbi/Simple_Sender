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
"""3D toolpath view panel."""

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.ui.toolpath_3d_data import Toolpath3DDataMixin
from simple_sender.ui.toolpath_3d_interaction import Toolpath3DInteractionMixin
from simple_sender.ui.toolpath_3d_rendering import Toolpath3DRenderMixin
from simple_sender.ui.widgets import apply_tooltip, set_kb_id
from simple_sender.utils.constants import (
    TOOLPATH_STREAMING_RENDER_INTERVAL_DEFAULT,
    VIEW_3D_ARC_STEP_DEFAULT,
    VIEW_3D_ARC_STEP_FAST,
    VIEW_3D_ARC_STEP_LARGE,
    VIEW_3D_DEFAULT_AZIMUTH,
    VIEW_3D_DEFAULT_ELEVATION,
    VIEW_3D_DEFAULT_ZOOM,
    VIEW_3D_DRAW_PERCENT_DEFAULT,
    VIEW_3D_FAST_MODE_DURATION,
    VIEW_3D_FULL_PARSE_LIMIT,
    VIEW_3D_LIGHTWEIGHT_PREVIEW_TARGET,
    VIEW_3D_MAX_SEGMENTS_FULL,
    VIEW_3D_MAX_SEGMENTS_INTERACTIVE,
    VIEW_3D_PERF_LOG_THRESHOLD,
    VIEW_3D_PREVIEW_TARGET,
    VIEW_3D_RENDER_INTERVAL,
)

class Toolpath3D(Toolpath3DDataMixin, Toolpath3DRenderMixin, Toolpath3DInteractionMixin, ttk.Frame):
    def __init__(
        self,
        parent,
        on_save_view=None,
        on_load_view=None,
        perf_callback: Callable[[str, float], None] | None = None,
    ):
        super().__init__(parent)
        bg = "SystemButtonFace"
        try:
            bg = parent.cget("background")
        except Exception:
            pass
        self.show_rapid = tk.BooleanVar(value=False)
        self.show_feed = tk.BooleanVar(value=True)
        self.show_arc = tk.BooleanVar(value=False)
        self._draw_percent_default = VIEW_3D_DRAW_PERCENT_DEFAULT
        self._draw_percent = self._draw_percent_default
        self._draw_percent_text = tk.StringVar(value=f"{self._draw_percent}%")

        self.on_save_view = on_save_view
        self.on_load_view = on_load_view

        legend = ttk.Frame(self)
        legend.pack(side="top", fill="x")
        self._legend_frame = legend
        self._perf_frame = None
        self._perf_scale = None
        self._perf_value_label = None
        self._legend_label(legend, "#8a8a8a", "Rapid", self.show_rapid)
        self._legend_label(legend, "#2c6dd2", "Feed", self.show_feed)
        self._legend_label(legend, "#2aa876", "Arc", self.show_arc)
        self.btn_reset_view = ttk.Button(legend, text="Reset View", command=self._reset_view)
        set_kb_id(self.btn_reset_view, "view_reset")
        self.btn_reset_view.pack(side="right", padx=(6, 0))
        self.btn_load_view = ttk.Button(legend, text="Load View", command=self._load_view)
        set_kb_id(self.btn_load_view, "view_load")
        self.btn_load_view.pack(side="right", padx=(6, 0))
        self.btn_save_view = ttk.Button(legend, text="Save View", command=self._save_view)
        set_kb_id(self.btn_save_view, "view_save")
        self.btn_save_view.pack(side="right", padx=(6, 0))
        apply_tooltip(self.btn_save_view, "Save the current 3D view.")
        apply_tooltip(self.btn_load_view, "Load the saved 3D view.")
        apply_tooltip(self.btn_reset_view, "Reset the 3D view.")

        try:
            self.canvas = tk.Canvas(self, background=bg, highlightthickness=0)
        except tk.TclError:
            style = ttk.Style()
            fallback = (
                style.lookup("TFrame", "background")
                or style.lookup("TLabelframe", "background")
                or "#f0f0f0"
            )
            if not fallback or fallback == "SystemButtonFace":
                fallback = "#f0f0f0"
            self.canvas = tk.Canvas(self, background=fallback, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Shift-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Shift-B1-Motion>", self._on_pan)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds = None
        self.position = None
        self.azimuth = VIEW_3D_DEFAULT_AZIMUTH
        self.elevation = VIEW_3D_DEFAULT_ELEVATION
        self.zoom = VIEW_3D_DEFAULT_ZOOM
        self._drag_start = None
        self._pan_start = None
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.enabled = True
        self._pending_lines = None
        self._pending_parsed = None
        self._parse_token = 0
        self._render_pending = False
        self._render_interval = VIEW_3D_RENDER_INTERVAL
        self._streaming_render_interval = TOOLPATH_STREAMING_RENDER_INTERVAL_DEFAULT
        self._last_render_ts = 0.0
        self._visible = True
        self._colors = {
            "rapid": "#8a8a8a",
            "feed": "#2c6dd2",
            "arc": "#2aa876",
        }
        self._preview_target = VIEW_3D_PREVIEW_TARGET
        self._full_parse_limit = VIEW_3D_FULL_PARSE_LIMIT
        self._arc_step_default = VIEW_3D_ARC_STEP_DEFAULT
        self._arc_step_fast = VIEW_3D_ARC_STEP_FAST
        self._arc_step_large = VIEW_3D_ARC_STEP_LARGE
        self._arc_step_rad = self._arc_step_default
        self._arc_step_override_rad = None
        self._max_draw_segments = VIEW_3D_MAX_SEGMENTS_FULL
        self._interactive_max_draw_segments = VIEW_3D_MAX_SEGMENTS_INTERACTIVE
        self._fast_mode = False
        self._fast_mode_after_id = None
        self._fast_mode_duration = VIEW_3D_FAST_MODE_DURATION
        self._render_params = None
        self._position_item = None
        self._last_lines_hash = None
        self._last_segments = None
        self._last_bounds = None
        self._lightweight_mode = False
        self._lightweight_preview_target = VIEW_3D_LIGHTWEIGHT_PREVIEW_TARGET
        self._job_name = ""
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None
        self._perf_callback = perf_callback
        self._perf_threshold = VIEW_3D_PERF_LOG_THRESHOLD
        self._last_gcode_lines = None
        self._full_parse_skipped = False
        self._streaming_mode = False
        self._streaming_prev_render_interval = None
        self._deferred_full_parse = False
        self._overlay_grid: ProbeGrid | None = None

    def _legend_label(self, parent, color, text, var):
        swatch = tk.Label(parent, width=2, background=color)
        swatch.pack(side="left", padx=(0, 4), pady=(2, 2))
        chk = ttk.Checkbutton(parent, text=text, variable=var, command=self._schedule_render)
        chk.pack(side="left", padx=(0, 10))

    def set_performance_controls(
        self,
        perf_var,
        perf_value_var,
        on_move: Callable[[str], Any] | None = None,
        on_commit: Callable[[tk.Event], Any] | None = None,
        on_key_release: Callable[[tk.Event], Any] | None = None,
    ):
        if self._perf_frame is None:
            frame = ttk.Frame(self._legend_frame)
            frame.pack(side="left", padx=(8, 0))
            ttk.Label(frame, text="3D Performance").pack(side="left")
            ttk.Label(frame, text="Min").pack(side="left", padx=(6, 2))
            scale_kwargs: dict[str, Any] = {
                "master": frame,
                "from_": 0,
                "to": 100,
                "orient": "horizontal",
                "length": 140,
                "variable": perf_var,
            }
            if on_move:
                scale_kwargs["command"] = on_move
            scale = ttk.Scale(**scale_kwargs)
            scale.pack(side="left", padx=(4, 4))
            ttk.Label(frame, text="Max").pack(side="left", padx=(2, 6))
            if on_commit:
                scale.bind("<ButtonRelease-1>", on_commit)
            if on_key_release:
                scale.bind("<KeyRelease>", on_key_release)
            value_label = ttk.Label(frame, textvariable=perf_value_var, width=4)
            value_label.pack(side="left")
            apply_tooltip(scale, "Adjust 3D preview quality vs speed.")
            self._perf_frame = frame
            self._perf_scale = scale
            self._perf_value_label = value_label
        else:
            if self._perf_scale is not None:
                scale_kwargs = {"variable": perf_var}
                if on_move:
                    scale_kwargs["command"] = on_move
                self._perf_scale.configure(**scale_kwargs)
            if self._perf_value_label is not None:
                self._perf_value_label.configure(textvariable=perf_value_var)

