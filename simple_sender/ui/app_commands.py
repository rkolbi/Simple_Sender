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
import time
from collections import deque
from tkinter import filedialog, messagebox
from typing import Any, Callable

from simple_sender.constants.messages import BusyMessages, DialogTitles
from simple_sender.services.job_service import (
    DryRunStartDecision,
    JobService,
    JobStartOutcome,
    JobStopOutcome,
)
from simple_sender.ui.dry_run_start_prompt import confirm_dry_run_start_mode
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.icons import ICON_CONNECT, icon_label
from simple_sender.ui.job_setup_state import (
    confirm_job_start_without_setup,
    has_valid_job_setup_state,
    invalidate_job_setup_state,
)
from simple_sender.ui.preflight_gate import run_preflight_gate
from simple_sender.utils.constants import BAUD_DEFAULT

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_CONNECTION_TIMELINE_LIMIT = 200


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _append_connection_timeline_event(app, event: str, details: str = "") -> None:
    history = getattr(app, "_connection_timeline", None)
    if history is None:
        history = deque(maxlen=_CONNECTION_TIMELINE_LIMIT)
        setattr(app, "_connection_timeline", history)
    payload = {
        "ts": float(time.time()),
        "event": str(event or "").strip() or "unknown",
        "details": str(details or "").strip(),
    }
    try:
        history.append(payload)
    except Exception as exc:
        _log_suppressed("Failed appending connection timeline event", exc)


def _post_ui(app, func, *args, on_drop: Callable[[], None] | None = None, **kwargs) -> bool:
    if threading.current_thread() is threading.main_thread():
        try:
            func(*args, **kwargs)
            return True
        except Exception as exc:
            _log_suppressed("Failed running UI callback on main thread", exc)
            return False
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(func, *args, **kwargs)
            return True
        except Exception as exc:
            _log_suppressed("Failed posting UI callback via _post_ui_thread", exc)
    after = getattr(app, "after", None)
    if callable(after):
        try:
            after(0, lambda: func(*args, **kwargs))
            return True
        except Exception as exc:
            _log_suppressed("Failed posting UI callback via after", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", func, args, kwargs))
            return True
        except Exception as exc:
            _log_suppressed("Failed posting UI callback via ui_q", exc)
    _log_suppressed("Dropped UI callback: no safe UI-post path", RuntimeError("no ui_post path"))
    if callable(on_drop):
        try:
            on_drop()
        except Exception as exc:
            _log_suppressed("Failed running dropped UI callback cleanup", exc)
    return False


def _job_service() -> JobService:
    return JobService(
        has_valid_job_setup_state=has_valid_job_setup_state,
        invalidate_job_setup_state=invalidate_job_setup_state,
        log_suppressed=_log_suppressed,
        confirm_dry_run_start=_confirm_dry_run_start,
        set_dry_run_enabled=_set_dry_run_enabled,
    )


def _confirm_dry_run_start(app: Any) -> DryRunStartDecision:
    if getattr(app, "tk", None) is None:
        return DryRunStartDecision.CONTINUE_DRY_RUN
    decision = confirm_dry_run_start_mode(app, messagebox_module=messagebox)
    if isinstance(decision, DryRunStartDecision):
        return decision
    return DryRunStartDecision.CANCEL


def _set_dry_run_enabled(app: Any, enabled: bool) -> None:
    dry_run_enabled = bool(enabled)
    app.dry_run_sanitize_stream.set(dry_run_enabled)
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        settings["dry_run_sanitize_stream"] = dry_run_enabled


def _report_operator_message(
    app,
    *,
    status_text: str | None = None,
    log_text: str | None = None,
) -> None:
    if status_text:
        try:
            app.status.config(text=status_text)
        except Exception as exc:
            _log_suppressed("Failed updating operator status text", exc)
    if log_text:
        ui_q = getattr(app, "ui_q", None)
        if ui_q is None:
            return
        try:
            ui_q.put(("log", log_text))
        except Exception as exc:
            _log_suppressed("Failed queueing operator message", exc)


def _run_preflight(app) -> bool:
    return bool(run_preflight_gate(app, action_label="Run", messagebox_module=messagebox))


def _report_start_failure(app, detail: str | None) -> None:
    message = detail or "GRBL did not enter streaming state after the Run command."
    _report_operator_message(
        app,
        status_text=f"Run failed: {message}",
        log_text=f"[run] Start failed: {message}",
    )
    messagebox.showwarning("Run failed", message)


def ensure_serial_available(app, serial_available: bool, serial_error: str | None = None) -> bool:
    _ = app
    if serial_available:
        return True
    msg = (
        "pyserial is required to communicate with GRBL. Install pyserial (pip install pyserial) "
        "and restart the application."
    )
    if serial_error:
        msg += f"\n{serial_error}"
    messagebox.showerror("Missing dependency", msg)
    return False


def _safe_initial_dir(path: str) -> str:
    if not path:
        return ""
    try:
        path = os.path.expanduser(str(path))
    except Exception as exc:
        _log_suppressed("Failed expanding initial G-code directory path", exc)
        return ""
    if os.name == "nt":
        if path.startswith("\\\\") or path.startswith("//"):
            return ""
        drive, _ = os.path.splitdrive(path)
        if not drive:
            return ""
        root = drive + "\\"
        try:
            import ctypes
            DRIVE_REMOVABLE = 2
            DRIVE_FIXED = 3
            DRIVE_RAMDISK = 6
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
            if dtype not in (DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_RAMDISK):
                return ""
        except Exception as exc:
            _log_suppressed("Failed validating Windows drive type for initial directory", exc)
            return ""
    try:
        return path if os.path.isdir(path) else ""
    except Exception as exc:
        _log_suppressed("Failed checking initial G-code directory existence", exc)
        return ""


def choose_gcode_path(app, initial_dir: str) -> str:
    return str(
        run_file_dialog(
            app,
            filedialog.askopenfilename,
            title="Open G-code",
            initialdir=initial_dir,
            filetypes=[("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")],
        )
        or ""
    )


def refresh_ports(app, auto_connect: bool = False):
    ports = app.grbl.list_ports()
    try:
        cached_ports = tuple(str(port).strip() for port in ports if str(port).strip())
        setattr(app, "_auto_reconnect_ports_cache", cached_ports)
        setattr(app, "_auto_reconnect_ports_cache_ts", float(time.time()))
    except Exception as exc:
        _log_suppressed("Failed updating auto-reconnect port cache during refresh", exc)
    last = ""
    try:
        last = getattr(app, "_auto_reconnect_last_port", "") or ""
    except Exception as exc:
        _log_suppressed("Failed reading auto-reconnect last port", exc)
        last = ""
    if not last:
        last = (app.settings.get("last_port") or "").strip()
    if os.name == "posix" and last and last in ports:
        ports = [last] + [port for port in ports if port != last]
    app.port_combo["values"] = ports
    if ports and app.current_port.get() not in ports:
        if last and last in ports:
            app.current_port.set(last)
        else:
            app.current_port.set(ports[0])
    if not ports:
        app.current_port.set("")
    if auto_connect and (not app.connected):
        if last and last in ports:
            app.current_port.set(last)
            _append_connection_timeline_event(
                app,
                "auto_connect_refresh_trigger",
                f"port={last}",
            )
            try:
                app.toggle_connect()
            except Exception as exc:
                _log_suppressed("Failed auto-connecting to last known port after refresh", exc)


def _set_connection_controls_pending(app, label: str) -> None:
    try:
        app.btn_conn.config(text=icon_label(ICON_CONNECT, label), state="disabled")
    except Exception as exc:
        _log_suppressed("Failed setting connect button to pending state", exc)
    try:
        app.btn_refresh.config(state="disabled")
    except Exception as exc:
        _log_suppressed("Failed disabling refresh button while connection pending", exc)
    try:
        app.port_combo.config(state="disabled")
    except Exception as exc:
        _log_suppressed("Failed disabling port combobox while connection pending", exc)


def _sync_connection_controls(app) -> None:
    try:
        connected = bool(getattr(app, "connected", False))
    except Exception as exc:
        _log_suppressed("Failed reading connected flag while syncing connection controls", exc)
        connected = False
    try:
        is_streaming = bool(app.grbl.is_streaming())
    except Exception as exc:
        _log_suppressed("Failed reading streaming state while syncing connection controls", exc)
        is_streaming = False
    busy = bool(is_streaming or getattr(app, "_stream_done_pending_idle", False))
    btn_state = "disabled" if busy else "normal"
    label = "Disconnect" if connected else "Connect"
    try:
        app.btn_conn.config(text=icon_label(ICON_CONNECT, label), state=btn_state)
    except Exception as exc:
        _log_suppressed("Failed syncing connect button state", exc)
    try:
        app.btn_refresh.config(state=btn_state)
    except Exception as exc:
        _log_suppressed("Failed syncing refresh button state", exc)
    try:
        app.port_combo.config(state="disabled" if busy else "readonly")
    except Exception as exc:
        _log_suppressed("Failed syncing port combobox state", exc)


def _note_connection_ui_sync_drop(app, phase: str) -> None:
    try:
        setattr(app, "_connection_ui_sync_failed", True)
        setattr(app, "_connection_ui_sync_failure_phase", str(phase or "").strip() or "unknown")
    except Exception:
        pass
    _append_connection_timeline_event(
        app,
        "connection_ui_sync_dropped",
        f"phase={phase}",
    )


def toggle_connect(app):
    if not app._ensure_serial_available():
        return False
    if getattr(app, "_connecting", False):
        _set_connection_controls_pending(app, "Connecting...")
        return False
    if getattr(app, "_disconnecting", False):
        _set_connection_controls_pending(app, "Disconnecting...")
        return False
    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
        messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_DISCONNECTING,
        )
        return False
    is_connected = bool(getattr(app, "connected", False))
    try:
        is_connected = is_connected or bool(app.grbl.is_connected())
    except Exception as exc:
        _log_suppressed("Failed reading GRBL connected state in toggle_connect", exc)
    if is_connected:
        _append_connection_timeline_event(app, "disconnect_requested")
        app._user_disconnect = True
        app._auto_reconnect_pending = False
        app._auto_reconnect_retry = 0
        app._auto_reconnect_next_ts = 0.0
        app._auto_reconnect_blocked = True
        _set_connection_controls_pending(app, "Disconnecting...")
        app._start_disconnect_worker()
        return True
    app._user_disconnect = False
    app._auto_reconnect_blocked = False
    port = app.current_port.get().strip()
    if not port:
        _sync_connection_controls(app)
        messagebox.showwarning("No port", "No serial port selected.")
        return False
    _set_connection_controls_pending(app, "Connecting...")
    _append_connection_timeline_event(app, "connect_requested", f"port={port}")
    app._start_connect_worker(port)
    return True


def start_connect_worker(
    app,
    port: str,
    *,
    show_error: bool = True,
    on_failure: Callable[[Exception], Any] | None = None,
):
    if app._connecting:
        _set_connection_controls_pending(app, "Connecting...")
        return
    try:
        if bool(app.grbl.is_connected()):
            if bool(getattr(app, "connected", False)) and bool(getattr(app, "_grbl_ready", False)):
                _append_connection_timeline_event(
                    app,
                    "connect_worker_skip_already_connected",
                    f"port={port}",
                )
                _sync_connection_controls(app)
                return
            _append_connection_timeline_event(
                app,
                "connect_worker_skip_worker_connected",
                f"port={port}",
            )
            return
    except Exception as exc:
        _log_suppressed("Failed checking worker connection state before connect worker", exc)
    try:
        if bool(getattr(app, "connected", False)) and bool(getattr(app, "_grbl_ready", False)):
            if bool(app.grbl.is_connected()):
                _append_connection_timeline_event(
                    app,
                    "connect_worker_skip_already_connected",
                    f"port={port}",
                )
                _sync_connection_controls(app)
                return
    except Exception as exc:
        _log_suppressed("Failed checking existing connected+ready state before connect worker", exc)

    def worker():
        connected_ok = False
        try:
            try:
                baud = int(app.settings.get("baud_rate", BAUD_DEFAULT))
            except Exception:
                baud = BAUD_DEFAULT
            _append_connection_timeline_event(app, "connect_worker_start", f"port={port} baud={baud}")
            app.grbl.connect(port, baud)
            connected_ok = True
            _append_connection_timeline_event(app, "connect_worker_success", f"port={port}")
        except Exception as exc:
            _append_connection_timeline_event(app, "connect_worker_failed", f"port={port} error={exc}")
            callback = on_failure
            connect_error = exc

            if show_error or callback is not None:
                def _handle_connect_failure() -> None:
                    if show_error:
                        messagebox.showerror("Connect failed", str(connect_error))
                    if callback is not None:
                        callback(connect_error)

                _post_ui(
                    app,
                    _handle_connect_failure,
                    on_drop=lambda: _note_connection_ui_sync_drop(app, "connect_failure"),
                )
        finally:
            app._connecting = False
            if not connected_ok:
                _post_ui(
                    app,
                    _sync_connection_controls,
                    app,
                    on_drop=lambda: _note_connection_ui_sync_drop(app, "connect_failure_sync"),
                )

    app._connecting = True
    _set_connection_controls_pending(app, "Connecting...")
    app._connect_thread = threading.Thread(target=worker, daemon=True)
    app._connect_thread.start()


def start_disconnect_worker(app):
    if app._disconnecting:
        _set_connection_controls_pending(app, "Disconnecting...")
        return

    def worker():
        disconnected_ok = False
        try:
            disconnect_fn = getattr(app.grbl, "disconnect")
            try:
                disconnect_fn(requested_by="ui", reason="Disconnect button")
            except TypeError:
                disconnect_fn()
            disconnected_ok = True
        except Exception as exc:
            try:
                app.ui_q.put(("log", f"[disconnect] {exc}"))
            except Exception as log_exc:
                _log_suppressed("Failed queueing disconnect failure log message", log_exc)
        finally:
            app._disconnecting = False
            if not disconnected_ok:
                _post_ui(
                    app,
                    _sync_connection_controls,
                    app,
                    on_drop=lambda: _note_connection_ui_sync_drop(app, "disconnect_failure_sync"),
                )

    app._disconnecting = True
    _set_connection_controls_pending(app, "Disconnecting...")
    app._disconnect_thread = threading.Thread(target=worker, daemon=True)
    app._disconnect_thread.start()


def open_gcode(app):
    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
        messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_LOADING_NEW_GCODE,
        )
        return
    initial_dir = _safe_initial_dir(app.settings.get("last_gcode_dir", ""))
    path = choose_gcode_path(app, initial_dir)
    if not path:
        return
    app._load_gcode_from_path(path)


def run_job(app):
    if not app._require_grbl_connection():
        return
    if not _run_preflight(app):
        return
    result = _job_service().start_job(app)
    if result.outcome is JobStartOutcome.SETUP_CONFIRMATION_REQUIRED:
        if not confirm_job_start_without_setup(app):
            return
        if not _run_preflight(app):
            return
        result = _job_service().start_job(app, allow_start_without_setup=True)
    if result.outcome is JobStartOutcome.CANCELED:
        message = result.detail or "Run canceled before job start."
        _report_operator_message(
            app,
            status_text=message,
            log_text=f"[run] {message}",
        )
        return
    if result.outcome is JobStartOutcome.START_FAILED:
        _report_start_failure(app, result.detail)
        return


def pause_job(app):
    if not app._require_grbl_connection():
        return
    result = app.grbl.pause_stream()
    if result is True:
        return
    if result is False:
        _report_operator_message(
            app,
            status_text="Pause failed: controller did not accept feed hold",
            log_text="[job] Pause failed: controller did not accept feed hold.",
        )
        return
    _report_operator_message(
        app,
        status_text="Pause ignored: no active job is running",
        log_text="[job] Pause ignored: no active job is running.",
    )


def resume_job(app):
    if not app._require_grbl_connection():
        return
    result = app.grbl.resume_stream()
    if result is True:
        return
    if result is False:
        _report_operator_message(
            app,
            status_text="Resume failed: controller did not accept cycle start",
            log_text="[job] Resume failed: controller did not accept cycle start.",
        )
        return
    _report_operator_message(
        app,
        status_text="Resume ignored: no active job is running",
        log_text="[job] Resume ignored: no active job is running.",
    )


def stop_job(app):
    if not app._require_grbl_connection():
        return
    result = _job_service().stop_job(app)
    if result.outcome is JobStopOutcome.STOPPED:
        _report_operator_message(
            app,
            status_text="Job stopped",
            log_text="[job] Stop Job completed.",
        )
        return
    message = result.detail or "Stop Job did not stop an active job."
    if result.outcome is JobStopOutcome.NOTHING_ACTIVE:
        _report_operator_message(
            app,
            status_text=message,
            log_text=f"[job] Stop Job ignored: {message}",
        )
        return
    _report_operator_message(
        app,
        status_text=f"Stop Job failed: {message}",
        log_text=f"[job] Stop Job failed: {message}",
    )
