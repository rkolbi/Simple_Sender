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

import json
import os
import shutil
from tkinter import filedialog, messagebox

from simple_sender.autolevel.height_map import HeightMap
from .helpers import update_stats_summary


def save_leveled(app, status_var) -> None:
    leveled = getattr(app, "_auto_level_leveled_lines", None)
    leveled_path = getattr(app, "_auto_level_leveled_path", None)
    if not leveled and not leveled_path:
        messagebox.showwarning("Auto-Level", "Apply leveling before saving.")
        return
    initial_dir = app.settings.get("last_gcode_dir", "")
    default_name = "leveled.gcode"
    path = getattr(app, "_auto_level_original_path", None) or getattr(app, "_last_gcode_path", None)
    if path:
        base, ext = os.path.splitext(os.path.basename(path))
        suffix = ext if ext else ".gcode"
        default_name = f"{base}-AL{suffix}"
    save_path = filedialog.asksaveasfilename(
        title="Save leveled G-code",
        initialdir=initial_dir or None,
        initialfile=default_name,
        defaultextension=".gcode",
        filetypes=[("G-code", "*.gcode *.nc *.tap"), ("All files", "*.*")],
    )
    if not save_path:
        return
    try:
        if leveled:
            with open(save_path, "w", encoding="utf-8") as f:
                for line in leveled:
                    f.write(line.rstrip("\n"))
                    f.write("\n")
        else:
            if not leveled_path or not os.path.isfile(leveled_path):
                raise FileNotFoundError("Leveled file not found.")
            shutil.copyfile(leveled_path, save_path)
    except Exception as exc:
        messagebox.showerror("Save leveled G-code", str(exc))
        return
    try:
        app.settings["last_gcode_dir"] = os.path.dirname(save_path)
    except Exception:
        pass
    status_var.set(f"Saved leveled job: {os.path.basename(save_path)}")


def save_height_map(app, status_var) -> None:
    height_map = getattr(app, "_auto_level_height_map", None)
    if height_map is None or not height_map.is_complete():
        messagebox.showwarning("Auto-Level", "Probe a complete grid before saving.")
        return
    initial_dir = app.settings.get("last_gcode_dir", "")
    default_name = "height_map.json"
    path = getattr(app, "_last_gcode_path", None)
    if path:
        base, _ = os.path.splitext(os.path.basename(path))
        default_name = f"{base}_height_map.json"
    save_path = filedialog.asksaveasfilename(
        title="Save height map",
        initialdir=initial_dir or None,
        initialfile=default_name,
        defaultextension=".json",
        filetypes=[("Height map", "*.json"), ("All files", "*.*")],
    )
    if not save_path:
        return
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(height_map.to_dict(), f, indent=2, ensure_ascii=True)
    except Exception as exc:
        messagebox.showerror("Save height map", str(exc))
        return
    try:
        app.settings["last_gcode_dir"] = os.path.dirname(save_path)
    except Exception:
        pass
    status_var.set(f"Saved height map: {os.path.basename(save_path)}")


def load_height_map(
    app,
    status_var,
    stats_var,
    map_summary_var,
    apply_btn,
    save_map_btn,
    save_btn,
) -> None:
    initial_dir = app.settings.get("last_gcode_dir", "")
    load_path = filedialog.askopenfilename(
        title="Load height map",
        initialdir=initial_dir or None,
        filetypes=[("Height map", "*.json"), ("All files", "*.*")],
    )
    if not load_path:
        return
    try:
        with open(load_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        height_map = HeightMap.from_dict(data)
    except Exception as exc:
        messagebox.showerror("Load height map", str(exc))
        return
    app._auto_level_height_map = height_map
    app._auto_level_grid = None
    app._auto_level_bounds = None
    if not height_map.is_complete():
        status_var.set("Loaded height map (incomplete).")
    else:
        stats = height_map.stats()
        if stats:
            status_var.set(
                f"Loaded map. Min {stats.min_z:.4f} Max {stats.max_z:.4f} Span {stats.span():.4f} mm"
            )
        else:
            status_var.set("Loaded height map.")
    update_stats_summary(height_map, stats_var)
    try:
        apply_btn.config(state="normal" if height_map.is_complete() else "disabled")
    except Exception:
        pass
    if save_map_btn is not None:
        try:
            save_map_btn.config(state="normal" if height_map.is_complete() else "disabled")
        except Exception:
            pass
    if save_btn is not None and (
        isinstance(getattr(app, "_auto_level_leveled_lines", None), list)
        or getattr(app, "_auto_level_leveled_path", None)
    ):
        try:
            save_btn.config(state="normal")
        except Exception:
            pass
    map_summary_var.set(
        f"Loaded map: {len(height_map.xs)} x {len(height_map.ys)} "
        f"({len(height_map.xs) * len(height_map.ys)} points)"
    )
    try:
        app.settings["last_gcode_dir"] = os.path.dirname(load_path)
    except Exception:
        pass
