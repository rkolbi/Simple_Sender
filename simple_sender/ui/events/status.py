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
from dataclasses import dataclass

from simple_sender.ui.dro import format_dro_value
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from .stream_state_ui import apply_stream_busy_state, restore_controls_after_stream

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_WPOS_FLASH_MIN_INTERVAL_S = 0.25
_DRO_DISPLAY_STEP = 0.001


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _signal_thread_event(obj, attr_name: str) -> None:
    evt = getattr(obj, attr_name, None)
    if isinstance(evt, threading.Event):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed(f"Failed signaling thread event {attr_name}", exc)


def _status_state_token(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("<"):
        text = text[1:]
    end_idx = text.find("|")
    if end_idx >= 0:
        return text[:end_idx]
    if text.endswith(">"):
        text = text[:-1]
    return text


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


def _stream_active_or_finishing(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    return getattr(app, "_stream_state", None) in ("running", "paused")


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
    line = raw.strip()
    if not (line.startswith("[GC:") and line.endswith("]")):
        return
    tokens = line.strip("[]").split()
    if not tokens:
        return
    modal_units = None
    modal_state = {}
    for token in tokens:
        if token.startswith("GC:"):
            token = token[3:]
            if not token:
                continue
        if token in ("G20", "G21"):
            modal_units = "inch" if token == "G20" else "mm"
            modal_state["units"] = token
            continue
        if token in ("G90", "G91"):
            modal_state["distance"] = token
            continue
        if token in ("G17", "G18", "G19"):
            modal_state["plane"] = token
            continue
        if token in ("G93", "G94"):
            modal_state["feedmode"] = token
            continue
        if token in ("G90.1", "G91.1"):
            modal_state["arc"] = token
            continue
        if token in ("G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3"):
            modal_state["WCS"] = token
            continue
        if token in ("G0", "G1", "G2", "G3", "G38.2", "G38.3", "G38.4", "G38.5"):
            modal_state["motion"] = token
            continue
        if token in ("M3", "M4", "M5"):
            modal_state["spindle"] = token
            continue
        if token in ("M7", "M8", "M9"):
            modal_state["coolant"] = token
            continue
        if token.startswith("T") and token[1:].isdigit():
            modal_state["tool"] = str(int(token[1:]))
    if modal_units:
        app._modal_units = modal_units
        try:
            app._set_unit_mode(modal_units)
        except Exception as exc:
            _log_suppressed("Failed to apply modal unit mode", exc)
    if modal_state or modal_units:
        with app.macro_executor.macro_vars() as macro_vars:
            for key, value in modal_state.items():
                macro_vars[key] = value
            macro_vars["_modal_seq"] = int(macro_vars.get("_modal_seq", 0) or 0) + 1
        _signal_thread_event(app, "_modal_update_event")


def _parse_report_units_setting(app, raw: str) -> None:
    line = raw.strip()
    if not line.startswith("$13="):
        return
    try:
        raw_val = line.split("=", 1)[1].strip()
        raw_val = raw_val.split(" ", 1)[0]
        raw_val = raw_val.split("(", 1)[0].strip()
        val = int(raw_val)
    except Exception as exc:
        _log_suppressed("Failed parsing $13 report-units setting", exc)
        return
    app._report_units = "inch" if val == 1 else "mm"
    try:
        app._update_unit_toggle_display()
    except Exception as exc:
        _log_suppressed("Failed updating unit toggle display from $13", exc)
    try:
        status_text = ""
        try:
            status_text = app.status.cget("text")
        except Exception as exc:
            _log_suppressed("Failed reading status label text for $13 update", exc)
            status_text = ""
        if getattr(app, "_connected_port", None) and status_text.startswith("Connected"):
            app.status.config(
                text=f"Connected: {app._connected_port} | Report: {app._report_units}"
            )
    except Exception as exc:
        _log_suppressed("Failed updating connected status label after $13 update", exc)
    try:
        app._refresh_dro_display()
    except Exception as exc:
        _log_suppressed("Failed refreshing DRO display after $13 update", exc)


@dataclass(slots=True)
class _StatusFields:
    state: str
    wpos: str | None = None
    mpos: str | None = None
    feed: float | None = None
    spindle: float | None = None
    planner: int | None = None
    rxbytes: int | None = None
    wco: str | None = None
    ov: str | None = None
    pins: str | None = None


def _parse_status_fields(raw: str) -> _StatusFields:
    parts = raw.strip("<>").split("|")
    fields = _StatusFields(state=parts[0] if parts else "?")
    for part in parts:
        if part.startswith("WPos:"):
            fields.wpos = part[5:]
        elif part.startswith("MPos:"):
            fields.mpos = part[5:]
        elif part.startswith("FS:"):
            try:
                feed_str, spindle_str = part[3:].split(",", 1)
                fields.feed = float(feed_str)
                fields.spindle = float(spindle_str)
            except ValueError as exc:
                _log_suppressed("Failed parsing FS field from status line", exc)
        elif part.startswith("Bf:"):
            try:
                planner_str, rx_str = part[3:].split(",", 1)
                fields.planner = int(planner_str)
                fields.rxbytes = int(rx_str)
            except ValueError as exc:
                _log_suppressed("Failed parsing Bf field from status line", exc)
        elif part.startswith("WCO:"):
            fields.wco = part[4:]
        elif part.startswith("Ov:"):
            fields.ov = part[3:]
        elif part.startswith("Pn:"):
            fields.pins = part[3:]
    return fields


def _resolve_display_state(app, state: str) -> str:
    state_lower = state.lower()
    display_state = "Homing" if state_lower.startswith("home") else state
    if not getattr(app, "_homing_in_progress", False):
        return display_state
    if state_lower.startswith("home"):
        app._homing_state_seen = True
        return "Homing"
    if state_lower.startswith("idle"):
        start_ts = getattr(app, "_homing_start_ts", 0.0)
        timeout_s = getattr(app, "_homing_timeout_s", 30.0)
        elapsed = max(0.0, (time.time() - start_ts)) if start_ts else 0.0
        timed_out = bool(start_ts) and elapsed > timeout_s
        grace_elapsed = (not start_ts) or (elapsed >= _homing_idle_grace_seconds(app))
        if getattr(app, "_homing_state_seen", False) or timed_out or grace_elapsed:
            app._homing_in_progress = False
            app._homing_state_seen = False
            try:
                app.grbl.clear_watchdog_ignore("homing")
            except Exception as exc:
                _log_suppressed("Failed clearing homing watchdog ignore on idle", exc)
            return state
        return "Homing"

    app._homing_in_progress = False
    app._homing_state_seen = False
    try:
        app.grbl.clear_watchdog_ignore("homing")
    except Exception as exc:
        context = (
            "Failed clearing homing watchdog ignore on alarm/door"
            if (state_lower.startswith("alarm") or state_lower.startswith("door"))
            else "Failed clearing homing watchdog ignore on other state"
        )
        _log_suppressed(context, exc)
    return state


def _format_hhmm(seconds: int) -> str:
    total_minutes = int(round(seconds / 60)) if seconds else 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def _run_completion_eta_text(app) -> str | None:
    remaining_min = getattr(app, "_live_estimate_display_min", None)
    if remaining_min is None:
        remaining_min = getattr(app, "_live_estimate_min", None)
    try:
        remaining_min = float(remaining_min)
    except Exception:
        return None
    if remaining_min < 0:
        remaining_min = 0.0

    factor = 1.0
    getter = getattr(app, "_estimate_factor_value", None)
    if callable(getter):
        try:
            factor = float(getter())
        except Exception:
            factor = 1.0
    if factor <= 0:
        factor = 1.0

    remaining_seconds = int(round(remaining_min * factor * 60.0))
    return _format_hhmm(remaining_seconds)


def _render_machine_state_text(app, state: str, display_state: str) -> str:
    if not str(state or "").lower().startswith("run"):
        return display_state
    eta_text = _run_completion_eta_text(app)
    if not eta_text:
        return display_state
    return f"{display_state}: {eta_text}"


def _apply_machine_state(app, state: str, display_state: str) -> bool:
    state_lower = state.lower()
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if app._alarm_locked:
            app._set_alarm_lock(False)
        elif not getattr(app, "_macro_status_active", False):
            rendered_state = _render_machine_state_text(app, state, display_state)
            app.machine_state.set(rendered_state)
            try:
                app._ensure_state_label_width(rendered_state)
            except Exception as exc:
                _log_suppressed("Failed adjusting machine state label width", exc)
            app._update_state_highlight(display_state)
            try:
                app._update_current_highlight()
            except Exception as exc:
                _log_suppressed("Failed updating current-line highlight from status state", exc)
        _maybe_restore_pending_g90(app)

    if app._grbl_ready and app._pending_settings_refresh and not app._alarm_locked:
        if _stream_active_or_finishing(app) or app.grbl.is_streaming():
            return False
        app._pending_settings_refresh = False
        _schedule_request_settings_dump(app)
    if (
        app.connected
        and app._grbl_ready
        and app._status_seen
        and not app._alarm_locked
        and not _stream_active_or_finishing(app)
    ):
        app._set_manual_controls_enabled(True)
        set_run_resume_from(app, job_controls_ready(app))
    with app.macro_executor.macro_vars() as macro_vars:
        macro_vars["state"] = state
        macro_vars["_status_seq"] = int(macro_vars.get("_status_seq", 0) or 0) + 1
    _signal_thread_event(app, "_status_update_event")
    return True


def _sync_deferred_stream_completion(app, state: str) -> None:
    if not bool(getattr(app, "_stream_done_pending_idle", False)):
        return
    total = int(getattr(app, "_gcode_total_lines", 0) or 0)
    if total <= 0:
        return
    done = int(getattr(app, "_last_acked_index", -1)) + 1
    if done < total:
        return
    if str(state or "").lower().startswith("idle"):
        app._stream_done_pending_idle = False
        app._stream_state = "done"
        try:
            app.progress_pct.set(100)
        except Exception as exc:
            _log_suppressed("Failed finalizing deferred progress at stream completion", exc)

        def _finalize_completion_ui() -> None:
            app._deferred_stream_finalize_pending = False
            try:
                app._maybe_notify_job_completion(done, total)
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
    try:
        if int(app.progress_pct.get()) >= 100:
            app.progress_pct.set(99)
    except (AttributeError, TypeError, ValueError) as exc:
        _log_suppressed("Failed clamping deferred completion progress", exc)


def _parse_xyz_triplet(text: str) -> list[float] | None:
    parts = text.split(",")
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except ValueError as exc:
        _log_suppressed("Failed parsing XYZ triplet", exc)
        return None


def _unit_scale_cached(unit_mode: str) -> float:
    return 25.4 if str(unit_mode or "").lower() == "inch" else 1.0


def _position_deadband_report_units(report_units: str, modal_units: str) -> float:
    # Use half of the displayed precision step to avoid churn from noise that
    # cannot be represented in the DRO.
    report_scale = _unit_scale_cached(report_units)
    modal_scale = _unit_scale_cached(modal_units)
    if report_scale <= 0:
        return 1e-6
    step_report = _DRO_DISPLAY_STEP * (modal_scale / report_scale)
    return max(1e-6, step_report * 0.5)


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


def _update_positions_and_macro_state(app, fields: _StatusFields) -> None:
    wco_vals = _parse_xyz_triplet(fields.wco) if fields.wco else None
    mpos_vals = _parse_xyz_triplet(fields.mpos) if fields.mpos else None
    wpos_vals = _parse_xyz_triplet(fields.wpos) if fields.wpos else None
    if wco_vals:
        app._wco_raw = tuple(wco_vals)
    else:
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

    def to_mm(value: float) -> float:
        return value * to_mm_factor

    def to_modal(value: float) -> float:
        return value * to_modal_factor

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
                try:
                    app.toolpath_panel.set_position(
                        to_mm(wpos_vals[0]),
                        to_mm(wpos_vals[1]),
                        to_mm(wpos_vals[2]),
                    )
                except Exception as exc:
                    _log_suppressed("Failed updating toolpath position from WPos", exc)
            except Exception as exc:
                _log_suppressed("Failed updating WPos DRO values", exc)
        macro_updates["wx"] = to_modal(wpos_vals[0])
        macro_updates["wy"] = to_modal(wpos_vals[1])
        macro_updates["wz"] = to_modal(wpos_vals[2])
        if wpos_changed:
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
                try:
                    app.toolpath_panel.set_position(
                        to_mm(wpos_calc[0]),
                        to_mm(wpos_calc[1]),
                        to_mm(wpos_calc[2]),
                    )
                except Exception as exc:
                    _log_suppressed("Failed updating toolpath position from computed WPos", exc)
            except Exception as exc:
                _log_suppressed("Failed updating computed WPos DRO values", exc)
        macro_updates["wx"] = to_modal(wpos_calc[0])
        macro_updates["wy"] = to_modal(wpos_calc[1])
        macro_updates["wz"] = to_modal(wpos_calc[2])

    if fields.feed is not None:
        macro_updates["curfeed"] = fields.feed
    if fields.spindle is not None:
        macro_updates["curspindle"] = fields.spindle
        mpos_rpm_var = getattr(app, "mpos_rpm", None)
        if mpos_rpm_var is not None:
            try:
                rpm_text = str(int(round(float(fields.spindle))))
                _set_var_if_changed(mpos_rpm_var, rpm_text)
            except Exception as exc:
                _log_suppressed("Failed updating MPos spindle-RPM display", exc)
        spindle_rpm_var = getattr(app, "spindle_current_rpm_var", None)
        if spindle_rpm_var is not None:
            try:
                rpm_text = str(int(round(float(fields.spindle))))
                _set_var_if_changed(spindle_rpm_var, rpm_text)
            except Exception as exc:
                _log_suppressed("Failed updating spindle current-speed display", exc)
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
                if feed_val is not None:
                    app._set_feed_override_slider_value(feed_val)
                if spindle_val is not None:
                    app._set_spindle_override_slider_value(spindle_val)
                app._refresh_override_info()
    pin_state = {char for char in (fields.pins or "").upper() if char.isalpha()}
    endstop_active = bool(pin_state & {"X", "Y", "Z"})
    prb_value = None
    try:
        with app.macro_executor.macro_vars() as macro_vars:
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
            prb_value = macro_vars.get("PRB")
    except Exception as exc:
        _log_suppressed("Failed updating macro status values", exc)
    probe_active = bool(pin_state & {"P"}) or bool(prb_value)
    hold_active = bool(pin_state & {"H"}) or "hold" in fields.state.lower()
    app._update_led_panel(endstop_active, probe_active, hold_active)


def handle_status_event(app, raw: str):
    event_start = time.perf_counter()
    _signal_thread_event(app, "_status_update_event")
    now_ts = time.time()
    app._last_status_ts = now_ts
    previous_raw = str(getattr(app, "_last_status_raw", "") or "")
    app._last_status_raw = raw
    state_token = _status_state_token(raw)
    if state_token and not str(state_token).lower().startswith("idle"):
        try:
            app._status_last_non_idle_ts = time.monotonic()
        except Exception as exc:
            _log_suppressed("Failed tracking last non-idle status timestamp", exc)
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
    app._status_duplicate_count = 0
    history = getattr(app, "_status_history", None)
    if not isinstance(history, deque):
        seed: list[tuple[float, str]] = []
        if isinstance(history, list):
            seed = history[-200:]
        history = deque(seed, maxlen=200)
        app._status_history = history
    history.append((now_ts, raw))
    parse_start = time.perf_counter()
    fields = _parse_status_fields(raw)
    _record_status_perf_metric(app, "parse", (time.perf_counter() - parse_start) * 1000.0)
    app._status_seen = True
    app._last_status_pins = fields.pins
    display_state = _resolve_display_state(app, fields.state)
    apply_start = time.perf_counter()
    if not _apply_machine_state(app, fields.state, display_state):
        _record_status_perf_metric(
            app,
            "apply_state",
            (time.perf_counter() - apply_start) * 1000.0,
        )
        _record_status_perf_metric(app, "total", (time.perf_counter() - event_start) * 1000.0)
        return
    _record_status_perf_metric(app, "apply_state", (time.perf_counter() - apply_start) * 1000.0)
    update_start = time.perf_counter()
    _update_positions_and_macro_state(app, fields)
    _record_status_perf_metric(
        app,
        "positions_macro",
        (time.perf_counter() - update_start) * 1000.0,
    )
    finalize_start = time.perf_counter()
    _sync_deferred_stream_completion(app, fields.state)
    _record_status_perf_metric(
        app,
        "deferred_completion",
        (time.perf_counter() - finalize_start) * 1000.0,
    )
    _record_status_perf_metric(app, "total", (time.perf_counter() - event_start) * 1000.0)


