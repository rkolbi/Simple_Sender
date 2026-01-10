from simple_sender.ui.widgets import apply_tooltip, attach_log_gcode


_WCS_TO_P = {
    "G54": 1,
    "G55": 2,
    "G56": 3,
    "G57": 4,
    "G58": 5,
    "G59": 6,
    "G59.1": 7,
    "G59.2": 8,
    "G59.3": 9,
}


def _zeroing_persistent_enabled(app) -> bool:
    try:
        return bool(app.zeroing_persistent.get())
    except Exception:
        return bool(getattr(app, "zeroing_persistent", False))


def _current_wcs(app) -> str:
    wcs = ""
    try:
        with app.macro_executor.macro_vars() as macro_vars:
            wcs = str(macro_vars.get("WCS") or "")
    except Exception:
        wcs = ""
    wcs = wcs.strip().upper()
    return wcs if wcs else "G54"


def _wcs_to_p(wcs: str) -> int:
    return _WCS_TO_P.get(wcs, 1)


def zeroing_gcode(app, axes: str) -> str:
    axes = axes.upper()
    axis_order = [axis for axis in "XYZABC" if axis in axes]
    if not axis_order:
        return ""
    if _zeroing_persistent_enabled(app):
        wcs = _current_wcs(app)
        p_num = _wcs_to_p(wcs)
        words = " ".join(f"{axis}0" for axis in axis_order)
        return f"G10 L20 P{p_num} {words}".strip()
    words = " ".join(f"{axis}0" for axis in axis_order)
    return f"G92 {words}".strip()


def refresh_zeroing_ui(app):
    if not all(hasattr(app, attr) for attr in ("btn_zero_x", "btn_zero_y", "btn_zero_z", "btn_zero_all")):
        return
    persistent = _zeroing_persistent_enabled(app)
    if persistent:
        tip_x = "Zero the WCS X axis (G10 L20; uses active WCS)."
        tip_y = "Zero the WCS Y axis (G10 L20; uses active WCS)."
        tip_z = "Zero the WCS Z axis (G10 L20; uses active WCS)."
        tip_all = "Zero all WCS axes (G10 L20; uses active WCS)."
    else:
        tip_x = "Zero the WCS X axis (G92 X0)."
        tip_y = "Zero the WCS Y axis (G92 Y0)."
        tip_z = "Zero the WCS Z axis (G92 Z0)."
        tip_all = "Zero all WCS axes (G92 X0 Y0 Z0)."
    apply_tooltip(app.btn_zero_x, tip_x)
    apply_tooltip(app.btn_zero_y, tip_y)
    apply_tooltip(app.btn_zero_z, tip_z)
    apply_tooltip(app.btn_zero_all, tip_all)
    attach_log_gcode(app.btn_zero_x, lambda: zeroing_gcode(app, "X"))
    attach_log_gcode(app.btn_zero_y, lambda: zeroing_gcode(app, "Y"))
    attach_log_gcode(app.btn_zero_z, lambda: zeroing_gcode(app, "Z"))
    attach_log_gcode(app.btn_zero_all, lambda: zeroing_gcode(app, "XYZ"))


def on_zeroing_mode_change(app):
    try:
        app.settings["zeroing_persistent"] = bool(app.zeroing_persistent.get())
    except Exception:
        pass
    refresh_zeroing_ui(app)


def zero_x(app):
    if not app._require_grbl_connection():
        return
    cmd = zeroing_gcode(app, "X")
    if cmd:
        app._send_manual(cmd, "zero")


def zero_y(app):
    if not app._require_grbl_connection():
        return
    cmd = zeroing_gcode(app, "Y")
    if cmd:
        app._send_manual(cmd, "zero")


def zero_z(app):
    if not app._require_grbl_connection():
        return
    cmd = zeroing_gcode(app, "Z")
    if cmd:
        app._send_manual(cmd, "zero")


def zero_all(app):
    if not app._require_grbl_connection():
        return
    cmd = zeroing_gcode(app, "XYZ")
    if cmd:
        app._send_manual(cmd, "zero")


def goto_zero(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G0 X0 Y0", "zero")
