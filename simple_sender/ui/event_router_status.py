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

from simple_sender.ui.dro import convert_units, format_dro_value
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from


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
            modal_state["tool"] = int(token[1:])
    if modal_units:
        app._modal_units = modal_units
        try:
            app._set_unit_mode(modal_units)
        except Exception:
            pass
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
    except Exception:
        return
    app._report_units = "inch" if val == 1 else "mm"
    try:
        app._update_unit_toggle_display()
    except Exception:
        pass
    try:
        status_text = ""
        try:
            status_text = app.status.cget("text")
        except Exception:
            status_text = ""
        if getattr(app, "_connected_port", None) and status_text.startswith("Connected"):
            app.status.config(
                text=f"Connected: {app._connected_port} | Report: {app._report_units}"
            )
    except Exception:
        pass
    try:
        app._refresh_dro_display()
    except Exception:
        pass


def handle_status_event(app, raw: str):
    # Parse minimal fields: state + WPos if present
    app._last_status_raw = raw
    app._last_status_ts = time.time()
    history = getattr(app, "_status_history", None)
    if not isinstance(history, list):
        history = []
        app._status_history = history
    history.append((app._last_status_ts, raw))
    if len(history) > 200:
        del history[:-200]
    s = raw.strip("<>")
    parts = s.split("|")
    state = parts[0] if parts else "?"
    app._status_seen = True
    wpos = None
    mpos = None
    feed = None
    spindle = None
    planner = None
    rxbytes = None
    wco = None
    ov = None
    pins = None
    for p in parts:
        if p.startswith("WPos:"):
            wpos = p[5:]
        elif p.startswith("MPos:"):
            mpos = p[5:]
        elif p.startswith("FS:"):
            try:
                f_str, s_str = p[3:].split(",", 1)
                feed = float(f_str)
                spindle = float(s_str)
            except Exception:
                pass
        elif p.startswith("Bf:"):
            try:
                bf_planner, bf_rx = p[3:].split(",", 1)
                planner = int(bf_planner)
                rxbytes = int(bf_rx)
            except Exception:
                pass
        elif p.startswith("WCO:"):
            wco = p[4:]
        elif p.startswith("Ov:"):
            ov = p[3:]
        elif p.startswith("Pn:"):
            pins = p[3:]
    app._last_status_pins = pins

    state_lower = state.lower()
    display_state = "Homing" if state_lower.startswith("home") else state
    if getattr(app, "_homing_in_progress", False):
        if state_lower.startswith("home"):
            app._homing_state_seen = True
            display_state = "Homing"
        elif state_lower.startswith("idle"):
            start_ts = getattr(app, "_homing_start_ts", 0.0)
            timeout_s = getattr(app, "_homing_timeout_s", 30.0)
            timed_out = start_ts and (time.time() - start_ts) > timeout_s
            if getattr(app, "_homing_state_seen", False) or timed_out:
                app._homing_in_progress = False
                app._homing_state_seen = False
                display_state = state
                try:
                    app.grbl.clear_watchdog_ignore("homing")
                except Exception:
                    pass
            else:
                display_state = "Homing"
        elif state_lower.startswith("alarm") or state_lower.startswith("door"):
            app._homing_in_progress = False
            app._homing_state_seen = False
            display_state = state
            try:
                app.grbl.clear_watchdog_ignore("homing")
            except Exception:
                pass
        else:
            app._homing_in_progress = False
            app._homing_state_seen = False
            display_state = state
            try:
                app.grbl.clear_watchdog_ignore("homing")
            except Exception:
                pass
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if app._alarm_locked:
            app._set_alarm_lock(False)
        else:
            if not getattr(app, "_macro_status_active", False):
                app.machine_state.set(display_state)
                app._update_state_highlight(display_state)
    if app._grbl_ready and app._pending_settings_refresh and not app._alarm_locked:
        if app._stream_state in ("running", "paused") or app.grbl.is_streaming():
            return
        app._pending_settings_refresh = False
        app._request_settings_dump()
    if (
        app.connected
        and app._grbl_ready
        and app._status_seen
        and not app._alarm_locked
        and app._stream_state not in ("running", "paused")
    ):
        app._set_manual_controls_enabled(True)
        set_run_resume_from(app, job_controls_ready(app))
    with app.macro_executor.macro_vars() as macro_vars:
        macro_vars["state"] = state
        macro_vars["_status_seq"] = int(macro_vars.get("_status_seq", 0) or 0) + 1

    def parse_xyz(text: str):
        parts = text.split(",")
        if len(parts) < 3:
            return None
        try:
            return [float(parts[0]), float(parts[1]), float(parts[2])]
        except Exception:
            return None

    wco_vals = parse_xyz(wco) if wco else None
    mpos_vals = parse_xyz(mpos) if mpos else None
    wpos_vals = parse_xyz(wpos) if wpos else None
    if wco_vals:
        app._wco_raw = tuple(wco_vals)
    else:
        cached_wco = getattr(app, "_wco_raw", None)
        if cached_wco and len(cached_wco) >= 3:
            wco_vals = [cached_wco[0], cached_wco[1], cached_wco[2]]
    report_units = getattr(app, "_report_units", None) or app.unit_mode.get()
    modal_units = app.unit_mode.get()

    def to_mm(value: float) -> float:
        return convert_units(value, report_units, "mm")

    def to_modal(value: float) -> float:
        return convert_units(value, report_units, modal_units)

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
        except Exception:
            pass
    elif mpos_calc:
        try:
            app.mpos_x.set(format_dro_value(mpos_calc[0], report_units, modal_units))
            app.mpos_y.set(format_dro_value(mpos_calc[1], report_units, modal_units))
            app.mpos_z.set(format_dro_value(mpos_calc[2], report_units, modal_units))
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["mx"] = to_modal(mpos_calc[0])
                macro_vars["my"] = to_modal(mpos_calc[1])
                macro_vars["mz"] = to_modal(mpos_calc[2])
        except Exception:
            pass
    def flash_wpos_labels():
        labels = getattr(app, "_wpos_value_labels", None)
        if not labels:
            return
        for axis, label in labels.items():
            default_fg = ""
            try:
                default_fg = app._wpos_label_default_fg.get(axis, "")
            except Exception:
                default_fg = ""
            after_id = None
            try:
                after_id = app._wpos_flash_after_ids.get(axis)
            except Exception:
                after_id = None
            if after_id:
                try:
                    app.after_cancel(after_id)
                except Exception:
                    pass
            try:
                label.configure(foreground="#2196f3")
            except Exception:
                continue

            def restore(target=label, axis_key=axis, fg=default_fg):
                try:
                    if fg:
                        target.configure(foreground=fg)
                    else:
                        target.configure(foreground="")
                except Exception:
                    pass
                try:
                    app._wpos_flash_after_ids[axis_key] = None
                except Exception:
                    pass

            try:
                app._wpos_flash_after_ids[axis] = app.after(150, restore)
            except Exception:
                pass

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
            except Exception:
                pass
        except Exception:
            pass
        flash_wpos_labels()
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
            except Exception:
                pass
        except Exception:
            pass
    if feed is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["curfeed"] = feed
    if spindle is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["curspindle"] = spindle
    if planner is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["planner"] = planner
    if rxbytes is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["rxbytes"] = rxbytes
    if wco_vals:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["wcox"] = to_modal(wco_vals[0])
            macro_vars["wcoy"] = to_modal(wco_vals[1])
            macro_vars["wcoz"] = to_modal(wco_vals[2])
    if pins is not None:
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["pins"] = pins
    if ov:
        feed_val = spindle_val = None
        try:
            ov_parts = [int(float(v)) for v in ov.split(",")]
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
        except Exception:
            pass
        else:
            if feed_val is not None:
                app._set_feed_override_slider_value(feed_val)
            if spindle_val is not None:
                app._set_spindle_override_slider_value(spindle_val)
            app._refresh_override_info()
    pin_state = {c for c in (pins or "").upper() if c.isalpha()}
    endstop_active = bool(pin_state & {"X", "Y", "Z"})
    with app.macro_executor.macro_vars() as macro_vars:
        prb_value = macro_vars.get("PRB")
    probe_active = bool(pin_state & {"P"}) or bool(prb_value)
    hold_active = bool(pin_state & {"H"}) or "hold" in str(state).lower()
    app._update_led_panel(endstop_active, probe_active, hold_active)


