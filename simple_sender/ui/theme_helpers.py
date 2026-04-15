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
import tkinter.font as tkfont

from simple_sender.ui.led_panel import refresh_led_backgrounds
from simple_sender.ui.widgets_buttons import StopSignButton

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


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
    accent = palette.get("accent") or palette.get("button_pressed") or "#0b63d1"
    check = palette.get("selection_fg") or palette.get("fg") or "#ffffff"
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


def register_theme_refresh(app, callback) -> None:
    if not callable(callback):
        return
    callbacks = getattr(app, "_theme_refresh_callbacks", None)
    if not isinstance(callbacks, list):
        callbacks = []
        app._theme_refresh_callbacks = callbacks
    if callback not in callbacks:
        callbacks.append(callback)


def unregister_theme_refresh(app, callback) -> None:
    callbacks = getattr(app, "_theme_refresh_callbacks", None)
    if not isinstance(callbacks, list):
        return
    try:
        callbacks.remove(callback)
    except ValueError:
        return


def refresh_theme_widgets(app) -> None:
    callbacks = getattr(app, "_theme_refresh_callbacks", None)
    if not isinstance(callbacks, list):
        return
    for callback in tuple(callbacks):
        try:
            callback()
        except Exception as exc:
            _log_suppressed("Failed refreshing theme-managed widget", exc)


_TEXT_DISPLAY_OPTIONS = (
    "background",
    "foreground",
    "insertbackground",
    "insertwidth",
    "selectbackground",
    "selectforeground",
    "inactiveselectbackground",
    "highlightbackground",
    "highlightcolor",
    "highlightthickness",
    "relief",
    "borderwidth",
)


def _cache_widget_theme_defaults(widget, *, options: tuple[str, ...]) -> dict[str, object]:
    cached = getattr(widget, "_simple_sender_theme_defaults", None)
    if isinstance(cached, dict):
        return cached
    cached = {}
    for option in options:
        try:
            cached[option] = widget.cget(option)
        except Exception:
            continue
    try:
        widget._simple_sender_theme_defaults = cached
    except Exception:
        pass
    return cached


def _text_display_palette(app) -> dict[str, object] | None:
    palette = getattr(app, "theme_palette", None)
    if not isinstance(palette, dict) or not palette:
        return None
    bg = (
        palette.get("text_pane_bg")
        or palette.get("panel_raised")
        or palette.get("button_bg")
        or palette.get("panel_bg")
        or palette.get("bg")
    )
    fg = palette.get("text_pane_fg") or palette.get("fg") or "#ffffff"
    selection_bg = palette.get("selection_bg") or palette.get("accent") or bg
    selection_fg = palette.get("selection_fg") or fg
    border = palette.get("text_pane_border") or palette.get("border") or bg or "#000000"
    inactive_selection = palette.get("text_pane_inactive_selection_bg") or selection_bg
    insert = palette.get("text_pane_insert") or palette.get("accent_secondary") or fg
    return {
        "background": bg,
        "foreground": fg,
        "insertbackground": insert,
        "insertwidth": 2,
        "selectbackground": selection_bg,
        "selectforeground": selection_fg,
        "inactiveselectbackground": inactive_selection,
        "highlightbackground": border,
        "highlightcolor": palette.get("accent") or border,
        "highlightthickness": 1,
        "relief": "flat",
        "borderwidth": 1,
    }


def text_display_theme_options(app) -> dict[str, object]:
    palette = _text_display_palette(app)
    return dict(palette) if isinstance(palette, dict) else {}


def apply_text_display_theme(app, widget) -> None:
    if widget is None:
        return
    exists = getattr(widget, "winfo_exists", None)
    if callable(exists):
        try:
            if not bool(exists()):
                return
        except Exception:
            return
    defaults = _cache_widget_theme_defaults(widget, options=_TEXT_DISPLAY_OPTIONS)
    palette = _text_display_palette(app)
    values = palette or defaults
    if not isinstance(values, dict) or not values:
        return
    try:
        widget.configure(**values)
    except Exception as exc:
        _log_suppressed("Failed applying themed text-display widget colors", exc)


_CANVAS_OPTIONS = (
    "background",
    "highlightbackground",
    "highlightcolor",
    "highlightthickness",
    "borderwidth",
    "relief",
)

_SCROLLBAR_OPTIONS = (
    "style",
    "width",
)


def _canvas_palette(app) -> dict[str, object] | None:
    palette = getattr(app, "theme_palette", None)
    if not isinstance(palette, dict) or not palette:
        return None
    background = (
        palette.get("panel_bg")
        or palette.get("bg")
        or palette.get("button_bg")
    )
    border = palette.get("border") or background or "#000000"
    return {
        "background": background,
        "highlightbackground": background,
        "highlightcolor": border,
        "highlightthickness": 0,
        "borderwidth": 0,
        "relief": "flat",
    }


def _scrollbar_width_value(app) -> int | None:
    cached = getattr(app, "_scrollbar_width_px", None)
    try:
        if isinstance(cached, (int, float, str)) and cached not in ("", None):
            return max(1, int(cached))
    except Exception:
        pass
    style = getattr(app, "style", None)
    if style is None:
        return None
    for style_name in ("Vertical.TScrollbar", "Horizontal.TScrollbar", "TScrollbar"):
        for option in ("width", "arrowsize"):
            try:
                value = style.lookup(style_name, option)
            except Exception:
                value = None
            try:
                if value not in ("", None):
                    return max(1, int(value))
            except Exception:
                continue
    return None


def _scrollbar_palette(app, widget) -> dict[str, object] | None:
    width = _scrollbar_width_value(app)
    try:
        orient = str(widget.cget("orient") or "").strip().lower()
    except Exception:
        orient = "vertical"
    style_name = "Horizontal.TScrollbar" if orient == "horizontal" else "Vertical.TScrollbar"
    values: dict[str, object] = {"style": style_name}
    if width is not None:
        values["width"] = int(width)
    return values


def _plain_tk_defaults(app) -> dict[str, object]:
    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    style = getattr(app, "style", None)
    frame_bg = _normalize_color(
        app,
        palette.get("panel_bg")
        or palette.get("bg")
        or palette.get("button_bg")
        or _style_lookup(style, "TFrame", "background", "#f0f0f0"),
        "#f0f0f0",
    )
    text_bg = _normalize_color(
        app,
        palette.get("text_pane_bg")
        or palette.get("panel_raised")
        or palette.get("button_bg")
        or _style_lookup(style, "TEntry", "fieldbackground", frame_bg),
        frame_bg,
    )
    fg = _normalize_color(
        app,
        palette.get("text_pane_fg")
        or palette.get("fg")
        or _style_lookup(style, "TLabel", "foreground", "#000000"),
        "#000000",
    )
    border = _normalize_color(
        app,
        palette.get("text_pane_border")
        or palette.get("border")
        or _style_lookup(style, "TFrame", "bordercolor", frame_bg),
        frame_bg,
    )
    accent = _normalize_color(
        app,
        palette.get("accent")
        or _style_lookup(style, "TButton", "focuscolor", border),
        border,
    )
    selection_bg = _normalize_color(
        app,
        palette.get("selection_bg") or accent,
        accent,
    )
    selection_fg = _normalize_color(
        app,
        palette.get("selection_fg") or fg,
        fg,
    )
    muted = _normalize_color(
        app,
        palette.get("muted_fg") or fg,
        fg,
    )
    return {
        "frame_bg": frame_bg,
        "text_bg": text_bg,
        "fg": fg,
        "border": border,
        "accent": accent,
        "selection_bg": selection_bg,
        "selection_fg": selection_fg,
        "muted": muted,
    }


def plain_tk_theme_defaults(app) -> dict[str, object]:
    return dict(_plain_tk_defaults(app))


def _seed_plain_tk_widget_defaults(app) -> None:
    option_add = getattr(app, "option_add", None)
    if not callable(option_add):
        return
    defaults = _plain_tk_defaults(app)
    option_values = {
        "*Text.background": defaults["text_bg"],
        "*Text.foreground": defaults["fg"],
        "*Text.insertBackground": defaults["accent"],
        "*Text.selectBackground": defaults["selection_bg"],
        "*Text.selectForeground": defaults["selection_fg"],
        "*Text.highlightBackground": defaults["border"],
        "*Text.highlightColor": defaults["accent"],
        "*Listbox.background": defaults["text_bg"],
        "*Listbox.foreground": defaults["fg"],
        "*Listbox.selectBackground": defaults["selection_bg"],
        "*Listbox.selectForeground": defaults["selection_fg"],
        "*Listbox.disabledForeground": defaults["muted"],
        "*Listbox.highlightBackground": defaults["border"],
        "*Listbox.highlightColor": defaults["accent"],
        "*Canvas.background": defaults["frame_bg"],
        "*Canvas.highlightBackground": defaults["frame_bg"],
        "*Canvas.highlightColor": defaults["border"],
    }
    for pattern, value in option_values.items():
        try:
            option_add(pattern, value)
        except Exception as exc:
            _log_suppressed(f"Failed seeding Tk option database for {pattern}", exc)


def configure_notebook_page_style(app) -> None:
    style = getattr(app, "style", None)
    if style is None:
        return
    defaults = _plain_tk_defaults(app)
    try:
        style.configure("SimpleSender.NotebookPage.TFrame", background=defaults["frame_bg"])
    except Exception as exc:
        _log_suppressed("Failed configuring notebook page frame style", exc)


def notebook_page_style_name() -> str:
    return "SimpleSender.NotebookPage.TFrame"


def canvas_theme_options(app) -> dict[str, object]:
    palette = _canvas_palette(app)
    return dict(palette) if isinstance(palette, dict) else {}


def apply_canvas_theme(app, widget) -> None:
    if widget is None:
        return
    exists = getattr(widget, "winfo_exists", None)
    if callable(exists):
        try:
            if not bool(exists()):
                return
        except Exception:
            return
    defaults = _cache_widget_theme_defaults(widget, options=_CANVAS_OPTIONS)
    palette = _canvas_palette(app)
    values = palette or defaults
    if not isinstance(values, dict) or not values:
        return
    try:
        widget.configure(**values)
    except Exception as exc:
        _log_suppressed("Failed applying themed canvas colors", exc)


def apply_scrollbar_theme(app, widget) -> None:
    if widget is None:
        return
    exists = getattr(widget, "winfo_exists", None)
    if callable(exists):
        try:
            if not bool(exists()):
                return
        except Exception:
            return
    defaults = _cache_widget_theme_defaults(widget, options=_SCROLLBAR_OPTIONS)
    palette = _scrollbar_palette(app, widget)
    values = palette or defaults
    if not isinstance(values, dict) or not values:
        return
    style_name = values.get("style")
    if style_name not in ("", None):
        try:
            widget.configure(style=style_name)
        except Exception as exc:
            _log_suppressed("Failed applying themed scrollbar style", exc)
    width = values.get("width")
    if width not in ("", None):
        try:
            widget.configure(width=width)
        except Exception:
            pass


_LISTBOX_OPTIONS = (
    "background",
    "foreground",
    "selectbackground",
    "selectforeground",
    "disabledforeground",
    "highlightbackground",
    "highlightcolor",
    "highlightthickness",
    "relief",
    "borderwidth",
    "activestyle",
)


def _listbox_palette(app) -> dict[str, object] | None:
    palette = getattr(app, "theme_palette", None)
    if not isinstance(palette, dict) or not palette:
        return None
    bg = (
        palette.get("text_pane_bg")
        or palette.get("panel_raised")
        or palette.get("button_bg")
        or palette.get("panel_bg")
        or palette.get("bg")
    )
    fg = palette.get("text_pane_fg") or palette.get("fg") or "#ffffff"
    selection_bg = palette.get("selection_bg") or palette.get("accent") or bg
    selection_fg = palette.get("selection_fg") or fg
    border = palette.get("text_pane_border") or palette.get("border") or bg or "#000000"
    return {
        "background": bg,
        "foreground": fg,
        "selectbackground": selection_bg,
        "selectforeground": selection_fg,
        "disabledforeground": palette.get("muted_fg") or fg,
        "highlightbackground": border,
        "highlightcolor": palette.get("accent") or border,
        "highlightthickness": 1,
        "relief": "flat",
        "borderwidth": 1,
        "activestyle": "none",
    }


def apply_listbox_theme(app, widget) -> None:
    if widget is None:
        return
    exists = getattr(widget, "winfo_exists", None)
    if callable(exists):
        try:
            if not bool(exists()):
                return
        except Exception:
            return
    defaults = _cache_widget_theme_defaults(widget, options=_LISTBOX_OPTIONS)
    palette = _listbox_palette(app)
    values = palette or defaults
    if not isinstance(values, dict) or not values:
        return
    try:
        widget.configure(**values)
    except Exception as exc:
        _log_suppressed("Failed applying themed listbox widget colors", exc)


def _bind_theme_managed_widget(app, widget, *, apply_callback_name: str, apply_func) -> None:
    if widget is None:
        return
    callback = getattr(widget, apply_callback_name, None)
    if not callable(callback):
        def callback(widget=widget) -> None:
            apply_func(app, widget)
        try:
            setattr(widget, apply_callback_name, callback)
        except Exception:
            pass
        register_theme_refresh(app, callback)

        def _cleanup(event=None, *, widget=widget, callback=callback) -> None:
            if event is not None and getattr(event, "widget", None) is not widget:
                return
            unregister_theme_refresh(app, callback)
            try:
                setattr(widget, apply_callback_name, None)
            except Exception:
                pass

        try:
            widget.bind("<Destroy>", _cleanup, add="+")
        except Exception as exc:
            _log_suppressed("Failed binding themed-widget cleanup handler", exc)
    callback()


def bind_text_display_theme(app, widget) -> None:
    _bind_theme_managed_widget(
        app,
        widget,
        apply_callback_name="_simple_sender_theme_refresh",
        apply_func=apply_text_display_theme,
    )


def bind_listbox_theme(app, widget) -> None:
    _bind_theme_managed_widget(
        app,
        widget,
        apply_callback_name="_simple_sender_listbox_theme_refresh",
        apply_func=apply_listbox_theme,
    )


def bind_canvas_theme(app, widget) -> None:
    _bind_theme_managed_widget(
        app,
        widget,
        apply_callback_name="_simple_sender_canvas_theme_refresh",
        apply_func=apply_canvas_theme,
    )


def bind_scrollbar_theme(app, widget) -> None:
    _bind_theme_managed_widget(
        app,
        widget,
        apply_callback_name="_simple_sender_scrollbar_theme_refresh",
        apply_func=apply_scrollbar_theme,
    )


def touch_scale_metrics(
    app,
    *,
    minimum_thickness: int = 32,
    thickness_offset: int = 10,
    length_offset: int = 12,
) -> tuple[int, int]:
    width = _scrollbar_width_value(app)
    if width is None:
        width = 24
    thickness = max(int(minimum_thickness), int(width) + int(thickness_offset))
    slider_length = max(thickness + 2, int(width) + int(length_offset))
    return thickness, slider_length


def apply_touch_scale_theme(
    app,
    widget,
    *,
    minimum_thickness: int = 32,
    thickness_offset: int = 10,
    length_offset: int = 12,
) -> None:
    if widget is None:
        return
    exists = getattr(widget, "winfo_exists", None)
    if callable(exists):
        try:
            if not bool(exists()):
                return
        except Exception:
            return
    colors = plain_tk_theme_defaults(app)
    thickness, slider_length = touch_scale_metrics(
        app,
        minimum_thickness=minimum_thickness,
        thickness_offset=thickness_offset,
        length_offset=length_offset,
    )
    try:
        widget.configure(
            width=thickness,
            sliderlength=slider_length,
            showvalue=0,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            background=colors["frame_bg"],
            foreground=colors["fg"],
            activebackground=colors["accent"],
            troughcolor=colors["text_bg"],
        )
    except Exception as exc:
        _log_suppressed("Failed applying themed touch-scale widget", exc)


def bind_touch_scale_theme(
    app,
    widget,
    *,
    minimum_thickness: int = 32,
    thickness_offset: int = 10,
    length_offset: int = 12,
) -> None:
    _bind_theme_managed_widget(
        app,
        widget,
        apply_callback_name="_simple_sender_touch_scale_theme_refresh",
        apply_func=lambda app_obj, widget_obj: apply_touch_scale_theme(
            app_obj,
            widget_obj,
            minimum_thickness=minimum_thickness,
            thickness_offset=thickness_offset,
            length_offset=length_offset,
        ),
    )


def resolve_theme_choice(app, theme: str | None, *, default_theme: str | None = None) -> str:
    available = list(getattr(app, "available_themes", ()) or ())
    candidates: list[str] = []
    for value in (theme, default_theme):
        normalized = str(value or "").strip()
        if normalized:
            candidates.append(normalized)
    current_theme = ""
    style = getattr(app, "style", None)
    if style is not None:
        try:
            current_theme = str(style.theme_use() or "").strip()
        except Exception:
            current_theme = ""
    if current_theme:
        candidates.append(current_theme)
    candidates.extend(str(name or "").strip() for name in available)
    for candidate in candidates:
        if candidate and candidate in available:
            return candidate
    return current_theme or str(default_theme or theme or "").strip()


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
    accent = (
        palette.get("accent_secondary")
        or palette.get("accent")
        or "#5b3b89"
        if isinstance(palette, dict)
        else "#5b3b89"
    )
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


def apply_theme(app, theme: str) -> str:
    applied_theme = resolve_theme_choice(app, theme, default_theme=getattr(app, "default_theme_name", ""))
    try:
        if applied_theme in app.available_themes:
            app.style.theme_use(applied_theme)
            _reapply_button_metrics(app)
            palette = None
            try:
                palette = app.theme_palettes.get(applied_theme)
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
            _seed_plain_tk_widget_defaults(app)
            configure_notebook_page_style(app)
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
                app.style.configure("TNotebook.Tab", padding=(14, 8))
            except Exception as exc:
                _log_suppressed("Failed applying notebook tab padding after theme change", exc)
            refresh_stop_button_backgrounds(app)
            refresh_led_backgrounds(app)
            refresh_theme_widgets(app)
    except tk.TclError as exc:
        _log_suppressed("Failed applying requested Tk theme", exc)
    selected_theme = getattr(app, "selected_theme", None)
    setter = getattr(selected_theme, "set", None)
    if callable(setter) and applied_theme:
        try:
            setter(applied_theme)
        except Exception as exc:
            _log_suppressed("Failed syncing selected-theme variable after theme apply", exc)
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict) and applied_theme:
        try:
            settings["theme"] = applied_theme
        except Exception as exc:
            _log_suppressed("Failed syncing theme setting after theme apply", exc)
    return applied_theme

