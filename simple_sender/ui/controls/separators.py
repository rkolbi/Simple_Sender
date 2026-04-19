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
import tkinter as tk
from tkinter import ttk
from typing import Any

from simple_sender.ui.theme_helpers import register_theme_refresh, unregister_theme_refresh
from simple_sender.utils.log_suppressed import log_suppressed_exception

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()

MAIN_WINDOW_SUBTLE_SEPARATOR_VISIBILITY = 0.40
MAIN_WINDOW_SUBTLE_SEPARATOR_PADX = 8


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _blend_hex_colors(base: str, target: str, factor: float) -> str:
    base = str(base or "").lstrip("#")
    target = str(target or "").lstrip("#")
    if len(base) != 6 or len(target) != 6:
        return f"#{base}" if base else f"#{target}" if target else "#3c3c3c"
    br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
    tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
    r = int(br + (tr - br) * factor)
    g = int(bg + (tg - bg) * factor)
    b = int(bb + (tb - bb) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _resolve_separator_palette(app: Any, widget: Any) -> tuple[str, str]:
    style_obj = getattr(app, "style", None)
    if style_obj is not None:
        style = style_obj
    else:
        try:
            style = ttk.Style()
        except Exception as exc:
            _log_suppressed("Failed creating ttk.Style for main-window separators", exc)
            return "#3c3c3c", "#f0f0f0"

    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    try:
        parent = getattr(widget, "master", None)
        background = ""
        parent_style = ""
        if parent is not None:
            try:
                parent_style = str(parent.cget("style") or "").strip()
            except Exception:
                parent_style = ""
            if parent_style:
                try:
                    background = str(style.lookup(parent_style, "background") or "").strip()
                except Exception:
                    background = ""
            if not background:
                try:
                    background = str(parent.cget("background") or "").strip()
                except Exception:
                    background = ""
        if not background:
            background = (
                str(style.lookup("TFrame", "background") or "").strip()
                or str(palette.get("panel_bg") or "").strip()
                or str(palette.get("bg") or "").strip()
                or str(style.lookup("TLabel", "background") or "").strip()
            )
        if not background:
            background = str(getattr(app, "cget", lambda _key: "")("bg") or "").strip()
        if not background:
            background = "#f0f0f0"

        base_separator = (
            str(style.lookup("TLabelframe", "bordercolor") or "").strip()
            or str(style.lookup("TSeparator", "background") or "").strip()
            or str(palette.get("border") or "").strip()
            or "#3c3c3c"
        )
        separator_bg = _blend_hex_colors(
            base_separator,
            background,
            1.0 - MAIN_WINDOW_SUBTLE_SEPARATOR_VISIBILITY,
        )
        return separator_bg, background
    except Exception as exc:
        _log_suppressed("Failed resolving main-window separator palette", exc)
        return "#3c3c3c", "#f0f0f0"


def apply_main_window_subtle_separator_theme(
    app: Any,
    widget: Any,
    *,
    orient: str = "vertical",
) -> str:
    line_color, background = _resolve_separator_palette(app, widget)
    try:
        if str(orient).strip().lower() == "horizontal":
            widget.configure(
                background=line_color,
                height=1,
                borderwidth=0,
                highlightthickness=0,
                relief="flat",
                takefocus=0,
            )
        else:
            widget.configure(
                background=line_color,
                width=1,
                borderwidth=0,
                highlightthickness=0,
                relief="flat",
                takefocus=0,
            )
    except Exception as exc:
        _log_suppressed("Failed applying main-window separator theme", exc)
    return background


def bind_main_window_subtle_separator_theme(
    app: Any,
    widget: Any,
    *,
    orient: str = "vertical",
) -> None:
    callback = getattr(widget, "_simple_sender_separator_theme_refresh", None)
    if not callable(callback):
        def callback(widget=widget) -> None:
            apply_main_window_subtle_separator_theme(app, widget, orient=orient)
        try:
            widget._simple_sender_separator_theme_refresh = callback
        except Exception:
            pass
        register_theme_refresh(app, callback)

        def _cleanup(event=None, *, widget=widget, callback=callback) -> None:
            if event is not None and getattr(event, "widget", None) is not widget:
                return
            unregister_theme_refresh(app, callback)
            try:
                widget._simple_sender_separator_theme_refresh = None
            except Exception:
                pass

        try:
            widget.bind("<Destroy>", _cleanup, add="+")
        except Exception as exc:
            _log_suppressed("Failed binding main-window separator cleanup handler", exc)
    callback()


def create_main_window_subtle_separator(
    parent: Any,
    app: Any,
    *,
    orient: str = "vertical",
) -> tk.Frame:
    kwargs: dict[str, Any] = {
        "borderwidth": 0,
        "highlightthickness": 0,
        "relief": "flat",
        "takefocus": 0,
    }
    if str(orient).strip().lower() == "horizontal":
        kwargs["height"] = 1
    else:
        kwargs["width"] = 1
    widget = tk.Frame(parent, **kwargs)
    bind_main_window_subtle_separator_theme(app, widget, orient=orient)
    return widget
