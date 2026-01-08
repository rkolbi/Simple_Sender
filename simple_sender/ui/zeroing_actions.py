def zero_x(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G92 X0", "zero")


def zero_y(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G92 Y0", "zero")


def zero_z(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G92 Z0", "zero")


def zero_all(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G92 X0 Y0 Z0", "zero")


def goto_zero(app):
    if not app._require_grbl_connection():
        return
    app._send_manual("G0 X0 Y0", "zero")
