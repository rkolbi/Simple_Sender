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

"""Diagnostics performance-action helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def apply_performance_test_preset(
    app: Any,
    *,
    set_var_value: Callable[[Any, str, Any], None],
    log_suppressed: Callable[[str, BaseException], None],
    showinfo: Callable[[str, str], None],
    showerror: Callable[[str, str], None],
    perf_test_status_poll_interval: float,
) -> None:
    try:
        app._status_perf_metrics_enabled = True
    except Exception as exc:
        log_suppressed(
            "Failed enabling status perf-metric capture in diagnostics preset", exc
        )
    set_var_value(app, "performance_profile_enabled", True)
    set_var_value(app, "performance_leak_watch_enabled", False)
    set_var_value(app, "performance_mode", True)
    set_var_value(app, "gui_logging_enabled", False)
    set_var_value(app, "status_poll_interval", perf_test_status_poll_interval)

    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        settings["performance_profile_enabled"] = True
        settings["performance_leak_watch_enabled"] = False
        settings["performance_mode"] = True
        settings["gui_logging_enabled"] = False
        settings["status_poll_interval"] = perf_test_status_poll_interval

    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            log_suppressed(
                "Failed saving settings for diagnostics perf-test preset", exc
            )
            showerror("Diagnostics preset", f"Failed to save settings:\n{exc}")
            return
    try:
        app.ui_q.put(
            ("log", "[diagnostics] Performance test preset applied (restart required).")
        )
    except Exception as exc:
        log_suppressed("Failed queueing diagnostics preset status log", exc)
    showinfo(
        "Diagnostics preset",
        (
            "Performance test preset applied.\n\n"
            "- Runtime performance profiling: ON\n"
            "- Leak-watch snapshots: OFF\n"
            "- Performance mode: ON\n"
            "- GUI logging: OFF\n"
            f"- Status poll interval: {perf_test_status_poll_interval:.2f}s\n\n"
            "Restart the app before the next run for clean benchmark numbers."
        ),
    )


def save_performance_report_to_logs(
    app: Any,
    *,
    get_log_dir: Callable[[], Path],
    build_performance_report_text: Callable[[Any], str],
    log_suppressed: Callable[[str, BaseException], None],
    showinfo: Callable[[str, str], None],
    showerror: Callable[[str, str], None],
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"simple_sender_performance_report_{timestamp}.txt"
    path = get_log_dir() / filename
    report = build_performance_report_text(app)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log_suppressed("Failed creating logs directory for performance report", exc)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write(report)
            outfile.write("\n")
        showinfo("Save performance report", f"Saved to:\n{path}")
    except Exception as exc:
        showerror("Save performance report", f"Failed to write report:\n{exc}")
