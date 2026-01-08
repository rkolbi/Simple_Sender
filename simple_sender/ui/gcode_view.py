def reset_gcode_view_for_run(app):
    if not hasattr(app, "gview") or app.gview.lines_count <= 0:
        return
    app._clear_pending_ui_updates()
    app.gview.clear_highlights()
    app._last_sent_index = -1
    app._last_acked_index = -1
    app.gview.highlight_current(0)
