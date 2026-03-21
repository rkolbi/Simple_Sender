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

"""Diagnostics preflight helper functions."""

from __future__ import annotations

from typing import Any


def format_validation_summary(report: Any) -> list[str]:
    if report is None:
        return []
    summary = []
    if getattr(report, "long_line_count", 0):
        summary.append(f"Overlong lines: {report.long_line_count}")
    unsupported_axes = getattr(report, "unsupported_axes", {})
    if unsupported_axes:
        axes = ", ".join(f"{k} x{v}" for k, v in unsupported_axes.items())
        summary.append(f"Unsupported axes: {axes}")
    unsupported_g = getattr(report, "unsupported_g_codes", {})
    if unsupported_g:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_g.items())
        summary.append(f"Unsupported G-codes: {codes}")
    unsupported_m = getattr(report, "unsupported_m_codes", {})
    if unsupported_m:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_m.items())
        summary.append(f"Unsupported M-codes: {codes}")
    grbl_warnings = getattr(report, "grbl_warnings", {})
    if grbl_warnings:
        warnings = ", ".join(f"{k} x{v}" for k, v in grbl_warnings.items())
        summary.append(f"GRBL warnings: {warnings}")
    unsupported_words = getattr(report, "unsupported_words", {})
    if unsupported_words:
        words = ", ".join(f"{k} x{v}" for k, v in unsupported_words.items())
        summary.append(f"Unknown words: {words}")
    hazards = sorted(getattr(report, "modal_hazards", set()))
    if hazards:
        summary.append(f"Modal hazards: {', '.join(hazards)}")
    if getattr(report, "line_issue_count", 0):
        summary.append(f"Line issues: {report.line_issue_count}")
    return summary


def get_bounds(app: Any):
    parse_result = getattr(app, "_last_parse_result", None)
    bounds = getattr(parse_result, "bounds", None) if parse_result else None
    if not bounds:
        quick_bounds = getattr(app, "_gcode_bounds_box", None)
        if isinstance(quick_bounds, dict):
            try:
                bounds = (
                    float(quick_bounds.get("min_x", 0.0) or 0.0),
                    float(quick_bounds.get("max_x", 0.0) or 0.0),
                    float(quick_bounds.get("min_y", 0.0) or 0.0),
                    float(quick_bounds.get("max_y", 0.0) or 0.0),
                    float(quick_bounds.get("min_z", 0.0) or 0.0),
                    float(quick_bounds.get("max_z", 0.0) or 0.0),
                )
            except Exception:
                bounds = None
    if not bounds or len(bounds) < 6:
        return None
    return bounds


def get_travel_limits(app: Any) -> dict[str, float]:
    data = (
        getattr(getattr(app, "settings_controller", None), "_settings_data", {}) or {}
    )
    out: dict[str, float] = {}
    for key, axis in (("$130", "x"), ("$131", "y"), ("$132", "z")):
        raw = data.get(key)
        if not raw:
            continue
        try:
            out[axis] = float(raw[0])
        except Exception:
            continue
    return out


def evaluate_run_preflight(
    app: Any,
    *,
    get_bounds: Any,
    get_travel_limits: Any,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    path = getattr(app, "_last_gcode_path", None)
    has_job = bool(path)
    if not has_job:
        try:
            has_job = bool(getattr(app.gview, "lines_count", 0))
        except Exception:
            has_job = False
    if not has_job:
        failures.append("No G-code job is loaded.")
        return failures, warnings

    if bool(getattr(app, "_alarm_locked", False)):
        failures.append("Controller is in Alarm state. Clear alarm before running.")
    if bool(getattr(app, "_homing_in_progress", False)):
        failures.append("Homing is currently active. Wait until homing finishes.")
    if not bool(getattr(app, "_grbl_ready", False)):
        failures.append("GRBL is not ready yet. Wait for startup/status sync.")
    if not bool(getattr(app, "_status_seen", False)):
        failures.append("No live status has been received yet.")

    bounds = get_bounds(app)
    if not bounds:
        failures.append("Job bounds are unavailable (wait for parsing to complete).")
    else:
        minx, maxx, miny, maxy, minz, maxz = bounds
        span_x = max(0.0, float(maxx) - float(minx))
        span_y = max(0.0, float(maxy) - float(miny))
        span_z = max(0.0, float(maxz) - float(minz))
        travel = get_travel_limits(app)
        if travel:
            if "x" in travel and span_x > travel["x"] + 1e-6:
                failures.append(
                    f"X span {span_x:.3f} mm exceeds machine travel $130={travel['x']:.3f} mm."
                )
            if "y" in travel and span_y > travel["y"] + 1e-6:
                failures.append(
                    f"Y span {span_y:.3f} mm exceeds machine travel $131={travel['y']:.3f} mm."
                )
            if "z" in travel and span_z > travel["z"] + 1e-6:
                failures.append(
                    f"Z span {span_z:.3f} mm exceeds machine travel $132={travel['z']:.3f} mm."
                )
        else:
            warnings.append("Machine travel settings ($130/$131/$132) are unavailable.")

    return failures, warnings
