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

import os
import threading
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import Any, Callable

from simple_sender.utils.constants import BAUD_DEFAULT


def ensure_serial_available(app, serial_available: bool, serial_error: str | None = None) -> bool:
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
    except Exception:
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
        except Exception:
            return ""
    try:
        return path if os.path.isdir(path) else ""
    except Exception:
        return ""


def refresh_ports(app, auto_connect: bool = False):
    ports = app.grbl.list_ports()
    last = ""
    try:
        last = getattr(app, "_auto_reconnect_last_port", "") or ""
    except Exception:
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
            try:
                app.toggle_connect()
            except Exception:
                pass


def toggle_connect(app):
    if not app._ensure_serial_available():
        return
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before disconnecting.")
        return
    if app.connected:
        app._user_disconnect = True
        app._start_disconnect_worker()
        return
    app._user_disconnect = False
    port = app.current_port.get().strip()
    if not port:
        messagebox.showwarning("No port", "No serial port selected.")
        return
    app._start_connect_worker(port)


def start_connect_worker(
    app,
    port: str,
    *,
    show_error: bool = True,
    on_failure: Callable[[Exception], Any] | None = None,
):
    if app._connecting:
        return

    def worker():
        try:
            try:
                baud = int(app.settings.get("baud_rate", BAUD_DEFAULT))
            except Exception:
                baud = BAUD_DEFAULT
            app.grbl.connect(port, baud)
        except Exception as exc:
            if show_error:
                try:
                    app.after(0, lambda exc=exc: messagebox.showerror("Connect failed", str(exc)))
                except Exception:
                    pass
            callback = on_failure
            if callback is not None:
                try:
                    app.after(0, lambda exc=exc: callback(exc))
                except Exception:
                    pass
        finally:
            app._connecting = False

    app._connecting = True
    app._connect_thread = threading.Thread(target=worker, daemon=True)
    app._connect_thread.start()


def start_disconnect_worker(app):
    if app._disconnecting:
        return

    def worker():
        try:
            app.grbl.disconnect()
        except Exception as exc:
            app.ui_q.put(("log", f"[disconnect] {exc}"))
        finally:
            app._disconnecting = False

    app._disconnecting = True
    app._disconnect_thread = threading.Thread(target=worker, daemon=True)
    app._disconnect_thread.start()


def open_gcode(app):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
        return
    initial_dir = _safe_initial_dir(app.settings.get("last_gcode_dir", ""))
    path = filedialog.askopenfilename(
        title="Open G-code",
        initialdir=initial_dir,
        filetypes=[("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")],
    )
    if not path:
        return
    try:
        if getattr(app, "notebook", None) is not None and getattr(app, "gcode_tab", None) is not None:
            app.notebook.select(app.gcode_tab)
    except Exception:
        pass
    app._load_gcode_from_path(path)


def run_job(app):
    if not app._require_grbl_connection():
        return
    app.grbl.set_dry_run_sanitize(bool(app.dry_run_sanitize_stream.get()))
    app._reset_gcode_view_for_run()
    app._job_started_at = datetime.now()
    app._job_completion_notified = False
    app.grbl.start_stream()


def pause_job(app):
    if not app._require_grbl_connection():
        return
    app.grbl.pause_stream()


def resume_job(app):
    if not app._require_grbl_connection():
        return
    app.grbl.resume_stream()


def stop_job(app):
    if not app._require_grbl_connection():
        return
    app.grbl.stop_stream()
