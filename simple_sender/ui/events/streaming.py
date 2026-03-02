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
import logging
import tkinter as tk

from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.stream_completion import should_defer_done_until_idle
from .stream_state_ui import apply_stream_busy_state, restore_controls_after_stream

logger = logging.getLogger(__name__)


def _log_stream_ui_issue(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc)


def _stop_job_accessories_for_state(app, state: str) -> None:
    try:
        if hasattr(app, "_stop_job_accessories"):
            app._stop_job_accessories(f"job_{state}")
    except Exception as exc:
        _log_stream_ui_issue("Failed stopping Kasa job accessories on stream-state transition", exc)


def _refresh_stream_busy_ui(app) -> None:
    if hasattr(app, "_update_quick_button_visibility"):
        try:
            app._update_quick_button_visibility()
        except Exception as exc:
            _log_stream_ui_issue("Failed refreshing quick-button visibility after stream-state transition", exc)
    if hasattr(app, "_refresh_toolbar_action_focus"):
        try:
            app._refresh_toolbar_action_focus()
        except Exception as exc:
            _log_stream_ui_issue("Failed refreshing toolbar action focus after stream-state transition", exc)


def handle_stream_state_event(app, evt):
    st = evt[1]
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        try:
            perf_monitor.note_stream_state(str(st))
        except Exception as exc:
            _log_stream_ui_issue("Failed forwarding stream-state transition to performance monitor", exc)
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
        except (AttributeError, tk.TclError):
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
            except (AttributeError, tk.TclError, TypeError) as exc:
                logger.debug("Failed updating streaming status label: %s", exc)
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
        restore_controls_after_stream(
            app,
            loaded_total=total,
            job_ready_hook=job_controls_ready,
            set_run_resume_hook=set_run_resume_from,
        )
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
        _stop_job_accessories_for_state(app, st)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        if st == "done":
            defer_done = should_defer_done_until_idle(app, now_ts=now)
            app._stream_done_pending_idle = bool(defer_done)
            app.progress_pct.set(99 if defer_done else 100)
        else:
            app._stream_done_pending_idle = False
            app.progress_pct.set(0)
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        if st == "done" and app._stream_done_pending_idle:
            app._set_manual_controls_enabled(False)
            app._set_streaming_lock(True)
        else:
            restore_controls_after_stream(
                app,
                job_ready_hook=job_controls_ready,
                set_run_resume_hook=set_run_resume_from,
            )
    elif st == "error":
        _stop_job_accessories_for_state(app, st)
        app._stream_done_pending_idle = False
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        restore_controls_after_stream(
            app,
            job_ready_hook=job_controls_ready,
            set_run_resume_hook=set_run_resume_from,
        )
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.status.config(text=f"Stream error: {evt[2]}")
    elif st == "alarm":
        _stop_job_accessories_for_state(app, st)
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
    apply_stream_busy_state(app, stream_busy, log_hook=_log_stream_ui_issue)
    _refresh_stream_busy_ui(app)
    if hasattr(app, "_update_joystick_polling_state"):
        try:
            app._update_joystick_polling_state()
        except Exception as exc:
            _log_stream_ui_issue("Failed updating joystick polling state after stream-state transition", exc)
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
