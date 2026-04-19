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
import os
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, cast

from simple_sender.constants.messages import MachineStateMessages
from simple_sender.ui.icons import (
    ICON_CONNECT,
    ICON_JOB_CLEAR,
    ICON_JOB_READ,
    ICON_AUTO_LEVEL,
    ICON_PAUSE,
    ICON_RECOVER,
    ICON_REFRESH,
    ICON_RESUME,
    ICON_RESUME_FROM,
    ICON_RUN,
    ICON_STOP,
    ICON_UNLOCK,
    stacked_icon_label,
)
from simple_sender.ui.widgets_tooltips import apply_tooltip
from simple_sender.ui.widgets_common import attach_log_gcode, set_kb_id
from simple_sender.ui.widgets_buttons import ToolbarShapeButton

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_TOOLBAR_GROUP_LABEL_STYLE = "SimpleSender.ToolbarGroup.TLabel"
_TOOLBAR_GROUP_FOCUS_LABEL_STYLE = "SimpleSender.ToolbarGroupFocus.TLabel"
_TOOLBAR_FOCUS_BLUE = "#1565c0"

_TOOLBAR_BUTTON_ACCENTS = {
    "connection": "#5b7cff",
    "job": "#5fd0ff",
    "run": "#2e7d32",
    "pause": "#ef6c00",
    "resume": "#00a79d",
    "stop": "#d83b2d",
    "recovery": "#b08974",
}
_TOOLBAR_BUTTON_DEFAULT_SIZE = 72
_TOOLBAR_ASSET_PRIMARY_DIR = Path(__file__).resolve().parents[1] / "icons"
_TOOLBAR_ASSET_FALLBACK_DIRS = (
    Path(__file__).resolve().parents[3] / "ref" / "icons",
)
_TOOLBAR_ASSET_NAME_BY_KEY = {
    "refresh": "refresh.svg",
    "connect": "connect.svg",
    "read_job": "read_job.svg",
    "clear_job": "clear_job.svg",
    "run": "run.svg",
    "pause": "pause.svg",
    "resume": "resume.svg",
    "stop_reset": "stop.svg",
    "unlock": "unlock.svg",
}


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _widget_exists(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return True


def _widget_mapped(widget: Any) -> bool:
    if not _widget_exists(widget):
        return False
    try:
        return bool(widget.winfo_ismapped())
    except Exception:
        return False


def _widget_enabled(widget: Any) -> bool:
    if not _widget_exists(widget):
        return False
    try:
        state = str(widget.cget("state")).strip().lower()
    except Exception:
        state = str(getattr(widget, "state", "")).strip().lower()
    return state != "disabled"


def _toolbar_has_job(app: Any) -> bool:
    gview = getattr(app, "gview", None)
    if gview is not None:
        try:
            if bool(getattr(gview, "lines_count", 0)):
                return True
        except Exception:
            pass
    if getattr(app, "_gcode_source", None) is not None:
        return True
    return bool(getattr(app, "_last_gcode_lines", None))


def _var_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception as exc:
            _log_suppressed("Failed reading tkinter variable boolean value", exc)
            return default
    return bool(value)


def _toolbar_focus_group(app: Any) -> str | None:
    stream_state = str(getattr(app, "_stream_state", "") or "").lower()
    connected = bool(getattr(app, "connected", False))
    grbl_ready = bool(getattr(app, "_grbl_ready", False))
    status_seen = bool(getattr(app, "_status_seen", False))
    alarm_locked = bool(getattr(app, "_alarm_locked", False))
    has_job = _toolbar_has_job(app)

    if alarm_locked:
        if _widget_enabled(getattr(app, "btn_unlock_top", None)) or _widget_enabled(
            getattr(app, "btn_alarm_recover", None)
        ):
            return "Recovery"
        return None
    if stream_state == "running":
        if _widget_enabled(getattr(app, "btn_pause", None)) or _widget_enabled(
            getattr(app, "btn_stop", None)
        ):
            return "Run"
        return None
    if stream_state == "paused":
        if _widget_enabled(getattr(app, "btn_resume", None)) or _widget_enabled(
            getattr(app, "btn_stop", None)
        ):
            return "Run"
        return None
    if not connected:
        if _widget_enabled(getattr(app, "btn_conn", None)):
            return "Connection"
        return None
    if not grbl_ready or not status_seen:
        return None
    if not has_job:
        if _widget_enabled(getattr(app, "btn_open", None)):
            return "Job"
        return None
    if _widget_enabled(getattr(app, "btn_run", None)) or _widget_enabled(
        getattr(app, "btn_resume_from", None)
    ):
        return "Run"
    if _widget_enabled(getattr(app, "btn_open", None)):
        return "Job"
    return None


def _ensure_toolbar_group_styles(app: Any) -> None:
    style_obj = getattr(app, "style", None)
    if style_obj is not None:
        style = style_obj
    else:
        try:
            style = ttk.Style()
        except Exception as exc:
            _log_suppressed("Failed creating ttk.Style for toolbar group styles", exc)
            return
    palette = getattr(app, "theme_palette", None) or {}
    try:
        label_style = str(getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE))
        focus_style = str(getattr(app, "toolbar_group_focus_label_style", _TOOLBAR_GROUP_FOCUS_LABEL_STYLE))
        default_fg = (
            palette.get("fg")
            or style.lookup("TLabel", "foreground")
            or "#000000"
        )
        default_bg = (
            palette.get("bg")
            or style.lookup("TLabel", "background")
            or style.lookup("TFrame", "background")
            or "#f0f0f0"
        )
        base_font = tkfont.nametofont("TkDefaultFont")
        focus_font = tkfont.Font(
            family=base_font.cget("family"),
            size=base_font.cget("size"),
            weight="bold",
        )
        style_signature = (
            label_style,
            focus_style,
            str(default_fg),
            str(default_bg),
            str(base_font.cget("family")),
            str(base_font.cget("size")),
        )
        if style_signature == getattr(app, "_toolbar_group_style_signature", None):
            return
        app._toolbar_group_focus_font = focus_font
        app.toolbar_group_label_style = label_style
        app.toolbar_group_focus_label_style = focus_style
        app._toolbar_group_style_signature = style_signature
        style.configure(
            label_style,
            foreground=default_fg,
            background=default_bg,
            font=base_font,
        )
        style.configure(
            focus_style,
            foreground=_TOOLBAR_FOCUS_BLUE,
            background=default_bg,
            font=focus_font,
        )
    except Exception as exc:
        _log_suppressed("Failed refreshing toolbar group styles", exc)


def _apply_toolbar_button_style(button: Any, style_name: str) -> None:
    if not _widget_exists(button):
        return
    try:
        button.config(style=style_name)
    except Exception as exc:
        _log_suppressed("Failed applying toolbar button style", exc)


def _apply_toolbar_group_style(label: Any, style_name: str) -> None:
    if not _widget_exists(label):
        return
    try:
        label.config(style=style_name)
    except Exception as exc:
        _log_suppressed("Failed applying toolbar group label style", exc)


def toolbar_button_label(icon: str, *lines: str) -> str:
    return stacked_icon_label(icon, *lines)


def set_toolbar_button_label(button: Any, icon: str, *lines: str) -> None:
    if not _widget_exists(button):
        return
    text = toolbar_button_label(icon, *lines)
    try:
        setter = getattr(button, "set_content", None)
        if callable(setter):
            setter(text)
        else:
            button.config(text=text)
    except Exception as exc:
        _log_suppressed("Failed updating toolbar button label", exc)
    try:
        button._toolbar_accessible_label = " ".join(str(line).strip() for line in lines if str(line).strip())
    except Exception as exc:
        _log_suppressed("Failed caching toolbar button accessible label", exc)


def _toolbar_button_accent(app: Any, role: str) -> str:
    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    if role == "connection":
        return str(palette.get("accent") or _TOOLBAR_BUTTON_ACCENTS["connection"])
    if role == "job":
        return str(
            palette.get("accent_secondary")
            or palette.get("accent")
            or _TOOLBAR_BUTTON_ACCENTS["job"]
        )
    return str(_TOOLBAR_BUTTON_ACCENTS.get(role, palette.get("fg") or "#202020"))


def _toolbar_button_background(app: Any) -> str:
    try:
        bar = getattr(app, "toolbar_bar", None)
        if bar is not None:
            background = str(bar.cget("background") or "").strip()
            if background:
                return background
    except Exception:
        pass
    style = getattr(app, "style", None)
    if style is not None:
        try:
            frame_style = getattr(getattr(app, "toolbar_bar", None), "cget", lambda _key: "")("style") or "TFrame"
            background = str(style.lookup(frame_style, "background") or "").strip()
            if background:
                return background
        except Exception:
            pass
    try:
        return str(app.cget("background") or "").strip() or "#1f2430"
    except Exception:
        return "#1f2430"


def _toolbar_icon_pixel_size(button: Any) -> int:
    width = int(getattr(button, "_width", getattr(button, "width", _TOOLBAR_BUTTON_DEFAULT_SIZE)) or _TOOLBAR_BUTTON_DEFAULT_SIZE)
    height = int(getattr(button, "_height", getattr(button, "height", _TOOLBAR_BUTTON_DEFAULT_SIZE)) or _TOOLBAR_BUTTON_DEFAULT_SIZE)
    return max(18, int(min(width, height) * 0.42))


def _toolbar_asset_svg_path(asset_key: str) -> Path | None:
    filename = _TOOLBAR_ASSET_NAME_BY_KEY.get(str(asset_key or "").strip().lower())
    if not filename:
        return None
    for asset_dir in (_TOOLBAR_ASSET_PRIMARY_DIR, *_TOOLBAR_ASSET_FALLBACK_DIRS):
        svg_path = asset_dir / filename
        if svg_path.exists():
            return svg_path
    return _TOOLBAR_ASSET_PRIMARY_DIR / filename


def _toolbar_asset_raster_path(asset_key: str) -> Path | None:
    svg_filename = _TOOLBAR_ASSET_NAME_BY_KEY.get(str(asset_key or "").strip().lower())
    if not svg_filename:
        return None
    raster_filename = f"{Path(svg_filename).stem}.png"
    for asset_dir in (_TOOLBAR_ASSET_PRIMARY_DIR, *_TOOLBAR_ASSET_FALLBACK_DIRS):
        raster_path = asset_dir / raster_filename
        if raster_path.exists():
            return raster_path
    return _TOOLBAR_ASSET_PRIMARY_DIR / raster_filename


def _load_toolbar_raster_asset_images(
    app: Any,
    *,
    raster_path: Path,
    size_px: int,
    normal_color: str,
    disabled_color: str,
) -> dict[str, object] | None:
    try:
        from PIL import Image, ImageColor, ImageTk
    except Exception as exc:
        _log_suppressed("Failed importing Pillow for toolbar raster icons", exc)
        return None

    def _render(color: str) -> object | None:
        try:
            with Image.open(raster_path) as image:
                rgba = image.convert("RGBA")
            if rgba.size != (size_px, size_px):
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                rgba = rgba.resize((size_px, size_px), resampling)
            alpha = rgba.getchannel("A")
            if alpha.getbbox() is None:
                alpha = rgba.convert("L")
            solid = Image.new("RGBA", rgba.size, ImageColor.getcolor(str(color), "RGBA"))
            solid.putalpha(alpha)
            return cast(object, ImageTk.PhotoImage(solid, master=app))
        except Exception as exc:
            _log_suppressed(f"Failed loading toolbar raster asset '{raster_path.name}'", exc)
            return None

    normal_image = _render(normal_color)
    if normal_image is None:
        return None
    disabled_image = _render(disabled_color) or normal_image
    return {
        "normal": normal_image,
        "disabled": disabled_image,
    }


def _load_toolbar_svg_asset_images(
    app: Any,
    *,
    svg_path: Path,
    size_px: int,
    normal_color: str,
    disabled_color: str,
) -> dict[str, object] | None:
    try:
        from PIL import Image, ImageTk
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except Exception as exc:
        _log_suppressed("Failed importing local SVG render dependencies for toolbar icons", exc)
        return None

    def _render(color: str) -> object | None:
        try:
            svg_text = svg_path.read_text(encoding="utf-8").replace("currentColor", str(color))
            renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
            if not renderer.isValid():
                return None
            image = QImage(size_px, size_px, QImage.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            renderer.render(painter)
            painter.end()
            ptr = image.bits()
            raw = bytes(ptr[: image.sizeInBytes()])
            pil_image = Image.frombuffer(
                "RGBA",
                (image.width(), image.height()),
                raw,
                "raw",
                "BGRA",
                0,
                1,
            ).copy()
            return cast(object, ImageTk.PhotoImage(pil_image, master=app))
        except Exception as exc:
            _log_suppressed(f"Failed rendering toolbar SVG asset '{svg_path.name}'", exc)
            return None

    normal_image = _render(normal_color)
    if normal_image is None:
        return None
    disabled_image = _render(disabled_color) or normal_image
    return {
        "normal": normal_image,
        "disabled": disabled_image,
    }


def _load_toolbar_asset_images(
    app: Any,
    *,
    asset_key: str,
    size_px: int,
    normal_color: str,
    disabled_color: str,
) -> dict[str, object] | None:
    raster_path = _toolbar_asset_raster_path(asset_key)
    svg_path = _toolbar_asset_svg_path(asset_key)
    if (raster_path is None or not raster_path.exists()) and (svg_path is None or not svg_path.exists()):
        return None
    cache = getattr(app, "_toolbar_asset_image_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        app._toolbar_asset_image_cache = cache
    source_path = raster_path if raster_path is not None and raster_path.exists() else svg_path
    if source_path is None:
        return None
    cache_key = (
        str(source_path),
        int(size_px),
        str(normal_color),
        str(disabled_color),
    )
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached:
        return cached
    images = None
    if raster_path is not None and raster_path.exists():
        images = _load_toolbar_raster_asset_images(
            app,
            raster_path=raster_path,
            size_px=size_px,
            normal_color=normal_color,
            disabled_color=disabled_color,
        )
    if images is None and svg_path is not None and svg_path.exists():
        images = _load_toolbar_svg_asset_images(
            app,
            svg_path=svg_path,
            size_px=size_px,
            normal_color=normal_color,
            disabled_color=disabled_color,
        )
    if images is None:
        return None
    cache[cache_key] = images
    return images


def refresh_toolbar_button_theme(app: Any) -> None:
    button_bg = _toolbar_button_background(app)
    palette = getattr(app, "theme_palette", None)
    palette = palette if isinstance(palette, dict) else {}
    disabled_color = str(palette.get("muted_fg") or "#808080")
    for button in getattr(app, "_toolbar_buttons", ()) or ():
        if not isinstance(button, ToolbarShapeButton):
            continue
        role = str(getattr(button, "_toolbar_role", "") or "").strip() or "job"
        try:
            button.refresh_palette(accent=_toolbar_button_accent(app, role), bg=button_bg)
            asset_key = str(getattr(button, "_toolbar_asset_key", "") or "").strip()
            if asset_key:
                images = _load_toolbar_asset_images(
                    app,
                    asset_key=asset_key,
                    size_px=_toolbar_icon_pixel_size(button),
                    normal_color=_toolbar_button_accent(app, role),
                    disabled_color=disabled_color,
                )
                button.set_icon_images(images)
            else:
                button.set_icon_images(None)
        except Exception as exc:
            _log_suppressed("Failed refreshing toolbar shape button palette", exc)


def _toolbar_button_style(app: Any, role: str = "default") -> str:
    role_styles = getattr(app, "top_toolbar_button_styles", None)
    if isinstance(role_styles, dict):
        style_name = str(role_styles.get(role, "") or "").strip()
        if style_name:
            return style_name
    style_name = str(getattr(app, "top_toolbar_button_style", "") or "").strip()
    if style_name:
        return style_name
    style_name = str(getattr(app, "icon_button_style", "") or "").strip()
    return style_name or "TButton"


def _create_toolbar_button(
    app: Any,
    parent: Any,
    *,
    icon: str,
    lines: tuple[str, ...],
    role: str,
    shape: str,
    asset_key: str | None,
    command: Any,
    kb_id: str,
    tooltip: str,
    width: int = _TOOLBAR_BUTTON_DEFAULT_SIZE,
    height: int = _TOOLBAR_BUTTON_DEFAULT_SIZE,
    padx: tuple[int, int] = (0, 0),
    state: str | None = None,
) -> Any:
    style_name = _toolbar_button_style(app, role)
    kwargs: dict[str, Any] = {}
    if state is not None:
        kwargs["state"] = state
    button = ToolbarShapeButton(
        parent,
        text=toolbar_button_label(icon, *lines),
        command=command,
        shape=shape,
        accent=_toolbar_button_accent(app, role),
        width=width,
        height=height,
        style=style_name,
        bg=_toolbar_button_background(app),
        **kwargs,
    )
    try:
        button._toolbar_default_style = style_name
        button._toolbar_role = role
        button._toolbar_asset_key = str(asset_key or "").strip().lower()
        button._toolbar_accessible_label = " ".join(lines)
    except Exception as exc:
        _log_suppressed("Failed caching toolbar button presentation metadata", exc)
    set_kb_id(button, kb_id)
    button.pack(side="left", padx=padx)
    apply_tooltip(button, tooltip)
    return button


def refresh_toolbar_action_focus(app) -> None:
    _ensure_toolbar_group_styles(app)
    default_style = _toolbar_button_style(app, "default")
    tracked_buttons = [
        getattr(app, "btn_conn", None),
        getattr(app, "btn_open", None),
        getattr(app, "btn_run", None),
        getattr(app, "btn_pause", None),
        getattr(app, "btn_resume", None),
        getattr(app, "btn_resume_from", None),
        getattr(app, "btn_stop", None),
        getattr(app, "btn_unlock_top", None),
        getattr(app, "btn_alarm_recover", None),
    ]
    for btn in tracked_buttons:
        style_name = str(getattr(btn, "_toolbar_default_style", "") or "").strip() or default_style
        _apply_toolbar_button_style(btn, style_name)
    group_labels = getattr(app, "_toolbar_group_title_labels", {}) or {}
    default_group_style = str(
        getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE)
    )
    focus_group_style = str(
        getattr(app, "toolbar_group_focus_label_style", _TOOLBAR_GROUP_FOCUS_LABEL_STYLE)
    )
    focus_group = _toolbar_focus_group(app)
    signature = (
        default_style,
        default_group_style,
        focus_group_style,
        focus_group,
        tuple(sorted((str(name), id(label)) for name, label in group_labels.items())),
    )
    if signature == getattr(app, "_toolbar_focus_signature", None):
        return
    app._toolbar_focus_signature = signature
    for label in group_labels.values():
        _apply_toolbar_group_style(label, default_group_style)
    if focus_group:
        _apply_toolbar_group_style(group_labels.get(focus_group), focus_group_style)


def update_job_button_mode(app, mode: str) -> None:
    btn = getattr(app, "btn_open", None)
    if not btn:
        return
    auto_level_enabled = _var_bool(getattr(app, "auto_level_enabled", True), default=True)
    mode = "auto_level" if str(mode).lower() in ("auto_level", "auto-level") else "read_job"
    if not auto_level_enabled:
        mode = "read_job"
    streaming = bool(getattr(app, "_gcode_streaming_mode", False))
    leveled_path = getattr(app, "_last_gcode_path", None)
    leveled = False
    if leveled_path:
        base = os.path.splitext(os.path.basename(leveled_path))[0]
        base_upper = base.upper()
        if base_upper.endswith("-AL"):
            leveled = True
        else:
            prefix, sep, suffix = base_upper.rpartition("-AL-")
            leveled = bool(prefix) and bool(sep) and suffix.isdigit()
    force_disabled = bool(mode == "auto_level" and leveled)
    if (
        getattr(app, "_job_button_mode", None) == mode
        and getattr(app, "_job_button_streaming", None) == streaming
        and getattr(app, "_job_button_leveled", None) == leveled
        and bool(getattr(btn, "_force_disabled", False)) == force_disabled
    ):
        return
    if mode == "auto_level":
        set_toolbar_button_label(btn, ICON_AUTO_LEVEL, "Auto", "Level")
        btn._toolbar_asset_key = ""
        btn.set_icon_images(None)
        btn.config(shape="hex", command=lambda: app._confirm_and_run("Auto-Level", app._show_auto_level_dialog))
        refresh_toolbar_button_theme(app)
        if leveled:
            reason = "Auto-level unavailable (already leveled)."
            tooltip = "Auto-level is already applied. Load the original file to re-level."
        else:
            reason = None
            tooltip = "Probe only the job bounds and build a height map."
        try:
            btn._disabled_reason = reason
        except Exception as exc:
            _log_suppressed("Failed setting Auto-Level disabled reason on job button", exc)
        apply_tooltip(btn, tooltip)
        set_kb_id(btn, "auto_level")
        try:
            app._offline_controls.add(btn)
        except Exception as exc:
            _log_suppressed("Failed adding Auto-Level job button to offline controls", exc)
    else:
        set_toolbar_button_label(btn, ICON_JOB_READ, "Read", "Job")
        btn._toolbar_asset_key = "read_job"
        btn.config(shape="document", command=app.open_gcode)
        refresh_toolbar_button_theme(app)
        try:
            btn._disabled_reason = None
        except Exception as exc:
            _log_suppressed("Failed clearing disabled reason on Read Job button", exc)
        if not auto_level_enabled:
            apply_tooltip(
                btn,
                "Load a G-code job for streaming (Auto-Level disabled in Interface settings).",
            )
        else:
            apply_tooltip(btn, "Load a G-code job for streaming (read-only).")
        set_kb_id(btn, "gcode_open")
        try:
            app._offline_controls.add(btn)
        except Exception as exc:
            _log_suppressed("Failed adding Read Job button to offline controls", exc)
    hint = getattr(app, "job_button_hint", None)
    if hint is not None:
        visible = bool(getattr(app, "_job_button_hint_visible", False))
        if leveled and mode == "auto_level":
            hint.config(text="Auto-Level disabled (already leveled)")
            apply_tooltip(hint, "Auto-level is already applied. Load the original file to re-level.")
            if not visible:
                hint.pack(side="left", padx=(6, 0), after=btn)
                try:
                    app._job_button_hint_visible = True
                except Exception as exc:
                    _log_suppressed("Failed marking job-button hint visible", exc)
        elif visible:
            try:
                hint.pack_forget()
            except Exception as exc:
                _log_suppressed("Failed hiding job-button hint", exc)
            try:
                app._job_button_hint_visible = False
            except Exception as exc:
                _log_suppressed("Failed marking job-button hint hidden", exc)
    try:
        app._job_button_mode = mode
        app._job_button_streaming = streaming
        app._job_button_leveled = leveled
    except Exception as exc:
        _log_suppressed("Failed caching job-button mode state", exc)
    try:
        btn._force_disabled = force_disabled
    except Exception as exc:
        _log_suppressed("Failed setting job-button force-disabled flag", exc)
    if force_disabled:
        try:
            btn.config(state="disabled")
        except Exception as exc:
            _log_suppressed("Failed disabling job button", exc)
    try:
        ready = (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and bool(getattr(app, "_status_seen", False))
            and not bool(getattr(app, "_alarm_locked", False))
        )
        app._set_manual_controls_enabled(ready)
    except Exception as exc:
        _log_suppressed("Failed refreshing manual controls after job-button mode update", exc)
    refresh_toolbar_action_focus(app)


def on_resume_button_visibility_change(app):
    app.settings["show_resume_from_button"] = bool(app.show_resume_from_button.get())
    update_resume_button_visibility(app)


def on_recover_button_visibility_change(app):
    app.settings["show_recover_button"] = bool(app.show_recover_button.get())
    update_recover_button_visibility(app)


def update_resume_button_visibility(app):
    if not hasattr(app, "btn_resume_from"):
        return
    visible = bool(app.show_resume_from_button.get())
    if visible:
        if not _widget_mapped(app.btn_resume_from):
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            app.btn_resume_from.pack(**pack_kwargs)
    else:
        app.btn_resume_from.pack_forget()
    refresh_toolbar_action_focus(app)


def update_recover_button_visibility(app):
    if not hasattr(app, "btn_alarm_recover"):
        return
    visible = bool(app.show_recover_button.get())
    if visible:
        if not _widget_mapped(app.btn_alarm_recover):
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            app.btn_alarm_recover.pack(**pack_kwargs)
    else:
        app.btn_alarm_recover.pack_forget()
    refresh_toolbar_action_focus(app)


def build_toolbar(app):
    bar = ttk.Frame(app, padding=(8, 8, 8, 8))
    bar.pack(side="top", fill="x")
    app.toolbar_bar = bar
    app._toolbar_buttons = []
    _ensure_toolbar_group_styles(app)

    def _build_group(parent, title: str):
        group = ttk.Frame(parent, padding=(0, 0, 8, 0))
        group.pack(side="left", fill="y")
        title_label = ttk.Label(
            group,
            text=title,
            style=str(getattr(app, "toolbar_group_label_style", _TOOLBAR_GROUP_LABEL_STYLE)),
        )
        title_label.pack(side="top", anchor="w")
        row = ttk.Frame(group)
        row.pack(side="top", anchor="w", pady=(4, 0))
        return group, row, title_label

    def _add_group_separator(parent):
        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=(0, 8), pady=(0, 2))

    groups = ttk.Frame(bar)
    groups.pack(side="left", fill="x", expand=True)

    _connection_group, connection_row, connection_label = _build_group(groups, "Connection")
    _add_group_separator(groups)
    _job_group, job_row, job_label = _build_group(groups, "Job")
    _add_group_separator(groups)
    _run_group, run_row, run_label = _build_group(groups, "Run")
    _add_group_separator(groups)
    _recovery_group, recovery_row, recovery_label = _build_group(groups, "Recovery")
    app._toolbar_connection_row = connection_row
    app._toolbar_job_row = job_row
    app._toolbar_run_row = run_row
    app._toolbar_recovery_row = recovery_row
    app._toolbar_group_title_labels = {
        "Connection": connection_label,
        "Job": job_label,
        "Run": run_label,
        "Recovery": recovery_label,
    }

    ttk.Label(connection_row, text="Port:").pack(side="left", padx=(0, 2))
    app.port_combo = ttk.Combobox(connection_row, width=18, textvariable=app.current_port, state="readonly")
    app.port_combo.pack(side="left", padx=(6, 8))

    app.btn_refresh = _create_toolbar_button(
        app,
        connection_row,
        icon=ICON_REFRESH,
        lines=("Refresh",),
        role="connection",
        shape="refresh",
        asset_key="refresh",
        command=app.refresh_ports,
        kb_id="port_refresh",
        tooltip="Refresh the list of serial ports.",
        width=70,
        padx=(0, 6),
    )
    app._toolbar_buttons.append(app.btn_refresh)
    app.btn_conn = _create_toolbar_button(
        app,
        connection_row,
        icon=ICON_CONNECT,
        lines=("Connect",),
        role="connection",
        shape="bolt",
        asset_key="connect",
        command=lambda: app._confirm_and_run("Connect/Disconnect", app.toggle_connect),
        kb_id="port_connect",
        tooltip="Connect or disconnect from the selected serial port.",
        width=70,
    )
    app._toolbar_buttons.append(app.btn_conn)
    attach_log_gcode(app.btn_conn, "")

    app.btn_open = _create_toolbar_button(
        app,
        job_row,
        icon=ICON_JOB_READ,
        lines=("Read", "Job"),
        role="job",
        shape="document",
        asset_key="read_job",
        command=app.open_gcode,
        kb_id="gcode_open",
        tooltip="Load a G-code job for streaming (read-only).",
    )
    app._toolbar_buttons.append(app.btn_open)
    app._manual_controls.append(app.btn_open)
    app._offline_controls.add(app.btn_open)
    app.job_button_hint = ttk.Label(job_row, text="")
    try:
        app._job_button_hint_visible = False
    except Exception as exc:
        _log_suppressed("Failed initializing job-button hint visibility flag", exc)
    try:
        app._job_button_mode = "read_job"
    except Exception as exc:
        _log_suppressed("Failed initializing default job-button mode", exc)
    app.btn_clear = _create_toolbar_button(
        app,
        job_row,
        icon=ICON_JOB_CLEAR,
        lines=("Clear", "Job"),
        role="job",
        shape="tray",
        asset_key="clear_job",
        command=lambda: app._confirm_and_run("Clear Job", app._clear_gcode),
        kb_id="gcode_clear",
        tooltip="Unload the current job and reset the live job state.",
        padx=(6, 0),
    )
    app._toolbar_buttons.append(app.btn_clear)
    app._manual_controls.append(app.btn_clear)
    app._offline_controls.add(app.btn_clear)
    app.btn_run = _create_toolbar_button(
        app,
        run_row,
        icon=ICON_RUN,
        lines=("Run",),
        role="run",
        shape="play",
        asset_key="run",
        command=lambda: app._confirm_and_run("Run job", app.run_job),
        kb_id="job_run",
        tooltip="Start streaming the loaded G-code.",
        padx=(8, 0),
        width=66,
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_run)
    attach_log_gcode(app.btn_run, "Cycle Start")
    app.btn_pause = _create_toolbar_button(
        app,
        run_row,
        icon=ICON_PAUSE,
        lines=("Pause",),
        role="pause",
        shape="pause",
        asset_key="pause",
        command=lambda: app._confirm_and_run("Pause job", app.pause_job),
        kb_id="job_pause",
        tooltip="Feed hold the running job.",
        padx=(6, 0),
        width=66,
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_pause)
    attach_log_gcode(app.btn_pause, "!")
    app.btn_resume = _create_toolbar_button(
        app,
        run_row,
        icon=ICON_RESUME,
        lines=("Resume",),
        role="resume",
        shape="play",
        asset_key="resume",
        command=lambda: app._confirm_and_run("Resume job", app.resume_job),
        kb_id="job_resume",
        tooltip="Resume a paused job.",
        padx=(6, 0),
        width=66,
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_resume)
    attach_log_gcode(app.btn_resume, "~")
    app.btn_stop = _create_toolbar_button(
        app,
        run_row,
        icon=ICON_STOP,
        lines=("Stop", "Reset"),
        role="stop",
        shape="square",
        asset_key="stop_reset",
        command=lambda: app._confirm_and_run("Stop/Reset", app.stop_job),
        kb_id="job_stop_reset",
        tooltip="Stop the job and soft reset GRBL.",
        padx=(6, 0),
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_stop)
    attach_log_gcode(app.btn_stop, "Ctrl-X")
    app.btn_resume_from = _create_toolbar_button(
        app,
        run_row,
        icon=ICON_RESUME_FROM,
        lines=("Resume", "From"),
        role="resume",
        shape="return",
        asset_key=None,
        command=lambda: app._confirm_and_run("Resume from line", app._show_resume_dialog),
        kb_id="job_resume_from",
        tooltip="Resume from a specific line with modal re-sync.",
        padx=(6, 0),
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_resume_from)
    app.btn_unlock_top = _create_toolbar_button(
        app,
        recovery_row,
        icon=ICON_UNLOCK,
        lines=("Unlock",),
        role="recovery",
        shape="lock",
        asset_key="unlock",
        command=lambda: app._confirm_and_run(
            "Unlock ($X)", lambda: app._run_if_connected(app.grbl.unlock)
        ),
        kb_id="unlock_top",
        tooltip="Send $X to clear alarm (top-bar).",
        padx=(6, 0),
        width=68,
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_unlock_top)
    app._manual_controls.append(app.btn_unlock_top)
    app.btn_alarm_recover = _create_toolbar_button(
        app,
        recovery_row,
        icon=ICON_RECOVER,
        lines=("Recover",),
        role="recovery",
        shape="shield",
        asset_key=None,
        command=app._show_alarm_recovery,
        kb_id="alarm_recover",
        tooltip="Show alarm recovery steps.",
        padx=(6, 0),
        state="disabled",
    )
    app._toolbar_buttons.append(app.btn_alarm_recover)

    app._recover_separator = None

    app._update_resume_button_visibility()
    app._update_recover_button_visibility()

    # right side status
    base_font = tkfont.nametofont("TkDefaultFont")
    try:
        size = int(base_font.cget("size"))
    except Exception as exc:
        _log_suppressed("Failed reading toolbar base font size; using default", exc)
        size = 10
    style = ttk.Style()
    frame_style = bar.cget("style") or "TFrame"
    bar_bg = style.lookup(frame_style, "background") or app.cget("background")
    state_font = tkfont.Font(
        family=base_font.cget("family"),
        size=size + 2,
        weight=base_font.cget("weight"),
    )
    app.machine_state_label = tk.Label(
        bar,
        textvariable=app.machine_state,
        font=state_font,
        fg=style.lookup("TLabel", "foreground") or "#000000",
        bg=bar_bg,
        padx=20,
        pady=6,
    )
    app.machine_state_label.pack(side="right", fill="y")
    try:
        app._state_default_bg = app.machine_state_label.cget("background")
    except Exception as exc:
        _log_suppressed("Failed caching default state-label background", exc)
    try:
        app._state_default_fg = app.machine_state_label.cget("foreground")
    except Exception as exc:
        _log_suppressed("Failed caching default state-label foreground", exc)
    try:
        app._machine_state_max_chars = len(MachineStateMessages.DISCONNECTED) + 2
        app.machine_state_label.config(width=app._machine_state_max_chars)
    except Exception as exc:
        _log_suppressed("Failed sizing machine-state label width", exc)
    try:
        app._ensure_state_label_width(app.machine_state.get())
    except Exception as exc:
        _log_suppressed("Failed enforcing machine-state label width", exc)
    try:
        from simple_sender.ui.theme_helpers import register_theme_refresh

        register_theme_refresh(app, lambda: refresh_toolbar_button_theme(app))
    except Exception as exc:
        _log_suppressed("Failed registering toolbar button theme refresh callback", exc)
    refresh_toolbar_button_theme(app)
    refresh_toolbar_action_focus(app)


