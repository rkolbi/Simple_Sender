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
import threading
import time
import traceback
from tkinter import messagebox

from simple_sender.ui.dialogs.error_dialogs_ui import close_grbl_code_popup
from simple_sender.ui.job_setup_state import invalidate_job_setup_state

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_SHUTDOWN_POLL_INTERVAL_MS = 25
_SHUTDOWN_TIMEOUT_S = 10.0


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


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


def _macro_running(app) -> bool:
    macro_executor = getattr(app, "macro_executor", None)
    if macro_executor is None:
        return False
    try:
        checker = getattr(macro_executor, "is_macro_active", None)
        if callable(checker):
            return bool(checker())
        lock = getattr(macro_executor, "_macro_lock", None)
        if lock is not None and hasattr(lock, "locked"):
            return bool(lock.locked())
        if hasattr(macro_executor, "macro_vars"):
            with macro_executor.macro_vars() as macro_vars:
                return bool(macro_vars.get("running", False))
    except Exception as exc:
        _log_suppressed("Failed checking macro-running state before app lifecycle action", exc)
        return False
    return False


def _lifecycle_risk_reasons(app) -> list[str]:
    reasons: list[str] = []
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    done_pending_idle = bool(getattr(app, "_stream_done_pending_idle", False))
    try:
        grbl = getattr(app, "grbl", None)
        is_streaming = bool(grbl.is_streaming()) if grbl is not None and hasattr(grbl, "is_streaming") else False
    except Exception as exc:
        _log_suppressed("Failed checking GRBL streaming state before app lifecycle action", exc)
        is_streaming = False

    if is_streaming or stream_state == "running":
        reasons.append("A job is currently running.")
    elif stream_state == "paused":
        reasons.append("A job is currently paused.")

    if done_pending_idle:
        reasons.append("Job completion is still settling.")
    if bool(getattr(app, "_resume_after_disconnect", False)) or getattr(app, "_resume_from_index", None) is not None:
        reasons.append("Reconnect recovery is pending.")
    if bool(getattr(app, "_gcode_restore_failed", False)):
        reasons.append("A reconnect recovery failure is awaiting operator attention.")
    if bool(getattr(app, "_auto_reconnect_pending", False)):
        reasons.append("Automatic reconnect is pending.")
    if bool(getattr(app, "_connecting", False)):
        reasons.append("A controller connection attempt is in progress.")
    if bool(getattr(app, "_disconnecting", False)):
        reasons.append("A controller disconnect is in progress.")
    if bool(getattr(app, "_homing_in_progress", False)):
        reasons.append("Homing is currently active.")
    if _macro_running(app):
        reasons.append("A macro or workflow is currently active.")

    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return deduped


def _confirm_application_close(app) -> bool:
    reasons = _lifecycle_risk_reasons(app)
    if reasons:
        title = "Close Application"
        consequence = (
            "Closing now will first request Stop Job for any active or paused job, "
            "then disconnect after that stop request is accepted."
        )
        prompt = "Close Simple Sender anyway?"
        message = "\n".join(
            ["Simple Sender is currently busy:"]
            + [f"- {reason}" for reason in reasons]
            + ["", consequence, "", prompt]
        )
    else:
        title = "Close Application"
        message = "Close Simple Sender now?"
    try:
        return bool(messagebox.askyesno(title, message))
    except Exception as exc:
        _log_suppressed("Failed showing application lifecycle confirmation dialog", exc)
        return False


def _shutdown_needs_job_stop(app) -> bool:
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state in {"running", "paused"}:
        return True
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    grbl = getattr(app, "grbl", None)
    checker = getattr(grbl, "is_streaming", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception as exc:
            _log_suppressed("Failed checking GRBL streaming state before shutdown stop", exc)
    return False


def _request_job_stop_before_shutdown(app) -> bool:
    if not _shutdown_needs_job_stop(app):
        return True
    grbl = getattr(app, "grbl", None)
    stop_stream = getattr(grbl, "stop_stream", None)
    if not callable(stop_stream):
        _report_shutdown_stop_failure(app, "Stop Job is not available; shutdown canceled.")
        return False
    try:
        stop_result = stop_stream()
    except Exception as exc:
        _report_shutdown_failure(app, "Failed requesting Stop Job before shutdown", exc)
        _report_shutdown_stop_failure(app, "Stop Job failed; shutdown canceled.")
        return False
    if stop_result is not True:
        _report_shutdown_stop_failure(app, "Stop Job was not accepted; shutdown canceled.")
        return False
    try:
        if hasattr(app, "_stop_job_accessories"):
            app._stop_job_accessories("job_stop")
    except Exception as exc:
        _log_suppressed("Failed stopping Kasa job accessories after shutdown Stop Job", exc)
    try:
        invalidate_job_setup_state(app)
    except Exception as exc:
        _log_suppressed("Failed invalidating Job Setup after shutdown Stop Job", exc)
    return True


def _report_shutdown_stop_failure(app, message: str) -> None:
    try:
        status = getattr(app, "status", None)
        if status is not None and hasattr(status, "config"):
            status.config(text=message)
    except Exception as exc:
        _log_suppressed("Failed updating shutdown stop-failure status", exc)
    try:
        app.streaming_controller.handle_log(f"[shutdown] {message}")
    except Exception as exc:
        _log_suppressed("Failed logging shutdown stop-failure message", exc)
    try:
        messagebox.showerror("Close canceled", message)
    except Exception as exc:
        _log_suppressed("Failed showing shutdown stop-failure dialog", exc)


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
    if bool(getattr(app, "_shutdown_in_progress", False)):
        return True

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
                return False
    if not _request_job_stop_before_shutdown(app):
        return False

    def _set_shutdown_status(text: str) -> None:
        try:
            status = getattr(app, "status", None)
            if status is not None and hasattr(status, "config"):
                status.config(text=text)
        except Exception as exc:
            _log_suppressed("Failed updating shutdown status text", exc)

    def _log_shutdown(message: str, *, context: str) -> None:
        try:
            app.streaming_controller.handle_log(message)
        except Exception as exc:
            _log_suppressed(context, exc)

    app._shutdown_in_progress = True
    app._closing = True
    app._shutdown_timed_out = False
    _set_shutdown_status("Shutting down...")
    _log_shutdown("[shutdown] Shutting down...", context="Failed logging shutdown start message")
    try:
        from simple_sender.ui.events.status import (
            _clear_coalesced_status_positions_state,
        )

        _clear_coalesced_status_positions_state(app)
    except Exception as exc:
        _log_suppressed("Failed clearing coalesced status coordinates during shutdown", exc)
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
    try:
        app._stop_joystick_hold()
    except Exception as exc:
        _log_suppressed("Failed stopping joystick hold during shutdown", exc)
    try:
        app._stop_joystick_polling()
    except Exception as exc:
        _log_suppressed("Failed stopping joystick polling during shutdown", exc)
    shutdown_complete = getattr(app, "_shutdown_complete_event", None)
    if shutdown_complete is None or not hasattr(shutdown_complete, "is_set"):
        shutdown_complete = threading.Event()
        app._shutdown_complete_event = shutdown_complete
    else:
        try:
            shutdown_complete.clear()
        except Exception:
            shutdown_complete = threading.Event()
            app._shutdown_complete_event = shutdown_complete
    try:
        shutdown_timeout_s = float(
            getattr(app, "_shutdown_timeout_s", _SHUTDOWN_TIMEOUT_S)
        )
    except (TypeError, ValueError):
        shutdown_timeout_s = _SHUTDOWN_TIMEOUT_S
    if shutdown_timeout_s <= 0:
        shutdown_timeout_s = _SHUTDOWN_TIMEOUT_S
    shutdown_started_at = time.monotonic()

    def _set_shutdown_cleanup_step(step: str) -> None:
        try:
            app._shutdown_cleanup_step = str(step or "").strip()
        except Exception as exc:
            _log_suppressed("Failed updating shutdown cleanup step", exc)

    def _shutdown_cleanup_step_text() -> str:
        try:
            step = str(getattr(app, "_shutdown_cleanup_step", "") or "").strip()
        except Exception:
            step = ""
        return step or "not reported"

    def _shutdown_worker() -> None:
        try:
            accessory_router = getattr(app, "accessory_router", None)
            if accessory_router is not None:
                try:
                    if hasattr(app, "_stop_job_accessories"):
                        _set_shutdown_cleanup_step("requesting accessory cleanup")
                        app._stop_job_accessories("app_exit")
                except Exception as exc:
                    _log_suppressed("Failed issuing Kasa OFF command during app close", exc)
                try:
                    wait_for_idle = getattr(accessory_router, "wait_for_idle", None)
                    if callable(wait_for_idle):
                        _set_shutdown_cleanup_step("waiting for accessory idle")
                        wait_for_idle(timeout=1.0)
                except Exception as exc:
                    _log_suppressed("Failed waiting for Kasa worker drain during app close", exc)
                try:
                    _set_shutdown_cleanup_step("shutting down accessory router")
                    accessory_router.shutdown(timeout=1.0)
                except Exception as exc:
                    _log_suppressed("Failed shutting down accessory router during app close", exc)
            try:
                _set_shutdown_cleanup_step("disconnecting GRBL")
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
                    _set_shutdown_cleanup_step("closing G-code source")
                    source.close()
                except Exception as exc:
                    _log_suppressed("Failed closing streaming G-code source during shutdown", exc)
                if cleanup_path:
                    try:
                        _set_shutdown_cleanup_step("removing temporary cleanup file")
                        os.remove(cleanup_path)
                    except OSError as exc:
                        _log_suppressed("Failed deleting temporary G-code cleanup file during shutdown", exc)
                app._gcode_source = None
            py = app._get_pygame_module()
            if py is not None:
                try:
                    _set_shutdown_cleanup_step("shutting down pygame")
                    py.quit()
                except Exception as exc:
                    _log_suppressed("Failed quitting pygame during shutdown", exc)
            perf_monitor = getattr(app, "_perf_monitor", None)
            if perf_monitor is not None:
                try:
                    _set_shutdown_cleanup_step("writing performance exit report")
                    perf_monitor.emit_exit_report()
                except Exception as exc:
                    _log_suppressed("Failed emitting performance report during shutdown", exc)
        except Exception as exc:
            try:
                _report_shutdown_failure(app, "Unexpected shutdown worker failure", exc)
            except Exception as log_exc:
                _log_suppressed("Failed logging unexpected shutdown worker failure", log_exc)
        finally:
            _set_shutdown_cleanup_step("completed")
            shutdown_complete.set()

    def _finalize_shutdown() -> bool:
        try:
            app.destroy()
        except Exception as exc:
            _report_shutdown_failure(app, "Failed destroying application root during shutdown", exc)
            try:
                messagebox.showerror(
                    "Close failed",
                    (
                        "Simple Sender could not close cleanly.\n\n"
                        f"{exc}\n\n"
                        "The application is still running."
                    ),
                )
            except Exception as dialog_exc:
                _log_suppressed("Failed showing close-failed dialog", dialog_exc)
            app._closing = False
            app._shutdown_in_progress = False
            return False
        clear_runtime_marker = getattr(app, "_clear_runtime_marker", None)
        if callable(clear_runtime_marker):
            try:
                clear_runtime_marker()
            except Exception as exc:
                _log_suppressed("Failed clearing runtime integrity marker during shutdown", exc)
        return True

    def _escalate_shutdown(*, status_text: str, log_message: str, context: str) -> None:
        app._shutdown_in_progress = False
        _set_shutdown_status(status_text)
        _log_shutdown(log_message, context=context)
        _finalize_shutdown()

    def _poll_shutdown_completion() -> None:
        if shutdown_complete.is_set():
            app._shutdown_in_progress = False
            _finalize_shutdown()
            return
        elapsed_s = max(0.0, time.monotonic() - shutdown_started_at)
        if elapsed_s >= shutdown_timeout_s:
            app._shutdown_timed_out = True
            cleanup_step = _shutdown_cleanup_step_text()
            _escalate_shutdown(
                status_text=(
                    "Shutdown timed out; last reported cleanup step: "
                    f"{cleanup_step}; forcing close..."
                ),
                log_message=(
                    f"[shutdown] Shutdown timed out after {shutdown_timeout_s:.1f}s; "
                    f"last reported cleanup step: {cleanup_step}; forcing close."
                ),
                context="Failed logging shutdown-timeout message",
            )
            return
        if not shutdown_complete.is_set():
            try:
                app.after(_SHUTDOWN_POLL_INTERVAL_MS, _poll_shutdown_completion)
            except Exception as exc:
                _log_suppressed("Failed scheduling shutdown completion poll", exc)
                _escalate_shutdown(
                    status_text="Shutdown polling failed; forcing close...",
                    log_message="[shutdown] Shutdown polling failed; forcing close.",
                    context="Failed logging shutdown-poll failure message",
                )
            return

    shutdown_thread = threading.Thread(
        target=_shutdown_worker,
        name="AppShutdown",
        daemon=True,
    )
    app._shutdown_thread = shutdown_thread
    shutdown_thread.start()
    try:
        app.after(_SHUTDOWN_POLL_INTERVAL_MS, _poll_shutdown_completion)
    except Exception as exc:
        _log_suppressed("Failed scheduling shutdown completion poll", exc)
        _escalate_shutdown(
            status_text="Shutdown polling failed; forcing close...",
            log_message="[shutdown] Shutdown polling failed; forcing close.",
            context="Failed logging shutdown-poll failure message",
        )
    return True


def close_application(app) -> bool:
    if not _confirm_application_close(app):
        return False
    return bool(on_close(app))
