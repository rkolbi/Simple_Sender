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

"""Top-level status-event sequencing and machine-state application helpers."""

from simple_sender.types import NormalSessionPhase

from .status_parsing import StatusCoordinateApplication


def _normal_initialization_blocks_settings_refresh(app, required: bool) -> bool:
    if not required:
        return False
    state_getter = getattr(getattr(app, "grbl", None), "normal_session_state", None)
    if not callable(state_getter):
        return True
    try:
        state = state_getter()
    except Exception:
        return True
    return (
        getattr(state, "phase", None) is not NormalSessionPhase.POSITION_REQUIRED
        or bool(getattr(state, "homing_started", False))
    )


def apply_machine_state_minimal(
    app,
    state: str,
    display_state: str,
    *,
    status_allows_alarm_clear,
    stream_latched_banner_state,
    render_machine_state_text,
    apply_machine_state_visuals_deferred_highlight,
    with_macro_vars_nonblocking,
) -> None:
    state_lower = str(state or "").strip().lower()
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if status_allows_alarm_clear(app):
            app._set_alarm_lock(False)
        if (not bool(getattr(app, "_alarm_locked", False))) and not getattr(
            app, "_macro_status_active", False
        ):
            banner_state = stream_latched_banner_state(app, state, display_state)
            rendered_state = render_machine_state_text(app, state, banner_state)
            apply_machine_state_visuals_deferred_highlight(
                app,
                rendered_state=rendered_state,
                banner_state=banner_state,
                width_context="Failed adjusting machine-state width during settling",
                highlight_context="Failed updating machine-state highlight during settling",
            )

    def _update_macro_state(macro_vars: dict) -> None:
        macro_vars["state"] = state
        macro_vars["_status_seq"] = int(macro_vars.get("_status_seq", 0) or 0) + 1

    with_macro_vars_nonblocking(
        app,
        _update_macro_state,
        context="Failed updating macro state during settling status handling",
    )


def apply_machine_state(
    app,
    state: str,
    display_state: str,
    *,
    status_connect_settling_active,
    status_allows_alarm_clear,
    stream_latched_banner_state,
    render_machine_state_text,
    apply_machine_state_visuals_deferred_highlight,
    apply_machine_state_visuals,
    maybe_restore_pending_g90,
    modal_sync_retry_ready,
    stream_active_or_finishing,
    schedule_request_settings_dump,
    schedule_request_modal_state_sync,
    job_controls_ready,
    set_run_resume_from,
    schedule_status_ui_callback,
    with_macro_vars_nonblocking,
    signal_thread_event,
) -> bool:
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
            if status_allows_alarm_clear(app):
                app._set_alarm_lock(False)
        elif not getattr(app, "_macro_status_active", False):
            banner_state = stream_latched_banner_state(app, state, display_state)
            rendered_state = render_machine_state_text(app, state, banner_state)
            if status_connect_settling_active(app):
                apply_machine_state_visuals_deferred_highlight(
                    app,
                    rendered_state=rendered_state,
                    banner_state=banner_state,
                    width_context="Failed adjusting machine state label width",
                    highlight_context="Failed updating machine-state highlight during connect settling",
                )
            else:
                apply_machine_state_visuals(
                    app,
                    rendered_state=rendered_state,
                    banner_state=banner_state,
                    width_context="Failed adjusting machine state label width",
                    highlight_context="Failed updating machine-state highlight",
                )
        maybe_restore_pending_g90(app)

    recovery_checker = getattr(getattr(app, "grbl", None), "recovery_required", None)
    try:
        recovery_required = bool(recovery_checker()) if callable(recovery_checker) else False
    except Exception:
        recovery_required = True
    normal_checker = getattr(
        getattr(app, "grbl", None),
        "normal_session_initialization_required",
        None,
    )
    try:
        normal_initialization_required = (
            bool(normal_checker()) if callable(normal_checker) else False
        )
    except Exception:
        normal_initialization_required = True
    if app._grbl_ready and app._pending_settings_refresh and not app._alarm_locked:
        if stream_active_or_finishing(app) or app.grbl.is_streaming():
            return False
        if not recovery_required and not _normal_initialization_blocks_settings_refresh(
            app,
            normal_initialization_required,
        ):
            app._pending_settings_refresh = False
            schedule_request_settings_dump(app)
    if (
        modal_sync_retry_ready(
            app,
            timeout_status="Modal-state sync timed out; retrying.",
            timeout_log="[status] $G modal sync timed out; retry pending.",
        )
        and app._grbl_ready
        and not app._alarm_locked
    ):
        if not (stream_active_or_finishing(app) or app.grbl.is_streaming()):
            schedule_request_modal_state_sync(app)
    controls_allowed = bool(
        app.connected
        and app._grbl_ready
        and app._status_seen
        and not app._alarm_locked
        and not stream_active_or_finishing(app)
        and not recovery_required
        and not normal_initialization_required
    )
    ready_now = job_controls_ready(app) if controls_allowed else False
    if ready_now != bool(getattr(app, "_job_controls_last_ready", False)):
        set_run_resume_from(app, ready_now)
        app._job_controls_last_ready = bool(ready_now)
    manual_last = bool(getattr(app, "_manual_controls_last_enabled", False))
    if bool(controls_allowed) != manual_last:
        new_manual_enabled = bool(controls_allowed)
        if status_connect_settling_active(app):
            worker = getattr(app, "grbl", None)
            recovery_epoch_getter = getattr(worker, "recovery_epoch", None)
            try:
                scheduled_recovery_epoch = (
                    int(recovery_epoch_getter()) if callable(recovery_epoch_getter) else None
                )
            except Exception:
                scheduled_recovery_epoch = None

            def _apply_manual_controls_deferred() -> None:
                recovery_checker = getattr(worker, "recovery_required", None)
                try:
                    if callable(recovery_checker) and bool(recovery_checker()):
                        app._set_manual_controls_enabled(False)
                        return
                    if callable(normal_checker) and bool(normal_checker()):
                        app._set_manual_controls_enabled(False)
                        return
                    if callable(recovery_epoch_getter) and scheduled_recovery_epoch is not None:
                        if int(recovery_epoch_getter()) != scheduled_recovery_epoch:
                            app._set_manual_controls_enabled(False)
                            return
                except Exception:
                    app._set_manual_controls_enabled(False)
                    return
                app._set_manual_controls_enabled(new_manual_enabled)

            schedule_status_ui_callback(
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

    with_macro_vars_nonblocking(
        app,
        _update_macro_state,
        context="Failed updating macro state from status event",
    )
    signal_thread_event(app, "_status_update_event")
    if next_state_token != prev_state_token:

        def _apply_transition_ui_updates() -> None:
            if hasattr(app, "_refresh_toolbar_action_focus"):
                app._refresh_toolbar_action_focus()
            if hasattr(app, "_update_quick_button_visibility"):
                app._update_quick_button_visibility()

        schedule_status_ui_callback(
            app,
            callback_attr="_status_state_transition_ui_after_id",
            callback=_apply_transition_ui_updates,
            context="Failed applying state-transition toolbar/quick-button refresh",
        )
    return True


def handle_status_event(
    app,
    raw: str,
    *,
    status_generation: int | None,
    status_recovery_epoch: int | None,
    status_event_identity_current,
    time_module,
    deque_cls,
    settling_active,
    signal_thread_event,
    status_state_token,
    homing_status_resolution_pending,
    stream_active_or_finishing,
    mark_status_coordinates_fresh,
    record_status_perf_metric,
    sync_deferred_stream_completion,
    status_relaxed_idle_signature,
    status_apply_interval_ok,
    parse_status_fields,
    resolve_display_state,
    apply_machine_state_minimal,
    apply_machine_state,
    status_positions_should_force_defer,
    queue_coalesced_status_positions_update,
    stream_status_positions_coalesce_active,
    clear_coalesced_status_positions_state,
    update_positions_and_macro_state,
    sync_manual_jog_prediction_with_status,
    log_slow_status_event,
    noncritical_budget_ms: float,
    log_suppressed,
) -> None:
    event_start = time_module.perf_counter()
    parse_elapsed_ms = 0.0
    apply_elapsed_ms = 0.0
    positions_elapsed_ms = 0.0
    deferred_elapsed_ms = 0.0
    if not status_event_identity_current(
        app,
        generation=status_generation,
        recovery_epoch=status_recovery_epoch,
    ):
        return
    settling = settling_active(app)
    signal_thread_event(app, "_status_update_event")
    now_ts = time_module.time()
    app._last_status_ts = now_ts
    previous_raw = str(getattr(app, "_last_status_raw", "") or "")
    app._last_status_raw = raw
    parse_start = time_module.perf_counter()
    fields = parse_status_fields(raw)
    fields.event_generation = status_generation
    fields.event_recovery_epoch = status_recovery_epoch
    parse_elapsed_ms = (time_module.perf_counter() - parse_start) * 1000.0
    record_status_perf_metric(app, "parse", parse_elapsed_ms)
    state_token = status_state_token(raw)
    state_lower = str(state_token or "").strip().lower()
    homing_resolution_pending = homing_status_resolution_pending(app)
    live_updates_during_settling = bool(
        settling and state_lower.startswith(("jog", "run", "hold"))
    )
    if state_token and not str(state_token).lower().startswith("idle"):
        try:
            app._status_last_non_idle_ts = time_module.monotonic()
        except Exception as exc:
            log_suppressed("Failed tracking last non-idle status timestamp", exc)
    installed_coordinate_signature = getattr(
        app,
        "_status_installed_coordinate_signature",
        None,
    )
    frame_matches_installed_coordinates = bool(
        fields.coordinate_signature is not None
        and fields.coordinate_signature == installed_coordinate_signature
    )
    fields.coordinate_unchanged = frame_matches_installed_coordinates
    if (
        (not homing_resolution_pending)
        and raw == previous_raw
        and frame_matches_installed_coordinates
    ):
        app._status_seen = True
        app._status_duplicate_count = int(getattr(app, "_status_duplicate_count", 0) or 0) + 1
        if state_token:
            sync_deferred_stream_completion(app, state_token)
        mark_status_coordinates_fresh(
            app,
            context="Failed marking duplicate status coordinates fresh",
        )
        record_status_perf_metric(
            app,
            "duplicate_short_circuit",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        record_status_perf_metric(
            app,
            "total",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        return
    if (
        (not homing_resolution_pending)
        and state_lower.startswith("idle")
        and not stream_active_or_finishing(app)
        and frame_matches_installed_coordinates
        and status_relaxed_idle_signature(raw) == status_relaxed_idle_signature(previous_raw)
    ):
        app._status_seen = True
        app._status_duplicate_count = int(getattr(app, "_status_duplicate_count", 0) or 0) + 1
        sync_deferred_stream_completion(app, state_token or "Idle")
        mark_status_coordinates_fresh(
            app,
            context="Failed marking relaxed duplicate status coordinates fresh",
        )
        record_status_perf_metric(
            app,
            "duplicate_short_circuit_relaxed",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        record_status_perf_metric(
            app,
            "total",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        return
    if not status_apply_interval_ok(app, state_token):
        record_status_perf_metric(
            app,
            "settling_coalesced",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        record_status_perf_metric(
            app,
            "total",
            (time_module.perf_counter() - event_start) * 1000.0,
        )
        return
    app._status_duplicate_count = 0
    history = getattr(app, "_status_history", None)
    if not isinstance(history, deque_cls):
        seed: list[tuple[float, str]] = []
        if isinstance(history, list):
            seed = history[-200:]
        history = deque_cls(seed, maxlen=200)
        app._status_history = history
    history.append((now_ts, raw))
    if fields.feed is not None:
        try:
            app._last_status_feed_raw = float(fields.feed)
        except Exception:
            pass
    app._status_seen = True
    app._last_status_pins = fields.pins
    display_state = resolve_display_state(app, fields.state)
    apply_start = time_module.perf_counter()
    if not fields.coordinates_valid:
        record_status_perf_metric(app, "positions_macro", 0.0)
    elif settling and not live_updates_during_settling:
        apply_machine_state_minimal(app, fields.state, display_state)
    else:
        if not apply_machine_state(app, fields.state, display_state):
            apply_elapsed_ms = (time_module.perf_counter() - apply_start) * 1000.0
            record_status_perf_metric(app, "apply_state", apply_elapsed_ms)
            total_ms = (time_module.perf_counter() - event_start) * 1000.0
            record_status_perf_metric(app, "total", total_ms)
            log_slow_status_event(
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
    apply_elapsed_ms = (time_module.perf_counter() - apply_start) * 1000.0
    record_status_perf_metric(app, "apply_state", apply_elapsed_ms)
    if not fields.coordinates_valid:
        record_status_perf_metric(app, "positions_macro", 0.0)
    elif settling and not live_updates_during_settling:
        record_status_perf_metric(app, "positions_macro", 0.0)
    else:
        update_start = time_module.perf_counter()
        force_defer_positions = status_positions_should_force_defer(
            app,
            event_started_perf=event_start,
            noncritical_budget_ms=noncritical_budget_ms,
        )
        if queue_coalesced_status_positions_update(
            app,
            fields,
            force_defer=force_defer_positions,
        ):
            pass
        else:
            if not stream_status_positions_coalesce_active(app):
                clear_coalesced_status_positions_state(app)
            coordinate_application = update_positions_and_macro_state(
                app,
                fields,
                event_started_perf=event_start,
                noncritical_budget_ms=noncritical_budget_ms,
            )
            if coordinate_application is StatusCoordinateApplication.VALID_INSTALLED:
                sync_manual_jog_prediction_with_status(app)
                setattr(
                    app,
                    "_status_positions_last_apply_ts",
                    float(time_module.monotonic()),
                )
        positions_elapsed_ms = (time_module.perf_counter() - update_start) * 1000.0
        record_status_perf_metric(app, "positions_macro", positions_elapsed_ms)
    finalize_start = time_module.perf_counter()
    sync_deferred_stream_completion(app, fields.state)
    deferred_elapsed_ms = (time_module.perf_counter() - finalize_start) * 1000.0
    record_status_perf_metric(app, "deferred_completion", deferred_elapsed_ms)
    total_ms = (time_module.perf_counter() - event_start) * 1000.0
    record_status_perf_metric(app, "total", total_ms)
    log_slow_status_event(
        app,
        total_ms=total_ms,
        state=fields.state,
        parse_ms=parse_elapsed_ms,
        apply_ms=apply_elapsed_ms,
        positions_ms=positions_elapsed_ms,
        deferred_ms=deferred_elapsed_ms,
        settling=settling,
    )
