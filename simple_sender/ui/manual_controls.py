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

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_TRANSIENT_TTK_STATES_TO_CLEAR = ("!pressed", "!selected", "!active")


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def clear_widget_transient_state(widget) -> None:
    state_fn = getattr(widget, "state", None)
    if callable(state_fn):
        try:
            state_fn(list(_TRANSIENT_TTK_STATES_TO_CLEAR))
        except tk.TclError:
            pass
    try:
        relief = str(widget.cget("relief")).strip().lower()
    except Exception:
        relief = ""
    if relief == "sunken":
        try:
            widget.config(relief="raised")
        except Exception:
            pass


def _manual_control_state(app, widget, enabled: bool, connected: bool) -> str:
    if getattr(widget, "_force_disabled", False):
        return "disabled"
    if not connected:
        return "normal" if widget in app._offline_controls else "disabled"
    if not enabled:
        stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
        stream_busy = bool(getattr(app, "_stream_done_pending_idle", False)) or stream_state in {
            "running",
            "paused",
        }
        if widget is getattr(app, "btn_all_stop", None):
            return "normal"
        if widget in app._override_controls:
            return "normal"
        if (
            not stream_busy
            and widget in {getattr(app, "btn_open", None), getattr(app, "btn_clear", None)}
        ):
            return "normal"
        return "disabled"
    return "normal"


def _widget_state(widget) -> str | None:
    try:
        return str(widget.cget("state")).strip().lower()
    except tk.TclError:
        return None


def _set_widget_state_if_needed(app, widget, state: str) -> None:
    cache = getattr(app, "_manual_control_state_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(app, "_manual_control_state_cache", cache)
    cached_state = cache.get(widget)
    if cached_state == state:
        return
    if cached_state is None:
        current = _widget_state(widget)
        if current == state:
            cache[widget] = state
            return
    # Clear transient button visuals before disabling to avoid sticky states.
    if state != "normal":
        clear_widget_transient_state(widget)
    widget.config(state=state)
    cache[widget] = state


def set_manual_controls_enabled(app, enabled: bool):
    was_enabled = bool(getattr(app, "_manual_controls_last_enabled", False))
    if getattr(app, "_alarm_locked", False):
        for w in app._manual_controls:
            try:
                if w is getattr(app, "btn_all_stop", None):
                    continue
                if w is getattr(app, "btn_home_mpos", None):
                    _set_widget_state_if_needed(app, w, "normal")
                    continue
                if w is getattr(app, "btn_unlock_mpos", None):
                    _set_widget_state_if_needed(app, w, "normal")
                    continue
                if w is getattr(app, "btn_unlock_top", None):
                    _set_widget_state_if_needed(app, w, "normal")
                    continue
                _set_widget_state_if_needed(app, w, "disabled")
            except tk.TclError as exc:
                _log_suppressed("Failed disabling manual control while alarm lock active", exc)
        app._manual_controls_last_enabled = False
        return
    connected = bool(getattr(app, "connected", False))
    for w in app._manual_controls:
        try:
            _set_widget_state_if_needed(app, w, _manual_control_state(app, w, enabled, connected))
        except tk.TclError as exc:
            _log_suppressed("Failed setting manual control state", exc)
    now_enabled = bool(enabled and connected)
    app._manual_controls_last_enabled = now_enabled
    if now_enabled and (not was_enabled):
        app._set_unit_mode(app.unit_mode.get())
        app._set_step_xy(app.step_xy.get())
        app._set_step_z(app.step_z.get())

