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

from datetime import datetime, timedelta
import logging
import os
import time
import tkinter as tk
from tkinter import messagebox, ttk
import tkinter.font as tkfont

from simple_sender.ui.alarm_state import mark_alarm_clear_requested
from simple_sender.ui.file_info_tab import ssmeta_toolpaths, ssmeta_tools
from simple_sender.ui.gcode.stats import format_duration
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.gcode_validator import format_validation_details, format_validation_report
from simple_sender.ui.modal_sync import request_modal_state_sync
from simple_sender.ui.theme_helpers import (
    apply_toggle_indicator_style,
    bind_scrollbar_theme,
    bind_text_display_theme,
    refresh_theme_widgets,
    text_display_theme_options,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _persist_ui_setting_change(app, *, failure_text: str) -> bool:
    if bool(getattr(app, "_defer_ui_settings_save", False)):
        return True
    saver = getattr(app, "_save_settings", None)
    if not callable(saver):
        return True
    try:
        saver()
        return True
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed saving settings after immediate UI setting change", exc)
        try:
            app.status.config(text=failure_text)
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            pass
        return False


def toggle_tooltips(app):
    current = bool(app.tooltip_enabled.get())
    new_val = not current
    app.tooltip_enabled.set(new_val)
    app._refresh_tooltips_toggle_text()


def on_gui_logging_change(app):
    enabled = bool(app.gui_logging_enabled.get())
    status = "enabled" if enabled else "disabled"
    grbl = getattr(app, "grbl", None)
    if grbl is not None:
        setter = getattr(grbl, "set_ui_rx_logging", None)
        if callable(setter):
            try:
                setter(enabled)
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
                _log_suppressed("Failed applying GUI logging setting to GRBL worker", exc)
    try:
        app.streaming_controller.handle_log(f"[settings] GUI logging {status}")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed logging GUI logging setting change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="GUI logging changed for this session only; settings save failed",
    )


def on_theme_change(app, *_):
    app._apply_theme(app.selected_theme.get())
    if hasattr(app, "_refresh_toolbar_action_focus"):
        try:
            app._refresh_toolbar_action_focus()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed refreshing toolbar focus after theme change", exc)
    try:
        app._scrollbar_width_default = _style_scrollbar_width(getattr(app, "style", None))
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed caching default scrollbar width after theme change", exc)
    try:
        app._apply_scrollbar_width()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed applying configured scrollbar width after theme change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Theme changed for this session only; settings save failed",
    )


def on_optional_tab_visibility_change(app) -> None:
    sync_tabs = getattr(app, "_sync_optional_tab_visibility", None)
    if callable(sync_tabs):
        try:
            sync_tabs()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed syncing optional notebook tab visibility", exc)
    try:
        app.status.config(text="Tab visibility updated")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for tab visibility change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Tab visibility changed for this session only; settings save failed",
    )


_UI_SCALE_NAMED_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkHeadingFont",
    "TkMenuFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)

_SCROLLBAR_WIDTHS = {
    "wide": 24,
    "wider": 32,
    "widest": 40,
}
_LINUX_FILE_DIALOG_SCALE_MIN = 1.4
_LINUX_FILE_DIALOG_SCALE_MAX = 3.0
_TOUCH_SCROLL_MODES = {"thumb_only", "thumb_and_swipe"}
_TOUCH_FEEDBACK_STATUS_PREFIX = "Touch received: "
_TOUCH_FEEDBACK_STATUS_MS = 850
_TOUCH_FEEDBACK_PULSE_MS = 120
_TOUCH_FEEDBACK_CANVAS_HIGHLIGHT = "#1b8bd8"

def _style_scrollbar_width(style) -> int | None:
    if style is None:
        return None
    for option in ("width", "arrowsize"):
        try:
            value = style.lookup("TScrollbar", option)
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            continue
        try:
            return int(value)
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            continue
    return None

def _coerce_scrollbar_width(value, default: str = "wide") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
    else:
        normalized = str(value).strip().lower()
    if normalized == "narrow":
        return "default"
    if normalized in _SCROLLBAR_WIDTHS or normalized == "default":
        return normalized
    return default


def _coerce_touch_scroll_mode(value, default: str = "thumb_and_swipe") -> str:
    if value is None:
        return default
    normalized = str(value).strip().lower().replace(" ", "_").replace("+", "_and_")
    if normalized in _TOUCH_SCROLL_MODES:
        return normalized
    return default


def _coerce_linux_file_dialog_scale(value, default: float = _LINUX_FILE_DIALOG_SCALE_MIN) -> float:
    try:
        scale = float(value)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return default
    if scale <= 0:
        return default
    scale = max(_LINUX_FILE_DIALOG_SCALE_MIN, min(_LINUX_FILE_DIALOG_SCALE_MAX, scale))
    return round(scale, 1)


def _coerce_ui_scale(value, default: float = 1.0) -> float:
    try:
        scale = float(value)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return default
    if scale <= 0:
        return default
    scale = max(0.5, min(3.0, scale))
    return round(scale, 2)


def _scaled_font_size(size: int, scale: float) -> int:
    sign = -1 if size < 0 else 1
    value = max(1, int(round(abs(size) * scale)))
    return sign * value


def _apply_scaled_named_fonts(app, scale: float) -> None:
    bases = getattr(app, "_ui_scale_named_font_bases", None)
    if bases is None:
        bases = {}
        for name in _UI_SCALE_NAMED_FONTS:
            try:
                size = int(tkfont.nametofont(name).cget("size"))
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
                continue
            bases[name] = size
        app._ui_scale_named_font_bases = bases
    for name, base in bases.items():
        try:
            tkfont.nametofont(name).configure(size=_scaled_font_size(base, scale))
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            continue


def _apply_scaled_custom_fonts(app, scale: float) -> None:
    bases = getattr(app, "_ui_scale_custom_font_bases", None)
    if bases is None:
        bases = {}
        app._ui_scale_custom_font_bases = bases
    for key in ("icon_button_font", "tab_font", "home_button_font", "dro_value_font", "console_font"):
        font = getattr(app, key, None)
        if font is None:
            continue
        if key not in bases:
            try:
                bases[key] = int(font.cget("size"))
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
                continue
        try:
            font.configure(size=_scaled_font_size(bases[key], scale))
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            continue


def apply_ui_scale(app, value: float | None = None) -> float:
    raw = value
    if raw is None:
        try:
            raw = app.ui_scale.get()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            raw = 1.0
    scale = _coerce_ui_scale(raw, 1.0)
    try:
        app.tk.call("tk", "scaling", scale)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return scale
    _apply_scaled_named_fonts(app, scale)
    _apply_scaled_custom_fonts(app, scale)
    try:
        app.ui_scale.set(scale)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed writing normalized UI scale to Tk variable", exc)
    try:
        app.settings["ui_scale"] = scale
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed persisting normalized UI scale setting", exc)
    try:
        app.update_idletasks()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating idle tasks after UI scale change", exc)
    try:
        apply_toggle_indicator_style(app)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed refreshing toggle indicator style after UI scale change", exc)
    return scale


def apply_scrollbar_width(app, value: str | None = None) -> str:
    raw = value
    if raw is None:
        try:
            raw = app.scrollbar_width.get()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            raw = "wide"
    choice = _coerce_scrollbar_width(raw, "wide")
    if choice == "default":
        width = getattr(app, "_scrollbar_width_default", None)
        if width is None:
            width = _style_scrollbar_width(getattr(app, "style", None))
        if width is None:
            width = 16
    else:
        width = _SCROLLBAR_WIDTHS.get(choice, _SCROLLBAR_WIDTHS["wide"])
    try:
        app._scrollbar_width_px = int(width)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        pass
    try:
        app.style.configure("TScrollbar", width=width)
        app.style.configure("TScrollbar", arrowsize=width)
        app.style.configure("Vertical.TScrollbar", width=width)
        app.style.configure("Vertical.TScrollbar", arrowsize=width)
        app.style.configure("Horizontal.TScrollbar", width=width)
        app.style.configure("Horizontal.TScrollbar", arrowsize=width)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed applying scrollbar width styles", exc)
    try:
        app.scrollbar_width.set(choice)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed writing normalized scrollbar width to Tk variable", exc)
    try:
        app.settings["scrollbar_width"] = choice
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed persisting scrollbar width setting", exc)
    try:
        refresh_theme_widgets(app)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed refreshing theme-managed widgets after scrollbar width change", exc)
    return choice


def on_scrollbar_width_change(app, _event=None):
    choice = apply_scrollbar_width(app)
    try:
        app.status.config(text=f"Scrollbar width: {choice}")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for scrollbar width change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Scrollbar width changed for this session only; settings save failed",
    )


def on_ui_scale_change(app, _event=None):
    scale = apply_ui_scale(app)
    try:
        app.status.config(text=f"UI scale: {scale:.2f}x")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for UI scale change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="UI scale changed for this session only; settings save failed",
    )


def on_linux_file_dialog_scale_change(app, _event=None):
    scale = _coerce_linux_file_dialog_scale(
        app.linux_file_dialog_scale.get() if hasattr(app, "linux_file_dialog_scale") else None,
        _LINUX_FILE_DIALOG_SCALE_MIN,
    )
    try:
        app.linux_file_dialog_scale.set(scale)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed writing normalized Linux file dialog scale to Tk variable", exc)
    try:
        app.settings["linux_file_dialog_scale"] = scale
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed persisting Linux file dialog scale", exc)
    try:
        app.status.config(text=f"Linux file dialog scale: {scale:.1f}x")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for Linux file dialog scale change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Linux file dialog scale changed for this session only; settings save failed",
    )


def on_touch_scroll_mode_change(app, _event=None):
    mode = _coerce_touch_scroll_mode(
        app.touch_scroll_mode.get() if hasattr(app, "touch_scroll_mode") else None,
        "thumb_and_swipe",
    )
    try:
        app.touch_scroll_mode.set(mode)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed writing normalized touch-scroll mode to Tk variable", exc)
    try:
        app.settings["touch_scroll_mode"] = mode
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed persisting touch-scroll mode", exc)

    if mode == "thumb_only":
        try:
            app._unbind_app_settings_touch_scroll()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed disabling App Settings swipe scroll for thumb-only mode", exc)
    else:
        try:
            app._bind_app_settings_touch_scroll()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed enabling App Settings swipe scroll for thumb+swipe mode", exc)
    try:
        app.status.config(
            text=(
                "Touch scroll mode: Thumb only"
                if mode == "thumb_only"
                else "Touch scroll mode: Thumb + swipe"
            )
        )
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for touch-scroll mode change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Touch scroll mode changed for this session only; settings save failed",
    )


def _is_widget_disabled(widget) -> bool:
    try:
        instate = getattr(widget, "instate", None)
        if callable(instate):
            if bool(instate(("disabled",))):
                return True
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        pass
    try:
        state = str(widget.cget("state")).strip().lower()
        return state == "disabled"
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return False


def _looks_like_command_widget(widget) -> bool:
    invoke = getattr(widget, "invoke", None)
    if not callable(invoke):
        return False
    try:
        widget_class = str(widget.winfo_class() or "")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        widget_class = ""
    class_lower = widget_class.lower()
    type_name = str(type(widget).__name__)
    if "button" in class_lower:
        return True
    if type_name == "StopSignButton":
        return True
    if widget_class in {"Button", "Checkbutton", "Radiobutton", "Menubutton"}:
        return True
    return bool(getattr(widget, "_command", None))


def _resolve_command_widget(app, widget):
    current = widget
    while current is not None:
        if _looks_like_command_widget(current):
            return current
        if current is app:
            break
        current = getattr(current, "master", None)
    return None


def _command_widget_label(widget) -> str:
    for key in ("text", "label"):
        try:
            value = str(widget.cget(key) or "").strip()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            continue
        if value:
            return value
    try:
        name = str(widget.winfo_name() or "").replace("_", " ").strip()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        name = ""
    if name:
        return name
    try:
        cls = str(widget.winfo_class() or "").strip()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        cls = "Command"
    return cls or "Command"


def _pulse_command_widget(widget) -> None:
    try:
        state = getattr(widget, "state", None)
        if callable(state):
            def _clear_state_pulse() -> None:
                try:
                    # Preserve the widget's real toggle state. Clearing "selected"
                    # here makes ttk checkbuttons/radiobuttons lie about their
                    # bound variable value after a click.
                    state(["!pressed", "!active"])
                except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
                    _log_suppressed("Failed clearing pressed-state pulse for touch feedback", exc)

            state(["pressed"])
            widget.after(_TOUCH_FEEDBACK_PULSE_MS, _clear_state_pulse)
            return
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed applying pressed-state pulse for touch feedback", exc)
    try:
        relief = widget.cget("relief")
        widget.config(relief="sunken")
        widget.after(_TOUCH_FEEDBACK_PULSE_MS, lambda: widget.config(relief=relief))
        return
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        pass
    try:
        highlight = widget.cget("highlightthickness")
        highlight_bg = widget.cget("highlightbackground")
        widget.config(highlightthickness=2, highlightbackground=_TOUCH_FEEDBACK_CANVAS_HIGHLIGHT)
        widget.after(
            _TOUCH_FEEDBACK_PULSE_MS,
            lambda: widget.config(highlightthickness=highlight, highlightbackground=highlight_bg),
        )
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return


def _status_text(app) -> str:
    try:
        return str(app.status.cget("text") or "")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        return str(getattr(getattr(app, "status", None), "text", "") or "")


def _set_status_text(app, text: str) -> None:
    try:
        app.status.config(text=text)
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed setting status text for touch feedback", exc)


def _restore_touch_feedback_status(app, expected_text: str) -> None:
    try:
        app._touch_feedback_after_id = None
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        pass
    current = _status_text(app)
    if current != expected_text:
        return
    baseline = str(getattr(app, "_touch_feedback_status_baseline", "") or "")
    _set_status_text(app, baseline)


def _show_touch_feedback_status(app, label: str) -> None:
    status_widget = getattr(app, "status", None)
    if status_widget is None:
        return
    current = _status_text(app)
    if not current.startswith(_TOUCH_FEEDBACK_STATUS_PREFIX):
        try:
            app._touch_feedback_status_baseline = current
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            pass
    message = f"{_TOUCH_FEEDBACK_STATUS_PREFIX}{label}"
    _set_status_text(app, message)
    after_id = getattr(app, "_touch_feedback_after_id", None)
    if after_id is not None:
        try:
            app.after_cancel(after_id)
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed canceling previous touch-feedback timer", exc)
    try:
        app._touch_feedback_after_id = app.after(
            _TOUCH_FEEDBACK_STATUS_MS,
            lambda expected=message: _restore_touch_feedback_status(app, expected),
        )
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed scheduling touch-feedback status restore timer", exc)


def on_touch_command_feedback(app, event=None) -> None:
    widget = getattr(event, "widget", None)
    command_widget = _resolve_command_widget(app, widget)
    if command_widget is None or _is_widget_disabled(command_widget):
        return
    label = _command_widget_label(command_widget)
    _pulse_command_widget(command_widget)
    _show_touch_feedback_status(app, label)


def bind_touch_command_feedback(app) -> None:
    if getattr(app, "_touch_command_feedback_bound", False):
        return
    handler = getattr(app, "_on_touch_command_feedback", None)
    if not callable(handler):
        return
    bind_all = getattr(app, "bind_all", None)
    if callable(bind_all):
        bind_all("<ButtonRelease-1>", handler, add="+")
        app._touch_command_feedback_bound = True


def toggle_performance(app):
    app.performance_mode.set(not bool(app.performance_mode.get()))
    on_performance_mode_change(app)


def on_performance_mode_change(app):
    new_val = bool(app.performance_mode.get())
    if not new_val:
        app.streaming_controller.flush_console()
    app._apply_status_poll_profile()
    try:
        status_label = object.__getattribute__(app, "status")
    except Exception:
        status_label = None
    try:
        if status_label is not None and hasattr(status_label, "config"):
            status_label.config(text=f"Performance mode: {'On' if new_val else 'Off'}")
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed updating status text for performance mode change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Performance mode changed for this session only; settings save failed",
    )


def toggle_console_pos_status(app):
    current = bool(app.console_positions_enabled.get())
    new_val = not current
    app.console_positions_enabled.set(new_val)
    if hasattr(app, "btn_console_pos"):
        app.btn_console_pos.config(text="Pos/Status: On" if new_val else "Pos/Status: Off")
    app.streaming_controller.render_console()

def toggle_autolevel_overlay(app):
    current = bool(app.show_autolevel_overlay.get())
    app.show_autolevel_overlay.set(not current)
    on_autolevel_overlay_change(app)

def on_autolevel_overlay_change(app):
    show = bool(app.show_autolevel_overlay.get())
    try:
        app.settings["show_autolevel_overlay"] = show
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed persisting Auto-Level overlay preference", exc)
    try:
        app._refresh_autolevel_overlay_button()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed refreshing Auto-Level overlay toggle button", exc)
    try:
        app._update_quick_button_visibility()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed refreshing quick-button visibility after Auto-Level overlay change", exc)
    _persist_ui_setting_change(
        app,
        failure_text="Auto-Level overlay changed for this session only; settings save failed",
    )


def request_unit_mode_change(app, new_mode: str, *, source: str = "units") -> bool:
    if new_mode not in ("mm", "inch"):
        return False
    current_mode = str(app.unit_mode.get() or "")
    if new_mode == current_mode:
        return True
    stream_busy = False
    try:
        stream_busy = bool(app.grbl.is_streaming())
    except Exception:
        stream_busy = False
    if stream_busy or str(getattr(app, "_stream_state", "") or "") in ("running", "paused") or bool(
        getattr(app, "_stream_done_pending_idle", False)
    ):
        try:
            app.status.config(text="Unit change disabled while streaming")
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            pass
        return False
    if app.grbl.is_connected():
        gcode = "G20" if new_mode == "inch" else "G21"
        accepted = True
        try:
            accepted = app._send_manual(gcode, source)
        except Exception:
            accepted = False
        if accepted is False:
            try:
                app.status.config(text=f"Unit change rejected: controller did not accept {gcode}")
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
                pass
            ui_q = getattr(app, "ui_q", None)
            if ui_q is not None:
                try:
                    ui_q.put(("log", f"[units] Controller rejected {gcode}; UI mode unchanged."))
                except Exception:
                    pass
            return False
        app._pending_unit_mode = new_mode
        try:
            app.status.config(text=f"Unit change requested: waiting for {gcode} confirmation")
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            pass
        request_modal_state_sync(
            app,
            source="status",
            failure_status="Unit change sent, but modal confirmation is pending retry.",
            failure_log="[units] Unit change sent, but $G modal sync was rejected; retry pending.",
        )
        return True
    app._set_unit_mode(new_mode)
    return True


def toggle_unit_mode(app):
    new_mode = "inch" if app.unit_mode.get() == "mm" else "mm"
    request_unit_mode_change(app, new_mode, source="units")

def start_homing(app):
    if not require_grbl_connection(app):
        return False
    if app._stream_state in ("running", "paused") or bool(
        getattr(app, "_stream_done_pending_idle", False)
    ):
        try:
            app.status.config(text="Homing blocked while streaming")
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed updating status text when homing blocked", exc)
        return False
    try:
        accepted = bool(app.grbl.home())
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        accepted = False
    if not accepted:
        try:
            app.status.config(text="Homing rejected: controller did not accept $H")
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            pass
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("log", "[home] Controller rejected $H; homing was not started."))
            except Exception:
                pass
        app._homing_in_progress = False
        app._homing_state_seen = False
        return False
    app._homing_in_progress = True
    app._homing_state_seen = False
    app._homing_start_ts = time.time()
    app._machine_state_text = "Home"
    app.machine_state.set("Homing")
    app._update_state_highlight("Homing")
    mark_alarm_clear_requested(app)
    return True

def confirm_and_run(app, label: str, func):
    try:
        need_confirm = bool(app.training_wheels.get())
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        need_confirm = False
    now = time.time()
    last_ts = app._confirm_last_time.get(label, 0.0)
    if need_confirm:
        if (now - last_ts) < app._confirm_debounce_sec:
            return
        if label in ("Run job", "Resume job"):
            if not _confirm_run_job(app, label):
                return
        else:
            if not messagebox.askyesno("Confirm", f"{label}?"):
                return
    app._confirm_last_time[label] = now
    func()


def _format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "n/a"
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{int(num_bytes)} B"


def _job_estimate_text(app) -> tuple[str, str, str]:
    stats = getattr(app, "_last_stats", None)
    if isinstance(stats, dict):
        time_min = stats.get("time_min")
        rapid_min = stats.get("rapid_min")
    else:
        time_min = None
        rapid_min = None
    try:
        factor = app._estimate_factor_value()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
        factor = 1.0
    feed_only = "n/a"
    total = "n/a"
    finish_at = "n/a"
    fallback_total_min = None
    if time_min is None:
        try:
            raw_fallback_total = getattr(app, "_loaded_estimate_total_min", None)
            if isinstance(raw_fallback_total, (int, float)):
                fallback_total_min = float(raw_fallback_total)
            elif isinstance(raw_fallback_total, (str, bytes, bytearray)):
                fallback_total_min = float(raw_fallback_total)
        except (TypeError, ValueError):
            fallback_total_min = None
    if time_min is not None:
        seconds = int(round(time_min * factor * 60))
        feed_only = format_duration(seconds)
    if time_min is not None and rapid_min is not None:
        total_min = (time_min + rapid_min) * factor
        total_seconds = int(round(total_min * 60))
        total = format_duration(total_seconds)
        rate_source = getattr(app, "_last_rate_source", None)
        if rate_source in ("fallback", "profile", "estimate"):
            total = f"{total} ({rate_source})"
        finish_at = (datetime.now() + timedelta(minutes=total_min)).strftime("%Y-%m-%d %H:%M:%S")
    elif fallback_total_min is not None and fallback_total_min > 0:
        total_min = fallback_total_min * factor
        total_seconds = int(round(total_min * 60))
        total = format_duration(total_seconds)
        finish_at = (datetime.now() + timedelta(minutes=total_min)).strftime("%Y-%m-%d %H:%M:%S")
    return feed_only, total, finish_at


def _run_job_metadata_summary_text(app) -> str:
    ssmeta = getattr(app, "_gcode_ssmeta", None)
    ssmeta_map = dict(ssmeta) if isinstance(ssmeta, dict) else {}
    if not bool(getattr(app, "_gcode_ssmeta_present", False)) or not ssmeta_map:
        return ""

    sections: list[str] = []
    toolpaths = ssmeta_toolpaths(ssmeta_map)
    if toolpaths:
        sections.append(
            "\n".join(
                ["The following toolpaths are about to run:"]
                + [f"- {toolpath}" for toolpath in toolpaths]
            )
        )

    tools = ssmeta_tools(ssmeta_map)
    if tools:
        sections.append(
            "\n".join(
                ["Please ensure the following tools are available and ready:"]
                + [f"- {tool}" for tool in tools]
            )
        )

    return "\n\n".join(section for section in sections if section)


def _confirm_run_job(app, label: str = "Run job") -> bool:
    path = getattr(app, "_last_gcode_path", None)
    name = ""
    if path:
        name = os.path.basename(path)
    else:
        name = getattr(app.grbl, "_gcode_name", "") or "Unknown"
    size = None
    if path and os.path.isfile(path):
        try:
            size = os.path.getsize(path)
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError):
            size = None
    feed_only, total, finish_at = _job_estimate_text(app)

    dialog = tk.Toplevel(app)
    dialog.title(f"Confirm {label.lower()}")
    dialog.transient(app)
    dialog.resizable(False, False)
    dialog.configure(padx=20, pady=16)

    base_font = tkfont.nametofont("TkDefaultFont")
    title_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 4,
        weight="bold",
    )
    label_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 1,
        weight="bold",
    )
    value_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 1,
    )

    title = "Run job?" if label == "Run job" else "Resume job?"
    ttk.Label(dialog, text=title, font=title_font).grid(row=0, column=0, columnspan=2, sticky="w")

    rows = [
        ("File", name),
        ("Size", _format_bytes(size)),
        ("Est time (feed only)", feed_only),
        ("Est time (with rapids)", total),
        ("If started now, finishes at", finish_at),
    ]
    for idx, (label, value) in enumerate(rows, start=1):
        ttk.Label(dialog, text=f"{label}:", font=label_font).grid(
            row=idx, column=0, sticky="w", padx=(0, 12), pady=2
        )
        ttk.Label(dialog, text=value, font=value_font, wraplength=520).grid(
            row=idx, column=1, sticky="w", pady=2
        )
    report = getattr(app, "_gcode_validation_report", None)
    summary_text = _run_job_metadata_summary_text(app)
    next_row = len(rows) + 1
    if summary_text:
        ttk.Label(
            dialog,
            text=summary_text,
            wraplength=520,
            justify="left",
        ).grid(row=next_row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        next_row += 1
    report_text = format_validation_report(report)
    report_row = next_row
    ttk.Label(
        dialog,
        text=report_text,
        wraplength=520,
        justify="left",
    ).grid(row=report_row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    details_window: dict[str, tk.Toplevel | None] = {"win": None}

    def open_details():
        win = details_window.get("win")
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.focus_force()
                    return
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
                _log_suppressed("Failed focusing existing validation-details window", exc)
        win = tk.Toplevel(dialog)
        details_window["win"] = win
        win.title("G-code validation details")
        win.transient(dialog)
        win.minsize(640, 420)
        container = ttk.Frame(win, padding=12)
        container.pack(fill="both", expand=True)
        text = tk.Text(container, wrap="word", height=18)
        themed_options = text_display_theme_options(app)
        if themed_options:
            text.configure(themed_options)
        vsb = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        bind_scrollbar_theme(app, vsb)
        text.configure(yscrollcommand=vsb.set)
        bind_text_display_theme(app, text)
        text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        text.insert("end", format_validation_details(report))
        text.configure(state="disabled")

        def close():
            details_window["win"] = None
            try:
                win.destroy()
            except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
                _log_suppressed("Failed closing validation-details window", exc)

        btn_row = ttk.Frame(container)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btn_row, text="Close", command=close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)
        center_window(win, dialog)

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=report_row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
    result = {"ok": False}

    def accept():
        result["ok"] = True
        try:
            dialog.destroy()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed closing run-confirmation dialog on accept", exc)

    def cancel():
        try:
            dialog.destroy()
        except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
            _log_suppressed("Failed closing run-confirmation dialog on cancel", exc)

    confirm_label = "START"
    if report is not None and getattr(report, "line_issue_count", 0) > 0:
        ttk.Button(btn_frame, text="Details...", command=open_details).pack(
            side="left",
            padx=(0, 6),
        )
    ttk.Button(btn_frame, text=confirm_label, command=accept).pack(side="right", padx=(6, 0))
    ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side="right")
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    center_window(dialog, app)
    try:
        dialog.grab_set()
    except (AttributeError, RuntimeError, tk.TclError, TypeError, ValueError, OSError) as exc:
        _log_suppressed("Failed setting run-confirmation dialog grab", exc)
    dialog.wait_window()
    return result["ok"]


def require_grbl_connection(app) -> bool:
    if not app.grbl.is_connected():
        messagebox.showwarning("Not connected", "Connect to GRBL first.")
        return False
    return True


def run_if_connected(app, func):
    if not require_grbl_connection(app):
        return
    try:
        name = str(getattr(func, "__name__", "") or "").strip().lower()
    except Exception:
        name = ""
    if name in {"unlock", "home"}:
        accepted = func()
        if accepted is True:
            mark_alarm_clear_requested(app)
        return
    func()


def send_manual(app, command: str, source: str) -> bool:
    cmd = str(command or "").strip()
    upper = cmd.upper()
    accepted = bool(app.grbl.send_immediate(cmd, source=source))
    if accepted and (upper.startswith("$X") or upper.startswith("$H")):
        mark_alarm_clear_requested(app)
    return accepted
