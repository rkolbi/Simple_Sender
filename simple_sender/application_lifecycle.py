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
import sys
from typing import Any, cast


def _app_module(instance):
    return sys.modules[instance.__class__.__module__]


class LifecycleMixin:
    def _drain_ui_queue(self):
        _app_module(self).drain_ui_queue(self)

    def _clear_pending_ui_updates(self):
        app = cast(Any, self)
        app.streaming_controller.clear_pending_ui_updates()

    def _handle_evt(self, evt):
        _app_module(self).handle_event(self, evt)

    def _on_close(self):
        _app_module(self).on_close(self)

    def _call_on_ui_thread(self, func, *args, timeout: float | None = 5.0, **kwargs):
        return _app_module(self).call_on_ui_thread(self, func, *args, timeout=timeout, **kwargs)

    def _post_ui_thread(self, func, *args, **kwargs):
        _app_module(self).post_ui_thread(self, func, *args, **kwargs)

    def _log_exception(
        self,
        context: str,
        exc: BaseException,
        *,
        show_dialog: bool = False,
        dialog_title: str = "Error",
        traceback_text: str | None = None,
    ):
        _app_module(self).log_exception(
            self,
            context,
            exc,
            show_dialog=show_dialog,
            dialog_title=dialog_title,
            traceback_text=traceback_text,
        )

    def _tk_report_callback_exception(self, exc, val, tb):
        _app_module(self).tk_report_callback_exception(self, exc, val, tb)

    def _should_show_error_dialog(self) -> bool:
        return bool(_app_module(self).should_show_error_dialog(self))

    def _reset_error_dialog_state(self):
        _app_module(self).reset_error_dialog_state(self)

    def _set_error_dialog_status(self, text: str):
        _app_module(self).set_error_dialog_status(self, text)

    def _load_settings(self) -> dict:
        return cast(dict, _app_module(self).load_settings(self))

    def _save_settings(self):
        _app_module(self).save_settings(self)
