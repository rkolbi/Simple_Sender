import threading
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import Any, Callable

from simple_sender.utils.constants import BAUD_DEFAULT


def refresh_ports(app, auto_connect: bool = False):
    ports = app.grbl.list_ports()
    app.port_combo["values"] = ports
    if ports and app.current_port.get() not in ports:
        app.current_port.set(ports[0])
    if not ports:
        app.current_port.set("")
    if auto_connect and (not app.connected):
        last = (app.settings.get("last_port") or "").strip()
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
            app.grbl.connect(port, BAUD_DEFAULT)
        except Exception as exc:
            if show_error:
                try:
                    app.after(0, lambda: messagebox.showerror("Connect failed", str(exc)))
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
    path = filedialog.askopenfilename(
        title="Open G-code",
        initialdir=app.settings.get("last_gcode_dir", ""),
        filetypes=[("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")],
    )
    if not path:
        return
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
