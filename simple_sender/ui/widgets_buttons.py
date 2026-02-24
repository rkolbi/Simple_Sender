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

import tkinter as tk
from typing import Any

from simple_sender.utils.constants import STOP_SIGN_CUT_RATIO


def _resolve_bg(master) -> str:
    try:
        from .widgets_common import _resolve_widget_bg

        return str(_resolve_widget_bg(master))
    except Exception:
        return "#f0f0f0"


class StopSignButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        fill: str,
        text_color: str,
        command=None,
        size: int = 60,
        outline: str = "#2f2f2f",
        **kwargs,
    ):
        bg = kwargs.pop("bg", None)
        if bg is None:
            bg = kwargs.pop("background", None)
        if not bg:
            bg = _resolve_bg(master)
        super().__init__(
            master,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=bg,
            **kwargs,
        )
        self._default_bg = bg
        self._text = text
        self._fill = fill
        self._text_color = text_color
        self._outline = outline
        self._command = command
        self._size = size
        self._state = "normal"
        self._poly: int | None = None
        self._text_id: int | None = None
        self._disabled_fill = self._blend_color(fill, "#f0f0f0", 0.55)
        self._disabled_text = self._blend_color(text_color, "#808080", 0.55)
        self._draw_octagon()
        self._apply_state()
        self._log_button = True
        self.bind("<Button-1>", self._on_click, add="+")

    def _blend_color(self, base: str, target: str, factor: float) -> str:
        base = base.lstrip("#")
        target = target.lstrip("#")
        if len(base) != 6 or len(target) != 6:
            return base if base.startswith("#") else f"#{base}"
        br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
        tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
        r = int(br + (tr - br) * factor)
        g = int(bg + (tg - bg) * factor)
        b = int(bb + (tb - bb) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def refresh_background(self):
        bg = _resolve_bg(self.master)
        self._default_bg = bg
        try:
            self.config(bg=bg)
        except tk.TclError:
            return

    def _draw_octagon(self):
        size = self._size
        pad = 2
        s = size - pad * 2
        cut = s * STOP_SIGN_CUT_RATIO
        x0, y0 = pad, pad
        x1, y1 = pad + s, pad + s
        points = [
            x0 + cut,
            y0,
            x1 - cut,
            y0,
            x1,
            y0 + cut,
            x1,
            y1 - cut,
            x1 - cut,
            y1,
            x0 + cut,
            y1,
            x0,
            y1 - cut,
            x0,
            y0 + cut,
        ]
        self._poly = self.create_polygon(points, fill=self._fill, outline=self._outline, width=1)
        self._text_id = self.create_text(
            size / 2,
            size / 2,
            text=self._text,
            fill=self._text_color,
            justify="center",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _apply_state(self):
        is_disabled = self._state == "disabled"
        fill = self._disabled_fill if is_disabled else self._fill
        text_color = self._disabled_text if is_disabled else self._text_color
        if self._poly is not None:
            self.itemconfig(self._poly, fill=fill)
        if self._text_id is not None:
            self.itemconfig(self._text_id, fill=text_color)
        self.config(cursor="arrow" if is_disabled else "hand2")

    def _on_click(self, event: Any | None = None) -> None:
        if self._state == "disabled":
            return
        if callable(self._command):
            self._command()

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        config_options: dict[str, Any] = {}
        if cnf:
            if isinstance(cnf, dict):
                config_options.update(cnf)
            else:
                for item in cnf:
                    if isinstance(item, tuple) and len(item) == 2:
                        config_options[item[0]] = item[1]
        config_options.update(kwargs)
        if "text" in config_options:
            self._text = config_options.pop("text")
            if self._text_id is not None:
                self.itemconfig(self._text_id, text=self._text)
        if "command" in config_options:
            self._command = config_options.pop("command")
        if "state" in config_options:
            self._state = config_options.pop("state")
            self._apply_state()
        return super().configure(**config_options)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self):
        self._on_click()


class VirtualHoldButton:
    def __init__(self, label: str, kb_id: str, axis: str, direction: int):
        self._text = label
        self._kb_id = kb_id
        self._hold_axis = axis
        self._hold_direction = direction
        self._tooltip_text = ""

    def cget(self, key: str):
        if key == "text":
            return self._text
        if key == "state":
            return "normal"
        if key == "command":
            return None
        raise KeyError(key)

    def winfo_name(self):
        return f"virtual_{self._kb_id}"

    def winfo_class(self):
        return "VirtualHoldButton"
