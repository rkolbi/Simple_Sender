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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import queue
from typing import Any, cast

# GUI imports
import tkinter as tk
from simple_sender.constants.messages import MachineStateMessages
from simple_sender.ui.controls.toolbar import (
    on_recover_button_visibility_change,
    refresh_toolbar_action_focus,
    on_resume_button_visibility_change,
    update_job_button_mode,
    update_recover_button_visibility,
    update_resume_button_visibility,
)
from simple_sender.ui.dialogs import show_macro_prompt
from simple_sender.ui.main_tabs import sync_auxiliary_button_visibility
from simple_sender.ui.manual_controls import clear_widget_transient_state
from simple_sender.ui.settings import (
    bind_app_settings_mousewheel,
    bind_app_settings_touch_scroll,
    on_app_settings_mousewheel,
    on_app_settings_touch_end,
    on_app_settings_touch_move,
    on_app_settings_touch_start,
    unbind_app_settings_mousewheel,
    unbind_app_settings_touch_scroll,
    update_app_settings_scrollregion,
)

MACRO_STATUS_SCROLL_INTERVAL_MS = 200
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


class UiEventsMixin:
    def _start_status_banner(self, prefix: str, name: str) -> None:
        app = cast(Any, self)
        self._clear_manual_control_transient_states()
        base_name = (name or prefix).strip() or prefix
        base_prefix = (prefix or "Status").strip() or "Status"
        app._macro_status_text = f"{base_prefix}: {base_name}"
        app._macro_status_scroll_index = 0
        app._macro_status_active = True
        app._macro_status_width = getattr(app, "_machine_state_max_chars", 0) or (
            len(MachineStateMessages.DISCONNECTED) + 2
        )
        app._cancel_state_flash()
        app._apply_state_fg("#00c853")
        app._update_macro_status_display()

    def _clear_manual_control_transient_states(self) -> None:
        app = cast(Any, self)
        for widget in getattr(app, "_manual_controls", ()):
            try:
                clear_widget_transient_state(widget)
            except Exception:
                continue

    def _on_app_focus_out(self, event=None):
        _ = event
        app = cast(Any, self)
        if not bool(app.stop_hold_on_focus_loss.get()):
            return
        try:
            app.after_idle(self._maybe_stop_joystick_hold_on_focus_loss)
        except Exception:
            self._maybe_stop_joystick_hold_on_focus_loss()

    def _maybe_stop_joystick_hold_on_focus_loss(self):
        app = cast(Any, self)
        if not bool(app.stop_hold_on_focus_loss.get()):
            return
        try:
            focus_widget = app.focus_get()
        except (tk.TclError, KeyError):
            return
        if focus_widget is None and getattr(app, "_active_joystick_hold_binding", None):
            app._stop_joystick_hold()

    def _show_macro_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        cancel_label: str,
        result_q: queue.Queue,
    ) -> None:
        show_macro_prompt(self, title, message, choices, cancel_label, result_q)

    def _sync_auxiliary_button_visibility(self):
        sync_auxiliary_button_visibility(self)

    def _update_app_settings_scrollregion(self):
        update_app_settings_scrollregion(self)

    def _on_app_settings_mousewheel(self, event):
        on_app_settings_mousewheel(self, event)

    def _bind_app_settings_mousewheel(self):
        bind_app_settings_mousewheel(self)

    def _unbind_app_settings_mousewheel(self):
        unbind_app_settings_mousewheel(self)

    def _on_app_settings_touch_start(self, event):
        on_app_settings_touch_start(self, event)

    def _on_app_settings_touch_move(self, event):
        on_app_settings_touch_move(self, event)

    def _on_app_settings_touch_end(self, event=None):
        on_app_settings_touch_end(self, event)

    def _bind_app_settings_touch_scroll(self):
        bind_app_settings_touch_scroll(self)

    def _unbind_app_settings_touch_scroll(self):
        unbind_app_settings_touch_scroll(self)

    def _on_auto_level_enabled_change(self):
        app = cast(Any, self)
        enabled = bool(app.auto_level_enabled.get())
        try:
            app.settings["auto_level_enabled"] = enabled
        except Exception as exc:
            _log_suppressed("Failed persisting auto-level-enabled preference", exc)
        frame = getattr(app, "auto_level_frame", None)
        if frame is not None:
            try:
                if enabled:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception as exc:
                _log_suppressed("Failed toggling auto-level settings frame visibility", exc)
        if enabled:
            if getattr(app, "_last_gcode_lines", None) or getattr(app, "_gcode_source", None):
                app._set_job_button_mode("auto_level")
        else:
            app._set_job_button_mode("read_job")

    def _start_macro_status(self, name: str):
        self._start_status_banner("Macro", name)

    def _start_workflow_status(self, name: str):
        self._start_status_banner("Workflow", name)

    def _update_macro_status_display(self):
        app = cast(Any, self)
        if not app._macro_status_active:
            return
        width = int(getattr(app, "_macro_status_width", 0) or 0)
        text = app._macro_status_text
        if width <= 0:
            width = len(text)
        if len(text) <= width:
            display = text.ljust(width)
        else:
            gap = "   "
            scroll = text + gap
            start = app._macro_status_scroll_index % len(scroll)
            double = scroll + scroll
            display = double[start : start + width]
            app._macro_status_scroll_index += 1
        try:
            app.machine_state.set(display)
            try:
                app._ensure_state_label_width(display)
            except Exception as exc:
                _log_suppressed("Failed ensuring machine-state label width during macro-status scroll", exc)
        except Exception as exc:
            _log_suppressed("Failed updating machine-state text during macro-status scroll", exc)
        app._macro_status_after_id = app.after(
            MACRO_STATUS_SCROLL_INTERVAL_MS,
            self._update_macro_status_display,
        )

    def _stop_macro_status(self):
        app = cast(Any, self)
        if not getattr(app, "_macro_status_active", False):
            self._clear_manual_control_transient_states()
            return
        app._macro_status_active = False
        after_id = getattr(app, "_macro_status_after_id", None)
        if after_id:
            try:
                app.after_cancel(after_id)
            except Exception as exc:
                _log_suppressed("Failed canceling macro-status scroll timer", exc)
        app._macro_status_after_id = None
        cached_state = str(getattr(app, "_machine_state_text", "") or "")
        stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
        stream_busy = stream_state in {"running", "paused"} or bool(
            getattr(app, "_stream_done_pending_idle", False)
        )
        if (not stream_busy) and cached_state.strip().lower().startswith("run"):
            self._clear_manual_control_transient_states()
            return
        app.machine_state.set(app._machine_state_text)
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception as exc:
            _log_suppressed("Failed restoring machine-state label width after macro-status stop", exc)
        self._clear_manual_control_transient_states()
        app._update_state_highlight(app._machine_state_text)

    def _stop_workflow_status(self):
        self._stop_macro_status()

    def _on_resume_button_visibility_change(self):
        on_resume_button_visibility_change(self)

    def _on_recover_button_visibility_change(self):
        on_recover_button_visibility_change(self)

    def _update_resume_button_visibility(self):
        update_resume_button_visibility(self)

    def _update_recover_button_visibility(self):
        update_recover_button_visibility(self)

    def _set_job_button_mode(self, mode: str):
        update_job_button_mode(self, mode)

    def _refresh_toolbar_action_focus(self):
        refresh_toolbar_action_focus(self)

