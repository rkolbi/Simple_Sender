def all_stop_action(app):
    try:
        app._stop_joystick_hold()
    except Exception:
        pass
    if not app._require_grbl_connection():
        return
    mode = app.all_stop_mode.get()
    if mode == "reset":
        app.grbl.reset()
    elif mode == "stop_reset":
        app.grbl.stop_stream()
        app.grbl.reset()
    else:
        app.grbl.stop_stream()


def all_stop_gcode_label(app) -> str:
    mode = app.all_stop_mode.get()
    if mode == "reset":
        return "Ctrl-X"
    return "Stop stream + Ctrl-X"
