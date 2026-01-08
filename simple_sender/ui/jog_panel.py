import tkinter as tk
from tkinter import ttk

from simple_sender.ui.icons import ICON_HOME, ICON_HOLD, ICON_RESUME, icon_label
from simple_sender.ui.widgets import (
    StopSignButton,
    _resolve_widget_bg,
    apply_tooltip,
    attach_log_gcode,
    set_kb_id,
)

def build_jog_panel(app, parent):
    top = ttk.Frame(parent)
    top.pack(side="top", fill="x")

    # Left: DRO
    mpos = ttk.Labelframe(top, text="Machine Position (MPos)", padding=8)
    mpos.pack(side="left", fill="y", padx=(0, 10))

    app._dro_value_row(mpos, "X", app.mpos_x)
    app._dro_value_row(mpos, "Y", app.mpos_y)
    app._dro_value_row(mpos, "Z", app.mpos_z)
    app.btn_home_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_HOME, "Home"),
        style=app.icon_button_style,
        command=app._start_homing,
    )
    set_kb_id(app.btn_home_mpos, "home")
    app.btn_home_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_home_mpos)
    apply_tooltip(app.btn_home_mpos, "Run the homing cycle.")
    attach_log_gcode(app.btn_home_mpos, "$H")
    app.btn_unit_toggle = ttk.Button(
        mpos,
        text=app._unit_toggle_label(),
        style=app.icon_button_style,
        command=app._toggle_unit_mode,
    )
    set_kb_id(app.btn_unit_toggle, "unit_toggle")
    app.btn_unit_toggle.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_unit_toggle)
    app._offline_controls.add(app.btn_unit_toggle)
    apply_tooltip(
        app.btn_unit_toggle,
        "Toggle modal units (G20/G21). Blue text means report units are tracked ($13).",
    )
    app._update_unit_toggle_display()
    app.btn_hold_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_HOLD, "Hold"),
        style=app.icon_button_style,
        command=lambda: app._run_if_connected(app.grbl.hold),
    )
    set_kb_id(app.btn_hold_mpos, "feed_hold")
    app.btn_hold_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_hold_mpos)
    apply_tooltip(app.btn_hold_mpos, "Feed hold.")
    attach_log_gcode(app.btn_hold_mpos, "!")
    app.btn_resume_mpos = ttk.Button(
        mpos,
        text=icon_label(ICON_RESUME, "Resume"),
        style=app.icon_button_style,
        command=lambda: app._run_if_connected(app.grbl.resume),
    )
    set_kb_id(app.btn_resume_mpos, "feed_resume")
    app.btn_resume_mpos.pack(fill="x", pady=(6, 0))
    app._manual_controls.append(app.btn_resume_mpos)
    apply_tooltip(app.btn_resume_mpos, "Resume after hold.")
    attach_log_gcode(app.btn_resume_mpos, "~")

    dro = ttk.Labelframe(top, text="Work Position (WPos)", padding=8)
    dro.pack(side="left", fill="y", padx=(0, 10))

    app.btn_zero_x = app._dro_row(dro, "X", app.wpos_x, app.zero_x)
    app.btn_zero_y = app._dro_row(dro, "Y", app.wpos_y, app.zero_y)
    app.btn_zero_z = app._dro_row(dro, "Z", app.wpos_z, app.zero_z)
    app._manual_controls.extend([app.btn_zero_x, app.btn_zero_y, app.btn_zero_z])
    apply_tooltip(app.btn_zero_x, "Zero the WCS X axis (G92 X0).")
    apply_tooltip(app.btn_zero_y, "Zero the WCS Y axis (G92 Y0).")
    apply_tooltip(app.btn_zero_z, "Zero the WCS Z axis (G92 Z0).")
    attach_log_gcode(app.btn_zero_x, "G92 X0")
    attach_log_gcode(app.btn_zero_y, "G92 Y0")
    attach_log_gcode(app.btn_zero_z, "G92 Z0")

    btns = ttk.Frame(dro)
    btns.pack(fill="x", pady=(6, 0))
    app.btn_zero_all = ttk.Button(btns, text="Zero All", command=app.zero_all)
    set_kb_id(app.btn_zero_all, "zero_all")
    app.btn_zero_all.pack(side="left", expand=True, fill="x")
    app._manual_controls.append(app.btn_zero_all)
    apply_tooltip(app.btn_zero_all, "Zero all WCS axes (G92 X0 Y0 Z0).")
    attach_log_gcode(app.btn_zero_all, "G92 X0 Y0 Z0")
    app.btn_goto_zero = ttk.Button(btns, text="Goto Zero", command=app.goto_zero)
    set_kb_id(app.btn_goto_zero, "goto_zero")
    app.btn_goto_zero.pack(side="left", expand=True, fill="x", padx=(6, 0))
    app._manual_controls.append(app.btn_goto_zero)
    apply_tooltip(app.btn_goto_zero, "Rapid move to WCS X0 Y0.")
    attach_log_gcode(app.btn_goto_zero, "G0 X0 Y0")

    style = ttk.Style()
    sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background") or "#a0a0a0"
    bg_color = _resolve_widget_bg(dro)
    try:
        r1, g1, b1 = dro.winfo_rgb(sep_color)
        r2, g2, b2 = dro.winfo_rgb(bg_color)
        t = 0.65
        sep_color = "#{:02x}{:02x}{:02x}".format(
            int((r1 + (r2 - r1) * t) / 256),
            int((g1 + (g2 - g1) * t) / 256),
            int((b1 + (b2 - b1) * t) / 256),
        )
    except tk.TclError:
        sep_color = "#dcdcdc"

    tk.Frame(dro, height=1, bg=sep_color, bd=0, highlightthickness=0).pack(fill="x", pady=(8, 6))
    macro_left = ttk.Frame(dro)
    macro_left.pack(fill="x")

    # Center: Jog
    jog = ttk.Labelframe(top, text="Jog", padding=8)
    jog.pack(side="left", fill="both", expand=True, padx=(10, 10))

    pad = ttk.Frame(jog)
    pad.pack(side="left", padx=(0, 12))

    def _jog_feed_for_move(dx, dy, dz) -> float:
        # Use Z feed only for pure Z moves; otherwise use XY feed.
        if abs(dz) > 0 and abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return float(app.jog_feed_z.get())
        return float(app.jog_feed_xy.get())

    def j(dx, dy, dz):
        if not app.grbl.is_connected():
            app.streaming_controller.log("Jog ignored - GRBL is not connected.")
            return
        feed = _jog_feed_for_move(dx, dy, dz)
        app.grbl.jog(dx, dy, dz, feed, app.unit_mode.get())

    def jog_cmd(dx, dy, dz):
        feed = _jog_feed_for_move(dx, dy, dz)
        gunit = "G21" if app.unit_mode.get() == "mm" else "G20"
        return f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"

    # 3x3 pad for XY
    app.btn_jog_y_plus = ttk.Button(pad, text="Y+", width=6, command=lambda: j(0, app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_plus, "jog_y_plus")
    app.btn_jog_y_plus.grid(row=0, column=1, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_plus, lambda: jog_cmd(0, app.step_xy.get(), 0))
    app.btn_jog_x_minus = ttk.Button(pad, text="X-", width=6, command=lambda: j(-app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_minus, "jog_x_minus")
    app.btn_jog_x_minus.grid(row=1, column=0, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_minus, lambda: jog_cmd(-app.step_xy.get(), 0, 0))
    app.btn_jog_x_plus = ttk.Button(pad, text="X+", width=6, command=lambda: j(app.step_xy.get(), 0, 0))
    set_kb_id(app.btn_jog_x_plus, "jog_x_plus")
    app.btn_jog_x_plus.grid(row=1, column=2, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_x_plus, lambda: jog_cmd(app.step_xy.get(), 0, 0))
    app.btn_jog_y_minus = ttk.Button(pad, text="Y-", width=6, command=lambda: j(0, -app.step_xy.get(), 0))
    set_kb_id(app.btn_jog_y_minus, "jog_y_minus")
    app.btn_jog_y_minus.grid(row=2, column=1, padx=4, pady=2)
    attach_log_gcode(app.btn_jog_y_minus, lambda: jog_cmd(0, -app.step_xy.get(), 0))
    apply_tooltip(app.btn_jog_y_plus, "Jog +Y by the selected step.")
    apply_tooltip(app.btn_jog_y_minus, "Jog -Y by the selected step.")
    apply_tooltip(app.btn_jog_x_minus, "Jog -X by the selected step.")
    apply_tooltip(app.btn_jog_x_plus, "Jog +X by the selected step.")

    style = ttk.Style()
    sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background")
    pad_bg = style.lookup("TFrame", "background") or app.cget("bg")
    style.configure("JogSeparator.TSeparator", background=sep_color)
    # Z
    def cancel_jog():
        app._stop_joystick_hold()
        app.grbl.jog_cancel()
        try:
            app.grbl.cancel_pending_jogs()
        except Exception:
            pass

    app.btn_jog_cancel = StopSignButton(
        pad,
        text="JOG\nSTOP",
        fill="#f2b200",
        text_color="#000000",
        command=cancel_jog,
        bg=pad_bg,
    )
    set_kb_id(app.btn_jog_cancel, "jog_stop")
    app.btn_jog_cancel.grid(row=0, column=3, rowspan=3, padx=(6, 2), pady=2, sticky="ns")
    apply_tooltip(app.btn_jog_cancel, "Cancel an active jog ($J cancel).")
    attach_log_gcode(app.btn_jog_cancel, "RT 0x85")

    sep = ttk.Separator(pad, orient="vertical", style="JogSeparator.TSeparator")
    # Align the jog tab separator with the macro panel spacing by nudging it right.
    sep.grid(row=0, column=4, rowspan=5, sticky="ns", padx=(10, 2))

    app.btn_jog_z_plus = ttk.Button(pad, text="Z+", width=6, command=lambda: j(0, 0, app.step_z.get()))
    set_kb_id(app.btn_jog_z_plus, "jog_z_plus")
    app.btn_jog_z_plus.grid(row=0, column=5, padx=(6, 2), pady=2)
    app.btn_jog_z_minus = ttk.Button(pad, text="Z-", width=6, command=lambda: j(0, 0, -app.step_z.get()))
    set_kb_id(app.btn_jog_z_minus, "jog_z_minus")
    app.btn_jog_z_minus.grid(row=2, column=5, padx=(6, 2), pady=2)
    apply_tooltip(app.btn_jog_z_plus, "Jog +Z by the selected step.")
    apply_tooltip(app.btn_jog_z_minus, "Jog -Z by the selected step.")
    attach_log_gcode(app.btn_jog_z_plus, lambda: jog_cmd(0, 0, app.step_z.get()))
    attach_log_gcode(app.btn_jog_z_minus, lambda: jog_cmd(0, 0, -app.step_z.get()))

    all_stop_size = 60
    app._all_stop_offset_px = int(app.winfo_fpixels("0.7i"))
    app._all_stop_slot = ttk.Frame(pad, width=all_stop_size, height=all_stop_size)
    app._all_stop_slot.grid(row=0, column=6, rowspan=3, padx=(6, 0), pady=2, sticky="ns")
    app._all_stop_slot.grid_propagate(False)
    app.btn_all_stop = StopSignButton(
        pad,
        text="ALL\nSTOP",
        fill="#d83b2d",
        text_color="#ffffff",
        command=app._all_stop_action,
        bg=pad_bg,
        size=all_stop_size,
    )
    set_kb_id(app.btn_all_stop, "all_stop")
    apply_tooltip(app.btn_all_stop, "Immediate stop (behavior from App Settings).")
    attach_log_gcode(app.btn_all_stop, app._all_stop_gcode_label)
    pad.bind("<Configure>", app._position_all_stop_offset, add="+")
    app.after(0, app._position_all_stop_offset)

    app._manual_controls.extend([
        app.btn_jog_y_plus,
        app.btn_jog_x_minus,
        app.btn_jog_x_plus,
        app.btn_jog_y_minus,
        app.btn_jog_z_plus,
        app.btn_jog_z_minus,
    ])
    app._manual_controls.append(app.btn_jog_cancel)
    app._manual_controls.append(app.btn_all_stop)

    spacer = ttk.Frame(pad, height=6)
    spacer.grid(row=3, column=0, columnspan=6)

    xy_steps = ttk.Frame(pad)
    xy_steps.grid(row=4, column=0, columnspan=4, pady=(6, 0))
    xy_values = [0.1, 1.0, 5.0, 10, 25, 50, 100, 400]
    for i, v in enumerate(xy_values):
        r = i // 4
        c = i % 4
        btn = ttk.Button(
            xy_steps,
            text=f"{v:g}",
            command=lambda value=v: app._set_step_xy(value),
        )
        btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
        set_kb_id(btn, f"step_xy_{v:g}")
        app._xy_step_buttons.append((v, btn))
        apply_tooltip(btn, f"Set XY step to {v:g}.")

    z_steps = ttk.Frame(pad)
    z_steps.grid(row=4, column=5, padx=(6, 0), pady=(6, 0))
    z_values = [0.05, 0.1, 0.5, 1, 5, 10, 25, 50]
    for i, v in enumerate(z_values):
        r = 0 if i < 4 else 1
        c = i if i < 4 else i - 4
        btn = ttk.Button(
            z_steps,
            text=f"{v:g}",
            command=lambda value=v: app._set_step_z(value),
        )
        btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
        set_kb_id(btn, f"step_z_{v:g}")
        app._z_step_buttons.append((v, btn))
        apply_tooltip(btn, f"Set Z step to {v:g}.")

    app._manual_controls.extend([btn for _, btn in app._xy_step_buttons])
    app._manual_controls.extend([btn for _, btn in app._z_step_buttons])
    app._set_step_xy(app.step_xy.get())
    app._set_step_z(app.step_z.get())

    macro_right = ttk.Frame(pad)
    macro_right.grid(row=5, column=0, columnspan=6, pady=(6, 0), sticky="ew")

    app.macro_panel.attach_frames(macro_left, macro_right)

    app._set_unit_mode(app.unit_mode.get())

