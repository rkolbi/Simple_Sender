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
from dataclasses import dataclass
from typing import cast

from simple_sender.ui.dro import convert_units, format_dro_value
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _stream_active_or_finishing(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    return getattr(app, "_stream_state", None) in ("running", "paused")


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
            except Exception as exc:
                _log_suppressed("Failed parsing FS field from status line", exc)
        elif part.startswith("Bf:"):
            try:
                planner_str, rx_str = part[3:].split(",", 1)
                fields.planner = int(planner_str)
                fields.rxbytes = int(rx_str)
            except Exception as exc:
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
        timed_out = start_ts and (time.time() - start_ts) > timeout_s
        if getattr(app, "_homing_state_seen", False) or timed_out:
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


def _apply_machine_state(app, state: str, display_state: str) -> bool:
    state_lower = state.lower()
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if app._alarm_locked:
            app._set_alarm_lock(False)
        elif not getattr(app, "_macro_status_active", False):
            app.machine_state.set(display_state)
            try:
                app._ensure_state_label_width(display_state)
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
        app._request_settings_dump()
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
        try:
            app._maybe_notify_job_completion(done, total)
        except Exception as exc:
            _log_suppressed("Failed notifying deferred stream completion", exc)
        try:
            set_run_resume_from(app, job_controls_ready(app))
            app.btn_pause.config(state="disabled")
            app.btn_resume.config(state="disabled")
        except Exception as exc:
            _log_suppressed("Failed finalizing run/pause/resume controls after deferred completion", exc)
        try:
            app._set_manual_controls_enabled(
                app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
            )
            app._set_streaming_lock(False)
        except Exception as exc:
            _log_suppressed("Failed finalizing manual controls after deferred completion", exc)
        try:
            app.settings_controller.set_streaming_lock(False)
        except Exception:
            pass
        try:
            app.toolpath_panel.set_streaming(False)
        except Exception:
            pass
        if (
            getattr(app, "_pending_settings_refresh", False)
            and app._grbl_ready
            and not app._alarm_locked
            and not app.grbl.is_streaming()
        ):
            app._pending_settings_refresh = False
            app._request_settings_dump()
        if (
            app._toolpath_reparse_deferred
            and app._last_gcode_lines
            and not getattr(app, "_gcode_streaming_mode", False)
        ):
            app._toolpath_reparse_deferred = False
            app.toolpath_panel.reparse_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)
        try:
            app._apply_status_poll_profile()
        except Exception as exc:
            _log_suppressed("Failed applying status poll profile after deferred completion", exc)
        return
    try:
        if int(app.progress_pct.get()) >= 100:
            app.progress_pct.set(99)
    except Exception:
        pass


def _parse_xyz_triplet(text: str) -> list[float] | None:
    parts = text.split(",")
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception as exc:
        _log_suppressed("Failed parsing XYZ triplet", exc)
        return None


def _flash_wpos_labels(app) -> None:
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

    def to_mm(value: float) -> float:
        return cast(float, convert_units(value, report_units, "mm"))

    def to_modal(value: float) -> float:
        return cast(float, convert_units(value, report_units, modal_units))

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
        try:
            app._mpos_raw = tuple(mpos_vals)
            app.mpos_x.set(format_dro_value(mpos_vals[0], report_units, modal_units))
            app.mpos_y.set(format_dro_value(mpos_vals[1], report_units, modal_units))
            app.mpos_z.set(format_dro_value(mpos_vals[2], report_units, modal_units))
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["mx"] = to_modal(mpos_vals[0])
                macro_vars["my"] = to_modal(mpos_vals[1])
                macro_vars["mz"] = to_modal(mpos_vals[2])
        except Exception as exc:
            _log_suppressed("Failed updating machine-position DRO values", exc)
    elif mpos_calc:
        try:
            app.mpos_x.set(format_dro_value(mpos_calc[0], report_units, modal_units))
            app.mpos_y.set(format_dro_value(mpos_calc[1], report_units, modal_units))
            app.mpos_z.set(format_dro_value(mpos_calc[2], report_units, modal_units))
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["mx"] = to_modal(mpos_calc[0])
                macro_vars["my"] = to_modal(mpos_calc[1])
                macro_vars["mz"] = to_modal(mpos_calc[2])
        except Exception as exc:
            _log_suppressed("Failed updating computed machine-position DRO values", exc)

    if wpos_vals:
        try:
            app._wpos_raw = tuple(wpos_vals)
            app.wpos_x.set(format_dro_value(wpos_vals[0], report_units, modal_units))
            app.wpos_y.set(format_dro_value(wpos_vals[1], report_units, modal_units))
            app.wpos_z.set(format_dro_value(wpos_vals[2], report_units, modal_units))
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["wx"] = to_modal(wpos_vals[0])
                macro_vars["wy"] = to_modal(wpos_vals[1])
                macro_vars["wz"] = to_modal(wpos_vals[2])
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
        _flash_wpos_labels(app)
    elif wpos_calc:
        try:
            app._wpos_raw = tuple(wpos_calc)
            app.wpos_x.set(format_dro_value(wpos_calc[0], report_units, modal_units))
            app.wpos_y.set(format_dro_value(wpos_calc[1], report_units, modal_units))
            app.wpos_z.set(format_dro_value(wpos_calc[2], report_units, modal_units))
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["wx"] = to_modal(wpos_calc[0])
                macro_vars["wy"] = to_modal(wpos_calc[1])
                macro_vars["wz"] = to_modal(wpos_calc[2])
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

    if fields.feed is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["curfeed"] = fields.feed
    if fields.spindle is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["curspindle"] = fields.spindle
    if fields.planner is not None:
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
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["planner"] = fields.planner
    if fields.rxbytes is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["rxbytes"] = fields.rxbytes
    if wco_vals:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["wcox"] = to_modal(wco_vals[0])
            macro_vars["wcoy"] = to_modal(wco_vals[1])
            macro_vars["wcoz"] = to_modal(wco_vals[2])
    if fields.pins is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["pins"] = fields.pins
    if fields.ov:
        feed_val = spindle_val = None
        try:
            ov_parts = [int(float(v)) for v in fields.ov.split(",")]
            if len(ov_parts) >= 3:
                feed_val, spindle_val = ov_parts[0], ov_parts[2]
                with app.macro_executor.macro_vars() as macro_vars:
                    changed = (
                        macro_vars.get("OvFeed") != ov_parts[0]
                        or macro_vars.get("OvRapid") != ov_parts[1]
                        or macro_vars.get("OvSpindle") != ov_parts[2]
                    )
                    macro_vars["OvFeed"] = ov_parts[0]
                    macro_vars["OvRapid"] = ov_parts[1]
                    macro_vars["OvSpindle"] = ov_parts[2]
                    macro_vars["_OvChanged"] = bool(changed)
        except Exception as exc:
            _log_suppressed("Failed parsing override values from status line", exc)
        else:
            if feed_val is not None:
                app._set_feed_override_slider_value(feed_val)
            if spindle_val is not None:
                app._set_spindle_override_slider_value(spindle_val)
            app._refresh_override_info()
    pin_state = {char for char in (fields.pins or "").upper() if char.isalpha()}
    endstop_active = bool(pin_state & {"X", "Y", "Z"})
    with app.macro_executor.macro_vars() as macro_vars:
        prb_value = macro_vars.get("PRB")
    probe_active = bool(pin_state & {"P"}) or bool(prb_value)
    hold_active = bool(pin_state & {"H"}) or "hold" in fields.state.lower()
    app._update_led_panel(endstop_active, probe_active, hold_active)


def handle_status_event(app, raw: str):
    app._last_status_raw = raw
    app._last_status_ts = time.time()
    history = getattr(app, "_status_history", None)
    if not isinstance(history, list):
        history = []
        app._status_history = history
    history.append((app._last_status_ts, raw))
    if len(history) > 200:
        del history[:-200]
    fields = _parse_status_fields(raw)
    app._status_seen = True
    app._last_status_pins = fields.pins
    display_state = _resolve_display_state(app, fields.state)
    if not _apply_machine_state(app, fields.state, display_state):
        return
    _update_positions_and_macro_state(app, fields)
    _sync_deferred_stream_completion(app, fields.state)


