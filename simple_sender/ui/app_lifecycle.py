import threading
import traceback
from tkinter import messagebox


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
        except Exception:
            pass
    else:
        try:
            app.ui_q.put(("log", header))
            for ln in tb.splitlines():
                app.ui_q.put(("log", ln))
        except Exception:
            pass
    if show_dialog:
        if app._should_show_error_dialog():
            app._post_ui_thread(messagebox.showerror, dialog_title, tb)


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
    app._closing = True
    try:
        app._save_settings()
        app.grbl.disconnect()
    except Exception as exc:
        app._log_exception("Shutdown failed", exc)
    app._stop_joystick_hold()
    app._stop_joystick_polling()
    py = app._get_pygame_module()
    if py is not None:
        try:
            py.quit()
        except Exception:
            pass
    app.destroy()
