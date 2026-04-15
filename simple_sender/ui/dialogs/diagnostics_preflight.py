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

from typing import Any, Callable

from simple_sender.services.preflight_service import PreflightService

_DEFAULT_PREFLIGHT_SERVICE = PreflightService()


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
    bounds = _DEFAULT_PREFLIGHT_SERVICE.get_bounds(app)
    if bounds is None:
        return None
    return bounds.as_tuple()


def get_travel_limits(app: Any) -> dict[str, float]:
    return _DEFAULT_PREFLIGHT_SERVICE.get_travel_limits(app).as_dict()


def evaluate_run_preflight(
    app: Any,
    *,
    get_bounds: Callable[[Any], Any],
    get_travel_limits: Callable[[Any], dict[str, float]],
) -> tuple[list[str], list[str]]:
    result = PreflightService(
        get_bounds=get_bounds,
        get_travel_limits=get_travel_limits,
    ).validate_job(app)
    return list(result.failures), list(result.warnings)


def run_preflight_check(
    app: Any,
    *,
    evaluate_run_preflight: Callable[[Any], tuple[list[str], list[str]]],
    format_validation_summary: Callable[[Any], list[str]],
    showinfo: Callable[[str, str], None],
    showwarning: Callable[[str, str], None],
) -> None:
    path = getattr(app, "_last_gcode_path", None)
    if not path:
        showinfo("Preflight check", "Load a G-code file first.")
        return
    failures, warnings = evaluate_run_preflight(app)
    report = getattr(app, "_gcode_validation_report", None)
    issues: list[str] = []
    if failures:
        issues.extend(failures)
    if warnings:
        issues.extend(warnings)
    if report is not None:
        issues.extend(format_validation_summary(report))
    if issues:
        title = "Preflight check"
        prefix = "Review before running:\n"
        if failures:
            title = "Preflight check (fail)"
            prefix = "Blocking issues found:\n"
        showwarning(
            title,
            prefix + "\n".join(f"- {item}" for item in issues),
        )
        return
    showinfo("Preflight check", "No issues detected.")
