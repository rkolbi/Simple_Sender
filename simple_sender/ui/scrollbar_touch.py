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

from __future__ import annotations

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import tkinter as tk
from tkinter import ttk
from typing import Any, TypeGuard

_DRAG_STATE_ATTR = "_simple_sender_touch_scrollbar_drag_state"
_ACTIVE_SCROLLBAR: Any = None
_ACTIVE_DRAG_WATCHDOG_WIDGET: Any = None
_ACTIVE_DRAG_WATCHDOG_AFTER_ID: Any = None
_DRAG_WATCHDOG_INTERVAL_MS = 10

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
ScrollbarWidget = tk.Scrollbar | ttk.Scrollbar


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _is_scrollbar_widget(widget: Any) -> TypeGuard[ScrollbarWidget]:
    return isinstance(widget, (tk.Scrollbar, ttk.Scrollbar))


def _normalize_element_name(name: Any) -> str:
    return str(name or "").strip().lower()


def _scrollbar_orientation(widget: Any) -> str:
    try:
        orient = str(widget.cget("orient") or "vertical").strip().lower()
    except Exception:
        orient = "vertical"
    return "horizontal" if orient.startswith("h") else "vertical"


def _scrollbar_extent(widget: Any, orient: str) -> float:
    try:
        if orient == "horizontal":
            value = float(widget.winfo_width())
        else:
            value = float(widget.winfo_height())
    except Exception:
        value = 1.0
    return max(1.0, value)


def _pointer_fraction_from_widget_pointer(widget: Any, orient: str) -> float:
    extent = _scrollbar_extent(widget, orient)
    try:
        px = float(widget.winfo_pointerx() - widget.winfo_rootx())
        py = float(widget.winfo_pointery() - widget.winfo_rooty())
        coord = px if orient == "horizontal" else py
    except Exception:
        coord = 0.0
    if coord <= 0:
        return 0.0
    if coord >= extent:
        return 1.0
    return coord / extent


def _cancel_drag_watchdog() -> None:
    global _ACTIVE_DRAG_WATCHDOG_WIDGET
    global _ACTIVE_DRAG_WATCHDOG_AFTER_ID
    if _ACTIVE_DRAG_WATCHDOG_WIDGET is not None and _ACTIVE_DRAG_WATCHDOG_AFTER_ID is not None:
        try:
            _ACTIVE_DRAG_WATCHDOG_WIDGET.after_cancel(_ACTIVE_DRAG_WATCHDOG_AFTER_ID)
        except Exception:
            pass
    _ACTIVE_DRAG_WATCHDOG_WIDGET = None
    _ACTIVE_DRAG_WATCHDOG_AFTER_ID = None


def _apply_drag_from_pointer(widget: Any, state: dict[str, Any]) -> None:
    orient = str(state.get("orient", "vertical"))
    span = max(0.0, min(1.0, float(state.get("span", 0.0) or 0.0)))
    offset = float(state.get("offset", 0.0) or 0.0)
    pointer_frac = _pointer_fraction_from_widget_pointer(widget, orient)
    first = pointer_frac - offset
    max_first = max(0.0, 1.0 - span)
    if first < 0.0:
        first = 0.0
    elif first > max_first:
        first = max_first
    _set_scrollbar_position(widget, first)


def _schedule_drag_watchdog(widget: Any) -> None:
    global _ACTIVE_DRAG_WATCHDOG_WIDGET
    global _ACTIVE_DRAG_WATCHDOG_AFTER_ID
    if _ACTIVE_DRAG_WATCHDOG_WIDGET is widget and _ACTIVE_DRAG_WATCHDOG_AFTER_ID is not None:
        return
    _cancel_drag_watchdog()
    _ACTIVE_DRAG_WATCHDOG_WIDGET = widget

    def _tick() -> None:
        global _ACTIVE_DRAG_WATCHDOG_AFTER_ID
        if _ACTIVE_SCROLLBAR is not widget:
            _ACTIVE_DRAG_WATCHDOG_AFTER_ID = None
            return
        state = getattr(widget, _DRAG_STATE_ATTR, None)
        if not isinstance(state, dict):
            _ACTIVE_DRAG_WATCHDOG_AFTER_ID = None
            return
        _apply_drag_from_pointer(widget, state)
        try:
            _ACTIVE_DRAG_WATCHDOG_AFTER_ID = widget.after(_DRAG_WATCHDOG_INTERVAL_MS, _tick)
        except Exception:
            _ACTIVE_DRAG_WATCHDOG_AFTER_ID = None

    try:
        _ACTIVE_DRAG_WATCHDOG_AFTER_ID = widget.after(_DRAG_WATCHDOG_INTERVAL_MS, _tick)
    except Exception:
        _ACTIVE_DRAG_WATCHDOG_AFTER_ID = None


def _scrollbar_span(widget: Any) -> float:
    try:
        first, last = widget.get()
        span = float(last) - float(first)
    except Exception:
        span = 0.0
    return max(0.0, min(1.0, span))


def _set_scrollbar_position(widget: Any, first: float) -> None:
    try:
        command = str(widget.cget("command") or "").strip()
    except Exception:
        command = ""
    if not command:
        return
    clamped = max(0.0, min(1.0, float(first)))
    try:
        widget.tk.call(command, "moveto", f"{clamped:.6f}")
    except Exception as exc:
        _log_suppressed("Failed moving scrollbar via touch fallback handler", exc)


def _on_scrollbar_touch_press(event: Any):
    global _ACTIVE_SCROLLBAR
    widget = getattr(event, "widget", None)
    if not _is_scrollbar_widget(widget):
        _ACTIVE_SCROLLBAR = None
        return None
    try:
        element = widget.identify(int(getattr(event, "x", 0)), int(getattr(event, "y", 0)))
    except Exception:
        element = ""

    orient = _scrollbar_orientation(widget)
    span = _scrollbar_span(widget)
    pointer_frac = _pointer_fraction_from_widget_pointer(widget, orient)
    try:
        values = widget.get()
        first = values[0] if isinstance(values, tuple) and values else 0.0
        first_frac = float(first)
    except Exception:
        first_frac = 0.0
    normalized_element = _normalize_element_name(element)
    if normalized_element in {"slider", "thumb"}:
        offset = pointer_frac - first_frac
        consume_press = False
    else:
        offset = span / 2.0
        consume_press = True
    setattr(
        widget,
        _DRAG_STATE_ATTR,
        {
            "orient": orient,
            "span": span,
            "offset": offset,
        },
    )
    _ACTIVE_SCROLLBAR = widget
    _schedule_drag_watchdog(widget)
    if consume_press:
        max_first = max(0.0, 1.0 - span)
        first = pointer_frac - offset
        if first < 0.0:
            first = 0.0
        elif first > max_first:
            first = max_first
        _set_scrollbar_position(widget, first)
        return "break"
    return None


def _on_scrollbar_touch_motion(event: Any):
    global _ACTIVE_SCROLLBAR
    widget = getattr(event, "widget", None)
    if _is_scrollbar_widget(widget):
        active_widget = widget
    else:
        active_widget = _ACTIVE_SCROLLBAR
    if not _is_scrollbar_widget(active_widget):
        return None
    state = getattr(active_widget, _DRAG_STATE_ATTR, None)
    if not isinstance(state, dict):
        return None
    _apply_drag_from_pointer(active_widget, state)
    _schedule_drag_watchdog(active_widget)
    _ACTIVE_SCROLLBAR = active_widget
    return "break"


def _on_scrollbar_touch_release(event: Any):
    global _ACTIVE_SCROLLBAR
    widget = getattr(event, "widget", None)
    if not _is_scrollbar_widget(widget):
        _ACTIVE_SCROLLBAR = None
        _cancel_drag_watchdog()
        return None
    setattr(widget, _DRAG_STATE_ATTR, None)
    if _ACTIVE_SCROLLBAR is widget:
        _ACTIVE_SCROLLBAR = None
    _cancel_drag_watchdog()
    return None


def _on_any_touch_release(_event: Any):
    global _ACTIVE_SCROLLBAR
    _ACTIVE_SCROLLBAR = None
    _cancel_drag_watchdog()
    return None


def install_touch_scrollbar_support(app: Any) -> None:
    if bool(getattr(app, "_touch_scrollbar_support_installed", False)):
        return
    app._touch_scrollbar_support_installed = True
    for class_name in ("TScrollbar", "Scrollbar"):
        try:
            app.bind_class(class_name, "<ButtonPress-1>", _on_scrollbar_touch_press, add="+")
            app.bind_class(class_name, "<B1-Motion>", _on_scrollbar_touch_motion, add="+")
            app.bind_class(class_name, "<ButtonRelease-1>", _on_scrollbar_touch_release, add="+")
        except Exception as exc:
            _log_suppressed("Failed binding class-level touch scrollbar support", exc)
    try:
        app.bind_all("<ButtonRelease-1>", _on_any_touch_release, add="+")
    except Exception as exc:
        _log_suppressed("Failed binding global touch-release cleanup for scrollbar support", exc)

