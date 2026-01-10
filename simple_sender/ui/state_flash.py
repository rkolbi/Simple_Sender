import tkinter as tk


def apply_state_fg(app, color: str | None, fg: str | None = None):
    target = color if color else (app._state_default_bg or "#f0f0f0")
    text_color = fg or "#000000"
    lbl = getattr(app, "machine_state_label", None)
    if not lbl:
        return
    try:
        lbl.config(background=target, foreground=text_color)
    except tk.TclError:
        pass


def cancel_state_flash(app):
    if app._state_flash_after_id:
        try:
            app.after_cancel(app._state_flash_after_id)
        except Exception:
            pass
    app._state_flash_after_id = None
    app._state_flash_color = None
    app._state_flash_on = False


def toggle_state_flash(app):
    if not app._state_flash_color:
        return
    app._state_flash_on = not app._state_flash_on
    color = app._state_flash_color if app._state_flash_on else (app._state_default_bg or "#f0f0f0")
    apply_state_fg(app, color)
    app._state_flash_after_id = app.after(500, lambda: toggle_state_flash(app))


def start_state_flash(app, color: str):
    cancel_state_flash(app)
    app._state_flash_color = color
    toggle_state_flash(app)


def update_state_highlight(app, state: str | None):
    text = str(state or "").lower()
    if not text:
        cancel_state_flash(app)
        apply_state_fg(app, None)
        return
    if text.startswith("run"):
        cancel_state_flash(app)
        apply_state_fg(app, "#00c853")
    elif text.startswith("idle"):
        cancel_state_flash(app)
        apply_state_fg(app, "#2196f3")
    elif text.startswith("disconnected"):
        cancel_state_flash(app)
        apply_state_fg(app, "#2b2b2b", fg="#ffffff")
    elif text.startswith(("home", "homing")):
        cancel_state_flash(app)
        apply_state_fg(app, "#7e57c2")
    elif text.startswith("hold"):
        cancel_state_flash(app)
        apply_state_fg(app, "#ffc107")
    elif text.startswith("jog"):
        cancel_state_flash(app)
        apply_state_fg(app, "#4fc3f7")
    elif text.startswith("check"):
        cancel_state_flash(app)
        apply_state_fg(app, "#ffb74d")
    elif text.startswith("door"):
        start_state_flash(app, "#ff8a65")
    elif text.startswith("alarm"):
        start_state_flash(app, "#ff5252")
    elif text.startswith("sleep"):
        cancel_state_flash(app)
        apply_state_fg(app, "#b0bec5")
    else:
        cancel_state_flash(app)
        apply_state_fg(app, None)
