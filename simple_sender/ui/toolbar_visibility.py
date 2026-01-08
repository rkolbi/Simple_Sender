def on_resume_button_visibility_change(app):
    app.settings["show_resume_from_button"] = bool(app.show_resume_from_button.get())
    update_resume_button_visibility(app)


def on_recover_button_visibility_change(app):
    app.settings["show_recover_button"] = bool(app.show_recover_button.get())
    update_recover_button_visibility(app)


def update_resume_button_visibility(app):
    if not hasattr(app, "btn_resume_from"):
        return
    visible = bool(app.show_resume_from_button.get())
    if visible:
        if not app.btn_resume_from.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            before_widget = getattr(app, "btn_unlock_top", None)
            if before_widget and before_widget.winfo_exists():
                pack_kwargs["before"] = before_widget
            app.btn_resume_from.pack(**pack_kwargs)
    else:
        app.btn_resume_from.pack_forget()


def update_recover_button_visibility(app):
    if not hasattr(app, "btn_alarm_recover"):
        return
    visible = bool(app.show_recover_button.get())
    if visible:
        if not app.btn_alarm_recover.winfo_ismapped():
            pack_kwargs = {"side": "left", "padx": (6, 0)}
            separator = getattr(app, "_recover_separator", None)
            if separator and separator.winfo_exists():
                pack_kwargs["before"] = separator
            app.btn_alarm_recover.pack(**pack_kwargs)
    else:
        app.btn_alarm_recover.pack_forget()
