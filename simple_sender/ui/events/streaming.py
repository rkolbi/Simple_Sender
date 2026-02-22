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

from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from


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
        app._stream_done_pending_idle = False
        total = evt[2] if len(evt) > 2 else None
        app.progress_pct.set(0)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        set_run_resume_from(app, job_controls_ready(app, bool(total)))
        app._set_manual_controls_enabled(
            app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
        )
        app._set_streaming_lock(False)
    elif st == "running":
        app._stream_done_pending_idle = False
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
        app._stream_done_pending_idle = False
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
            state_text = str(getattr(app, "_machine_state_text", "") or "").lower()
            motion_active = bool(state_text) and not state_text.startswith("idle")
            app._stream_done_pending_idle = bool(motion_active)
            app.progress_pct.set(99 if motion_active else 100)
        else:
            app._stream_done_pending_idle = False
            app.progress_pct.set(0)
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        if st == "done" and app._stream_done_pending_idle:
            app._set_manual_controls_enabled(False)
            app._set_streaming_lock(True)
        else:
            set_run_resume_from(app, job_controls_ready(app))
            app._set_manual_controls_enabled(
                app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
            )
            app._set_streaming_lock(False)
    elif st == "error":
        app._stream_done_pending_idle = False
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        set_run_resume_from(app, job_controls_ready(app))
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.status.config(text=f"Stream error: {evt[2]}")
        app._set_manual_controls_enabled(
            app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
        )
        app._set_streaming_lock(False)
    elif st == "alarm":
        app._stream_done_pending_idle = False
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
    stream_busy = st in ("running", "paused") or bool(getattr(app, "_stream_done_pending_idle", False))
    if stream_busy:
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
