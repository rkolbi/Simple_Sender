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

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.popup_utils import center_window


def _merge_auto_level_job_prefs(defaults: dict, overrides: object) -> dict:
    merged = dict(defaults) if isinstance(defaults, dict) else {}
    if not isinstance(overrides, dict):
        return merged
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(val)
            merged[key] = nested
        else:
            merged[key] = val
    return merged


def _select_auto_level_profile(area: float, prefs: dict, default_prefs: dict) -> str:
    def pref_float_optional(raw, fallback: float | None) -> float | None:
        try:
            val = float(raw)
        except Exception:
            return fallback
        return val if val > 0 else fallback

    small_max_area = pref_float_optional(
        prefs.get("small_max_area"),
        pref_float_optional(default_prefs.get("small_max_area"), None),
    )
    large_min_area = pref_float_optional(
        prefs.get("large_min_area"),
        pref_float_optional(default_prefs.get("large_min_area"), None),
    )
    if small_max_area is not None and area <= small_max_area:
        return "small"
    if large_min_area is not None and area >= large_min_area:
        return "large"
    return "custom"


def _prompt_auto_level_profile_choice(app, base_bounds, chosen_profile: str) -> str | None:
    profile_choice: str | None = None
    dlg = tk.Toplevel(app)
    dlg.title("Auto-Level preset")
    dlg.transient(app)
    dlg.grab_set()
    dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)
    width = base_bounds.width()
    height = base_bounds.height()
    area = base_bounds.area()
    msg = (
        f"Job size: {width:.2f} x {height:.2f} mm "
        f"({area:.0f} mm^2)\n"
        f"Preset selected: {chosen_profile.title()}"
    )
    ttk.Label(frm, text=msg, wraplength=440, justify="left").pack(fill="x", pady=(0, 10))
    btn_row = ttk.Frame(frm)
    btn_row.pack(fill="x")

    def choose(value: str | None) -> None:
        nonlocal profile_choice
        profile_choice = value
        try:
            dlg.destroy()
        except Exception:
            pass

    ttk.Button(
        btn_row,
        text=f"Continue ({chosen_profile.title()})",
        command=lambda: choose(chosen_profile),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        btn_row,
        text="Continue (Custom)",
        command=lambda: choose("custom"),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(btn_row, text="Cancel", command=lambda: choose(None)).pack(side="left")
    dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    center_window(dlg, app)
    dlg.wait_window()
    return profile_choice
