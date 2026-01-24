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
from tkinter import messagebox

from simple_sender.ui.event_router_status import (
    _parse_modal_units,
    _parse_report_units_setting,
    handle_status_event,
)
from simple_sender.ui import event_router_streaming as _event_router_streaming
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error


def set_streaming_lock(app, locked: bool):
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except Exception:
        pass
    try:
        app.btn_refresh.config(state=state)
    except Exception:
        pass
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except Exception:
        pass
    try:
        app.btn_unit_toggle.config(state=state)
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
    if kind == "gcode_load_progress":
        handle_gcode_load_progress(app, evt[1], evt[2], evt[3], evt[4])
        return
    if kind == "streaming_validation_prompt":
        handle_streaming_validation_prompt(app, evt[1], evt[2], evt[3], evt[4], evt[5])
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
    if kind == "gcode_load_invalid_command":
        handle_gcode_load_invalid_command(app, evt[1], evt[2], evt[3], evt[4])
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
        probe_controller = getattr(app, "probe_controller", None)
        if probe_controller is not None:
            probe_controller.handle_rx_line(raw)
        app.settings_controller.handle_line(raw)
        app.streaming_controller.handle_log_rx(raw)
        return
    if kind == "settings_dump_done":
        try:
            app.settings_controller.handle_line("ok")
        except Exception:
            pass
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
                app.grbl.clear_watchdog_ignore("homing")
            except Exception:
                pass
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
            try:
                app.grbl.clear_watchdog_ignore("homing")
            except Exception:
                pass
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
        err_idx = evt[2] if len(evt) > 2 else None
        if err_idx is not None:
            try:
                err_idx = int(err_idx)
            except Exception:
                err_idx = None
        if err_idx is not None and err_idx >= 0:
            app._last_error_index = err_idx
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


def handle_stream_state_event(app, evt):
    _event_router_streaming.job_controls_ready = job_controls_ready
    _event_router_streaming.set_run_resume_from = set_run_resume_from
    return _event_router_streaming.handle_stream_state_event(app, evt)


def handle_stream_interrupted(app, evt):
    return _event_router_streaming.handle_stream_interrupted(app, evt)


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


def handle_gcode_load_progress(app, token, done, total, label):
    if token != app._gcode_load_token:
        return
    try:
        app._set_gcode_loading_progress(done, total, label)
    except Exception:
        pass


def handle_streaming_validation_prompt(
    app,
    token,
    name: str,
    cleaned_lines: int,
    threshold: int,
    result_q,
):
    if token != app._gcode_load_token:
        if result_q.empty():
            result_q.put(False)
        return
    msg = (
        f"Validate streaming G-code for '{name}'?\n\n"
        f"Detected {cleaned_lines:,} non-empty lines (prompt at {threshold:,}).\n"
        "Validation adds another full scan and can take a while on huge files."
    )
    try:
        allow = messagebox.askyesno("Validate large file?", msg)
    except Exception:
        allow = False
    if result_q.empty():
        result_q.put(allow)


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
        source = evt[3] if len(evt) > 3 else None
        cleanup_path = getattr(source, "_cleanup_path", None) if source is not None else None
        if cleanup_path:
            try:
                source.close()
            except Exception:
                pass
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
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
    _clear_autolevel_restore(app)
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


def handle_gcode_load_invalid_command(
    app,
    token,
    _path,
    line_no: int | None,
    line_text: str | None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("No file loaded")
    text = (line_text or "").strip() or "$"
    msg = "GRBL system commands ($...) are not allowed inside G-code jobs."
    if line_no is not None:
        msg += f"\nFirst at line {line_no}: {text}"
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def handle_gcode_load_error(app, token, _path, err):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("No file loaded")
    messagebox.showerror("Open G-code", f"Failed to read file:\n{err}")
    app.status.config(text="G-code load failed")


def _clear_autolevel_restore(app) -> None:
    restore = getattr(app, "_auto_level_restore", None)
    if not isinstance(restore, dict):
        return
    app._auto_level_restore = None
    leveled_path = restore.get("leveled_path")
    if restore.get("leveled_temp") and leveled_path:
        try:
            os.remove(leveled_path)
        except OSError:
            pass
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None


