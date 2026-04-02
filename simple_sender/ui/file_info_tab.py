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

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.theme_helpers import (
    bind_scrollbar_theme,
    bind_text_display_theme,
    notebook_page_style_name,
    text_display_theme_options,
)
from simple_sender.ui.widgets_tooltips import set_tab_tooltip


def _job_loaded_for_file_info(app) -> bool:
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip().lower()
    if storage_mode and storage_mode != "none":
        return True
    if getattr(app, "_gcode_source", None) is not None:
        return True
    if int(getattr(app, "_gcode_file_line_count", 0) or 0) > 0:
        return True
    if int(getattr(app, "_gcode_total_lines", 0) or 0) > 0:
        return True
    return False


def _fmt_confidence(raw: str | None) -> str:
    return "CONFIDENT" if str(raw or "").strip().lower() == "confident" else "ROUGH"


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None or int(seconds) <= 0:
        return "n/a"
    total_minutes = int(round(float(seconds) / 60.0))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def _fmt_mm(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def _fmt_in(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) / 25.4:.3f}"


def _line_count_text(count: int, known: bool) -> str:
    state = "known" if bool(known) else "estimated"
    return f"{int(max(0, count)):,} ({state})"


def _bounds_dims_mm(app) -> tuple[float | None, float | None, float | None]:
    bounds = getattr(app, "_gcode_bounds_box", None)
    if not isinstance(bounds, dict):
        return (None, None, None)
    try:
        min_x = float(bounds.get("min_x", 0.0) or 0.0)
        max_x = float(bounds.get("max_x", 0.0) or 0.0)
        min_y = float(bounds.get("min_y", 0.0) or 0.0)
        max_y = float(bounds.get("max_y", 0.0) or 0.0)
        min_z = float(bounds.get("min_z", 0.0) or 0.0)
        max_z = float(bounds.get("max_z", 0.0) or 0.0)
    except Exception:
        return (None, None, None)
    return (
        max(0.0, max_x - min_x),
        max(0.0, max_y - min_y),
        max(0.0, max_z - min_z),
    )


def _format_ssmeta_extents(ssmeta: dict[str, str]) -> list[str]:
    rows: list[str] = []
    pairs = (
        ("xmin", "xmax", "X"),
        ("ymin", "ymax", "Y"),
        ("zmin", "zmax", "Z"),
    )
    for prefix, unit_label in (("extents_mm_", "mm"), ("extents_in_", "in")):
        for min_key, max_key, axis in pairs:
            raw_min = ssmeta.get(f"{prefix}{min_key}")
            raw_max = ssmeta.get(f"{prefix}{max_key}")
            if raw_min is None or raw_max is None:
                continue
            rows.append(f"- {axis} ({unit_label}): {raw_min} .. {raw_max}")
    for min_key, max_key, axis in pairs:
        raw_min = ssmeta.get(min_key)
        raw_max = ssmeta.get(max_key)
        if raw_min is None or raw_max is None:
            continue
        rows.append(f"- {axis}: {raw_min} .. {raw_max}")
    for suffix, unit_label in (("_mm", "mm"), ("_in", "in")):
        for min_key, max_key, axis in pairs:
            raw_min = ssmeta.get(f"{min_key}{suffix}")
            raw_max = ssmeta.get(f"{max_key}{suffix}")
            if raw_min is None or raw_max is None:
                continue
            rows.append(f"- {axis} ({unit_label}): {raw_min} .. {raw_max}")
    return rows


def _first(ssmeta: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(ssmeta.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _material_size_text(ssmeta: dict[str, str]) -> str:
    direct_mm = _first(ssmeta, "material_size_mm")
    direct_in = _first(ssmeta, "material_size_in")
    if direct_mm or direct_in:
        if direct_mm and direct_in:
            return f"{direct_mm} / {direct_in}"
        return direct_mm or direct_in
    mm_xyz = (
        _first(ssmeta, "material_size_mm_x"),
        _first(ssmeta, "material_size_mm_y"),
        _first(ssmeta, "material_size_mm_z"),
    )
    in_xyz = (
        _first(ssmeta, "material_size_in_x"),
        _first(ssmeta, "material_size_in_y"),
        _first(ssmeta, "material_size_in_z"),
    )
    if any(mm_xyz):
        return f"X={mm_xyz[0] or 'n/a'} Y={mm_xyz[1] or 'n/a'} Z={mm_xyz[2] or 'n/a'} mm"
    if any(in_xyz):
        return f"X={in_xyz[0] or 'n/a'} Y={in_xyz[1] or 'n/a'} Z={in_xyz[2] or 'n/a'} in"
    return ""


def _origin_text(ssmeta: dict[str, str]) -> str:
    return ", ".join(
        part
        for part in (
            f"xy={_first(ssmeta, 'origin_xy')}" if _first(ssmeta, "origin_xy") else "",
            f"z={_first(ssmeta, 'origin_z')}" if _first(ssmeta, "origin_z") else "",
            f"x_pos={_first(ssmeta, 'origin_x_pos')}" if _first(ssmeta, "origin_x_pos") else "",
            f"y_pos={_first(ssmeta, 'origin_y_pos')}" if _first(ssmeta, "origin_y_pos") else "",
            _first(ssmeta, "origin"),
        )
        if part
    )


def _home_text(ssmeta: dict[str, str]) -> str:
    parts: list[str] = []
    for axis in ("x", "y", "z"):
        value = _first(ssmeta, f"home_{axis}")
        if value:
            parts.append(f"{axis.upper()}={value}")
    safez = _first(ssmeta, "home_safez", "safez")
    if safez:
        parts.append(f"safez={safez}")
    return ", ".join(parts)


def _ordered_ssmeta_values(
    ssmeta: dict[str, str], *, list_key: str, fallback_keys: tuple[str, ...]
) -> list[str]:
    raw_list = _first(ssmeta, list_key)
    if raw_list:
        values = [part.strip() for part in raw_list.splitlines() if part.strip()]
        if values:
            return values
    fallback = _first(ssmeta, *fallback_keys)
    return [fallback] if fallback else []


def ssmeta_toolpaths(ssmeta: dict[str, str]) -> list[str]:
    return _ordered_ssmeta_values(
        ssmeta,
        list_key="__ssmeta_toolpaths_list",
        fallback_keys=("toolpaths_output", "toolpaths"),
    )


def ssmeta_tools(ssmeta: dict[str, str]) -> list[str]:
    return _ordered_ssmeta_values(
        ssmeta,
        list_key="__ssmeta_tools_list",
        fallback_keys=("tools_used", "tools"),
    )


def _append_ssmeta_list_section(lines: list[str], title: str, values: list[str]) -> None:
    if not values:
        return
    lines.append("")
    lines.append(f"- {title}:")
    lines.extend(f"  - {value}" for value in values)


def render_file_info_text(app) -> str:
    job_loaded = _job_loaded_for_file_info(app)
    ssmeta = getattr(app, "_gcode_ssmeta", None)
    ssmeta_map = dict(ssmeta) if isinstance(ssmeta, dict) else {}
    ssmeta_present = bool(getattr(app, "_gcode_ssmeta_present", False) and ssmeta_map)
    if not job_loaded and not ssmeta_present:
        return ""

    lines: list[str] = []
    lines.append(f"SSMETA: {'Present' if ssmeta_present else 'Not found'}")
    lines.append("")
    lines.append("Header Metadata (SSMETA)")
    if ssmeta_present:
        material_size = _material_size_text(ssmeta_map)
        origin = _origin_text(ssmeta_map)
        home = _home_text(ssmeta_map)
        key_rows = (
            ("product", "Product"),
            ("product_version", "Product version"),
            ("date", "Date"),
            ("time", "Time"),
            ("file_name", "File name"),
            ("filename", "File name"),
            ("file", "File"),
            ("units", "Units"),
            ("plane", "Plane"),
            ("abs", "Abs mode"),
            ("notes", "Notes"),
        )
        seen_labels: set[str] = set()
        if material_size:
            lines.append(f"- Material size: {material_size}")
            seen_labels.add("Material size (mm)")
            seen_labels.add("Material size (in)")
        if origin:
            lines.append(f"- Origin: {origin}")
            seen_labels.add("Origin XY")
            seen_labels.add("Origin Z")
            seen_labels.add("Origin")
        if home:
            lines.append(f"- Home/SafeZ: {home}")
            seen_labels.add("Home")
            seen_labels.add("Safe Z")
        for key, label in key_rows:
            value = str(ssmeta_map.get(key, "") or "").strip()
            if not value or label in seen_labels:
                continue
            lines.append(f"- {label}: {value}")
            seen_labels.add(label)
        _append_ssmeta_list_section(lines, "Toolpaths", ssmeta_toolpaths(ssmeta_map))
        _append_ssmeta_list_section(lines, "Tools", ssmeta_tools(ssmeta_map))
        extents_rows = _format_ssmeta_extents(ssmeta_map)
        if extents_rows:
            lines.append("")
            lines.append("- Extents:")
            lines.extend(extents_rows)
    else:
        lines.append("- Not available")

    lines.append("")
    lines.append("File / Scan Metrics")
    file_size = int(getattr(app, "_gcode_file_size_bytes", 0) or 0)
    total_lines = int(getattr(app, "_gcode_file_line_count", 0) or 0)
    total_known = bool(getattr(app, "_gcode_file_line_count_known", False))
    exec_lines = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    exec_known = bool(getattr(app, "_gcode_executable_lines_known", False))
    motion_lines = int(getattr(app, "_gcode_motion_lines", 0) or 0)
    motion_known = bool(getattr(app, "_gcode_motion_lines_known", False))
    estimate_sec = getattr(app, "_gcode_estimated_job_time_sec", None)
    estimate_conf = _fmt_confidence(
        getattr(app, "_estimate_confidence", _fmt_confidence("rough"))
    )
    dim_conf = _fmt_confidence(
        getattr(app, "_gcode_dimensions_confidence", "rough")
    )
    mm_x, mm_y, mm_z = _bounds_dims_mm(app)
    lines.append(f"- File size: {file_size:,} bytes")
    lines.append(f"- Total lines: {_line_count_text(total_lines, total_known)}")
    lines.append(f"- Executable lines: {_line_count_text(exec_lines, exec_known)}")
    lines.append(f"- Motion lines: {_line_count_text(motion_lines, motion_known)}")
    lines.append(
        f"- Estimated Job Time: {_fmt_duration(estimate_sec)} [{estimate_conf}]"
    )
    lines.append(
        "- Job Dimensions: "
        f"{_fmt_mm(mm_x)},{_fmt_mm(mm_y)},{_fmt_mm(mm_z)} mm / "
        f"{_fmt_in(mm_x)},{_fmt_in(mm_y)},{_fmt_in(mm_z)} in "
        f"[{dim_conf}]"
    )
    lines.append(
        "- Dimensions source: "
        f"{str(getattr(app, '_gcode_dimensions_source', 'scan') or 'scan')}"
    )
    lines.append(
        "- Units source: "
        f"{str(getattr(app, '_gcode_units_source', 'scan') or 'scan')}"
    )
    autolevel_snapshot = getattr(app, "_auto_level_prereq_snapshot", None)
    if isinstance(autolevel_snapshot, dict):
        lines.append(
            "- Auto-level prereq: "
            f"bounds_ready={bool(autolevel_snapshot.get('bounds_ready', False))}, "
            f"probe_grid_applicable={bool(autolevel_snapshot.get('probe_grid_applicable', False))}"
        )
    return "\n".join(lines)


def refresh_file_info_tab(app) -> None:
    var = getattr(app, "file_info_var", None)
    text = render_file_info_text(app)
    last = str(getattr(app, "_file_info_last_text", "") or "")
    if text == last:
        return
    app._file_info_last_text = text
    if var is not None:
        try:
            var.set(text)
        except Exception:
            pass
    text_widget = getattr(app, "file_info_text", None)
    if text_widget is not None:
        try:
            text_widget.configure(state="normal")
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", text)
            text_widget.configure(state="disabled")
        except Exception:
            return


def build_file_info_tab(app, notebook) -> None:
    tab = ttk.Frame(notebook, padding=6, style=notebook_page_style_name())
    notebook.add(tab, text="File Info")
    set_tab_tooltip(notebook, tab, "Loaded file metadata and quick-scan metrics.")
    app.file_info_tab = tab
    content = ttk.Frame(tab, style=notebook_page_style_name())
    content.pack(fill="both", expand=True)
    text_widget = tk.Text(
        content,
        wrap="word",
        height=20,
        state="disabled",
        takefocus=0,
    )
    themed_options = text_display_theme_options(app)
    if themed_options:
        text_widget.configure(themed_options)
    text_widget.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(content, orient="vertical", command=text_widget.yview)
    bind_scrollbar_theme(app, scrollbar)
    scrollbar.pack(side="right", fill="y")
    text_widget.configure(yscrollcommand=scrollbar.set)
    bind_text_display_theme(app, text_widget)
    app.file_info_text = text_widget
    app.file_info_scrollbar = scrollbar
    refresh_file_info_tab(app)
