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

def _rate_scale(old_units: str, new_units: str) -> float:
    if old_units == new_units:
        return 1.0
    if old_units == "inch" and new_units == "mm":
        return 25.4
    if old_units == "mm" and new_units == "inch":
        return 1.0 / 25.4
    return 1.0


def _convert_rate_value(raw: str, scale: float) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        val = float(raw)
    except Exception:
        return raw
    return f"{val * scale:.3f}"


def convert_estimate_rates(app, old_units: str, new_units: str) -> None:
    scale = _rate_scale(old_units, new_units)
    if scale == 1.0:
        return
    for var in ("estimate_rate_x_var", "estimate_rate_y_var", "estimate_rate_z_var"):
        entry_var = getattr(app, var, None)
        if entry_var is None:
            continue
        entry_var.set(_convert_rate_value(entry_var.get(), scale))


def update_estimate_rate_units_label(app) -> None:
    units = str(app.unit_mode.get()).lower()
    label = "in/min" if units.startswith("in") else "mm/min"
    for attr in ("estimate_rate_x_units", "estimate_rate_y_units", "estimate_rate_z_units"):
        lbl = getattr(app, attr, None)
        if lbl is None:
            continue
        try:
            lbl.config(text=label)
        except Exception:
            pass


def on_estimate_rates_change(app, _event=None) -> None:
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)


def validate_estimate_rate_text(text: str) -> bool:
    if text == "":
        return True
    if not _RATE_RE.match(text):
        return False
    try:
        return float(text) >= 0.0
    except Exception:
        return False
import re


_RATE_RE = re.compile(r"^\d*\.?\d*$")
