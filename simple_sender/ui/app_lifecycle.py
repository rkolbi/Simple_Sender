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
import os
import threading
import traceback
from tkinter import messagebox

from simple_sender.ui.dialogs.error_dialogs_ui import close_grbl_code_popup

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def log_exception(
    app,
    context: str,
    exc: BaseException,
    *,
    show_dialog: bool = False,
    dialog_title: str = "Error",
    traceback_text: str | None = None,
):
    tb = traceback_text or format_exception(exc)
    header = f"[error] {context}: {exc}"
    if threading.current_thread() is threading.main_thread():
        try:
            app.streaming_controller.handle_log(header)
            for ln in tb.splitlines():
                app.streaming_controller.handle_log(ln)
        except Exception as exc:
            _log_suppressed("Failed writing exception details to streaming controller log", exc)
    else:
        try:
            app.ui_q.put(("log", header))
            for ln in tb.splitlines():
                app.ui_q.put(("log", ln))
        except Exception as exc:
            _log_suppressed("Failed queueing exception details to UI log queue", exc)
    if show_dialog:
        if app._should_show_error_dialog():
            app._post_ui_thread(messagebox.showerror, dialog_title, tb)


def _report_shutdown_failure(app, context: str, exc: BaseException) -> None:
    try:
        app._log_exception(context, exc)
    except Exception as log_exc:
        _log_suppressed(f"{context} (failed to call app._log_exception)", log_exc)
        _log_suppressed(context, exc)


def tk_report_callback_exception(app, exc, val, tb):
    try:
        text = "".join(traceback.format_exception(exc, val, tb))
    except Exception:
        text = f"{val}"
    log_exception(
        app,
        "Unhandled UI exception",
        val or RuntimeError("Unknown UI exception"),
        show_dialog=True,
        dialog_title="Application error",
        traceback_text=text,
    )


def on_close(app):
    save_context = "Failed saving settings during shutdown"
    while True:
        try:
            app._save_settings()
            break
        except Exception as exc:
            _report_shutdown_failure(app, save_context, exc)
            try:
                choice = messagebox.askyesnocancel(
                    "Settings Save Failed",
                    (
                        "Settings could not be saved before closing.\n\n"
                        f"{exc}\n\n"
                        "Yes: Exit without saving\n"
                        "No: Retry save\n"
                        "Cancel: Keep the application open"
                    ),
                )
            except Exception as prompt_exc:
                _log_suppressed("Failed showing shutdown settings-save warning dialog", prompt_exc)
                choice = True
            if choice is True:
                break
            if choice is None:
                return
    app._closing = True
    for event_name in ("_connection_state_event", "_status_update_event", "_modal_update_event"):
        evt = getattr(app, event_name, None)
        try:
            if evt is not None and hasattr(evt, "set"):
                evt.set()
        except Exception as exc:
            _log_suppressed(f"Failed signaling {event_name} during shutdown", exc)
    try:
        close_grbl_code_popup(app)
    except Exception as exc:
        _log_suppressed("Failed closing GRBL code popup during shutdown", exc)
    accessory_router = getattr(app, "accessory_router", None)
    if accessory_router is not None:
        try:
            if hasattr(app, "_stop_job_accessories"):
                app._stop_job_accessories("app_exit")
        except Exception as exc:
            _log_suppressed("Failed issuing Kasa OFF command during app close", exc)
        try:
            wait_for_idle = getattr(accessory_router, "wait_for_idle", None)
            if callable(wait_for_idle):
                wait_for_idle(timeout=1.0)
        except Exception as exc:
            _log_suppressed("Failed waiting for Kasa worker drain during app close", exc)
        try:
            accessory_router.shutdown(timeout=1.0)
        except Exception as exc:
            _log_suppressed("Failed shutting down accessory router during app close", exc)
    try:
        disconnect_fn = getattr(app.grbl, "disconnect")
        try:
            disconnect_fn(requested_by="shutdown", reason="Application close")
        except TypeError:
            disconnect_fn()
    except Exception as exc:
        _report_shutdown_failure(app, "Failed disconnecting GRBL during shutdown", exc)
    source = getattr(app, "_gcode_source", None)
    if source is not None:
        cleanup_path = getattr(source, "_cleanup_path", None)
        try:
            source.close()
        except Exception as exc:
            _log_suppressed("Failed closing streaming G-code source during shutdown", exc)
        if cleanup_path:
            try:
                os.remove(cleanup_path)
            except OSError as exc:
                _log_suppressed("Failed deleting temporary G-code cleanup file during shutdown", exc)
        app._gcode_source = None
    try:
        app._stop_joystick_hold()
    except Exception as exc:
        _log_suppressed("Failed stopping joystick hold during shutdown", exc)
    try:
        app._stop_joystick_polling()
    except Exception as exc:
        _log_suppressed("Failed stopping joystick polling during shutdown", exc)
    py = app._get_pygame_module()
    if py is not None:
        try:
            py.quit()
        except Exception as exc:
            _log_suppressed("Failed quitting pygame during shutdown", exc)
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        try:
            perf_monitor.emit_exit_report()
        except Exception as exc:
            _log_suppressed("Failed emitting performance report during shutdown", exc)
    try:
        app.destroy()
    except Exception as exc:
        _log_suppressed("Failed destroying application root during shutdown", exc)
