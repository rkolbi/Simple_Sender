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

import os
import time
from tkinter import messagebox

from simple_sender.ui.icons import ICON_CONNECT, icon_label
from simple_sender.ui.job_controls import disable_job_controls
from simple_sender.utils.constants import STATUS_POLL_DEFAULT


def handle_connection_event(app, is_on: bool, port):
    app.connected = bool(is_on)
    app._connecting = False
    app._disconnecting = False
    app._homing_in_progress = False
    app._homing_state_seen = False
    if app.connected:
        app._auto_reconnect_last_port = port or app._auto_reconnect_last_port
        app._auto_reconnect_pending = False
        app._auto_reconnect_last_attempt = 0.0
        app._auto_reconnect_retry = 0
        app._auto_reconnect_delay = 3.0
        app._auto_reconnect_next_ts = 0.0
        app._auto_reconnect_blocked = False
        app._report_units = None
        try:
            app._update_unit_toggle_display()
        except Exception:
            pass
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Disconnect"), state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception:
            pass
        try:
            app.port_combo.config(state="readonly")
        except Exception:
            pass
        app._connected_port = port
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = True
        app._status_seen = False
        app.machine_state.set(f"CONNECTED ({port})")
        app._machine_state_text = f"CONNECTED ({port})"
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception:
            pass
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text=f"Connected: {port} (waiting for Grbl)")
        app.btn_stop.config(state="normal")
        disable_job_controls(app)
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app.throughput_var.set("TX: 0 B/s")
        try:
            if getattr(app, "_gcode_source", None) is not None:
                name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
                app.grbl.load_gcode(app._gcode_source, name=name or None)
            elif app._last_gcode_lines:
                name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
                app.grbl.load_gcode(app._last_gcode_lines, name=name or None)
        except Exception:
            pass
    else:
        try:
            app._stop_macro_status()
        except Exception:
            pass
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Connect"), state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception:
            pass
        try:
            app.port_combo.config(state="readonly")
        except Exception:
            pass
        app._connected_port = None
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = False
        app._status_seen = False
        app._report_units = None
        try:
            app._update_unit_toggle_display()
        except Exception:
            pass
        app.machine_state.set("DISCONNECTED")
        app._machine_state_text = "DISCONNECTED"
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception:
            pass
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text="Disconnected")
        disable_job_controls(app)
        app.btn_stop.config(state="disabled")
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app._rapid_rates = None
        app._rapid_rates_source = None
        app._accel_rates = None
        if app._last_gcode_lines:
            app._update_gcode_stats(app._last_gcode_lines)
        if app._user_disconnect:
            app._resume_after_disconnect = False
            app._resume_from_index = None
            app._resume_job_name = None
            app._auto_reconnect_pending = False
            app._auto_reconnect_retry = 0
            app._auto_reconnect_next_ts = 0.0
            app._auto_reconnect_blocked = True
        if not app._user_disconnect:
            app._auto_reconnect_pending = True
            app._auto_reconnect_retry = 0
            app._auto_reconnect_delay = 3.0
            app._auto_reconnect_next_ts = 0.0
        app._user_disconnect = False
        app.throughput_var.set("TX: 0 B/s")
    apply_status_poll_profile(app)


def handle_ready_event(app, ready):
    app._grbl_ready = bool(ready)
    if not app._grbl_ready:
        app._status_seen = False
        app._alarm_locked = False
        app._alarm_message = ""
        if app.connected:
            disable_job_controls(app)
            app._set_manual_controls_enabled(False)
            if app._connected_port:
                app.status.config(text=f"Connected: {app._connected_port} (waiting for Grbl)")
        apply_status_poll_profile(app)
        return
    if app._alarm_locked:
        return
    if app.connected and app._connected_port:
        app.status.config(text=f"Connected: {app._connected_port}")
        try:
            app._send_manual("$G", "status")
        except Exception:
            pass
        try:
            app._send_manual("$$", "status")
        except Exception:
            pass
        if getattr(app, "_resume_after_disconnect", False) and not app._alarm_locked:
            app._resume_after_disconnect = False
            total_lines = (
                app._gcode_total_lines
                if getattr(app, "_gcode_streaming_mode", False)
                else len(app._last_gcode_lines)
            )
            if total_lines > 0:
                start_index = app._resume_from_index
                if start_index is None:
                    start_index = max(0, app._last_acked_index + 1)
                start_index = max(0, min(start_index, total_lines - 1))
                job_name = app._resume_job_name or os.path.basename(
                    getattr(app, "_last_gcode_path", "") or ""
                )
                label = f" '{job_name}'" if job_name else ""
                prompt = f"Resume interrupted job{label} from line {start_index + 1}?"
                if messagebox.askyesno("Resume job", prompt):
                    preamble, _ = app._build_resume_preamble(app._last_gcode_lines, start_index)
                    app._resume_from_line(start_index, preamble)
            app._resume_from_index = None
            app._resume_job_name = None
    apply_status_poll_profile(app)


def maybe_auto_reconnect(app):
    if app.connected or app._closing or (not app._auto_reconnect_pending):
        return
    if getattr(app, "_user_disconnect", False):
        return
    if getattr(app, "_auto_reconnect_blocked", False):
        return
    if app._connecting:
        return
    if not app._auto_reconnect_last_port:
        return
    try:
        if not bool(app.reconnect_on_open.get()):
            app._auto_reconnect_pending = False
            return
    except Exception:
        pass
    now = time.time()
    if now < app._auto_reconnect_next_ts:
        return
    ports = app.grbl.list_ports()
    if app._auto_reconnect_last_port not in ports:
        # If we've exceeded retries, allow a cool-down retry later.
        if app._auto_reconnect_retry >= app._auto_reconnect_max_retry:
            app._auto_reconnect_next_ts = now + max(30.0, app._auto_reconnect_delay)
            app._auto_reconnect_pending = True
        else:
            app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
        return
    app._auto_reconnect_last_attempt = now
    app.current_port.set(app._auto_reconnect_last_port)
    app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
    app._start_connect_worker(
        app._auto_reconnect_last_port,
        show_error=False,
        on_failure=app._handle_auto_reconnect_failure,
    )


def handle_auto_reconnect_failure(app, exc: Exception):
    now = time.time()
    app.ui_q.put(("log", f"[auto-reconnect] Attempt failed: {exc}"))
    app._auto_reconnect_retry += 1
    if app._auto_reconnect_retry > app._auto_reconnect_max_retry:
        app._auto_reconnect_delay = 30.0
    else:
        app._auto_reconnect_delay = min(30.0, app._auto_reconnect_delay * 1.5)
    app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
    app._auto_reconnect_pending = True


def effective_status_poll_interval(app) -> float:
    try:
        base = float(app.status_poll_interval.get())
    except Exception:
        base = STATUS_POLL_DEFAULT
    if base <= 0:
        base = STATUS_POLL_DEFAULT
    return base


def apply_status_poll_profile(app):
    interval = effective_status_poll_interval(app)
    app.grbl.set_status_poll_interval(interval)
