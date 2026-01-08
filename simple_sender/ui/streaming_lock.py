def set_streaming_lock(app, locked: bool):
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except Exception:
        pass
    try:
        app.btn_refresh.config(state=state)
    except Exception:
        pass
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except Exception:
        pass
    try:
        app.btn_unit_toggle.config(state=state)
    except Exception:
        pass
