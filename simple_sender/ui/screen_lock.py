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

from tkinter import messagebox
from typing import Any


SCREEN_LOCK_BINDTAG = "SimpleSender.ScreenLockGuard"
SCREEN_LOCK_EVENTS = (
    "<ButtonPress>",
    "<ButtonRelease>",
    "<Double-Button-1>",
    "<B1-Motion>",
    "<MouseWheel>",
    "<Button-4>",
    "<Button-5>",
    "<KeyPress>",
    "<KeyRelease>",
)


def _install_bindtag(app: Any, widget: Any) -> None:
    tag = getattr(app, "_screen_lock_bindtag", SCREEN_LOCK_BINDTAG)
    try:
        tags = tuple(widget.bindtags())
    except Exception:
        return
    if tag in tags:
        return
    try:
        widget.bindtags((tag,) + tags)
    except Exception:
        return


def _install_bindtag_recursive(app: Any, widget: Any) -> None:
    _install_bindtag(app, widget)
    try:
        children = widget.winfo_children()
    except Exception:
        children = []
    for child in children:
        _install_bindtag_recursive(app, child)


def _is_unlock_widget(app: Any, widget: Any) -> bool:
    unlock = getattr(app, "btn_screen_lock", None)
    if unlock is None or widget is None:
        return False
    current = widget
    while current is not None:
        if current is unlock:
            return True
        try:
            parent_name = current.winfo_parent()
        except Exception:
            return False
        if not parent_name:
            return False
        try:
            current = current.nametowidget(parent_name)
        except Exception:
            return False
    return False


def refresh_screen_lock_toggle_text(app: Any) -> None:
    btn = getattr(app, "btn_screen_lock", None)
    if btn is None:
        return
    try:
        locked = bool(getattr(app, "_screen_lock_active", False))
    except Exception:
        locked = False
    try:
        btn.config(text="Unlock" if locked else "Lock")
    except Exception:
        return


def on_screen_lock_event(app: Any, event: Any):
    if not bool(getattr(app, "_screen_lock_active", False)):
        return None
    widget = getattr(event, "widget", None)
    if _is_unlock_widget(app, widget):
        return None
    return "break"


def on_screen_lock_widget_mapped(app: Any, event: Any):
    widget = getattr(event, "widget", None)
    if widget is None:
        return None
    _install_bindtag(app, widget)
    return None


def init_screen_lock_guard(app: Any) -> None:
    if bool(getattr(app, "_screen_lock_guard_ready", False)):
        return
    app._screen_lock_guard_ready = True
    app._screen_lock_active = bool(getattr(app, "_screen_lock_active", False))
    app._screen_lock_bindtag = SCREEN_LOCK_BINDTAG
    for sequence in SCREEN_LOCK_EVENTS:
        try:
            app.bind_class(
                SCREEN_LOCK_BINDTAG,
                sequence,
                app._on_screen_lock_event,
                add="+",
            )
        except Exception:
            continue
    try:
        app.bind_all("<Map>", app._on_screen_lock_widget_mapped, add="+")
    except Exception:
        pass
    _install_bindtag_recursive(app, app)
    refresh_screen_lock_toggle_text(app)


def toggle_screen_lock(app: Any) -> None:
    active = bool(getattr(app, "_screen_lock_active", False))
    if active:
        if not bool(
            messagebox.askyesno(
                "Unlock screen",
                "Unlock the screen and re-enable operator input?",
            )
        ):
            return
    else:
        if not bool(
            messagebox.askyesno(
                "Lock screen",
                "Lock the screen and ignore operator input until unlocked?",
            )
        ):
            return
    app._screen_lock_active = not active
    try:
        app._clear_key_sequence_buffer()
    except Exception:
        pass
    if app._screen_lock_active:
        try:
            app._stop_joystick_hold()
        except Exception:
            pass
    refresh_screen_lock_toggle_text(app)
    try:
        app.status.config(
            text="Screen locked: all operator input disabled"
            if app._screen_lock_active
            else "Screen unlocked"
        )
    except Exception:
        pass
