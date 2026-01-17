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
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
from tkinter import messagebox

from simple_sender.ui.dro_display import convert_units, format_dro_value
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error


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


def handle_event(app, evt):
    kind = evt[0]
    if kind == "conn":
        handle_connection_event(app, evt[1], evt[2])
        return
    if kind == "ui_call":
        handle_ui_call(app, evt[1], evt[2], evt[3], evt[4])
        return
    if kind == "ui_post":
        handle_ui_post(app, evt[1], evt[2], evt[3])
        return
    if kind == "macro_prompt":
        handle_macro_prompt(app, evt[1], evt[2], evt[3], evt[4], evt[5])
        return
    if kind == "gcode_loaded":
        handle_gcode_loaded(app, evt)
        return
    if kind == "gcode_loaded_stream":
        handle_gcode_loaded_stream(app, evt)
        return
    if kind == "gcode_load_invalid":
        handle_gcode_load_invalid(
            app,
            evt[1],
            evt[2],
            evt[3],
            evt[4],
            evt[5],
            evt[6] if len(evt) > 6 else None,
            evt[7] if len(evt) > 7 else None,
        )
        return
    if kind == "gcode_load_error":
        handle_gcode_load_error(app, evt[1], evt[2], evt[3])
        return
    if kind == "log":
        app.streaming_controller.handle_log(evt[1])
        return
    if kind == "log_tx":
        app.streaming_controller.handle_log_tx(evt[1])
        return
    if kind == "log_rx":
        raw = evt[1]
        _parse_modal_units(app, raw)
        _parse_report_units_setting(app, raw)
        app.settings_controller.handle_line(raw)
        app.streaming_controller.handle_log_rx(raw)
        return
    if kind == "manual_error":
        msg = evt[1] if len(evt) > 1 else "error"
        msg = annotate_grbl_error(msg)
        source = evt[2] if len(evt) > 2 else None
        label = str(source).strip() if source else ""
        prefix = f"GRBL error ({label})" if label else "GRBL error"
        if getattr(app, "_homing_in_progress", False):
            app._homing_in_progress = False
            app._homing_state_seen = False
        try:
            app.status.config(text=f"{prefix}: {msg}")
        except Exception:
            pass
        return
    if kind == "ready":
        handle_ready_event(app, evt[1])
        return
    if kind == "alarm":
        msg = evt[1] if len(evt) > 1 else ""
        msg = annotate_grbl_alarm(msg)
        if getattr(app, "_homing_in_progress", False):
            app._homing_in_progress = False
            app._homing_state_seen = False
        app._set_alarm_lock(True, msg)
        app.macro_executor.notify_alarm(msg)
        app._apply_status_poll_profile()
        return
    if kind == "status":
        handle_status_event(app, evt[1])
        return
    if kind == "buffer_fill":
        pct, used, window = evt[1], evt[2], evt[3]
        app.streaming_controller.handle_buffer_fill(pct, used, window)
        return
    if kind == "throughput":
        bps = evt[1] if len(evt) > 1 else 0.0
        app.streaming_controller.handle_throughput(float(bps))
        return
    if kind == "stream_state":
        handle_stream_state_event(app, evt)
        return
    if kind == "stream_interrupted":
        handle_stream_interrupted(app, evt)
        return
    if kind == "stream_error":
        msg = evt[1] if len(evt) > 1 else ""
        try:
            app.status.config(text=f"Stream error: {msg}")
        except Exception:
            pass
        return
    if kind == "stream_pause_reason":
        reason = evt[1] if len(evt) > 1 else ""
        if reason:
            try:
                app.status.config(text=f"Paused ({reason})")
            except Exception:
                pass
        return
    if kind == "gcode_sent":
        app.streaming_controller.handle_gcode_sent(evt[1])
        return
    if kind == "gcode_acked":
        app.streaming_controller.handle_gcode_acked(evt[1])
        return
    if kind == "progress":
        done, total = evt[1], evt[2]
        app.streaming_controller.handle_progress(done, total)


def handle_ui_call(app, func, args, kwargs, result_q):
    try:
        result_q.put((True, func(*args, **kwargs)))
    except Exception as exc:
        app._log_exception("UI action failed", exc)
        result_q.put((False, exc))


def handle_ui_post(app, func, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception as exc:
        app._log_exception("UI action failed", exc)


def handle_macro_prompt(app, title, message, choices, cancel_label, result_q):
    try:
        app._show_macro_prompt(title, message, choices, cancel_label, result_q)
    except Exception as exc:
        try:
            app.streaming_controller.log(f"[macro] Prompt failed: {exc}")
        except Exception:
            pass
        if result_q.empty():
            result_q.put(cancel_label)


def handle_gcode_loaded(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        return
    path = evt[2]
    lines = evt[3]
    lines_hash = evt[4] if len(evt) > 4 else None
    validated = bool(evt[5]) if len(evt) > 5 else False
    report = evt[6] if len(evt) > 6 else None
    app._gcode_validation_report = report
    app._apply_loaded_gcode(path, lines, lines_hash=lines_hash, validated=validated)


def handle_gcode_loaded_stream(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        return
    path = evt[2]
    source = evt[3]
    preview_lines = evt[4] if len(evt) > 4 else []
    lines_hash = evt[5] if len(evt) > 5 else None
    total_lines = evt[6] if len(evt) > 6 else None
    report = evt[7] if len(evt) > 7 else None
    app._gcode_validation_report = report
    app._apply_loaded_gcode(
        path,
        preview_lines,
        lines_hash=lines_hash,
        validated=True,
        streaming_source=source,
        total_lines=total_lines,
    )


def handle_gcode_load_invalid(
    app,
    token,
    _path,
    too_long: int,
    first_idx: int | None,
    first_len: int | None,
    total_lines: int | None = None,
    cleaned_lines: int | None = None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("No file loaded")
    msg = f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit."
    if first_idx is not None and first_len is not None:
        msg += f"\nFirst at line {first_idx + 1} ({first_len} bytes including newline)."
    if total_lines is not None or cleaned_lines is not None:
        orig = f"{total_lines}" if total_lines is not None else "?"
        cleaned = f"{cleaned_lines}" if cleaned_lines is not None else "?"
        msg += f"\nFile lines: {orig} (non-empty: {cleaned})."
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def handle_gcode_load_error(app, token, _path, err):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("No file loaded")
    messagebox.showerror("Open G-code", f"Failed to read file:\n{err}")
    app.status.config(text="G-code load failed")


def handle_status_event(app, raw: str):
    # Parse minimal fields: state + WPos if present
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
            else:
                display_state = "Homing"
        elif state_lower.startswith("alarm") or state_lower.startswith("door"):
            app._homing_in_progress = False
            app._homing_state_seen = False
            display_state = state
        else:
            app._homing_in_progress = False
            app._homing_state_seen = False
            display_state = state
    app._machine_state_text = state
    if state_lower.startswith("alarm"):
        app._set_alarm_lock(True, state)
    else:
        if app._alarm_locked:
            app._set_alarm_lock(False)
        else:
            app.machine_state.set(display_state)
            app._update_state_highlight(display_state)
    if app._grbl_ready and app._pending_settings_refresh and not app._alarm_locked:
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
        if app.gview.lines_count:
            app.btn_run.config(state="normal")
            app.btn_resume_from.config(state="normal")
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


def handle_stream_state_event(app, evt):
    st = evt[1]
    prev = app._stream_state
    now = time.time()
    app._stream_state = st
    if st == "running":
        if prev == "paused":
            if app._stream_paused_at is not None:
                app._stream_pause_total += max(0.0, now - app._stream_paused_at)
                app._stream_paused_at = None
        elif prev != "running":
            app._stream_start_ts = now
            app._stream_pause_total = 0.0
            app._stream_paused_at = None
            app._live_estimate_min = None
            app._refresh_gcode_stats_display()
            app.throughput_var.set("TX: 0 B/s")
        try:
            status_text = app.status.cget("text")
        except Exception:
            status_text = ""
        if status_text.startswith(("Stream error", "Paused", "Resuming")):
            try:
                name = ""
                path = getattr(app, "_last_gcode_path", None)
                if path:
                    name = os.path.basename(path)
                if not name:
                    name = getattr(app.grbl, "_gcode_name", "") or ""
                label = f"Streaming: {name}" if name else "Streaming..."
                app.status.config(text=label)
            except Exception:
                pass
    elif st == "paused":
        if app._stream_paused_at is None:
            app._stream_paused_at = now
    elif st in ("done", "stopped", "error", "alarm", "loaded"):
        app._stream_start_ts = None
        app._stream_pause_total = 0.0
        app._stream_paused_at = None
        app._live_estimate_min = None
        app._refresh_gcode_stats_display()
        app.throughput_var.set("TX: 0 B/s")

    if st == "loaded":
        total = evt[2] if len(evt) > 2 else None
        app.progress_pct.set(0)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        if (
            app.connected
            and total
            and app._grbl_ready
            and app._status_seen
            and not app._alarm_locked
        ):
            app.btn_run.config(state="normal")
            app.btn_resume_from.config(state="normal")
        else:
            app.btn_run.config(state="disabled")
            app.btn_resume_from.config(state="disabled")
        app._set_manual_controls_enabled(
            app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
        )
        app._set_streaming_lock(False)
    elif st == "running":
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = True
            macro_vars["paused"] = False
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="normal")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app._set_streaming_lock(True)
    elif st == "paused":
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = True
            macro_vars["paused"] = True
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="normal")
        app.btn_resume_from.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app._set_streaming_lock(True)
    elif st in ("done", "stopped"):
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        if st == "done":
            app.progress_pct.set(100)
        else:
            app.progress_pct.set(0)
        app.btn_run.config(
            state="normal"
            if (
                app.connected
                and app.gview.lines_count
                and app._grbl_ready
                and app._status_seen
                and not app._alarm_locked
            )
            else "disabled"
        )
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(
            state="normal"
            if (
                app.connected
                and app.gview.lines_count
                and app._grbl_ready
                and app._status_seen
                and not app._alarm_locked
            )
            else "disabled"
        )
        app._set_manual_controls_enabled(
            app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
        )
        app._set_streaming_lock(False)
    elif st == "error":
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        app.btn_run.config(
            state="normal"
            if (
                app.connected
                and app.gview.lines_count
                and app._grbl_ready
                and app._status_seen
                and not app._alarm_locked
            )
            else "disabled"
        )
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(
            state="normal"
            if (
                app.connected
                and app.gview.lines_count
                and app._grbl_ready
                and app._status_seen
                and not app._alarm_locked
            )
            else "disabled"
        )
        app.status.config(text=f"Stream error: {evt[2]}")
        app._set_manual_controls_enabled(
            app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
        )
        app._set_streaming_lock(False)
    elif st == "alarm":
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app._set_alarm_lock(True, evt[2] if len(evt) > 2 else None)
        app._set_streaming_lock(False)
    if st in ("running", "paused"):
        try:
            app.settings_controller.set_streaming_lock(True)
        except Exception:
            pass
        app.toolpath_panel.set_streaming(True)
    else:
        try:
            app.settings_controller.set_streaming_lock(False)
        except Exception:
            pass
        app.toolpath_panel.set_streaming(False)
        if (
            app._toolpath_reparse_deferred
            and app._last_gcode_lines
            and not getattr(app, "_gcode_streaming_mode", False)
        ):
            app._toolpath_reparse_deferred = False
            app.toolpath_panel.reparse_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)
    app._apply_status_poll_profile()


def handle_stream_interrupted(app, evt):
    was_streaming = bool(evt[1]) if len(evt) > 1 else False
    if not was_streaming:
        return
    if getattr(app, "_user_disconnect", False):
        return
    app._resume_after_disconnect = True
    app._resume_from_index = max(0, app._last_acked_index + 1)
    app._resume_job_name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
