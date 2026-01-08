import logging
import time

logger = logging.getLogger(__name__)


def update_tab_visibility(app, nb=None):
    if nb is None:
        nb = getattr(app, "notebook", None)
    if not nb:
        return
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception as exc:
        logger.exception("Failed to update tab visibility: %s", exc)
        return
    app.toolpath_panel.set_visible(label == "3D View")
    app.toolpath_panel.set_top_view_visible(label == "Top View")


def on_tab_changed(app, event):
    update_tab_visibility(app, event.widget)
    if not bool(app.gui_logging_enabled.get()):
        return
    nb = event.widget
    try:
        tab_id = nb.select()
        label = nb.tab(tab_id, "text")
    except Exception:
        return
    if not label:
        return
    ts = time.strftime("%H:%M:%S")
    app.streaming_controller.log(f"[{ts}] Tab: {label}")
