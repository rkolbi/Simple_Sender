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

import hashlib
import os
import queue
import sys
import tempfile
import threading
import time
from tkinter import messagebox

from simple_sender.gcode_parser import (
    clean_gcode_line,
    parse_gcode_lines,
    split_gcode_lines,
    split_gcode_lines_stream,
)
from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.gcode_source import FileGcodeSource
from simple_sender.utils.constants import (
    GCODE_LOAD_PROGRESS_INTERVAL,
    GCODE_STREAMING_PREVIEW_LINES,
    GCODE_STREAMING_SIZE_THRESHOLD,
    GCODE_STREAMING_LINE_THRESHOLD,
    GCODE_VIEWER_CHUNK_LOAD_THRESHOLD,
    GCODE_VIEWER_CHUNK_SIZE_LOAD_LARGE,
    GCODE_VIEWER_CHUNK_SIZE_SMALL,
    MAX_LINE_LENGTH,
    STREAMING_VALIDATION_PROMPT_TIMEOUT,
    STREAMING_VALIDATION_PROMPT_LINES,
)
from simple_sender.utils.hashing import hash_lines
from simple_sender.ui.job_controls import disable_job_controls
from simple_sender.ui.preview_policy import configure_toolpath_preview, set_preview_streaming_state
from simple_sender.ui.gcode_pipeline_apply import apply_loaded_gcode as _apply_loaded_gcode
from simple_sender.ui.gcode_pipeline_loader import load_gcode_from_path as _load_gcode_from_path


def load_gcode_from_path(app, path: str):
    _load_gcode_from_path(app, path, module=sys.modules[__name__])


def apply_loaded_gcode(
    app,
    path: str,
    lines: list[str],
    *,
    lines_hash: str | None = None,
    validated: bool = False,
    streaming_source: FileGcodeSource | None = None,
    total_lines: int | None = None,
):
    _apply_loaded_gcode(
        app,
        path,
        lines,
        lines_hash=lines_hash,
        validated=validated,
        streaming_source=streaming_source,
        total_lines=total_lines,
        module=sys.modules[__name__],
    )


def schedule_gcode_parse(app, lines: list[str], lines_hash: str | None):
    if not lines:
        app._last_parse_result = None
        app._last_parse_hash = None
        return
    app._gcode_parse_token += 1
    token = app._gcode_parse_token
    arc_step = app.toolpath_panel.get_arc_step_rad(len(lines))

    def worker():
        try:
            def keep_running():
                return token == app._gcode_parse_token

            result = parse_gcode_lines(lines, arc_step, keep_running=keep_running)
        except Exception as exc:
            app.ui_q.put(("log", f"[gcode] Parse failed: {exc}"))

            def apply_error():
                if token != app._gcode_parse_token:
                    return
                app._last_parse_result = None
                app._last_parse_hash = None
                app._stats_token += 1
                app._last_stats = None
                app._last_rate_source = None
                app.gcode_stats_var.set("Estimate unavailable")

            app.after(0, apply_error)
            return
        if result is None:
            return

        def apply_result():
            if token != app._gcode_parse_token:
                return
            app._last_parse_result = result
            app._last_parse_hash = lines_hash
            app.toolpath_panel.apply_parse_result(lines, result, lines_hash=lines_hash)
            app._update_gcode_stats(lines, parse_result=result)

        app.after(0, apply_result)

    threading.Thread(target=worker, daemon=True).start()


def clear_gcode(app):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before clearing the G-code file.")
        return
    existing_source = getattr(app, "_gcode_source", None)
    if existing_source is not None:
        cleanup_path = getattr(existing_source, "_cleanup_path", None)
        try:
            existing_source.close()
        except Exception:
            pass
        if cleanup_path:
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
    app._gcode_source = None
    set_preview_streaming_state(app, False)
    try:
        app._set_job_button_mode("read_job")
    except Exception:
        pass
    app._gcode_total_lines = 0
    app._resume_after_disconnect = False
    app._resume_from_index = None
    app._resume_job_name = None
    app._last_gcode_lines = []
    app._last_gcode_path = None
    app._gcode_hash = None
    app._gcode_validation_report = None
    app._last_parse_result = None
    app._last_parse_hash = None
    app._last_error_index = -1
    _reset_autolevel_state(app)
    app._gcode_parse_token += 1
    app._stats_cache.clear()
    app.grbl.load_gcode([])
    app.gview.set_lines([])
    app.gcode_stats_var.set("No file loaded")
    app.progress_pct.set(0)
    app.status.config(text="G-code cleared")
    disable_job_controls(app)
    app.toolpath_panel.clear()
    app._job_started_at = None
    app._job_completion_notified = False


def _reset_autolevel_state(app) -> None:
    restore = getattr(app, "_auto_level_restore", None)
    keep_temp = False
    leveled_path = getattr(app, "_auto_level_leveled_path", None)
    if isinstance(restore, dict):
        restore_path = restore.get("leveled_path")
        if restore_path and leveled_path:
            try:
                if os.path.normcase(restore_path) == os.path.normcase(leveled_path):
                    keep_temp = bool(restore.get("leveled_temp", False))
            except Exception:
                keep_temp = False
    if (
        leveled_path
        and getattr(app, "_auto_level_leveled_temp", False)
        and not keep_temp
    ):
        try:
            os.remove(leveled_path)
        except OSError:
            pass
    app._auto_level_grid = None
    app._auto_level_height_map = None
    app._auto_level_bounds = None
    app._auto_level_original_lines = None
    app._auto_level_original_path = None
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None
    try:
        app.toolpath_panel.set_autolevel_overlay(None)
    except Exception:
        pass


def _find_overlong_lines(
    lines: list[str],
    *,
    fallback_index: int | None = None,
    fallback_len: int | None = None,
) -> tuple[int, int | None, int | None]:
    too_long = 0
    first_idx = None
    first_len = None
    for idx, line in enumerate(lines):
        line_len = len(line.encode("utf-8")) + 1
        if line_len > MAX_LINE_LENGTH:
            too_long += 1
            if first_idx is None:
                first_idx = idx
                first_len = line_len
    if too_long == 0 and fallback_index is not None:
        too_long = 1
        first_idx = fallback_index
        first_len = fallback_len
    return too_long, first_idx, first_len
