from tkinter import ttk

from simple_sender.ui.widgets import apply_tooltip, set_kb_id


def build_status_bar(app, before):
    # Status bar
    status_bar = ttk.Frame(app, padding=(8, 0, 8, 6))
    status_bar.pack(side="bottom", fill="x", before=before)
    app.status = ttk.Label(status_bar, text="Disconnected", anchor="w")
    app.status.pack(side="left", fill="x", expand=True)
    ttk.Label(status_bar, text="Progress").pack(side="right")
    app.progress_bar = ttk.Progressbar(
        status_bar,
        orient="horizontal",
        length=140,
        mode="determinate",
        maximum=100,
        variable=app.progress_pct,
        style="SimpleSender.Blue.Horizontal.TProgressbar",
    )
    app.progress_bar.pack(side="right", padx=(6, 12))
    app.buffer_bar = ttk.Progressbar(
        status_bar,
        orient="horizontal",
        length=120,
        mode="determinate",
        maximum=100,
        variable=app.buffer_fill_pct,
        style="SimpleSender.Blue.Horizontal.TProgressbar",
    )
    app.buffer_bar.pack(side="right", padx=(6, 0))
    app.error_dialog_status_label = ttk.Label(
        status_bar,
        textvariable=app.error_dialog_status_var,
        anchor="e",
    )
    app.error_dialog_status_label.pack(side="right", padx=(6, 0))
    apply_tooltip(
        app.error_dialog_status_label,
        "Shows when error dialogs are disabled or suppressed.",
    )
    ttk.Label(status_bar, textvariable=app.buffer_fill, anchor="e").pack(side="right")
    app.throughput_label = ttk.Label(
        status_bar,
        textvariable=app.throughput_var,
        anchor="e",
    )
    app.throughput_label.pack(side="right", padx=(6, 0))
    app._build_led_panel(status_bar)
    app.btn_toggle_tips = ttk.Button(
        status_bar,
        text="Tips",
        command=app._toggle_tooltips,
    )
    set_kb_id(app.btn_toggle_tips, "toggle_tooltips")
    app.btn_toggle_tips.pack(side="right", padx=(8, 0))
    app.btn_toggle_3d = ttk.Button(
        status_bar,
        text="3DR",
        command=app._toggle_render_3d,
    )
    set_kb_id(app.btn_toggle_3d, "toggle_render_3d")
    app.btn_toggle_3d.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_toggle_3d, "Toggle 3D toolpath rendering.")
    app.btn_toggle_keybinds = ttk.Button(
        status_bar,
        text="Keys",
        command=app._toggle_keyboard_bindings,
    )
    set_kb_id(app.btn_toggle_keybinds, "toggle_keybindings")
    app.btn_toggle_keybinds.pack(side="right", padx=(8, 0))
    apply_tooltip(app.btn_toggle_keybinds, "Toggle keyboard shortcuts.")
    app._refresh_tooltips_toggle_text()
    app._refresh_render_3d_toggle_text()
    app._refresh_keybindings_toggle_text()
    app._on_error_dialogs_enabled_change()
    if getattr(app, "_state_default_bg", None) is None:
        try:
            app._state_default_bg = app.machine_state_label.cget("background")
        except Exception:
            app._state_default_bg = app.status.cget("background") if app.status else None
    app._update_state_highlight(app._machine_state_text)

