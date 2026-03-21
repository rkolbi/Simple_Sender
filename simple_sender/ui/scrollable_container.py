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

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk
from typing import Any

_WHEEL_DELTA_UNIT = 120


def is_descendant(widget, ancestor) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "master", None)
    return False


@dataclass
class ScrollableContainerRefs:
    host: Any
    content: Any
    canvas: Any | None = None
    scrollbar: Any | None = None
    content_window: Any | None = None
    scroll_enabled: bool = False

    def update_scrollregion(self, _event=None) -> None:
        if self.canvas is None:
            return
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def resize_width(self, event) -> None:
        if self.canvas is None or self.content_window is None:
            return
        self.canvas.itemconfig(self.content_window, width=event.width)


def _supports_scrollable_widgets(host, canvas, scrollbar, content) -> bool:
    if not all(hasattr(host, name) for name in ("grid_columnconfigure", "grid_rowconfigure")):
        return False
    if not all(hasattr(canvas, name) for name in ("grid", "configure", "create_window", "bind", "itemconfig", "bbox", "yview")):
        return False
    if not all(hasattr(scrollbar, name) for name in ("grid", "set")):
        return False
    if not hasattr(content, "bind"):
        return False
    return True


def _place_plain_content(host, content) -> ScrollableContainerRefs:
    if hasattr(host, "grid_columnconfigure"):
        host.grid_columnconfigure(0, weight=1)
    if hasattr(host, "grid_rowconfigure"):
        host.grid_rowconfigure(0, weight=1)
    if hasattr(content, "grid"):
        content.grid(row=0, column=0, sticky="nsew")
    elif hasattr(content, "pack"):
        content.pack(fill="both", expand=True)
    return ScrollableContainerRefs(host=host, content=content, scroll_enabled=False)


def bind_mousewheel(container: ScrollableContainerRefs) -> None:
    canvas = container.canvas
    content = container.content
    if canvas is None or content is None:
        return
    if not hasattr(canvas, "winfo_toplevel") or not hasattr(canvas, "yview_scroll"):
        return
    wheel_remainder = 0.0
    scroll_bound = False

    def _on_mousewheel(event):
        nonlocal wheel_remainder
        widget = getattr(event, "widget", None)
        if widget is not None and not is_descendant(widget, content) and not is_descendant(widget, canvas):
            return
        delta = 0
        wheel_delta = getattr(event, "delta", 0) or 0
        if wheel_delta:
            wheel_remainder += float(wheel_delta)
            steps = int(wheel_remainder / _WHEEL_DELTA_UNIT)
            wheel_remainder -= float(steps * _WHEEL_DELTA_UNIT)
            delta = -steps
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            canvas.yview_scroll(delta, "units")
            return "break"

    def _bind_scroll() -> None:
        nonlocal scroll_bound
        if scroll_bound:
            return
        root = canvas.winfo_toplevel()
        root.bind_all("<MouseWheel>", _on_mousewheel, add="+")
        root.bind_all("<Button-4>", _on_mousewheel, add="+")
        root.bind_all("<Button-5>", _on_mousewheel, add="+")
        scroll_bound = True

    _bind_scroll()
    try:
        canvas._simple_sender_mousewheel_handler = _on_mousewheel
    except Exception:
        pass


def build_scrollable_container(
    host,
    *,
    tk_module=tk,
    ttk_module=ttk,
    bind_mousewheel_support: bool = False,
) -> ScrollableContainerRefs:
    frame_cls = getattr(ttk_module, "Frame", None)
    canvas_cls = getattr(tk_module, "Canvas", None)
    scrollbar_cls = getattr(ttk_module, "Scrollbar", None)
    if not callable(frame_cls):
        raise TypeError("ttk.Frame is required to build a scrollable container")

    if callable(canvas_cls) and callable(scrollbar_cls):
        canvas = canvas_cls(host, highlightthickness=0)
        scrollbar = scrollbar_cls(host, orient="vertical", command=canvas.yview)
        content = frame_cls(canvas)
        if _supports_scrollable_widgets(host, canvas, scrollbar, content):
            host.grid_columnconfigure(0, weight=1)
            host.grid_rowconfigure(0, weight=1)
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")
            canvas.configure(yscrollcommand=scrollbar.set)
            content_window = canvas.create_window((0, 0), window=content, anchor="nw")
            refs = ScrollableContainerRefs(
                host=host,
                content=content,
                canvas=canvas,
                scrollbar=scrollbar,
                content_window=content_window,
                scroll_enabled=True,
            )
            content.bind("<Configure>", refs.update_scrollregion)
            canvas.bind("<Configure>", refs.resize_width)
            if bind_mousewheel_support:
                bind_mousewheel(refs)
            return refs
    content = frame_cls(host)
    return _place_plain_content(host, content)
