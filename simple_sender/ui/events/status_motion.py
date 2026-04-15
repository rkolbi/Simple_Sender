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

"""Status-event motion/DRO helpers extracted from the main status module."""

from .status_parsing import _StatusFields


def xyz_tuple_changed(
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


def flash_wpos_labels(
    app,
    *,
    time_module,
    flash_min_interval_s: float,
    log_suppressed,
) -> None:
    now = time_module.monotonic()
    last_flash = float(getattr(app, "_wpos_flash_last_ts", 0.0) or 0.0)
    if (now - last_flash) < flash_min_interval_s:
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
            log_suppressed("Failed reading default WPos label color", exc)
            default_fg = ""
        after_id = None
        try:
            after_id = app._wpos_flash_after_ids.get(axis)
        except Exception as exc:
            log_suppressed("Failed reading pending WPos flash id", exc)
            after_id = None
        if after_id:
            try:
                app.after_cancel(after_id)
            except Exception as exc:
                log_suppressed("Failed cancelling previous WPos flash timer", exc)
        try:
            label.configure(foreground="#2196f3")
        except Exception as exc:
            log_suppressed("Failed applying WPos flash color", exc)
            continue

        def restore(target=label, axis_key=axis, fg=default_fg):
            try:
                if fg:
                    target.configure(foreground=fg)
                else:
                    target.configure(foreground="")
            except Exception as exc:
                log_suppressed("Failed restoring WPos label color", exc)
            try:
                app._wpos_flash_after_ids[axis_key] = None
            except Exception as exc:
                log_suppressed("Failed clearing WPos flash timer id", exc)

        try:
            app._wpos_flash_after_ids[axis] = app.after(150, restore)
        except Exception as exc:
            log_suppressed("Failed scheduling WPos flash restore timer", exc)


def run_manual_jog_prediction_tick(
    app,
    *,
    time_module,
    log_suppressed,
    jog_prediction_should_run,
    clear_manual_jog_prediction,
    manual_jog_prediction_horizon_s,
    interp_unsynced_max_dt_s: float,
    format_dro_value_hook,
    set_var_if_changed,
    record_jog_dro_trace,
    schedule_manual_jog_prediction_tick,
) -> None:
    app._manual_jog_predict_after_id = None
    state = getattr(app, "_manual_jog_predict_state", None)
    if not isinstance(state, dict):
        return
    if not jog_prediction_should_run(app):
        clear_manual_jog_prediction(app)
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
        log_suppressed("Failed reading manual jog prediction state", exc)
        clear_manual_jog_prediction(app)
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
        clear_manual_jog_prediction(app)
        return
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
    prediction_horizon_s = manual_jog_prediction_horizon_s(state)
    elapsed_s = max(0.0, time_module.monotonic() - anchor_ts)
    status_sync_interval_s = max(0.0, float(state.get("status_sync_interval_s", 0.0) or 0.0))
    if status_sync_interval_s > 0.0:
        elapsed_cap_s = min(float(prediction_horizon_s), status_sync_interval_s)
    else:
        elapsed_cap_s = min(float(prediction_horizon_s), float(interp_unsynced_max_dt_s))
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
        schedule_manual_jog_prediction_tick(app)
        return
    try:
        est_wpos: tuple[float, float, float] | None = None
        mpos_x = format_dro_value_hook(est_mpos[0], report_units, modal_units)
        mpos_y = format_dro_value_hook(est_mpos[1], report_units, modal_units)
        mpos_z = format_dro_value_hook(est_mpos[2], report_units, modal_units)
        set_var_if_changed(app.mpos_x, mpos_x)
        set_var_if_changed(app.mpos_y, mpos_y)
        set_var_if_changed(app.mpos_z, mpos_z)
        wco_raw = getattr(app, "_wco_raw", None)
        if isinstance(wco_raw, (list, tuple)) and len(wco_raw) >= 3:
            est_wpos = (
                float(est_mpos[0]) - float(wco_raw[0]),
                float(est_mpos[1]) - float(wco_raw[1]),
                float(est_mpos[2]) - float(wco_raw[2]),
            )
            wpos_x = format_dro_value_hook(est_wpos[0], report_units, modal_units)
            wpos_y = format_dro_value_hook(est_wpos[1], report_units, modal_units)
            wpos_z = format_dro_value_hook(est_wpos[2], report_units, modal_units)
            set_var_if_changed(app.wpos_x, wpos_x)
            set_var_if_changed(app.wpos_y, wpos_y)
            set_var_if_changed(app.wpos_z, wpos_z)
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
        record_jog_dro_trace(
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
        log_suppressed("Failed applying manual jog DRO interpolation", exc)
        clear_manual_jog_prediction(app)
        return
    schedule_manual_jog_prediction_tick(app)


def start_manual_jog_prediction(
    app,
    *,
    dx: float,
    dy: float,
    dz: float,
    feed: float,
    unit_mode: str,
    source: str | None,
    time_module,
    normalized_jog_prediction_source,
    jog_prediction_enabled_for_source,
    clear_manual_jog_prediction,
    units_ratio,
    record_jog_dro_trace,
    manual_jog_prediction_horizon_s,
    schedule_manual_jog_prediction_tick,
) -> None:
    normalized_source = normalized_jog_prediction_source(source)
    if not jog_prediction_enabled_for_source(app, normalized_source):
        clear_manual_jog_prediction(app)
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
        clear_manual_jog_prediction(app)
        return
    report_units = str(getattr(app, "_report_units", None) or unit_mode or "mm")
    ratio = units_ratio(str(unit_mode or "mm"), report_units)
    dx_report = dx_val * ratio
    dy_report = dy_val * ratio
    dz_report = dz_val * ratio
    max_distance_report = (dx_report * dx_report + dy_report * dy_report + dz_report * dz_report) ** 0.5
    if max_distance_report <= 0.0:
        clear_manual_jog_prediction(app)
        return
    speed_report_s = (feed_val * ratio) / 60.0
    if speed_report_s <= 0.0:
        clear_manual_jog_prediction(app)
        return
    mpos_raw = getattr(app, "_mpos_raw", None)
    if not (isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3):
        return
    start_ts = float(time_module.monotonic())
    anchor_ts = float(time_module.monotonic())
    app._manual_jog_predict_state = {
        "start_ts": start_ts,
        "anchor_ts": anchor_ts,
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
    record_jog_dro_trace(
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
        horizon_s=float(manual_jog_prediction_horizon_s(app._manual_jog_predict_state)),
    )
    schedule_manual_jog_prediction_tick(app)


def sync_manual_jog_prediction_with_status(
    app,
    *,
    time_module,
    log_suppressed,
    jog_prediction_should_run,
    clear_manual_jog_prediction,
    manual_jog_prediction_horizon_s,
    record_jog_dro_delta_stats,
    record_jog_dro_trace,
    schedule_manual_jog_prediction_tick,
    observed_velocity_alpha: float,
    zero_feed_eps: float,
    stationary_distance_eps: float,
) -> None:
    state = getattr(app, "_manual_jog_predict_state", None)
    if not isinstance(state, dict):
        return
    if not jog_prediction_should_run(app):
        clear_manual_jog_prediction(app)
        return
    mpos_raw = getattr(app, "_mpos_raw", None)
    if isinstance(mpos_raw, (list, tuple)) and len(mpos_raw) >= 3:
        try:
            now_mono = float(time_module.monotonic())
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
            prediction_horizon_s = manual_jog_prediction_horizon_s(state)
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
                record_jog_dro_delta_stats(
                    app,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    sync_interval_s=sync_interval_s,
                    predict_horizon_s=prediction_horizon_s,
                )
                record_jog_dro_trace(
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
                    last_feed <= float(zero_feed_eps)
                    or observed_speed <= float(stationary_distance_eps)
                )
                prev_velocity = state.get("velocity_report_s")
                if freeze_interp:
                    next_velocity = (0.0, 0.0, 0.0)
                elif isinstance(prev_velocity, (list, tuple)) and len(prev_velocity) >= 3:
                    alpha = float(observed_velocity_alpha)
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
                record_jog_dro_trace(
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
            state["start_ts"] = float(time_module.monotonic())
            state["anchor_ts"] = float(time_module.monotonic())
        except Exception as exc:
            log_suppressed("Failed syncing manual jog prediction anchor to status", exc)
    schedule_manual_jog_prediction_tick(app)


def stop_manual_jog_prediction(
    app,
    *,
    reason: str,
    record_jog_dro_trace,
    clear_manual_jog_prediction,
    snap_dro_to_last_status_raw,
    request_stop_status_refresh,
) -> None:
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
    record_jog_dro_trace(
        app,
        "stop",
        reason=str(reason or "stop"),
        est_mpos=est_mpos if isinstance(est_mpos, tuple) and len(est_mpos) >= 3 else None,
        actual_mpos=actual_mpos,
        delta=delta,
    )
    clear_manual_jog_prediction(app)
    snap_dro_to_last_status_raw(app)
    request_stop_status_refresh(app)


def update_positions_and_macro_state(
    app,
    fields: _StatusFields,
    *,
    event_started_perf: float | None = None,
    noncritical_budget_ms: float,
    time_module,
    log_suppressed,
    stream_active_or_finishing,
    parse_xyz_triplet,
    unit_scale_cached,
    position_deadband_report_units,
    xyz_tuple_changed,
    format_dro_value_hook,
    set_var_if_changed,
    performance_mode_enabled,
    schedule_status_ui_callback,
    with_macro_vars_nonblocking,
    mark_status_coordinates_fresh,
    flash_wpos_labels,
) -> None:
    stream_busy_for_noncritical = stream_active_or_finishing(app)
    reported_wco_vals = parse_xyz_triplet(fields.wco) if fields.wco else None
    mpos_vals = parse_xyz_triplet(fields.mpos) if fields.mpos else None
    reported_wpos_vals = parse_xyz_triplet(fields.wpos) if fields.wpos else None
    wco_vals = list(reported_wco_vals) if reported_wco_vals else None
    wpos_vals = list(reported_wpos_vals) if reported_wpos_vals else None
    if wco_vals:
        app._wco_raw = tuple(wco_vals)
    else:
        cached_wco = getattr(app, "_wco_raw", None)
        if cached_wco and len(cached_wco) >= 3:
            wco_vals = [cached_wco[0], cached_wco[1], cached_wco[2]]

    report_units = getattr(app, "_report_units", None) or app.unit_mode.get()
    modal_units = app.unit_mode.get()
    report_scale = unit_scale_cached(report_units)
    modal_scale = unit_scale_cached(modal_units)
    to_modal_factor = report_scale / modal_scale
    pos_deadband = position_deadband_report_units(report_units, modal_units)

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
            now_mono = time_module.monotonic()
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

    def to_modal(value: float) -> float:
        return float(value) * float(to_modal_factor)

    def _status_event_elapsed_ms() -> float:
        if event_started_perf is None:
            return 0.0
        now_perf = float(time_module.perf_counter())
        return max(0.0, (now_perf - float(event_started_perf)) * 1000.0)

    def _should_defer_noncritical_updates() -> bool:
        if stream_busy_for_noncritical or performance_mode_enabled(app):
            return True
        return _status_event_elapsed_ms() >= max(1.0, float(noncritical_budget_ms))

    macro_updates: dict[str, object] = {}
    wpos_calc = None
    mpos_calc = None
    if mpos_vals and wpos_vals is None and wco_vals:
        wpos_calc = [mpos_vals[0] - wco_vals[0], mpos_vals[1] - wco_vals[1], mpos_vals[2] - wco_vals[2]]
    elif wpos_vals and mpos_vals is None and wco_vals:
        mpos_calc = [wpos_vals[0] + wco_vals[0], wpos_vals[1] + wco_vals[1], wpos_vals[2] + wco_vals[2]]

    if mpos_vals:
        mpos_tuple = (mpos_vals[0], mpos_vals[1], mpos_vals[2])
        mpos_changed = xyz_tuple_changed(getattr(app, "_mpos_raw", None), mpos_tuple, deadband=pos_deadband)
        app._mpos_raw = mpos_tuple
        if mpos_changed:
            try:
                set_var_if_changed(app.mpos_x, format_dro_value_hook(mpos_vals[0], report_units, modal_units))
                set_var_if_changed(app.mpos_y, format_dro_value_hook(mpos_vals[1], report_units, modal_units))
                set_var_if_changed(app.mpos_z, format_dro_value_hook(mpos_vals[2], report_units, modal_units))
            except Exception as exc:
                log_suppressed("Failed updating machine-position DRO values", exc)
        macro_updates["mx"] = to_modal(mpos_vals[0])
        macro_updates["my"] = to_modal(mpos_vals[1])
        macro_updates["mz"] = to_modal(mpos_vals[2])
    elif mpos_calc:
        mpos_calc_tuple = (mpos_calc[0], mpos_calc[1], mpos_calc[2])
        mpos_changed = xyz_tuple_changed(getattr(app, "_mpos_raw", None), mpos_calc_tuple, deadband=pos_deadband)
        app._mpos_raw = mpos_calc_tuple
        if mpos_changed:
            try:
                set_var_if_changed(app.mpos_x, format_dro_value_hook(mpos_calc[0], report_units, modal_units))
                set_var_if_changed(app.mpos_y, format_dro_value_hook(mpos_calc[1], report_units, modal_units))
                set_var_if_changed(app.mpos_z, format_dro_value_hook(mpos_calc[2], report_units, modal_units))
            except Exception as exc:
                log_suppressed("Failed updating computed machine-position DRO values", exc)
        macro_updates["mx"] = to_modal(mpos_calc[0])
        macro_updates["my"] = to_modal(mpos_calc[1])
        macro_updates["mz"] = to_modal(mpos_calc[2])

    if wpos_vals:
        wpos_tuple = (wpos_vals[0], wpos_vals[1], wpos_vals[2])
        wpos_changed = xyz_tuple_changed(getattr(app, "_wpos_raw", None), wpos_tuple, deadband=pos_deadband)
        app._wpos_raw = wpos_tuple
        if wpos_changed:
            try:
                set_var_if_changed(app.wpos_x, format_dro_value_hook(wpos_vals[0], report_units, modal_units))
                set_var_if_changed(app.wpos_y, format_dro_value_hook(wpos_vals[1], report_units, modal_units))
                set_var_if_changed(app.wpos_z, format_dro_value_hook(wpos_vals[2], report_units, modal_units))
            except Exception as exc:
                log_suppressed("Failed updating WPos DRO values", exc)
        macro_updates["wx"] = to_modal(wpos_vals[0])
        macro_updates["wy"] = to_modal(wpos_vals[1])
        macro_updates["wz"] = to_modal(wpos_vals[2])
        if wpos_changed and not stream_busy_for_noncritical:
            flash_wpos_labels(app)
    elif wpos_calc:
        wpos_calc_tuple = (wpos_calc[0], wpos_calc[1], wpos_calc[2])
        wpos_changed = xyz_tuple_changed(getattr(app, "_wpos_raw", None), wpos_calc_tuple, deadband=pos_deadband)
        app._wpos_raw = wpos_calc_tuple
        if wpos_changed:
            try:
                set_var_if_changed(app.wpos_x, format_dro_value_hook(wpos_calc[0], report_units, modal_units))
                set_var_if_changed(app.wpos_y, format_dro_value_hook(wpos_calc[1], report_units, modal_units))
                set_var_if_changed(app.wpos_z, format_dro_value_hook(wpos_calc[2], report_units, modal_units))
            except Exception as exc:
                log_suppressed("Failed updating computed WPos DRO values", exc)
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
            log_suppressed("Failed parsing spindle RPM from status line", exc)
            rpm_text = None
        if rpm_text is not None:
            mpos_rpm_var = getattr(app, "mpos_rpm", None)
            spindle_rpm_var = getattr(app, "spindle_current_rpm_var", None)

            def _apply_spindle_rpm_ui_sync() -> None:
                if mpos_rpm_var is not None:
                    set_var_if_changed(mpos_rpm_var, rpm_text)
                if spindle_rpm_var is not None:
                    set_var_if_changed(spindle_rpm_var, rpm_text)

            if _should_defer_noncritical_updates():
                schedule_status_ui_callback(
                    app,
                    callback_attr="_status_spindle_rpm_after_id",
                    callback=_apply_spindle_rpm_ui_sync,
                    context="Failed applying deferred spindle-RPM UI sync from status",
                )
            else:
                try:
                    _apply_spindle_rpm_ui_sync()
                except Exception as exc:
                    log_suppressed("Failed applying spindle-RPM UI sync from status", exc)
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
            log_suppressed("Failed tracking planner availability from status line", exc)
    if fields.rxbytes is not None:
        macro_updates["rxbytes"] = fields.rxbytes
    if wco_vals:
        macro_updates["wcox"] = to_modal(wco_vals[0])
        macro_updates["wcoy"] = to_modal(wco_vals[1])
        macro_updates["wcoz"] = to_modal(wco_vals[2])
    if fields.pins is not None:
        macro_updates["pins"] = fields.pins
    ov_values: tuple[int, int, int] | None = None
    if fields.ov:
        feed_val = spindle_val = None
        try:
            ov_parts = [int(float(v)) for v in fields.ov.split(",")]
            if len(ov_parts) >= 3:
                feed_val, spindle_val = ov_parts[0], ov_parts[2]
                ov_values = (ov_parts[0], ov_parts[1], ov_parts[2])
        except Exception as exc:
            log_suppressed("Failed parsing override values from status line", exc)
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
                    schedule_status_ui_callback(
                        app,
                        callback_attr="_status_override_sync_after_id",
                        callback=_apply_override_ui_sync,
                        context="Failed applying deferred override UI sync from status",
                    )
                else:
                    try:
                        _apply_override_ui_sync()
                    except Exception as exc:
                        log_suppressed("Failed applying override UI sync from status", exc)
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

    with_macro_vars_nonblocking(
        app,
        _apply_macro_status_updates,
        context="Failed updating macro status values",
    )
    mark_status_coordinates_fresh(
        app,
        context="Failed marking status coordinates fresh after position update",
    )
    probe_active = bool(pin_state & {"P"})
    hold_active = bool(pin_state & {"H"}) or "hold" in fields.state.lower()

    def _apply_led_panel_state() -> None:
        app._update_led_panel(endstop_active, probe_active, hold_active)

    if _should_defer_noncritical_updates() and stream_busy_for_noncritical:
        schedule_status_ui_callback(
            app,
            callback_attr="_status_led_panel_after_id",
            callback=_apply_led_panel_state,
            context="Failed applying deferred LED panel state from status",
        )
    else:
        try:
            _apply_led_panel_state()
        except Exception as exc:
            log_suppressed("Failed updating LED panel from status", exc)
