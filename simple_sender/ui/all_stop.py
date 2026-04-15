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

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import tkinter as tk

from simple_sender.ui.job_setup_state import invalidate_job_setup_state
from simple_sender.utils.constants import (
    JOG_PANEL_ALL_STOP_OFFSET_FALLBACK_PX,
    JOG_PANEL_ALL_STOP_OFFSET_IN,
)

ALL_STOP_POSITION_RETRY_MS = 50
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _cancel_machine_driving_tasks(app) -> None:
    reason = "Canceled by ALL STOP."
    try:
        grbl = getattr(app, "grbl", None)
        if grbl is not None:
            jog_cancel = getattr(grbl, "jog_cancel", None)
            if callable(jog_cancel):
                jog_cancel()
            cancel_pending_jogs = getattr(grbl, "cancel_pending_jogs", None)
            if callable(cancel_pending_jogs):
                cancel_pending_jogs()
            complete_tool_change = getattr(grbl, "complete_stream_tool_change", None)
            if callable(complete_tool_change):
                complete_tool_change(False, reason)
    except Exception as exc:
        _log_suppressed("Failed canceling GRBL queued motion/tool-change during ALL STOP", exc)
    try:
        macro_executor = getattr(app, "macro_executor", None)
        cancel_macro = getattr(macro_executor, "cancel_macro", None)
        if callable(cancel_macro):
            cancel_macro(reason=reason)
    except Exception as exc:
        _log_suppressed("Failed canceling active macro during ALL STOP", exc)
    try:
        auto_level_runner = getattr(app, "auto_level_runner", None)
        cancel_probe = getattr(auto_level_runner, "cancel", None)
        if callable(cancel_probe):
            cancel_probe()
    except Exception as exc:
        _log_suppressed("Failed canceling auto-level probing during ALL STOP", exc)


def all_stop_action(app):
    try:
        app._stop_joystick_hold()
    except Exception as exc:
        _log_suppressed("Failed stopping joystick hold before ALL STOP action", exc)
    _cancel_machine_driving_tasks(app)
    if not app._require_grbl_connection():
        return
    try:
        if hasattr(app, "_stop_job_accessories"):
            app._stop_job_accessories("job_all_stop")
    except Exception as exc:
        _log_suppressed("Failed stopping Kasa job accessories during ALL STOP", exc)
    mode = app.all_stop_mode.get()
    action_applied = False
    reset_applied = False
    stop_applied = False
    if mode in {"reset", "stop_reset"}:
        stop_applied = bool(app.grbl.stop_stream())
        action_applied = bool(stop_applied)
    if mode == "reset":
        reset_applied = bool(app.grbl.reset())
        action_applied = bool(reset_applied)
    elif mode == "stop_reset":
        stop_stream_resets = False
        stop_stream_resets_checker = getattr(app.grbl, "stop_stream_performs_reset", None)
        if callable(stop_stream_resets_checker):
            try:
                stop_stream_resets = bool(stop_stream_resets_checker())
            except Exception as exc:
                _log_suppressed(
                    "Failed checking whether stop_stream already performs reset",
                    exc,
                )
                stop_stream_resets = True
        if not stop_stream_resets:
            reset_applied = bool(app.grbl.reset())
            action_applied = bool(reset_applied) or bool(action_applied)
        elif not action_applied:
            reset_applied = bool(app.grbl.reset())
            action_applied = bool(reset_applied)
    else:
        action_applied = bool(app.grbl.stop_stream())
    if action_applied:
        if mode == "reset":
            if reset_applied:
                invalidate_job_setup_state(app)
            else:
                action_applied = False
        else:
            invalidate_job_setup_state(app)
    if action_applied:
        return
    try:
        app.status.config(text="ALL STOP warning: controller did not accept stop/reset")
    except Exception:
        pass
    try:
        app.ui_q.put(("log", "[all stop] Stop/reset command was not sent; setup state was preserved."))
    except Exception:
        pass


def all_stop_gcode_label(app) -> str:
    return "Stop stream + Ctrl-X"


def position_all_stop_offset(app, event=None):
    _ = event
    slot = getattr(app, "_all_stop_slot", None)
    btn = getattr(app, "btn_all_stop", None)
    if not slot or not btn:
        return
    try:
        if not slot.winfo_exists():
            return
    except tk.TclError:
        return
    if not slot.winfo_ismapped():
        app.after(ALL_STOP_POSITION_RETRY_MS, app._position_all_stop_offset)
        return
    offset = getattr(app, "_all_stop_offset_px", None)
    if offset is None:
        try:
            offset = int(app.winfo_fpixels(f"{JOG_PANEL_ALL_STOP_OFFSET_IN}i"))
        except tk.TclError:
            offset = JOG_PANEL_ALL_STOP_OFFSET_FALLBACK_PX
        app._all_stop_offset_px = offset
    x = slot.winfo_x() - offset
    if x < 0:
        x = 0
    y = slot.winfo_y()
    btn.place(in_=slot.master, x=x, y=y)
    try:
        btn.tk.call("raise", btn._w)
    except tk.TclError as exc:
        _log_suppressed("Failed raising ALL STOP button after placement", exc)

