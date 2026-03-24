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

import time
import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import cast

from simple_sender.ui.dro import format_dro_value
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.stream_completion import (
    begin_deferred_completion_wait,
    end_deferred_completion_wait,
)
from simple_sender.utils.constants import (
    JOG_DRO_SMOOTHING_ALL_JOG,
    JOG_DRO_SMOOTHING_CHOICES,
    JOG_DRO_SMOOTHING_OFF,
    JOG_DRO_SMOOTHING_UI_JOG_ONLY,
    RT_STATUS,
)
from .status_parsing import _StatusFields
from .status_parsing import _clone_status_fields as _clone_status_fields_impl
from .status_parsing import _parse_status_fields as _parse_status_fields_impl
from .status_parsing import _parse_xyz_triplet as _parse_xyz_triplet_impl
from .status_parsing import _position_deadband_report_units as _position_deadband_report_units_impl
from .status_parsing import _rounded_xyz as _rounded_xyz_impl
from .status_parsing import _status_relaxed_idle_signature as _status_relaxed_idle_signature_impl
from .status_parsing import _status_state_token as _status_state_token_impl
from .status_parsing import _unit_scale_cached as _unit_scale_cached_impl
from .status_parsing import _units_ratio as _units_ratio_impl
from .status_machine_state import _format_hhmm as _format_hhmm_impl
from .status_machine_state import _apply_machine_state_visuals as _apply_machine_state_visuals_impl
from .status_machine_state import _machine_state_highlight_key as _machine_state_highlight_key_impl
from .status_machine_state import _render_machine_state_text as _render_machine_state_text_impl
from .status_machine_state import _resolve_display_state as _resolve_display_state_impl
from .status_machine_state import _run_progress_pct_from_bytes as _run_progress_pct_from_bytes_impl
from .status_machine_state import _run_progress_pct_from_lines as _run_progress_pct_from_lines_impl
from .status_machine_state import _run_progress_text as _run_progress_text_impl
from .status_machine_state import _status_allows_alarm_clear as _status_allows_alarm_clear_impl
from .status_machine_state import _stream_latched_banner_state as _stream_latched_banner_state_impl
from .status_units import _parse_modal_units as _parse_modal_units_impl
from .status_units import _parse_report_units_setting as _parse_report_units_setting_impl
from .stream_state_ui import apply_stream_busy_state, restore_controls_after_stream

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_WPOS_FLASH_MIN_INTERVAL_S = 0.25
_DRO_DISPLAY_STEP = 0.001
_STATUS_SETTLING_MIN_INTERVAL_S = 0.2
_STATUS_SLOW_LOG_MS = 50.0
_STATUS_NONCRITICAL_BUDGET_MS = 8.0
_STATUS_STREAM_POSITION_COALESCE_DEFAULT_MS = 80.0
_STATUS_STREAM_POSITION_COALESCE_MIN_MS = 20.0
_STATUS_STREAM_POSITION_COALESCE_MAX_MS = 250.0
_STATUS_STREAM_POSITION_COALESCE_PRESSURE_MS = 140.0
_STATUS_STREAM_POSITION_COALESCE_PRESSURE_RECOVERY_S = 1.5
_STATUS_STREAM_POSITION_COALESCE_PRESSURE_PENDING_THRESHOLD = 5
_STATUS_STREAM_POSITION_COALESCE_PRESSURE_MIN_DEFER_MS = 12
_JOG_DRO_PREDICT_TICK_MS = 60
_JOG_DRO_PREDICT_DEFAULT_HORIZON_S = 1.25
_JOG_DRO_PREDICT_MIN_HORIZON_S = 0.35
_JOG_DRO_PREDICT_MAX_HORIZON_S = 2.5
_JOG_DRO_PREDICT_SYNC_HORIZON_MULTIPLIER = 1.25
_JOG_DRO_OBSERVED_VELOCITY_ALPHA = 0.35
_JOG_DRO_STATIONARY_DISTANCE_EPS = 0.002
_JOG_DRO_ZERO_FEED_EPS = 0.05
_JOG_DRO_INTERP_UNSYNCED_MAX_DT_S = 0.2


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _schedule_status_ui_callback(
    app,
    *,
    callback_attr: str,
    callback: Callable[[], None],
    context: str,
    replace_pending: bool = True,
) -> None:
    pending_after_id = getattr(app, callback_attr, None)
    if pending_after_id is not None:
        if not bool(replace_pending):
            return
        after_cancel = getattr(app, "after_cancel", None)
        if callable(after_cancel):
            try:
                after_cancel(pending_after_id)
            except Exception as exc:
                _log_suppressed(
                    f"Failed canceling deferred status callback: {callback_attr}", exc
                )
        setattr(app, callback_attr, None)

    def _run() -> None:
        setattr(app, callback_attr, None)
        try:
            callback()
        except Exception as exc:
            _log_suppressed(context, exc)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            setattr(app, callback_attr, after_fn(0, _run))
            return
        except Exception as exc:
            _log_suppressed(
                f"Failed scheduling deferred status callback: {callback_attr}",
                exc,
            )
    _run()


def _with_macro_vars_nonblocking(
    app,
    callback: Callable[[dict], None],
    *,
    context: str,
) -> bool:
    macro_executor = getattr(app, "macro_executor", None)
    lock = getattr(macro_executor, "_macro_vars_lock", None)
    macro_vars = getattr(macro_executor, "_macro_vars", None)
    if (
        lock is not None
        and hasattr(lock, "acquire")
        and hasattr(lock, "release")
        and isinstance(macro_vars, dict)
    ):
        acquired = False
        try:
            acquired = bool(lock.acquire(blocking=False))
        except Exception:
            acquired = False
        if not acquired:
            return False
        try:
            callback(macro_vars)
            return True
        except Exception as exc:
            _log_suppressed(context, exc)
            return False
        finally:
            try:
                lock.release()
            except Exception:
                pass
    if macro_executor is None or not hasattr(macro_executor, "macro_vars"):
        return False
    try:
        with macro_executor.macro_vars() as macro_vars_ctx:
            if isinstance(macro_vars_ctx, dict):
                callback(macro_vars_ctx)
                return True
    except Exception as exc:
        _log_suppressed(context, exc)
    return False


def _signal_thread_event(obj, attr_name: str) -> None:
    evt = getattr(obj, attr_name, None)
    if isinstance(evt, threading.Event):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed(f"Failed signaling thread event {attr_name}", exc)


def _status_state_token(raw: str) -> str:
    return cast(str, _status_state_token_impl(raw))


def _status_relaxed_idle_signature(raw: str) -> str:
    return cast(str, _status_relaxed_idle_signature_impl(raw))


def _record_status_perf_metric(app, name: str, elapsed_ms: float) -> None:
    if not bool(getattr(app, "_status_perf_metrics_enabled", False)):
        return
    metric_name = str(name or "").strip()
    if not metric_name:
        return
    try:
        metrics = getattr(app, "_status_perf_metrics", None)
        if not isinstance(metrics, dict):
            metrics = {}
            setattr(app, "_status_perf_metrics", metrics)
        entry = metrics.get(metric_name)
        if not isinstance(entry, dict):
            entry = {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
            metrics[metric_name] = entry
        entry["count"] = int(entry.get("count", 0) or 0) + 1
        entry["total_ms"] = float(entry.get("total_ms", 0.0) or 0.0) + max(0.0, float(elapsed_ms))
        entry["max_ms"] = max(float(entry.get("max_ms", 0.0) or 0.0), max(0.0, float(elapsed_ms)))
    except Exception as exc:
        _log_suppressed("Failed recording status timing metric", exc)


def _status_settling_active(app) -> bool:
    return bool(getattr(app, "_gcode_load_settling", False))


def _status_connect_settling_active(app) -> bool:
    if not bool(getattr(app, "connected", False)):
        return False
    if _stream_active_or_finishing(app):
        return False
    try:
        until_ts = float(getattr(app, "_status_connect_settling_until_ts", 0.0) or 0.0)
    except Exception:
        return False
    if until_ts <= 0.0:
        return False
    try:
        now_mono = float(time.monotonic())
    except Exception:
        return False
    if now_mono >= until_ts:
        try:
            setattr(app, "_status_connect_settling_until_ts", 0.0)
        except Exception:
            pass
        return False
    return True


def _status_apply_interval_ok(app, state_token: str) -> bool:
    if not _status_settling_active(app):
        return True
    now = time.monotonic()
    state_lower = str(state_token or "").strip().lower()
    if state_lower.startswith(("jog", "run", "hold", "home")):
        app._status_settling_last_state = state_token
        app._status_settling_last_apply_ts = now
        return True
    last_state = str(getattr(app, "_status_settling_last_state", "") or "")
    last_ts = float(getattr(app, "_status_settling_last_apply_ts", 0.0) or 0.0)
    if state_token and state_token == last_state and (now - last_ts) < _STATUS_SETTLING_MIN_INTERVAL_S:
        app._status_settling_drop_count = int(getattr(app, "_status_settling_drop_count", 0) or 0) + 1
        return False
    app._status_settling_last_state = state_token
    app._status_settling_last_apply_ts = now
    return True


def _status_allows_alarm_clear(app) -> bool:
    return cast(bool, _status_allows_alarm_clear_impl(app))


def _apply_machine_state_minimal(app, state: str, display_state: str) -> None:
    state_lower = str(state or "").strip().lower()
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if _status_allows_alarm_clear(app):
            app._set_alarm_lock(False)
        if (not bool(getattr(app, "_alarm_locked", False))) and not getattr(
            app, "_macro_status_active", False
        ):
            banner_state = _stream_latched_banner_state(app, state, display_state)
            rendered_state = _render_machine_state_text(app, state, banner_state)
            _apply_machine_state_visuals(
                app,
                rendered_state=rendered_state,
                banner_state=banner_state,
                width_context="Failed adjusting machine-state width during settling",
                highlight_context="Failed updating machine-state highlight during settling",
            )
    def _update_macro_state(macro_vars: dict) -> None:
        macro_vars["state"] = state
        macro_vars["_status_seq"] = int(macro_vars.get("_status_seq", 0) or 0) + 1

    _with_macro_vars_nonblocking(
        app,
        _update_macro_state,
        context="Failed updating macro state during settling status handling",
    )


def _log_slow_status_event(
    app,
    *,
    total_ms: float,
    state: str,
    parse_ms: float,
    apply_ms: float,
    positions_ms: float,
    deferred_ms: float,
    settling: bool,
) -> None:
    if total_ms <= _STATUS_SLOW_LOG_MS:
        return
    logger.info(
        "[ui] Slow status event: total=%.2fms state=%s tab=%s settling=%s parse=%.2fms apply=%.2fms "
        "positions=%.2fms deferred=%.2fms dropped_settling=%d",
        total_ms,
        str(state or "?"),
        str(getattr(app, "_active_tab_label", "") or "unknown"),
        bool(settling),
        parse_ms,
        apply_ms,
        positions_ms,
        deferred_ms,
        int(getattr(app, "_status_settling_drop_count", 0) or 0),
    )


def _stream_active_or_finishing(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    return getattr(app, "_stream_state", None) in ("running", "paused")


def _performance_mode_enabled(app) -> bool:
    mode_var = getattr(app, "performance_mode", None)
    if mode_var is not None and hasattr(mode_var, "get"):
        try:
            return bool(mode_var.get())
        except Exception:
            return False
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("performance_mode", False))
        except Exception:
            return False
    return False


def _schedule_request_settings_dump(app) -> None:
    if bool(getattr(app, "_settings_dump_deferred_pending", False)):
        return

    def _run() -> None:
        app._settings_dump_deferred_pending = False
        try:
            app._request_settings_dump()
        except Exception as exc:
            _log_suppressed("Failed requesting deferred settings dump", exc)

    after = getattr(app, "after", None)
    if callable(after):
        try:
            app._settings_dump_deferred_pending = True
            after(0, _run)
            return
        except Exception as exc:
            app._settings_dump_deferred_pending = False
            _log_suppressed("Failed scheduling deferred settings dump request", exc)
    _run()


def _homing_idle_grace_seconds(app) -> float:
    interval = 0.2
    poll_interval = getattr(app, "status_poll_interval", None)
    try:
        if poll_interval is not None and hasattr(poll_interval, "get"):
            interval = float(poll_interval.get())
        elif poll_interval is not None:
            interval = float(poll_interval)
    except Exception as exc:
        _log_suppressed("Failed reading status poll interval for homing grace", exc)
        interval = 0.2
    if interval <= 0:
        interval = 0.2
    return max(0.3, min(2.0, interval * 3.0))


def _maybe_restore_pending_g90(app) -> None:
    if not getattr(app, "_pending_force_g90", False):
        return
    if not app.grbl.is_connected():
        return
    if getattr(app, "_alarm_locked", False):
        return
    if app.grbl.is_streaming() or _stream_active_or_finishing(app):
        return
    try:
        app.grbl.send_immediate("G90", source="autolevel")
    except Exception as exc:
        _log_suppressed("Failed to restore pending G90", exc)
        return
    app._pending_force_g90 = False
    try:
        app.ui_q.put(("log", "[autolevel] Restored G90 after alarm clear."))
    except Exception as exc:
        _log_suppressed("Failed to queue G90 restore log message", exc)


def _parse_modal_units(app, raw: str) -> None:
    _parse_modal_units_impl(
        app,
        raw,
        log_suppressed=_log_suppressed,
        signal_thread_event=_signal_thread_event,
    )


def _parse_report_units_setting(app, raw: str) -> None:
    _parse_report_units_setting_impl(app, raw, log_suppressed=_log_suppressed)


def _clone_status_fields(fields: _StatusFields) -> _StatusFields:
    return cast(_StatusFields, _clone_status_fields_impl(fields))


def _stream_status_positions_coalesce_active(app) -> bool:
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state not in {"running", "paused"}:
        return False
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return False
    return True


def _status_positions_pressure_active(app, *, now_mono: float | None = None) -> bool:
    if now_mono is None:
        now_mono = float(time.monotonic())
    pressure_until_ts = float(
        getattr(app, "_status_positions_pressure_until_ts", 0.0) or 0.0
    )
    return pressure_until_ts > 0.0 and now_mono < pressure_until_ts


def _mark_status_positions_pressure(app, *, now_mono: float | None = None) -> None:
    if now_mono is None:
        now_mono = float(time.monotonic())
    raw_recovery_s = getattr(
        app,
        "_status_positions_pressure_recovery_s",
        _STATUS_STREAM_POSITION_COALESCE_PRESSURE_RECOVERY_S,
    )
    try:
        recovery_s = float(raw_recovery_s)
    except Exception:
        recovery_s = _STATUS_STREAM_POSITION_COALESCE_PRESSURE_RECOVERY_S
    recovery_s = max(0.2, min(10.0, recovery_s))
    setattr(app, "_status_positions_pressure_until_ts", float(now_mono + recovery_s))


def _status_ui_queue_pending_depth(app) -> int:
    ui_q = getattr(app, "ui_q", None)
    if ui_q is None or not hasattr(ui_q, "qsize"):
        return 0
    try:
        return max(0, int(ui_q.qsize()))
    except Exception:
        return 0


def _status_positions_should_force_defer(
    app,
    *,
    event_started_perf: float | None,
    noncritical_budget_ms: float,
) -> bool:
    if not _stream_status_positions_coalesce_active(app):
        return False
    now_mono = float(time.monotonic())
    raw_pending_threshold = getattr(
        app,
        "_status_positions_pressure_pending_threshold",
        _STATUS_STREAM_POSITION_COALESCE_PRESSURE_PENDING_THRESHOLD,
    )
    try:
        pending_threshold = int(raw_pending_threshold)
    except Exception:
        pending_threshold = _STATUS_STREAM_POSITION_COALESCE_PRESSURE_PENDING_THRESHOLD
    pending_threshold = max(1, pending_threshold)
    pending_depth = _status_ui_queue_pending_depth(app)
    if pending_depth >= pending_threshold:
        _mark_status_positions_pressure(app, now_mono=now_mono)
        _record_status_perf_metric(app, "positions_pressure_queue", 0.0)
        return True
    if event_started_perf is not None:
        elapsed_ms = max(0.0, (time.perf_counter() - float(event_started_perf)) * 1000.0)
        if elapsed_ms >= max(1.0, float(noncritical_budget_ms)):
            _mark_status_positions_pressure(app, now_mono=now_mono)
            _record_status_perf_metric(app, "positions_pressure_budget", 0.0)
            return True
    return _status_positions_pressure_active(app, now_mono=now_mono)


def _status_positions_coalesce_interval_s(app) -> float:
    raw_base = getattr(
        app,
        "_status_stream_position_coalesce_ms",
        _STATUS_STREAM_POSITION_COALESCE_DEFAULT_MS,
    )
    raw_pressure = getattr(
        app,
        "_status_stream_position_pressure_coalesce_ms",
        _STATUS_STREAM_POSITION_COALESCE_PRESSURE_MS,
    )
    try:
        base_ms = float(raw_base)
    except Exception:
        base_ms = _STATUS_STREAM_POSITION_COALESCE_DEFAULT_MS
    try:
        pressure_ms = float(raw_pressure)
    except Exception:
        pressure_ms = _STATUS_STREAM_POSITION_COALESCE_PRESSURE_MS
    base_ms = max(
        _STATUS_STREAM_POSITION_COALESCE_MIN_MS,
        min(_STATUS_STREAM_POSITION_COALESCE_MAX_MS, base_ms),
    )
    pressure_ms = max(
        _STATUS_STREAM_POSITION_COALESCE_MIN_MS,
        min(_STATUS_STREAM_POSITION_COALESCE_MAX_MS, pressure_ms),
    )
    interval_ms = (
        max(base_ms, pressure_ms)
        if _status_positions_pressure_active(app)
        else base_ms
    )
    interval_ms = max(
        _STATUS_STREAM_POSITION_COALESCE_MIN_MS,
        min(_STATUS_STREAM_POSITION_COALESCE_MAX_MS, interval_ms),
    )
    return interval_ms / 1000.0


def _clear_coalesced_status_positions_state(app) -> None:
    after_id = getattr(app, "_status_positions_coalesce_after_id", None)
    if after_id is not None:
        after_cancel = getattr(app, "after_cancel", None)
        if callable(after_cancel):
            try:
                after_cancel(after_id)
            except Exception as exc:
                _log_suppressed("Failed canceling coalesced status-positions callback", exc)
    setattr(app, "_status_positions_coalesce_after_id", None)
    setattr(app, "_status_positions_coalesce_pending_fields", None)


def _flush_coalesced_status_positions(app) -> None:
    setattr(app, "_status_positions_coalesce_after_id", None)
    fields = getattr(app, "_status_positions_coalesce_pending_fields", None)
    setattr(app, "_status_positions_coalesce_pending_fields", None)
    if not isinstance(fields, _StatusFields):
        return
    started = time.perf_counter()
    try:
        _update_positions_and_macro_state(
            app,
            fields,
            event_started_perf=None,
            noncritical_budget_ms=_STATUS_NONCRITICAL_BUDGET_MS,
        )
        sync_manual_jog_prediction_with_status(app)
    finally:
        setattr(app, "_status_positions_last_apply_ts", float(time.monotonic()))
        _record_status_perf_metric(
            app,
            "positions_coalesced_apply",
            (time.perf_counter() - started) * 1000.0,
        )


def _queue_coalesced_status_positions_update(
    app,
    fields: _StatusFields,
    *,
    force_defer: bool = False,
) -> bool:
    if not _stream_status_positions_coalesce_active(app):
        return False
    now_mono = float(time.monotonic())
    interval_s = _status_positions_coalesce_interval_s(app)
    last_apply_ts = float(getattr(app, "_status_positions_last_apply_ts", 0.0) or 0.0)
    pending_after_id = getattr(app, "_status_positions_coalesce_after_id", None)
    if (
        not force_defer
        and pending_after_id is None
        and (last_apply_ts <= 0.0 or (now_mono - last_apply_ts) >= interval_s)
    ):
        return False

    setattr(app, "_status_positions_coalesce_pending_fields", _clone_status_fields(fields))
    if pending_after_id is not None:
        return True

    remaining_s = max(0.0, interval_s - max(0.0, now_mono - last_apply_ts))
    raw_pressure_min_delay_ms = getattr(
        app,
        "_status_positions_pressure_min_defer_ms",
        _STATUS_STREAM_POSITION_COALESCE_PRESSURE_MIN_DEFER_MS,
    )
    try:
        pressure_min_delay_ms = int(raw_pressure_min_delay_ms)
    except Exception:
        pressure_min_delay_ms = _STATUS_STREAM_POSITION_COALESCE_PRESSURE_MIN_DEFER_MS
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
        callback_id = after_fn(
            delay_ms,
            lambda: _flush_coalesced_status_positions(app),
        )
    except Exception as exc:
        setattr(app, "_status_positions_coalesce_pending_fields", None)
        _log_suppressed("Failed scheduling coalesced status-positions callback", exc)
        return False
    setattr(app, "_status_positions_coalesce_after_id", callback_id)
    _record_status_perf_metric(app, "positions_coalesced_defer", 0.0)
    if force_defer:
        _record_status_perf_metric(app, "positions_coalesced_pressure_defer", 0.0)
    return True


def _parse_status_fields(raw: str) -> _StatusFields:
    return cast(
        _StatusFields,
        _parse_status_fields_impl(raw, log_suppressed=_log_suppressed),
    )


def _resolve_display_state(app, state: str) -> str:
    return cast(
        str,
        _resolve_display_state_impl(
            app,
            state,
            homing_idle_grace_seconds=_homing_idle_grace_seconds,
            log_suppressed=_log_suppressed,
        ),
    )


def _format_hhmm(seconds: int) -> str:
    return cast(str, _format_hhmm_impl(seconds))


def _run_progress_pct_from_bytes(app) -> float | None:
    return cast(float | None, _run_progress_pct_from_bytes_impl(app))


def _run_progress_pct_from_lines(app) -> tuple[float | None, bool]:
    return cast(tuple[float | None, bool], _run_progress_pct_from_lines_impl(app))


def _run_progress_text(app) -> str:
    return cast(str, _run_progress_text_impl(app))


def _stream_latched_banner_state(app, state: str, display_state: str) -> str:
    return cast(str, _stream_latched_banner_state_impl(app, state, display_state))


def _render_machine_state_text(app, state: str, display_state: str) -> str:
    return cast(str, _render_machine_state_text_impl(app, state, display_state))


def _machine_state_highlight_key(state: str) -> str:
    return cast(str, _machine_state_highlight_key_impl(state))


def _apply_machine_state_visuals(
    app,
    *,
    rendered_state: str,
    banner_state: str,
    width_context: str,
    highlight_context: str,
) -> None:
    _apply_machine_state_visuals_impl(
        app,
        rendered_state=rendered_state,
        banner_state=banner_state,
        width_context=width_context,
        highlight_context=highlight_context,
        set_var_if_changed=_set_var_if_changed,
        machine_state_highlight_key=_machine_state_highlight_key,
        log_suppressed=_log_suppressed,
    )


def _apply_machine_state(app, state: str, display_state: str) -> bool:
    prev_state = str(getattr(app, "_machine_state_text", "") or "")
    prev_state_token = prev_state.strip().lower()
    if "|" in prev_state_token:
        prev_state_token = prev_state_token.split("|", 1)[0]
    next_state_token = str(state or "").strip().lower()
    if "|" in next_state_token:
        next_state_token = next_state_token.split("|", 1)[0]
    state_lower = state.lower()
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if bool(getattr(app, "_alarm_locked", False)):
            if _status_allows_alarm_clear(app):
                app._set_alarm_lock(False)
        elif not getattr(app, "_macro_status_active", False):
            banner_state = _stream_latched_banner_state(app, state, display_state)
            rendered_state = _render_machine_state_text(app, state, banner_state)
            _apply_machine_state_visuals(
                app,
                rendered_state=rendered_state,
                banner_state=banner_state,
                width_context="Failed adjusting machine state label width",
                highlight_context="Failed updating machine-state highlight",
            )
            if hasattr(app, "_update_current_highlight"):
                _schedule_status_ui_callback(
                    app,
                    callback_attr="_status_current_highlight_after_id",
                    callback=lambda: app._update_current_highlight(),
                    context="Failed updating current-line highlight from status state",
                    replace_pending=False,
                )
        _maybe_restore_pending_g90(app)

    if app._grbl_ready and app._pending_settings_refresh and not app._alarm_locked:
        if _stream_active_or_finishing(app) or app.grbl.is_streaming():
            return False
        app._pending_settings_refresh = False
        _schedule_request_settings_dump(app)
    controls_allowed = bool(
        app.connected
        and app._grbl_ready
        and app._status_seen
        and not app._alarm_locked
        and not _stream_active_or_finishing(app)
    )
    ready_now = job_controls_ready(app) if controls_allowed else False
    if ready_now != bool(getattr(app, "_job_controls_last_ready", False)):
        set_run_resume_from(app, ready_now)
        app._job_controls_last_ready = bool(ready_now)
    manual_last = bool(getattr(app, "_manual_controls_last_enabled", False))
    if bool(controls_allowed) != manual_last:
        new_manual_enabled = bool(controls_allowed)
        if _status_connect_settling_active(app):
            def _apply_manual_controls_deferred() -> None:
                app._set_manual_controls_enabled(new_manual_enabled)

            _schedule_status_ui_callback(
                app,
                callback_attr="_status_manual_controls_after_id",
                callback=_apply_manual_controls_deferred,
                context="Failed applying deferred manual-control state during connect settling",
            )
        else:
            app._set_manual_controls_enabled(new_manual_enabled)
    def _update_macro_state(macro_vars: dict) -> None:
        macro_vars["state"] = state
        macro_vars["_status_seq"] = int(macro_vars.get("_status_seq", 0) or 0) + 1

    _with_macro_vars_nonblocking(
        app,
        _update_macro_state,
        context="Failed updating macro state from status event",
    )
    _signal_thread_event(app, "_status_update_event")
    if next_state_token != prev_state_token:
        def _apply_transition_ui_updates() -> None:
            if hasattr(app, "_refresh_toolbar_action_focus"):
                app._refresh_toolbar_action_focus()
            if hasattr(app, "_update_quick_button_visibility"):
                app._update_quick_button_visibility()

        _schedule_status_ui_callback(
            app,
            callback_attr="_status_state_transition_ui_after_id",
            callback=_apply_transition_ui_updates,
            context="Failed applying state-transition toolbar/quick-button refresh",
        )
    return True


def _deferred_completion_target_total(app) -> int:
    try:
        executable_total = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    except Exception:
        executable_total = 0
    if executable_total > 0:
        return executable_total
    try:
        return int(getattr(app, "_gcode_total_lines", 0) or 0)
    except Exception:
        return 0


def _deferred_completion_bytes_done(app) -> bool:
    try:
        file_size = max(
            int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
            int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
        )
    except Exception:
        file_size = 0
    if file_size <= 0:
        return False
    try:
        acked = int(getattr(app, "_stream_acked_byte_offset", 0) or 0)
    except Exception:
        acked = 0
    return max(0, acked) >= file_size


def _sync_deferred_stream_completion(app, state: str) -> None:
    if not bool(getattr(app, "_stream_done_pending_idle", False)):
        return
    now_ts = time.time()
    total = _deferred_completion_target_total(app)
    done = max(0, int(getattr(app, "_last_acked_index", -1)) + 1)
    complete_by_lines = total > 0 and done >= total
    complete_by_bytes = _deferred_completion_bytes_done(app)
    if not complete_by_lines and not complete_by_bytes:
        begin_deferred_completion_wait(app, now_ts=now_ts)
        return
    if str(state or "").lower().startswith("idle"):
        end_deferred_completion_wait(app, now_ts=now_ts)
        app._stream_done_pending_idle = False
        app._stream_state = "done"
        notify_total = total if total > 0 else done
        if complete_by_bytes and done > 0 and done < notify_total:
            # Keep completion signaling consistent when non-executable file
            # lines exceed streamed executable commands.
            notify_total = done
        try:
            app.progress_pct.set(100)
        except Exception as exc:
            _log_suppressed("Failed finalizing deferred progress at stream completion", exc)

        def _finalize_completion_ui() -> None:
            app._deferred_stream_finalize_pending = False
            try:
                app._maybe_notify_job_completion(done, notify_total)
            except Exception as exc:
                _log_suppressed("Failed notifying deferred stream completion", exc)
            try:
                app.btn_pause.config(state="disabled")
                app.btn_resume.config(state="disabled")
            except Exception as exc:
                _log_suppressed("Failed finalizing pause/resume controls after deferred completion", exc)
            try:
                restore_controls_after_stream(
                    app,
                    job_ready_hook=job_controls_ready,
                    set_run_resume_hook=set_run_resume_from,
                )
            except Exception as exc:
                _log_suppressed("Failed finalizing manual controls after deferred completion", exc)
            apply_stream_busy_state(app, False, log_hook=_log_suppressed)
            try:
                if hasattr(app, "_update_joystick_polling_state"):
                    app._update_joystick_polling_state()
            except Exception as exc:
                _log_suppressed(
                    "Failed refreshing joystick polling after deferred completion",
                    exc,
                )
            try:
                app._apply_status_poll_profile()
            except Exception as exc:
                _log_suppressed("Failed applying status poll profile after deferred completion", exc)

        if bool(getattr(app, "_deferred_stream_finalize_pending", False)):
            return
        after = getattr(app, "after", None)
        if callable(after):
            try:
                app._deferred_stream_finalize_pending = True
                after(0, _finalize_completion_ui)
                return
            except Exception as exc:
                app._deferred_stream_finalize_pending = False
                _log_suppressed("Failed scheduling deferred stream-completion finalize callback", exc)
        _finalize_completion_ui()
        return
    begin_deferred_completion_wait(app, now_ts=now_ts)
    try:
        if int(app.progress_pct.get()) >= 100:
            app.progress_pct.set(99)
    except (AttributeError, TypeError, ValueError) as exc:
        _log_suppressed("Failed clamping deferred completion progress", exc)


def _parse_xyz_triplet(text: str) -> list[float] | None:
    return cast(
        list[float] | None,
        _parse_xyz_triplet_impl(text, log_suppressed=_log_suppressed),
    )


def _unit_scale_cached(unit_mode: str) -> float:
    return cast(float, _unit_scale_cached_impl(unit_mode))


def _position_deadband_report_units(report_units: str, modal_units: str) -> float:
    return cast(
        float,
        _position_deadband_report_units_impl(
            report_units,
            modal_units,
            dro_display_step=_DRO_DISPLAY_STEP,
        ),
    )


def _set_var_if_changed(var, value: str) -> bool:
    try:
        current = var.get()
    except Exception as exc:
        _log_suppressed("Failed reading UI variable value", exc)
        current = None
    if current == value:
        return False
    try:
        var.set(value)
    except Exception as exc:
        _log_suppressed("Failed writing UI variable value", exc)
        return False
    return True


def _snap_dro_to_last_status_raw(app) -> None:
    mpos_raw = getattr(app, "_mpos_raw", None)
    if not (isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3):
        return
    try:
        report_units = str(getattr(app, "_report_units", None) or app.unit_mode.get() or "mm")
    except Exception:
        report_units = "mm"
    try:
        modal_units = str(app.unit_mode.get() or "mm")
    except Exception:
        modal_units = report_units
    try:
        mpos = (float(mpos_raw[0]), float(mpos_raw[1]), float(mpos_raw[2]))
    except Exception:
        return
    try:
        _set_var_if_changed(app.mpos_x, format_dro_value(mpos[0], report_units, modal_units))
        _set_var_if_changed(app.mpos_y, format_dro_value(mpos[1], report_units, modal_units))
        _set_var_if_changed(app.mpos_z, format_dro_value(mpos[2], report_units, modal_units))
    except Exception as exc:
        _log_suppressed("Failed snapping MPos DRO values to last status raw coordinates", exc)
    wco_raw = getattr(app, "_wco_raw", None)
    if not (isinstance(wco_raw, (list, tuple)) and len(wco_raw) >= 3):
        return
    try:
        wpos = (
            float(mpos[0]) - float(wco_raw[0]),
            float(mpos[1]) - float(wco_raw[1]),
            float(mpos[2]) - float(wco_raw[2]),
        )
        _set_var_if_changed(app.wpos_x, format_dro_value(wpos[0], report_units, modal_units))
        _set_var_if_changed(app.wpos_y, format_dro_value(wpos[1], report_units, modal_units))
        _set_var_if_changed(app.wpos_z, format_dro_value(wpos[2], report_units, modal_units))
    except Exception as exc:
        _log_suppressed("Failed snapping WPos DRO values to last status raw coordinates", exc)


def _request_stop_status_refresh(app) -> None:
    try:
        mark_manual_motion = getattr(app, "_mark_manual_motion_activity", None)
        if callable(mark_manual_motion):
            mark_manual_motion(duration_s=2.0)
    except Exception as exc:
        _log_suppressed("Failed extending fast status-poll profile after jog stop", exc)
    grbl = getattr(app, "grbl", None)
    send_rt = getattr(grbl, "send_realtime", None)
    if not callable(send_rt):
        return

    def _send_status_query() -> None:
        try:
            send_rt(RT_STATUS)
        except Exception as exc:
            _log_suppressed("Failed sending immediate status query after jog stop", exc)

    _send_status_query()
    after_fn = getattr(app, "after", None)
    if not callable(after_fn):
        return
    try:
        after_fn(120, _send_status_query)
    except Exception as exc:
        _log_suppressed("Failed scheduling delayed status query after jog stop", exc)


def _units_ratio(from_units: str, to_units: str) -> float:
    return cast(float, _units_ratio_impl(from_units, to_units))


def _rounded_xyz(value: tuple[float, float, float] | None) -> list[float] | None:
    return cast(list[float] | None, _rounded_xyz_impl(value))


def _record_jog_dro_trace(app, kind: str, **payload: object) -> None:
    history = getattr(app, "_jog_dro_trace", None)
    if not isinstance(history, deque):
        history = deque(maxlen=1200)
        setattr(app, "_jog_dro_trace", history)
    entry: dict[str, object] = {
        "ts": round(float(time.time()), 6),
        "kind": str(kind or "").strip() or "unknown",
    }
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, tuple) and len(value) >= 3:
            rounded = _rounded_xyz((float(value[0]), float(value[1]), float(value[2])))
            if rounded is not None:
                entry[str(key)] = rounded
            continue
        if isinstance(value, float):
            entry[str(key)] = round(float(value), 6)
            continue
        entry[str(key)] = value
    try:
        history.append(entry)
    except Exception as exc:
        _log_suppressed("Failed appending jog DRO trace entry", exc)


def _record_jog_dro_delta_stats(
    app,
    *,
    dx: float,
    dy: float,
    dz: float,
    sync_interval_s: float | None = None,
    predict_horizon_s: float | None = None,
) -> None:
    stats = getattr(app, "_jog_dro_interp_stats", None)
    if not isinstance(stats, dict):
        stats = {
            "status_sync_count": 0,
            "delta_abs_avg_x": 0.0,
            "delta_abs_avg_y": 0.0,
            "delta_abs_avg_z": 0.0,
            "delta_abs_max_x": 0.0,
            "delta_abs_max_y": 0.0,
            "delta_abs_max_z": 0.0,
            "status_sync_interval_avg_s": 0.0,
            "status_sync_interval_max_s": 0.0,
            "predict_horizon_avg_s": 0.0,
            "predict_horizon_max_s": 0.0,
        }
        setattr(app, "_jog_dro_interp_stats", stats)
    count = int(stats.get("status_sync_count", 0) or 0) + 1
    dx_abs = abs(float(dx))
    dy_abs = abs(float(dy))
    dz_abs = abs(float(dz))
    prev_count = max(0, count - 1)
    stats["status_sync_count"] = count
    for axis, value_abs in (("x", dx_abs), ("y", dy_abs), ("z", dz_abs)):
        avg_key = f"delta_abs_avg_{axis}"
        max_key = f"delta_abs_max_{axis}"
        prev_avg = float(stats.get(avg_key, 0.0) or 0.0)
        next_avg = ((prev_avg * prev_count) + value_abs) / count
        stats[avg_key] = float(next_avg)
        stats[max_key] = max(float(stats.get(max_key, 0.0) or 0.0), value_abs)
    if sync_interval_s is not None:
        sync_value = max(0.0, float(sync_interval_s))
        prev_avg = float(stats.get("status_sync_interval_avg_s", 0.0) or 0.0)
        stats["status_sync_interval_avg_s"] = ((prev_avg * prev_count) + sync_value) / count
        stats["status_sync_interval_max_s"] = max(
            float(stats.get("status_sync_interval_max_s", 0.0) or 0.0),
            sync_value,
        )
    if predict_horizon_s is not None:
        horizon_value = max(0.0, float(predict_horizon_s))
        prev_avg = float(stats.get("predict_horizon_avg_s", 0.0) or 0.0)
        stats["predict_horizon_avg_s"] = ((prev_avg * prev_count) + horizon_value) / count
        stats["predict_horizon_max_s"] = max(
            float(stats.get("predict_horizon_max_s", 0.0) or 0.0),
            horizon_value,
        )


def _manual_jog_prediction_horizon_s(state: dict) -> float:
    try:
        observed_sync_interval_s = float(state.get("status_sync_interval_s", 0.0) or 0.0)
    except Exception:
        observed_sync_interval_s = 0.0
    if observed_sync_interval_s > 0.0:
        horizon_s = observed_sync_interval_s * float(_JOG_DRO_PREDICT_SYNC_HORIZON_MULTIPLIER)
    else:
        horizon_s = float(_JOG_DRO_PREDICT_DEFAULT_HORIZON_S)
    return max(
        float(_JOG_DRO_PREDICT_MIN_HORIZON_S),
        min(float(_JOG_DRO_PREDICT_MAX_HORIZON_S), float(horizon_s)),
    )


def _normalized_jog_prediction_source(source: str | None) -> str:
    raw = str(source or "").strip().lower()
    if not raw:
        return "jog"
    return raw


def _is_joystick_jog_source(source: str | None) -> bool:
    normalized = _normalized_jog_prediction_source(source)
    return normalized.startswith("joystick") or normalized.startswith("jog_hold")


def _normalized_jog_dro_smoothing_mode(app) -> str:
    fallback = JOG_DRO_SMOOTHING_OFF
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        raw = str(settings.get("jog_dro_smoothing_mode", fallback) or "").strip().lower()
        if raw in JOG_DRO_SMOOTHING_CHOICES:
            fallback = raw
    var = getattr(app, "jog_dro_smoothing_mode", None)
    if var is not None:
        try:
            raw = str(var.get() or "").strip().lower()
        except Exception:
            raw = ""
        if raw in JOG_DRO_SMOOTHING_CHOICES:
            return raw
    return fallback


def _jog_prediction_enabled_for_source(app, source: str | None) -> bool:
    mode = _normalized_jog_dro_smoothing_mode(app)
    if mode == JOG_DRO_SMOOTHING_OFF:
        return False
    if mode == JOG_DRO_SMOOTHING_ALL_JOG:
        return True
    if mode == JOG_DRO_SMOOTHING_UI_JOG_ONLY:
        return not _is_joystick_jog_source(source)
    return False


def _jog_prediction_should_run(app) -> bool:
    state = getattr(app, "_manual_jog_predict_state", None)
    source = None
    if isinstance(state, dict):
        source = str(state.get("source", "") or "").strip().lower() or None
    if not _jog_prediction_enabled_for_source(app, source):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    if not bool(getattr(app, "_grbl_ready", False)):
        return False
    if bool(getattr(app, "_alarm_locked", False)):
        return False
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return False
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state in {"running", "paused"}:
        return False
    state = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
    if state.startswith(("jog", "hold")):
        return True
    return bool(getattr(app, "_active_joystick_hold_binding", None))


def _clear_manual_jog_prediction(app) -> None:
    after_id = getattr(app, "_manual_jog_predict_after_id", None)
    if after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(after_id)
        except Exception as exc:
            _log_suppressed("Failed cancelling manual jog prediction timer", exc)
    app._manual_jog_predict_after_id = None
    app._manual_jog_predict_state = None
    app._manual_jog_predict_last_est_mpos = None
    app._manual_jog_predict_last_est_wpos = None


def _schedule_manual_jog_prediction_tick(app) -> None:
    if getattr(app, "_manual_jog_predict_after_id", None) is not None:
        return
    after_fn = getattr(app, "after", None)
    if not callable(after_fn):
        return
    try:
        app._manual_jog_predict_after_id = after_fn(
            int(_JOG_DRO_PREDICT_TICK_MS), lambda: _run_manual_jog_prediction_tick(app)
        )
    except Exception as exc:
        app._manual_jog_predict_after_id = None
        _log_suppressed("Failed scheduling manual jog prediction tick", exc)


def _run_manual_jog_prediction_tick(app) -> None:
    """Advance the UI-side jog prediction until the next real status sync."""

    app._manual_jog_predict_after_id = None
    # Snapshot and validate the last prediction state before doing any
    # interpolation work.
    state = getattr(app, "_manual_jog_predict_state", None)
    if not isinstance(state, dict):
        return
    if not _jog_prediction_should_run(app):
        _clear_manual_jog_prediction(app)
        return
    try:
        started_ts = float(state.get("start_ts", 0.0) or 0.0)
        anchor_ts = float(state.get("anchor_ts", started_ts) or started_ts)
        max_distance = max(0.0, float(state.get("max_distance_report", 0.0) or 0.0))
        base_mpos = state.get("base_mpos_report")
        velocity_report_s = state.get("velocity_report_s")
        unit_vec = state.get("unit_vec")
        speed_report_s = max(0.0, float(state.get("speed_report_s", 0.0) or 0.0))
        report_units = str(state.get("report_units", "mm") or "mm")
        freeze_interp = bool(state.get("freeze", False))
    except Exception as exc:
        _log_suppressed("Failed reading manual jog prediction state", exc)
        _clear_manual_jog_prediction(app)
        return
    try:
        modal_units = str(app.unit_mode.get())
    except Exception:
        modal_units = "mm"
    if (
        started_ts <= 0.0
        or max_distance <= 0.0
        or not isinstance(base_mpos, (list, tuple))
        or len(base_mpos) < 3
    ):
        _clear_manual_jog_prediction(app)
        return
    # Derive a velocity vector from either the last observed status delta or
    # the normalized jog direction plus reported speed.
    if isinstance(velocity_report_s, (list, tuple)) and len(velocity_report_s) >= 3:
        try:
            velocity_vec = (
                float(velocity_report_s[0]),
                float(velocity_report_s[1]),
                float(velocity_report_s[2]),
            )
        except Exception:
            velocity_vec = (0.0, 0.0, 0.0)
    elif isinstance(unit_vec, (list, tuple)) and len(unit_vec) >= 3 and speed_report_s > 0.0:
        velocity_vec = (
            float(unit_vec[0]) * float(speed_report_s),
            float(unit_vec[1]) * float(speed_report_s),
            float(unit_vec[2]) * float(speed_report_s),
        )
    else:
        velocity_vec = (0.0, 0.0, 0.0)
    # Clamp interpolation to a short horizon so the UI prediction stays bounded
    # and naturally yields back to real GRBL status data.
    prediction_horizon_s = _manual_jog_prediction_horizon_s(state)
    elapsed_s = max(0.0, time.monotonic() - anchor_ts)
    status_sync_interval_s = max(0.0, float(state.get("status_sync_interval_s", 0.0) or 0.0))
    if status_sync_interval_s > 0.0:
        elapsed_cap_s = min(float(prediction_horizon_s), status_sync_interval_s)
    else:
        elapsed_cap_s = min(float(prediction_horizon_s), float(_JOG_DRO_INTERP_UNSYNCED_MAX_DT_S))
    elapsed_s = min(elapsed_s, max(0.0, float(elapsed_cap_s)))
    feed_mm_min = abs(float(getattr(app, "_last_status_feed_raw", 0.0) or 0.0))
    feed_report_s = max(0.0, feed_mm_min / 60.0)
    if freeze_interp:
        traveled = 0.0
        est_mpos = (
            float(base_mpos[0]),
            float(base_mpos[1]),
            float(base_mpos[2]),
        )
    else:
        est_mpos = (
            float(base_mpos[0]) + float(velocity_vec[0]) * elapsed_s,
            float(base_mpos[1]) + float(velocity_vec[1]) * elapsed_s,
            float(base_mpos[2]) + float(velocity_vec[2]) * elapsed_s,
        )
        dx = float(est_mpos[0]) - float(base_mpos[0])
        dy = float(est_mpos[1]) - float(base_mpos[1])
        dz = float(est_mpos[2]) - float(base_mpos[2])
        traveled = (dx * dx + dy * dy + dz * dz) ** 0.5
        velocity_mag = (
            (float(velocity_vec[0]) * float(velocity_vec[0]))
            + (float(velocity_vec[1]) * float(velocity_vec[1]))
            + (float(velocity_vec[2]) * float(velocity_vec[2]))
        ) ** 0.5
        speed_cap = max(float(feed_report_s), float(velocity_mag))
        if speed_cap > 0.0:
            max_by_speed = max(0.0, (speed_cap * float(elapsed_s)) * 1.1)
        else:
            max_by_speed = 0.0
        if max_by_speed > 0.0 and traveled > max_by_speed and traveled > 1e-9:
            scale = max_by_speed / traveled
            est_mpos = (
                float(base_mpos[0]) + (dx * scale),
                float(base_mpos[1]) + (dy * scale),
                float(base_mpos[2]) + (dz * scale),
            )
            dx = float(est_mpos[0]) - float(base_mpos[0])
            dy = float(est_mpos[1]) - float(base_mpos[1])
            dz = float(est_mpos[2]) - float(base_mpos[2])
            traveled = (dx * dx + dy * dy + dz * dz) ** 0.5
        if traveled > max_distance and traveled > 1e-9:
            scale = max_distance / traveled
            est_mpos = (
                float(base_mpos[0]) + (dx * scale),
                float(base_mpos[1]) + (dy * scale),
                float(base_mpos[2]) + (dz * scale),
            )
            traveled = max_distance
    if traveled <= 0.0 and freeze_interp:
        _schedule_manual_jog_prediction_tick(app)
        return
    try:
        est_wpos: tuple[float, float, float] | None = None
        mpos_x = format_dro_value(est_mpos[0], report_units, modal_units)
        mpos_y = format_dro_value(est_mpos[1], report_units, modal_units)
        mpos_z = format_dro_value(est_mpos[2], report_units, modal_units)
        _set_var_if_changed(app.mpos_x, mpos_x)
        _set_var_if_changed(app.mpos_y, mpos_y)
        _set_var_if_changed(app.mpos_z, mpos_z)
        wco_raw = getattr(app, "_wco_raw", None)
        if isinstance(wco_raw, (list, tuple)) and len(wco_raw) >= 3:
            est_wpos = (
                float(est_mpos[0]) - float(wco_raw[0]),
                float(est_mpos[1]) - float(wco_raw[1]),
                float(est_mpos[2]) - float(wco_raw[2]),
            )
            wpos_x = format_dro_value(est_wpos[0], report_units, modal_units)
            wpos_y = format_dro_value(est_wpos[1], report_units, modal_units)
            wpos_z = format_dro_value(est_wpos[2], report_units, modal_units)
            _set_var_if_changed(app.wpos_x, wpos_x)
            _set_var_if_changed(app.wpos_y, wpos_y)
            _set_var_if_changed(app.wpos_z, wpos_z)
        app._manual_jog_predict_last_est_mpos = (
            float(est_mpos[0]),
            float(est_mpos[1]),
            float(est_mpos[2]),
        )
        app._manual_jog_predict_last_est_wpos = (
            (float(est_wpos[0]), float(est_wpos[1]), float(est_wpos[2]))
            if est_wpos is not None
            else None
        )
        _record_jog_dro_trace(
            app,
            "interp",
            elapsed_s=float(elapsed_s),
            horizon_s=float(prediction_horizon_s),
            traveled=float(traveled),
            velocity_report_s=velocity_vec,
            feed_report_s=float(feed_report_s),
            freeze=bool(freeze_interp),
            est_mpos=app._manual_jog_predict_last_est_mpos,
            est_wpos=app._manual_jog_predict_last_est_wpos,
        )
    except Exception as exc:
        _log_suppressed("Failed applying manual jog DRO interpolation", exc)
        _clear_manual_jog_prediction(app)
        return
    _schedule_manual_jog_prediction_tick(app)


def start_manual_jog_prediction(
    app,
    *,
    dx: float,
    dy: float,
    dz: float,
    feed: float,
    unit_mode: str,
    source: str | None = None,
) -> None:
    normalized_source = _normalized_jog_prediction_source(source)
    if not _jog_prediction_enabled_for_source(app, normalized_source):
        _clear_manual_jog_prediction(app)
        return
    try:
        dx_val = float(dx)
        dy_val = float(dy)
        dz_val = float(dz)
        feed_val = max(0.0, float(feed))
    except Exception:
        return
    vector_len = (dx_val * dx_val + dy_val * dy_val + dz_val * dz_val) ** 0.5
    if vector_len <= 0.0 or feed_val <= 0.0:
        _clear_manual_jog_prediction(app)
        return
    report_units = str(getattr(app, "_report_units", None) or unit_mode or "mm")
    ratio = _units_ratio(str(unit_mode or "mm"), report_units)
    dx_report = dx_val * ratio
    dy_report = dy_val * ratio
    dz_report = dz_val * ratio
    max_distance_report = (dx_report * dx_report + dy_report * dy_report + dz_report * dz_report) ** 0.5
    if max_distance_report <= 0.0:
        _clear_manual_jog_prediction(app)
        return
    speed_report_s = (feed_val * ratio) / 60.0
    if speed_report_s <= 0.0:
        _clear_manual_jog_prediction(app)
        return
    mpos_raw = getattr(app, "_mpos_raw", None)
    if not (isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3):
        return
    app._manual_jog_predict_state = {
        "start_ts": float(time.monotonic()),
        "anchor_ts": float(time.monotonic()),
        "source": normalized_source,
        "base_mpos_report": (
            float(mpos_raw[0]),
            float(mpos_raw[1]),
            float(mpos_raw[2]),
        ),
        "unit_vec": (
            float(dx_report / max_distance_report),
            float(dy_report / max_distance_report),
            float(dz_report / max_distance_report),
        ),
        "speed_report_s": float(speed_report_s),
        "velocity_report_s": (
            float((dx_report / max_distance_report) * speed_report_s),
            float((dy_report / max_distance_report) * speed_report_s),
            float((dz_report / max_distance_report) * speed_report_s),
        ),
        "max_distance_report": float(max_distance_report),
        "report_units": report_units,
        "status_sync_interval_s": 0.0,
        "status_sync_last_ts": 0.0,
        "last_status_mpos_report": (
            float(mpos_raw[0]),
            float(mpos_raw[1]),
            float(mpos_raw[2]),
        ),
        "freeze": False,
    }
    app._manual_jog_predict_last_est_mpos = (
        float(mpos_raw[0]),
        float(mpos_raw[1]),
        float(mpos_raw[2]),
    )
    app._manual_jog_predict_last_est_wpos = None
    _record_jog_dro_trace(
        app,
        "start",
        dx=float(dx_val),
        dy=float(dy_val),
        dz=float(dz_val),
        feed=float(feed_val),
        unit_mode=str(unit_mode or ""),
        source=normalized_source,
        report_units=report_units,
        base_mpos=app._manual_jog_predict_last_est_mpos,
        max_distance_report=float(max_distance_report),
        horizon_s=float(_manual_jog_prediction_horizon_s(app._manual_jog_predict_state)),
    )
    _schedule_manual_jog_prediction_tick(app)


def sync_manual_jog_prediction_with_status(app) -> None:
    state = getattr(app, "_manual_jog_predict_state", None)
    if not isinstance(state, dict):
        return
    if not _jog_prediction_should_run(app):
        _clear_manual_jog_prediction(app)
        return
    mpos_raw = getattr(app, "_mpos_raw", None)
    if isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3:
        try:
            now_mono = float(time.monotonic())
            sync_interval_s: float | None = None
            last_sync_ts = float(state.get("status_sync_last_ts", 0.0) or 0.0)
            prev_status_mpos = state.get("last_status_mpos_report")
            if last_sync_ts > 0.0 and now_mono > last_sync_ts:
                observed_interval_s = now_mono - last_sync_ts
                prev_interval_s = float(state.get("status_sync_interval_s", 0.0) or 0.0)
                if prev_interval_s > 0.0:
                    sync_interval_s = (prev_interval_s * 0.7) + (observed_interval_s * 0.3)
                else:
                    sync_interval_s = observed_interval_s
                state["status_sync_interval_s"] = float(sync_interval_s)
            state["status_sync_last_ts"] = now_mono
            prediction_horizon_s = _manual_jog_prediction_horizon_s(state)
            actual_mpos = (
                float(mpos_raw[0]),
                float(mpos_raw[1]),
                float(mpos_raw[2]),
            )
            est_mpos = getattr(app, "_manual_jog_predict_last_est_mpos", None)
            if isinstance(est_mpos, tuple) and len(est_mpos) >= 3:
                dx = float(actual_mpos[0]) - float(est_mpos[0])
                dy = float(actual_mpos[1]) - float(est_mpos[1])
                dz = float(actual_mpos[2]) - float(est_mpos[2])
                _record_jog_dro_delta_stats(
                    app,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    sync_interval_s=sync_interval_s,
                    predict_horizon_s=prediction_horizon_s,
                )
                _record_jog_dro_trace(
                    app,
                    "status_sync",
                    actual_mpos=actual_mpos,
                    est_mpos=(
                        float(est_mpos[0]),
                        float(est_mpos[1]),
                        float(est_mpos[2]),
                    ),
                    delta=(dx, dy, dz),
                    sync_interval_s=sync_interval_s,
                    horizon_s=float(prediction_horizon_s),
                )
            observed_velocity = None
            observed_speed = 0.0
            if (
                isinstance(prev_status_mpos, (list, tuple))
                and len(prev_status_mpos) >= 3
                and last_sync_ts > 0.0
                and now_mono > last_sync_ts
            ):
                dt_s = max(1e-6, float(now_mono - last_sync_ts))
                observed_velocity = (
                    (float(actual_mpos[0]) - float(prev_status_mpos[0])) / dt_s,
                    (float(actual_mpos[1]) - float(prev_status_mpos[1])) / dt_s,
                    (float(actual_mpos[2]) - float(prev_status_mpos[2])) / dt_s,
                )
                observed_speed = (
                    (observed_velocity[0] * observed_velocity[0])
                    + (observed_velocity[1] * observed_velocity[1])
                    + (observed_velocity[2] * observed_velocity[2])
                ) ** 0.5
                last_feed = abs(float(getattr(app, "_last_status_feed_raw", 0.0) or 0.0))
                freeze_interp = bool(
                    last_feed <= float(_JOG_DRO_ZERO_FEED_EPS)
                    or observed_speed <= float(_JOG_DRO_STATIONARY_DISTANCE_EPS)
                )
                prev_velocity = state.get("velocity_report_s")
                if freeze_interp:
                    next_velocity = (0.0, 0.0, 0.0)
                elif isinstance(prev_velocity, (list, tuple)) and len(prev_velocity) >= 3:
                    alpha = float(_JOG_DRO_OBSERVED_VELOCITY_ALPHA)
                    next_velocity = (
                        (float(prev_velocity[0]) * (1.0 - alpha)) + (float(observed_velocity[0]) * alpha),
                        (float(prev_velocity[1]) * (1.0 - alpha)) + (float(observed_velocity[1]) * alpha),
                        (float(prev_velocity[2]) * (1.0 - alpha)) + (float(observed_velocity[2]) * alpha),
                    )
                else:
                    next_velocity = (
                        float(observed_velocity[0]),
                        float(observed_velocity[1]),
                        float(observed_velocity[2]),
                    )
                state["velocity_report_s"] = next_velocity
                state["freeze"] = bool(freeze_interp)
                _record_jog_dro_trace(
                    app,
                    "velocity_sync",
                    observed_velocity=observed_velocity,
                    filtered_velocity=next_velocity,
                    observed_speed=float(observed_speed),
                    feed=float(getattr(app, "_last_status_feed_raw", 0.0) or 0.0),
                    freeze=bool(freeze_interp),
                )
            state["base_mpos_report"] = (
                float(actual_mpos[0]),
                float(actual_mpos[1]),
                float(actual_mpos[2]),
            )
            state["last_status_mpos_report"] = (
                float(actual_mpos[0]),
                float(actual_mpos[1]),
                float(actual_mpos[2]),
            )
            state["start_ts"] = float(time.monotonic())
            state["anchor_ts"] = float(time.monotonic())
        except Exception as exc:
            _log_suppressed("Failed syncing manual jog prediction anchor to status", exc)
    _schedule_manual_jog_prediction_tick(app)


def stop_manual_jog_prediction(app, *, reason: str = "stop") -> None:
    est_mpos = getattr(app, "_manual_jog_predict_last_est_mpos", None)
    actual_mpos = None
    mpos_raw = getattr(app, "_mpos_raw", None)
    if isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3:
        try:
            actual_mpos = (
                float(mpos_raw[0]),
                float(mpos_raw[1]),
                float(mpos_raw[2]),
            )
        except Exception:
            actual_mpos = None
    delta = None
    if isinstance(est_mpos, tuple) and len(est_mpos) >= 3 and isinstance(actual_mpos, tuple):
        try:
            delta = (
                float(actual_mpos[0]) - float(est_mpos[0]),
                float(actual_mpos[1]) - float(est_mpos[1]),
                float(actual_mpos[2]) - float(est_mpos[2]),
            )
        except Exception:
            delta = None
    _record_jog_dro_trace(
        app,
        "stop",
        reason=str(reason or "stop"),
        est_mpos=est_mpos if isinstance(est_mpos, tuple) and len(est_mpos) >= 3 else None,
        actual_mpos=actual_mpos,
        delta=delta,
    )
    _clear_manual_jog_prediction(app)
    _snap_dro_to_last_status_raw(app)
    _request_stop_status_refresh(app)


def _xyz_tuple_changed(
    previous: tuple[float, float, float] | None,
    current: tuple[float, float, float],
    *,
    deadband: float = 1e-9,
) -> bool:
    if not previous or len(previous) < 3:
        return True
    threshold = max(0.0, float(deadband))
    return (
        abs(previous[0] - current[0]) > threshold
        or abs(previous[1] - current[1]) > threshold
        or abs(previous[2] - current[2]) > threshold
    )


def _flash_wpos_labels(app) -> None:
    now = time.monotonic()
    last_flash = float(getattr(app, "_wpos_flash_last_ts", 0.0) or 0.0)
    if (now - last_flash) < _WPOS_FLASH_MIN_INTERVAL_S:
        return
    app._wpos_flash_last_ts = now
    labels = getattr(app, "_wpos_value_labels", None)
    if not labels:
        return
    for axis, label in labels.items():
        default_fg = ""
        try:
            default_fg = app._wpos_label_default_fg.get(axis, "")
        except Exception as exc:
            _log_suppressed("Failed reading default WPos label color", exc)
            default_fg = ""
        after_id = None
        try:
            after_id = app._wpos_flash_after_ids.get(axis)
        except Exception as exc:
            _log_suppressed("Failed reading pending WPos flash id", exc)
            after_id = None
        if after_id:
            try:
                app.after_cancel(after_id)
            except Exception as exc:
                _log_suppressed("Failed cancelling previous WPos flash timer", exc)
        try:
            label.configure(foreground="#2196f3")
        except Exception as exc:
            _log_suppressed("Failed applying WPos flash color", exc)
            continue

        def restore(target=label, axis_key=axis, fg=default_fg):
            try:
                if fg:
                    target.configure(foreground=fg)
                else:
                    target.configure(foreground="")
            except Exception as exc:
                _log_suppressed("Failed restoring WPos label color", exc)
            try:
                app._wpos_flash_after_ids[axis_key] = None
            except Exception as exc:
                _log_suppressed("Failed clearing WPos flash timer id", exc)

        try:
            app._wpos_flash_after_ids[axis] = app.after(150, restore)
        except Exception as exc:
            _log_suppressed("Failed scheduling WPos flash restore timer", exc)


def _update_positions_and_macro_state(
    app,
    fields: _StatusFields,
    *,
    event_started_perf: float | None = None,
    noncritical_budget_ms: float = _STATUS_NONCRITICAL_BUDGET_MS,
) -> None:
    """Update DROs, derived coordinates, and macro-visible status fields."""

    stream_busy_for_noncritical = _stream_active_or_finishing(app)
    reported_wco_vals = _parse_xyz_triplet(fields.wco) if fields.wco else None
    mpos_vals = _parse_xyz_triplet(fields.mpos) if fields.mpos else None
    reported_wpos_vals = _parse_xyz_triplet(fields.wpos) if fields.wpos else None
    wco_vals = list(reported_wco_vals) if reported_wco_vals else None
    wpos_vals = list(reported_wpos_vals) if reported_wpos_vals else None
    if wco_vals:
        app._wco_raw = tuple(wco_vals)
    else:
        # GRBL can omit WCO on some status frames; reuse the last confirmed WCO
        # so fresh MPos reports can still drive WPos/macro updates.
        cached_wco = getattr(app, "_wco_raw", None)
        if cached_wco and len(cached_wco) >= 3:
            wco_vals = [cached_wco[0], cached_wco[1], cached_wco[2]]

    report_units = getattr(app, "_report_units", None) or app.unit_mode.get()
    modal_units = app.unit_mode.get()
    report_scale = _unit_scale_cached(report_units)
    modal_scale = _unit_scale_cached(modal_units)
    to_mm_factor = report_scale
    to_modal_factor = report_scale / modal_scale
    pos_deadband = _position_deadband_report_units(report_units, modal_units)

    def _clear_zero_all_pending_latch() -> None:
        app._zero_all_pending_active = False
        app._zero_all_pending_expected_wco_raw = None
        app._zero_all_pending_until_ts = 0.0
        app._zero_all_pending_hard_until_ts = 0.0
        app._zero_all_pending_post_timeout_wco_seen = False

    zero_all_pending_active = bool(getattr(app, "_zero_all_pending_active", False))
    expected_wco = getattr(app, "_zero_all_pending_expected_wco_raw", None)
    expected_wco_tuple: tuple[float, float, float] | None = None
    if zero_all_pending_active:
        # Zero-all can briefly race with stale or missing WCO frames. Keep the
        # expected WCO latched until status reports confirm the new zero or the
        # guard window expires.
        expected_wco_raw = (
            expected_wco
            if isinstance(expected_wco, (list, tuple)) and len(expected_wco) >= 3
            else None
        )
        valid_expected = expected_wco_raw is not None
        if expected_wco_raw is not None:
            try:
                expected_wco_tuple = (
                    float(expected_wco_raw[0]),
                    float(expected_wco_raw[1]),
                    float(expected_wco_raw[2]),
                )
            except Exception:
                valid_expected = False
        if not valid_expected or expected_wco_tuple is None:
            _clear_zero_all_pending_latch()
            zero_all_pending_active = False
        else:
            expected_wco_live = expected_wco_tuple
            now_mono = time.monotonic()
            soft_until = float(getattr(app, "_zero_all_pending_until_ts", 0.0) or 0.0)
            hard_until = float(getattr(app, "_zero_all_pending_hard_until_ts", 0.0) or 0.0)
            soft_expired = soft_until > 0.0 and now_mono >= soft_until
            hard_expired = hard_until > 0.0 and now_mono >= hard_until
            zero_confirmed = False
            if reported_wpos_vals and len(reported_wpos_vals) >= 3:
                zero_confirmed = all(abs(float(val)) <= pos_deadband for val in reported_wpos_vals[:3])
            if not zero_confirmed and mpos_vals and reported_wco_vals:
                zero_confirmed = all(
                    abs(float(mpos_vals[idx]) - float(reported_wco_vals[idx])) <= pos_deadband
                    for idx in range(3)
                )
            if not zero_confirmed and reported_wco_vals:
                zero_confirmed = all(
                    abs(float(reported_wco_vals[idx]) - float(expected_wco_live[idx])) <= pos_deadband
                    for idx in range(3)
                )
            if hard_expired or zero_confirmed:
                _clear_zero_all_pending_latch()
                zero_all_pending_active = False
            else:
                # Keep WPos stable during zero-all settling even when WCO is
                # temporarily missing or stale in status reports.
                wco_vals = [
                    float(expected_wco_live[0]),
                    float(expected_wco_live[1]),
                    float(expected_wco_live[2]),
                ]
                app._wco_raw = (
                    float(expected_wco_live[0]),
                    float(expected_wco_live[1]),
                    float(expected_wco_live[2]),
                )
                if mpos_vals is not None:
                    wpos_vals = None
                else:
                    wpos_vals = [0.0, 0.0, 0.0]
                if soft_expired and reported_wco_vals:
                    if bool(getattr(app, "_zero_all_pending_post_timeout_wco_seen", False)):
                        _clear_zero_all_pending_latch()
                        zero_all_pending_active = False
                        wco_vals = list(reported_wco_vals)
                        wpos_vals = list(reported_wpos_vals) if reported_wpos_vals else None
                    else:
                        app._zero_all_pending_post_timeout_wco_seen = True

    def to_mm(value: float) -> float:
        return value * to_mm_factor

    def to_modal(value: float) -> float:
        return value * to_modal_factor

    def _status_event_elapsed_ms() -> float:
        if event_started_perf is None:
            return 0.0
        return max(0.0, (time.perf_counter() - float(event_started_perf)) * 1000.0)

    def _should_defer_noncritical_updates() -> bool:
        if stream_busy_for_noncritical or _performance_mode_enabled(app):
            return True
        return _status_event_elapsed_ms() >= max(1.0, float(noncritical_budget_ms))

    # Derive whichever position space GRBL omitted so both DROs and macro state
    # remain internally consistent from mixed MPos/WPos/WCO reports.
    macro_updates: dict[str, object] = {}
    wpos_calc = None
    mpos_calc = None
    if mpos_vals and wpos_vals is None and wco_vals:
        wpos_calc = [
            mpos_vals[0] - wco_vals[0],
            mpos_vals[1] - wco_vals[1],
            mpos_vals[2] - wco_vals[2],
        ]
    elif wpos_vals and mpos_vals is None and wco_vals:
        mpos_calc = [
            wpos_vals[0] + wco_vals[0],
            wpos_vals[1] + wco_vals[1],
            wpos_vals[2] + wco_vals[2],
        ]

    if mpos_vals:
        mpos_tuple = (mpos_vals[0], mpos_vals[1], mpos_vals[2])
        mpos_changed = _xyz_tuple_changed(
            getattr(app, "_mpos_raw", None),
            mpos_tuple,
            deadband=pos_deadband,
        )
        app._mpos_raw = mpos_tuple
        if mpos_changed:
            try:
                mpos_x = format_dro_value(mpos_vals[0], report_units, modal_units)
                mpos_y = format_dro_value(mpos_vals[1], report_units, modal_units)
                mpos_z = format_dro_value(mpos_vals[2], report_units, modal_units)
                _set_var_if_changed(app.mpos_x, mpos_x)
                _set_var_if_changed(app.mpos_y, mpos_y)
                _set_var_if_changed(app.mpos_z, mpos_z)
            except Exception as exc:
                _log_suppressed("Failed updating machine-position DRO values", exc)
        macro_updates["mx"] = to_modal(mpos_vals[0])
        macro_updates["my"] = to_modal(mpos_vals[1])
        macro_updates["mz"] = to_modal(mpos_vals[2])
    elif mpos_calc:
        mpos_calc_tuple = (mpos_calc[0], mpos_calc[1], mpos_calc[2])
        mpos_changed = _xyz_tuple_changed(
            getattr(app, "_mpos_raw", None),
            mpos_calc_tuple,
            deadband=pos_deadband,
        )
        app._mpos_raw = mpos_calc_tuple
        if mpos_changed:
            try:
                mpos_x = format_dro_value(mpos_calc[0], report_units, modal_units)
                mpos_y = format_dro_value(mpos_calc[1], report_units, modal_units)
                mpos_z = format_dro_value(mpos_calc[2], report_units, modal_units)
                _set_var_if_changed(app.mpos_x, mpos_x)
                _set_var_if_changed(app.mpos_y, mpos_y)
                _set_var_if_changed(app.mpos_z, mpos_z)
            except Exception as exc:
                _log_suppressed("Failed updating computed machine-position DRO values", exc)
        macro_updates["mx"] = to_modal(mpos_calc[0])
        macro_updates["my"] = to_modal(mpos_calc[1])
        macro_updates["mz"] = to_modal(mpos_calc[2])

    if wpos_vals:
        wpos_tuple = (wpos_vals[0], wpos_vals[1], wpos_vals[2])
        wpos_changed = _xyz_tuple_changed(
            getattr(app, "_wpos_raw", None),
            wpos_tuple,
            deadband=pos_deadband,
        )
        app._wpos_raw = wpos_tuple
        if wpos_changed:
            try:
                wpos_x = format_dro_value(wpos_vals[0], report_units, modal_units)
                wpos_y = format_dro_value(wpos_vals[1], report_units, modal_units)
                wpos_z = format_dro_value(wpos_vals[2], report_units, modal_units)
                _set_var_if_changed(app.wpos_x, wpos_x)
                _set_var_if_changed(app.wpos_y, wpos_y)
                _set_var_if_changed(app.wpos_z, wpos_z)
            except Exception as exc:
                _log_suppressed("Failed updating WPos DRO values", exc)
        macro_updates["wx"] = to_modal(wpos_vals[0])
        macro_updates["wy"] = to_modal(wpos_vals[1])
        macro_updates["wz"] = to_modal(wpos_vals[2])
        if wpos_changed and not stream_busy_for_noncritical:
            _flash_wpos_labels(app)
    elif wpos_calc:
        wpos_calc_tuple = (wpos_calc[0], wpos_calc[1], wpos_calc[2])
        wpos_changed = _xyz_tuple_changed(
            getattr(app, "_wpos_raw", None),
            wpos_calc_tuple,
            deadband=pos_deadband,
        )
        app._wpos_raw = wpos_calc_tuple
        if wpos_changed:
            try:
                wpos_x = format_dro_value(wpos_calc[0], report_units, modal_units)
                wpos_y = format_dro_value(wpos_calc[1], report_units, modal_units)
                wpos_z = format_dro_value(wpos_calc[2], report_units, modal_units)
                _set_var_if_changed(app.wpos_x, wpos_x)
                _set_var_if_changed(app.wpos_y, wpos_y)
                _set_var_if_changed(app.wpos_z, wpos_z)
            except Exception as exc:
                _log_suppressed("Failed updating computed WPos DRO values", exc)
        macro_updates["wx"] = to_modal(wpos_calc[0])
        macro_updates["wy"] = to_modal(wpos_calc[1])
        macro_updates["wz"] = to_modal(wpos_calc[2])

    if fields.feed is not None:
        macro_updates["curfeed"] = fields.feed
    if fields.spindle is not None:
        macro_updates["curspindle"] = fields.spindle
        try:
            rpm_text = str(int(round(float(fields.spindle))))
        except Exception as exc:
            _log_suppressed("Failed parsing spindle RPM from status line", exc)
            rpm_text = None
        if rpm_text is not None:
            mpos_rpm_var = getattr(app, "mpos_rpm", None)
            spindle_rpm_var = getattr(app, "spindle_current_rpm_var", None)

            def _apply_spindle_rpm_ui_sync() -> None:
                if mpos_rpm_var is not None:
                    _set_var_if_changed(mpos_rpm_var, rpm_text)
                if spindle_rpm_var is not None:
                    _set_var_if_changed(spindle_rpm_var, rpm_text)

            if _should_defer_noncritical_updates():
                _schedule_status_ui_callback(
                    app,
                    callback_attr="_status_spindle_rpm_after_id",
                    callback=_apply_spindle_rpm_ui_sync,
                    context="Failed applying deferred spindle-RPM UI sync from status",
                )
            else:
                try:
                    _apply_spindle_rpm_ui_sync()
                except Exception as exc:
                    _log_suppressed(
                        "Failed applying spindle-RPM UI sync from status",
                        exc,
                    )
    if fields.planner is not None:
        macro_updates["planner"] = fields.planner
        try:
            planner_available = int(fields.planner)
            if planner_available < 0:
                planner_available = 0
            planner_capacity = int(getattr(app, "_planner_blocks_capacity", 15) or 15)
            if planner_capacity <= 0:
                planner_capacity = 15
            if planner_available > planner_capacity:
                planner_capacity = planner_available
                app._planner_blocks_capacity = planner_capacity
            app._planner_blocks_available = min(planner_available, planner_capacity)
        except Exception as exc:
            _log_suppressed("Failed tracking planner availability from status line", exc)
    if fields.rxbytes is not None:
        macro_updates["rxbytes"] = fields.rxbytes
    if wco_vals:
        macro_updates["wcox"] = to_modal(wco_vals[0])
        macro_updates["wcoy"] = to_modal(wco_vals[1])
        macro_updates["wcoz"] = to_modal(wco_vals[2])
    if fields.pins is not None:
        macro_updates["pins"] = fields.pins
    # Override widgets are relatively expensive UI work, so they follow the
    # same noncritical deferral path used for other streaming-time sync.
    ov_values: tuple[int, int, int] | None = None
    if fields.ov:
        feed_val = spindle_val = None
        try:
            ov_parts = [int(float(v)) for v in fields.ov.split(",")]
            if len(ov_parts) >= 3:
                feed_val, spindle_val = ov_parts[0], ov_parts[2]
                ov_values = (ov_parts[0], ov_parts[1], ov_parts[2])
        except Exception as exc:
            _log_suppressed("Failed parsing override values from status line", exc)
        else:
            ov_changed = bool(ov_values is not None and ov_values != getattr(app, "_last_status_ov", None))
            if ov_values is not None:
                app._last_status_ov = ov_values
            if ov_changed:
                def _apply_override_ui_sync() -> None:
                    if feed_val is not None:
                        app._set_feed_override_slider_value(feed_val)
                    if spindle_val is not None:
                        app._set_spindle_override_slider_value(spindle_val)
                    app._refresh_override_info()

                if _should_defer_noncritical_updates():
                    _schedule_status_ui_callback(
                        app,
                        callback_attr="_status_override_sync_after_id",
                        callback=_apply_override_ui_sync,
                        context="Failed applying deferred override UI sync from status",
                    )
                else:
                    try:
                        _apply_override_ui_sync()
                    except Exception as exc:
                        _log_suppressed(
                            "Failed applying override UI sync from status",
                            exc,
                        )
    pin_state = {char for char in (fields.pins or "").upper() if char.isalpha()}
    endstop_active = bool(pin_state & {"X", "Y", "Z"})

    def _apply_macro_status_updates(macro_vars: dict) -> None:
        if ov_values is not None:
            changed = (
                macro_vars.get("OvFeed") != ov_values[0]
                or macro_vars.get("OvRapid") != ov_values[1]
                or macro_vars.get("OvSpindle") != ov_values[2]
            )
            macro_updates["OvFeed"] = ov_values[0]
            macro_updates["OvRapid"] = ov_values[1]
            macro_updates["OvSpindle"] = ov_values[2]
            macro_updates["_OvChanged"] = bool(changed)
        if macro_updates:
            macro_vars.update(macro_updates)

    _with_macro_vars_nonblocking(
        app,
        _apply_macro_status_updates,
        context="Failed updating macro status values",
    )
    # LED updates are visually helpful but noncritical during a busy stream, so
    # they can be deferred when status processing is already under pressure.
    probe_active = bool(pin_state & {"P"})
    hold_active = bool(pin_state & {"H"}) or "hold" in fields.state.lower()

    def _apply_led_panel_state() -> None:
        app._update_led_panel(endstop_active, probe_active, hold_active)

    if _should_defer_noncritical_updates() and stream_busy_for_noncritical:
        _schedule_status_ui_callback(
            app,
            callback_attr="_status_led_panel_after_id",
            callback=_apply_led_panel_state,
            context="Failed applying deferred LED panel state from status",
        )
    else:
        try:
            _apply_led_panel_state()
        except Exception as exc:
            _log_suppressed("Failed updating LED panel from status", exc)


def handle_status_event(app, raw: str):
    """Parse one status frame and apply state, position, and completion sync."""

    event_start = time.perf_counter()
    parse_elapsed_ms = 0.0
    apply_elapsed_ms = 0.0
    positions_elapsed_ms = 0.0
    deferred_elapsed_ms = 0.0
    settling = _status_settling_active(app)
    _signal_thread_event(app, "_status_update_event")
    now_ts = time.time()
    app._last_status_ts = now_ts
    previous_raw = str(getattr(app, "_last_status_raw", "") or "")
    app._last_status_raw = raw
    state_token = _status_state_token(raw)
    state_lower = str(state_token or "").strip().lower()
    live_updates_during_settling = bool(
        settling and state_lower.startswith(("jog", "run", "hold"))
    )
    if state_token and not str(state_token).lower().startswith("idle"):
        try:
            app._status_last_non_idle_ts = time.monotonic()
        except Exception as exc:
            _log_suppressed("Failed tracking last non-idle status timestamp", exc)
    # Short-circuit exact duplicates first, then a relaxed idle signature that
    # tolerates benign idle-only churn outside active streaming.
    if raw == previous_raw:
        app._status_seen = True
        app._status_duplicate_count = int(getattr(app, "_status_duplicate_count", 0) or 0) + 1
        if state_token:
            _sync_deferred_stream_completion(app, state_token)
        _record_status_perf_metric(
            app,
            "duplicate_short_circuit",
            (time.perf_counter() - event_start) * 1000.0,
        )
        _record_status_perf_metric(
            app,
            "total",
            (time.perf_counter() - event_start) * 1000.0,
        )
        return
    if (
        state_lower.startswith("idle")
        and not _stream_active_or_finishing(app)
        and _status_relaxed_idle_signature(raw)
        == _status_relaxed_idle_signature(previous_raw)
    ):
        app._status_seen = True
        app._status_duplicate_count = int(
            getattr(app, "_status_duplicate_count", 0) or 0
        ) + 1
        _sync_deferred_stream_completion(app, state_token or "Idle")
        _record_status_perf_metric(
            app,
            "duplicate_short_circuit_relaxed",
            (time.perf_counter() - event_start) * 1000.0,
        )
        _record_status_perf_metric(
            app,
            "total",
            (time.perf_counter() - event_start) * 1000.0,
        )
        return
    if not _status_apply_interval_ok(app, state_token):
        _record_status_perf_metric(
            app,
            "settling_coalesced",
            (time.perf_counter() - event_start) * 1000.0,
        )
        _record_status_perf_metric(
            app,
            "total",
            (time.perf_counter() - event_start) * 1000.0,
        )
        return
    app._status_duplicate_count = 0
    history = getattr(app, "_status_history", None)
    if not isinstance(history, deque):
        seed: list[tuple[float, str]] = []
        if isinstance(history, list):
            seed = history[-200:]
        history = deque(seed, maxlen=200)
        app._status_history = history
    history.append((now_ts, raw))
    # Parse and apply the machine-state portion before touching heavier DRO and
    # macro updates.
    parse_start = time.perf_counter()
    fields = _parse_status_fields(raw)
    if fields.feed is not None:
        try:
            app._last_status_feed_raw = float(fields.feed)
        except Exception:
            pass
    parse_elapsed_ms = (time.perf_counter() - parse_start) * 1000.0
    _record_status_perf_metric(app, "parse", parse_elapsed_ms)
    app._status_seen = True
    app._last_status_pins = fields.pins
    display_state = _resolve_display_state(app, fields.state)
    apply_start = time.perf_counter()
    if settling and not live_updates_during_settling:
        _apply_machine_state_minimal(app, fields.state, display_state)
    else:
        if not _apply_machine_state(app, fields.state, display_state):
            apply_elapsed_ms = (time.perf_counter() - apply_start) * 1000.0
            _record_status_perf_metric(
                app,
                "apply_state",
                apply_elapsed_ms,
            )
            total_ms = (time.perf_counter() - event_start) * 1000.0
            _record_status_perf_metric(app, "total", total_ms)
            _log_slow_status_event(
                app,
                total_ms=total_ms,
                state=fields.state,
                parse_ms=parse_elapsed_ms,
                apply_ms=apply_elapsed_ms,
                positions_ms=positions_elapsed_ms,
                deferred_ms=deferred_elapsed_ms,
                settling=settling,
            )
            return
    apply_elapsed_ms = (time.perf_counter() - apply_start) * 1000.0
    _record_status_perf_metric(app, "apply_state", apply_elapsed_ms)
    if settling and not live_updates_during_settling:
        _record_status_perf_metric(
            app,
            "positions_macro",
            0.0,
        )
    else:
        # Position work can be deferred or coalesced while streaming so status
        # handling does not outrun the UI queue.
        update_start = time.perf_counter()
        force_defer_positions = _status_positions_should_force_defer(
            app,
            event_started_perf=event_start,
            noncritical_budget_ms=_STATUS_NONCRITICAL_BUDGET_MS,
        )
        if _queue_coalesced_status_positions_update(
            app,
            fields,
            force_defer=force_defer_positions,
        ):
            pass
        else:
            if not _stream_status_positions_coalesce_active(app):
                _clear_coalesced_status_positions_state(app)
            _update_positions_and_macro_state(
                app,
                fields,
                event_started_perf=event_start,
                noncritical_budget_ms=_STATUS_NONCRITICAL_BUDGET_MS,
            )
            sync_manual_jog_prediction_with_status(app)
            setattr(app, "_status_positions_last_apply_ts", float(time.monotonic()))
        positions_elapsed_ms = (time.perf_counter() - update_start) * 1000.0
        _record_status_perf_metric(
            app,
            "positions_macro",
            positions_elapsed_ms,
        )
    # Completion sync stays last so it observes the latest state/position work
    # from the current frame before deciding whether a run is actually done.
    finalize_start = time.perf_counter()
    _sync_deferred_stream_completion(app, fields.state)
    deferred_elapsed_ms = (time.perf_counter() - finalize_start) * 1000.0
    _record_status_perf_metric(
        app,
        "deferred_completion",
        deferred_elapsed_ms,
    )
    total_ms = (time.perf_counter() - event_start) * 1000.0
    _record_status_perf_metric(app, "total", total_ms)
    _log_slow_status_event(
        app,
        total_ms=total_ms,
        state=fields.state,
        parse_ms=parse_elapsed_ms,
        apply_ms=apply_elapsed_ms,
        positions_ms=positions_elapsed_ms,
        deferred_ms=deferred_elapsed_ms,
        settling=settling,
    )


