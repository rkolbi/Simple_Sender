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
# ruff: noqa: F401

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import hashlib
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import types
import array
from tkinter import messagebox

from simple_sender.constants.messages import BusyMessages, DialogTitles, StatusMessages
from simple_sender.gcode_parser import (
    clean_gcode_line,
    parse_gcode_lines,
    split_gcode_lines,
    split_gcode_lines_stream,
)
from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.gcode_source import FileGcodeSource
from simple_sender.ui.autolevel_state import clear_active_auto_level_map
from simple_sender.utils.hashing import hash_lines
from simple_sender.utils.task_timing import record_task_timing
from simple_sender.utils.constants import (
    GCODE_LOAD_PROGRESS_INTERVAL,
    GCODE_STREAMING_SAMPLE_LINES,
    GCODE_STREAMING_SIZE_THRESHOLD,
    GCODE_PREP_SAMPLE_HEAD_LINES,
    GCODE_PREP_SAMPLE_TAIL_LINES,
    GCODE_PREP_SAMPLE_INTERVAL_LINES,
    GCODE_PREP_SAMPLE_MAX_LINES,
    GCODE_PREP_FAST_SCAN_MAX_LINES_DEFAULT,
    GCODE_PREP_FAST_SCAN_MAX_LINES_LOW_POWER,
    GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES,
    GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES,
    GCODE_ESTIMATE_SAMPLE_MAX_SCALE,
    GCODE_OFFSET_INDEX_MAX_FILE_BYTES,
    GCODE_OFFSET_INDEX_MAX_LINES,
    GCODE_OFFSET_INDEX_SPARSE_MIN_LINES,
    GCODE_OFFSET_INDEX_SPARSE_STRIDE_LINES,
    GCODE_ULTRA_LARGE_SIZE_THRESHOLD,
    GCODE_STREAMING_LINE_THRESHOLD,
    GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT,
    GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER,
    GCODE_IN_MEMORY_SEND_CACHE_THRESHOLD,
    TEMP_FILE_BUFFER_SIZE,
    MAX_LINE_LENGTH,
)
from simple_sender.utils.temp_paths import get_preferred_temp_dir
from simple_sender.ui.job_controls import disable_job_controls
from .source_transaction import (
    abort_source_transaction,
    commit_source_transaction,
    reserve_source_transaction,
)
from .pipeline_apply import apply_loaded_gcode as _apply_loaded_gcode
from .pipeline_loader import load_gcode_from_path as _load_gcode_from_path

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()

_CLEAR_GCODE_RUNTIME_DEFAULTS: dict[str, object] = {
    "_gcode_total_lines": 0,
    "_gcode_total_lines_known": False,
    "_gcode_executable_lines": 0,
    "_gcode_executable_lines_known": False,
    "_gcode_motion_lines": 0,
    "_gcode_motion_lines_known": False,
    "_gcode_storage_mode": "none",
    "_gcode_load_mode": "",
    "_gcode_index_mode": "none",
    "_gcode_source_line_count_known": False,
    "_gcode_time_to_stream_ready_ms": None,
    "_gcode_time_to_popup_close_ms": None,
    "_gcode_retained_line_count": 0,
    "_gcode_source_offset_count": 0,
    "_gcode_source_offset_type": "",
    "_gcode_offset_index_enabled": False,
    "_gcode_prepare_sample_line_count": 0,
    "_gcode_prepare_sample_head_lines": 0,
    "_gcode_prepare_sample_tail_lines": 0,
    "_gcode_prepare_sample_interval_lines": 0,
    "_gcode_prepare_sample_max_lines": 0,
    "_gcode_file_size_bytes": 0,
    "_gcode_file_line_count": 0,
    "_gcode_file_line_count_known": False,
    "_gcode_quick_scan_ms": 0.0,
    "_gcode_bounds_box": None,
    "_gcode_bounds_confidence": "rough",
    "_gcode_dimensions_confidence": "rough",
    "_gcode_dimensions_confidence_reasons": {},
    "_gcode_dimensions_source": "scan",
    "_gcode_estimated_job_time_sec": None,
    "_gcode_estimate_confidence": "provisional",
    "_gcode_estimate_confidence_reasons": {},
    "_gcode_estimate_replaced_quick": False,
    "_gcode_units_source": "scan",
    "_gcode_ssmeta_present": False,
    "_gcode_ssmeta": {},
    "_gcode_ssmeta_scan_reduced": False,
    "_gcode_prepare_executable_total_lines": 0,
    "_gcode_prepare_motion_total_lines": 0,
    "_gcode_prepare_sampled_executable_lines": 0,
    "_gcode_prepare_sampled_motion_lines": 0,
    "_gcode_stats_compute_mode": "",
    "_gcode_stats_sample_scale": 1.0,
    "_gcode_stats_sample_line_count": 0,
    "_gcode_stats_sample_total_lines": 0,
    "_gcode_stats_sample_executable_lines": 0,
    "_gcode_stats_sample_motion_lines": 0,
    "_gcode_stats_executable_total_lines": 0,
    "_gcode_stats_motion_total_lines": 0,
    "_gcode_post_popup_background_tasks": "none",
    "_gcode_full_line_cache_profile": "",
    "_gcode_full_line_cache_cap_lines": 0,
    "_gcode_full_line_cache_cap_hit": False,
    "_gcode_sample_line_cap": 0,
    "_resume_after_disconnect": False,
    "_resume_from_index": None,
    "_resume_job_name": None,
    "_stream_state": "loaded",
    "_stream_start_ts": None,
    "_stream_pause_total": 0.0,
    "_stream_paused_at": None,
    "_stream_done_pending_idle": False,
    "_last_gcode_lines": [],
    "_last_gcode_path": None,
    "_gcode_hash": None,
    "_gcode_validation_report": None,
    "_last_parse_result": None,
    "_last_parse_hash": None,
    "_auto_level_job_source_path": None,
    "_auto_level_job_hash": None,
    "_auto_level_job_total_lines": 0,
    "_live_estimate_min": None,
    "_live_estimate_total_min": None,
    "_live_estimate_observed_total_min": None,
    "_live_estimate_display_min": None,
    "_live_estimate_display_ts": 0.0,
    "_loaded_estimate_total_min": None,
    "_loaded_estimate_source": "",
    "_estimate_confidence": "provisional",
    "_estimate_inputs_snapshot": {},
    "_last_stats": None,
    "_last_rate_source": None,
    "_last_error_index": -1,
    "_manual_queue_drop_total": 0,
    "_gcode_restore_failed": False,
    "_gcode_restore_failure_message": "",
}


def set_sample_streaming_state(app, streaming: bool) -> None:
    app._gcode_streaming_mode = bool(streaming)


def _lightweight_cached_parse_result(result):
    try:
        cached = type(result)(
            segments=result.segments,
            bounds=result.bounds,
            moves=[],
        )
    except Exception:
        cached = types.SimpleNamespace(
            segments=result.segments,
            bounds=result.bounds,
            moves=[],
        )
    try:
        setattr(cached, "_includes_moves", False)
    except Exception:
        pass
    return cached


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _post_ui(app, func, *args, **kwargs) -> None:
    # Test harnesses may run worker targets inline on the main thread.
    if threading.current_thread() is threading.main_thread():
        after = getattr(app, "after", None)
        if callable(after):
            try:
                after(0, lambda: func(*args, **kwargs))
                return
            except Exception as exc:
                _log_suppressed("Failed posting UI callback via app.after", exc)
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(func, *args, **kwargs)
            return
        except Exception as exc:
            _log_suppressed("Failed posting UI callback via _post_ui_thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", func, args, kwargs))
        except Exception as exc:
            _log_suppressed("Failed posting UI callback via ui_q", exc)


def _snapshot_macro_state(app) -> dict[str, object] | None:
    try:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_ns = macro_vars.get("macro")
            state_ns = getattr(macro_ns, "state", None)
            if not isinstance(state_ns, types.SimpleNamespace):
                return None
            return dict(vars(state_ns))
    except Exception as exc:
        _log_suppressed("Failed snapshotting macro.state before clearing G-code", exc)
        return None


def _restore_macro_state(app, snapshot: dict[str, object] | None) -> None:
    if snapshot is None:
        return
    try:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_ns = macro_vars.get("macro")
            if not isinstance(macro_ns, types.SimpleNamespace):
                macro_ns = types.SimpleNamespace()
                macro_vars["macro"] = macro_ns
            state_ns = getattr(macro_ns, "state", None)
            if not isinstance(state_ns, types.SimpleNamespace):
                state_ns = types.SimpleNamespace()
                setattr(macro_ns, "state", state_ns)
            state_data = vars(state_ns)
            state_data.clear()
            state_data.update(snapshot)
    except Exception as exc:
        _log_suppressed("Failed restoring macro.state after clearing G-code", exc)


def _clone_state_default(value: object) -> object:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _apply_state_defaults(app, defaults: dict[str, object]) -> None:
    for attr_name, value in defaults.items():
        setattr(app, attr_name, _clone_state_default(value))


def load_gcode_from_path(app, path: str):
    return _load_gcode_from_path(app, path, module=sys.modules[__name__])


def apply_loaded_gcode(
    app,
    path: str,
    lines: list[str],
    *,
    lines_hash: str | None = None,
    validated: bool = False,
    streaming_source: FileGcodeSource | None = None,
    total_lines: int | None = None,
    sample_only: bool = False,
    defer_viewer_stage_apply: bool = False,
):
    return _apply_loaded_gcode(
        app,
        path,
        lines,
        lines_hash=lines_hash,
        validated=validated,
        streaming_source=streaming_source,
        total_lines=total_lines,
        sample_only=sample_only,
        defer_viewer_stage_apply=defer_viewer_stage_apply,
        module=sys.modules[__name__],
    )


def schedule_gcode_parse(app, lines: list[str], lines_hash: str | None):
    if not lines:
        app._last_parse_result = None
        app._last_parse_hash = None
        app._gcode_parsing_active = False
        return
    app._gcode_parse_token += 1
    token = app._gcode_parse_token
    arc_step = 0.17453292519943295  # 10 degrees in radians
    parse_limit = None
    app._gcode_parsing_active = True

    def _clear_parse_active_flag() -> None:
        try:
            if token == app._gcode_parse_token:
                app._gcode_parsing_active = False
        except Exception:
            pass

    def worker():
        parse_started_at = time.perf_counter()
        parse_success = False
        try:
            try:

                def keep_running():
                    return token == app._gcode_parse_token

                result = parse_gcode_lines(
                    lines,
                    arc_step,
                    keep_running=keep_running,
                    max_segments=parse_limit,
                    include_moves=True,
                )
                parse_success = result is not None
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

                _post_ui(app, apply_error)
                return
            if result is None:
                return

            def apply_result():
                if token != app._gcode_parse_token:
                    return
                cached_result = result
                # Avoid retaining large move lists in long-lived cache.
                if len(lines) > int(GCODE_IN_MEMORY_SEND_CACHE_THRESHOLD):
                    cached_result = _lightweight_cached_parse_result(result)
                try:
                    setattr(result, "_includes_moves", True)
                except Exception as exc:
                    _log_suppressed(
                        "Failed tagging parse result with include-moves marker", exc
                    )
                app._last_parse_result = cached_result
                app._last_parse_hash = lines_hash
                app._update_gcode_stats(lines, parse_result=result)

            _post_ui(app, apply_result)
        finally:
            elapsed_ms = max(0.0, (time.perf_counter() - parse_started_at) * 1000.0)
            record_task_timing(
                app, "gcode.parse.sample", elapsed_ms, success=parse_success
            )

    def _worker_wrapper() -> None:
        try:
            worker()
        finally:
            _clear_parse_active_flag()

    threading.Thread(target=_worker_wrapper, daemon=True).start()


def _install_cleared_gcode_ui(
    app,
    *,
    macro_state_snapshot,
    keep_safety_lock: bool = False,
) -> None:
    app._gcode_load_token += 1
    app._gcode_loading = False
    try:
        app._finish_gcode_loading()
    except Exception as exc:
        _log_suppressed("Failed finishing G-code loading popup while clearing", exc)
    try:
        app._clear_pending_ui_updates()
    except Exception as exc:
        _log_suppressed("Failed clearing pending UI updates while clearing G-code", exc)
    existing_source = getattr(app, "_gcode_source", None)
    if existing_source is not None:
        cleanup_path = getattr(existing_source, "_cleanup_path", None)
        try:
            existing_source.close()
        except Exception as exc:
            _log_suppressed("Failed closing existing G-code source during clear", exc)
        if cleanup_path:
            try:
                os.remove(cleanup_path)
            except OSError as exc:
                _log_suppressed(
                    "Failed removing temporary G-code cleanup file during clear", exc
                )
    app._gcode_source = None
    set_sample_streaming_state(app, False)
    try:
        app._set_job_button_mode("read_job")
    except Exception as exc:
        _log_suppressed("Failed resetting job button mode after clear", exc)
    _apply_state_defaults(app, _CLEAR_GCODE_RUNTIME_DEFAULTS)
    _reset_autolevel_state(app)
    app._gcode_parse_token += 1
    after_id = getattr(app, "_stats_after_id", None)
    if after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(after_id)
        except Exception as exc:
            _log_suppressed(
                "Failed canceling pending stats debounce timer during clear", exc
            )
    app._stats_after_id = None
    app._stats_pending_request = None
    app._stats_token += 1
    app._stats_cache.clear()
    app.gview.clear()
    app._live_gcode_past_count = 0
    app._live_gcode_current_count = 0
    app._live_gcode_next_count = 0
    app._live_gcode_pending_depth = 0
    app._live_gcode_last_acked_index = -1
    app._live_gcode_last_acked_byte_offset = 0
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    try:
        file_info_var = getattr(app, "file_info_var", None)
        if file_info_var is not None:
            file_info_var.set("")
    except Exception:
        pass
    app.progress_pct.set(0)
    try:
        app.buffer_fill.set("Buffer: 0%")
        app.buffer_fill_pct.set(0)
    except Exception as exc:
        _log_suppressed(
            "Failed resetting buffer-fill UI state after clearing G-code", exc
        )
    try:
        app.throughput_var.set("TX: 0 B/s")
    except Exception as exc:
        _log_suppressed(
            "Failed resetting throughput UI state after clearing G-code", exc
        )
    app.status.config(text=StatusMessages.GCODE_CLEARED)
    disable_job_controls(app)
    try:
        ready = bool(
            app.connected
            and app._grbl_ready
            and app._status_seen
            and not app._alarm_locked
        )
        app._set_manual_controls_enabled(bool(ready and not keep_safety_lock))
        app._set_streaming_lock(bool(keep_safety_lock))
    except Exception as exc:
        _log_suppressed(
            "Failed restoring control-state lock after clearing G-code", exc
        )
    try:
        app._refresh_toolbar_action_focus()
    except Exception as exc:
        _log_suppressed("Failed refreshing toolbar focus after clearing G-code", exc)
    _restore_macro_state(app, macro_state_snapshot)
    app._job_started_at = None
    app._job_completion_notified = False
    app._job_completion_finalize_pending = False
    file_info_refresher = getattr(app, "_refresh_file_info_tab", None)
    if callable(file_info_refresher):
        try:
            file_info_refresher()
        except Exception as exc:
            _log_suppressed("Failed refreshing Job Info after clearing G-code", exc)
    return None


def clear_gcode(app):
    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
        messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_CLEARING_GCODE,
        )
        return False
    macro_state_snapshot = _snapshot_macro_state(app)
    identity = None
    ui_generation = 0
    try:
        recovery_checker = getattr(app.grbl, "recovery_required", None)
        keep_safety_lock = bool(
            callable(recovery_checker) and recovery_checker()
        )
        ui_generation, admission = reserve_source_transaction(app, [], name=None)
        if not bool(getattr(admission, "accepted", False)):
            app.status.config(
                text="Clear blocked; the worker still owns the displayed G-code source."
            )
            return False
        identity = admission.identity
        _install_cleared_gcode_ui(
            app,
            macro_state_snapshot=macro_state_snapshot,
            keep_safety_lock=keep_safety_lock,
        )
        if not commit_source_transaction(app, identity, ui_generation):
            raise RuntimeError("Worker rejected the cleared-source commit")
        return True
    except Exception as exc:
        if identity is not None:
            abort_source_transaction(
                app,
                identity,
                ui_generation,
                reason=f"clear UI installation failed: {exc}",
            )
        try:
            disable_job_controls(app)
            app.status.config(
                text="G-code clear failed closed; no worker source is runnable."
            )
        except Exception as ui_exc:
            _log_suppressed("Failed reporting fail-closed G-code clear", ui_exc)
        logger.exception("G-code clear transaction failed")
        return False


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
        except OSError as exc:
            _log_suppressed(
                "Failed removing temporary auto-level output during reset", exc
            )
    clear_active_auto_level_map(
        app,
        "Auto-Level map invalidated by G-code source replacement.",
    )
    app._auto_level_prereq_snapshot = {}
    app._auto_level_original_lines = None
    app._auto_level_original_path = None
    app._auto_level_original_source_path = None
    app._auto_level_original_hash = None
    app._auto_level_original_total_lines = 0
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None


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
