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
        _ = event
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


class ToolbarShapeButton(tk.Canvas):
    def __init__(
        self,
        master,
        *,
        text: str,
        command=None,
        shape: str = "square",
        accent: str = "#5b7cff",
        width: int = 72,
        height: int = 72,
        label_font: tuple[str, int, str] = ("TkDefaultFont", 9, "bold"),
        **kwargs,
    ):
        bg = kwargs.pop("bg", None)
        if bg is None:
            bg = kwargs.pop("background", None)
        style_name = str(kwargs.pop("style", "") or "").strip()
        initial_state = str(kwargs.pop("state", "normal") or "normal")
        if not bg:
            bg = _resolve_bg(master)
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            relief="flat",
            bg=bg,
            takefocus=1,
            **kwargs,
        )
        self._default_bg = bg
        self._text = str(text or "")
        self._command = command
        self._shape = str(shape or "square")
        self._accent = str(accent or "#5b7cff")
        self._style = style_name
        self._state = initial_state
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._width = int(width)
        self._height = int(height)
        self._layout_full_height = int(height)
        self._layout_compact_height = self._compute_compact_height(
            self._width,
            self._layout_full_height,
        )
        self._compact_layout_enabled = False
        self._label_font = label_font
        self._disabled_text = "#808080"
        self._disabled_accent = self._blend_color(self._accent, "#808080", 0.55)
        self._icon_images: dict[str, object] = {}
        self._toolbar_owner: Any | None = None
        self._toolbar_default_style: str = self._style
        self._toolbar_role: str = ""
        self._toolbar_asset_key: str = ""
        self._toolbar_icon: str = ""
        self._toolbar_caption_lines: tuple[str, ...] = ()
        self._toolbar_accessible_label: str = ""
        self._log_button = True
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<space>", self._on_invoke_key, add="+")
        self.bind("<Return>", self._on_invoke_key, add="+")
        self._redraw()

    def _compute_compact_height(self, width: int, full_height: int) -> int:
        base = max(1, min(int(width), int(full_height)))
        return max(46, int(round(base * 0.67)))

    def _blend_color(self, base: str, target: str, factor: float) -> str:
        base = str(base or "").lstrip("#")
        target = str(target or "").lstrip("#")
        if len(base) != 6 or len(target) != 6:
            return f"#{base}" if base else "#000000"
        br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
        tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
        r = int(br + (tr - br) * factor)
        g = int(bg + (tg - bg) * factor)
        b = int(bb + (tb - bb) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def refresh_background(self) -> None:
        self._default_bg = _resolve_bg(self.master)
        try:
            self.config(bg=self._default_bg)
        except tk.TclError:
            return
        self._redraw()

    def refresh_palette(self, *, accent: str | None = None, bg: str | None = None) -> None:
        if accent:
            self._accent = str(accent)
            self._disabled_accent = self._blend_color(self._accent, "#808080", 0.55)
        if bg:
            self._default_bg = str(bg)
            try:
                self.config(bg=self._default_bg)
            except tk.TclError:
                pass
        self._redraw()

    def set_content(self, text: str) -> None:
        self._text = str(text or "")
        self._redraw()

    def set_icon_images(self, images: dict[str, object] | None) -> None:
        self._icon_images = dict(images or {})
        self._redraw()

    def set_layout_mode(self, *, compact: bool) -> None:
        self._compact_layout_enabled = bool(compact)
        target_height = (
            self._layout_compact_height
            if self._compact_layout_enabled
            else self._layout_full_height
        )
        if target_height == self._height:
            self._redraw()
            return
        self._height = int(target_height)
        try:
            super().configure(height=self._height)
        except tk.TclError:
            return
        self._redraw()

    def _label_lines(self) -> list[str]:
        lines = [str(line).strip() for line in str(self._text or "").splitlines() if str(line).strip()]
        if len(lines) > 1:
            return lines[1:]
        return lines or [str(self._text or "").strip()]

    def _content_colors(self) -> tuple[str, str, str]:
        if self._state == "disabled":
            outline = self._disabled_accent
            fill = self._blend_color(self._accent, self._default_bg, 0.88)
            text = self._disabled_text
            return outline, fill, text
        fill_factor = 0.86
        if self._hovered:
            fill_factor = 0.78
        if self._pressed:
            fill_factor = 0.68
        outline = self._accent
        fill = self._blend_color(self._accent, self._default_bg, fill_factor)
        text = self._accent
        return outline, fill, text

    def _shape_center(self) -> tuple[float, float]:
        if self._compact_layout_enabled:
            return self._width / 2.0, max(18.0, self._height * 0.50)
        return self._width / 2.0, max(18.0, self._height * 0.30)

    def _shape_size(self) -> float:
        return min(self._width * 0.48, 40.0)

    def _draw_refresh(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        r = self._shape_size() * 0.44
        ring_width = 3
        start = 32
        extent = 282
        self.create_arc(
            cx - r,
            cy - r,
            cx + r,
            cy + r,
            start=start,
            extent=extent,
            style="arc",
            outline=outline,
            width=ring_width,
        )
        # Standard refresh arrowhead: short tangent stem plus triangular head.
        arrow_tip_x = cx + r * 0.96
        arrow_tip_y = cy - r * 0.08
        arrow_back_x = cx + r * 0.68
        arrow_back_y = cy - r * 0.46
        self.create_line(
            arrow_back_x,
            arrow_back_y,
            arrow_tip_x - r * 0.10,
            arrow_tip_y - r * 0.04,
            fill=outline,
            width=ring_width,
            capstyle="round",
        )
        self.create_polygon(
            arrow_tip_x,
            arrow_tip_y,
            cx + r * 0.44,
            cy - r * 0.22,
            cx + r * 0.62,
            cy + r * 0.18,
            fill=outline,
            outline=outline,
        )

    def _draw_bolt(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        s = self._shape_size() * 0.56
        points = [
            cx - s * 0.10,
            cy - s * 1.18,
            cx + s * 0.34,
            cy - s * 0.24,
            cx + s * 0.08,
            cy - s * 0.24,
            cx + s * 0.28,
            cy + s * 0.10,
            cx - s * 0.20,
            cy + s * 1.18,
            cx - s * 0.02,
            cy + s * 0.22,
            cx - s * 0.30,
            cy + s * 0.22,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")

    def _draw_play(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        s = self._shape_size() * 0.95
        points = [
            cx - s * 0.42,
            cy - s * 0.55,
            cx + s * 0.58,
            cy,
            cx - s * 0.42,
            cy + s * 0.55,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")

    def _draw_pause(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        bar_h = self._shape_size() * 0.95
        bar_w = self._shape_size() * 0.22
        gap = self._shape_size() * 0.18
        for direction in (-1, 1):
            x0 = cx + direction * (gap / 2 + bar_w) - bar_w
            x1 = x0 + bar_w
            y0 = cy - bar_h / 2
            y1 = cy + bar_h / 2
            self.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2)

    def _draw_square(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        s = self._shape_size() * 0.92
        self.create_rectangle(
            cx - s / 2,
            cy - s / 2,
            cx + s / 2,
            cy + s / 2,
            fill=fill,
            outline=outline,
            width=2,
        )

    def _draw_document(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size() * 0.92
        h = self._shape_size() * 1.08
        fold = w * 0.26
        x0 = cx - w / 2
        y0 = cy - h / 2
        x1 = cx + w / 2
        y1 = cy + h / 2
        points = [
            x0,
            y0,
            x1 - fold,
            y0,
            x1,
            y0 + fold,
            x1,
            y1,
            x0,
            y1,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")
        self.create_line(x1 - fold, y0, x1 - fold, y0 + fold, fill=outline, width=2)
        self.create_line(x1 - fold, y0 + fold, x1, y0 + fold, fill=outline, width=2)

    def _draw_tray(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size()
        h = self._shape_size() * 0.82
        x0 = cx - w / 2
        x1 = cx + w / 2
        y0 = cy - h / 2
        y1 = cy + h / 2
        self.create_rectangle(x0, y0 + h * 0.18, x1, y1, fill=fill, outline=outline, width=2)
        self.create_line(x0, y0 + h * 0.18, cx - w * 0.18, y0, fill=outline, width=2)
        self.create_line(cx - w * 0.18, y0, cx + w * 0.18, y0, fill=outline, width=2)
        self.create_line(cx + w * 0.18, y0, x1, y0 + h * 0.18, fill=outline, width=2)

    def _draw_lock(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size() * 0.94
        body_h = self._shape_size() * 0.40
        shackle_h = self._shape_size() * 0.78
        body_top = cy + body_h * 0.12
        self.create_rectangle(
            cx - w / 2,
            body_top,
            cx + w / 2,
            body_top + body_h,
            fill=fill,
            outline=outline,
            width=2,
        )
        left_leg_x = cx - w * 0.20
        shackle_top = body_top - shackle_h * 0.82
        self.create_arc(
            left_leg_x - w * 0.28,
            shackle_top,
            cx + w * 0.22,
            body_top + shackle_h * 0.02,
            start=18,
            extent=248,
            style="arc",
            outline=outline,
            width=3,
        )
        self.create_line(
            left_leg_x,
            body_top,
            left_leg_x,
            body_top - shackle_h * 0.02,
            fill=outline,
            width=3,
            capstyle="round",
        )
        self.create_line(
            cx + w * 0.18,
            body_top - shackle_h * 0.14,
            cx + w * 0.36,
            body_top - shackle_h * 0.34,
            fill=outline,
            width=3,
            capstyle="round",
        )

    def _draw_return(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size() * 0.92
        h = self._shape_size() * 0.72
        points = [
            cx + w * 0.42,
            cy - h * 0.36,
            cx - w * 0.02,
            cy - h * 0.36,
            cx - w * 0.02,
            cy - h * 0.58,
            cx - w * 0.46,
            cy,
            cx - w * 0.02,
            cy + h * 0.58,
            cx - w * 0.02,
            cy + h * 0.36,
            cx + w * 0.42,
            cy + h * 0.36,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")

    def _draw_shield(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size() * 0.95
        h = self._shape_size() * 1.05
        points = [
            cx - w * 0.42,
            cy - h * 0.46,
            cx + w * 0.42,
            cy - h * 0.46,
            cx + w * 0.42,
            cy - h * 0.02,
            cx,
            cy + h * 0.54,
            cx - w * 0.42,
            cy - h * 0.02,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")

    def _draw_hex(self, outline: str, fill: str) -> None:
        cx, cy = self._shape_center()
        w = self._shape_size() * 0.96
        h = self._shape_size() * 0.82
        points = [
            cx - w * 0.24,
            cy - h * 0.52,
            cx + w * 0.24,
            cy - h * 0.52,
            cx + w * 0.48,
            cy,
            cx + w * 0.24,
            cy + h * 0.52,
            cx - w * 0.24,
            cy + h * 0.52,
            cx - w * 0.48,
            cy,
        ]
        self.create_polygon(points, fill=fill, outline=outline, width=2, joinstyle="round")

    def _draw_shape(self, outline: str, fill: str) -> None:
        shape = str(self._shape or "square").strip().lower()
        if shape == "refresh":
            self._draw_refresh(outline, fill)
            return
        if shape == "bolt":
            self._draw_bolt(outline, fill)
            return
        if shape == "play":
            self._draw_play(outline, fill)
            return
        if shape == "pause":
            self._draw_pause(outline, fill)
            return
        if shape == "document":
            self._draw_document(outline, fill)
            return
        if shape == "tray":
            self._draw_tray(outline, fill)
            return
        if shape == "lock":
            self._draw_lock(outline, fill)
            return
        if shape == "return":
            self._draw_return(outline, fill)
            return
        if shape == "shield":
            self._draw_shield(outline, fill)
            return
        if shape == "hex":
            self._draw_hex(outline, fill)
            return
        self._draw_square(outline, fill)

    def _draw_focus_ring(self, color: str) -> None:
        if not self._focused or self._state == "disabled":
            return
        self.create_rectangle(
            3,
            3,
            self._width - 3,
            self._height - 3,
            outline=color,
            width=1,
            dash=(3, 2),
        )

    def _draw_label(self, color: str) -> None:
        lines = self._label_lines()
        label = "\n".join(line for line in lines if line)
        if not label:
            return
        self.create_text(
            self._width / 2,
            self._height - 14,
            text=label,
            fill=color,
            justify="center",
            font=self._label_font,
        )

    def _draw_icon_image(self) -> bool:
        if not self._icon_images:
            return False
        state_key = "disabled" if self._state == "disabled" else "normal"
        image = self._icon_images.get(state_key) or self._icon_images.get("normal")
        if image is None:
            return False
        self.create_image(
            self._width / 2,
            self._shape_center()[1],
            image=image,
        )
        return True

    def _redraw(self) -> None:
        try:
            self.delete("all")
        except tk.TclError:
            return
        outline, fill, text_color = self._content_colors()
        self._draw_focus_ring(outline)
        if not self._draw_icon_image():
            self._draw_shape(outline, fill)
        self._draw_label(text_color)
        try:
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
        except tk.TclError:
            return

    def _on_press(self, event: Any | None = None) -> None:
        _ = event
        if self._state == "disabled":
            return
        self._pressed = True
        try:
            self.focus_set()
        except Exception:
            pass
        self._redraw()

    def _on_release(self, event: Any | None = None) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        if not was_pressed:
            return
        if event is None:
            self.invoke()
            return
        x = getattr(event, "x", -1)
        y = getattr(event, "y", -1)
        if 0 <= x <= self._width and 0 <= y <= self._height:
            self.invoke()

    def _on_enter(self, event: Any | None = None) -> None:
        _ = event
        if self._state == "disabled":
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, event: Any | None = None) -> None:
        _ = event
        self._hovered = False
        self._pressed = False
        self._redraw()

    def _on_focus_in(self, event: Any | None = None) -> None:
        _ = event
        self._focused = True
        self._redraw()

    def _on_focus_out(self, event: Any | None = None) -> None:
        _ = event
        self._focused = False
        self._pressed = False
        self._redraw()

    def _on_invoke_key(self, event: Any | None = None) -> str:
        _ = event
        self.invoke()
        return "break"

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
            self._text = str(config_options.pop("text") or "")
        if "command" in config_options:
            self._command = config_options.pop("command")
        if "state" in config_options:
            self._state = str(config_options.pop("state") or "normal")
        if "style" in config_options:
            self._style = str(config_options.pop("style") or "")
        if "shape" in config_options:
            self._shape = str(config_options.pop("shape") or "square")
        if "accent" in config_options:
            self._accent = str(config_options.pop("accent") or self._accent)
            self._disabled_accent = self._blend_color(self._accent, "#808080", 0.55)
        if "height" in config_options:
            new_height = int(config_options["height"] or self._height)
            self._height = new_height
            self._layout_full_height = new_height
            self._layout_compact_height = self._compute_compact_height(
                self._width,
                self._layout_full_height,
            )
        result = super().configure(**config_options)
        self._redraw()
        return result

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        if key == "style":
            return self._style
        return super().cget(key)

    def invoke(self):
        if self._state == "disabled":
            return None
        if callable(self._command):
            return self._command()
        return None


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
