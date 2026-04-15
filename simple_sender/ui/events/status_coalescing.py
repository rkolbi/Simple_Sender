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

"""Streaming-time status position coalescing helpers."""


def stream_status_positions_coalesce_active(app) -> bool:
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state not in {"running", "paused"}:
        return False
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return False
    return True


def status_positions_pressure_active(app, *, time_module, now_mono: float | None = None) -> bool:
    if now_mono is None:
        now_mono = float(time_module.monotonic())
    pressure_until_ts = float(
        getattr(app, "_status_positions_pressure_until_ts", 0.0) or 0.0
    )
    return pressure_until_ts > 0.0 and now_mono < pressure_until_ts


def mark_status_positions_pressure(
    app,
    *,
    time_module,
    default_recovery_s: float,
    now_mono: float | None = None,
) -> None:
    if now_mono is None:
        now_mono = float(time_module.monotonic())
    raw_recovery_s = getattr(
        app,
        "_status_positions_pressure_recovery_s",
        default_recovery_s,
    )
    try:
        recovery_s = float(raw_recovery_s)
    except Exception:
        recovery_s = default_recovery_s
    recovery_s = max(0.2, min(10.0, recovery_s))
    setattr(app, "_status_positions_pressure_until_ts", float(now_mono + recovery_s))


def status_ui_queue_pending_depth(app) -> int:
    ui_q = getattr(app, "ui_q", None)
    if ui_q is None or not hasattr(ui_q, "qsize"):
        return 0
    try:
        return max(0, int(ui_q.qsize()))
    except Exception:
        return 0


def status_positions_should_force_defer(
    app,
    *,
    event_started_perf: float | None,
    noncritical_budget_ms: float,
    time_module,
    stream_status_positions_coalesce_active,
    status_ui_queue_pending_depth,
    mark_status_positions_pressure,
    status_positions_pressure_active,
    record_status_perf_metric,
    default_pending_threshold: int,
) -> bool:
    if not stream_status_positions_coalesce_active(app):
        return False
    now_mono = float(time_module.monotonic())
    raw_pending_threshold = getattr(
        app,
        "_status_positions_pressure_pending_threshold",
        default_pending_threshold,
    )
    try:
        pending_threshold = int(raw_pending_threshold)
    except Exception:
        pending_threshold = default_pending_threshold
    pending_threshold = max(1, pending_threshold)
    pending_depth = status_ui_queue_pending_depth(app)
    if pending_depth >= pending_threshold:
        mark_status_positions_pressure(app, now_mono=now_mono)
        record_status_perf_metric(app, "positions_pressure_queue", 0.0)
        return True
    if event_started_perf is not None:
        elapsed_ms = max(0.0, (time_module.perf_counter() - float(event_started_perf)) * 1000.0)
        if elapsed_ms >= max(1.0, float(noncritical_budget_ms)):
            mark_status_positions_pressure(app, now_mono=now_mono)
            record_status_perf_metric(app, "positions_pressure_budget", 0.0)
            return True
    return bool(status_positions_pressure_active(app, now_mono=now_mono))


def status_positions_coalesce_interval_s(
    app,
    *,
    status_positions_pressure_active,
    default_ms: float,
    min_ms: float,
    max_ms: float,
    pressure_ms_default: float,
) -> float:
    raw_base = getattr(
        app,
        "_status_stream_position_coalesce_ms",
        default_ms,
    )
    raw_pressure = getattr(
        app,
        "_status_stream_position_pressure_coalesce_ms",
        pressure_ms_default,
    )
    try:
        base_ms = float(raw_base)
    except Exception:
        base_ms = default_ms
    try:
        pressure_ms = float(raw_pressure)
    except Exception:
        pressure_ms = pressure_ms_default
    base_ms = max(min_ms, min(max_ms, base_ms))
    pressure_ms = max(min_ms, min(max_ms, pressure_ms))
    interval_ms = max(base_ms, pressure_ms) if status_positions_pressure_active(app) else base_ms
    interval_ms = max(min_ms, min(max_ms, interval_ms))
    return interval_ms / 1000.0


def clear_coalesced_status_positions_state(app, *, log_suppressed) -> None:
    after_id = getattr(app, "_status_positions_coalesce_after_id", None)
    if after_id is not None:
        after_cancel = getattr(app, "after_cancel", None)
        if callable(after_cancel):
            try:
                after_cancel(after_id)
            except Exception as exc:
                log_suppressed("Failed canceling coalesced status-positions callback", exc)
    setattr(app, "_status_positions_coalesce_after_id", None)
    setattr(app, "_status_positions_coalesce_pending_fields", None)


def flush_coalesced_status_positions(
    app,
    *,
    time_module,
    status_fields_type,
    update_positions_and_macro_state,
    sync_manual_jog_prediction_with_status,
    record_status_perf_metric,
    noncritical_budget_ms: float,
) -> None:
    setattr(app, "_status_positions_coalesce_after_id", None)
    fields = getattr(app, "_status_positions_coalesce_pending_fields", None)
    setattr(app, "_status_positions_coalesce_pending_fields", None)
    if not isinstance(fields, status_fields_type):
        return
    started = time_module.perf_counter()
    try:
        update_positions_and_macro_state(
            app,
            fields,
            event_started_perf=None,
            noncritical_budget_ms=noncritical_budget_ms,
        )
        sync_manual_jog_prediction_with_status(app)
    finally:
        setattr(app, "_status_positions_last_apply_ts", float(time_module.monotonic()))
        record_status_perf_metric(
            app,
            "positions_coalesced_apply",
            (time_module.perf_counter() - started) * 1000.0,
        )


def queue_coalesced_status_positions_update(
    app,
    fields,
    *,
    force_defer: bool,
    time_module,
    clone_status_fields,
    stream_status_positions_coalesce_active,
    status_positions_coalesce_interval_s,
    flush_coalesced_status_positions,
    log_suppressed,
    record_status_perf_metric,
    default_pressure_min_defer_ms: int,
) -> bool:
    if not stream_status_positions_coalesce_active(app):
        return False
    now_mono = float(time_module.monotonic())
    interval_s = status_positions_coalesce_interval_s(app)
    last_apply_ts = float(getattr(app, "_status_positions_last_apply_ts", 0.0) or 0.0)
    pending_after_id = getattr(app, "_status_positions_coalesce_after_id", None)
    ready_for_deferred_apply = (
        pending_after_id is None
        and (last_apply_ts <= 0.0 or (now_mono - last_apply_ts) >= interval_s)
    )

    setattr(app, "_status_positions_coalesce_pending_fields", clone_status_fields(fields))
    if pending_after_id is not None:
        return True

    remaining_s = (
        0.0
        if ready_for_deferred_apply
        else max(0.0, interval_s - max(0.0, now_mono - last_apply_ts))
    )
    raw_pressure_min_delay_ms = getattr(
        app,
        "_status_positions_pressure_min_defer_ms",
        default_pressure_min_defer_ms,
    )
    try:
        pressure_min_delay_ms = int(raw_pressure_min_delay_ms)
    except Exception:
        pressure_min_delay_ms = default_pressure_min_defer_ms
    pressure_min_delay_ms = max(1, min(100, pressure_min_delay_ms))
    delay_ms = max(
        pressure_min_delay_ms if force_defer else 1,
        int(round(remaining_s * 1000.0)),
    )
    after_fn = getattr(app, "after", None)
    if not callable(after_fn):
        setattr(app, "_status_positions_coalesce_pending_fields", None)
        return False

    try:
        callback_id = after_fn(delay_ms, lambda: flush_coalesced_status_positions(app))
    except Exception as exc:
        setattr(app, "_status_positions_coalesce_pending_fields", None)
        log_suppressed("Failed scheduling coalesced status-positions callback", exc)
        return False
    setattr(app, "_status_positions_coalesce_after_id", callback_id)
    record_status_perf_metric(app, "positions_coalesced_defer", 0.0)
    if force_defer:
        record_status_perf_metric(app, "positions_coalesced_pressure_defer", 0.0)
    return True
