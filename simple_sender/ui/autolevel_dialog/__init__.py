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

import itertools
import math
import os
import tempfile
import threading
import time
import tkinter as tk
from collections.abc import Callable, Iterable
from typing import Any, cast
from tkinter import ttk, messagebox, simpledialog

from simple_sender.autolevel.grid import AdaptiveGridSpec, ProbeBounds, ProbeGrid, build_adaptive_grid
from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import (
    LevelFileResult,
    level_gcode_file,
    level_gcode_lines,
    write_gcode_lines,
)
from simple_sender.gcode_parser import clean_gcode_line, split_gcode_lines, split_gcode_lines_stream
from simple_sender.autolevel.probe_runner import ProbeRunSettings
from .profiles import (
    _merge_auto_level_job_prefs,
    _select_auto_level_profile,
)
from .helpers import (
    parse_float_var,
    parse_int_optional_var,
    probe_connection_state,
    safe_float_text,
    update_stats_summary,
    validate_probe_settings_vars,
)
from .io import (
    load_height_map as load_height_map_file,
    save_height_map as save_height_map_file,
    save_leveled as save_leveled_job,
)
from .prefs import pref_float, pref_interp
from simple_sender.ui.dro import convert_units
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.widgets import apply_tooltip, attach_numeric_keypad
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import (
    AUTOLEVEL_SPACING_MIN,
    AUTOLEVEL_START_STATE_POLL_MS,
    MAX_LINE_LENGTH,
)

def _find_overlong_lines(
    lines: list[str],
    *,
    fallback_index: int | None = None,
    fallback_len: int | None = None,
    fallback_count: int | None = None,
) -> tuple[int, int | None, int | None]:
    too_long = 0
    first_idx = None
    first_len = None
    for idx, line in enumerate(lines):
        line_len = len(line.encode("utf-8")) + 1
        if line_len > MAX_LINE_LENGTH:
            too_long += 1
            if first_idx is None:
                first_idx = idx
                first_len = line_len
    if too_long == 0 and fallback_index is not None:
        too_long = fallback_count if fallback_count is not None else 1
        first_idx = fallback_index
        first_len = fallback_len
    return too_long, first_idx, first_len


def _format_overlong_error(
    lines: list[str],
    *,
    fallback_index: int | None = None,
    fallback_len: int | None = None,
    fallback_count: int | None = None,
    line_offset: int = 0,
) -> str:
    too_long, first_idx, first_len = _find_overlong_lines(
        lines,
        fallback_index=fallback_index,
        fallback_len=fallback_len,
        fallback_count=fallback_count,
    )
    idx_msg = "?"
    len_msg = "?"
    if first_idx is not None:
        idx_msg = str(first_idx + 1 + line_offset)
    if first_len is not None:
        len_msg = str(first_len)
    return (
        f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit.\n"
        f"First at line {idx_msg} ({len_msg} bytes including newline)."
    )


def _log_split_result(log_fn: Callable[[str], None] | None, split_result: Any) -> None:
    if not log_fn or not getattr(split_result, "modified_count", 0):
        return
    if getattr(split_result, "split_count", 0):
        msg = (
            f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
            f"{MAX_LINE_LENGTH}-byte limit (split {split_result.split_count})."
        )
    else:
        msg = (
            f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
            f"{MAX_LINE_LENGTH}-byte limit."
        )
    try:
        log_fn(msg)
    except Exception:
        pass


def _apply_auto_level_to_path(
    *,
    source_path: str,
    source_lines: list[str] | None,
    output_path: str,
    temp_path_fn: Callable[[str], str],
    height_map: HeightMap,
    arc_step_rad: float,
    interpolation: str,
    header_lines: list[str] | None,
    streaming_mode: bool,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[LevelFileResult, bool, str | None]:
    def _level_and_write(target_path: str) -> LevelFileResult:
        use_lines = (
            not streaming_mode and isinstance(source_lines, list) and bool(source_lines)
        )
        if use_lines:
            lines = cast(list[str], source_lines)
            level_result = level_gcode_lines(
                lines,
                height_map,
                arc_step_rad=arc_step_rad,
                interpolation=interpolation,
            )
            if level_result.error:
                return LevelFileResult(None, 0, level_result.error, False)
            split_result = split_gcode_lines(level_result.lines, MAX_LINE_LENGTH)
            if split_result.failed_index is not None:
                error = _format_overlong_error(
                    level_result.lines,
                    fallback_index=split_result.failed_index,
                    fallback_len=split_result.failed_len,
                    line_offset=len(header_lines or []),
                )
                return LevelFileResult(None, 0, error, False)
            _log_split_result(log_fn, split_result)
            return write_gcode_lines(
                target_path,
                split_result.lines,
                header_lines=header_lines,
            )

        result = level_gcode_file(
            source_path,
            target_path,
            height_map,
            arc_step_rad=arc_step_rad,
            interpolation=interpolation,
            header_lines=header_lines,
        )
        if result.error:
            return result
        expected_headers = [h.rstrip("\r\n") for h in header_lines or []]
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as infile:
                header_buffer: list[str] = []
                matched_headers = True
                for header in expected_headers:
                    raw_header = infile.readline()
                    if not raw_header:
                        matched_headers = False
                        break
                    raw_header_text = raw_header.rstrip("\r\n")
                    header_buffer.append(raw_header_text)
                    if raw_header_text != header:
                        matched_headers = False
                        break
                if matched_headers and expected_headers:
                    header_count = len(header_buffer)
                    raw_iter: Iterable[str] = infile
                else:
                    header_count = 0
                    raw_iter = itertools.chain(header_buffer, infile)
                stream_result = split_gcode_lines_stream(
                    raw_iter,
                    max_len=MAX_LINE_LENGTH,
                    clean_line=clean_gcode_line,
                    preserve_raw=True,
                )
        except Exception as exc:
            try:
                os.remove(target_path)
            except OSError:
                pass
            return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))

        if stream_result.failed_index is not None:
            try:
                os.remove(target_path)
            except OSError:
                pass
            error = _format_overlong_error(
                [],
                fallback_index=stream_result.failed_index,
                fallback_len=stream_result.failed_len,
                fallback_count=stream_result.too_long,
                line_offset=header_count,
            )
            return LevelFileResult(None, 0, error, False)
        if stream_result.modified_count == 0:
            return result

        header_lines_to_write: list[str] = []
        header_count = 0
        temp_path = None
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as infile:
                header_buffer = []
                matched_headers = True
                for header in expected_headers:
                    raw_header = infile.readline()
                    if not raw_header:
                        matched_headers = False
                        break
                    raw_header_text = raw_header.rstrip("\r\n")
                    header_buffer.append(raw_header_text)
                    if raw_header_text != header:
                        matched_headers = False
                        break
                if matched_headers and expected_headers:
                    header_lines_to_write = header_buffer
                    header_count = len(header_lines_to_write)
                    rewrite_iter: Iterable[str] = infile
                else:
                    rewrite_iter = itertools.chain(header_buffer, infile)

                dir_name = os.path.dirname(target_path) or "."
                base_name = os.path.splitext(os.path.basename(target_path))[0]
                ext = os.path.splitext(target_path)[1] or ".gcode"
                temp_file = tempfile.NamedTemporaryFile(
                    prefix=f"{base_name}_split_",
                    suffix=ext,
                    delete=False,
                    dir=dir_name,
                )
                temp_path = temp_file.name
                temp_file.close()
                with open(temp_path, "w", encoding="utf-8", newline="") as outfile:
                    def write_line(line: str) -> None:
                        outfile.write(line.rstrip("\n"))
                        outfile.write("\n")

                    for header_line in header_lines_to_write:
                        write_line(header_line)
                    stream_result = split_gcode_lines_stream(
                        rewrite_iter,
                        max_len=MAX_LINE_LENGTH,
                        clean_line=clean_gcode_line,
                        preserve_raw=True,
                        write_line=write_line,
                    )
        except Exception as exc:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            try:
                os.remove(target_path)
            except OSError:
                pass
            return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))

        if stream_result.failed_index is not None:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            try:
                os.remove(target_path)
            except OSError:
                pass
            error = _format_overlong_error(
                [],
                fallback_index=stream_result.failed_index,
                fallback_len=stream_result.failed_len,
                fallback_count=stream_result.too_long,
                line_offset=header_count,
            )
            return LevelFileResult(None, 0, error, False)
        _log_split_result(log_fn, stream_result)
        try:
            if temp_path:
                os.replace(temp_path, target_path)
        except Exception as exc:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            try:
                os.remove(target_path)
            except OSError:
                pass
            return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))
        return LevelFileResult(
            target_path,
            stream_result.lines_written + header_count,
            None,
            False,
        )

    result = _level_and_write(output_path)
    fallback_warning = None
    is_temp = False
    if result.error and result.io_error:
        temp_path = temp_path_fn(source_path)
        fallback = _level_and_write(temp_path)
        if fallback.error is None:
            result = fallback
            is_temp = True
            fallback_warning = (
                "Could not write the leveled file next to the source file. "
                "Saved a temporary leveled file instead. Use Save Leveled to keep a copy."
            )
        else:
            result = fallback
    return result, is_temp, fallback_warning


def show_auto_level_dialog(app: Any) -> None:
    streaming_mode = bool(getattr(app, "_gcode_streaming_mode", False))
    last_path = getattr(app, "_last_gcode_path", None)

    def resolve_bounds() -> Any:
        parse_result = getattr(app, "_last_parse_result", None)
        bounds = getattr(parse_result, "bounds", None) if parse_result else None
        if bounds:
            return bounds
        top_view = getattr(getattr(app, "toolpath_panel", None), "top_view", None)
        return getattr(top_view, "bounds", None) if top_view else None

    def is_al_path(path: str) -> bool:
        base = os.path.splitext(os.path.basename(path))[0]
        base_upper = base.upper()
        if base_upper.endswith("-AL"):
            return True
        prefix, sep, suffix = base_upper.rpartition("-AL-")
        return bool(prefix) and bool(sep) and suffix.isdigit()

    bounds = resolve_bounds()
    if streaming_mode and not last_path:
        messagebox.showwarning("Auto-Level", "Load a G-code file first.")
        return
    if last_path and is_al_path(last_path):
        messagebox.showwarning(
            "Auto-Level",
            "Auto-Level is already applied to this file. Load the original file to re-level.",
        )
        return
    if not bounds:
        if streaming_mode:
            messagebox.showwarning(
                "Auto-Level",
                "Bounds are not ready yet. Open the Top View or wait for parsing to finish.",
            )
        else:
            messagebox.showwarning("Auto-Level", "Load a G-code file with bounds first.")
        return
    minx, maxx, miny, maxy = bounds[0], bounds[1], bounds[2], bounds[3]
    base_bounds = ProbeBounds(minx=minx, maxx=maxx, miny=miny, maxy=maxy)
    if base_bounds.width() <= 0 or base_bounds.height() <= 0:
        messagebox.showwarning("Auto-Level", "G-code bounds look empty.")
        return

    default_job_prefs = DEFAULT_SETTINGS.get("auto_level_job_prefs", {})
    raw_job_prefs = getattr(app, "auto_level_job_prefs", None)
    job_prefs = _merge_auto_level_job_prefs(default_job_prefs, raw_job_prefs)
    chosen_profile = _select_auto_level_profile(base_bounds.area(), job_prefs, default_job_prefs)
    profile_options = ("small", "large", "custom")
    profile_name = chosen_profile if chosen_profile in profile_options else "custom"

    dlg = tk.Toplevel(app)
    dlg.title("Auto-Level")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)

    saved = dict(getattr(app, "auto_level_settings", {}) or {})
    margin_default = float(saved.get("margin", 5.0) or 0.0)
    base_spacing_saved = float(saved.get("base_spacing", 5.0) or 5.0)
    base_spacing_default = base_spacing_saved
    min_spacing_default = float(saved.get("min_spacing", 2.0) or 2.0)
    max_spacing_default = float(saved.get("max_spacing", 12.0) or 12.0)
    max_points_default = saved.get("max_points", None)
    interp_saved = pref_interp(saved.get("interpolation", "bicubic"), "bicubic")
    path_order_default = str(saved.get("path_order", "serpentine") or "serpentine")
    run_defaults = ProbeRunSettings(
        safe_z=float(saved.get("safe_z", 5.0) or 0.0),
        probe_depth=float(saved.get("probe_depth", 3.0) or 0.0),
        probe_feed=float(saved.get("probe_feed", 100.0) or 0.0),
        retract_z=float(saved.get("retract_z", 2.0) or 0.0),
        settle_time=float(saved.get("settle_time", 0.0) or 0.0),
    )

    profile = job_prefs.get(profile_name, {}) if isinstance(job_prefs, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    base_spacing_default = pref_float(profile.get("spacing"), base_spacing_default)
    interp_default = pref_interp(profile.get("interpolation"), interp_saved)

    defaults = AdaptiveGridSpec(
        margin=margin_default,
        base_spacing=base_spacing_default,
        min_spacing=min_spacing_default,
        max_spacing=max_spacing_default,
        max_points=max_points_default,
    )

    margin_var = tk.StringVar(value=f"{defaults.margin:.2f}")
    base_spacing_var = tk.StringVar(value=f"{defaults.base_spacing:.2f}")
    min_spacing_var = tk.StringVar(value=f"{defaults.min_spacing:.2f}")
    max_spacing_var = tk.StringVar(value=f"{defaults.max_spacing:.2f}")
    max_points_var = tk.StringVar(
        value="" if defaults.max_points is None else str(int(defaults.max_points))
    )
    safe_z_var = tk.StringVar(value=f"{run_defaults.safe_z:.2f}")
    probe_depth_var = tk.StringVar(value=f"{run_defaults.probe_depth:.2f}")
    probe_feed_var = tk.StringVar(value=f"{run_defaults.probe_feed:.1f}")
    retract_var = tk.StringVar(value=f"{run_defaults.retract_z:.2f}")
    settle_var = tk.StringVar(value=f"{run_defaults.settle_time:.2f}")
    interp_var = tk.StringVar(value=interp_default)

    avoidance_count = 8
    default_avoidance = DEFAULT_SETTINGS.get("auto_level_settings", {}).get("avoidance_areas", [])
    if not isinstance(default_avoidance, list) or len(default_avoidance) < avoidance_count:
        default_avoidance = [
            {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""}
            for _ in range(avoidance_count)
        ]

    def _coerce_avoidance(area: object, fallback: dict) -> dict:
        if not isinstance(area, dict):
            area = {}

        def to_float(value: object, default: float) -> float:
            try:
                if isinstance(value, (int, float, str)):
                    return float(value)
            except Exception:
                pass
            return float(default)

        return {
            "enabled": bool(area.get("enabled", fallback.get("enabled", False))),
            "x": to_float(area.get("x"), fallback.get("x", 0.0)),
            "y": to_float(area.get("y"), fallback.get("y", 0.0)),
            "radius": to_float(area.get("radius"), fallback.get("radius", 20.0)),
            "note": str(area.get("note", fallback.get("note", "")) or ""),
        }

    raw_avoidance = saved.get("avoidance_areas")
    avoidance_rows: list[dict] = []
    for idx in range(avoidance_count):
        fallback = default_avoidance[idx] if idx < len(default_avoidance) else default_avoidance[0]
        if isinstance(raw_avoidance, list) and idx < len(raw_avoidance):
            avoidance_rows.append(_coerce_avoidance(raw_avoidance[idx], fallback))
        else:
            avoidance_rows.append(_coerce_avoidance({}, fallback))

    avoidance_vars: list[dict[str, tk.Variable]] = []
    for row in avoidance_rows:
        avoidance_vars.append(
            {
                "enabled": tk.BooleanVar(value=bool(row.get("enabled", False))),
                "x": tk.StringVar(value=f"{float(row.get('x', 0.0)):.2f}"),
                "y": tk.StringVar(value=f"{float(row.get('y', 0.0)):.2f}"),
                "radius": tk.StringVar(value=f"{float(row.get('radius', 20.0)):.2f}"),
                "note": tk.StringVar(value=str(row.get("note", "") or "")),
            }
        )
    avoidance_controls: list[Any] = []

    preview_var = tk.StringVar(value="")
    bounds_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="")
    map_summary_var = tk.StringVar(value="")
    stats_var = tk.StringVar(value="")
    revert_btn = None
    save_btn = None
    save_map_btn = None
    load_map_btn = None
    original_lines = getattr(app, "_auto_level_original_lines", None)
    original_path = getattr(app, "_auto_level_original_path", None)
    profile_labels = [name.title() for name in profile_options]
    profile_var = tk.StringVar(value=profile_name.title())
    job_info_text = (
        f"Job size: {base_bounds.width():.2f} x {base_bounds.height():.2f} mm "
        f"({base_bounds.area():.0f} mm^2)\n"
        f"Recommended profile: {chosen_profile.title()}"
    )
    path_order_options: dict[str, str] = {
        "Serpentine (bottom-left)": "serpentine",
        "Spiral (center)": "spiral",
    }

    def _path_order_label(order: str) -> str:
        order = (order or "").strip().lower()
        for label, value in path_order_options.items():
            if value == order:
                return label
        return "Serpentine (bottom-left)"

    def _path_order_value(label: str) -> str:
        return path_order_options.get(label, "serpentine")

    path_order_var = tk.StringVar(value=_path_order_label(path_order_default))

    def apply_profile_choice(name: str) -> None:
        if not name:
            return
        profile = job_prefs.get(name, {}) if isinstance(job_prefs, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        spacing = pref_float(profile.get("spacing"), base_spacing_saved)
        base_spacing_var.set(f"{spacing:.2f}")
        interp_var.set(pref_interp(profile.get("interpolation"), interp_saved))
        update_preview()

    def preset_snapshot() -> dict:
        return {
            "margin": safe_float_text(margin_var),
            "base_spacing": safe_float_text(base_spacing_var),
            "min_spacing": safe_float_text(min_spacing_var),
            "max_spacing": safe_float_text(max_spacing_var),
            "max_points": parse_int_optional_var(max_points_var),
            "safe_z": safe_float_text(safe_z_var),
            "probe_depth": safe_float_text(probe_depth_var),
            "probe_feed": safe_float_text(probe_feed_var),
            "retract_z": safe_float_text(retract_var),
            "settle_time": safe_float_text(settle_var),
            "path_order": _path_order_value(path_order_var.get()),
            "interpolation": interp_var.get().strip().lower(),
        }

    def refresh_preset_values():
        values = sorted(list((app.auto_level_presets or {}).keys()))
        preset_combo.configure(values=values)

    def apply_preset(name: str):
        if not name:
            return
        preset = (app.auto_level_presets or {}).get(name)
        if not isinstance(preset, dict):
            return
        margin_var.set(f"{float(preset.get('margin', defaults.margin)):.2f}")
        base_spacing_var.set(f"{float(preset.get('base_spacing', defaults.base_spacing)):.2f}")
        min_spacing_var.set(f"{float(preset.get('min_spacing', defaults.min_spacing)):.2f}")
        max_spacing_var.set(f"{float(preset.get('max_spacing', defaults.max_spacing)):.2f}")
        max_points = preset.get("max_points", None)
        max_points_var.set("" if max_points is None else str(int(max_points)))
        path_order_var.set(_path_order_label(str(preset.get("path_order", path_order_default))))
        safe_z_var.set(f"{float(preset.get('safe_z', run_defaults.safe_z)):.2f}")
        probe_depth_var.set(f"{float(preset.get('probe_depth', run_defaults.probe_depth)):.2f}")
        probe_feed_var.set(f"{float(preset.get('probe_feed', run_defaults.probe_feed)):.1f}")
        retract_var.set(f"{float(preset.get('retract_z', run_defaults.retract_z)):.2f}")
        settle_var.set(f"{float(preset.get('settle_time', run_defaults.settle_time)):.2f}")
        interp_var.set(str(preset.get("interpolation", interp_var.get())))
        update_preview()

    def save_preset():
        update_preview()
        if grid_state.get("grid") is None:
            messagebox.showwarning("Save preset", "Fix the grid settings before saving.")
            return
        errors = validate_probe_settings_vars(
            safe_z_var,
            probe_depth_var,
            probe_feed_var,
            retract_var,
            settle_var,
        )
        if errors:
            messagebox.showwarning("Save preset", errors[0])
            return
        try:
            snapshot = preset_snapshot()
        except ValueError:
            messagebox.showwarning("Save preset", "Fix the settings before saving.")
            return
        name = simpledialog.askstring("Save preset", "Preset name:", parent=dlg)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if app.auto_level_presets is None:
            app.auto_level_presets = {}
        if name in app.auto_level_presets:
            if not messagebox.askyesno("Save preset", f"Overwrite preset '{name}'?"):
                return
        app.auto_level_presets[name] = snapshot
        try:
            app.settings["auto_level_presets"] = dict(app.auto_level_presets)
        except Exception:
            pass
        refresh_preset_values()
        preset_var.set(name)

    def delete_preset():
        name = preset_var.get().strip()
        if not name:
            messagebox.showwarning("Delete preset", "Choose a preset to delete.")
            return
        if not messagebox.askyesno("Delete preset", f"Delete preset '{name}'?"):
            return
        if name in app.auto_level_presets:
            del app.auto_level_presets[name]
        try:
            app.settings["auto_level_presets"] = dict(app.auto_level_presets)
        except Exception:
            pass
        refresh_preset_values()
        preset_var.set("")

    def _avoidance_snapshot() -> list[dict]:
        snapshot: list[dict] = []
        for row in avoidance_vars:
            snapshot.append(
                {
                    "enabled": bool(row["enabled"].get()),
                    "x": safe_float_text(row["x"]),
                    "y": safe_float_text(row["y"]),
                    "radius": safe_float_text(row["radius"]),
                    "note": str(row["note"].get()),
                }
            )
        return snapshot

    def _parse_avoidance_areas() -> list[tuple[float, float, float]]:
        areas: list[tuple[float, float, float]] = []
        for idx, row in enumerate(avoidance_vars, start=1):
            if not bool(row["enabled"].get()):
                continue

            def read_value(var: tk.Variable, label: str) -> float:
                try:
                    value = float(var.get())
                except Exception as exc:
                    raise ValueError(f"{label} must be a number.") from exc
                if math.isnan(value) or math.isinf(value):
                    raise ValueError(f"{label} must be a valid number.")
                return value

            x = read_value(row["x"], f"Area {idx} X")
            y = read_value(row["y"], f"Area {idx} Y")
            radius = read_value(row["radius"], f"Area {idx} radius")
            if radius <= 0:
                raise ValueError(f"Area {idx} radius must be > 0.")
            areas.append((x, y, radius * radius))
        return areas

    def _any_avoidance_enabled() -> bool:
        return any(bool(row["enabled"].get()) for row in avoidance_vars)

    def _point_in_avoidance(px: float, py: float, areas: list[tuple[float, float, float]]) -> bool:
        for ax, ay, r2 in areas:
            dx = px - ax
            dy = py - ay
            if dx * dx + dy * dy <= r2:
                return True
        return False

    def _apply_avoidance(
        grid: ProbeGrid, areas: list[tuple[float, float, float]]
    ) -> tuple[ProbeGrid, list[tuple[float, float]]]:
        if not areas:
            return grid, []
        points: list[tuple[float, float]] = []
        skipped: list[tuple[float, float]] = []
        for px, py in grid.points:
            if _point_in_avoidance(px, py, areas):
                skipped.append((px, py))
            else:
                points.append((px, py))
        if not skipped:
            return grid, []
        filtered = ProbeGrid(
            bounds=grid.bounds,
            xs=grid.xs,
            ys=grid.ys,
            points=points,
            spacing_x=grid.spacing_x,
            spacing_y=grid.spacing_y,
            margin=grid.margin,
        )
        return filtered, skipped

    def _current_wpos_mm() -> tuple[float, float] | None:
        if not getattr(app, "connected", False):
            return None
        if not getattr(app, "_status_seen", False):
            return None
        wpos = getattr(app, "_wpos_raw", None)
        if not wpos or len(wpos) < 2:
            return None
        report_units = getattr(app, "_report_units", None) or app.unit_mode.get()
        try:
            return (
                convert_units(float(wpos[0]), report_units, "mm"),
                convert_units(float(wpos[1]), report_units, "mm"),
            )
        except Exception:
            return None

    def _set_avoidance_from_position(row_index: int) -> None:
        pos = _current_wpos_mm()
        if pos is None:
            messagebox.showwarning(
                "Auto-Level", "Position unavailable. Connect and wait for status."
            )
            return
        x_mm, y_mm = pos
        try:
            avoidance_vars[row_index]["x"].set(f"{x_mm:.2f}")
            avoidance_vars[row_index]["y"].set(f"{y_mm:.2f}")
        except Exception:
            return
        update_preview()

    grid_state: dict[str, object] = {"grid": None, "skipped_points": []}

    def set_start_state() -> None:
        if app.auto_level_runner.is_running():
            start_btn.config(state="disabled")
            return
        grid = grid_state.get("grid")
        if not isinstance(grid, ProbeGrid):
            start_btn.config(state="disabled")
            return
        errors = validate_probe_settings_vars(
            safe_z_var,
            probe_depth_var,
            probe_feed_var,
            retract_var,
            settle_var,
        )
        if errors:
            status_var.set(f"Probe settings: {errors[0]}")
            start_btn.config(state="disabled")
            return
        ready, reason = probe_connection_state(app)
        if not ready:
            status_var.set(reason)
            start_btn.config(state="disabled")
            return
        if status_var.get().startswith(
            ("Probe settings:", "Connect", "Waiting for GRBL", "Clear the alarm")
        ):
            status_var.set("")
        start_btn.config(state="normal")
        _sync_pending_g90_notice()

    pending_g90_text = "Pending G90 restore after alarm clears."

    def _sync_pending_g90_notice() -> None:
        if getattr(app, "_pending_force_g90", False):
            if not status_var.get():
                status_var.set(pending_g90_text)
            return
        if status_var.get() == pending_g90_text:
            status_var.set("")

    def update_preview() -> None:
        try:
            margin = max(0.0, parse_float_var(margin_var, "margin"))
            base_spacing = max(
                AUTOLEVEL_SPACING_MIN,
                parse_float_var(base_spacing_var, "base spacing"),
            )
            min_spacing = max(
                AUTOLEVEL_SPACING_MIN,
                parse_float_var(min_spacing_var, "min spacing"),
            )
            max_spacing = max(
                min_spacing,
                parse_float_var(max_spacing_var, "max spacing"),
            )
            max_points = parse_int_optional_var(max_points_var)
            path_order = _path_order_value(path_order_var.get())
            avoidance_areas = _parse_avoidance_areas()
        except ValueError as exc:
            preview_var.set(str(exc))
            bounds_var.set("")
            grid_state["grid"] = None
            grid_state["skipped_points"] = []
            start_btn.config(state="disabled")
            return
        app.auto_level_settings = {
            "margin": margin,
            "base_spacing": base_spacing,
            "min_spacing": min_spacing,
            "max_spacing": max_spacing,
            "max_points": max_points,
            "safe_z": safe_float_text(safe_z_var),
            "probe_depth": safe_float_text(probe_depth_var),
            "probe_feed": safe_float_text(probe_feed_var),
            "retract_z": safe_float_text(retract_var),
            "settle_time": safe_float_text(settle_var),
            "path_order": path_order,
            "interpolation": interp_var.get().strip().lower(),
            "avoidance_areas": _avoidance_snapshot(),
        }
        try:
            app.settings["auto_level_settings"] = dict(app.auto_level_settings)
        except Exception:
            pass
        spec = AdaptiveGridSpec(
            base_spacing=base_spacing,
            min_spacing=min_spacing,
            max_spacing=max_spacing,
            margin=margin,
            max_points=max_points,
        )
        grid = build_adaptive_grid(base_bounds, spec, path_order=path_order)
        grid, skipped_points = _apply_avoidance(grid, avoidance_areas)
        if grid.point_count() == 0:
            preview_var.set("Avoidance areas exclude all probe points.")
            bounds_var.set("")
            grid_state["grid"] = None
            grid_state["skipped_points"] = skipped_points
            start_btn.config(state="disabled")
            return
        grid_state["grid"] = grid
        grid_state["skipped_points"] = skipped_points
        preview_var.set(
            f"Grid: {len(grid.xs)} x {len(grid.ys)} ({grid.point_count()} points) "
            f"Spacing: {grid.spacing_x:.2f} x {grid.spacing_y:.2f} mm"
        )
        bounds_var.set(
            f"Probe area: {grid.bounds.width():.2f} x {grid.bounds.height():.2f} mm "
            f"(margin {grid.margin:.2f} mm)"
        )
        map_summary_var.set(
            f"Grid spacing: {grid.spacing_x:.2f} x {grid.spacing_y:.2f} mm"
        )
        set_start_state()

    def start_probe() -> None:
        update_preview()
        try:
            _parse_avoidance_areas()
        except ValueError as exc:
            messagebox.showwarning("Auto-Level", str(exc))
            return
        if not _any_avoidance_enabled():
            proceed = messagebox.askokcancel(
                "Auto-Level",
                "No avoidance areas are configured. Continue anyway?\n"
                "Select Cancel to configure avoidance areas first.",
            )
            if not proceed:
                return
        grid = grid_state.get("grid")
        if not isinstance(grid, ProbeGrid):
            return
        errors = validate_probe_settings_vars(
            safe_z_var,
            probe_depth_var,
            probe_feed_var,
            retract_var,
            settle_var,
        )
        if errors:
            messagebox.showwarning("Auto-Level", errors[0])
            return
        if not app._require_grbl_connection():
            return
        if app._alarm_locked:
            messagebox.showwarning("Auto-Level", "Clear the alarm before probing.")
            return
        if not messagebox.askokcancel(
            "Auto-Level",
            "Confirm Z0 is set to the surface plane before probing.\n"
            "Offsets are applied relative to this reference.",
        ):
            return
        height_map = HeightMap(grid.xs, grid.ys)
        skipped_points = grid_state.get("skipped_points", [])
        if isinstance(skipped_points, list):
            for px, py in skipped_points:
                height_map.mark_invalid(px, py)
        try:
            settings = ProbeRunSettings(
                safe_z=parse_float_var(safe_z_var, "safe Z"),
                probe_depth=parse_float_var(probe_depth_var, "probe depth"),
                probe_feed=parse_float_var(probe_feed_var, "probe feed"),
                retract_z=parse_float_var(retract_var, "retract Z"),
                settle_time=parse_float_var(settle_var, "settle time"),
            )
        except ValueError as exc:
            messagebox.showwarning("Auto-Level", str(exc))
            return
        progress_bar.configure(maximum=grid.point_count(), value=0)
        status_var.set("Probing...")
        _set_controls_enabled(False)
        apply_btn.config(state="disabled")
        if save_map_btn is not None:
            save_map_btn.config(state="disabled")

        def on_progress(done: int, total: int) -> None:
            def update() -> None:
                progress_bar.configure(value=done)
                status_var.set(f"Probing {done}/{total}")
            app.after(0, update)

        def on_done(ok: bool, reason: str | None) -> None:
            def finish() -> None:
                _set_controls_enabled(True)
                if ok:
                    stats = height_map.stats()
                    if stats:
                        status_var.set(
                            "Done. "
                            f"Min {stats.min_z:.4f} Max {stats.max_z:.4f} Span {stats.span():.4f} mm, "
                            f"RMS {stats.rms_roughness:.4f} mm, "
                            f"Outliers {stats.outliers}/{stats.point_count}"
                        )
                        if abs(stats.mean_z) > 1.0:
                            messagebox.showwarning(
                                "Auto-Level",
                                "Probe average is far from Z0. "
                                "Verify Z0 is set to the surface before applying.",
                            )
                    else:
                        status_var.set("Done.")
                    update_stats_summary(height_map, stats_var)
                    app._auto_level_grid = grid
                    app._auto_level_height_map = height_map
                    app._auto_level_bounds = grid.bounds
                    try:
                        show_overlay = bool(app.show_autolevel_overlay.get())
                    except Exception:
                        show_overlay = True
                    try:
                        app.toolpath_panel.set_autolevel_overlay(grid if show_overlay else None)
                    except Exception:
                        pass
                    apply_btn.config(state="normal")
                    if save_map_btn is not None:
                        save_map_btn.config(state="normal")
                else:
                    message = f"Probe stopped: {reason or 'failed'}"
                    if getattr(app, "_pending_force_g90", False):
                        message = f"{message} (pending G90 restore)"
                    status_var.set(message)
            app.after(0, finish)

        started = app.auto_level_runner.start(
            grid,
            height_map,
            settings,
            on_progress=on_progress,
            on_done=on_done,
        )
        if not started:
            _set_controls_enabled(True)
            status_var.set("Probe start failed.")

    def apply_level() -> None:
        height_map = getattr(app, "_auto_level_height_map", None)
        if height_map is None or not height_map.is_complete():
            messagebox.showwarning("Auto-Level", "Probe a complete grid before applying.")
            return
        path = getattr(app, "_last_gcode_path", None)
        if not path:
            messagebox.showwarning("Auto-Level", "Load a G-code file first.")
            return
        if is_al_path(path):
            messagebox.showwarning(
                "Auto-Level",
                "Auto-Level is already applied to this file. Load the original file to re-level.",
            )
            return

        lines = getattr(app, "_last_gcode_lines", None) or []
        line_count = getattr(app, "_gcode_total_lines", None) or len(lines)
        arc_step = math.pi / 18
        try:
            arc_step = app.toolpath_panel.get_arc_step_rad(int(line_count or 0))
        except Exception:
            arc_step = math.pi / 18
        method = interp_var.get().strip().lower()

        status_var.set("Applying height map to file...")
        _set_controls_enabled(False)
        apply_btn.config(state="disabled")

        def make_output_path(source_path: str) -> str:
            base, ext = os.path.splitext(os.path.basename(source_path))
            ext = ext if ext else ".gcode"
            dir_name = os.path.dirname(source_path) or "."
            candidate = os.path.join(dir_name, f"{base}-AL{ext}")
            if not os.path.exists(candidate):
                return candidate
            for idx in range(1, 1000):
                candidate = os.path.join(dir_name, f"{base}-AL-{idx}{ext}")
                if not os.path.exists(candidate):
                    return candidate
            return os.path.join(dir_name, f"{base}-AL-{int(time.time())}{ext}")

        def make_temp_path(source_path: str) -> str:
            base, ext = os.path.splitext(os.path.basename(source_path))
            ext = ext if ext else ".gcode"
            temp = tempfile.NamedTemporaryFile(
                prefix=f"{base}_leveled_",
                suffix=ext,
                delete=False,
            )
            temp_path = temp.name
            temp.close()
            return temp_path

        def header_lines_for(source_path: str) -> list[str]:
            source_name = os.path.basename(source_path)
            max_len = 40
            if len(source_name) > max_len:
                source_name = f"{source_name[:max_len - 3]}..."
            return [f"(Auto-Level from {source_name})"]

        def worker() -> None:
            old_path = getattr(app, "_auto_level_leveled_path", None)
            if getattr(app, "_auto_level_leveled_temp", False) and old_path:
                try:
                    os.remove(old_path)
                except OSError:
                    pass
            output_path = make_output_path(path)
            header_lines = header_lines_for(path)
            log_fn = None
            ui_q = getattr(app, "ui_q", None)
            if ui_q is not None:
                log_fn = lambda msg: ui_q.put(("log", msg))
            result, is_temp, fallback_warning = _apply_auto_level_to_path(
                source_path=path,
                source_lines=lines,
                output_path=output_path,
                temp_path_fn=make_temp_path,
                height_map=height_map,
                arc_step_rad=arc_step,
                interpolation=method,
                header_lines=header_lines,
                streaming_mode=streaming_mode,
                log_fn=log_fn,
            )

            def on_done() -> None:
                _set_controls_enabled(True)
                if result.error:
                    status_var.set("")
                    messagebox.showerror("Auto-Level", result.error)
                    return
                if (
                    not streaming_mode
                    and not isinstance(getattr(app, "_auto_level_original_lines", None), list)
                    and lines
                ):
                    app._auto_level_original_lines = list(lines)
                if not getattr(app, "_auto_level_original_path", None):
                    app._auto_level_original_path = path
                app._auto_level_leveled_lines = None
                app._auto_level_leveled_path = result.output_path
                app._auto_level_leveled_temp = is_temp
                app._auto_level_leveled_name = os.path.basename(
                    result.output_path or path or "Leveled Job"
                )
                app._auto_level_restore = {
                    "original_lines": getattr(app, "_auto_level_original_lines", None),
                    "original_path": getattr(app, "_auto_level_original_path", None),
                    "leveled_lines": None,
                    "leveled_path": result.output_path,
                    "leveled_temp": is_temp,
                    "leveled_name": app._auto_level_leveled_name,
                }
                if revert_btn is not None:
                    revert_btn.config(state="normal")
                if save_btn is not None:
                    save_btn.config(state="normal")
                if fallback_warning:
                    messagebox.showwarning("Auto-Level", fallback_warning)
                status_var.set(
                    "Loading leveled file..."
                    if not is_temp
                    else "Loading leveled file (temporary)..."
                )
                app._load_gcode_from_path(result.output_path)

            app.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()
        return

    def revert_job() -> None:
        orig_lines = getattr(app, "_auto_level_original_lines", None)
        path = getattr(app, "_auto_level_original_path", None)
        if not orig_lines and not path:
            return
        name = "Original Job"
        if path:
            name = os.path.basename(path)
        if orig_lines:
            app._apply_loaded_gcode(name, orig_lines, validated=False)
        else:
            app._load_gcode_from_path(path)
        status_var.set("Original job restored.")

    def save_leveled() -> None:
        save_leveled_job(app, status_var)

    def save_height_map() -> None:
        save_height_map_file(app, status_var)

    def load_height_map() -> None:
        load_height_map_file(
            app,
            status_var,
            stats_var,
            map_summary_var,
            apply_btn,
            save_map_btn,
            save_btn,
        )

    def cancel_probe() -> None:
        if app.auto_level_runner.is_running():
            app.auto_level_runner.cancel()
            status_var.set("Canceling...")
        else:
            dlg.destroy()

    def _set_controls_enabled(enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            profile_combo,
            preset_combo,
            preset_save_btn,
            preset_delete_btn,
            margin_entry,
            base_spacing_entry,
            min_spacing_entry,
            max_spacing_entry,
            max_points_entry,
            path_order_combo,
            safe_z_entry,
            probe_depth_entry,
            probe_feed_entry,
            retract_entry,
            settle_entry,
            interp_combo,
            *avoidance_controls,
            start_btn,
            apply_btn,
            save_btn,
            save_map_btn,
            load_map_btn,
            revert_btn,
        ):
            try:
                if widget is None:
                    continue
                widget.config(state=state)
            except Exception:
                pass
        close_btn.config(text="Cancel" if not enabled else "Close")

    def grid_row(
        parent: ttk.Frame,
        label: str,
        var: tk.StringVar,
        row: int,
        *,
        allow_decimal: bool = True,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var, width=10)
        entry.grid(row=row, column=1, sticky="w", pady=2)
        attach_numeric_keypad(entry, allow_decimal=allow_decimal)
        return entry

    frm.grid_columnconfigure(0, weight=1)
    notebook = ttk.Notebook(frm)
    notebook.grid(row=0, column=0, columnspan=2, sticky="ew")
    settings_tab = ttk.Frame(notebook)
    avoidance_tab = ttk.Frame(notebook)
    notebook.add(settings_tab, text="Settings")
    notebook.add(avoidance_tab, text="Avoidance Areas")
    settings_tab.grid_columnconfigure(1, weight=1)
    avoidance_tab.grid_columnconfigure(0, weight=1)

    header_row = ttk.Frame(settings_tab)
    header_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
    ttk.Label(header_row, text=job_info_text, wraplength=460, justify="left").pack(
        fill="x",
        pady=(0, 6),
    )

    profile_row = ttk.Frame(header_row)
    profile_row.pack(fill="x", pady=(0, 6))
    ttk.Label(profile_row, text="Profile").pack(side="left")
    profile_combo = ttk.Combobox(
        profile_row,
        textvariable=profile_var,
        values=profile_labels,
        state="readonly",
        width=12,
    )
    profile_combo.pack(side="left", padx=(8, 6))
    profile_combo.bind(
        "<<ComboboxSelected>>",
        lambda _evt: apply_profile_choice(profile_var.get().strip().lower()),
    )

    preset_row = ttk.Frame(header_row)
    preset_row.pack(fill="x")
    ttk.Label(preset_row, text="Preset").pack(side="left")
    preset_var = tk.StringVar(value="")
    preset_combo = ttk.Combobox(
        preset_row,
        textvariable=preset_var,
        values=sorted(list((app.auto_level_presets or {}).keys())),
        state="readonly",
        width=16,
    )
    preset_combo.pack(side="left", padx=(8, 6))
    preset_combo.bind("<<ComboboxSelected>>", lambda _evt: apply_preset(preset_var.get()))
    preset_save_btn = ttk.Button(preset_row, text="Save", command=save_preset)
    preset_save_btn.pack(side="left", padx=(0, 6))
    preset_delete_btn = ttk.Button(preset_row, text="Delete", command=delete_preset)
    preset_delete_btn.pack(side="left")

    if preset_var.get():
        apply_preset(preset_var.get())

    ttk.Label(settings_tab, text="Adaptive grid").grid(
        row=1, column=0, columnspan=2, sticky="w"
    )
    margin_entry = grid_row(settings_tab, "Margin (mm)", margin_var, 2)
    base_spacing_entry = grid_row(settings_tab, "Base spacing (mm)", base_spacing_var, 3)
    min_spacing_entry = grid_row(settings_tab, "Min spacing (mm)", min_spacing_var, 4)
    max_spacing_entry = grid_row(settings_tab, "Max spacing (mm)", max_spacing_var, 5)
    max_points_entry = grid_row(
        settings_tab,
        "Max points (optional)",
        max_points_var,
        6,
        allow_decimal=False,
    )

    ttk.Label(settings_tab, text="Probe order").grid(
        row=7, column=0, sticky="w", pady=(4, 2)
    )
    path_order_combo = ttk.Combobox(
        settings_tab,
        textvariable=path_order_var,
        values=tuple(path_order_options.keys()),
        state="readonly",
        width=20,
    )
    path_order_combo.grid(row=7, column=1, sticky="w", pady=(4, 2))
    path_order_combo.bind("<<ComboboxSelected>>", lambda _evt: update_preview())

    ttk.Separator(settings_tab, orient="horizontal").grid(
        row=8, column=0, columnspan=2, sticky="ew", pady=6
    )
    ttk.Label(settings_tab, text="Probe settings").grid(
        row=9, column=0, columnspan=2, sticky="w"
    )
    safe_z_entry = grid_row(settings_tab, "Safe Z (mm)", safe_z_var, 10)
    probe_depth_entry = grid_row(settings_tab, "Probe depth (mm)", probe_depth_var, 11)
    probe_feed_entry = grid_row(settings_tab, "Probe feed (mm/min)", probe_feed_var, 12)
    retract_entry = grid_row(settings_tab, "Retract Z (mm)", retract_var, 13)
    settle_entry = grid_row(settings_tab, "Settle time (sec)", settle_var, 14)
    ttk.Label(settings_tab, text="Interpolation").grid(
        row=15, column=0, sticky="w", pady=(4, 2)
    )
    interp_combo = ttk.Combobox(
        settings_tab,
        textvariable=interp_var,
        values=("bilinear", "bicubic"),
        state="readonly",
        width=10,
    )
    interp_combo.grid(row=15, column=1, sticky="w", pady=(4, 2))
    interp_combo.bind("<<ComboboxSelected>>", lambda _evt: update_preview())

    avoidance_frame = ttk.Frame(avoidance_tab, padding=6)
    avoidance_frame.grid(row=0, column=0, sticky="ew")
    avoidance_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(avoidance_frame, text="").grid(row=0, column=0, sticky="w")
    ttk.Label(avoidance_frame, text="Note").grid(row=0, column=1, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="Y (mm)").grid(row=0, column=2, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="X (mm)").grid(row=0, column=3, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="Radius (mm)").grid(row=0, column=4, sticky="w", padx=(2, 2))

    for idx, row in enumerate(avoidance_vars, start=1):
        enabled_var = row["enabled"]
        x_var = row["x"]
        y_var = row["y"]
        radius_var = row["radius"]
        note_var = row["note"]
        chk = ttk.Checkbutton(
            avoidance_frame,
            text=f"Area {idx}",
            variable=enabled_var,
            command=update_preview,
        )
        chk.grid(row=idx, column=0, sticky="w")
        note_entry = ttk.Entry(avoidance_frame, textvariable=note_var, width=16)
        note_entry.grid(row=idx, column=1, sticky="ew", padx=(2, 2))
        y_entry = ttk.Entry(avoidance_frame, textvariable=y_var, width=10)
        y_entry.grid(row=idx, column=2, sticky="w", padx=(2, 2))
        x_entry = ttk.Entry(avoidance_frame, textvariable=x_var, width=10)
        x_entry.grid(row=idx, column=3, sticky="w", padx=(2, 2))
        radius_entry = ttk.Entry(avoidance_frame, textvariable=radius_var, width=10)
        radius_entry.grid(row=idx, column=4, sticky="w", padx=(2, 2))
        attach_numeric_keypad(x_entry, allow_decimal=True, allow_negative=True)
        attach_numeric_keypad(y_entry, allow_decimal=True, allow_negative=True)
        attach_numeric_keypad(radius_entry, allow_decimal=True)
        def _make_set_position(row_index: int) -> Callable[[], None]:
            return lambda: _set_avoidance_from_position(row_index)

        set_btn = ttk.Button(
            avoidance_frame,
            text="Read Position",
            command=_make_set_position(idx - 1),
        )
        set_btn.grid(row=idx, column=5, sticky="w", padx=(2, 0))
        apply_tooltip(
            set_btn,
            "Populate the X and Y positions with the current position.",
        )
        avoidance_controls.extend(
            [chk, note_entry, x_entry, y_entry, radius_entry, set_btn]
        )
        def _refresh_preview(_event: tk.Event | None = None) -> None:
            update_preview()

        for entry in (note_entry, x_entry, y_entry, radius_entry):
            entry.bind("<KeyRelease>", _refresh_preview)

    ttk.Label(settings_tab, textvariable=preview_var, wraplength=460, justify="left").grid(
        row=16, column=0, columnspan=2, sticky="w", pady=(6, 0)
    )
    ttk.Label(settings_tab, textvariable=bounds_var, wraplength=460, justify="left").grid(
        row=17, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(settings_tab, textvariable=map_summary_var, wraplength=460, justify="left").grid(
        row=18, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(settings_tab, textvariable=stats_var, wraplength=460, justify="left").grid(
        row=19, column=0, columnspan=2, sticky="w"
    )

    ttk.Label(frm, textvariable=status_var, wraplength=460, justify="left").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
    )

    progress_bar = ttk.Progressbar(frm, mode="determinate", length=240)
    progress_bar.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
    start_btn = ttk.Button(btn_row, text="Start Probe", command=start_probe)
    start_btn.pack(side="left", padx=(0, 6))
    apply_btn = ttk.Button(btn_row, text="Apply to Job", command=apply_level, state="disabled")
    apply_btn.pack(side="left", padx=(0, 6))
    save_btn = ttk.Button(btn_row, text="Save Leveled", command=save_leveled, state="disabled")
    save_btn.pack(side="left", padx=(0, 6))
    save_map_btn = ttk.Button(btn_row, text="Save Map", command=save_height_map, state="disabled")
    save_map_btn.pack(side="left", padx=(0, 6))
    load_map_btn = ttk.Button(btn_row, text="Load Map", command=load_height_map)
    load_map_btn.pack(side="left", padx=(0, 6))
    revert_btn = ttk.Button(btn_row, text="Revert Job", command=revert_job, state="disabled")
    revert_btn.pack(side="left", padx=(0, 6))
    close_btn = ttk.Button(btn_row, text="Close", command=cancel_probe)
    close_btn.pack(side="left")

    for entry in (
        margin_entry,
        base_spacing_entry,
        min_spacing_entry,
        max_spacing_entry,
        max_points_entry,
        safe_z_entry,
        probe_depth_entry,
        probe_feed_entry,
        retract_entry,
        settle_entry,
    ):
        entry.bind("<KeyRelease>", lambda _evt: update_preview())

    if getattr(app, "_auto_level_height_map", None) is not None:
        try:
            if app._auto_level_height_map.is_complete():
                apply_btn.config(state="normal")
        except Exception:
            pass
    if (isinstance(original_lines, list) and original_lines) or original_path:
        revert_btn.config(state="normal")
    if isinstance(getattr(app, "_auto_level_leveled_lines", None), list) or getattr(
        app, "_auto_level_leveled_path", None
    ):
        save_btn.config(state="normal")
    if getattr(app, "_auto_level_height_map", None) is not None:
        try:
            if app._auto_level_height_map.is_complete():
                save_map_btn.config(state="normal")
        except Exception:
            pass
        try:
            height_map = app._auto_level_height_map
            map_summary_var.set(
                f"Loaded map: {len(height_map.xs)} x {len(height_map.ys)} "
                f"({len(height_map.xs) * len(height_map.ys)} points)"
            )
            update_stats_summary(height_map, stats_var)
        except Exception:
            pass
    update_preview()
    def _poll_start_state() -> None:
        if not dlg.winfo_exists():
            return
        set_start_state()
        dlg.after(AUTOLEVEL_START_STATE_POLL_MS, _poll_start_state)
    _poll_start_state()
    dlg.protocol("WM_DELETE_WINDOW", cancel_probe)
    center_window(dlg, app)
