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

from simple_sender.gcode_source import FileGcodeSource


def apply_loaded_gcode(
    app,
    path: str,
    lines: list[str],
    *,
    lines_hash: str | None = None,
    validated: bool = False,
    streaming_source: FileGcodeSource | None = None,
    total_lines: int | None = None,
    module,
):
    deps = module
    if app.grbl.is_streaming():
        app._gcode_loading = False
        app._finish_gcode_loading()
        deps.messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
        app.status.config(text="G-code load skipped (streaming)")
        return
    if not validated and streaming_source is None:
        result = deps.split_gcode_lines(lines, deps.MAX_LINE_LENGTH)
        if result.failed_index is not None:
            max_len = deps.MAX_LINE_LENGTH
            too_long, first_idx, first_len = deps._find_overlong_lines(
                lines,
                fallback_index=result.failed_index,
                fallback_len=result.failed_len,
            )
            app._gcode_loading = False
            app._finish_gcode_loading()
            idx_msg = "?"
            if first_idx is not None:
                idx_msg = str(first_idx + 1)
            len_msg = "?"
            if first_len is not None:
                len_msg = str(first_len)
            deps.messagebox.showerror(
                "Open G-code",
                f"{too_long} non-empty line(s) exceed GRBL's {max_len}-byte limit.\n"
                f"First at line {idx_msg} ({len_msg} bytes including newline).",
            )
            app.status.config(text="G-code load failed")
            return
        if result.modified_count:
            if result.split_count:
                msg = (
                    f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                    f"{deps.MAX_LINE_LENGTH}-byte limit (split {result.split_count})."
                )
            else:
                msg = (
                    f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                    f"{deps.MAX_LINE_LENGTH}-byte limit."
                )
            app.streaming_controller.log(msg)
        lines = result.lines
        lines_hash = deps.hash_lines(lines)
    if app._gcode_validation_report is None and streaming_source is None:
        app._gcode_validation_report = deps.validate_gcode_lines(lines)
    app._clear_pending_ui_updates()
    app._last_gcode_lines = lines
    app._last_gcode_path = path
    if streaming_source is not None:
        app._gcode_hash = lines_hash
    else:
        app._gcode_hash = lines_hash if lines_hash is not None else deps.hash_lines(lines)
    app._stats_cache.clear()
    app._live_estimate_min = None
    app._last_stats = None
    app._last_rate_source = None
    existing_source = getattr(app, "_gcode_source", None)
    if existing_source is not None and existing_source is not streaming_source:
        cleanup_path = getattr(existing_source, "_cleanup_path", None)
        try:
            existing_source.close()
        except Exception:
            pass
        if cleanup_path:
            try:
                deps.os.remove(cleanup_path)
            except OSError:
                pass
    app._gcode_source = streaming_source
    deps.set_preview_streaming_state(app, streaming_source is not None)
    try:
        app._set_job_button_mode("auto_level" if (lines or streaming_source is not None) else "read_job")
    except Exception:
        pass
    app._gcode_total_lines = total_lines if total_lines is not None else len(lines)
    if streaming_source is not None:
        app.grbl.load_gcode(streaming_source, name=deps.os.path.basename(path))
    else:
        app.grbl.load_gcode(lines, name=deps.os.path.basename(path))
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1
    app._last_parse_result = None
    app._last_parse_hash = None
    deps._reset_autolevel_state(app)
    restore = getattr(app, "_auto_level_restore", None)
    if isinstance(restore, dict):
        app._auto_level_restore = None
        restore_path = restore.get("leveled_path")
        if restore_path and deps.os.path.normcase(restore_path) == deps.os.path.normcase(path):
            original_lines = restore.get("original_lines")
            if isinstance(original_lines, list):
                app._auto_level_original_lines = original_lines
            original_path = restore.get("original_path")
            if original_path:
                app._auto_level_original_path = original_path
            leveled_lines = restore.get("leveled_lines")
            if isinstance(leveled_lines, list):
                app._auto_level_leveled_lines = leveled_lines
            app._auto_level_leveled_path = restore.get("leveled_path")
            app._auto_level_leveled_temp = bool(restore.get("leveled_temp", False))
            app._auto_level_leveled_name = restore.get("leveled_name")
    deps.configure_toolpath_preview(app, path, lines, streaming_source)
    if streaming_source is not None:
        app.gcode_stats_var.set("Preview only (streaming mode)")
    elif lines:
        app.gcode_stats_var.set("Calculating stats...")
        deps.schedule_gcode_parse(app, lines, app._gcode_hash)
    else:
        app.gcode_stats_var.set("No file loaded")
    total_label = (
        app._gcode_total_lines
        if app._gcode_total_lines is not None
        else len(lines)
    )
    mode_label = " (streaming)" if streaming_source is not None else ""
    app.status.config(
        text=f"Loaded: {deps.os.path.basename(path)}  ({total_label} lines){mode_label}"
    )

    name = deps.os.path.basename(path)

    def on_done():
        app._gcode_loading = False
        app._finish_gcode_loading()
        if (
            app.connected
            and lines
            and app._grbl_ready
            and app._status_seen
            and not app._alarm_locked
        ):
            app.btn_run.config(state="normal")
            app.btn_resume_from.config(state="normal")
        else:
            app.btn_run.config(state="disabled")
            app.btn_resume_from.config(state="disabled")

    def on_progress(done, total):
        app._set_gcode_loading_progress(done, total, name)

    if not lines and streaming_source is None:
        app.gview.set_lines([])
        app._set_gcode_loading_progress(0, 0, name)
        on_done()
        return

    chunk_size = (
        deps.GCODE_VIEWER_CHUNK_SIZE_LOAD_LARGE
        if len(lines) > deps.GCODE_VIEWER_CHUNK_LOAD_THRESHOLD
        else deps.GCODE_VIEWER_CHUNK_SIZE_SMALL
    )
    app._set_gcode_loading_progress(0, len(lines), name)
    app.gview.set_lines_chunked(
        lines,
        chunk_size=chunk_size,
        on_done=on_done,
        on_progress=on_progress,
    )
