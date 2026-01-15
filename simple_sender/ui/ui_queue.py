import queue


def drain_ui_queue(app):
    for _ in range(100):
        try:
            evt = app.ui_q.get_nowait()
        except queue.Empty:
            break
        try:
            app._handle_evt(evt)
        except Exception as exc:
            app._log_exception("UI event error", exc)
    if app._closing:
        return
    if hasattr(app, "_sync_tool_reference_label"):
        app._sync_tool_reference_label()
    app._maybe_auto_reconnect()
    app.after(50, app._drain_ui_queue)
