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
import threading
import time
import re
from typing import Callable

from simple_sender.gcode_parser import parse_gcode_lines
from simple_sender.gcode_source import FileGcodeSource
from simple_sender.utils.constants import (
    GCODE_STATS_COOPERATIVE_YIELD_LINES,
    GCODE_STATS_COOPERATIVE_YIELD_SLEEP_S,
    GCODE_PREP_STATS_SAMPLE_THRESHOLD_LINES,
    GCODE_STATS_CACHE_MAX_ENTRIES,
    GCODE_STATS_DEBOUNCE_MS,
    GCODE_STATS_FULL_SCAN_BACKGROUND_TAB_SLEEP_S,
    GCODE_STATS_FULL_SCAN_DELAY_LARGE_MS,
    GCODE_STATS_FULL_SCAN_DELAY_THRESHOLD_LINES,
    GCODE_STATS_FULL_SCAN_THROTTLE_LINES,
    GCODE_STATS_FULL_SCAN_THROTTLE_SLEEP_S,
    GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES,
    GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES,
    GCODE_ESTIMATE_SAMPLE_MAX_SCALE,
)
from simple_sender.utils.task_timing import record_task_timing

logger = logging.getLogger(__name__)

_LIVE_ESTIMATE_DISPLAY_INTERVAL_S = 1.0
_LIVE_ESTIMATE_DISPLAY_EMA_ALPHA = 0.2
_LIVE_ESTIMATE_DISPLAY_MAX_UP_STEP_MIN = 0.25
_LIVE_ESTIMATE_OBSERVED_MIN_PROGRESS = 0.03
_LIVE_ESTIMATE_OBSERVED_MIN_DONE = 20
_LIVE_ESTIMATE_OBSERVED_MIN_ELAPSED_S = 30.0
_LIVE_ESTIMATE_OBSERVED_BLEND_MAX = 0.65
_MODAL_SCAN_MAX_LINES = 5000
_GCODE_STATS_COOPERATIVE_CHUNK_BUDGET_MS = 40.0

_TOKEN_G20 = re.compile(r"(?<![0-9.])G20(?![0-9.])")
_TOKEN_G21 = re.compile(r"(?<![0-9.])G21(?![0-9.])")
_TOKEN_G90 = re.compile(r"(?<![0-9.])G90(?![0-9.])")
_TOKEN_G91 = re.compile(r"(?<![0-9.])G91(?![0-9.])")
_TOKEN_G93 = re.compile(r"(?<![0-9.])G93(?![0-9.])")
_TOKEN_G94 = re.compile(r"(?<![0-9.])G94(?![0-9.])")
_TOKEN_MOTION_G = re.compile(r"(?<![0-9.])G(?:0|1|2|3)(?![0-9.])")
_TOKEN_AXIS_WORD = re.compile(r"[XYZ][-+]?(?:\d+(?:\.\d*)?|\.\d+)?", re.IGNORECASE)

_ESTIMATE_CONFIDENCE_PROVISIONAL = "provisional"
_ESTIMATE_CONFIDENCE_CONFIDENT = "confident"


def _confidence_badge(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    return "CONFIDENT" if value == _ESTIMATE_CONFIDENCE_CONFIDENT else "ROUGH"


def _set_gcode_status_text_if_changed(app, text: str) -> None:
    normalized = str(text or "")
    if normalized == str(getattr(app, "_gcode_status_last_text", "") or ""):
        return
    app._gcode_status_last_text = normalized
    app.gcode_stats_var.set(normalized)


def _job_loaded_for_status(app) -> bool:
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "").strip().lower()
    if storage_mode and storage_mode != "none":
        return True
    if getattr(app, "_gcode_source", None) is not None:
        return True
    last_lines = getattr(app, "_last_gcode_lines", None)
    if isinstance(last_lines, list) and len(last_lines) > 0:
        return True
    if int(getattr(app, "_gcode_total_lines", 0) or 0) > 0:
        return True
    if int(getattr(app, "_gcode_file_line_count", 0) or 0) > 0:
        return True
    return False


def _current_bounds_box(app, stats: dict | None = None) -> dict[str, float] | None:
    if isinstance(stats, dict):
        bounds = stats.get("bounds")
        if isinstance(bounds, (tuple, list)) and len(bounds) >= 6:
            try:
                min_x, max_x, min_y, max_y, min_z, max_z = [
                    float(bounds[i]) for i in range(6)
                ]
                return {
                    "min_x": min_x,
                    "max_x": max_x,
                    "min_y": min_y,
                    "max_y": max_y,
                    "min_z": min_z,
                    "max_z": max_z,
                    "width": max(0.0, max_x - min_x),
                    "height": max(0.0, max_y - min_y),
                }
            except Exception:
                pass
    raw = getattr(app, "_gcode_bounds_box", None)
    if isinstance(raw, dict):
        return raw
    return None


def _fmt_dim_value(value: float | None, *, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "in":
        return f"{value / 25.4:.3f}"
    return f"{value:.2f}"


def _format_dimensions_block(app, stats: dict | None = None) -> str:
    bounds = _current_bounds_box(app, stats)
    mm_x = mm_y = mm_z = None
    if isinstance(bounds, dict):
        try:
            mm_x = max(
                0.0,
                float(bounds.get("max_x", 0.0) or 0.0)
                - float(bounds.get("min_x", 0.0) or 0.0),
            )
            mm_y = max(
                0.0,
                float(bounds.get("max_y", 0.0) or 0.0)
                - float(bounds.get("min_y", 0.0) or 0.0),
            )
            min_z = bounds.get("min_z", None)
            max_z = bounds.get("max_z", None)
            if min_z is not None and max_z is not None:
                mm_z = max(0.0, float(max_z) - float(min_z))
        except Exception:
            mm_x = mm_y = mm_z = None
    dim_conf = str(
        getattr(app, "_gcode_dimensions_confidence", "")
        or getattr(app, "_gcode_bounds_confidence", "rough")
    )
    return (
        "Job Dimensions: "
        f"{_fmt_dim_value(mm_x, unit='mm')},{_fmt_dim_value(mm_y, unit='mm')},{_fmt_dim_value(mm_z, unit='mm')} mm / "
        f"{_fmt_dim_value(mm_x, unit='in')},{_fmt_dim_value(mm_y, unit='in')},{_fmt_dim_value(mm_z, unit='in')} in "
        f"[{_confidence_badge(dim_conf)}]"
    )


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalise_rate_tuple(values) -> tuple[float, float, float] | None:
    if not isinstance(values, tuple) or len(values) != 3:
        return None
    try:
        return (float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        return None


def _is_motion_line_for_estimate(line: str) -> bool:
    text = str(line or "").strip().upper()
    if not text:
        return False
    if _TOKEN_MOTION_G.search(text):
        return True
    return bool(_TOKEN_AXIS_WORD.search(text))


def _sample_line_characteristics(lines: list[str] | None) -> tuple[int, int]:
    if not isinstance(lines, list):
        return 0, 0
    executable = 0
    motion = 0
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        executable += 1
        if _is_motion_line_for_estimate(text):
            motion += 1
    return executable, motion


def _has_complete_grbl_motion_settings(motion_settings: dict | None) -> bool:
    if not isinstance(motion_settings, dict):
        return False
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        if _safe_float(motion_settings.get(key)) is None:
            return False
    return True


def _resolve_estimate_confidence(
    app,
    *,
    rate_source: str | None,
    motion_settings: dict | None,
) -> str:
    observed_total = _safe_float(
        getattr(app, "_live_estimate_observed_total_min", None)
    )
    if observed_total is not None and observed_total > 0.0:
        return _ESTIMATE_CONFIDENCE_CONFIDENT
    is_connected = bool(getattr(app, "connected", False))
    if not is_connected:
        return _ESTIMATE_CONFIDENCE_PROVISIONAL
    if str(
        rate_source or ""
    ).strip().lower() == "grbl" and _has_complete_grbl_motion_settings(motion_settings):
        return _ESTIMATE_CONFIDENCE_CONFIDENT
    return _ESTIMATE_CONFIDENCE_PROVISIONAL


def _estimate_total_min_from_stats(stats: dict | None) -> float | None:
    if not isinstance(stats, dict):
        return None
    time_min = _safe_float(stats.get("time_min"))
    if time_min is None:
        return None
    rapid_min = _safe_float(stats.get("rapid_min"))
    total_min = max(0.0, time_min)
    if rapid_min is not None:
        total_min += max(0.0, rapid_min)
    return total_min


def _set_loaded_estimate_total_min(app, total_min: float | None, source: str) -> None:
    if total_min is None:
        return
    try:
        total_min_f = max(0.0, float(total_min))
    except Exception:
        return
    app._loaded_estimate_total_min = total_min_f
    app._loaded_estimate_source = str(source or "").strip() or "unknown"


def _loaded_estimate_total_min(app) -> float | None:
    val = _safe_float(getattr(app, "_loaded_estimate_total_min", None))
    if val is None:
        return None
    return max(0.0, val)


def _stats_total_min(app) -> float | None:
    return _estimate_total_min_from_stats(getattr(app, "_last_stats", None))


def _raw_estimated_job_total_min(app) -> float | None:
    loaded_total_min = _loaded_estimate_total_min(app)
    if loaded_total_min is not None:
        return loaded_total_min
    stats_total_min = _stats_total_min(app)
    if stats_total_min is not None:
        return stats_total_min
    live_total_min = _safe_float(getattr(app, "_live_estimate_total_min", None))
    if live_total_min is not None:
        return max(0.0, live_total_min)
    return None


def _extract_motion_setting(settings_data, key: str) -> float | None:
    if not isinstance(settings_data, dict):
        return None
    raw_entry = settings_data.get(key)
    if isinstance(raw_entry, tuple):
        raw = raw_entry[0] if raw_entry else None
    else:
        raw = raw_entry
    val = _safe_float(raw)
    if val is None:
        return None
    return val


def _build_modal_context_snapshot(sample_lines: list[str] | None) -> dict:
    units = None
    distance_mode = None
    feed_mode = None
    scanned = 0
    if not isinstance(sample_lines, list):
        return {
            "units": units,
            "distance_mode": distance_mode,
            "feed_mode": feed_mode,
            "sampled_lines": 0,
            "sample_limit": int(_MODAL_SCAN_MAX_LINES),
        }
    scan_limit = min(int(_MODAL_SCAN_MAX_LINES), len(sample_lines))
    for idx in range(scan_limit):
        line = str(sample_lines[idx] or "").upper()
        if not line:
            continue
        scanned += 1
        if _TOKEN_G20.search(line):
            units = "inch"
        elif _TOKEN_G21.search(line):
            units = "mm"
        if _TOKEN_G90.search(line):
            distance_mode = "absolute"
        elif _TOKEN_G91.search(line):
            distance_mode = "relative"
        if _TOKEN_G93.search(line):
            feed_mode = "inverse_time"
        elif _TOKEN_G94.search(line):
            feed_mode = "units_per_minute"
        if units is not None and distance_mode is not None and feed_mode is not None:
            break
    return {
        "units": units,
        "distance_mode": distance_mode,
        "feed_mode": feed_mode,
        "sampled_lines": int(scanned),
        "sample_limit": int(_MODAL_SCAN_MAX_LINES),
    }


def _capture_estimation_inputs_snapshot(
    app,
    *,
    rapid_rates: tuple[float, float, float] | None,
    accel_rates: tuple[float, float, float] | None,
    rate_source: str | None,
    stats_mode: str,
    sample_scale: float,
    sample_lines: int,
    sample_total_lines: int,
    sample_executable_lines: int,
    sample_motion_lines: int,
    executable_total_lines: int,
    motion_total_lines: int,
    modal_sample_lines: list[str] | None,
) -> None:
    motion_settings = {}
    settings_controller = getattr(app, "settings_controller", None)
    settings_data = (
        getattr(settings_controller, "_settings_data", None)
        if settings_controller is not None
        else None
    )
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        motion_settings[key] = _extract_motion_setting(settings_data, key)
    confidence = _resolve_estimate_confidence(
        app,
        rate_source=rate_source,
        motion_settings=motion_settings,
    )
    setattr(app, "_estimate_confidence", confidence)

    snapshot = {
        "captured_at_ts": float(time.time()),
        "capture_stage": "prepare"
        if bool(getattr(app, "_gcode_loading", False))
        else "runtime",
        "gcode_hash": str(getattr(app, "_gcode_hash", "") or ""),
        "gcode_storage_mode": str(getattr(app, "_gcode_storage_mode", "") or ""),
        "stats_mode": str(stats_mode or ""),
        "stats_sample_scale": float(sample_scale),
        "stats_sample_line_count": int(sample_lines),
        "stats_sample_total_lines": int(sample_total_lines),
        "stats_sample_executable_lines": int(sample_executable_lines),
        "stats_sample_motion_lines": int(sample_motion_lines),
        "stats_executable_total_lines": int(executable_total_lines),
        "stats_motion_total_lines": int(motion_total_lines),
        "rate_source": str(rate_source or ""),
        "estimate_confidence": str(confidence),
        "rapid_rates_mm_min": _normalise_rate_tuple(rapid_rates),
        "accel_rates_mm_s2": _normalise_rate_tuple(accel_rates),
        "grbl_motion_settings": motion_settings,
        "modal_context": _build_modal_context_snapshot(modal_sample_lines),
    }
    app._estimate_inputs_snapshot = snapshot


def _compute_stats_from_moves(
    moves,
    bounds,
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    consume_move, _consume_values, finalize = _build_move_stats_accumulator(
        rapid_rates,
        accel_rates,
    )
    for move in moves:
        consume_move(move)
    return finalize(bounds)


def _build_move_stats_accumulator(
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
):
    total_time_min = 0.0
    has_time = False
    total_rapid_min = 0.0
    has_rapid = False
    last_f = None

    def axis_limits(dx: float, dy: float, dz: float):
        max_feed = None
        min_accel = None
        if rapid_rates:
            candidates = []
            if dx:
                candidates.append(rapid_rates[0])
            if dy:
                candidates.append(rapid_rates[1])
            if dz:
                candidates.append(rapid_rates[2])
            if candidates:
                max_feed = min(candidates)
        if accel_rates:
            candidates = []
            if dx:
                candidates.append(accel_rates[0])
            if dy:
                candidates.append(accel_rates[1])
            if dz:
                candidates.append(accel_rates[2])
            if candidates:
                min_accel = min(candidates)
        return max_feed, min_accel

    def move_duration(
        dist: float,
        feed_mm_min: float | None,
        min_accel: float | None,
        last_feed: float | None,
    ):
        if dist <= 0:
            return 0.0, last_feed
        if feed_mm_min is None or feed_mm_min <= 0:
            return None, last_feed
        f = feed_mm_min / 60.0
        if f <= 0:
            return None, last_feed
        accel = min_accel if (min_accel and min_accel > 0) else 0.0
        if accel <= 0:
            return dist / f, f
        if last_feed is not None and abs(f - last_feed) < 1e-6:
            return dist / f, f
        accel = accel if accel > 0 else 750.0
        half_len = dist / 2.0
        init_time = f / accel
        init_dx = 0.5 * f * init_time
        time_sec = 0.0
        if half_len >= init_dx:
            half_len -= init_dx
            time_sec += init_time
        time_sec += half_len / f
        return 2 * time_sec, f

    def consume_values(
        motion: int,
        feed: float | None,
        feed_mode: str,
        dx: float,
        dy: float,
        dz: float,
        dist: float,
        _arc_len: float | None,
    ) -> None:
        nonlocal total_time_min, has_time, total_rapid_min, has_rapid, last_f
        if motion == 0 and rapid_rates:
            max_feed, min_accel = axis_limits(dx, dy, dz)
            if max_feed:
                t_sec, last_f = move_duration(dist, max_feed, min_accel, last_f)
                if t_sec is not None:
                    total_rapid_min += t_sec / 60.0
                    has_rapid = True
        if motion in (1, 2, 3):
            if feed and feed > 0:
                if feed_mode == "G93":
                    total_time_min += 1.0 / feed
                else:
                    max_feed, min_accel = axis_limits(dx, dy, dz)
                    use_feed = feed
                    if max_feed and use_feed > max_feed:
                        use_feed = max_feed
                    t_sec, last_f = move_duration(dist, use_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_time_min += t_sec / 60.0
                has_time = True

    def consume_move(move) -> None:
        consume_values(
            int(move.motion),
            move.feed,
            str(move.feed_mode),
            float(move.dx),
            float(move.dy),
            float(move.dz),
            float(move.dist),
            getattr(move, "arc_len", None),
        )

    def finalize(bounds) -> dict:
        return {
            "bounds": bounds,
            "time_min": total_time_min if has_time else None,
            "rapid_min": total_rapid_min if has_rapid else None,
        }

    return consume_move, consume_values, finalize


def compute_gcode_stats_from_result(
    result,
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    if result is None:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    return _compute_stats_from_moves(
        result.moves, result.bounds, rapid_rates, accel_rates
    )


def compute_gcode_stats(
    lines: list[str],
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
    keep_running: Callable[[], bool] | None = None,
) -> dict:
    if not lines:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    _consume_move, consume_values, finalize_stats = _build_move_stats_accumulator(
        rapid_rates, accel_rates
    )
    result = parse_gcode_lines(
        lines,
        keep_running=keep_running,
        include_moves=False,
        move_values_callback=consume_values,
        include_segments=False,
    )
    if result is None:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    return finalize_stats(result.bounds)


def format_duration(seconds: int) -> str:
    total_minutes = int(round(seconds / 60)) if seconds else 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def _is_stream_job_in_progress(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return False
    state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    return state in {"running", "paused"}


def _streaming_estimated_job_seconds(app) -> int | None:
    total_min = _raw_estimated_job_total_min(app)
    if total_min is not None:
        factor = estimate_factor_value(app)
        return int(round(max(0.0, float(total_min) * factor * 60.0)))
    return None


def format_streaming_estimate_text(app) -> str:
    estimated_seconds = _streaming_estimated_job_seconds(app)
    confidence = str(
        getattr(app, "_estimate_confidence", _ESTIMATE_CONFIDENCE_PROVISIONAL)
        or _ESTIMATE_CONFIDENCE_PROVISIONAL
    )
    if estimated_seconds is None:
        estimate_txt = "n/a"
    else:
        estimate_txt = format_duration(estimated_seconds)
    text = f"Estimated Job Time: {estimate_txt} [{_confidence_badge(confidence)}]"
    return f"{text} / {_format_dimensions_block(app)}"


def _remaining_estimate_for_display_min(app) -> float | None:
    display_min = getattr(app, "_live_estimate_display_min", None)
    if display_min is not None:
        try:
            return max(0.0, float(display_min))
        except Exception:
            pass
    raw_min = getattr(app, "_live_estimate_min", None)
    if raw_min is None:
        return None
    try:
        return max(0.0, float(raw_min))
    except Exception:
        return None


def _update_remaining_estimate_display(
    app, raw_remaining_min: float, now_ts: float
) -> None:
    try:
        raw_remaining_min = max(0.0, float(raw_remaining_min))
    except Exception:
        return
    current_display = getattr(app, "_live_estimate_display_min", None)
    if current_display is None:
        app._live_estimate_display_min = raw_remaining_min
        app._live_estimate_display_ts = float(now_ts)
        return
    try:
        current_display = max(0.0, float(current_display))
    except Exception:
        app._live_estimate_display_min = raw_remaining_min
        app._live_estimate_display_ts = float(now_ts)
        return
    last_ts = float(getattr(app, "_live_estimate_display_ts", 0.0) or 0.0)
    if (float(now_ts) - last_ts) < _LIVE_ESTIMATE_DISPLAY_INTERVAL_S:
        return
    alpha = min(1.0, max(0.0, float(_LIVE_ESTIMATE_DISPLAY_EMA_ALPHA)))
    smoothed_min = ((1.0 - alpha) * current_display) + (alpha * raw_remaining_min)
    max_up = current_display + float(_LIVE_ESTIMATE_DISPLAY_MAX_UP_STEP_MIN)
    if smoothed_min > max_up:
        smoothed_min = max_up
    app._live_estimate_display_min = max(0.0, smoothed_min)
    app._live_estimate_display_ts = float(now_ts)


def estimate_factor_value(app) -> float:
    try:
        val = float(app.estimate_factor.get())
    except Exception:
        return 1.0
    if val <= 0:
        return 1.0
    return val


def refresh_gcode_stats_display(app):
    if not _job_loaded_for_status(app):
        _set_gcode_status_text_if_changed(app, "")
        return
    if getattr(app, "_gcode_streaming_mode", False):
        _set_gcode_status_text_if_changed(app, format_streaming_estimate_text(app))
        return
    if not app._last_stats:
        _set_gcode_status_text_if_changed(app, format_streaming_estimate_text(app))
        return
    _set_gcode_status_text_if_changed(
        app,
        format_gcode_stats_text(app, app._last_stats, app._last_rate_source),
    )


def on_estimate_factor_change(app, _value=None):
    factor = estimate_factor_value(app)
    app._estimate_factor_label.set(f"{factor:.2f}x")
    refresh_gcode_stats_display(app)


def update_live_estimate(app, done: int, total: int):
    if app._stream_start_ts is None or done <= 0 or total <= 0:
        return
    now = time.time()
    paused_total = app._stream_pause_total
    if app._stream_paused_at is not None:
        paused_total += max(0.0, now - app._stream_paused_at)
    elapsed = max(0.0, now - app._stream_start_ts - paused_total)
    if elapsed < 1.0:
        return
    progress = min(1.0, max(0.0, float(done) / float(max(1, total))))
    baseline_total_min = _raw_estimated_job_total_min(app)
    baseline_total_s = (
        float(baseline_total_min * 60.0) if baseline_total_min is not None else None
    )
    observed_total_s = None
    if (
        progress >= float(_LIVE_ESTIMATE_OBSERVED_MIN_PROGRESS)
        and done >= int(_LIVE_ESTIMATE_OBSERVED_MIN_DONE)
        and elapsed >= float(_LIVE_ESTIMATE_OBSERVED_MIN_ELAPSED_S)
    ):
        observed_total_s = max(elapsed, elapsed / max(progress, 1e-6))

    projected_total_s: float | None = baseline_total_s
    if observed_total_s is not None:
        if projected_total_s is None:
            projected_total_s = observed_total_s
        else:
            blend = min(
                float(_LIVE_ESTIMATE_OBSERVED_BLEND_MAX),
                max(0.0, (progress - 0.05) / 0.45)
                * float(_LIVE_ESTIMATE_OBSERVED_BLEND_MAX),
            )
            projected_total_s = ((1.0 - blend) * projected_total_s) + (
                blend * observed_total_s
            )

    if projected_total_s is None:
        projected_total_s = max(elapsed, (elapsed / max(progress, 1e-6)))
    projected_total_s = max(elapsed, float(projected_total_s))
    remaining = max(0.0, projected_total_s - elapsed)
    app._live_estimate_total_min = projected_total_s / 60.0
    app._live_estimate_observed_total_min = (
        (observed_total_s / 60.0) if observed_total_s is not None else None
    )
    if observed_total_s is not None:
        app._estimate_confidence = _ESTIMATE_CONFIDENCE_CONFIDENT
    app._live_estimate_min = remaining / 60.0
    loaded_source = str(getattr(app, "_loaded_estimate_source", "") or "")
    if _loaded_estimate_total_min(app) is None:
        _set_loaded_estimate_total_min(
            app, projected_total_s / 60.0, "runtime_projection"
        )
    elif observed_total_s is not None and loaded_source in {
        "runtime_projection",
        "observed_runtime",
    }:
        _set_loaded_estimate_total_min(
            app, projected_total_s / 60.0, "observed_runtime"
        )
    _update_remaining_estimate_display(app, app._live_estimate_min, now)
    refresh_gcode_stats_display(app)


def format_gcode_stats_text(app, stats: dict, rate_source: str | None) -> str:
    _ = rate_source
    estimated_seconds = _streaming_estimated_job_seconds(app)
    estimate_txt = (
        "n/a" if estimated_seconds is None else format_duration(estimated_seconds)
    )
    confidence = str(
        getattr(app, "_estimate_confidence", _ESTIMATE_CONFIDENCE_PROVISIONAL)
        or _ESTIMATE_CONFIDENCE_PROVISIONAL
    )
    line_1 = f"Estimated Job Time: {estimate_txt} [{_confidence_badge(confidence)}]"
    return f"{line_1} / {_format_dimensions_block(app, stats)}"


def apply_gcode_stats(app, token: int, stats: dict | None, rate_source: str | None):
    if token != app._stats_token:
        return
    app._last_stats = stats
    app._last_rate_source = rate_source
    if stats is None:
        app._estimate_confidence = _ESTIMATE_CONFIDENCE_PROVISIONAL
        _set_gcode_status_text_if_changed(app, format_streaming_estimate_text(app))
        return
    total_min = _estimate_total_min_from_stats(stats)
    if total_min is not None:
        previous_source = str(getattr(app, "_loaded_estimate_source", "") or "")
        source_tag = f"stats:{str(rate_source or 'unknown')}"
        _set_loaded_estimate_total_min(app, total_min, source_tag)
        if previous_source == "quick_scan" and source_tag != "quick_scan":
            setattr(app, "_gcode_estimate_replaced_quick", True)
    snapshot = getattr(app, "_estimate_inputs_snapshot", None)
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        motion_settings = snapshot.get("grbl_motion_settings")
        confidence = _resolve_estimate_confidence(
            app,
            rate_source=rate_source,
            motion_settings=motion_settings
            if isinstance(motion_settings, dict)
            else None,
        )
        app._estimate_confidence = confidence
        snapshot["stats_time_min"] = _safe_float(stats.get("time_min"))
        snapshot["stats_rapid_min"] = _safe_float(stats.get("rapid_min"))
        snapshot["stats_total_min"] = _estimate_total_min_from_stats(stats)
        snapshot["loaded_estimate_total_min"] = _loaded_estimate_total_min(app)
        snapshot["loaded_estimate_source"] = str(
            getattr(app, "_loaded_estimate_source", "") or ""
        )
        snapshot["estimate_confidence"] = str(confidence)
        app._estimate_inputs_snapshot = snapshot
    refresh_gcode_stats_display(app)


def get_fallback_rapid_rate(app) -> float | None:
    raw = app.fallback_rapid_rate.get().strip()
    if not raw:
        return None
    try:
        rate = float(raw)
    except Exception:
        return None
    if rate <= 0:
        return None
    return rate


def get_rapid_rates_for_estimate(app):
    if app._rapid_rates:
        return app._rapid_rates, "grbl"
    rx: float | None
    ry: float | None
    rz: float | None
    try:
        rx = float(app.estimate_rate_x_var.get().strip())
        ry = float(app.estimate_rate_y_var.get().strip())
        rz = float(app.estimate_rate_z_var.get().strip())
    except Exception:
        rx = ry = rz = None
    if (
        rx is not None
        and ry is not None
        and rz is not None
        and rx > 0
        and ry > 0
        and rz > 0
    ):
        units = str(app.unit_mode.get()).lower()
        scale = 25.4 if units.startswith("in") else 1.0
        return (rx * scale, ry * scale, rz * scale), "estimate"
    fallback = get_fallback_rapid_rate(app)
    if fallback:
        return (fallback, fallback, fallback), "fallback"
    return None, None


def get_accel_rates_for_estimate(app):
    return app._accel_rates


def _safe_close_source(source) -> None:
    closer = getattr(source, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:
        pass


def _clone_streaming_source_for_stats(app):
    source = getattr(app, "_gcode_source", None)
    if not isinstance(source, FileGcodeSource):
        return None
    clone_fn = getattr(source, "clone", None)
    if callable(clone_fn):
        try:
            return clone_fn()
        except Exception:
            return None
    try:
        offsets = getattr(source, "_offsets", None)
        if offsets is None or len(offsets) <= 0:
            return None
        return FileGcodeSource(source.path, offsets, already_clean=True)
    except Exception:
        return None


def _has_line_data(line_source) -> bool:
    if line_source is None:
        return False
    try:
        return len(line_source) > 0
    except Exception:
        return bool(line_source)


def make_stats_cache_key(
    app,
    rapid_rates: tuple[float, float, float] | None,
    accel_rates: tuple[float, float, float] | None,
):
    if not app._gcode_hash:
        return None
    rapid = tuple(rapid_rates) if rapid_rates is not None else None
    accel = tuple(accel_rates) if accel_rates is not None else None
    return (app._gcode_hash, rapid, accel)


def _result_has_moves(parse_result) -> bool:
    includes_moves = getattr(parse_result, "_includes_moves", None)
    if includes_moves is not None:
        return bool(includes_moves)
    try:
        return bool(getattr(parse_result, "moves", None))
    except Exception:
        return False


def _schedule_stats_launch(
    app, launch, *, delay_override_ms: int | None = None
) -> None:
    delay_ms = int(
        getattr(app, "_stats_debounce_ms", GCODE_STATS_DEBOUNCE_MS)
        or GCODE_STATS_DEBOUNCE_MS
    )
    if delay_override_ms is not None:
        delay_ms = max(delay_ms, int(delay_override_ms))
    _cancel_pending_stats_launch(app)
    app._stats_after_id = app.after(max(0, delay_ms), launch)


def _cancel_pending_stats_launch(app) -> None:
    after_id = getattr(app, "_stats_after_id", None)
    if after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(after_id)
        except Exception:
            pass
    app._stats_after_id = None


def _clear_pending_stats_request(app, *, cancel_launch: bool) -> None:
    pending = getattr(app, "_stats_pending_request", None)
    if pending and isinstance(pending, tuple) and len(pending) >= 8:
        _safe_close_source(pending[7])
    app._stats_pending_request = None
    if cancel_launch:
        _cancel_pending_stats_launch(app)


def _line_count_or_zero(line_source) -> int:
    try:
        return int(len(line_source))
    except Exception:
        return 0


def _stats_full_scan_launch_delay_ms(
    app, pending_lines, pending_parse_result
) -> int | None:
    delay_ms = 0
    if bool(getattr(app, "_gcode_load_settling", False)):
        try:
            settle_delay_ms = int(getattr(app, "_gcode_stats_settle_delay_ms", 1500))
        except Exception:
            settle_delay_ms = 1500
        if settle_delay_ms > 0:
            delay_ms = max(delay_ms, settle_delay_ms)
    if pending_parse_result is not None:
        return delay_ms or None
    if not getattr(app, "_gcode_streaming_mode", False):
        return delay_ms or None
    if not isinstance(pending_lines, FileGcodeSource):
        return delay_ms or None
    line_count = _line_count_or_zero(pending_lines)
    if line_count <= 0:
        return delay_ms or None
    if line_count < int(GCODE_STATS_FULL_SCAN_DELAY_THRESHOLD_LINES):
        return delay_ms or None
    delay_ms = max(delay_ms, int(GCODE_STATS_FULL_SCAN_DELAY_LARGE_MS))
    return delay_ms or None


def _stream_is_active(app) -> bool:
    state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if state in {"running", "paused"}:
        return True
    worker = getattr(app, "grbl", None)
    checker = getattr(worker, "is_streaming", None) if worker is not None else None
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _scale_stats_totals(stats: dict, scale: float) -> None:
    if not isinstance(stats, dict):
        return
    scale_f = float(scale)
    if scale_f <= 0.0:
        return
    for key in ("time_min", "rapid_min"):
        raw_val = stats.get(key)
        if raw_val is None:
            continue
        try:
            stats[key] = float(raw_val) * scale_f
        except Exception:
            continue


def update_gcode_stats(
    app,
    lines: list[str],
    parse_result=None,
    *,
    force_full_scan: bool = False,
):
    stats_lines = lines
    cleanup_stats_source = None
    sample_scale = 1.0
    stats_mode = "full_scan"
    sample_executable_lines = 0
    sample_motion_lines = 0
    executable_total_lines = int(getattr(app, "_gcode_total_lines", 0) or 0)
    motion_total_lines = 0
    if getattr(app, "_gcode_streaming_mode", False):
        source = getattr(app, "_gcode_source", None)
        sample_lines = (
            getattr(source, "_prepare_sampled_lines", None)
            if source is not None
            else None
        )
        loaded_sample_lines = lines if isinstance(lines, list) else []
        total_lines = _line_count_or_zero(source)
        source_offset_count = 0
        try:
            offsets = getattr(source, "_offsets", None)
            source_offset_count = int(len(offsets)) if offsets is not None else 0
        except Exception:
            source_offset_count = 0
        effective_total_lines = (
            int(total_lines) if int(total_lines) > 0 else int(source_offset_count)
        )
        executable_total_lines = (
            int(getattr(source, "_prepare_executable_total_lines", 0) or 0)
            if source is not None
            else 0
        )
        motion_total_lines = (
            int(getattr(source, "_prepare_motion_total_lines", 0) or 0)
            if source is not None
            else 0
        )
        if executable_total_lines <= 0:
            executable_total_lines = int(effective_total_lines)
        sample_threshold = max(0, int(GCODE_PREP_STATS_SAMPLE_THRESHOLD_LINES))
        has_sample_lines = isinstance(sample_lines, list) and len(sample_lines) > 0
        if force_full_scan:
            cloned_source = _clone_streaming_source_for_stats(app)
            if cloned_source is not None:
                stats_lines = cloned_source
                cleanup_stats_source = cloned_source
                stats_mode = "full_scan_forced"
            elif has_sample_lines:
                stats_lines = list(sample_lines)
                stats_mode = "sampled_force_fallback"
            else:
                stats_lines = list(loaded_sample_lines)
                stats_mode = "sample_force_fallback"
            if isinstance(sample_lines, list):
                sample_executable_lines, sample_motion_lines = (
                    _sample_line_characteristics(sample_lines)
                )
        else:
            # Lean sender policy: never auto-full-scan file-backed streaming jobs.
            # Use sampled prepare data when available; otherwise fall back to the
            # bounded sample lines already retained for the viewer.
            if has_sample_lines:
                stats_lines = list(sample_lines)
            else:
                allow_small_auto_full_scan = bool(
                    source is not None
                    and sample_threshold > 0
                    and effective_total_lines > 0
                    and effective_total_lines <= sample_threshold
                )
                if allow_small_auto_full_scan:
                    cloned_source = _clone_streaming_source_for_stats(app)
                    if cloned_source is not None:
                        stats_lines = cloned_source
                        cleanup_stats_source = cloned_source
                        stats_mode = "full_scan"
                    else:
                        stats_lines = list(loaded_sample_lines)
                else:
                    stats_lines = list(loaded_sample_lines)
            sample_executable_lines, sample_motion_lines = _sample_line_characteristics(
                stats_lines
            )
            if (
                sample_threshold > 0
                and effective_total_lines < sample_threshold
                and len(stats_lines) > 0
                and not isinstance(stats_lines, FileGcodeSource)
            ):
                # Small jobs with full/near-full sample coverage can be treated as
                # effectively complete for estimate scaling purposes.
                sample_scale = 1.0
                stats_mode = "sampled_small_unscaled"
            elif len(stats_lines) == 0:
                sample_scale = 1.0
                stats_mode = "sampled_empty"
            elif not has_sample_lines:
                sample_scale = 1.0
                stats_mode = "sampled_sample_fallback"
            else:
                # Prefer scaling by motion-line coverage; raw line-count scaling can
                # explode when the retained sample is tiny relative to huge files.
                min_exec = max(1, int(GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES))
                min_motion = max(1, int(GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES))
                max_scale = max(1.0, float(GCODE_ESTIMATE_SAMPLE_MAX_SCALE))
                if sample_motion_lines >= min_motion and motion_total_lines > 0:
                    sample_scale = float(motion_total_lines) / float(
                        max(1, sample_motion_lines)
                    )
                    stats_mode = "sampled_motion_scaled"
                elif sample_executable_lines >= min_exec and executable_total_lines > 0:
                    sample_scale = float(executable_total_lines) / float(
                        max(1, sample_executable_lines)
                    )
                    sample_scale = min(sample_scale, max_scale * 0.5)
                    stats_mode = "sampled_exec_scaled"
                elif sample_executable_lines >= 500 and executable_total_lines > 0:
                    sample_scale = min(
                        8.0,
                        float(executable_total_lines)
                        / float(max(1, sample_executable_lines)),
                    )
                    stats_mode = "sampled_guarded_scaled"
                else:
                    sample_scale = 1.0
                    stats_mode = "sampled_guarded_unscaled"
                sample_scale = max(1.0, min(float(sample_scale), max_scale))
    if sample_executable_lines <= 0 and isinstance(stats_lines, list):
        sample_executable_lines, sample_motion_lines = _sample_line_characteristics(
            stats_lines
        )
    setattr(app, "_gcode_stats_compute_mode", stats_mode)
    setattr(app, "_gcode_stats_sample_scale", float(sample_scale))
    setattr(
        app,
        "_gcode_stats_sample_line_count",
        int(len(stats_lines) if isinstance(stats_lines, list) else 0),
    )
    setattr(
        app,
        "_gcode_stats_sample_total_lines",
        int(getattr(app, "_gcode_total_lines", 0) or 0),
    )
    setattr(app, "_gcode_stats_sample_executable_lines", int(sample_executable_lines))
    setattr(app, "_gcode_stats_sample_motion_lines", int(sample_motion_lines))
    setattr(app, "_gcode_stats_executable_total_lines", int(executable_total_lines))
    setattr(app, "_gcode_stats_motion_total_lines", int(motion_total_lines))
    modal_sample_lines = None
    if isinstance(stats_lines, list):
        modal_sample_lines = stats_lines
    else:
        source = getattr(app, "_gcode_source", None)
        sampled = (
            getattr(source, "_prepare_sampled_lines", None)
            if source is not None
            else None
        )
        if isinstance(sampled, list):
            modal_sample_lines = sampled
        elif isinstance(lines, list):
            modal_sample_lines = lines
    if not _has_line_data(stats_lines):
        _clear_pending_stats_request(app, cancel_launch=True)
        app._last_stats = None
        app._last_rate_source = None
        _set_gcode_status_text_if_changed(app, "")
        _safe_close_source(cleanup_stats_source)
        return
    if parse_result is None and not getattr(app, "_gcode_streaming_mode", False):
        cached_parse = getattr(app, "_last_parse_result", None)
        cached_hash = getattr(app, "_last_parse_hash", None)
        if cached_parse is not None and cached_hash == app._gcode_hash:
            parse_result = cached_parse
    if parse_result is not None and not _result_has_moves(parse_result):
        parse_result = None
    app._last_stats = None
    app._last_rate_source = None
    app._stats_token += 1
    token = app._stats_token
    rapid_rates, rate_source = get_rapid_rates_for_estimate(app)
    accel_rates = get_accel_rates_for_estimate(app)
    snapshot_stats_mode = str(stats_mode)
    if snapshot_stats_mode.startswith("sampled") or snapshot_stats_mode.startswith(
        "sample"
    ):
        snapshot_stats_mode = "sampled"
    elif snapshot_stats_mode.startswith("full_scan"):
        snapshot_stats_mode = "full_scan"

    _capture_estimation_inputs_snapshot(
        app,
        rapid_rates=rapid_rates,
        accel_rates=accel_rates,
        rate_source=rate_source,
        stats_mode=snapshot_stats_mode,
        sample_scale=float(sample_scale),
        sample_lines=int(len(stats_lines) if isinstance(stats_lines, list) else 0),
        sample_total_lines=int(getattr(app, "_gcode_total_lines", 0) or 0),
        sample_executable_lines=int(sample_executable_lines),
        sample_motion_lines=int(sample_motion_lines),
        executable_total_lines=int(executable_total_lines),
        motion_total_lines=int(motion_total_lines),
        modal_sample_lines=modal_sample_lines,
    )
    cache_key = (
        None if force_full_scan else make_stats_cache_key(app, rapid_rates, accel_rates)
    )
    if cache_key and cache_key in app._stats_cache:
        _clear_pending_stats_request(app, cancel_launch=True)
        stats, cached_source = app._stats_cache[cache_key]
        apply_gcode_stats(app, token, stats, cached_source)
        _safe_close_source(cleanup_stats_source)
        return
    if getattr(app, "_gcode_streaming_mode", False):
        app.gcode_stats_var.set(format_streaming_estimate_text(app))
    else:
        app.gcode_stats_var.set("Calculating stats...")
    pending_lines = stats_lines if parse_result is None else None
    app._stats_pending_request = (
        token,
        pending_lines,
        parse_result,
        rapid_rates,
        accel_rates,
        rate_source,
        cache_key,
        cleanup_stats_source,
        float(sample_scale),
        str(stats_mode),
    )
    launch_delay_override_ms = _stats_full_scan_launch_delay_ms(
        app, pending_lines, parse_result
    )

    def launch_latest():
        app._stats_after_id = None
        pending = getattr(app, "_stats_pending_request", None)
        if not pending:
            return
        (
            pending_token,
            pending_lines,
            pending_parse_result,
            pending_rapid_rates,
            pending_accel_rates,
            pending_rate_source,
            pending_cache_key,
            pending_cleanup_source,
            pending_sample_scale,
            pending_stats_mode,
        ) = pending
        app._stats_pending_request = None
        if pending_token != app._stats_token:
            _safe_close_source(pending_cleanup_source)
            return

        throttle_file_backed_scan = bool(
            pending_parse_result is None
            and isinstance(pending_lines, FileGcodeSource)
            and getattr(app, "_gcode_streaming_mode", False)
        )
        throttle_every = max(1, int(GCODE_STATS_FULL_SCAN_THROTTLE_LINES))
        throttle_sleep_s = max(0.0, float(GCODE_STATS_FULL_SCAN_THROTTLE_SLEEP_S))
        background_tab_sleep_s = max(
            throttle_sleep_s,
            float(GCODE_STATS_FULL_SCAN_BACKGROUND_TAB_SLEEP_S),
        )
        cooperative_every = max(1, int(GCODE_STATS_COOPERATIVE_YIELD_LINES))
        cooperative_sleep_s = max(0.0, float(GCODE_STATS_COOPERATIVE_YIELD_SLEEP_S))
        chunk_budget_ms = max(
            5.0,
            float(
                getattr(
                    app,
                    "_gcode_stats_chunk_budget_ms",
                    _GCODE_STATS_COOPERATIVE_CHUNK_BUDGET_MS,
                )
            ),
        )
        keep_running_counter = 0
        chunk_started_at = time.perf_counter()
        chunk_max_ms = 0.0
        chunk_max_section = "unknown"
        chunk_yield_count = 0
        chunk_over_budget_count = 0

        def keep_running(section: str = "worker_loop") -> bool:
            nonlocal \
                keep_running_counter, \
                chunk_started_at, \
                chunk_max_ms, \
                chunk_max_section
            nonlocal chunk_yield_count, chunk_over_budget_count
            if pending_token != getattr(app, "_stats_token", None):
                return False
            if _stream_is_active(app):
                return False
            keep_running_counter += 1
            section_name = str(section or "unknown")

            should_yield = False
            sleep_s = 0.0
            now = time.perf_counter()
            elapsed_chunk_ms = max(0.0, (now - chunk_started_at) * 1000.0)
            if elapsed_chunk_ms >= chunk_budget_ms:
                should_yield = True
            if (
                throttle_file_backed_scan
                and (keep_running_counter % throttle_every) == 0
            ):
                should_yield = True
                active_tab = str(getattr(app, "_active_tab_label", "") or "")
                sleep_s = throttle_sleep_s
                if active_tab and active_tab != "G-code":
                    sleep_s = background_tab_sleep_s
            elif (keep_running_counter % cooperative_every) == 0:
                should_yield = True
                sleep_s = max(sleep_s, cooperative_sleep_s)

            if should_yield:
                chunk_ms = elapsed_chunk_ms
                if chunk_ms > chunk_max_ms:
                    chunk_max_ms = chunk_ms
                    chunk_max_section = section_name
                if chunk_ms > chunk_budget_ms:
                    chunk_over_budget_count += 1
                if sleep_s > 0.0:
                    time.sleep(sleep_s)
                else:
                    # Cooperative yield so background parsing does not monopolize the GIL.
                    time.sleep(0.0)
                chunk_started_at = time.perf_counter()
                chunk_yield_count += 1
            return True

        def worker():
            success = False
            task_name = (
                "gcode.stats.compute.from_parse_result"
                if pending_parse_result is not None
                else "gcode.stats.compute.full_scan"
            )
            try:
                try:
                    if not keep_running("worker_preflight"):
                        return

                    compute_started_at = time.perf_counter()
                    if pending_parse_result is None:
                        if not _has_line_data(pending_lines):
                            return
                        stats = compute_gcode_stats(
                            pending_lines,
                            pending_rapid_rates,
                            pending_accel_rates,
                            keep_running=keep_running,
                        )
                    else:
                        stats = compute_gcode_stats_from_result(
                            pending_parse_result,
                            pending_rapid_rates,
                            pending_accel_rates,
                        )
                    if pending_parse_result is None and isinstance(stats, dict):
                        _scale_stats_totals(stats, float(pending_sample_scale))
                        if pending_stats_mode == "sampled":
                            app.ui_q.put(
                                (
                                    "log",
                                    "[stats] Using sampled estimate path for large file-backed job.",
                                )
                            )
                    elapsed_ms = max(
                        0.0, (time.perf_counter() - compute_started_at) * 1000.0
                    )
                    record_task_timing(app, task_name, elapsed_ms, success=True)
                    success = True
                except Exception as exc:
                    if keep_running("worker_error_apply"):
                        app.after(
                            0,
                            lambda: apply_gcode_stats(
                                app, pending_token, None, pending_rate_source
                            ),
                        )
                        app.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
                    return
                if not keep_running("worker_finalize"):
                    return
                if pending_cache_key:
                    cache = app._stats_cache
                    cache[pending_cache_key] = (stats, pending_rate_source)
                    max_entries = max(1, int(GCODE_STATS_CACHE_MAX_ENTRIES))
                    while len(cache) > max_entries:
                        try:
                            oldest_key = next(iter(cache))
                        except StopIteration:
                            break
                        cache.pop(oldest_key, None)
                app.after(
                    0,
                    lambda: apply_gcode_stats(
                        app, pending_token, stats, pending_rate_source
                    ),
                )
            finally:
                _safe_close_source(pending_cleanup_source)
                try:
                    app._gcode_stats_chunk_max_ms = max(
                        float(getattr(app, "_gcode_stats_chunk_max_ms", 0.0) or 0.0),
                        float(chunk_max_ms),
                    )
                    app._gcode_stats_chunk_max_section = str(
                        chunk_max_section or "unknown"
                    )
                    app._gcode_stats_chunk_yield_count = int(chunk_yield_count)
                except Exception:
                    pass
                if chunk_max_ms > 0.0:
                    logger.info(
                        "[stats] Cooperative chunk max %.2fms (%d yields, over_budget=%d, budget=%.1fms, section=%s, mode=%s, success=%s)",
                        chunk_max_ms,
                        int(chunk_yield_count),
                        int(chunk_over_budget_count),
                        float(chunk_budget_ms),
                        str(chunk_max_section or "unknown"),
                        str(pending_stats_mode),
                        bool(success),
                    )
                if chunk_max_ms > float(chunk_budget_ms):
                    logger.warning(
                        "[stats] Cooperative chunk exceeded budget: max=%.2fms budget=%.1fms section=%s mode=%s",
                        chunk_max_ms,
                        float(chunk_budget_ms),
                        str(chunk_max_section or "unknown"),
                        str(pending_stats_mode),
                    )

        threading.Thread(target=worker, daemon=True).start()

    _schedule_stats_launch(
        app, launch_latest, delay_override_ms=launch_delay_override_ms
    )
