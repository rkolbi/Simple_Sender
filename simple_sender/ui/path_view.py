#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Passive Path View popup and preview worker coordination."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Iterable

from simple_sender.path_preview import (
    PreviewBounds,
    PreviewLod,
    PreviewWarningCode,
    build_lod,
    parse_preview_geometry,
)
from simple_sender.ui.theme_helpers import notebook_page_style_name

logger = logging.getLogger(__name__)

POSITION_FRESH_S = 0.75
POSITION_AGING_S = 2.5
POSITION_STALE_S = 8.0
PREVIEW_DETAIL_LIMITS = (2_000, 10_000, 50_000, 200_000)


@dataclass(frozen=True)
class MachineTravelArea:
    width_mm: float
    height_mm: float

    @property
    def bounds(self) -> PreviewBounds:
        return PreviewBounds(0.0, self.width_mm, 0.0, self.height_mm, 0.0, 0.0)


def position_freshness(position: tuple[float, float, float] | None, ts: float, *, now: float | None = None) -> str:
    if position is None or ts <= 0.0:
        return "UNKNOWN"
    current = time.monotonic() if now is None else float(now)
    age = max(0.0, current - float(ts))
    if age <= POSITION_FRESH_S:
        return "FRESH"
    if age <= POSITION_AGING_S:
        return "AGING"
    if age <= POSITION_STALE_S:
        return "STALE"
    return "UNKNOWN"


def known_xy_machine_travel_area(app) -> MachineTravelArea | None:
    settings = getattr(app, "_settings_data", None)
    if not isinstance(settings, dict):
        controller = getattr(app, "settings_controller", None)
        settings = getattr(controller, "_settings_data", None)
    if not isinstance(settings, dict):
        return None
    values: dict[str, float] = {}
    for key in ("$130", "$131"):
        raw = settings.get(key)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0.0:
            return None
        values[key] = value
    return MachineTravelArea(values["$130"], values["$131"])


def display_bounds_for(lod: PreviewLod | None, travel_area: MachineTravelArea | None) -> PreviewBounds | None:
    bounds = lod.bounds if lod is not None else None
    area_bounds = travel_area.bounds if travel_area is not None else None
    if bounds is None:
        return area_bounds
    if area_bounds is None:
        return bounds
    return PreviewBounds(
        min(bounds.min_x, area_bounds.min_x),
        max(bounds.max_x, area_bounds.max_x),
        min(bounds.min_y, area_bounds.min_y),
        max(bounds.max_y, area_bounds.max_y),
        min(bounds.min_z, area_bounds.min_z),
        max(bounds.max_z, area_bounds.max_z),
    )


class PathView(ttk.Frame):
    def __init__(self, master, app) -> None:
        super().__init__(master, padding=8, style=notebook_page_style_name())
        self.app = app
        self._lod: PreviewLod | None = None
        self._travel_area: MachineTravelArea | None = known_xy_machine_travel_area(app)
        self._position: tuple[float, float, float] | None = None
        self._position_ts = 0.0
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._drag_start: tuple[int, int, float, float] | None = None
        self._open_auto_fit_pending = False
        self._open_auto_fit_after_id = None
        self._open_auto_fit_running = False
        self._path_items: list[int] = []
        self._position_items: list[int] = []
        self.status_var = tk.StringVar(master=self, value="No job preview loaded.")
        self.warning_var = tk.StringVar(master=self, value="")
        self.detail_var = tk.IntVar(master=self, value=1)
        self._detail_level = 1
        self.detail_label_var = tk.StringVar(master=self, value="Preview detail: medium")
        self._preview_seed_lines: tuple[str, ...] = ()
        self._selected_tool_section_id: int | None = None
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style=notebook_page_style_name())
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="Fit", command=self.fit_to_view).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.zoom(1.25)).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="-", width=3, command=lambda: self.zoom(0.8)).pack(side="left", padx=(0, 8))
        ttk.Label(toolbar, textvariable=self.detail_label_var).pack(side="left", padx=(0, 4))
        ttk.Scale(toolbar, from_=0, to=3, variable=self.detail_var, command=self._detail_changed, length=120).pack(side="left", padx=(0, 8))
        self.tool_var = tk.StringVar(master=self, value="All paths")
        self.tool_combo = ttk.Combobox(toolbar, textvariable=self.tool_var, state="readonly", width=24, values=("All paths",))
        self.tool_combo.pack(side="left", padx=(0, 8))
        self.tool_combo.bind("<<ComboboxSelected>>", self._tool_changed)
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.canvas = tk.Canvas(self, highlightthickness=1, background="#111417")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<MouseWheel>", self._wheel)
        ttk.Label(self, textvariable=self.warning_var, wraplength=1000).grid(row=2, column=0, sticky="ew", pady=(6, 0))

    def set_lod(self, lod: PreviewLod | None) -> None:
        self._lod = lod
        labels = ["All paths"] + [f"{section.section_id + 1}: {section.label}" for section in (lod.tool_sections if lod else ())]
        tool_combo = getattr(self, "tool_combo", None)
        tool_var = getattr(self, "tool_var", None)
        if tool_combo is not None and tool_var is not None:
            tool_combo.configure(values=labels)
            if tool_var.get() not in labels:
                tool_var.set("All paths")
            selected_label = tool_var.get()
        else:
            selected_label = "All paths"
        selected_id = None
        if selected_label != "All paths":
            try:
                selected_id = int(selected_label.split(":", 1)[0]) - 1
            except (TypeError, ValueError):
                selected_id = None
        self._selected_tool_section_id = next(
            (s.section_id for s in (lod.tool_sections if lod else ()) if s.section_id == selected_id),
            None,
        )
        self._travel_area = known_xy_machine_travel_area(self.app)
        if lod is None:
            if self._travel_area is None:
                self.status_var.set("No job preview loaded. Machine Travel Area unavailable.")
            else:
                self.status_var.set(
                    f"Machine Travel Area: {self._travel_area.width_mm:g} x "
                    f"{self._travel_area.height_mm:g} mm"
                )
            self.warning_var.set("")
        else:
            selected_section = next(
                (section for section in lod.tool_sections if section.section_id == self._selected_tool_section_id),
                None,
            )
            if selected_section is not None:
                scope = f" Selected: {selected_section.label}."
            else:
                scope = ""
            self.status_var.set("Planned Path." + scope)
            self.warning_var.set(_warning_text(lod))
        self.render()

    def _tool_changed(self, _event=None) -> None:
        label = self.tool_var.get()
        self._selected_tool_section_id = next(
            (section.section_id for section in (self._lod.tool_sections if self._lod else ()) if label == f"{section.section_id + 1}: {section.label}"),
            None,
        )
        schedule_path_preview_from_app(
            self.app,
            level=self._detail_level,
            seed_lines=self._preview_seed_lines,
        )

    def _detail_changed(self, raw_value) -> None:
        level = max(0, min(3, int(round(float(raw_value)))))
        self.detail_var.set(level)
        names = ("low", "medium", "high", "maximum")
        self.detail_label_var.set(f"Preview detail: {names[level]}")
        if level == self._detail_level:
            return
        self._detail_level = level
        schedule_path_preview_from_app(self.app, level=level, seed_lines=self._preview_seed_lines)

    def update_reported_position(self, position: tuple[float, float, float] | None, ts: float) -> None:
        self._position = position
        self._position_ts = float(ts or 0.0)
        self._render_position()
        self._schedule_pending_auto_fit_after_render()

    def fit_to_view(self) -> bool:
        width = int(self.canvas.winfo_width())
        height = int(self.canvas.winfo_height())
        if width <= 1 or height <= 1:
            return False
        bounds = display_bounds_for(self._lod, self._travel_area)
        if bounds is None:
            if self._position is not None and position_freshness(self._position, self._position_ts) != "UNKNOWN":
                self._scale = 1.0
                self._offset_x = (width / 2.0) - (float(self._position[0]) * self._scale)
                self._offset_y = (height / 2.0) + (float(self._position[1]) * self._scale)
                self.render()
                return True
            if self._open_auto_fit_pending:
                return False
            self._scale = 1.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            self.render()
            return True
        span_x = max(0.001, bounds.max_x - bounds.min_x)
        span_y = max(0.001, bounds.max_y - bounds.min_y)
        margin = 32.0
        self._scale = max(0.001, min((width - margin * 2) / span_x, (height - margin * 2) / span_y))
        self._offset_x = margin - bounds.min_x * self._scale
        self._offset_y = height - margin + bounds.min_y * self._scale
        self.render()
        return True

    def zoom(self, factor: float) -> None:
        self._scale = max(0.001, min(1000.0, self._scale * float(factor)))
        self.render()

    def render(self) -> None:
        for item in self._path_items:
            self.canvas.delete(item)
        self._path_items.clear()
        lod = self._lod
        travel_area = self._travel_area
        if (lod is None or not lod.batches) and travel_area is None:
            self.canvas.delete("all")
            self._path_items.append(
                self.canvas.create_text(
                    max(20, self.canvas.winfo_width() // 2),
                    max(20, self.canvas.winfo_height() // 2),
                    text="Machine Travel Area unavailable",
                    fill="#d6dde3",
                )
            )
            self._render_position()
            self._schedule_pending_auto_fit_after_render()
            return
        self.canvas.delete("all")
        self._draw_machine_travel_area(travel_area)
        if lod is not None:
            self._draw_bounds(lod.bounds)
            for batch in lod.batches:
                selected_section = next(
                    (section for section in lod.tool_sections if section.section_id == self._selected_tool_section_id),
                    None,
                )
                if selected_section is not None and (
                    batch.source_end < selected_section.source_start
                    or batch.source_start > selected_section.source_end
                ):
                    continue
                coords: list[float] = []
                for x, y in batch.points:
                    sx, sy = self._to_screen(x, y)
                    coords.extend((sx, sy))
                if len(coords) < 4:
                    continue
                color = "#7fb7d8" if batch.kind == "feed" else "#6f7780"
                dash = () if batch.kind == "feed" else (5, 5)
                width = 2 if batch.kind == "feed" else 1
                self._path_items.append(
                    self.canvas.create_line(*coords, fill=color, width=width, dash=dash)
                )
        self._draw_origin()
        self._render_position()
        self._schedule_pending_auto_fit_after_render()

    def refresh_work_area(self) -> None:
        next_area = known_xy_machine_travel_area(self.app)
        if next_area == self._travel_area:
            return
        self._travel_area = next_area
        self.render()

    def request_open_auto_fit(self) -> None:
        self._open_auto_fit_pending = True
        self.render()

    def _schedule_pending_auto_fit_after_render(self) -> None:
        if self._open_auto_fit_pending and not self._open_auto_fit_running:
            self._schedule_open_auto_fit()

    def _schedule_open_auto_fit(self) -> None:
        if self._open_auto_fit_after_id is not None:
            return

        def _run() -> None:
            self._open_auto_fit_after_id = None
            if not self._open_auto_fit_pending:
                return
            if bool(getattr(self.app, "_path_preview_loading", False)):
                self._schedule_open_auto_fit_after_delay()
                return
            self._open_auto_fit_running = True
            try:
                fit_succeeded = bool(self.fit_to_view())
            finally:
                self._open_auto_fit_running = False
            if fit_succeeded:
                self._open_auto_fit_pending = False

        after_idle = getattr(self, "after_idle", None)
        if callable(after_idle):
            try:
                self._open_auto_fit_after_id = after_idle(_run)
                return
            except tk.TclError:
                self._open_auto_fit_after_id = None
        _run()

    def _schedule_open_auto_fit_after_delay(self) -> None:
        after = getattr(self, "after", None)
        if not callable(after):
            return
        def _retry() -> None:
            self._open_auto_fit_after_id = None
            self._schedule_open_auto_fit()

        try:
            self._open_auto_fit_after_id = after(100, _retry)
        except tk.TclError:
            self._open_auto_fit_after_id = None

    def _draw_bounds(self, bounds: PreviewBounds | None) -> None:
        if bounds is None:
            return
        x1, y1 = self._to_screen(bounds.min_x, bounds.min_y)
        x2, y2 = self._to_screen(bounds.max_x, bounds.max_y)
        self._path_items.append(
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#3f5968", width=1)
        )

    def _draw_machine_travel_area(self, travel_area: MachineTravelArea | None) -> None:
        if travel_area is None:
            return
        bounds = travel_area.bounds
        x1, y1 = self._to_screen(bounds.min_x, bounds.min_y)
        x2, y2 = self._to_screen(bounds.max_x, bounds.max_y)
        self._path_items.append(
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#8c9aa3", width=2)
        )
        label_x, label_y = self._to_screen(bounds.min_x, bounds.max_y)
        self._path_items.append(
            self.canvas.create_text(
                label_x + 8,
                label_y + 14,
                text="Machine Travel Area",
                fill="#d6dde3",
                anchor="w",
            )
        )

    def _draw_origin(self) -> None:
        x, y = self._to_screen(0.0, 0.0)
        self._path_items.append(self.canvas.create_line(x - 8, y, x + 8, y, fill="#d2c16f"))
        self._path_items.append(self.canvas.create_line(x, y - 8, x, y + 8, fill="#d2c16f"))

    def _render_position(self) -> None:
        for item in self._position_items:
            self.canvas.delete(item)
        self._position_items.clear()
        state = position_freshness(self._position, self._position_ts)
        if state == "UNKNOWN" or self._position is None:
            return
        x, y = self._to_screen(self._position[0], self._position[1])
        colors = {
            "FRESH": ("#f6f7f8", "#2aa7ff"),
            "AGING": ("#c8d1d9", "#8fb7d1"),
            "STALE": ("#777f86", "#777f86"),
        }
        fill, outline = colors.get(state, colors["STALE"])
        self._position_items.append(self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=fill, outline=outline, width=2))
        if state != "FRESH":
            self._position_items.append(self.canvas.create_oval(x - 13, y - 13, x + 13, y + 13, outline=outline, width=1))
        if state == "STALE":
            self._position_items.append(self.canvas.create_text(x + 48, y, text="Position Stale", fill="#d6dde3", anchor="w"))

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (float(x) * self._scale + self._offset_x, self._offset_y - float(y) * self._scale)

    def _start_pan(self, event) -> None:
        self._drag_start = (int(event.x), int(event.y), self._offset_x, self._offset_y)

    def _pan(self, event) -> None:
        if self._drag_start is None:
            return
        sx, sy, ox, oy = self._drag_start
        self._offset_x = ox + int(event.x) - sx
        self._offset_y = oy + int(event.y) - sy
        self.render()

    def _wheel(self, event) -> None:
        self.zoom(1.1 if int(getattr(event, "delta", 0) or 0) > 0 else 0.9)


def _warning_text(lod: PreviewLod) -> str:
    codes = {warning.code for warning in lod.warnings}
    if not codes:
        return ""
    labels = {
        PreviewWarningCode.UNSUPPORTED_PLANE: "Preview Limited: non-XY plane motion is not shown.",
        PreviewWarningCode.UNSUPPORTED_COORDINATE_CHANGE: "Preview Limited: coordinate-changing commands are present.",
        PreviewWarningCode.UNSUPPORTED_ARC_CENTER_MODE: "Preview Limited: unsupported arc-center mode is present.",
        PreviewWarningCode.MALFORMED_WORD: "Preview Limited: malformed G-code words were skipped.",
        PreviewWarningCode.MALFORMED_ARC: "Preview Limited: one or more arcs could not be drawn.",
        PreviewWarningCode.NONFINITE_VALUE: "Preview Limited: non-finite numeric values were skipped.",
        PreviewWarningCode.LOD_LIMITED: "Preview Limited: large job shown with reduced detail.",
        PreviewWarningCode.SOURCE_SAMPLED: "Preview Limited: large job preview uses sampled source lines; it is not a complete toolpath.",
    }
    return " ".join(labels[code] for code in labels if code in codes)


def build_path_view_panel(app, parent) -> PathView:
    view = PathView(parent, app)
    app.path_view = view
    current = getattr(app, "_path_preview_lod", None)
    if current is not None:
        view.set_lod(current)
    else:
        view.set_lod(None)
    pos = getattr(app, "_wpos_raw", None)
    ts = float(getattr(app, "_status_last_installed_coordinate_ts", 0.0) or 0.0)
    if isinstance(pos, tuple) and len(pos) >= 3:
        view.update_reported_position((float(pos[0]), float(pos[1]), float(pos[2])), ts)
    view.request_open_auto_fit()
    return view


def prepare_path_view_for_open(app) -> None:
    view = getattr(app, "path_view", None)
    if view is None:
        return
    if not callable(getattr(view, "request_open_auto_fit", None)):
        return
    current = getattr(app, "_path_preview_lod", None)
    view.set_lod(current)
    pos = getattr(app, "_wpos_raw", None)
    ts = float(getattr(app, "_status_last_installed_coordinate_ts", 0.0) or 0.0)
    if isinstance(pos, tuple) and len(pos) >= 3:
        view.update_reported_position((float(pos[0]), float(pos[1]), float(pos[2])), ts)
    else:
        view.update_reported_position(None, 0.0)
    view.request_open_auto_fit()


def clear_path_preview(app) -> None:
    app._path_preview_generation = int(getattr(app, "_path_preview_generation", 0) or 0) + 1
    app._path_preview_lod = None
    app._path_preview_loading = False
    view = getattr(app, "path_view", None)
    if view is not None and callable(getattr(view, "set_lod", None)):
        view.set_lod(None)


def _post_ui(app, callback) -> None:
    if threading.current_thread() is threading.main_thread():
        after = getattr(app, "after", None)
        if callable(after):
            try:
                after(0, callback)
                return
            except Exception:
                logger.exception("Failed scheduling Path View callback with app.after")
        callback()
        return
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(callback)
            return
        except Exception:
            logger.exception("Failed posting Path View callback through _post_ui_thread")
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return
        except Exception:
            logger.exception("Failed posting Path View callback through ui_q")


def schedule_path_preview(
    app,
    lines: Iterable[str],
    *,
    source_complete: bool = True,
    source_line_count: int | None = None,
    sampled_source: bool = False,
    tool_sections_override=None,
) -> None:
    app._path_preview_generation = int(getattr(app, "_path_preview_generation", 0) or 0) + 1
    app._path_preview_loading = True
    generation = int(app._path_preview_generation)
    source_lines = tuple(lines)
    app._path_preview_seed_lines = source_lines

    def keep_running() -> bool:
        return generation == int(getattr(app, "_path_preview_generation", 0) or 0)

    def worker() -> None:
        try:
            preview_lines = list(source_lines)
            source_path = str(getattr(app, "_path_preview_source_path", "") or "")
            if not source_complete and source_path and not sampled_source:
                preview_lines = _sample_snapshot_lines_by_tool(
                    source_path,
                    total_lines=int(getattr(app, "_path_preview_source_line_count", 0) or len(preview_lines)),
                    limit=max(1, len(preview_lines)),
                )
            geometry = parse_preview_geometry(preview_lines, keep_running=keep_running)
            geometry = type(geometry)(
                segments=geometry.segments,
                bounds=geometry.bounds,
                warnings=geometry.warnings,
                motion_count=geometry.motion_count,
                units=geometry.units,
                metadata={
                    **geometry.metadata,
                    "source_complete": bool(source_complete),
                    "source_line_count": source_line_count,
                    "preview_line_count": len(preview_lines),
                    "tool_sections": (
                        tuple(tool_sections_override)
                        if tool_sections_override is not None
                        else geometry.metadata.get("tool_sections", ())
                    ),
                },
            )
            lod = build_lod(geometry)
        except Exception:
            logger.exception("Path preview parse failed")
            lod = None

        def apply() -> None:
            if generation != int(getattr(app, "_path_preview_generation", 0) or 0):
                return
            app._path_preview_loading = False
            if lod is None:
                app._path_preview_lod = getattr(app, "_path_preview_lod", None)
            if lod is not None and (lod.batches or lod.bounds is not None):
                app._path_preview_lod = lod
            view = getattr(app, "path_view", None)
            if lod is not None and (lod.batches or lod.bounds is not None) and view is not None and callable(getattr(view, "set_lod", None)):
                view.set_lod(lod)

        _post_ui(app, apply)

    threading.Thread(target=worker, daemon=True).start()


def _sample_snapshot_lines(path: str, *, total_lines: int, limit: int) -> list[str]:
    stride = max(1, (max(1, int(total_lines)) + max(1, int(limit)) - 1) // max(1, int(limit)))
    selected: list[str] = []
    pending_tool_line: tuple[int, str] | None = None
    tool_change_re = re.compile(r"(?:\bM0*6\b|^\s*TC\s*:)", re.IGNORECASE)
    tool_select_re = re.compile(r"\bT\s*[-+]?\d+(?:\.0+)?\b", re.IGNORECASE)
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for index, line in enumerate(handle):
            text = line.rstrip("\r\n")
            keep_for_tool_change = bool(tool_change_re.search(text))
            if tool_select_re.search(text):
                pending_tool_line = (index, text)
            if keep_for_tool_change and pending_tool_line is not None:
                pending_index, pending_text = pending_tool_line
                if pending_index != index and pending_text not in selected:
                    selected.append(pending_text)
                pending_tool_line = None
            if index % stride == 0 or index == total_lines - 1 or keep_for_tool_change:
                if text:
                    selected.append(text)
    return selected


def _collect_tc_markers(path: str) -> list[str]:
    markers: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for line in handle:
            text = line.rstrip("\r\n")
            if text.lstrip().upper().startswith("TC:"):
                markers.append(text)
    return markers


def _sample_snapshot_lines_by_tool(path: str, *, total_lines: int, limit: int) -> list[str]:
    sections: list[list[tuple[int, str]]] = [[]]
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for line_index, raw in enumerate(handle):
            text = raw.rstrip("\r\n")
            if text.lstrip().upper().startswith("TC:"):
                sections.append([(line_index, text)])
            elif text:
                sections[-1].append((line_index, text))
    if len(sections) == 1:
        return _sample_snapshot_lines(path, total_lines=total_lines, limit=limit)
    per_section = max(1, int(limit) // len(sections))
    output: list[str] = []
    for section in sections:
        marker = section[:1] if section and section[0][1].lstrip().upper().startswith("TC:") else []
        body = section[1:] if marker else section
        stride = max(1, (len(body) + per_section - 1) // per_section)
        output.extend(marker)
        output.extend(line for index, line in enumerate(body) if index % stride == 0 or index == len(body) - 1)
    return output


def _sample_snapshot_tool_section(path: str, *, section_id: int, limit: int) -> list[tuple[int, str]]:
    sections: list[list[tuple[int, str]]] = [[]]
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for line_index, raw in enumerate(handle):
            text = raw.rstrip("\r\n")
            if text.lstrip().upper().startswith("TC:"):
                sections.append([(line_index, text)])
            elif text:
                sections[-1].append((line_index, text))
    # Section ID 0 is the first TC section; sections[0] is preamble content.
    source_index = section_id + 1
    if source_index < 0 or source_index >= len(sections):
        return []
    section = sections[source_index]
    marker = section[:1] if section and section[0][1].lstrip().upper().startswith("TC:") else []
    body = section[1:] if marker else section
    stride = max(1, (len(body) + max(1, limit) - 1) // max(1, limit))
    return marker + [line for index, line in enumerate(body) if index % stride == 0 or index == len(body) - 1]


def schedule_path_preview_from_app(app, *, level: int, seed_lines: Iterable[str] = ()) -> None:
    level = max(0, min(3, int(level)))
    limit = PREVIEW_DETAIL_LIMITS[level]
    if not tuple(seed_lines):
        seed_lines = getattr(app, "_path_preview_seed_lines", ())
    path = str(getattr(app, "_path_preview_source_path", "") or "")
    total_lines = int(getattr(app, "_path_preview_source_line_count", 0) or 0)
    selected_section_id = getattr(getattr(app, "path_view", None), "_selected_tool_section_id", None)
    if path and selected_section_id is not None:
        def selected_worker() -> None:
            try:
                lines = _sample_snapshot_tool_section(path, section_id=int(selected_section_id), limit=limit)
            except (OSError, UnicodeError):
                logger.exception("Failed reading selected Path View tool section")
                lines = list(seed_lines)
            schedule_path_preview(
                app,
                lines,
                source_complete=False,
                source_line_count=total_lines,
                sampled_source=True,
                tool_sections_override=getattr(getattr(app, "path_view", None), "_lod", None).tool_sections
                if getattr(getattr(app, "path_view", None), "_lod", None) is not None
                else None,
            )
        threading.Thread(target=selected_worker, daemon=True).start()
        return
    if path and total_lines > limit:
        def worker() -> None:
            try:
                lines = _sample_snapshot_lines_by_tool(path, total_lines=total_lines, limit=limit)
            except (OSError, UnicodeError):
                logger.exception("Failed reading Path View detail sample")
                lines = list(seed_lines)
            schedule_path_preview(
                app,
                lines,
                source_complete=False,
                source_line_count=total_lines,
                sampled_source=True,
            )
        threading.Thread(target=worker, daemon=True).start()
        return
    schedule_path_preview(
        app,
        seed_lines,
        source_complete=not bool(path),
        source_line_count=total_lines or len(tuple(seed_lines)),
    )


def update_path_view_position(app) -> None:
    view = getattr(app, "path_view", None)
    if view is None or not callable(getattr(view, "update_reported_position", None)):
        return
    refresher = getattr(view, "refresh_work_area", None)
    if callable(refresher):
        refresher()
    pos = getattr(app, "_wpos_raw", None)
    ts = float(getattr(app, "_status_last_installed_coordinate_ts", 0.0) or 0.0)
    if isinstance(pos, tuple) and len(pos) >= 3:
        view.update_reported_position((float(pos[0]), float(pos[1]), float(pos[2])), ts)
    else:
        view.update_reported_position(None, 0.0)
