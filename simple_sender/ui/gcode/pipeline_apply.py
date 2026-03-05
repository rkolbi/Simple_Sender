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
import time

from simple_sender.gcode_source import FileGcodeSource
from simple_sender.utils.task_timing import record_task_timing
from .stats import format_streaming_estimate_text

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_GCODE_APPLY_STAGE_BUDGET_MS = 15.0


def _is_motion_line(line: str) -> bool:
    text = str(line or "").strip().upper()
    if not text:
        return False
    if any(token in text for token in ("G0", "G1", "G2", "G3")):
        return True
    return any(axis in text for axis in ("X", "Y", "Z"))


def _count_motion_lines(lines: list[str]) -> int:
    motion = 0
    for line in lines:
        if _is_motion_line(line):
            motion += 1
    return int(motion)


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


def _read_int_setting(app, *, attr_name: str, key: str, default: int) -> int:
    var = getattr(app, attr_name, None)
    if var is not None:
        try:
            return int(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return int(settings.get(key, default))
        except Exception:
            return int(default)
    return int(default)


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


def _viewer_virtualization_policy(
    app,
    line_count: int,
    deps,
    *,
    sample_only: bool = False,
) -> tuple[bool, int, int]:
    try:
        default_threshold = int(
            getattr(deps, "GCODE_VIEWER_VIRTUALIZE_THRESHOLD_DEFAULT")
        )
    except Exception:
        default_threshold = 120_000
    try:
        low_power_threshold = int(
            getattr(deps, "GCODE_VIEWER_VIRTUALIZE_THRESHOLD_LOW_POWER")
        )
    except Exception:
        low_power_threshold = 40_000
    try:
        default_window = int(getattr(deps, "GCODE_VIEWER_VIRTUAL_WINDOW_SIZE_DEFAULT"))
    except Exception:
        default_window = 2000
    try:
        low_power_window = int(
            getattr(deps, "GCODE_VIEWER_VIRTUAL_WINDOW_SIZE_LOW_POWER")
        )
    except Exception:
        low_power_window = 800

    if _pi_profile_enabled(app):
        threshold = max(1000, int(low_power_threshold))
        window = max(200, int(low_power_window))
        if sample_only:
            # Sample-only loads should virtualize earlier on low-power systems.
            sample_threshold = _read_int_setting(
                app,
                attr_name="streaming_line_threshold",
                key="streaming_line_threshold",
                default=threshold,
            )
            if sample_threshold > 0:
                threshold = min(threshold, max(5000, int(sample_threshold)))
    else:
        threshold = max(1000, int(default_threshold))
        window = max(200, int(default_window))
    return line_count >= threshold, window, threshold


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
    module=None,
):
    deps = module
    if deps is None:
        raise ValueError(
            "pipeline_apply.apply_loaded_gcode requires module dependencies"
        )
    if app.grbl.is_streaming():
        app._gcode_loading = False
        app._finish_gcode_loading()
        deps.messagebox.showwarning(
            "Busy", "Stop the stream before loading a new G-code file."
        )
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
    if (
        app._gcode_validation_report is None
        and streaming_source is None
        and not validated
    ):
        app._gcode_validation_report = deps.validate_gcode_lines(lines)
    app._clear_pending_ui_updates()
    app._last_gcode_lines = lines
    app._gcode_retained_line_count = int(len(lines))
    app._last_gcode_path = path
    if streaming_source is not None:
        app._gcode_hash = lines_hash
    else:
        app._gcode_hash = (
            lines_hash if lines_hash is not None else deps.hash_lines(lines)
        )
    app._stats_cache.clear()
    app._stats_pending_request = None
    stats_after_id = getattr(app, "_stats_after_id", None)
    if stats_after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(stats_after_id)
        except Exception as exc:
            _log_suppressed(
                "Failed canceling pending stats debounce timer after loading G-code",
                exc,
            )
    app._stats_after_id = None
    app._stats_token = int(getattr(app, "_stats_token", 0)) + 1
    app._live_estimate_min = None
    app._live_estimate_total_min = None
    app._live_estimate_observed_total_min = None
    app._live_estimate_display_min = None
    app._live_estimate_display_ts = 0.0
    app._loaded_estimate_total_min = None
    app._loaded_estimate_source = ""
    app._estimate_confidence = "provisional"
    app._estimate_inputs_snapshot = {}
    app._last_stats = None
    app._last_rate_source = None
    app._gcode_stats_sample_executable_lines = 0
    app._gcode_stats_sample_motion_lines = 0
    app._gcode_stats_executable_total_lines = 0
    app._gcode_stats_motion_total_lines = 0
    existing_source = getattr(app, "_gcode_source", None)
    if existing_source is not None and existing_source is not streaming_source:
        cleanup_path = getattr(existing_source, "_cleanup_path", None)
        try:
            existing_source.close()
        except Exception as exc:
            _log_suppressed(
                "Failed closing existing G-code source before applying newly loaded job",
                exc,
            )
        if cleanup_path:
            try:
                deps.os.remove(cleanup_path)
            except OSError as exc:
                _log_suppressed(
                    "Failed removing existing G-code source cleanup path", exc
                )
    app._gcode_source = streaming_source
    if streaming_source is None:
        app._gcode_storage_mode = "in_memory"
        app._gcode_load_mode = "strict"
        app._gcode_index_mode = "none"
        app._gcode_source_line_count_known = True
        app._gcode_source_offset_count = 0
        app._gcode_source_offset_type = ""
        app._gcode_prepare_sample_line_count = 0
        app._gcode_prepare_sample_head_lines = 0
        app._gcode_prepare_sample_tail_lines = 0
        app._gcode_prepare_sample_interval_lines = 0
        app._gcode_prepare_sample_max_lines = 0
        app._gcode_file_size_bytes = 0
        app._gcode_file_line_count = 0
        app._gcode_file_line_count_known = False
        app._gcode_total_lines_known = True
        app._gcode_executable_lines = max(0, int(total_lines or len(lines)))
        app._gcode_executable_lines_known = True
        app._gcode_motion_lines = _count_motion_lines(lines)
        app._gcode_motion_lines_known = True
        app._gcode_prepare_executable_total_lines = 0
        app._gcode_prepare_motion_total_lines = 0
        app._gcode_prepare_sampled_executable_lines = 0
        app._gcode_prepare_sampled_motion_lines = 0
        app._gcode_quick_scan_ms = 0.0
        app._gcode_bounds_box = None
        app._gcode_bounds_confidence = "rough"
        app._gcode_dimensions_confidence = "rough"
        app._gcode_dimensions_confidence_reasons = {}
        app._gcode_estimated_job_time_sec = None
        app._gcode_estimate_confidence = "provisional"
        app._gcode_estimate_confidence_reasons = {}
        app._gcode_estimate_replaced_quick = False
        app._gcode_post_popup_background_tasks = "none"
        app._gcode_offset_index_enabled = False
    else:
        app._gcode_storage_mode = "file_backed_streaming"
        app._gcode_load_mode = str(
            getattr(streaming_source, "_load_mode", "quick_scan_ready")
            or "quick_scan_ready"
        )
        index_mode = "none"
        try:
            if hasattr(streaming_source, "index_mode"):
                index_mode = str(streaming_source.index_mode() or "none")
            else:
                index_mode = "none"
        except Exception:
            index_mode = "none"
        app._gcode_index_mode = index_mode
        line_count_known = True
        try:
            checker = getattr(streaming_source, "line_count_known", None)
            if callable(checker):
                line_count_known = bool(checker())
            else:
                line_count_known = bool(
                    getattr(streaming_source, "_line_count_known", True)
                )
        except Exception:
            line_count_known = True
        app._gcode_source_line_count_known = bool(line_count_known)
        offsets = getattr(streaming_source, "_offsets", None)
        try:
            app._gcode_source_offset_count = (
                int(len(offsets)) if offsets is not None else 0
            )
        except Exception:
            app._gcode_source_offset_count = 0
        app._gcode_source_offset_type = str(getattr(offsets, "typecode", "") or "")
        try:
            app._gcode_prepare_sample_line_count = int(
                getattr(streaming_source, "_prepare_sample_line_count", 0) or 0
            )
        except Exception:
            sampled_lines = getattr(streaming_source, "_prepare_sampled_lines", None)
            try:
                app._gcode_prepare_sample_line_count = (
                    int(len(sampled_lines)) if sampled_lines is not None else 0
                )
            except Exception:
                app._gcode_prepare_sample_line_count = 0
        app._gcode_prepare_sample_head_lines = int(
            getattr(streaming_source, "_prepare_sample_head_lines", 0) or 0
        )
        app._gcode_prepare_sample_tail_lines = int(
            getattr(streaming_source, "_prepare_sample_tail_lines", 0) or 0
        )
        app._gcode_prepare_sample_interval_lines = int(
            getattr(streaming_source, "_prepare_sample_interval_lines", 0) or 0
        )
        app._gcode_prepare_sample_max_lines = int(
            getattr(streaming_source, "_prepare_sample_max_lines", 0) or 0
        )
        app._gcode_file_size_bytes = int(
            getattr(streaming_source, "_prepare_file_size_bytes", 0) or 0
        )
        app._gcode_file_line_count = int(
            getattr(streaming_source, "_prepare_file_line_count", 0) or 0
        )
        app._gcode_file_line_count_known = bool(
            getattr(streaming_source, "_prepare_file_line_count_known", False)
        )
        source_path = str(getattr(streaming_source, "path", "") or "").strip()
        if app._gcode_file_size_bytes <= 0 and source_path:
            try:
                app._gcode_file_size_bytes = max(
                    0, int(deps.os.path.getsize(source_path))
                )
            except Exception as exc:
                _log_suppressed(
                    "Failed resolving file size from file-backed source path", exc
                )
        if app._gcode_file_line_count <= 0:
            fallback_line_count = 0
            try:
                fallback_line_count = int(
                    getattr(streaming_source, "_line_count", 0) or 0
                )
            except Exception:
                fallback_line_count = 0
            if fallback_line_count <= 0:
                try:
                    fallback_line_count = int(total_lines or 0)
                except Exception:
                    fallback_line_count = 0
            if fallback_line_count <= 0:
                try:
                    fallback_line_count = int(len(lines))
                except Exception:
                    fallback_line_count = 0
            app._gcode_file_line_count = max(0, int(fallback_line_count))
        if (not app._gcode_file_line_count_known) and bool(line_count_known):
            app._gcode_file_line_count_known = True
        if app._gcode_file_line_count <= 0:
            app._gcode_file_line_count_known = False
        app._gcode_prepare_executable_total_lines = int(
            getattr(streaming_source, "_prepare_executable_total_lines", 0) or 0
        )
        app._gcode_executable_lines = int(app._gcode_prepare_executable_total_lines)
        app._gcode_executable_lines_known = bool(
            getattr(
                streaming_source,
                "_prepare_executable_total_lines_known",
                app._gcode_file_line_count_known,
            )
        )
        app._gcode_prepare_motion_total_lines = int(
            getattr(streaming_source, "_prepare_motion_total_lines", 0) or 0
        )
        app._gcode_motion_lines = int(app._gcode_prepare_motion_total_lines)
        app._gcode_motion_lines_known = bool(
            getattr(
                streaming_source,
                "_prepare_motion_total_lines_known",
                app._gcode_file_line_count_known,
            )
        )
        app._gcode_prepare_sampled_executable_lines = int(
            getattr(streaming_source, "_prepare_sampled_executable_lines", 0) or 0
        )
        app._gcode_prepare_sampled_motion_lines = int(
            getattr(streaming_source, "_prepare_sampled_motion_lines", 0) or 0
        )
        app._gcode_quick_scan_ms = float(
            getattr(streaming_source, "_quick_scan_ms", 0.0) or 0.0
        )
        app._gcode_bounds_box = getattr(streaming_source, "_quick_bounds_box", None)
        app._gcode_bounds_confidence = str(
            getattr(streaming_source, "_quick_bounds_confidence", "rough") or "rough"
        )
        app._gcode_dimensions_confidence = str(
            getattr(
                streaming_source,
                "_quick_dimensions_confidence",
                app._gcode_bounds_confidence,
            )
            or app._gcode_bounds_confidence
        )
        reasons = getattr(
            streaming_source, "_quick_dimensions_confidence_reasons", None
        )
        app._gcode_dimensions_confidence_reasons = (
            dict(reasons) if isinstance(reasons, dict) else {}
        )
        app._gcode_estimated_job_time_sec = getattr(
            streaming_source, "_quick_estimated_job_time_sec", None
        )
        app._gcode_estimate_confidence = str(
            getattr(streaming_source, "_quick_estimate_confidence", "provisional")
            or "provisional"
        )
        est_reasons = getattr(
            streaming_source, "_quick_estimate_confidence_reasons", None
        )
        app._gcode_estimate_confidence_reasons = (
            dict(est_reasons) if isinstance(est_reasons, dict) else {}
        )
        app._gcode_estimate_replaced_quick = False
        app._gcode_post_popup_background_tasks = "none"
        quick_snapshot = getattr(
            streaming_source, "_quick_estimate_inputs_snapshot", None
        )
        if isinstance(quick_snapshot, dict):
            app._estimate_inputs_snapshot = dict(quick_snapshot)
        if getattr(streaming_source, "_quick_estimated_job_time_sec", None) is not None:
            try:
                seconds = float(
                    getattr(streaming_source, "_quick_estimated_job_time_sec")
                )
                if seconds > 0.0:
                    app._loaded_estimate_total_min = max(0.0, seconds / 60.0)
                    app._loaded_estimate_source = "quick_scan"
            except Exception:
                pass
        app._estimate_confidence = str(
            getattr(streaming_source, "_quick_estimate_confidence", "provisional")
            or "provisional"
        )
        app._gcode_offset_index_enabled = bool(index_mode in {"full", "sparse"})
        app._gcode_total_lines_known = bool(app._gcode_file_line_count_known)
    deps.set_sample_streaming_state(app, sample_only)
    try:
        app._set_job_button_mode(
            "auto_level" if (lines or streaming_source is not None) else "read_job"
        )
    except Exception as exc:
        _log_suppressed(
            "Failed updating job button mode after applying loaded G-code", exc
        )
    if streaming_source is not None:
        app._gcode_total_lines = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    else:
        app._gcode_total_lines = int(total_lines) if total_lines is not None else len(lines)
    if streaming_source is not None and app._gcode_total_lines <= 0 and total_lines is not None:
        app._gcode_total_lines = int(max(0, int(total_lines)))
    if streaming_source is not None and app._gcode_executable_lines <= 0:
        app._gcode_executable_lines = int(max(0, app._gcode_total_lines))
    if streaming_source is None and app._gcode_file_line_count <= 0:
        app._gcode_file_line_count = int(len(lines))
        app._gcode_file_line_count_known = True
    if streaming_source is None and app._gcode_executable_lines <= 0:
        app._gcode_executable_lines = int(app._gcode_total_lines)
        app._gcode_executable_lines_known = True
    if streaming_source is None and (not getattr(app, "_gcode_motion_lines_known", False)):
        app._gcode_motion_lines = _count_motion_lines(lines)
        app._gcode_motion_lines_known = True
    if streaming_source is None:
        app._gcode_total_lines_known = True
    load_started_at = getattr(app, "_gcode_load_started_at", None)
    if load_started_at is not None:
        try:
            app._gcode_time_to_stream_ready_ms = max(
                0.0,
                (time.perf_counter() - float(load_started_at)) * 1000.0,
            )
        except Exception:
            app._gcode_time_to_stream_ready_ms = None
    if streaming_source is not None:
        app.grbl.load_gcode(streaming_source, name=deps.os.path.basename(path))
        if not sample_only and lines and _should_prime_file_backed_send_cache(app):
            prime_cache = getattr(app.grbl, "prime_gcode_send_cache", None)
            if callable(prime_cache):
                try:
                    prime_cache(lines)
                except Exception as exc:
                    _log_suppressed(
                        "Failed priming in-memory G-code send cache for file-backed job",
                        exc,
                    )
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
        if restore_path and deps.os.path.normcase(
            restore_path
        ) == deps.os.path.normcase(path):
            original_lines = restore.get("original_lines")
            if isinstance(original_lines, list):
                app._auto_level_original_lines = original_lines
            original_path = restore.get("original_path")
            if original_path:
                app._auto_level_original_path = original_path
            original_source_path = restore.get("original_source_path")
            if original_source_path:
                app._auto_level_original_source_path = original_source_path
            original_hash = restore.get("original_hash")
            if original_hash is not None:
                app._auto_level_original_hash = str(original_hash)
            original_total_lines = restore.get("original_total_lines")
            if original_total_lines is not None:
                try:
                    app._auto_level_original_total_lines = int(original_total_lines)
                except (TypeError, ValueError):
                    app._auto_level_original_total_lines = 0
            leveled_lines = restore.get("leveled_lines")
            if isinstance(leveled_lines, list):
                app._auto_level_leveled_lines = leveled_lines
            app._auto_level_leveled_path = restore.get("leveled_path")
            app._auto_level_leveled_temp = bool(restore.get("leveled_temp", False))
            app._auto_level_leveled_name = restore.get("leveled_name")
    source_path_for_autolevel = ""
    if isinstance(streaming_source, FileGcodeSource):
        candidate = str(getattr(streaming_source, "path", "") or "").strip()
        if candidate:
            try:
                if deps.os.path.isfile(candidate):
                    source_path_for_autolevel = candidate
            except Exception as exc:
                _log_suppressed(
                    "Failed checking file-backed source path for auto-level snapshot",
                    exc,
                )
    if not source_path_for_autolevel:
        candidate = str(path or "").strip()
        if candidate:
            try:
                if deps.os.path.isfile(candidate):
                    source_path_for_autolevel = candidate
            except Exception as exc:
                _log_suppressed(
                    "Failed checking G-code path for auto-level snapshot", exc
                )
    app._auto_level_job_source_path = source_path_for_autolevel or None
    app._auto_level_job_hash = app._gcode_hash
    try:
        app._auto_level_job_total_lines = int(app._gcode_total_lines or len(lines))
    except Exception:
        app._auto_level_job_total_lines = int(len(lines))
    quick_autolevel = (
        getattr(streaming_source, "_quick_autolevel_prereq_snapshot", None)
        if streaming_source is not None
        else None
    )
    if isinstance(quick_autolevel, dict):
        app._auto_level_prereq_snapshot = dict(quick_autolevel)

    def _current_autolevel_bounds() -> (
        tuple[float, float, float, float, float, float] | None
    ):
        parse_result = getattr(app, "_last_parse_result", None)
        parse_bounds = (
            getattr(parse_result, "bounds", None) if parse_result is not None else None
        )
        if parse_bounds:
            return parse_bounds
        quick_bounds = getattr(app, "_gcode_bounds_box", None)
        if isinstance(quick_bounds, dict):
            try:
                return (
                    float(quick_bounds.get("min_x", 0.0) or 0.0),
                    float(quick_bounds.get("max_x", 0.0) or 0.0),
                    float(quick_bounds.get("min_y", 0.0) or 0.0),
                    float(quick_bounds.get("max_y", 0.0) or 0.0),
                    float(quick_bounds.get("min_z", 0.0) or 0.0),
                    float(quick_bounds.get("max_z", 0.0) or 0.0),
                )
            except Exception:
                pass
        return None

    def _refresh_autolevel_prereq_snapshot(stage: str) -> None:
        bounds = _current_autolevel_bounds()
        bounds_ready = bool(bounds and len(bounds) >= 6)
        width = 0.0
        height = 0.0
        min_z = 0.0
        max_z = 0.0
        if bounds_ready and bounds is not None:
            try:
                width = max(0.0, float(bounds[1]) - float(bounds[0]))
                height = max(0.0, float(bounds[3]) - float(bounds[2]))
                min_z = float(bounds[4])
                max_z = float(bounds[5])
            except Exception:
                bounds_ready = False
                bounds = None
                width = 0.0
                height = 0.0
                min_z = 0.0
                max_z = 0.0
        snapshot = {
            "stage": str(stage or "").strip() or "load",
            "prepared_at_ts": float(time.time()),
            "source_path": str(getattr(app, "_auto_level_job_source_path", "") or ""),
            "source_exists": bool(
                getattr(app, "_auto_level_job_source_path", None)
                and deps.os.path.isfile(
                    str(getattr(app, "_auto_level_job_source_path", "") or "")
                )
            ),
            "source_hash": str(getattr(app, "_auto_level_job_hash", "") or ""),
            "source_total_lines": int(
                getattr(app, "_auto_level_job_total_lines", 0) or 0
            ),
            "bounds_ready": bool(bounds_ready),
            "bounds": tuple(bounds) if bounds_ready and bounds is not None else None,
            "bounds_confidence": str(
                getattr(app, "_gcode_bounds_confidence", "") or "rough"
            ),
            "xy_width_mm": float(width),
            "xy_height_mm": float(height),
            "z_min_mm": float(min_z),
            "z_max_mm": float(max_z),
            "probe_grid_applicable": bool(width > 0.0 and height > 0.0),
            "streaming_mode": bool(getattr(app, "_gcode_streaming_mode", False)),
            "storage_mode": str(getattr(app, "_gcode_storage_mode", "") or ""),
        }
        app._auto_level_prereq_snapshot = snapshot

    _refresh_autolevel_prereq_snapshot("load")
    policy_line_count = int(total_lines) if total_lines is not None else len(lines)
    viewer_ready = False
    secondary_ready = True

    def finalize_load_if_ready() -> None:
        if not (viewer_ready and secondary_ready):
            return
        started_at = getattr(app, "_gcode_load_started_at", None)
        app._gcode_load_started_at = None
        if started_at is not None:
            try:
                elapsed_ms = max(
                    0.0, (time.perf_counter() - float(started_at)) * 1000.0
                )
                app._gcode_time_to_popup_close_ms = float(elapsed_ms)
                record_task_timing(
                    app, "gcode.load.end_to_end", elapsed_ms, success=True
                )
                record_task_timing(
                    app, "gcode.load.time_to_popup_close", elapsed_ms, success=True
                )
            except Exception as exc:
                _log_suppressed("Failed recording end-to-end G-code load timing", exc)
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

    name = deps.os.path.basename(path)

    def on_done():
        def _apply_done() -> None:
            nonlocal viewer_ready
            viewer_ready = True
            finalize_load_if_ready()

        after_fn = getattr(app, "after", None)
        if callable(after_fn):
            try:
                after_fn(0, _apply_done)
                return
            except Exception as exc:
                _log_suppressed(
                    "Failed deferring loaded-viewer completion callback", exc
                )
        _apply_done()

    def on_progress(done, total):
        app._set_gcode_loading_progress(done, total, name)

    if not lines and streaming_source is None:
        app.gview.set_lines([])
        app._set_gcode_loading_progress(0, 0, name)
        on_done()
        return

    use_virtualized_viewer, virtual_window, virtual_threshold = (
        _viewer_virtualization_policy(
            app,
            policy_line_count,
            deps,
            sample_only=sample_only,
        )
    )
    app._gcode_viewer_policy_line_count = int(policy_line_count)
    app._gcode_viewer_sample_line_count = int(len(lines))
    app._gcode_viewer_virtualization_threshold = int(virtual_threshold)
    app._gcode_viewer_sample_only = bool(sample_only)
    app._gcode_viewer_virtual_window = int(virtual_window)

    def _measure_section(section_timings_ms: dict[str, float], name: str, fn):
        started = time.perf_counter()
        result = fn()
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        section_timings_ms[str(name)] = elapsed_ms
        return result

    def _apply_stats_and_status() -> dict[str, float]:
        section_timings_ms: dict[str, float] = {}
        deferred_stats_work = None

        def _set_estimate_placeholder() -> None:
            refresher = getattr(app, "_refresh_gcode_stats_display", None)
            if callable(refresher):
                refresher()
                return
            app.gcode_stats_var.set(format_streaming_estimate_text(app))

        def _set_calculating_stats() -> None:
            app.gcode_stats_var.set("Calculating stats...")

        def _set_no_file_loaded() -> None:
            app.gcode_stats_var.set("")

        def _run_parse_schedule() -> None:
            deps.schedule_gcode_parse(app, lines, app._gcode_hash)

        if sample_only:
            if isinstance(streaming_source, FileGcodeSource):
                _measure_section(
                    section_timings_ms,
                    "stats.set_placeholder",
                    _set_estimate_placeholder,
                )
            else:
                _measure_section(
                    section_timings_ms,
                    "stats.set_placeholder",
                    _set_estimate_placeholder,
                )
        elif lines:
            _measure_section(
                section_timings_ms, "stats.set_placeholder", _set_calculating_stats
            )
            deferred_stats_work = _run_parse_schedule
        else:
            _measure_section(
                section_timings_ms, "stats.set_placeholder", _set_no_file_loaded
            )
        total_label = (
            app._gcode_total_lines if app._gcode_total_lines is not None else len(lines)
        )
        mode_label = " (file-backed streaming)" if streaming_source is not None else ""
        _measure_section(
            section_timings_ms,
            "status.loaded_label",
            lambda: app.status.config(
                text=f"Loaded: {deps.os.path.basename(path)}  ({total_label} lines){mode_label}"
            ),
        )
        if callable(deferred_stats_work):
            after_fn = getattr(app, "after", None)
            settle_delay_ms = int(
                max(0, int(getattr(app, "_gcode_stats_settle_delay_ms", 0) or 0))
            )
            if callable(after_fn):
                _measure_section(
                    section_timings_ms,
                    "stats.schedule_deferred",
                    lambda: after_fn(settle_delay_ms, deferred_stats_work),
                )
            else:
                _measure_section(
                    section_timings_ms, "stats.defer_fallback_sync", deferred_stats_work
                )
        return section_timings_ms

    def _apply_viewer_lines() -> dict[str, float]:
        section_timings_ms: dict[str, float] = {}

        def _emit_viewer_mode_log(message: str) -> None:
            logger_fn = getattr(getattr(app, "streaming_controller", None), "log", None)
            if callable(logger_fn):
                logger_fn(message)
            else:
                app.ui_q.put(("log", message))

        if use_virtualized_viewer and hasattr(app.gview, "set_lines_virtualized"):
            app._gcode_viewer_mode = "virtualized"
            app._gcode_viewer_chunk_size = None
            try:
                message = (
                    f"[gcode] Virtualized G-code viewer enabled for {policy_line_count:,} line job "
                    f"(showing {len(lines):,} lines, threshold {virtual_threshold:,}, "
                    f"window {virtual_window:,}, sample_only={bool(sample_only)})."
                )
                _measure_section(
                    section_timings_ms,
                    "viewer.virtualized_mode_log",
                    lambda: _emit_viewer_mode_log(message),
                )
            except Exception as exc:
                _log_suppressed("Failed reporting virtualized G-code viewer mode", exc)
            _measure_section(
                section_timings_ms,
                "viewer.progress_start",
                lambda: app._set_gcode_loading_progress(0, len(lines), name),
            )

            def _apply_virtualized_viewer() -> None:
                try:
                    app.gview.set_lines_virtualized(
                        lines,
                        window_size=virtual_window,
                        on_done=on_done,
                        on_progress=on_progress,
                        defer_initial_render=True,
                    )
                except TypeError:
                    app.gview.set_lines_virtualized(
                        lines,
                        window_size=virtual_window,
                        on_done=on_done,
                        on_progress=on_progress,
                    )

            _measure_section(
                section_timings_ms,
                "viewer.virtualized_apply",
                _apply_virtualized_viewer,
            )
            return section_timings_ms

        chunk_size = (
            deps.GCODE_VIEWER_CHUNK_SIZE_LOAD_LARGE
            if len(lines) > deps.GCODE_VIEWER_CHUNK_LOAD_THRESHOLD
            else deps.GCODE_VIEWER_CHUNK_SIZE_SMALL
        )
        app._gcode_viewer_mode = "chunked"
        app._gcode_viewer_chunk_size = int(chunk_size)
        _measure_section(
            section_timings_ms,
            "viewer.progress_start",
            lambda: app._set_gcode_loading_progress(0, len(lines), name),
        )
        try:
            message = (
                f"[gcode] Chunked G-code viewer load selected for {policy_line_count:,} line job "
                f"(showing {len(lines):,} lines, threshold {virtual_threshold:,}, "
                f"chunk {int(chunk_size):,}, sample_only={bool(sample_only)})."
            )
            _measure_section(
                section_timings_ms,
                "viewer.chunked_mode_log",
                lambda: _emit_viewer_mode_log(message),
            )
        except Exception as exc:
            _log_suppressed("Failed reporting chunked G-code viewer mode", exc)
        _measure_section(
            section_timings_ms,
            "viewer.chunked_apply",
            lambda: app.gview.set_lines_chunked(
                lines,
                chunk_size=chunk_size,
                on_done=on_done,
                on_progress=on_progress,
            ),
        )
        return section_timings_ms

    post_load_stages = [
        ("stats_and_status", _apply_stats_and_status),
        ("viewer_stage_apply", _apply_viewer_lines),
    ]

    if not defer_viewer_stage_apply:
        for _stage_name, stage_fn in post_load_stages:
            stage_fn()
        return

    stage_timings_ms: dict[str, float] = {}
    stage_subsection_timings_ms: dict[str, dict[str, float]] = {}

    def _run_stage(stage_index: int) -> None:
        if stage_index >= len(post_load_stages):
            if stage_timings_ms:
                logger.info(
                    "[ui] gcode_loaded_stream apply stage detail: %s",
                    ", ".join(
                        f"{name}={ms:.2f}ms" for name, ms in stage_timings_ms.items()
                    ),
                )
            return
        stage_name, stage_fn = post_load_stages[stage_index]
        started_at = time.perf_counter()
        try:
            stage_result = stage_fn()
        except Exception as exc:
            _log_suppressed(f"Failed deferred apply stage: {stage_name}", exc)
            return
        if isinstance(stage_result, dict):
            stage_subsection_timings_ms[str(stage_name)] = {
                str(k): float(v) for k, v in stage_result.items()
            }
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        stage_timings_ms[str(stage_name)] = elapsed_ms
        if elapsed_ms > _GCODE_APPLY_STAGE_BUDGET_MS:
            logger.info(
                "[ui] gcode_loaded_stream apply stage exceeded %.1fms: stage=%s %.2fms",
                _GCODE_APPLY_STAGE_BUDGET_MS,
                str(stage_name),
                elapsed_ms,
            )
            stage_detail = stage_subsection_timings_ms.get(str(stage_name), {})
            if stage_detail:
                detail_rows = sorted(
                    stage_detail.items(), key=lambda item: item[1], reverse=True
                )
                logger.info(
                    "[ui] gcode_loaded_stream apply stage subsection detail: stage=%s %s",
                    str(stage_name),
                    ", ".join(f"{name}={ms:.2f}ms" for name, ms in detail_rows[:8]),
                )
        after_fn = getattr(app, "after", None)
        if callable(after_fn):
            try:
                after_fn(0, lambda idx=stage_index + 1: _run_stage(idx))
                return
            except Exception as exc:
                _log_suppressed(
                    "Failed scheduling deferred apply stage continuation", exc
                )
        _run_stage(stage_index + 1)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            after_fn(0, lambda: _run_stage(0))
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling deferred G-code apply stages", exc)
    _run_stage(0)
