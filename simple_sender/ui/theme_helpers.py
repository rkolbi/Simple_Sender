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
import tkinter as tk
import tkinter.font as tkfont

from simple_sender.ui.led_panel import refresh_led_backgrounds
from simple_sender.ui.widgets_buttons import StopSignButton

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _style_lookup(style, style_name: str, option: str, fallback: str) -> str:
    try:
        value = str(style.lookup(style_name, option) or "").strip()
    except Exception:
        value = ""
    return value or fallback


def _normalize_color(app, value: str, fallback: str) -> str:
    color = str(value or "").strip() or str(fallback or "").strip()
    if not color:
        color = "#000000"
    if color.startswith("#") and len(color) in {4, 7}:
        return color
    try:
        rgb = None
        resolver = getattr(app, "winfo_rgb", None)
        if callable(resolver):
            rgb = resolver(color)
        elif hasattr(app, "tk"):
            rgb = app.tk.call("winfo", "rgb", ".", color)
        if rgb is None:
            return str(fallback or "#000000")
        r, g, b = (int(channel) for channel in rgb)
        return f"#{r // 256:02x}{g // 256:02x}{b // 256:02x}"
    except Exception:
        return str(fallback or "#000000")


def _toggle_indicator_size_px(app) -> int:
    try:
        linespace = int(tkfont.nametofont("TkDefaultFont").metrics("linespace"))
    except Exception:
        linespace = 16
    return max(18, min(28, int(linespace) + 2))


def _toggle_indicator_palette(app) -> dict[str, str]:
    style = getattr(app, "style", None)
    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    background = _style_lookup(style, "TFrame", "background", palette.get("bg", "#f0f0f0"))
    border = palette.get("border") or _style_lookup(style, "TCheckbutton", "foreground", "#5f5f5f")
    field = _style_lookup(style, "TEntry", "fieldbackground", palette.get("button_bg", "#ffffff"))
    accent = palette.get("button_pressed") or "#0b63d1"
    check = palette.get("bg") or "#ffffff"
    muted = palette.get("muted_fg") or "#9a9a9a"
    disabled_fill = palette.get("button_bg") or background
    return {
        "background": _normalize_color(app, background, "#f0f0f0"),
        "border": _normalize_color(app, border, "#5f5f5f"),
        "field": _normalize_color(app, field, "#ffffff"),
        "accent": _normalize_color(app, accent, "#0b63d1"),
        "check": _normalize_color(app, check, "#ffffff"),
        "muted": _normalize_color(app, muted, "#9a9a9a"),
        "disabled_fill": _normalize_color(app, disabled_fill, "#e0e0e0"),
    }


def _new_toggle_image(app, size: int, background: str) -> tk.PhotoImage:
    master = app if hasattr(app, "tk") else None
    image = tk.PhotoImage(master=master, width=size, height=size)
    image.put(background, to=(0, 0, size, size))
    return image


def _fill_square(
    image: tk.PhotoImage,
    *,
    size: int,
    background: str,
    border: str,
    fill: str,
) -> None:
    image.put(background, to=(0, 0, size, size))
    inset = max(1, size // 8)
    last = size - inset - 1
    image.put(border, to=(inset, inset, last + 1, inset + 1))
    image.put(border, to=(inset, last, last + 1, last + 1))
    image.put(border, to=(inset, inset, inset + 1, last + 1))
    image.put(border, to=(last, inset, last + 1, last + 1))
    inner = inset + 1
    if inner <= last - 1:
        image.put(fill, to=(inner, inner, last, last))


def _fill_circle(
    image: tk.PhotoImage,
    *,
    size: int,
    background: str,
    border: str,
    fill: str,
) -> None:
    image.put(background, to=(0, 0, size, size))
    center = (size - 1) / 2.0
    radius = max(4.0, center - max(1.0, size / 8.0))
    inner_radius = max(1.0, radius - max(1.5, size / 7.0))
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= radius:
                if dist >= inner_radius:
                    image.put(border, (x, y))
                else:
                    image.put(fill, (x, y))


def _draw_check_mark(image: tk.PhotoImage, *, size: int, color: str) -> None:
    start_x = max(1, size // 4)
    mid_x = max(start_x + 1, size // 2 - 1)
    end_x = size - max(2, size // 5)
    start_y = max(1, size // 2)
    mid_y = size - max(3, size // 4)
    end_y = max(1, size // 4)
    thickness = max(2, size // 7)
    for step in range(mid_x - start_x + 1):
        x = start_x + step
        y = start_y + step
        for offset in range(thickness):
            image.put(color, (x, y + offset))
    rise = max(1, end_x - mid_x)
    for step in range(rise + 1):
        x = mid_x + step
        y = mid_y - int(round((mid_y - end_y) * (step / float(rise or 1))))
        for offset in range(thickness):
            image.put(color, (x, max(0, y - offset)))


def _draw_radio_dot(image: tk.PhotoImage, *, size: int, color: str) -> None:
    center = (size - 1) / 2.0
    radius = max(2.0, size / 4.2)
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            if (dx * dx + dy * dy) ** 0.5 <= radius:
                image.put(color, (x, y))


def _build_toggle_indicator_images(app, *, size: int) -> dict[str, tk.PhotoImage]:
    palette = _toggle_indicator_palette(app)

    images: dict[str, tk.PhotoImage] = {}
    check_off = _new_toggle_image(app, size, palette["background"])
    _fill_square(
        check_off,
        size=size,
        background=palette["background"],
        border=palette["border"],
        fill=palette["field"],
    )
    images["check_off"] = check_off

    check_on = _new_toggle_image(app, size, palette["background"])
    _fill_square(
        check_on,
        size=size,
        background=palette["background"],
        border=palette["accent"],
        fill=palette["accent"],
    )
    _draw_check_mark(check_on, size=size, color=palette["check"])
    images["check_on"] = check_on

    check_disabled_off = _new_toggle_image(app, size, palette["background"])
    _fill_square(
        check_disabled_off,
        size=size,
        background=palette["background"],
        border=palette["muted"],
        fill=palette["disabled_fill"],
    )
    images["check_disabled_off"] = check_disabled_off

    check_disabled_on = _new_toggle_image(app, size, palette["background"])
    _fill_square(
        check_disabled_on,
        size=size,
        background=palette["background"],
        border=palette["muted"],
        fill=palette["muted"],
    )
    _draw_check_mark(check_disabled_on, size=size, color=palette["field"])
    images["check_disabled_on"] = check_disabled_on

    radio_off = _new_toggle_image(app, size, palette["background"])
    _fill_circle(
        radio_off,
        size=size,
        background=palette["background"],
        border=palette["border"],
        fill=palette["field"],
    )
    images["radio_off"] = radio_off

    radio_on = _new_toggle_image(app, size, palette["background"])
    _fill_circle(
        radio_on,
        size=size,
        background=palette["background"],
        border=palette["accent"],
        fill=palette["field"],
    )
    _draw_radio_dot(radio_on, size=size, color=palette["accent"])
    images["radio_on"] = radio_on

    radio_disabled_off = _new_toggle_image(app, size, palette["background"])
    _fill_circle(
        radio_disabled_off,
        size=size,
        background=palette["background"],
        border=palette["muted"],
        fill=palette["disabled_fill"],
    )
    images["radio_disabled_off"] = radio_disabled_off

    radio_disabled_on = _new_toggle_image(app, size, palette["background"])
    _fill_circle(
        radio_disabled_on,
        size=size,
        background=palette["background"],
        border=palette["muted"],
        fill=palette["disabled_fill"],
    )
    _draw_radio_dot(radio_disabled_on, size=size, color=palette["muted"])
    images["radio_disabled_on"] = radio_disabled_on
    return images


def _replace_indicator_element(layout, *, widget_kind: str, element_name: str):
    replaced = []
    indicator_token = f"{widget_kind}.indicator"
    for name, opts in layout:
        new_name = element_name if indicator_token in str(name) else name
        new_opts = dict(opts)
        children = new_opts.get("children")
        if children:
            new_opts["children"] = _replace_indicator_element(
                children,
                widget_kind=widget_kind,
                element_name=element_name,
            )
        replaced.append((new_name, new_opts))
    return replaced


def apply_toggle_indicator_style(app) -> None:
    style = getattr(app, "style", None)
    element_create = getattr(style, "element_create", None)
    layout = getattr(style, "layout", None)
    if not callable(element_create) or not callable(layout) or not hasattr(app, "tk"):
        return
    size = _toggle_indicator_size_px(app)
    images = _build_toggle_indicator_images(app, size=size)
    version = int(getattr(app, "_toggle_indicator_style_version", 0) or 0) + 1
    app._toggle_indicator_style_version = version
    app._toggle_indicator_size_px = size
    app._toggle_indicator_images = images
    check_element = f"SimpleSender.Checkbutton.indicator.{version}"
    radio_element = f"SimpleSender.Radiobutton.indicator.{version}"
    try:
        element_create(
            check_element,
            "image",
            images["check_off"],
            ("disabled", "selected", images["check_disabled_on"]),
            ("disabled", images["check_disabled_off"]),
            ("selected", images["check_on"]),
            border=0,
            sticky="",
        )
        element_create(
            radio_element,
            "image",
            images["radio_off"],
            ("disabled", "selected", images["radio_disabled_on"]),
            ("disabled", images["radio_disabled_off"]),
            ("selected", images["radio_on"]),
            border=0,
            sticky="",
        )
        layout(
            "TCheckbutton",
            _replace_indicator_element(
                layout("TCheckbutton"),
                widget_kind="Checkbutton",
                element_name=check_element,
            ),
        )
        layout(
            "TRadiobutton",
            _replace_indicator_element(
                layout("TRadiobutton"),
                widget_kind="Radiobutton",
                element_name=radio_element,
            ),
        )
    except Exception as exc:
        _log_suppressed("Failed applying custom toggle indicator style", exc)


def refresh_stop_button_backgrounds(app):
    for btn in (getattr(app, "btn_jog_cancel", None), getattr(app, "btn_all_stop", None)):
        if isinstance(btn, StopSignButton):
            btn.refresh_background()


def _apply_icon_button_theme(app, palette: dict):
    style = app.style
    style.configure(
        app.icon_button_style,
        background=palette["button_bg"],
        foreground=palette["fg"],
        bordercolor=palette["border"],
        lightcolor=palette["button_bg"],
        darkcolor=palette["button_bg"],
    )
    style.map(
        app.icon_button_style,
        background=[
            ("pressed", palette["button_pressed"]),
            ("active", palette["button_hover"]),
            ("disabled", palette["bg"]),
        ],
        foreground=[("disabled", palette["muted_fg"])],
    )


def _apply_home_button_theme(app, palette: dict):
    style = app.style
    style_name = getattr(app, "home_button_style", "")
    if not style_name:
        return
    button_bg = palette.get("button_bg", "#f0f0f0") if isinstance(palette, dict) else "#f0f0f0"
    accent = "#5b3b89"
    fg = accent
    border = palette.get("border", button_bg) if isinstance(palette, dict) else button_bg
    hover = palette.get("button_hover", button_bg) if isinstance(palette, dict) else button_bg
    pressed = palette.get("button_pressed", button_bg) if isinstance(palette, dict) else button_bg
    disabled_bg = palette.get("bg", "#f0f0f0") if isinstance(palette, dict) else "#f0f0f0"
    disabled_fg = palette.get("muted_fg", "#808080") if isinstance(palette, dict) else "#808080"
    style.configure(
        style_name,
        background=button_bg,
        foreground=fg,
        bordercolor=border,
        lightcolor=button_bg,
        darkcolor=button_bg,
    )
    style.map(
        style_name,
        background=[
            ("pressed", pressed),
            ("active", hover),
            ("disabled", disabled_bg),
        ],
        foreground=[
            ("pressed", fg),
            ("active", fg),
            ("disabled", disabled_fg),
        ],
    )


def _reapply_button_metrics(app) -> None:
    style = app.style
    touch_padding = (10, 12)
    try:
        style.configure(
            app.icon_button_style,
            anchor="center",
            justify="center",
            padding=(8, 4),
            font=app.icon_button_font,
        )
    except Exception as exc:
        _log_suppressed("Failed reapplying icon button metrics", exc)
    try:
        style_name = getattr(app, "home_button_style", "")
        if style_name:
            style.configure(
                style_name,
                anchor="center",
                justify="center",
                padding=touch_padding,
                font=app.home_button_font,
            )
    except Exception as exc:
        _log_suppressed("Failed reapplying home button metrics", exc)
    try:
        style.configure(
            app.mpos_button_style,
            anchor="center",
            justify="center",
            padding=touch_padding,
        )
    except Exception as exc:
        _log_suppressed("Failed reapplying MPos button metrics", exc)
    try:
        style.configure(
            app.macro_button_style,
            anchor="center",
            justify="center",
            padding=touch_padding,
        )
    except Exception as exc:
        _log_suppressed("Failed reapplying macro button metrics", exc)
    try:
        style.configure(
            "SimpleSender.UnitReported.TButton",
            anchor="center",
            justify="center",
        )
    except Exception as exc:
        _log_suppressed("Failed reapplying unit-toggle button metrics", exc)


def apply_theme(app, theme: str):
    try:
        if theme in app.available_themes:
            app.style.theme_use(theme)
            _reapply_button_metrics(app)
            palette = None
            try:
                palette = app.theme_palettes.get(theme)
            except Exception:
                palette = None
            if palette:
                app.theme_palette = palette
                try:
                    app.configure(background=palette["bg"])
                except Exception as exc:
                    _log_suppressed("Failed applying window background for selected theme", exc)
                try:
                    _apply_icon_button_theme(app, palette)
                except Exception as exc:
                    _log_suppressed("Failed applying icon button theme overrides", exc)
            else:
                app.theme_palette = {}
            apply_toggle_indicator_style(app)
            try:
                _apply_home_button_theme(app, palette or {})
            except Exception as exc:
                _log_suppressed("Failed applying home button theme overrides", exc)
            try:
                app.style.configure("TNotebook.Tab", font=app.tab_font)
            except Exception as exc:
                _log_suppressed("Failed applying notebook tab font after theme change", exc)
            try:
                app.style.configure("TNotebook.Tab", padding=(10, 4))
            except Exception as exc:
                _log_suppressed("Failed applying notebook tab padding after theme change", exc)
            refresh_stop_button_backgrounds(app)
            refresh_led_backgrounds(app)
    except tk.TclError as exc:
        _log_suppressed("Failed applying requested Tk theme", exc)
