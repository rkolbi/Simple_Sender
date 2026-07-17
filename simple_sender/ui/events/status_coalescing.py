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

from dataclasses import dataclass
from typing import Any, cast

from simple_sender.status_coordinates import StatusCoordinateEvidence

from .status_parsing import StatusCoordinateApplication


@dataclass(frozen=True, slots=True)
class CoalescedStatusCoordinateUpdate:
    """Identity-bound coordinate evidence awaiting deferred installation."""

    request_id: int
    fields: Any
    coordinate_evidence: StatusCoordinateEvidence
    coordinate_signature: object
    connection_generation: int | None
    recovery_epoch: int | None
    event_generation: int | None
    event_recovery_epoch: int | None
    worker_identity: int | None
    serial_port: object | None
    serial_identity: int | None
    normal_session_required: bool
    normal_session_identity: object | None
    recovery_required: bool
    connected: bool
    closing: bool


def stream_status_positions_coalesce_active(app) -> bool:
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state not in {
        "running",
        "pause_requested",
        "paused",
        "external_hold",
        "door_suspended",
        "resume_requested",
    }:
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
    setattr(app, "_status_positions_coalesce_current_id", None)


def _safe_int_call(obj: object, attr_name: str) -> int | None:
    getter = getattr(obj, attr_name, None)
    if not callable(getter):
        return None
    try:
        return int(getter())
    except Exception:
        return None


def _safe_bool_call(obj: object, attr_name: str, *, default: bool) -> bool:
    getter = getattr(obj, attr_name, None)
    if not callable(getter):
        return bool(default)
    try:
        return bool(getter())
    except Exception:
        return True


def _safe_normal_session_identity(worker: object) -> object | None:
    getter = getattr(worker, "normal_session_action_identity", None)
    if not callable(getter):
        return None
    try:
        return cast(object, getter())
    except Exception:
        return object()


def _worker_session_current(
    worker: object,
    *,
    generation: int | None,
    serial_port: object | None,
) -> bool:
    checker = getattr(worker, "_session_is_current", None)
    if callable(checker) and generation is not None:
        try:
            return bool(checker(int(generation), serial_port))
        except Exception:
            return False
    getter = getattr(worker, "connection_generation", None)
    if callable(getter) and generation is not None:
        try:
            if int(getter()) != int(generation):
                return False
        except Exception:
            return False
    if serial_port is not None:
        try:
            if getattr(worker, "ser", None) is not serial_port:
                return False
        except Exception:
            return False
    return True


def _coalesced_update_current(app, update: CoalescedStatusCoordinateUpdate) -> bool:
    if bool(getattr(app, "_closing", False)) or bool(
        getattr(app, "_shutdown_in_progress", False)
    ):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    if not bool(update.connected) or bool(update.closing):
        return False
    worker = getattr(app, "grbl", None)
    if worker is None:
        if update.worker_identity is not None:
            return False
    elif update.worker_identity is not None and id(worker) != int(
        update.worker_identity
    ):
        return False
    current_generation = _safe_int_call(worker, "connection_generation")
    if update.connection_generation is not None and current_generation != int(
        update.connection_generation
    ):
        return False
    if update.event_generation is not None and current_generation != int(
        update.event_generation
    ):
        return False
    current_recovery_epoch = _safe_int_call(worker, "recovery_epoch")
    if update.recovery_epoch is not None and current_recovery_epoch != int(
        update.recovery_epoch
    ):
        return False
    if update.event_recovery_epoch is not None and current_recovery_epoch != int(
        update.event_recovery_epoch
    ):
        return False
    if update.serial_identity is not None:
        current_serial = getattr(worker, "ser", None)
        if current_serial is not update.serial_port:
            return False
        if id(current_serial) != int(update.serial_identity):
            return False
    if not _worker_session_current(
        worker,
        generation=update.connection_generation,
        serial_port=update.serial_port,
    ):
        return False
    if _safe_bool_call(worker, "recovery_required", default=False):
        return False
    if _safe_int_call(worker, "recovery_epoch") != update.recovery_epoch:
        return False
    normal_required = _safe_bool_call(
        worker,
        "normal_session_initialization_required",
        default=bool(update.normal_session_required),
    )
    if normal_required != bool(update.normal_session_required):
        return False
    current_normal_identity = _safe_normal_session_identity(worker)
    if update.normal_session_identity is not None and (
        current_normal_identity != update.normal_session_identity
    ):
        return False
    return True


def _capture_coalesced_status_coordinate_update(
    app,
    fields,
    *,
    request_id: int,
    clone_status_fields,
) -> CoalescedStatusCoordinateUpdate | None:
    evidence = getattr(fields, "coordinate_evidence", None)
    signature = getattr(fields, "coordinate_signature", None)
    if not isinstance(evidence, StatusCoordinateEvidence):
        return None
    if not bool(evidence.valid) or signature is None:
        return None
    worker = getattr(app, "grbl", None)
    connection_generation = _safe_int_call(worker, "connection_generation")
    recovery_epoch = _safe_int_call(worker, "recovery_epoch")
    event_generation = getattr(fields, "event_generation", None)
    event_recovery_epoch = getattr(fields, "event_recovery_epoch", None)
    try:
        event_generation = None if event_generation is None else int(event_generation)
    except Exception:
        return None
    try:
        event_recovery_epoch = (
            None if event_recovery_epoch is None else int(event_recovery_epoch)
        )
    except Exception:
        return None
    if event_generation is not None and connection_generation != event_generation:
        return None
    if event_recovery_epoch is not None and recovery_epoch != event_recovery_epoch:
        return None
    serial_port = getattr(worker, "ser", None)
    serial_identity = id(serial_port) if serial_port is not None else None
    update = CoalescedStatusCoordinateUpdate(
        request_id=int(request_id),
        fields=clone_status_fields(fields),
        coordinate_evidence=evidence,
        coordinate_signature=signature,
        connection_generation=connection_generation,
        recovery_epoch=recovery_epoch,
        event_generation=event_generation,
        event_recovery_epoch=event_recovery_epoch,
        worker_identity=id(worker) if worker is not None else None,
        serial_port=serial_port,
        serial_identity=serial_identity,
        normal_session_required=_safe_bool_call(
            worker,
            "normal_session_initialization_required",
            default=False,
        ),
        normal_session_identity=_safe_normal_session_identity(worker),
        recovery_required=_safe_bool_call(worker, "recovery_required", default=False),
        connected=bool(getattr(app, "connected", False)),
        closing=bool(getattr(app, "_closing", False))
        or bool(getattr(app, "_shutdown_in_progress", False)),
    )
    if bool(update.recovery_required):
        return None
    if not _coalesced_update_current(app, update):
        return None
    return update


def flush_coalesced_status_positions(
    app,
    *,
    time_module,
    status_fields_type,
    update_positions_and_macro_state,
    sync_manual_jog_prediction_with_status,
    record_status_perf_metric,
    noncritical_budget_ms: float,
    request_id: int | None = None,
) -> None:
    current_request_id = getattr(app, "_status_positions_coalesce_current_id", None)
    if request_id is not None and current_request_id != int(request_id):
        return
    pending = getattr(app, "_status_positions_coalesce_pending_fields", None)
    if not isinstance(pending, CoalescedStatusCoordinateUpdate):
        if request_id is None or current_request_id == request_id:
            setattr(app, "_status_positions_coalesce_after_id", None)
            setattr(app, "_status_positions_coalesce_pending_fields", None)
            setattr(app, "_status_positions_coalesce_current_id", None)
        return
    if request_id is not None and int(pending.request_id) != int(request_id):
        return
    if pending is not getattr(app, "_status_positions_coalesce_pending_fields", None):
        return
    if not _coalesced_update_current(app, pending):
        if pending is getattr(app, "_status_positions_coalesce_pending_fields", None):
            setattr(app, "_status_positions_coalesce_after_id", None)
            setattr(app, "_status_positions_coalesce_pending_fields", None)
            setattr(app, "_status_positions_coalesce_current_id", None)
        return
    fields = pending.fields
    if not isinstance(fields, status_fields_type):
        if pending is getattr(app, "_status_positions_coalesce_pending_fields", None):
            setattr(app, "_status_positions_coalesce_after_id", None)
            setattr(app, "_status_positions_coalesce_pending_fields", None)
            setattr(app, "_status_positions_coalesce_current_id", None)
        return
    setattr(app, "_status_positions_coalesce_after_id", None)
    setattr(app, "_status_positions_coalesce_pending_fields", None)
    setattr(app, "_status_positions_coalesce_current_id", None)
    started = time_module.perf_counter()
    try:
        coordinate_application = update_positions_and_macro_state(
            app,
            fields,
            event_started_perf=None,
            noncritical_budget_ms=noncritical_budget_ms,
        )
        if coordinate_application is StatusCoordinateApplication.VALID_INSTALLED:
            sync_manual_jog_prediction_with_status(app)
            setattr(
                app,
                "_status_positions_last_apply_ts",
                float(time_module.monotonic()),
            )
    finally:
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

    request_id = int(getattr(app, "_status_positions_coalesce_sequence", 0) or 0) + 1
    setattr(app, "_status_positions_coalesce_sequence", request_id)
    update = _capture_coalesced_status_coordinate_update(
        app,
        fields,
        request_id=request_id,
        clone_status_fields=clone_status_fields,
    )
    if update is None:
        return False
    if pending_after_id is not None:
        after_cancel = getattr(app, "after_cancel", None)
        if callable(after_cancel):
            try:
                after_cancel(pending_after_id)
            except Exception as exc:
                log_suppressed("Failed replacing coalesced status-positions callback", exc)

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
        return False

    try:
        callback_id = after_fn(
            delay_ms,
            lambda request_id=request_id: flush_coalesced_status_positions(
                app,
                request_id=request_id,
            ),
        )
    except Exception as exc:
        log_suppressed("Failed scheduling coalesced status-positions callback", exc)
        return False
    setattr(app, "_status_positions_coalesce_pending_fields", update)
    setattr(app, "_status_positions_coalesce_current_id", request_id)
    setattr(app, "_status_positions_coalesce_after_id", callback_id)
    record_status_perf_metric(app, "positions_coalesced_defer", 0.0)
    if force_defer:
        record_status_perf_metric(app, "positions_coalesced_pressure_defer", 0.0)
    return True
