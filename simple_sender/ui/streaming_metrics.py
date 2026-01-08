from datetime import datetime
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

from simple_sender.ui.popup_utils import center_window


def format_throughput(bps: float) -> str:
    if bps <= 0:
        return "TX: 0 B/s"
    if bps < 1024:
        return f"TX: {bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"TX: {bps / 1024.0:.1f} KB/s"
    return f"TX: {bps / (1024.0 * 1024.0):.2f} MB/s"


def maybe_notify_job_completion(app, done: int, total: int) -> None:
    if (
        app._job_started_at is None
        or app._job_completion_notified
        or total <= 0
        or done < total
    ):
        return
    start_time = app._job_started_at
    finish_time = datetime.now()
    elapsed = finish_time - start_time
    elapsed_str = str(elapsed).split(".")[0]
    app._job_completion_notified = True
    app._job_started_at = None
    start_text = start_time.strftime("%Y-%m-%d %H:%M:%S")
    finish_text = finish_time.strftime("%Y-%m-%d %H:%M:%S")
    summary = (
        f"Job completed in {elapsed_str} "
        f"(started {start_text}, finished {finish_text})."
    )
    try:
        app.streaming_controller.handle_log(f"[job] {summary}")
    except Exception:
        pass
    message = (
        "Job completed.\n\n"
        f"Started: {start_text}\n"
        f"Finished: {finish_text}\n"
        f"Elapsed: {elapsed_str}"
    )
    if bool(app.job_completion_popup.get()):
        _show_job_completion_dialog(app, message)
    if bool(app.job_completion_beep.get()):
        try:
            app.bell()
        except Exception:
            pass


def _start_completion_flash(app) -> None:
    if getattr(app, "_completion_flash_active", False):
        return
    app._completion_flash_active = True
    app._completion_flash_on = False

    def tick():
        if not getattr(app, "_completion_flash_active", False):
            return
        app.progress_pct.set(0 if app._completion_flash_on else 100)
        app._completion_flash_on = not app._completion_flash_on
        app._completion_flash_id = app.after(350, tick)

    app._completion_flash_id = app.after(0, tick)


def _stop_completion_flash(app) -> None:
    app._completion_flash_active = False
    flash_id = getattr(app, "_completion_flash_id", None)
    if flash_id is not None:
        try:
            app.after_cancel(flash_id)
        except Exception:
            pass
    app._completion_flash_id = None
    app._completion_flash_on = False
    app.progress_pct.set(0)


def _show_job_completion_dialog(app, message: str) -> None:
    if getattr(app, "_completion_dialog", None) is not None:
        return
    _start_completion_flash(app)
    dialog = tk.Toplevel(app)
    app._completion_dialog = dialog
    dialog.title("Job completed")
    dialog.transient(app)
    dialog.resizable(False, False)
    dialog.configure(padx=24, pady=16)

    base_font = tkfont.nametofont("TkDefaultFont")
    title_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 4,
        weight="bold",
    )
    body_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 2,
    )

    header = ttk.Frame(dialog)
    header.pack(fill="x")
    ttk.Label(header, text="Job completed", font=title_font).pack(side="left")
    btn = ttk.Button(header, text="OK")
    btn.pack(side="right")
    ttk.Label(dialog, text=message, font=body_font, justify="left", wraplength=520).pack(
        anchor="w", pady=(12, 6)
    )

    def close():
        if getattr(app, "_completion_dialog", None) is None:
            return
        app._completion_dialog = None
        _stop_completion_flash(app)
        try:
            dialog.destroy()
        except Exception:
            pass

    btn.configure(command=close)
    dialog.protocol("WM_DELETE_WINDOW", close)
    try:
        dialog.grab_set()
    except Exception:
        pass
    center_window(dialog, app)
