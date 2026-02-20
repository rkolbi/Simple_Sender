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
import queue
import logging
from typing import Any, cast
from tkinter import messagebox

from .status import (
    _parse_modal_units,
    _parse_report_units_setting,
    handle_status_event,
)
from . import streaming as _event_router_streaming
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.dialogs.error_dialogs_ui import show_grbl_code_popup
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error
from simple_sender.types import UiEvent

logger = logging.getLogger(__name__)


_JOG_LIMIT_ERROR_HINT = (
    "Jog blocked by travel limits (error:15). "
    "Move away from axis limits or verify homing and $130-$132."
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _is_error_15(message: str | None) -> bool:
    if not message:
        return False
    return "error:15" in str(message).lower()


def _is_jog_source(source: str | None) -> bool:
    if not source:
        return False
    normalized = str(source).strip().lower()
    return normalized in {"joystick", "jog", "jog_hold", "jog_button"}


def set_streaming_lock(app: Any, locked: bool):
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except Exception as exc:
        _log_suppressed("Failed to update connect button state", exc)
    try:
        app.btn_refresh.config(state=state)
    except Exception as exc:
        _log_suppressed("Failed to update refresh button state", exc)
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except Exception as exc:
        _log_suppressed("Failed to update port combo state", exc)
    try:
        app.btn_unit_toggle.config(state=state)
    except Exception as exc:
        _log_suppressed("Failed to update unit toggle state", exc)


def handle_event(app: Any, evt: UiEvent):
    match evt:
        case ("conn", connected, port):
            handle_connection_event(app, cast(bool, connected), cast(str | None, port))
            return
        case ("ui_call", func, args, kwargs, result_q):
            handle_ui_call(app, func, args, kwargs, result_q)
            return
        case ("ui_post", func, args, kwargs):
            handle_ui_post(app, func, args, kwargs)
            return
        case ("macro_prompt", title, message, choices, cancel_label, result_q):
            handle_macro_prompt(app, title, message, choices, cancel_label, result_q)
            return
        case ("gcode_load_progress", token, done, total, label):
            handle_gcode_load_progress(app, token, done, total, label)
            return
        case ("streaming_validation_prompt", token, name, cleaned_lines, threshold, result_q):
            handle_streaming_validation_prompt(
                app,
                cast(int, token),
                cast(str, name),
                cast(int, cleaned_lines),
                cast(int, threshold),
                result_q,
            )
            return
        case ("gcode_loaded", *_):
            handle_gcode_loaded(app, evt)
            return
        case ("gcode_loaded_stream", *_):
            handle_gcode_loaded_stream(app, evt)
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines, cleaned_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                cast(int | None, cleaned_lines),
            )
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                None,
            )
            return
        case ("gcode_load_invalid_command", token, path, line_no, line_text):
            handle_gcode_load_invalid_command(
                app,
                cast(int, token),
                cast(str, path),
                cast(int | None, line_no),
                cast(str | None, line_text),
            )
            return
        case ("gcode_load_error", token, path, err):
            handle_gcode_load_error(app, token, path, err)
            return
        case ("log", message):
            app.streaming_controller.handle_log(message)
            return
        case ("log_tx", message):
            app.streaming_controller.handle_log_tx(message)
            return
        case ("log_rx", raw):
            raw = cast(str, raw)
            _parse_modal_units(app, raw)
            _parse_report_units_setting(app, raw)
            probe_controller = getattr(app, "probe_controller", None)
            if probe_controller is not None:
                probe_controller.handle_rx_line(raw)
            app.settings_controller.handle_line(raw)
            app.streaming_controller.handle_log_rx(raw)
            return
        case ("settings_dump_done",):
            try:
                app.settings_controller.handle_line("ok")
            except Exception as exc:
                _log_suppressed("Failed to process settings dump completion", exc)
            return
        case ("manual_error", msg, source):
            raw_msg = cast(str, msg)
            msg = annotate_grbl_error(raw_msg)
            label = str(source).strip() if source else ""
            prefix = f"GRBL error ({label})" if label else "GRBL error"
            show_jog_limit_hint = _is_jog_source(label) and (_is_error_15(raw_msg) or _is_error_15(msg))
            if getattr(app, "_homing_in_progress", False):
                app._homing_in_progress = False
                app._homing_state_seen = False
                try:
                    app.grbl.clear_watchdog_ignore("homing")
                except Exception as exc:
                    _log_suppressed("Failed clearing homing watchdog ignore after manual error", exc)
            try:
                if show_jog_limit_hint:
                    app.status.config(text=_JOG_LIMIT_ERROR_HINT)
                else:
                    app.status.config(text=f"{prefix}: {msg}")
            except Exception as exc:
                _log_suppressed("Failed to update status for manual error", exc)
            if show_jog_limit_hint and label.lower().startswith("joystick"):
                try:
                    if hasattr(app, "joystick_event_status"):
                        app.joystick_event_status.set(_JOG_LIMIT_ERROR_HINT)
                except Exception as exc:
                    _log_suppressed("Failed to update joystick status hint", exc)
            try:
                src_tag = f" ({label})" if label else ""
                app.streaming_controller.handle_log(f"[ERROR{src_tag}] {msg}")
            except Exception as exc:
                _log_suppressed("Failed to log manual error to console", exc)
            try:
                show_grbl_code_popup(app, msg)
            except Exception as exc:
                _log_suppressed("Failed showing GRBL error popup", exc)
            return
        case ("ready", is_ready):
            handle_ready_event(app, is_ready)
            return
        case ("alarm", msg):
            msg = annotate_grbl_alarm(cast(str, msg))
            if getattr(app, "_homing_in_progress", False):
                app._homing_in_progress = False
                app._homing_state_seen = False
                try:
                    app.grbl.clear_watchdog_ignore("homing")
                except Exception as exc:
                    _log_suppressed("Failed clearing homing watchdog ignore after alarm", exc)
            app._set_alarm_lock(True, msg)
            app.macro_executor.notify_alarm(msg)
            app._apply_status_poll_profile()
            try:
                show_grbl_code_popup(app, msg)
            except Exception as exc:
                _log_suppressed("Failed showing GRBL alarm popup", exc)
            return
        case ("status", line):
            handle_status_event(app, cast(str, line))
            return
        case ("buffer_fill", pct, used, window):
            app.streaming_controller.handle_buffer_fill(pct, used, window)
            return
        case ("throughput", bps):
            app.streaming_controller.handle_throughput(float(cast(float, bps)))
            return
        case ("stream_state", *_):
            handle_stream_state_event(app, evt)
            return
        case ("stream_interrupted", *_):
            handle_stream_interrupted(app, evt)
            return
        case ("stream_error", msg, err_idx, _err_line, _name):
            if err_idx is not None:
                try:
                    err_idx = int(cast(int | str, err_idx))
                except Exception:
                    err_idx = None
            if err_idx is not None and err_idx >= 0:
                app._last_error_index = err_idx
            try:
                app.status.config(text=f"Stream error: {msg}")
            except Exception as exc:
                _log_suppressed("Failed to update stream error status", exc)
            try:
                show_grbl_code_popup(app, cast(str | None, msg))
            except Exception as exc:
                _log_suppressed("Failed showing stream error popup", exc)
            return
        case ("stream_pause_reason", reason):
            if reason:
                try:
                    app.status.config(text=f"Paused ({reason})")
                except Exception as exc:
                    _log_suppressed("Failed to update pause reason status", exc)
            return
        case ("gcode_sent", idx, _line):
            app.streaming_controller.handle_gcode_sent(idx)
            return
        case ("gcode_acked", idx):
            app.streaming_controller.handle_gcode_acked(idx)
            return
        case ("progress", done, total):
            app.streaming_controller.handle_progress(done, total)
            return


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
        except Exception as log_exc:
            _log_suppressed("Failed to log macro prompt failure to UI console", log_exc)
        try:
            result_q.put_nowait(cancel_label)
        except queue.Full:
            pass


def handle_gcode_load_progress(app, token, done, total, label):
    if token != app._gcode_load_token:
        return
    try:
        app._set_gcode_loading_progress(done, total, label)
    except Exception as exc:
        _log_suppressed("Failed to update G-code loading progress", exc)


def handle_streaming_validation_prompt(
    app,
    token,
    name: str,
    cleaned_lines: int,
    threshold: int,
    result_q,
):
    if token != app._gcode_load_token:
        try:
            result_q.put_nowait(False)
        except queue.Full:
            pass
        return
    msg = (
        f"Validate streaming G-code for '{name}'?\n\n"
        f"Detected {cleaned_lines:,} non-empty lines (prompt at {threshold:,}).\n"
        "Validation adds another full scan and can take a while on huge files."
    )
    try:
        allow = messagebox.askyesno("Validate large file?", msg)
    except Exception as exc:
        _log_suppressed("Failed to show streaming validation prompt", exc)
        allow = False
    try:
        result_q.put_nowait(allow)
    except queue.Full:
        pass


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
        if cleanup_path and source is not None:
            try:
                source.close()
            except Exception as exc:
                _log_suppressed("Failed to close stale streaming source", exc)
            try:
                os.remove(cleanup_path)
            except OSError as exc:
                _log_suppressed("Failed to remove stale streamed temp file", exc)
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
        except OSError as exc:
            _log_suppressed("Failed removing autolevel temp restore file", exc)
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None


