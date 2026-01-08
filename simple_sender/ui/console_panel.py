import tkinter as tk
from tkinter import ttk

from simple_sender.ui.widgets import apply_tooltip, attach_log_gcode, set_kb_id


def build_console_tab(app, notebook: ttk.Notebook) -> ttk.Frame:
    ctab = ttk.Frame(notebook, padding=6)
    notebook.add(ctab, text="Console")

    app.console = tk.Text(ctab, wrap="word", height=12, state="disabled", font=app.console_font)
    csb = ttk.Scrollbar(ctab, orient="vertical", command=app.console.yview)
    app.console.configure(yscrollcommand=csb.set)
    app.console.grid(row=0, column=0, sticky="nsew")
    csb.grid(row=0, column=1, sticky="ns")
    app._setup_console_tags()
    ctab.grid_rowconfigure(0, weight=1)
    ctab.grid_columnconfigure(0, weight=1)

    entry_row = ttk.Frame(ctab)
    entry_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    entry_row.grid_columnconfigure(0, weight=1)

    app.cmd_entry = ttk.Entry(entry_row)
    app.cmd_entry.grid(row=0, column=0, sticky="ew")
    app.btn_send = ttk.Button(entry_row, text="Send", command=app._send_console)
    set_kb_id(app.btn_send, "console_send")
    app.btn_send.grid(row=0, column=1, padx=(8, 0))
    app._manual_controls.extend([app.cmd_entry, app.btn_send])
    apply_tooltip(app.btn_send, "Send the command from the console input.")
    attach_log_gcode(app.btn_send, lambda: app.cmd_entry.get().strip())
    app.btn_console_save = ttk.Button(entry_row, text="Save", command=app._save_console_log)
    set_kb_id(app.btn_console_save, "console_save")
    app.btn_console_save.grid(row=0, column=2, padx=(8, 0))
    apply_tooltip(app.btn_console_save, "Save the console log to a text file.")
    app.btn_console_clear = ttk.Button(entry_row, text="Clear", command=app._clear_console_log)
    set_kb_id(app.btn_console_clear, "console_clear")
    app.btn_console_clear.grid(row=0, column=3, padx=(8, 0))
    apply_tooltip(app.btn_console_clear, "Clear the console log.")
    app.console_filter_sep = ttk.Separator(entry_row, orient="vertical")
    app.console_filter_sep.grid(row=0, column=4, sticky="ns", padx=(8, 6))
    app.btn_console_all = ttk.Button(
        entry_row,
        text="ALL",
        command=lambda: app.streaming_controller.set_console_filter(None),
    )
    set_kb_id(app.btn_console_all, "console_filter_all")
    app.btn_console_all.grid(row=0, column=5, padx=(0, 0))
    apply_tooltip(app.btn_console_all, "Show all console log entries.")
    app.btn_console_errors = ttk.Button(
        entry_row,
        text="ERRORS",
        command=lambda: app.streaming_controller.set_console_filter("errors"),
    )
    set_kb_id(app.btn_console_errors, "console_filter_errors")
    app.btn_console_errors.grid(row=0, column=6, padx=(1, 0))
    apply_tooltip(app.btn_console_errors, "Show only error entries in the console log.")
    app.btn_console_alarms = ttk.Button(
        entry_row,
        text="ALARMS",
        command=lambda: app.streaming_controller.set_console_filter("alarms"),
    )
    set_kb_id(app.btn_console_alarms, "console_filter_alarms")
    app.btn_console_alarms.grid(row=0, column=7, padx=(1, 0))
    apply_tooltip(app.btn_console_alarms, "Show only alarm entries in the console log.")
    app.btn_console_pos = ttk.Button(
        entry_row,
        text="Pos/Status: On" if bool(app.console_positions_enabled.get()) else "Pos/Status: Off",
        command=app._toggle_console_pos_status,
    )
    set_kb_id(app.btn_console_pos, "console_pos_toggle")
    app.btn_console_pos.grid(row=0, column=8, padx=(10, 0))
    apply_tooltip(
        app.btn_console_pos,
        "Show/hide position and status reports in the console (not saved to log).",
    )

    app.cmd_entry.bind("<Return>", lambda e: app._send_console())
    app.streaming_controller.attach_widgets(
        console=app.console,
        gview=app.gview,
        progress_pct=app.progress_pct,
        buffer_fill=app.buffer_fill,
        buffer_fill_pct=app.buffer_fill_pct,
        throughput_var=app.throughput_var,
    )

    return ctab
