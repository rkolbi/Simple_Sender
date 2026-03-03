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

import math
import threading
import time
from typing import Callable

from simple_sender.gcode_parser import parse_gcode_lines
from simple_sender.gcode_source import FileGcodeSource
from simple_sender.utils.constants import (
    GCODE_STATS_CACHE_MAX_ENTRIES,
    GCODE_STATS_DEBOUNCE_MS,
)
from simple_sender.utils.task_timing import record_task_timing

_LIVE_ESTIMATE_DISPLAY_INTERVAL_S = 1.0
_LIVE_ESTIMATE_DISPLAY_EMA_ALPHA = 0.2
_LIVE_ESTIMATE_DISPLAY_MAX_UP_STEP_MIN = 0.25


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

    def move_duration(dist: float, feed_mm_min: float | None, min_accel: float | None, last_feed: float | None):
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
    return _compute_stats_from_moves(result.moves, result.bounds, rapid_rates, accel_rates)


def compute_gcode_stats(
    lines: list[str],
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
    keep_running: Callable[[], bool] | None = None,
) -> dict:
    if not lines:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    _consume_move, consume_values, finalize_stats = _build_move_stats_accumulator(rapid_rates, accel_rates)
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
    stats = getattr(app, "_last_stats", None)
    if isinstance(stats, dict):
        time_min = stats.get("time_min")
        if time_min is not None:
            rapid_min = stats.get("rapid_min")
            total_min = float(time_min)
            if rapid_min is not None:
                total_min += float(rapid_min)
            factor = estimate_factor_value(app)
            return int(round(max(0.0, total_min * factor * 60.0)))
    live_total_min = getattr(app, "_live_estimate_total_min", None)
    if live_total_min is not None:
        return int(round(max(0.0, float(live_total_min) * 60.0)))
    return None


def format_streaming_estimate_text(app) -> str:
    estimated_seconds = _streaming_estimated_job_seconds(app)
    if estimated_seconds is None:
        estimate_txt = "n/a"
    else:
        estimate_txt = format_duration(estimated_seconds)
    text = f"Estimated Job time: {estimate_txt}"
    if not _is_stream_job_in_progress(app):
        return text
    remaining_min = _remaining_estimate_for_display_min(app)
    if remaining_min is None:
        remaining_txt = "n/a"
    else:
        factor = estimate_factor_value(app)
        remaining_seconds = int(round(max(0.0, float(remaining_min)) * factor * 60.0))
        remaining_txt = format_duration(remaining_seconds)
    return f"{text} | Completion in: {remaining_txt}"


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


def _update_remaining_estimate_display(app, raw_remaining_min: float, now_ts: float) -> None:
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
    if getattr(app, "_gcode_streaming_mode", False):
        app.gcode_stats_var.set(format_streaming_estimate_text(app))
        return
    if not app._last_stats:
        return
    app.gcode_stats_var.set(format_gcode_stats_text(app, app._last_stats, app._last_rate_source))


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
    remaining = (elapsed / done) * total - elapsed
    if remaining < 0:
        remaining = 0.0
    app._live_estimate_total_min = (elapsed + remaining) / 60.0
    app._live_estimate_min = remaining / 60.0
    _update_remaining_estimate_display(app, app._live_estimate_min, now)
    refresh_gcode_stats_display(app)


def format_gcode_stats_text(app, stats: dict, rate_source: str | None) -> str:
    bounds = stats.get("bounds")
    if not bounds:
        return "No toolpath data"
    unit_mode = "mm"
    try:
        unit_mode = app.unit_mode.get()
    except Exception:
        unit_mode = "mm"
    unit_label = "in" if unit_mode == "inch" else "mm"
    unit_scale = 25.4 if unit_mode == "inch" else 1.0
    minx, maxx, miny, maxy, minz, maxz = bounds
    minx, maxx = minx / unit_scale, maxx / unit_scale
    miny, maxy = miny / unit_scale, maxy / unit_scale
    minz, maxz = minz / unit_scale, maxz / unit_scale
    minx = math.floor(minx)
    maxx = math.ceil(maxx)
    miny = math.floor(miny)
    maxy = math.ceil(maxy)
    minz = math.floor(minz)
    maxz = math.ceil(maxz)
    factor = estimate_factor_value(app)
    time_min = stats.get("time_min")
    rapid_min = stats.get("rapid_min")
    if time_min is None:
        time_txt = "n/a"
    else:
        seconds = int(round(time_min * factor * 60))
        time_txt = format_duration(seconds)
    if rapid_min is None or time_min is None:
        total_txt = "n/a"
        if rate_source is None:
            total_txt = "n/a (not connected)"
    else:
        seconds = int(round((time_min + rapid_min) * factor * 60))
        total_txt = format_duration(seconds)
        if rate_source == "fallback":
            total_txt = f"{total_txt} (fallback)"
        elif rate_source == "profile":
            total_txt = f"{total_txt} (profile)"
    live_txt = ""
    remaining_min = _remaining_estimate_for_display_min(app)
    if remaining_min is not None:
        live_seconds = int(round(remaining_min * factor * 60))
        live_txt = f" | Live est (stream): {format_duration(live_seconds)}"
    return (
        f"Bounds ({unit_label}) X[{minx}..{maxx}] "
        f"Y[{miny}..{maxy}] "
        f"Z[{minz}..{maxz}] | "
        f"Est time (feed only): {time_txt} | "
        f"Est time (with rapids): {total_txt}"
        f"{live_txt} | "
        "Approx"
    )


def apply_gcode_stats(app, token: int, stats: dict | None, rate_source: str | None):
    if token != app._stats_token:
        return
    app._last_stats = stats
    app._last_rate_source = rate_source
    if stats is None:
        app.gcode_stats_var.set("Estimate unavailable")
        return
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
    if rx is not None and ry is not None and rz is not None and rx > 0 and ry > 0 and rz > 0:
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


def _schedule_stats_launch(app, launch) -> None:
    delay_ms = int(getattr(app, "_stats_debounce_ms", GCODE_STATS_DEBOUNCE_MS) or GCODE_STATS_DEBOUNCE_MS)
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


def update_gcode_stats(app, lines: list[str], parse_result=None):
    stats_lines = lines
    cleanup_stats_source = None
    if getattr(app, "_gcode_streaming_mode", False):
        cloned_source = _clone_streaming_source_for_stats(app)
        if cloned_source is not None:
            stats_lines = cloned_source
            cleanup_stats_source = cloned_source
    if not _has_line_data(stats_lines):
        _clear_pending_stats_request(app, cancel_launch=True)
        app._last_stats = None
        app._last_rate_source = None
        app.gcode_stats_var.set("No file loaded")
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
    cache_key = make_stats_cache_key(app, rapid_rates, accel_rates)
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
        ) = pending
        app._stats_pending_request = None
        if pending_token != app._stats_token:
            _safe_close_source(pending_cleanup_source)
            return

        def keep_running() -> bool:
            return bool(pending_token == getattr(app, "_stats_token", None))

        def worker():
            started_at = time.perf_counter()
            success = False
            task_name = "gcode.stats.compute.from_parse_result" if pending_parse_result is not None else "gcode.stats.compute.full_scan"
            try:
                try:
                    if not keep_running():
                        return
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
                    success = True
                except Exception as exc:
                    if keep_running():
                        app.after(0, lambda: apply_gcode_stats(app, pending_token, None, pending_rate_source))
                        app.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
                    return
                if not keep_running():
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
                app.after(0, lambda: apply_gcode_stats(app, pending_token, stats, pending_rate_source))
            finally:
                _safe_close_source(pending_cleanup_source)
                elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
                record_task_timing(app, task_name, elapsed_ms, success=success)

        threading.Thread(target=worker, daemon=True).start()

    _schedule_stats_launch(app, launch_latest)
