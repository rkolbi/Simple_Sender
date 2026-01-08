import tkinter as tk


def position_all_stop_offset(app, event=None):
    slot = getattr(app, "_all_stop_slot", None)
    btn = getattr(app, "btn_all_stop", None)
    if not slot or not btn:
        return
    if not slot.winfo_ismapped():
        app.after(50, app._position_all_stop_offset)
        return
    offset = getattr(app, "_all_stop_offset_px", None)
    if offset is None:
        try:
            offset = int(app.winfo_fpixels("0.7i"))
        except tk.TclError:
            offset = 96
        app._all_stop_offset_px = offset
    x = slot.winfo_x() - offset
    if x < 0:
        x = 0
    y = slot.winfo_y()
    btn.place(in_=slot.master, x=x, y=y)
    try:
        btn.tk.call("raise", btn._w)
    except tk.TclError:
        pass
