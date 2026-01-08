def update_app_settings_scrollregion(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.configure(scrollregion=app.app_settings_canvas.bbox("all"))

def on_app_settings_mousewheel(app, event):
    if not hasattr(app, "app_settings_canvas"):
        return
    delta = 0
    if event.delta:
        delta = -int(event.delta / 120)
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    if delta:
        app.app_settings_canvas.yview_scroll(delta, "units")

def bind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.bind_all("<MouseWheel>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-4>", app._on_app_settings_mousewheel)
    app.app_settings_canvas.bind_all("<Button-5>", app._on_app_settings_mousewheel)

def unbind_app_settings_mousewheel(app):
    if not hasattr(app, "app_settings_canvas"):
        return
    app.app_settings_canvas.unbind_all("<MouseWheel>")
    app.app_settings_canvas.unbind_all("<Button-4>")
    app.app_settings_canvas.unbind_all("<Button-5>")
