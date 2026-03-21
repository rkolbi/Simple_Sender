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

"""Diagnostics runtime telemetry display helpers."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable


def open_runtime_telemetry(
    app: Any,
    *,
    runtime_metrics: Callable[[Any], dict[str, Any]],
    format_runtime_metrics: Callable[..., list[str]],
    log_suppressed: Callable[[str, BaseException], None],
    record_task_timing: Callable[..., None],
    center_window: Callable[[Any, Any], None],
    refresh_ms: int,
) -> None:
    existing = getattr(app, "_runtime_telemetry_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            log_suppressed("Failed restoring existing runtime telemetry window", exc)
    win = tk.Toplevel(app)
    app._runtime_telemetry_window = win
    app._runtime_telemetry_after_id = None
    win.title("Runtime telemetry")
    win.minsize(600, 360)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container, text="Runtime telemetry", font=("TkDefaultFont", 12, "bold")
    ).pack(anchor="w")
    ttk.Label(
        container,
        text="Live worker/queue counters. Refreshes every second while this window is open.",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    text = tk.Text(container, wrap="none", height=14, font=("TkFixedFont", 10))
    text.pack(fill="both", expand=True)
    text.configure(state="disabled")
    last_rendered: dict[str, str | None] = {"text": None}
    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))

    def _render() -> None:
        started = time.perf_counter()
        metrics = runtime_metrics(app)
        if metrics:
            lines = format_runtime_metrics(
                metrics, include_samples=True, sample_limit=12
            )
            body = "\n".join(lines) if lines else "Runtime telemetry unavailable."
        else:
            body = "Runtime telemetry unavailable."
        if body == last_rendered["text"]:
            return
        last_rendered["text"] = body
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", body)
        text.configure(state="disabled")
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        record_task_timing(
            app,
            "diagnostics.runtime_telemetry_refresh",
            elapsed_ms,
            success=True,
        )

    def _schedule_refresh() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            app._runtime_telemetry_after_id = win.after(
                refresh_ms,
                _on_refresh_timer,
            )
        except Exception as exc:
            log_suppressed("Failed scheduling runtime telemetry refresh", exc)

    def _on_refresh_timer() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        _render()
        _schedule_refresh()

    def _on_close() -> None:
        after_id = getattr(app, "_runtime_telemetry_after_id", None)
        if after_id is not None:
            try:
                win.after_cancel(after_id)
            except Exception:
                pass
        app._runtime_telemetry_after_id = None
        app._runtime_telemetry_window = None
        win.destroy()

    ttk.Button(btn_row, text="Refresh now", command=_render).pack(side="left")
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)
    _render()
    _schedule_refresh()
    center_window(win, app)
