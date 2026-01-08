import tkinter as tk


def set_manual_controls_enabled(app, enabled: bool):
    if getattr(app, "_alarm_locked", False):
        for w in app._manual_controls:
            try:
                if w is getattr(app, "btn_all_stop", None):
                    continue
                if w is getattr(app, "btn_home_mpos", None):
                    w.config(state="normal")
                    continue
                if w is getattr(app, "btn_unlock_mpos", None):
                    w.config(state="normal")
                    continue
                if w is getattr(app, "btn_unlock_top", None):
                    w.config(state="normal")
                    continue
                w.config(state="disabled")
            except tk.TclError:
                pass
        return
    connected = bool(getattr(app, "connected", False))
    state = "normal" if enabled else "disabled"
    for w in app._manual_controls:
        try:
            if not connected:
                if w in app._offline_controls:
                    w.config(state="normal")
                else:
                    w.config(state="disabled")
                continue
            if not enabled and w is getattr(app, "btn_all_stop", None):
                w.config(state="normal")
                continue
            if not enabled and w in app._override_controls:
                w.config(state="normal")
                continue
            w.config(state=state)
        except tk.TclError:
            pass
    if enabled and connected:
        app._set_unit_mode(app.unit_mode.get())
        app._set_step_xy(app.step_xy.get())
        app._set_step_z(app.step_z.get())
