def _ensure_toggle_button_styles(app) -> tuple[str, str]:
    style = app.style
    palette = getattr(app, "theme_palette", None) or {}
    on_fg = palette.get("toggle_on", "#2e7d32")
    off_fg = palette.get("toggle_off", "#c62828")
    on_style = "SimpleSender.ToggleOn.TButton"
    off_style = "SimpleSender.ToggleOff.TButton"
    disabled_fg = palette.get("muted_fg", "#b0b0b0")
    style.configure(
        on_style,
        foreground=on_fg,
    )
    style.map(
        on_style,
        foreground=[("disabled", disabled_fg)],
    )
    style.configure(
        off_style,
        foreground=off_fg,
    )
    style.map(
        off_style,
        foreground=[("disabled", disabled_fg)],
    )
    return on_style, off_style


def _apply_toggle_button_state(app, btn, enabled: bool):
    on_style, off_style = _ensure_toggle_button_styles(app)
    btn.config(style=on_style if enabled else off_style)


def refresh_tooltips_toggle_text(app):
    text = "Tips"
    enabled = app.tooltip_enabled.get()
    for attr in ("btn_toggle_tips", "btn_toggle_tips_settings"):
        btn = getattr(app, attr, None)
        if btn:
            btn.config(text=text)
            _apply_toggle_button_state(app, btn, enabled)


def refresh_render_3d_toggle_text(app):
    text = "3DR"
    enabled = app.render3d_enabled.get()
    for attr in ("btn_toggle_3d", "btn_toggle_3d_settings"):
        btn = getattr(app, attr, None)
        if btn:
            btn.config(text=text)
            _apply_toggle_button_state(app, btn, enabled)


def refresh_keybindings_toggle_text(app):
    text = "Keys"
    enabled = app.keyboard_bindings_enabled.get()
    for attr in ("btn_toggle_keybinds", "btn_toggle_keybinds_settings"):
        btn = getattr(app, attr, None)
        if btn:
            btn.config(text=text)
            _apply_toggle_button_state(app, btn, enabled)
