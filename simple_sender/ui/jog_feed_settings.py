import tkinter as tk


def validate_jog_feed_var(app, var: tk.DoubleVar, fallback_default: float):
    try:
        val = float(var.get())
    except Exception:
        val = None
    if val is None or val <= 0:
        try:
            fallback = float(fallback_default)
        except Exception:
            fallback = fallback_default
        var.set(fallback)
        return
    var.set(val)


def on_jog_feed_change_xy(app, _event=None):
    validate_jog_feed_var(app, app.jog_feed_xy, app.settings.get("jog_feed_xy", 4000.0))


def on_jog_feed_change_z(app, _event=None):
    validate_jog_feed_var(app, app.jog_feed_z, app.settings.get("jog_feed_z", 500.0))
