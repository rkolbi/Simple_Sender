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
from simple_sender.utils.log_suppressed import log_suppressed_exception
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
from .status_completion import deferred_completion_bytes_done as _deferred_completion_bytes_done_impl
from .status_completion import deferred_completion_target_total as _deferred_completion_target_total_impl
from .status_completion import sync_deferred_stream_completion as _sync_deferred_stream_completion_impl
from .status_coalescing import clear_coalesced_status_positions_state as _clear_coalesced_status_positions_state_impl
from .status_coalescing import flush_coalesced_status_positions as _flush_coalesced_status_positions_impl
from .status_coalescing import mark_status_positions_pressure as _mark_status_positions_pressure_impl
from .status_coalescing import queue_coalesced_status_positions_update as _queue_coalesced_status_positions_update_impl
from .status_coalescing import status_positions_coalesce_interval_s as _status_positions_coalesce_interval_s_impl
from .status_coalescing import status_positions_pressure_active as _status_positions_pressure_active_impl
from .status_coalescing import status_positions_should_force_defer as _status_positions_should_force_defer_impl
from .status_coalescing import status_ui_queue_pending_depth as _status_ui_queue_pending_depth_impl
from .status_coalescing import stream_status_positions_coalesce_active as _stream_status_positions_coalesce_active_impl
from .status_motion import flash_wpos_labels as _flash_wpos_labels_impl
from .status_motion import run_manual_jog_prediction_tick as _run_manual_jog_prediction_tick_impl
from .status_motion import start_manual_jog_prediction as _start_manual_jog_prediction_impl
from .status_motion import stop_manual_jog_prediction as _stop_manual_jog_prediction_impl
from .status_motion import sync_manual_jog_prediction_with_status as _sync_manual_jog_prediction_with_status_impl
from .status_motion import update_positions_and_macro_state as _update_positions_and_macro_state_impl
from .status_motion import xyz_tuple_changed as _xyz_tuple_changed_impl
from .status_orchestration import apply_machine_state as _apply_machine_state_impl
from .status_orchestration import apply_machine_state_minimal as _apply_machine_state_minimal_impl
from .status_orchestration import handle_status_event as _handle_status_event_impl
from .status_reactions import maybe_restore_pending_g90 as _maybe_restore_pending_g90_impl
from .status_reactions import schedule_request_modal_state_sync as _schedule_request_modal_state_sync_impl
from .status_reactions import schedule_request_settings_dump as _schedule_request_settings_dump_impl
from .status_units import _parse_modal_units as _parse_modal_units_impl
from .status_units import _parse_report_units_setting as _parse_report_units_setting_impl
from .streaming import _set_stream_progress_ui
from .stream_state_ui import apply_stream_busy_state, restore_controls_after_stream
from simple_sender.ui.modal_sync import modal_sync_retry_ready, request_modal_state_sync

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_warned_unexpected: set[tuple[str, str]] = set()
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
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _log_unexpected_status_warning(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _warned_unexpected:
        return
    _warned_unexpected.add(key)
    logger.warning("%s: %s", context, exc, exc_info=exc)


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
            context_text = f"Failed scheduling deferred status callback: {callback_attr}"
            if bool(getattr(app, "_closing", False)):
                _log_suppressed(context_text, exc)
            else:
                _log_unexpected_status_warning(
                    f"{context_text}; running inline fallback",
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


def _mark_status_coordinates_fresh(app, *, context: str) -> None:
    def _update_macro_status_coordinates_seq(macro_vars: dict) -> None:
        macro_vars["_status_coords_seq"] = int(
            macro_vars.get("_status_coords_seq", 0) or 0
        ) + 1

    _with_macro_vars_nonblocking(
        app,
        _update_macro_status_coordinates_seq,
        context=context,
    )
    _signal_thread_event(app, "_status_coords_update_event")


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


def _homing_status_resolution_pending(app) -> bool:
    # Repeated status frames remain meaningful while homing is latched because
    # `_resolve_display_state()` may need a later Idle/Alarm/other status to
    # clear the temporary Homing state truthfully.
    return bool(getattr(app, "_homing_in_progress", False))


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
    _apply_machine_state_minimal_impl(
        app,
        state,
        display_state,
        status_allows_alarm_clear=_status_allows_alarm_clear,
        stream_latched_banner_state=_stream_latched_banner_state,
        render_machine_state_text=_render_machine_state_text,
        apply_machine_state_visuals_deferred_highlight=_apply_machine_state_visuals_deferred_highlight,
        with_macro_vars_nonblocking=_with_macro_vars_nonblocking,
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
    _schedule_request_settings_dump_impl(app, log_suppressed=_log_suppressed)


def _schedule_request_modal_state_sync(app) -> None:
    _schedule_request_modal_state_sync_impl(
        app,
        log_suppressed=_log_suppressed,
        request_modal_state_sync=request_modal_state_sync,
    )


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
    _maybe_restore_pending_g90_impl(
        app,
        log_suppressed=_log_suppressed,
        stream_active_or_finishing=_stream_active_or_finishing,
    )


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
    return bool(_stream_status_positions_coalesce_active_impl(app))


def _status_positions_pressure_active(app, *, now_mono: float | None = None) -> bool:
    return bool(
        _status_positions_pressure_active_impl(
            app,
            time_module=time,
            now_mono=now_mono,
        )
    )


def _mark_status_positions_pressure(app, *, now_mono: float | None = None) -> None:
    _mark_status_positions_pressure_impl(
        app,
        time_module=time,
        default_recovery_s=_STATUS_STREAM_POSITION_COALESCE_PRESSURE_RECOVERY_S,
        now_mono=now_mono,
    )


def _status_ui_queue_pending_depth(app) -> int:
    return int(_status_ui_queue_pending_depth_impl(app))


def _status_positions_should_force_defer(
    app,
    *,
    event_started_perf: float | None,
    noncritical_budget_ms: float,
) -> bool:
    return bool(
        _status_positions_should_force_defer_impl(
            app,
            event_started_perf=event_started_perf,
            noncritical_budget_ms=noncritical_budget_ms,
            time_module=time,
            stream_status_positions_coalesce_active=_stream_status_positions_coalesce_active,
            status_ui_queue_pending_depth=_status_ui_queue_pending_depth,
            mark_status_positions_pressure=_mark_status_positions_pressure,
            status_positions_pressure_active=_status_positions_pressure_active,
            record_status_perf_metric=_record_status_perf_metric,
            default_pending_threshold=_STATUS_STREAM_POSITION_COALESCE_PRESSURE_PENDING_THRESHOLD,
        )
    )


def _status_positions_coalesce_interval_s(app) -> float:
    return float(
        _status_positions_coalesce_interval_s_impl(
            app,
            status_positions_pressure_active=_status_positions_pressure_active,
            default_ms=_STATUS_STREAM_POSITION_COALESCE_DEFAULT_MS,
            min_ms=_STATUS_STREAM_POSITION_COALESCE_MIN_MS,
            max_ms=_STATUS_STREAM_POSITION_COALESCE_MAX_MS,
            pressure_ms_default=_STATUS_STREAM_POSITION_COALESCE_PRESSURE_MS,
        )
    )


def _clear_coalesced_status_positions_state(app) -> None:
    _clear_coalesced_status_positions_state_impl(
        app,
        log_suppressed=_log_suppressed,
    )


def _flush_coalesced_status_positions(app) -> None:
    _flush_coalesced_status_positions_impl(
        app,
        time_module=time,
        status_fields_type=_StatusFields,
        update_positions_and_macro_state=_update_positions_and_macro_state,
        sync_manual_jog_prediction_with_status=sync_manual_jog_prediction_with_status,
        record_status_perf_metric=_record_status_perf_metric,
        noncritical_budget_ms=_STATUS_NONCRITICAL_BUDGET_MS,
    )


def _queue_coalesced_status_positions_update(
    app,
    fields: _StatusFields,
    *,
    force_defer: bool = False,
) -> bool:
    return bool(
        _queue_coalesced_status_positions_update_impl(
            app,
            fields,
            force_defer=force_defer,
            time_module=time,
            clone_status_fields=_clone_status_fields,
            stream_status_positions_coalesce_active=_stream_status_positions_coalesce_active,
            status_positions_coalesce_interval_s=_status_positions_coalesce_interval_s,
            flush_coalesced_status_positions=_flush_coalesced_status_positions,
            log_suppressed=_log_suppressed,
            record_status_perf_metric=_record_status_perf_metric,
            default_pressure_min_defer_ms=_STATUS_STREAM_POSITION_COALESCE_PRESSURE_MIN_DEFER_MS,
        )
    )


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


def _apply_machine_state_visuals_deferred_highlight(
    app,
    *,
    rendered_state: str,
    banner_state: str,
    width_context: str,
    highlight_context: str,
) -> None:
    rendered_changed = _set_var_if_changed(app.machine_state, rendered_state)
    if rendered_changed:
        try:
            app._ensure_state_label_width(rendered_state)
        except Exception as exc:
            _log_suppressed(width_context, exc)
    highlight_key = _machine_state_highlight_key(banner_state)
    previous_highlight_key = str(getattr(app, "_machine_state_highlight_key", "") or "")
    if highlight_key == previous_highlight_key:
        return
    setattr(app, "_machine_state_highlight_key", highlight_key)

    def _apply_state_highlight() -> None:
        app._update_state_highlight(banner_state)

    _schedule_status_ui_callback(
        app,
        callback_attr="_status_machine_state_highlight_after_id",
        callback=_apply_state_highlight,
        context=highlight_context,
    )


def _apply_machine_state(app, state: str, display_state: str) -> bool:
    return bool(
        _apply_machine_state_impl(
            app,
            state,
            display_state,
            status_connect_settling_active=_status_connect_settling_active,
            status_allows_alarm_clear=_status_allows_alarm_clear,
            stream_latched_banner_state=_stream_latched_banner_state,
            render_machine_state_text=_render_machine_state_text,
            apply_machine_state_visuals_deferred_highlight=_apply_machine_state_visuals_deferred_highlight,
            apply_machine_state_visuals=_apply_machine_state_visuals,
            maybe_restore_pending_g90=_maybe_restore_pending_g90,
            modal_sync_retry_ready=modal_sync_retry_ready,
            stream_active_or_finishing=_stream_active_or_finishing,
            schedule_request_settings_dump=_schedule_request_settings_dump,
            schedule_request_modal_state_sync=_schedule_request_modal_state_sync,
            job_controls_ready=job_controls_ready,
            set_run_resume_from=set_run_resume_from,
            schedule_status_ui_callback=_schedule_status_ui_callback,
            with_macro_vars_nonblocking=_with_macro_vars_nonblocking,
            signal_thread_event=_signal_thread_event,
        )
    )


def _deferred_completion_target_total(app) -> int:
    return int(_deferred_completion_target_total_impl(app))


def _deferred_completion_bytes_done(app) -> bool:
    return bool(_deferred_completion_bytes_done_impl(app))


def _sync_deferred_stream_completion(app, state: str) -> None:
    _sync_deferred_stream_completion_impl(
        app,
        state,
        time_module=time,
        deferred_completion_target_total=_deferred_completion_target_total,
        deferred_completion_bytes_done=_deferred_completion_bytes_done,
        begin_deferred_completion_wait=begin_deferred_completion_wait,
        end_deferred_completion_wait=end_deferred_completion_wait,
        set_stream_progress_ui=_set_stream_progress_ui,
        log_suppressed=_log_suppressed,
        restore_controls_after_stream=restore_controls_after_stream,
        apply_stream_busy_state=apply_stream_busy_state,
        job_controls_ready=job_controls_ready,
        set_run_resume_from=set_run_resume_from,
    )


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
    _run_manual_jog_prediction_tick_impl(
        app,
        time_module=time,
        log_suppressed=_log_suppressed,
        jog_prediction_should_run=_jog_prediction_should_run,
        clear_manual_jog_prediction=_clear_manual_jog_prediction,
        manual_jog_prediction_horizon_s=_manual_jog_prediction_horizon_s,
        interp_unsynced_max_dt_s=_JOG_DRO_INTERP_UNSYNCED_MAX_DT_S,
        format_dro_value_hook=format_dro_value,
        set_var_if_changed=_set_var_if_changed,
        record_jog_dro_trace=_record_jog_dro_trace,
        schedule_manual_jog_prediction_tick=_schedule_manual_jog_prediction_tick,
    )


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
    _start_manual_jog_prediction_impl(
        app,
        dx=dx,
        dy=dy,
        dz=dz,
        feed=feed,
        unit_mode=unit_mode,
        source=source,
        time_module=time,
        normalized_jog_prediction_source=_normalized_jog_prediction_source,
        jog_prediction_enabled_for_source=_jog_prediction_enabled_for_source,
        clear_manual_jog_prediction=_clear_manual_jog_prediction,
        units_ratio=_units_ratio,
        record_jog_dro_trace=_record_jog_dro_trace,
        manual_jog_prediction_horizon_s=_manual_jog_prediction_horizon_s,
        schedule_manual_jog_prediction_tick=_schedule_manual_jog_prediction_tick,
    )


def sync_manual_jog_prediction_with_status(app) -> None:
    _sync_manual_jog_prediction_with_status_impl(
        app,
        time_module=time,
        log_suppressed=_log_suppressed,
        jog_prediction_should_run=_jog_prediction_should_run,
        clear_manual_jog_prediction=_clear_manual_jog_prediction,
        manual_jog_prediction_horizon_s=_manual_jog_prediction_horizon_s,
        record_jog_dro_delta_stats=_record_jog_dro_delta_stats,
        record_jog_dro_trace=_record_jog_dro_trace,
        schedule_manual_jog_prediction_tick=_schedule_manual_jog_prediction_tick,
        observed_velocity_alpha=_JOG_DRO_OBSERVED_VELOCITY_ALPHA,
        zero_feed_eps=_JOG_DRO_ZERO_FEED_EPS,
        stationary_distance_eps=_JOG_DRO_STATIONARY_DISTANCE_EPS,
    )


def stop_manual_jog_prediction(app, *, reason: str = "stop") -> None:
    _stop_manual_jog_prediction_impl(
        app,
        reason=reason,
        record_jog_dro_trace=_record_jog_dro_trace,
        clear_manual_jog_prediction=_clear_manual_jog_prediction,
        snap_dro_to_last_status_raw=_snap_dro_to_last_status_raw,
        request_stop_status_refresh=_request_stop_status_refresh,
    )


def _xyz_tuple_changed(
    previous: tuple[float, float, float] | None,
    current: tuple[float, float, float],
    *,
    deadband: float = 1e-9,
) -> bool:
    return bool(
        _xyz_tuple_changed_impl(
            previous,
            current,
            deadband=deadband,
        )
    )


def _flash_wpos_labels(app) -> None:
    _flash_wpos_labels_impl(
        app,
        time_module=time,
        flash_min_interval_s=_WPOS_FLASH_MIN_INTERVAL_S,
        log_suppressed=_log_suppressed,
    )


def _update_positions_and_macro_state(
    app,
    fields: _StatusFields,
    *,
    event_started_perf: float | None = None,
    noncritical_budget_ms: float = _STATUS_NONCRITICAL_BUDGET_MS,
) -> None:
    _update_positions_and_macro_state_impl(
        app,
        fields,
        event_started_perf=event_started_perf,
        noncritical_budget_ms=noncritical_budget_ms,
        time_module=time,
        log_suppressed=_log_suppressed,
        stream_active_or_finishing=_stream_active_or_finishing,
        parse_xyz_triplet=_parse_xyz_triplet,
        unit_scale_cached=_unit_scale_cached,
        position_deadband_report_units=_position_deadband_report_units,
        xyz_tuple_changed=_xyz_tuple_changed,
        format_dro_value_hook=format_dro_value,
        set_var_if_changed=_set_var_if_changed,
        performance_mode_enabled=_performance_mode_enabled,
        schedule_status_ui_callback=_schedule_status_ui_callback,
        with_macro_vars_nonblocking=_with_macro_vars_nonblocking,
        mark_status_coordinates_fresh=_mark_status_coordinates_fresh,
        flash_wpos_labels=_flash_wpos_labels,
    )


def handle_status_event(app, raw: str):
    """Parse one status frame and apply state, position, and completion sync."""

    _handle_status_event_impl(
        app,
        raw,
        time_module=time,
        deque_cls=deque,
        settling_active=_status_settling_active,
        signal_thread_event=_signal_thread_event,
        status_state_token=_status_state_token,
        homing_status_resolution_pending=_homing_status_resolution_pending,
        stream_active_or_finishing=_stream_active_or_finishing,
        mark_status_coordinates_fresh=_mark_status_coordinates_fresh,
        record_status_perf_metric=_record_status_perf_metric,
        sync_deferred_stream_completion=_sync_deferred_stream_completion,
        status_relaxed_idle_signature=_status_relaxed_idle_signature,
        status_apply_interval_ok=_status_apply_interval_ok,
        parse_status_fields=_parse_status_fields,
        resolve_display_state=_resolve_display_state,
        apply_machine_state_minimal=_apply_machine_state_minimal,
        apply_machine_state=_apply_machine_state,
        status_positions_should_force_defer=_status_positions_should_force_defer,
        queue_coalesced_status_positions_update=_queue_coalesced_status_positions_update,
        stream_status_positions_coalesce_active=_stream_status_positions_coalesce_active,
        clear_coalesced_status_positions_state=_clear_coalesced_status_positions_state,
        update_positions_and_macro_state=_update_positions_and_macro_state,
        sync_manual_jog_prediction_with_status=sync_manual_jog_prediction_with_status,
        log_slow_status_event=_log_slow_status_event,
        noncritical_budget_ms=_STATUS_NONCRITICAL_BUDGET_MS,
        log_suppressed=_log_suppressed,
    )



