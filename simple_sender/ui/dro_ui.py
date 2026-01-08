from tkinter import ttk

from simple_sender.ui.widgets import set_kb_id


def dro_value_row(app, parent, axis, var):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    ttk.Label(
        row,
        textvariable=var,
        width=7,
        font=app.dro_value_font,
    ).grid(row=0, column=1, sticky="w")
    # Keep a hidden button area so the MPos rows mirror the WPos layout.
    btn = ttk.Button(
        row,
        text="",
        style=app.HIDDEN_MPOS_BUTTON_STYLE,
        state="disabled",
        width=9,
        takefocus=False,
    )
    btn.grid(row=0, column=2, sticky="w")


def dro_row(app, parent, axis, var, zero_cmd):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=f"{axis}:", width=3).grid(row=0, column=0, sticky="w")
    ttk.Label(
        row,
        textvariable=var,
        width=8,
        font=app.dro_value_font,
    ).grid(row=0, column=1, sticky="w")
    btn = ttk.Button(row, text=f"Zero {axis}", command=zero_cmd)
    btn.grid(row=0, column=2, sticky="w")
    set_kb_id(btn, f"zero_{axis.lower()}")
    return btn
