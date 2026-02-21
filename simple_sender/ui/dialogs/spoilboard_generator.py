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

import math
import os
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.widgets import attach_numeric_keypad
from simple_sender.utils.logging_config import get_log_dir


@dataclass(frozen=True)
class SpoilboardGeneratorParams:
    width: float
    height: float
    tool_diameter: float
    stepover_pct: float
    feed_xy: float
    feed_z: float
    spindle_rpm: int
    start_x: float
    start_y: float


def default_spoilboard_program_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"surfacing-{stamp}.nc"


def build_spoilboard_gcode_lines(params: SpoilboardGeneratorParams) -> list[str]:
    lift_mm = 10.0
    stepover = max(0.001, params.tool_diameter * (params.stepover_pct / 100.0))
    rows = max(1, int(math.ceil(params.height / stepover)))
    x0 = float(params.start_x)
    x1 = x0 + float(params.width)
    y0 = float(params.start_y)
    y1 = y0 + float(params.height)

    y_values: list[float] = []
    for idx in range(rows + 1):
        y = y0 + (idx * stepover)
        if y > y1:
            y = y1
        y_values.append(y)

    lines: list[str] = [
        "G21",
        "G90",
        "G17",
        "G94",
        "G54",
        "G91",
        f"G0 Z{lift_mm:.3f}",
        "G90",
        f"G0 X{x0:.3f} Y{y_values[0]:.3f}",
        f"M3 S{int(params.spindle_rpm)}",
        "G91",
        f"F{params.feed_z:.3f}",
        f"G1 Z{-lift_mm:.3f}",
        "G90",
        f"F{params.feed_xy:.3f}",
    ]
    for idx, y in enumerate(y_values):
        target_x = x1 if (idx % 2 == 0) else x0
        if idx > 0:
            lines.append(f"G1 Y{y:.3f}")
        lines.append(f"G1 X{target_x:.3f}")
    lines.extend(
        [
            "G91",
            f"G0 Z{lift_mm:.3f}",
            "G90",
            "M5",
            "M30",
        ]
    )
    return lines


def _load_generated_gcode_into_app(app: Any, gcode_text: str, virtual_name: str) -> None:
    lines = [ln.rstrip("\r\n") for ln in gcode_text.splitlines() if ln.strip()]
    if not lines:
        return
    try:
        if getattr(app, "notebook", None) is not None and getattr(app, "gcode_tab", None) is not None:
            app.notebook.select(app.gcode_tab)
    except Exception:
        pass
    app._apply_loaded_gcode(virtual_name, lines, validated=False)


def _save_generated_gcode(
    app: Any,
    gcode_text: str,
    *,
    default_name: str,
) -> str | None:
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Save G-code",
        defaultextension=".nc",
        initialdir=str(get_log_dir()),
        initialfile=default_name,
        filetypes=(("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")),
    )
    if not path:
        return None
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(gcode_text)
    try:
        app.settings["last_gcode_dir"] = os.path.dirname(path)
    except Exception:
        pass
    msg = f"Saved: {path}"
    try:
        app.status.config(text=msg)
    except Exception:
        pass
    try:
        app.streaming_controller.log(msg)
    except Exception:
        pass
    return str(path)


def _show_post_generate_options(app: Any, gcode_text: str, default_name: str) -> None:
    choice = {"value": "cancel"}

    dlg = tk.Toplevel(app)
    dlg.title("Spoilboard G-code Generated")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frame = ttk.Frame(dlg, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="What would you like to do with the generated surfacing program?",
        wraplength=420,
        justify="left",
    ).grid(row=0, column=0, columnspan=3, sticky="w")

    def _choose(value: str) -> None:
        choice["value"] = value
        try:
            dlg.destroy()
        except Exception:
            pass

    ttk.Button(frame, text="Read G-code", command=lambda: _choose("read")).grid(
        row=1, column=0, padx=(0, 6), pady=(10, 0), sticky="ew"
    )
    ttk.Button(frame, text="Save G-code", command=lambda: _choose("save")).grid(
        row=1, column=1, padx=(0, 6), pady=(10, 0), sticky="ew"
    )
    ttk.Button(frame, text="Cancel", command=lambda: _choose("cancel")).grid(
        row=1, column=2, pady=(10, 0), sticky="ew"
    )
    frame.grid_columnconfigure(0, weight=1)
    frame.grid_columnconfigure(1, weight=1)
    frame.grid_columnconfigure(2, weight=1)
    dlg.protocol("WM_DELETE_WINDOW", lambda: _choose("cancel"))
    center_window(dlg, app)
    dlg.wait_window()

    if choice["value"] == "read":
        _load_generated_gcode_into_app(app, gcode_text, default_name)
        return
    if choice["value"] == "save":
        try:
            _save_generated_gcode(app, gcode_text, default_name=default_name)
        except Exception as exc:
            messagebox.showerror("Save G-code", str(exc))


def _float_from_var(label: str, var: tk.StringVar) -> float:
    raw = var.get().strip()
    try:
        return float(raw)
    except Exception as exc:
        raise ValueError(f"Invalid {label}.") from exc


def show_spoilboard_generator_dialog(app: Any) -> None:
    defaults = dict(getattr(app, "settings", {}).get("spoilboard_generator", {}) or {})
    default_rpm = int(defaults.get("spindle_rpm", 18000) or 18000)

    values: dict[str, tk.StringVar] = {
        "width": tk.StringVar(value=str(defaults.get("width", 300.0))),
        "height": tk.StringVar(value=str(defaults.get("height", 300.0))),
        "tool_diameter": tk.StringVar(value=str(defaults.get("tool_diameter", 22.0))),
        "stepover_pct": tk.StringVar(value=str(defaults.get("stepover_pct", 40.0))),
        "feed_xy": tk.StringVar(value=str(defaults.get("feed_xy", 1200.0))),
        "feed_z": tk.StringVar(value=str(defaults.get("feed_z", 300.0))),
        "spindle_rpm": tk.StringVar(value=str(defaults.get("spindle_rpm", default_rpm))),
        "start_x": tk.StringVar(value=str(defaults.get("start_x", 0.0))),
        "start_y": tk.StringVar(value=str(defaults.get("start_y", 0.0))),
    }

    dlg = tk.Toplevel(app)
    dlg.title("Spoilboard Generator")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frame = ttk.Frame(dlg, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="Generate a spoilboard surfacing program (units: mm).",
        wraplength=520,
        justify="left",
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    fields: tuple[tuple[str, str], ...] = (
        ("Width X", "width"),
        ("Height Y", "height"),
        ("Tool Diameter", "tool_diameter"),
        ("Stepover %", "stepover_pct"),
        ("Feed XY", "feed_xy"),
        ("Feed Z", "feed_z"),
        ("Spindle RPM", "spindle_rpm"),
        ("Start X", "start_x"),
        ("Start Y", "start_y"),
    )
    entries: list[ttk.Entry] = []
    for row_idx, (label, key) in enumerate(fields, start=1):
        ttk.Label(frame, text=label).grid(row=row_idx, column=0, sticky="w", padx=(0, 10), pady=2)
        entry = ttk.Entry(frame, textvariable=values[key], width=12)
        entry.grid(row=row_idx, column=1, sticky="w", pady=2)
        allow_negative = key in {"start_x", "start_y"}
        allow_decimal = key != "spindle_rpm"
        attach_numeric_keypad(
            entry,
            allow_decimal=allow_decimal,
            allow_negative=allow_negative,
            allow_empty=False,
        )
        entries.append(entry)

    def _cancel() -> None:
        try:
            dlg.destroy()
        except Exception:
            pass

    def _generate() -> None:
        try:
            width = _float_from_var("Width X", values["width"])
            height = _float_from_var("Height Y", values["height"])
            tool_diameter = _float_from_var("Tool Diameter", values["tool_diameter"])
            stepover_pct = _float_from_var("Stepover %", values["stepover_pct"])
            feed_xy = _float_from_var("Feed XY", values["feed_xy"])
            feed_z = _float_from_var("Feed Z", values["feed_z"])
            spindle_rpm = int(round(_float_from_var("Spindle RPM", values["spindle_rpm"])))
            start_x = _float_from_var("Start X", values["start_x"])
            start_y = _float_from_var("Start Y", values["start_y"])
        except ValueError as exc:
            messagebox.showwarning("Spoilboard Generator", str(exc))
            return

        if width <= 0 or height <= 0:
            messagebox.showwarning("Spoilboard Generator", "Width and Height must be greater than 0.")
            return
        if tool_diameter <= 0:
            messagebox.showwarning("Spoilboard Generator", "Tool Diameter must be greater than 0.")
            return
        if stepover_pct <= 0 or stepover_pct > 100:
            messagebox.showwarning("Spoilboard Generator", "Stepover % must be > 0 and <= 100.")
            return
        if feed_xy <= 0 or feed_z <= 0:
            messagebox.showwarning("Spoilboard Generator", "Feed values must be greater than 0.")
            return
        if spindle_rpm < 0:
            messagebox.showwarning("Spoilboard Generator", "Spindle RPM must be 0 or greater.")
            return

        params = SpoilboardGeneratorParams(
            width=width,
            height=height,
            tool_diameter=tool_diameter,
            stepover_pct=stepover_pct,
            feed_xy=feed_xy,
            feed_z=feed_z,
            spindle_rpm=spindle_rpm,
            start_x=start_x,
            start_y=start_y,
        )
        try:
            app.settings["spoilboard_generator"] = {
                "width": width,
                "height": height,
                "tool_diameter": tool_diameter,
                "stepover_pct": stepover_pct,
                "feed_xy": feed_xy,
                "feed_z": feed_z,
                "spindle_rpm": spindle_rpm,
                "start_x": start_x,
                "start_y": start_y,
            }
        except Exception:
            pass
        lines = build_spoilboard_gcode_lines(params)
        gcode_text = "\n".join(lines) + "\n"
        virtual_name = default_spoilboard_program_name()
        _cancel()
        _show_post_generate_options(app, gcode_text, virtual_name)

    btn_row = ttk.Frame(frame)
    btn_row.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(btn_row, text="Generate", command=_generate).pack(side="right", padx=(6, 0))
    ttk.Button(btn_row, text="Cancel", command=_cancel).pack(side="right")

    dlg.protocol("WM_DELETE_WINDOW", _cancel)
    center_window(dlg, app)
    if entries:
        try:
            entries[0].focus_set()
        except Exception:
            pass
