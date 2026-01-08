import time
from tkinter import messagebox


def request_settings_dump(app):
    if not app.grbl.is_connected():
        messagebox.showwarning("Not connected", "Connect to GRBL first.")
        return
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before requesting settings.")
        return
    if not app._grbl_ready:
        app._pending_settings_refresh = True
        app.status.config(text="Waiting for Grbl startup...")
        return
    if app._alarm_locked:
        messagebox.showwarning("Alarm", "Clear alarm before requesting settings.")
        return
    app.streaming_controller.log(
        f"[{time.strftime('%H:%M:%S')}] Settings refresh requested ($$)."
    )
    app.settings_controller.start_capture("Requesting $$...")
    app._send_manual("$$", "settings")
