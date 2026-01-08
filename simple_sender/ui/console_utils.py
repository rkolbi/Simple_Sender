import logging
from tkinter import filedialog, messagebox

logger = logging.getLogger(__name__)

def setup_console_tags(app):
    text_fg = "#111111"
    try:
        app.console.tag_configure("console_tx", background="#e5efff", foreground=text_fg)       # light blue
        app.console.tag_configure("console_ok", background="#e6f7ed", foreground=text_fg)       # light green
        app.console.tag_configure("console_status", background="#fff4d8", foreground=text_fg)   # light orange
        app.console.tag_configure("console_error", background="#ffe5e5", foreground=text_fg)    # light red
        app.console.tag_configure("console_alarm", background="#ffd8d8", foreground=text_fg)    # light red/darker
    except Exception as exc:
        logger.exception("Failed to configure console tags: %s", exc)

def send_console(app):
    s = app.cmd_entry.get().strip()
    if not s:
        return
    app._send_manual(s, "console")
    app.cmd_entry.delete(0, "end")

def clear_console_log(app):
    if not messagebox.askyesno("Clear console", "Clear the console log?"):
        return
    app.streaming_controller.clear_console()

def save_console_log(app):
    path = filedialog.asksaveasfilename(
        title="Save console log",
        defaultextension=".txt",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    if not path:
        return
    # Save from stored console lines (position reports are excluded)
    data_lines = [
        text
        for text, tag in app.streaming_controller.get_console_lines()
        if app.streaming_controller.matches_filter((text, tag), for_save=True)
        and (not app.streaming_controller.is_position_line(text))
    ]
    data = "\n".join(data_lines)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    except Exception as e:
        messagebox.showerror("Save failed", str(e))
