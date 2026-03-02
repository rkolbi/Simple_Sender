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

import logging

from simple_sender.gcode_source import FileGcodeSource

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _read_bool_setting(app, *, attr_name: str, key: str, default: bool = False) -> bool:
    var = getattr(app, attr_name, None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get(key, default))
        except Exception:
            return bool(default)
    return bool(default)


def _pi_profile_enabled(app) -> bool:
    return _read_bool_setting(
        app,
        attr_name="pi_profile_enabled",
        key="pi_profile_enabled",
        default=False,
    )


def _should_prime_file_backed_send_cache(app) -> bool:
    # File-backed jobs already stream from disk; on low-power profiles we skip
    # in-memory priming to avoid duplicate payload caches.
    return not _pi_profile_enabled(app)


def _viewer_virtualization_policy(app, line_count: int, deps) -> tuple[bool, int]:
    try:
        default_threshold = int(getattr(deps, "GCODE_VIEWER_VIRTUALIZE_THRESHOLD_DEFAULT"))
    except Exception:
        default_threshold = 120_000
    try:
        low_power_threshold = int(getattr(deps, "GCODE_VIEWER_VIRTUALIZE_THRESHOLD_LOW_POWER"))
    except Exception:
        low_power_threshold = 40_000
    try:
        default_window = int(getattr(deps, "GCODE_VIEWER_VIRTUAL_WINDOW_SIZE_DEFAULT"))
    except Exception:
        default_window = 2000
    try:
        low_power_window = int(getattr(deps, "GCODE_VIEWER_VIRTUAL_WINDOW_SIZE_LOW_POWER"))
    except Exception:
        low_power_window = 800

    if _pi_profile_enabled(app):
        threshold = max(1000, int(low_power_threshold))
        window = max(200, int(low_power_window))
    else:
        threshold = max(1000, int(default_threshold))
        window = max(200, int(default_window))
    return line_count >= threshold, window


def apply_loaded_gcode(
    app,
    path: str,
    lines: list[str],
    *,
    lines_hash: str | None = None,
    validated: bool = False,
    streaming_source: FileGcodeSource | None = None,
    total_lines: int | None = None,
    preview_only: bool = False,
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
    if app._gcode_validation_report is None and streaming_source is None and not validated:
        app._gcode_validation_report = deps.validate_gcode_lines(lines)
    app._clear_pending_ui_updates()
    app._last_gcode_lines = lines
    app._last_gcode_path = path
    if streaming_source is not None:
        app._gcode_hash = lines_hash
    else:
        app._gcode_hash = lines_hash if lines_hash is not None else deps.hash_lines(lines)
    app._stats_cache.clear()
    app._stats_pending_request = None
    stats_after_id = getattr(app, "_stats_after_id", None)
    if stats_after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(stats_after_id)
        except Exception as exc:
            _log_suppressed("Failed canceling pending stats debounce timer after loading G-code", exc)
    app._stats_after_id = None
    app._stats_token = int(getattr(app, "_stats_token", 0)) + 1
    app._live_estimate_min = None
    app._last_stats = None
    app._last_rate_source = None
    existing_source = getattr(app, "_gcode_source", None)
    if existing_source is not None and existing_source is not streaming_source:
        cleanup_path = getattr(existing_source, "_cleanup_path", None)
        try:
            existing_source.close()
        except Exception as exc:
            _log_suppressed("Failed closing existing G-code source before applying newly loaded job", exc)
        if cleanup_path:
            try:
                deps.os.remove(cleanup_path)
            except OSError as exc:
                _log_suppressed("Failed removing existing G-code source cleanup path", exc)
    app._gcode_source = streaming_source
    deps.set_preview_streaming_state(app, preview_only)
    try:
        app._set_job_button_mode("auto_level" if (lines or streaming_source is not None) else "read_job")
    except Exception as exc:
        _log_suppressed("Failed updating job button mode after applying loaded G-code", exc)
    app._gcode_total_lines = total_lines if total_lines is not None else len(lines)
    if streaming_source is not None:
        app.grbl.load_gcode(streaming_source, name=deps.os.path.basename(path))
        if not preview_only and lines and _should_prime_file_backed_send_cache(app):
            prime_cache = getattr(app.grbl, "prime_gcode_send_cache", None)
            if callable(prime_cache):
                try:
                    prime_cache(lines)
                except Exception as exc:
                    _log_suppressed("Failed priming in-memory G-code send cache for file-backed job", exc)
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
    deps.configure_toolpath_preview(
        app,
        path,
        lines,
        streaming_source,
        preview_only,
    )
    if preview_only:
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
    mode_label = " (preview-only)" if preview_only else ""
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

    use_virtualized_viewer, virtual_window = _viewer_virtualization_policy(app, len(lines), deps)
    if use_virtualized_viewer and hasattr(app.gview, "set_lines_virtualized"):
        try:
            message = (
                f"[gcode] Virtualized G-code viewer enabled for {len(lines):,} lines "
                f"(window {virtual_window:,})."
            )
            logger_fn = getattr(getattr(app, "streaming_controller", None), "log", None)
            if callable(logger_fn):
                logger_fn(message)
            else:
                app.ui_q.put(("log", message))
        except Exception as exc:
            _log_suppressed("Failed reporting virtualized G-code viewer mode", exc)
        app._set_gcode_loading_progress(0, len(lines), name)
        app.gview.set_lines_virtualized(
            lines,
            window_size=virtual_window,
            on_done=on_done,
            on_progress=on_progress,
        )
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
