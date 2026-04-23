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
from tkinter import ttk

from simple_sender.ui.checklists_tab import build_checklists_panel
from simple_sender.ui.console import build_console_panel
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window
from simple_sender.ui.file_info_tab import build_file_info_panel
from simple_sender.ui.help_dialog import build_help_about_panel
from simple_sender.ui.log_viewer import LogViewer
from simple_sender.ui.overdrive_tab import build_overdrive_tab
from simple_sender.ui.settings import (
    activate_app_settings_surface,
    build_app_settings_panel,
    deactivate_app_settings_surface,
)
from simple_sender.ui.theme_helpers import (
    notebook_page_style_name,
)
from simple_sender.ui.viewer.gcode_viewer import HeadlessGcodeView
from simple_sender.ui.widgets_common import set_kb_id
from simple_sender.ui.widgets_tooltips import apply_tooltip, set_tab_tooltip

logger = logging.getLogger(__name__)

_POPUP_GEOMETRY = {
    "file_info": (1200, 860),
    "grbl_settings": (1280, 900),
    "logs": (1280, 900),
    "checklists": (1100, 860),
    "app_settings": (1380, 940),
    "help": (1380, 940),
}


def _lower_popup_button_entries(app) -> list[dict]:
    entries = [
        {
            "button": getattr(app, "btn_job_info_popup", None),
            "label": "Job Info",
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_checklists_popup", None),
            "label": "Checklists",
            "variable_name": "show_checklists_button",
            "default": True,
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_logs_popup", None),
            "label": "Logs",
            "variable_name": "show_logs_button",
            "default": False,
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_raw_grbl_popup", None),
            "label": "Raw $$",
            "variable_name": "show_raw_grbl_button",
            "default": False,
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_grbl_settings_popup", None),
            "label": "GRBL Settings",
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_app_settings_popup", None),
            "label": "App Settings",
            "pack_kwargs": {"side": "left", "padx": (0, 6)},
        },
        {
            "button": getattr(app, "btn_help_popup", None),
            "label": "About",
            "pack_kwargs": {"side": "left"},
        },
    ]
    return [entry for entry in entries if entry.get("button") is not None]


def _sync_lower_popup_button_order(app) -> None:
    entries = getattr(app, "_lower_popup_buttons", None)
    if not isinstance(entries, list) or not entries:
        entries = _lower_popup_button_entries(app)
    visible_entries: list[dict] = []
    for entry in entries:
        variable_name = str(entry.get("variable_name") or "").strip()
        if variable_name:
            should_show = _auxiliary_button_should_be_visible(
                app,
                variable_name,
                bool(entry.get("default", False)),
            )
        else:
            should_show = True
        button = entry.get("button")
        if button is None:
            continue
        if bool(getattr(button, "winfo_manager", lambda: "")()):
            try:
                button.pack_forget()
            except Exception:
                logger.exception("Failed resetting lower popup button %r", entry.get("label"))
        if should_show:
            visible_entries.append(entry)
    for entry in visible_entries:
        _set_button_visibility(
            entry.get("button"),
            True,
            dict(entry.get("pack_kwargs") or {}),
        )


def _var_bool(value, default: bool) -> bool:
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return default
    if value is None:
        return default
    return bool(value)


def _auxiliary_button_should_be_visible(app, variable_name: str, default: bool) -> bool:
    return _var_bool(getattr(app, variable_name, None), default)


def _set_button_visibility(button, should_show: bool, pack_kwargs: dict | None = None) -> None:
    if button is None:
        return
    try:
        visible = bool(button.winfo_manager())
    except Exception:
        visible = False
    if should_show:
        if not visible:
            kwargs = dict(pack_kwargs or {})
            try:
                button.pack(**kwargs)
            except Exception:
                logger.exception("Failed showing lower access button %r", button)
        return
    if visible:
        try:
            button.pack_forget()
        except Exception:
            logger.exception("Failed hiding lower access button %r", button)


def _register_optional_buttons(app) -> None:
    app._lower_popup_buttons = _lower_popup_button_entries(app)


def sync_auxiliary_button_visibility(app) -> None:
    if getattr(app, "_lower_popup_buttons", None):
        _sync_lower_popup_button_order(app)


def _build_gcode_view_runtime_state(app) -> None:
    """Install the headless job-view state holder used by the current runtime.

    The old lower G-code pane is gone. Runtime helpers still rely on ``app.gview``
    for light state bookkeeping, so use a non-widget state holder instead of
    keeping an off-screen Text/Scrollbar tree alive.
    """

    app.gview = HeadlessGcodeView()


def _report_popup_activation_failure(
    app,
    *,
    key: str,
    title: str,
    phase: str,
    exc: BaseException,
) -> None:
    logger.warning(
        "Lower popup %r could not %s: %s",
        key,
        phase,
        exc,
        exc_info=exc,
    )
    ui_q = getattr(app, "ui_q", None)
    if ui_q is None:
        return
    try:
        ui_q.put(
            (
                "log",
                "[ui] "
                f"{title} popup opened but could not {phase}. "
                "Check popup stacking/focus behavior on this desktop.",
            )
        )
    except Exception:
        logger.exception("Failed queueing popup activation warning for %r", key)


def _show_popup_window(
    app,
    *,
    key: str,
    title: str,
    build_body,
    width: int,
    height: int,
    on_show=None,
    on_hide=None,
    present: bool = True,
) -> tk.Toplevel:
    popup_windows = getattr(app, "_lower_popup_windows", None)
    if not isinstance(popup_windows, dict):
        popup_windows = {}
        app._lower_popup_windows = popup_windows

    popup = popup_windows.get(key)
    popup_exists = False
    if popup is not None:
        try:
            popup_exists = bool(popup.winfo_exists())
        except Exception:
            popup_exists = False
    if not popup_exists:
        popup = tk.Toplevel(app)
        popup_windows[key] = popup
        popup.title(title)
        popup.geometry(f"{int(width)}x{int(height)}")
        popup.minsize(max(900, int(width * 0.7)), max(650, int(height * 0.7)))
        apply_toplevel_theme(popup, app)
        body = ttk.Frame(popup, padding=8, style=notebook_page_style_name())
        body.pack(fill="both", expand=True)
        build_body(body, popup)

        def _hide_popup() -> None:
            if callable(on_hide):
                try:
                    on_hide()
                except Exception:
                    logger.exception("Failed hiding lower popup %r", key)
            try:
                popup.withdraw()
            except Exception:
                logger.exception("Failed withdrawing lower popup %r", key)

        popup.protocol("WM_DELETE_WINDOW", _hide_popup)
        center_window(popup, app)
        if not bool(present):
            try:
                popup.withdraw()
            except Exception:
                logger.exception("Failed prewarming hidden lower popup %r", key)
    else:
        if popup is None:
            raise RuntimeError(f"Lower popup window {key!r} is missing from the popup cache")
        if bool(present):
            try:
                popup.deiconify()
            except Exception:
                logger.exception("Failed restoring lower popup %r", key)
    if popup is None:
        raise RuntimeError(f"Lower popup window {key!r} could not be created")
    apply_toplevel_theme(popup, app)
    if bool(present):
        try:
            popup.lift()
            popup.focus_force()
        except Exception as exc:
            _report_popup_activation_failure(
                app,
                key=key,
                title=title,
                phase="raise/focus",
                exc=exc,
            )
        if callable(on_show):
            on_show()
    return popup


def _show_file_info_popup(app) -> tk.Toplevel:
    return _show_popup_window(
        app,
        key="file_info",
        title="Job Info",
        width=_POPUP_GEOMETRY["file_info"][0],
        height=_POPUP_GEOMETRY["file_info"][1],
        build_body=lambda body, _popup: build_file_info_panel(app, body).pack(fill="both", expand=True),
    )


def _show_logs_popup(app) -> None:
    def _build(body, _popup) -> None:
        viewer = LogViewer(body, app)
        viewer.pack(fill="both", expand=True)
        app.logs_viewer = viewer

    _show_popup_window(
        app,
        key="logs",
        title="Logs",
        width=_POPUP_GEOMETRY["logs"][0],
        height=_POPUP_GEOMETRY["logs"][1],
        build_body=_build,
    )


def _show_checklists_popup(app) -> None:
    _show_popup_window(
        app,
        key="checklists",
        title="Checklists",
        width=_POPUP_GEOMETRY["checklists"][0],
        height=_POPUP_GEOMETRY["checklists"][1],
        build_body=lambda body, _popup: build_checklists_panel(app, body).pack(fill="both", expand=True),
    )


def _show_grbl_settings_popup(app, *, select_raw: bool) -> tk.Toplevel:
    def _build(body, _popup) -> None:
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)
        raw_view, settings_view = app.settings_controller.build_views(
            raw_parent=nb,
            settings_parent=nb,
        )
        if raw_view is not None:
            nb.add(raw_view, text="Raw $$")
            set_tab_tooltip(nb, raw_view, "View the raw $$ settings dump from GRBL.")
            app._grbl_settings_popup_raw_tab = raw_view
        if settings_view is not None:
            nb.add(settings_view, text="GRBL Settings")
            set_tab_tooltip(nb, settings_view, "Edit GRBL configuration values and save changes.")
            app._grbl_settings_popup_settings_tab = settings_view
        app._grbl_settings_popup_notebook = nb

    popup = _show_popup_window(
        app,
        key="grbl_settings",
        title="GRBL Settings",
        width=_POPUP_GEOMETRY["grbl_settings"][0],
        height=_POPUP_GEOMETRY["grbl_settings"][1],
        build_body=_build,
    )
    nb = getattr(app, "_grbl_settings_popup_notebook", None)
    if nb is None:
        return popup
    target = (
        getattr(app, "_grbl_settings_popup_raw_tab", None)
        if bool(select_raw)
        else getattr(app, "_grbl_settings_popup_settings_tab", None)
    )
    if target is not None:
        try:
            nb.select(target)
        except Exception:
            logger.exception("Failed selecting GRBL popup page")
    try:
        popup.lift()
    except Exception:
        pass
    return popup


def _show_app_settings_popup(app, *, present: bool = True) -> None:
    def _on_hide() -> None:
        try:
            deactivate_app_settings_surface(app)
        except Exception:
            logger.exception("Failed deactivating App Settings popup surface")

    def _activate_if_viewable(popup) -> None:
        try:
            if not bool(popup.winfo_exists()) or not bool(popup.winfo_viewable()):
                return
            activate_app_settings_surface(app)
        except Exception:
            logger.exception("Failed activating App Settings popup surface")

    def _build_placeholder(body, popup) -> None:
        popup._simple_sender_app_settings_body = body
        popup._simple_sender_app_settings_built = False
        popup._simple_sender_app_settings_build_scheduled = False
        ttk.Label(
            body,
            text="Loading App Settings...",
        ).pack(anchor="w", padx=8, pady=8)

    popup = _show_popup_window(
        app,
        key="app_settings",
        title="App Settings",
        width=_POPUP_GEOMETRY["app_settings"][0],
        height=_POPUP_GEOMETRY["app_settings"][1],
        build_body=_build_placeholder,
        on_hide=_on_hide,
        present=bool(present),
    )
    if bool(getattr(popup, "_simple_sender_app_settings_built", False)):
        if bool(present):
            _activate_if_viewable(popup)
        return

    def _finish_build() -> None:
        try:
            setattr(popup, "_simple_sender_app_settings_build_scheduled", False)
        except Exception:
            pass
        try:
            if not bool(popup.winfo_exists()):
                return
        except Exception:
            return
        if bool(getattr(popup, "_simple_sender_app_settings_built", False)):
            _activate_if_viewable(popup)
            return
        body = getattr(popup, "_simple_sender_app_settings_body", None)
        if body is None:
            return
        try:
            for child in tuple(body.winfo_children()):
                child.destroy()
        except Exception:
            logger.exception("Failed clearing App Settings popup placeholder")
        build_app_settings_panel(app, body).pack(fill="both", expand=True)
        setattr(popup, "_simple_sender_app_settings_built", True)
        if bool(present):
            _activate_if_viewable(popup)

    if bool(getattr(popup, "_simple_sender_app_settings_build_scheduled", False)):
        return
    try:
        setattr(popup, "_simple_sender_app_settings_build_scheduled", True)
        popup.after_idle(_finish_build)
    except Exception:
        _finish_build()


def _show_help_popup(app) -> tk.Toplevel:
    def _build(body, popup) -> None:
        popup.tooltip_enabled = tk.BooleanVar(master=popup, value=False)
        build_help_about_panel(app, body).pack(fill="both", expand=True)

    def _focus_search() -> None:
        panel = getattr(app, "help_about_panel", None)
        if panel is None:
            return
        focus = getattr(panel, "focus_search", None)
        if callable(focus):
            focus()

    return _show_popup_window(
        app,
        key="help",
        title="About",
        width=_POPUP_GEOMETRY["help"][0],
        height=_POPUP_GEOMETRY["help"][1],
        build_body=_build,
        on_show=_focus_search,
    )


def _prewarm_app_settings_popup(app) -> None:
    if bool(getattr(app, "_closing", False)):
        return
    _show_app_settings_popup(app, present=False)


def _schedule_app_settings_prewarm(app, *, delay_ms: int = 800) -> None:
    pending = getattr(app, "_app_settings_prewarm_after_id", None)
    if pending is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(pending)
        except Exception:
            logger.exception("Failed canceling pending App Settings prewarm timer")
        finally:
            app._app_settings_prewarm_after_id = None
    if bool(getattr(app, "_closing", False)):
        return
    popup = getattr(app, "_lower_popup_windows", {}).get("app_settings")
    if popup is not None and bool(getattr(popup, "_simple_sender_app_settings_built", False)):
        return

    def _run() -> None:
        try:
            app._app_settings_prewarm_after_id = None
        except Exception:
            pass
        _prewarm_app_settings_popup(app)

    after = getattr(app, "after", None)
    if not callable(after):
        _run()
        return
    try:
        app._app_settings_prewarm_after_id = after(max(0, int(delay_ms)), _run)
    except Exception:
        _run()


def _build_popup_button(parent, *, text: str, command, kb_id: str, tooltip: str):
    button = ttk.Button(parent, text=text, command=command)
    set_kb_id(button, kb_id)
    apply_tooltip(button, tooltip)
    return button


def build_main_tabs(app, parent):
    shell = ttk.Frame(parent, padding=(0, 10, 0, 0), style=notebook_page_style_name())
    shell.pack(side="top", fill="both", expand=True)
    shell.grid_columnconfigure(0, weight=1)
    shell.grid_columnconfigure(1, weight=1)
    shell.grid_rowconfigure(0, weight=0)
    shell.grid_rowconfigure(1, weight=1)

    app.notebook = None
    app.file_info_tab = None
    app.logs_tab = None
    app.checklists_tab = None
    app._lower_popup_windows = {}

    _build_gcode_view_runtime_state(app)

    control_row = ttk.Frame(shell, style=notebook_page_style_name())
    control_row.grid(row=0, column=0, sticky="ew", pady=(0, 6), padx=(0, 6))
    app.lower_control_row = control_row

    left_buttons = ttk.Frame(control_row, style=notebook_page_style_name())
    left_buttons.pack(side="left", anchor="w")
    app.lower_button_row = left_buttons
    app.lower_optional_button_row = left_buttons
    app.lower_primary_button_row = left_buttons

    app.btn_logs_popup = _build_popup_button(
        left_buttons,
        text="Logs",
        command=lambda: _show_logs_popup(app),
        kb_id="show_logs_popup",
        tooltip="Open the log viewer in a large popup.",
    )
    app.btn_raw_grbl_popup = _build_popup_button(
        left_buttons,
        text="Raw $$",
        command=lambda: _show_grbl_settings_popup(app, select_raw=True),
        kb_id="show_raw_grbl_popup",
        tooltip="Open the raw GRBL settings dump in a large popup.",
    )
    app.btn_checklists_popup = _build_popup_button(
        left_buttons,
        text="Checklists",
        command=lambda: _show_checklists_popup(app),
        kb_id="show_checklists_popup",
        tooltip="Open setup and safety checklists in a large popup.",
    )

    app.btn_job_info_popup = _build_popup_button(
        left_buttons,
        text="Job Info",
        command=lambda: _show_file_info_popup(app),
        kb_id="show_job_info_popup",
        tooltip="Open loaded job details in a large popup.",
    )
    app.btn_file_info_popup = app.btn_job_info_popup
    app.btn_grbl_settings_popup = _build_popup_button(
        left_buttons,
        text="GRBL Settings",
        command=lambda: _show_grbl_settings_popup(app, select_raw=False),
        kb_id="show_grbl_settings_popup",
        tooltip="Open GRBL Settings in a large popup.",
    )
    app.btn_app_settings_popup = _build_popup_button(
        left_buttons,
        text="App Settings",
        command=lambda: _show_app_settings_popup(app),
        kb_id="show_app_settings_popup",
        tooltip="Open App Settings in a large popup.",
    )
    app.btn_help_popup = _build_popup_button(
        left_buttons,
        text="About",
        command=lambda: _show_help_popup(app),
        kb_id="show_help_popup",
        tooltip="Open the About popup.",
    )

    console_pane = ttk.Labelframe(shell, text="Console", padding=0)
    console_pane.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
    overdrive_pane = ttk.Frame(shell, padding=0, style=notebook_page_style_name())
    overdrive_pane.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
    app.lower_split_frame = shell
    app.console_pane = console_pane
    app.overdrive_pane = overdrive_pane

    build_console_panel(app, console_pane).pack(fill="both", expand=True)
    build_overdrive_tab(app, overdrive_pane)

    _register_optional_buttons(app)
    sync_auxiliary_button_visibility(app)
    app._active_tab_label = "Console"
    app._app_settings_tab_active = False
    app._app_settings_prewarm_after_id = None
    app._prewarm_app_settings_popup = lambda: _prewarm_app_settings_popup(app)
    app._schedule_app_settings_prewarm = lambda delay_ms=800: _schedule_app_settings_prewarm(
        app,
        delay_ms=int(delay_ms),
    )
