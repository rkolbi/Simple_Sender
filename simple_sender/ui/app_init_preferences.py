#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

def init_basic_preferences(app, app_version: str, module):
    deps = module
    DEFAULT_SETTINGS = deps.DEFAULT_SETTINGS
    GCODE_STREAMING_LINE_THRESHOLD = deps.GCODE_STREAMING_LINE_THRESHOLD
    PYGAME_AVAILABLE = deps.PYGAME_AVAILABLE
    WATCHDOG_HOMING_TIMEOUT = deps.WATCHDOG_HOMING_TIMEOUT
    tk = deps.tk
    ttk = deps.ttk
    tkfont = deps.tkfont

    def setting(key: str, fallback):
        return app.settings.get(key, DEFAULT_SETTINGS.get(key, fallback))

    app.tooltip_enabled = tk.BooleanVar(value=setting("tooltips_enabled", True))
    app.tooltip_timeout_sec = tk.DoubleVar(value=setting("tooltip_timeout_sec", 10.0))
    app.numeric_keypad_enabled = tk.BooleanVar(
        value=setting("numeric_keypad_enabled", True)
    )
    app.gui_logging_enabled = tk.BooleanVar(value=setting("gui_logging_enabled", True))
    app.error_dialogs_enabled = tk.BooleanVar(value=setting("error_dialogs_enabled", True))
    app.grbl_popup_enabled = tk.BooleanVar(value=setting("grbl_popup_enabled", True))
    app.grbl_popup_auto_dismiss_sec = tk.DoubleVar(
        value=setting("grbl_popup_auto_dismiss_sec", 12.0)
    )
    app.grbl_popup_dedupe_sec = tk.DoubleVar(
        value=setting("grbl_popup_dedupe_sec", 3.0)
    )
    app.macros_allow_python = tk.BooleanVar(value=setting("macros_allow_python", False))
    app.macro_line_timeout_sec = tk.DoubleVar(
        value=setting(
            "macro_line_timeout_sec",
            DEFAULT_SETTINGS.get("macro_line_timeout_sec", 0.0),
        )
    )
    app.macro_total_timeout_sec = tk.DoubleVar(
        value=setting(
            "macro_total_timeout_sec",
            DEFAULT_SETTINGS.get("macro_total_timeout_sec", 0.0),
        )
    )
    app.macro_probe_z_location = tk.DoubleVar(
        value=setting(
            "macro_probe_z_location",
            DEFAULT_SETTINGS.get("macro_probe_z_location", -5.0),
        )
    )
    app.macro_probe_safety_margin = tk.DoubleVar(
        value=setting(
            "macro_probe_safety_margin",
            DEFAULT_SETTINGS.get("macro_probe_safety_margin", 3.0),
        )
    )
    app.performance_mode = tk.BooleanVar(value=setting("performance_mode", False))
    app.render3d_enabled = tk.BooleanVar(value=setting("render3d_enabled", True))
    app._render3d_blocked = False
    app.all_stop_mode = tk.StringVar(value=setting("all_stop_mode", "stop_reset"))
    app.training_wheels = tk.BooleanVar(value=setting("training_wheels", True))
    app.stop_hold_on_focus_loss = tk.BooleanVar(
        value=setting("stop_joystick_hold_on_focus_loss", True)
    )
    app.validate_streaming_gcode = tk.BooleanVar(
        value=setting("validate_streaming_gcode", True)
    )
    app.streaming_line_threshold = tk.IntVar(
        value=setting("streaming_line_threshold", GCODE_STREAMING_LINE_THRESHOLD)
    )
    app.reconnect_on_open = tk.BooleanVar(value=setting("reconnect_on_open", True))
    app.fullscreen_on_startup = tk.BooleanVar(value=setting("fullscreen_on_startup", True))
    app.zeroing_persistent = tk.BooleanVar(value=setting("zeroing_persistent", False))
    app.keyboard_bindings_enabled = tk.BooleanVar(
        value=setting("keyboard_bindings_enabled", True)
    )
    app.joystick_bindings_enabled = tk.BooleanVar(
        value=setting("joystick_bindings_enabled", False)
    )
    app.dry_run_sanitize_stream = tk.BooleanVar(
        value=setting("dry_run_sanitize_stream", False)
    )
    app.homing_watchdog_enabled = tk.BooleanVar(
        value=setting("homing_watchdog_enabled", True)
    )
    app.homing_watchdog_timeout = tk.DoubleVar(
        value=setting("homing_watchdog_timeout", WATCHDOG_HOMING_TIMEOUT)
    )
    app.joystick_safety_enabled = tk.BooleanVar(
        value=setting("joystick_safety_enabled", False)
    )
    if app.joystick_bindings_enabled.get() and not PYGAME_AVAILABLE:
        app.joystick_bindings_enabled.set(False)
    app._joystick_auto_enable_requested = bool(app.joystick_bindings_enabled.get())
    app.job_completion_popup = tk.BooleanVar(value=setting("job_completion_popup", True))
    app.job_completion_beep = tk.BooleanVar(value=setting("job_completion_beep", False))
    pos_enabled = bool(setting("console_positions_enabled", True))
    legacy_status_enabled = bool(setting("console_status_enabled", False))
    combined_console_enabled = pos_enabled or legacy_status_enabled
    app.console_positions_enabled = tk.BooleanVar(value=combined_console_enabled)
    app.console_status_enabled = tk.BooleanVar(value=legacy_status_enabled)
    app.ui_scale = tk.DoubleVar(value=setting("ui_scale", 1.0))
    app.scrollbar_width = tk.StringVar(value=setting("scrollbar_width", "wide"))
    app.style = ttk.Style()
    try:
        default_scrollbar = app.style.lookup("TScrollbar", "width")
    except Exception:
        default_scrollbar = None
    try:
        app._scrollbar_width_default = int(default_scrollbar)
    except Exception:
        app._scrollbar_width_default = None
    app.theme_palettes = {}
    default_font = tkfont.nametofont("TkDefaultFont")
    app.icon_button_font = tkfont.Font(
        family=default_font.cget("family"),
        size=default_font.cget("size"),
        weight=default_font.cget("weight"),
    )
    try:
        tab_size = int(default_font.cget("size"))
    except Exception:
        tab_size = 10
    if tab_size < 0:
        tab_size += 1
    else:
        tab_size = max(tab_size - 1, 1)
    app.tab_font = tkfont.Font(
        family=default_font.cget("family"),
        size=tab_size,
        weight=default_font.cget("weight"),
    )
    app.icon_button_style = "SimpleSender.IconButton.TButton"
    app.style.configure(
        app.icon_button_style,
        anchor="center",
        justify="center",
        padding=(8, 4),
        font=app.icon_button_font,
    )
    app.home_button_style = "SimpleSender.HomeButton.TButton"
    home_size = default_font.cget("size")
    if not isinstance(home_size, int):
        try:
            home_size = int(home_size)
        except Exception:
            home_size = 10
    app.home_button_font = tkfont.Font(
        family=default_font.cget("family"),
        size=home_size,
        weight="bold",
    )
    app._ui_scale_named_font_bases = {}
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkFixedFont",
        "TkHeadingFont",
        "TkMenuFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            app._ui_scale_named_font_bases[name] = int(tkfont.nametofont(name).cget("size"))
        except Exception:
            pass
    app.style.configure(
        app.home_button_style,
        anchor="center",
        justify="center",
        padding=(10, 12),
        font=app.home_button_font,
    )
    app.mpos_button_style = "SimpleSender.MposButton.TButton"
    app.macro_button_style = "SimpleSender.MacroButton.TButton"
    touch_padding = (10, 12)
    app.style.configure(
        app.mpos_button_style,
        anchor="center",
        justify="center",
        padding=touch_padding,
    )
    app.style.configure(
        app.macro_button_style,
        anchor="center",
        justify="center",
        padding=touch_padding,
    )
    app.style.configure(
        "SimpleSender.UnitReported.TButton",
        anchor="center",
        justify="center",
        padding=touch_padding,
    )
    dro_size = default_font.cget("size")
    if not isinstance(dro_size, int):
        try:
            dro_size = int(dro_size)
        except Exception:
            dro_size = 10
    app.dro_value_font = tkfont.Font(
        family="Courier New",
        size=dro_size * 2,
        weight="bold",
    )
    app.console_font = tkfont.Font(
        family="Consolas",
        size=default_font.cget("size"),
        weight=default_font.cget("weight"),
    )
    app._ui_scale_custom_font_bases = {}
    for key in ("icon_button_font", "tab_font", "home_button_font", "dro_value_font", "console_font"):
        font = getattr(app, key, None)
        if font is None:
            continue
        try:
            app._ui_scale_custom_font_bases[key] = int(font.cget("size"))
        except Exception:
            pass
    app.available_themes = list(app.style.theme_names())
    theme_choice = setting("theme", app.style.theme_use())
    app.selected_theme = tk.StringVar(value=theme_choice)
    app._apply_theme(theme_choice)
    try:
        app._apply_scrollbar_width()
    except Exception:
        pass
    app.version_var = tk.StringVar(value=f"Simple Sender (BETA)  -  Version: v{app_version}")
    app.show_resume_from_button = tk.BooleanVar(value=setting("show_resume_from_button", True))
    app.show_recover_button = tk.BooleanVar(value=setting("show_recover_button", True))
    app.show_endstop_indicator = tk.BooleanVar(value=setting("show_endstop_indicator", True))
    app.show_probe_indicator = tk.BooleanVar(value=setting("show_probe_indicator", True))
    app.show_hold_indicator = tk.BooleanVar(value=setting("show_hold_indicator", True))
    app.auto_level_enabled = tk.BooleanVar(value=setting("auto_level_enabled", True))
    app.show_autolevel_overlay = tk.BooleanVar(value=setting("show_autolevel_overlay", True))
    app.show_quick_tips_button = tk.BooleanVar(value=setting("show_quick_tips_button", True))
    app.show_quick_3d_button = tk.BooleanVar(value=setting("show_quick_3d_button", True))
    app.show_quick_keys_button = tk.BooleanVar(value=setting("show_quick_keys_button", True))
    app.show_quick_alo_button = tk.BooleanVar(value=setting("show_quick_alo_button", True))
    app.show_quick_release_button = tk.BooleanVar(value=setting("show_quick_release_button", True))
    app.current_line_mode = tk.StringVar(value=setting("current_line_mode", "acked"))
