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


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _default_state_label_fg(app) -> str:
    return str(getattr(app, "_state_default_fg", "") or "#000000")


def apply_state_fg(app, color: str | None, fg: str | None = None):
    target = color if color else (app._state_default_bg or "#f0f0f0")
    default_bg = str(getattr(app, "_state_default_bg", "") or "")
    if fg is not None:
        text_color = fg
    elif (color is None) or (default_bg and str(color) == default_bg):
        text_color = _default_state_label_fg(app)
    else:
        text_color = "#000000"
    lbl = getattr(app, "machine_state_label", None)
    if not lbl:
        return
    try:
        lbl.config(background=target, foreground=text_color)
    except tk.TclError as exc:
        _log_suppressed("Failed applying machine-state label flash colors", exc)


def ensure_state_label_width(app, text: str | None) -> None:
    lbl = getattr(app, "machine_state_label", None)
    if not lbl:
        return
    text = str(text or "")
    if not text:
        return
    try:
        current = int(getattr(app, "_machine_state_max_chars", 0) or 0)
    except Exception:
        current = 0
    needed = len(text)
    width = max(current, needed)
    if width != current:
        try:
            app._machine_state_max_chars = width
        except Exception as exc:
            _log_suppressed("Failed storing machine-state max-width cache", exc)
    if width <= 0:
        return
    try:
        applied_width = int(getattr(app, "_machine_state_label_width", 0) or 0)
    except Exception:
        applied_width = 0
    if applied_width == width:
        return
    try:
        lbl.config(width=width)
        app._machine_state_label_width = int(width)
    except tk.TclError as exc:
        _log_suppressed("Failed applying machine-state label width", exc)


def cancel_state_flash(app):
    if app._state_flash_after_id:
        try:
            app.after_cancel(app._state_flash_after_id)
        except Exception as exc:
            _log_suppressed("Failed canceling machine-state flash timer", exc)
    app._state_flash_after_id = None
    app._state_flash_color = None
    app._state_flash_on = False


def toggle_state_flash(app):
    if not app._state_flash_color:
        return
    app._state_flash_on = not app._state_flash_on
    color = app._state_flash_color if app._state_flash_on else (app._state_default_bg or "#f0f0f0")
    apply_state_fg(app, color)
    app._state_flash_after_id = app.after(500, lambda: toggle_state_flash(app))


def start_state_flash(app, color: str):
    cancel_state_flash(app)
    app._state_flash_color = color
    toggle_state_flash(app)


def update_state_highlight(app, state: str | None):
    text = str(state or "").lower()
    if not text:
        cancel_state_flash(app)
        apply_state_fg(app, None)
        return
    if text.startswith("run"):
        cancel_state_flash(app)
        apply_state_fg(app, "#00c853")
    elif text.startswith("idle"):
        cancel_state_flash(app)
        apply_state_fg(app, "#2196f3")
    elif text.startswith("connected"):
        cancel_state_flash(app)
        apply_state_fg(app, "#607d8b", fg="#ffffff")
    elif text.startswith("disconnected"):
        cancel_state_flash(app)
        apply_state_fg(app, "#2b2b2b", fg="#ffffff")
    elif text.startswith(("restore failed", "reload job")):
        cancel_state_flash(app)
        apply_state_fg(app, "#ef6c00", fg="#ffffff")
    elif text.startswith(("home", "homing")):
        cancel_state_flash(app)
        apply_state_fg(app, "#7e57c2")
    elif text.startswith("hold"):
        cancel_state_flash(app)
        apply_state_fg(app, "#ffc107")
    elif text.startswith("jog"):
        cancel_state_flash(app)
        apply_state_fg(app, "#4fc3f7")
    elif text.startswith("check"):
        cancel_state_flash(app)
        apply_state_fg(app, "#ffb74d")
    elif text.startswith("door"):
        start_state_flash(app, "#ff8a65")
    elif text.startswith("alarm"):
        start_state_flash(app, "#ff5252")
    elif text.startswith("sleep"):
        cancel_state_flash(app)
        apply_state_fg(app, "#b0bec5")
    else:
        cancel_state_flash(app)
        apply_state_fg(app, None)

