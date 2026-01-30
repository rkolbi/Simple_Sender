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

"""Event routing package exports."""

from . import router as _router
from . import status as _status

messagebox = _router.messagebox
job_controls_ready = _router.job_controls_ready
set_run_resume_from = _router.set_run_resume_from


def handle_event(app, evt):
    _router.messagebox = messagebox
    return _router.handle_event(app, evt)


def handle_ui_call(app, func, args, kwargs, result_q):
    return _router.handle_ui_call(app, func, args, kwargs, result_q)


def handle_ui_post(app, func, args, kwargs):
    return _router.handle_ui_post(app, func, args, kwargs)


def handle_macro_prompt(app, title, message, choices, cancel_label, result_q):
    return _router.handle_macro_prompt(app, title, message, choices, cancel_label, result_q)


def handle_gcode_load_progress(app, token, done, total, label):
    return _router.handle_gcode_load_progress(app, token, done, total, label)


def handle_streaming_validation_prompt(app, token, name, cleaned_lines, threshold, result_q):
    _router.messagebox = messagebox
    return _router.handle_streaming_validation_prompt(
        app, token, name, cleaned_lines, threshold, result_q
    )


def handle_gcode_loaded(app, evt):
    return _router.handle_gcode_loaded(app, evt)


def handle_gcode_loaded_stream(app, evt):
    return _router.handle_gcode_loaded_stream(app, evt)


def handle_gcode_load_invalid(
    app,
    token,
    path,
    too_long,
    first_idx,
    first_len,
    total_lines=None,
    cleaned_lines=None,
):
    _router.messagebox = messagebox
    return _router.handle_gcode_load_invalid(
        app,
        token,
        path,
        too_long,
        first_idx,
        first_len,
        total_lines,
        cleaned_lines,
    )


def handle_gcode_load_invalid_command(app, idx, name, line_no, command):
    _router.messagebox = messagebox
    return _router.handle_gcode_load_invalid_command(app, idx, name, line_no, command)


def handle_gcode_load_error(app, idx, name, message):
    _router.messagebox = messagebox
    return _router.handle_gcode_load_error(app, idx, name, message)


def handle_status_event(app, raw):
    return _status.handle_status_event(app, raw)


def handle_stream_state_event(app, evt):
    _router.job_controls_ready = job_controls_ready
    _router.set_run_resume_from = set_run_resume_from
    return _router.handle_stream_state_event(app, evt)


def handle_stream_interrupted(app, evt):
    return _router.handle_stream_interrupted(app, evt)


def set_streaming_lock(app, locked: bool):
    return _router.set_streaming_lock(app, locked)

__all__ = [
    "handle_event",
    "handle_gcode_load_error",
    "handle_gcode_load_invalid",
    "handle_gcode_load_invalid_command",
    "handle_gcode_loaded",
    "handle_gcode_loaded_stream",
    "handle_gcode_load_progress",
    "handle_macro_prompt",
    "handle_status_event",
    "handle_stream_interrupted",
    "handle_stream_state_event",
    "handle_streaming_validation_prompt",
    "handle_ui_call",
    "handle_ui_post",
    "job_controls_ready",
    "messagebox",
    "set_run_resume_from",
    "set_streaming_lock",
]
