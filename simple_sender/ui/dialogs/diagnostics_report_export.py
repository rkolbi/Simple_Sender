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

"""Diagnostics report export helpers."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable


def _post_ui_callback(
    app: Any,
    callback,
    *,
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(callback)
            return
        except Exception as exc:
            log_suppressed(
                "Failed posting diagnostics report callback via _post_ui_thread",
                exc,
            )
    after = getattr(app, "after", None)
    if callable(after):
        try:
            after(0, callback)
            return
        except Exception as exc:
            log_suppressed("Failed posting diagnostics report callback to UI thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return
        except Exception as exc:
            log_suppressed("Failed posting diagnostics report callback via ui_q", exc)
    log_suppressed(
        "Dropping diagnostics report callback because no safe UI post path is available",
        RuntimeError("ui thread unavailable"),
    )


def export_session_diagnostics(
    app: Any,
    *,
    run_file_dialog: Callable[..., Any],
    asksaveasfilename: Callable[..., Any],
    build_session_diagnostics_lines: Callable[[Any], list[str]],
    log_suppressed: Callable[[str, BaseException], None],
    showinfo: Callable[[str, str], None],
    showerror: Callable[[str, str], None],
    thread_cls: Any,
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_{timestamp}.txt"
    path = run_file_dialog(
        app,
        asksaveasfilename,
        title="Export diagnostics",
        defaultextension=".txt",
        initialfile=default_name,
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
    )
    if not path:
        return
    out_path = str(path)

    def _write_report_text(lines: list[str]) -> None:
        dir_name = os.path.dirname(out_path)
        if dir_name:
            try:
                os.makedirs(dir_name, exist_ok=True)
            except Exception as exc:
                log_suppressed(
                    "Failed creating export directory for diagnostics report", exc
                )
        text = "\n".join(lines)
        with open(out_path, "w", encoding="utf-8", newline="\n") as outfile:
            for start in range(0, len(text), 16_384):
                outfile.write(text[start : start + 16_384])

    use_background_export = bool(
        getattr(app, "_diagnostics_report_async_export", True)
    )
    after = getattr(app, "after", None)
    if use_background_export and callable(after):
        if bool(getattr(app, "_diagnostics_report_export_inflight", False)):
            showinfo(
                "Export diagnostics",
                "A diagnostics report export is already running.",
            )
            return
        app._diagnostics_report_export_inflight = True

        def _complete_export(error: Exception | None = None) -> None:
            app._diagnostics_report_export_inflight = False
            if error is None:
                showinfo("Export diagnostics", f"Saved to:\n{out_path}")
                return
            showerror("Export diagnostics", f"Failed to write diagnostics:\n{error}")

        def _export_worker() -> None:
            error: Exception | None = None
            try:
                _write_report_text(build_session_diagnostics_lines(app))
            except Exception as exc:
                log_suppressed(
                    "Failed exporting diagnostics report in background worker", exc
                )
                error = exc
            _post_ui_callback(
                app,
                lambda: _complete_export(error),
                log_suppressed=log_suppressed,
            )

        try:
            worker = thread_cls(
                target=_export_worker,
                name="diagnostics-report-export",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            app._diagnostics_report_export_inflight = False
            log_suppressed("Failed starting diagnostics report export thread", exc)

    try:
        _write_report_text(build_session_diagnostics_lines(app))
        showinfo("Export diagnostics", f"Saved to:\n{out_path}")
    except Exception as exc:
        showerror("Export diagnostics", f"Failed to write diagnostics:\n{exc}")
