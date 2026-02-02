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

from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from simple_sender.ui.widgets import apply_tooltip, attach_numeric_keypad


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


def build_avoidance_tab(
    parent: ttk.Frame,
    avoidance_vars: list[dict[str, Any]],
    update_preview: Callable[[], None],
    set_avoidance_from_position: Callable[[int], None],
) -> list[Any]:
    avoidance_frame = ttk.Frame(parent, padding=6)
    avoidance_frame.grid(row=0, column=0, sticky="ew")
    avoidance_frame.grid_columnconfigure(1, weight=1)
    ttk.Label(avoidance_frame, text="").grid(row=0, column=0, sticky="w")
    ttk.Label(avoidance_frame, text="Note").grid(row=0, column=1, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="Y (mm)").grid(row=0, column=2, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="X (mm)").grid(row=0, column=3, sticky="w", padx=(2, 2))
    ttk.Label(avoidance_frame, text="Radius (mm)").grid(row=0, column=4, sticky="w", padx=(2, 2))

    avoidance_controls: list[Any] = []
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
            return lambda: set_avoidance_from_position(row_index)

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

    return avoidance_controls
