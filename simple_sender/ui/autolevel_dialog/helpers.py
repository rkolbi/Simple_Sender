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

import math
import tkinter as tk

from simple_sender.autolevel.height_map import HeightMap


def parse_float_var(var: tk.Variable, label: str) -> float:
    try:
        return float(var.get())
    except Exception as exc:
        raise ValueError(f"Invalid {label}") from exc


def parse_int_optional_var(var: tk.Variable) -> int | None:
    raw = var.get().strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except Exception as exc:
        raise ValueError("Invalid max points") from exc
    return val if val > 0 else None


def safe_float_text(var: tk.Variable) -> float:
    try:
        return float(var.get())
    except Exception:
        return 0.0


def validate_probe_settings_vars(
    safe_z_var: tk.StringVar,
    probe_depth_var: tk.StringVar,
    probe_feed_var: tk.StringVar,
    retract_var: tk.StringVar,
    settle_var: tk.StringVar,
) -> list[str]:
    try:
        safe_z = parse_float_var(safe_z_var, "safe Z")
    except ValueError:
        return ["Safe Z must be a number."]
    try:
        probe_depth = parse_float_var(probe_depth_var, "probe depth")
    except ValueError:
        return ["Probe depth must be a number."]
    try:
        probe_feed = parse_float_var(probe_feed_var, "probe feed")
    except ValueError:
        return ["Probe feed must be a number."]
    try:
        retract_z = parse_float_var(retract_var, "retract Z")
    except ValueError:
        return ["Retract Z must be a number."]
    try:
        settle_time = parse_float_var(settle_var, "settle time")
    except ValueError:
        return ["Settle time must be a number."]
    errors: list[str] = []
    if probe_depth <= 0:
        errors.append("Probe depth must be > 0.")
    if probe_feed <= 0:
        errors.append("Probe feed must be > 0.")
    if retract_z < 0:
        errors.append("Retract Z must be >= 0.")
    if settle_time < 0:
        errors.append("Settle time must be >= 0.")
    if math.isnan(safe_z) or math.isinf(safe_z):
        errors.append("Safe Z must be a valid number.")
    return errors


def update_stats_summary(height_map: HeightMap | None, stats_var: tk.StringVar) -> None:
    if height_map is None:
        stats_var.set("")
        return
    try:
        if not height_map.is_complete():
            stats_var.set("Last probe: map incomplete.")
            return
        stats = height_map.stats()
    except Exception:
        stats_var.set("")
        return
    if stats:
        stats_var.set(
            "Last probe: "
            f"Min {stats.min_z:.4f} Max {stats.max_z:.4f} Span {stats.span():.4f} mm | "
            f"RMS {stats.rms_roughness:.4f} mm | "
            f"Outliers {stats.outliers}/{stats.point_count}"
        )
    else:
        stats_var.set("")


def probe_connection_state(app) -> tuple[bool, str]:
    if not getattr(app, "connected", False):
        return False, "Connect to enable probing."
    if not getattr(app, "_grbl_ready", False) or not getattr(app, "_status_seen", False):
        return False, "Waiting for GRBL status."
    if getattr(app, "_alarm_locked", False):
        return False, "Clear the alarm before probing."
    return True, ""
