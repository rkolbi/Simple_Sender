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

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import sys

from simple_sender import tool_measurement
from simple_sender.ui.theme_helpers import resolve_theme_choice
from simple_sender.ui.ttk_themes import register_simple_sender_themes
from simple_sender.utils.constants import (
    JOG_DRO_SMOOTHING_CHOICES,
    JOG_DRO_SMOOTHING_OFF,
    MACRO_LINE_TIMEOUT,
    MACRO_TOTAL_TIMEOUT,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _toolbar_button_padding(style) -> object:
    try:
        configured = style.configure("TButton")
        if isinstance(configured, dict):
            padding = configured.get("padding")
            if padding not in ("", None):
                return padding
    except Exception as exc:
        _log_suppressed("Failed reading TButton configured padding", exc)
    try:
        padding = style.lookup("TButton", "padding")
        if padding not in ("", None):
            return padding
    except Exception as exc:
        _log_suppressed("Failed reading TButton lookup padding", exc)
    return (10, 6)


def _init_behavior_preferences(
    app,
    *,
    setting,
    default_settings: dict,
    gcode_streaming_line_threshold: int,
    gcode_ultra_large_size_threshold_mb: int,
    pygame_available: bool,
    watchdog_homing_timeout: float,
    tk,
) -> None:
    def _outlet_setting(key: str, fallback: int) -> int:
        try:
            value = int(setting(key, fallback))
        except Exception:
            value = int(fallback)
        if value not in (1, 2):
            return int(fallback)
        return value

    def _runtime_logging_mode_setting() -> str:
        raw = str(setting("runtime_logging_mode", "Standard") or "").strip().lower()
        if raw == "verbose":
            return "Verbose"
        return "Standard"

    app.tooltip_enabled = tk.BooleanVar(value=setting("tooltips_enabled", True))
    app.tooltip_timeout_sec = tk.DoubleVar(value=setting("tooltip_timeout_sec", 10.0))
    app.numeric_keypad_enabled = tk.BooleanVar(
        value=setting("numeric_keypad_enabled", True)
    )
    app.developer_options_enabled = tk.BooleanVar(
        value=setting("developer_options_enabled", False)
    )
    app.gui_logging_enabled = tk.BooleanVar(value=setting("gui_logging_enabled", True))
    app.runtime_logging_mode = tk.StringVar(value=_runtime_logging_mode_setting())
    app.pi_profile_enabled = tk.BooleanVar(value=setting("pi_profile_enabled", False))
    app.error_dialogs_enabled = tk.BooleanVar(value=setting("error_dialogs_enabled", True))
    app.grbl_popup_enabled = tk.BooleanVar(value=setting("grbl_popup_enabled", True))
    app.grbl_popup_dedupe_sec = tk.DoubleVar(
        value=setting("grbl_popup_dedupe_sec", 3.0)
    )
    app.macros_allow_python = tk.BooleanVar(value=setting("macros_allow_python", False))
    app.macro_line_timeout_sec = tk.DoubleVar(
        value=setting(
            "macro_line_timeout_sec",
            default_settings.get("macro_line_timeout_sec", MACRO_LINE_TIMEOUT),
        )
    )
    app.macro_total_timeout_sec = tk.DoubleVar(
        value=setting(
            "macro_total_timeout_sec",
            default_settings.get("macro_total_timeout_sec", MACRO_TOTAL_TIMEOUT),
        )
    )
    app.disable_macro_timeouts = tk.BooleanVar(
        value=setting(
            "disable_macro_timeouts",
            default_settings.get("disable_macro_timeouts", False),
        )
    )
    app.macro_probe_z_location = tk.DoubleVar(
        value=setting(
            "macro_probe_z_location",
            default_settings.get("macro_probe_z_location", -5.0),
        )
    )
    app.macro_probe_safety_margin = tk.DoubleVar(
        value=setting(
            "macro_probe_safety_margin",
            default_settings.get("macro_probe_safety_margin", 3.0),
        )
    )
    app.bit_setter_x = tk.DoubleVar(
        value=setting(
            "bit_setter_x",
            default_settings.get(
                "bit_setter_x", tool_measurement.DEFAULT_BIT_SETTER_X_MM
            ),
        )
    )
    app.bit_setter_y = tk.DoubleVar(
        value=setting(
            "bit_setter_y",
            default_settings.get(
                "bit_setter_y", tool_measurement.DEFAULT_BIT_SETTER_Y_MM
            ),
        )
    )
    app.bit_setter_rough_probe_speed = tk.DoubleVar(
        value=setting(
            "bit_setter_rough_probe_speed",
            default_settings.get(
                "bit_setter_rough_probe_speed",
                tool_measurement.DEFAULT_TOOL_PROBE_ROUGH_FEED_MM_MIN,
            ),
        )
    )
    app.bit_setter_fine_probe_speed = tk.DoubleVar(
        value=setting(
            "bit_setter_fine_probe_speed",
            default_settings.get(
                "bit_setter_fine_probe_speed",
                tool_measurement.DEFAULT_TOOL_PROBE_FINE_FEED_MM_MIN,
            ),
        )
    )
    app.bit_setter_probe_dwell = tk.DoubleVar(
        value=setting(
            "bit_setter_probe_dwell",
            default_settings.get(
                "bit_setter_probe_dwell",
                tool_measurement.DEFAULT_TOOL_PROBE_DWELL_S,
            ),
        )
    )
    app.performance_mode = tk.BooleanVar(value=setting("performance_mode", False))
    app.performance_profile_enabled = tk.BooleanVar(
        value=setting(
            "performance_profile_enabled",
            default_settings.get("performance_profile_enabled", True),
        )
    )
    app.performance_leak_watch_enabled = tk.BooleanVar(
        value=setting("performance_leak_watch_enabled", False)
    )
    app.performance_profile_log_path = tk.StringVar(
        value=str(setting("performance_profile_log_path", "") or "").strip()
    )
    app.all_stop_mode = tk.StringVar(value=setting("all_stop_mode", "stop_reset"))
    app.training_wheels = tk.BooleanVar(value=setting("training_wheels", True))
    app.stop_hold_on_focus_loss = tk.BooleanVar(
        value=setting("stop_joystick_hold_on_focus_loss", True)
    )
    app.streaming_line_threshold = tk.IntVar(
        value=setting("streaming_line_threshold", gcode_streaming_line_threshold)
    )
    app.ultra_large_size_threshold_mb = tk.IntVar(
        value=setting("ultra_large_size_threshold_mb", gcode_ultra_large_size_threshold_mb)
    )
    app.reconnect_on_open = tk.BooleanVar(value=setting("reconnect_on_open", True))
    app.fullscreen_on_startup = tk.BooleanVar(value=setting("fullscreen_on_startup", True))
    app.app_settings_preload_enabled = tk.BooleanVar(
        value=setting(
            "app_settings_preload_enabled",
            default_settings.get("app_settings_preload_enabled", False),
        )
    )
    app.zeroing_persistent = tk.BooleanVar(value=setting("zeroing_persistent", False))
    app.keyboard_bindings_enabled = tk.BooleanVar(
        value=setting("keyboard_bindings_enabled", True)
    )
    app.joystick_bindings_enabled = tk.BooleanVar(
        value=setting("joystick_bindings_enabled", False)
    )
    jog_dro_smoothing_mode = str(
        setting(
            "jog_dro_smoothing_mode",
            default_settings.get("jog_dro_smoothing_mode", JOG_DRO_SMOOTHING_OFF),
        )
        or ""
    ).strip().lower()
    if jog_dro_smoothing_mode not in JOG_DRO_SMOOTHING_CHOICES:
        jog_dro_smoothing_mode = str(default_settings.get("jog_dro_smoothing_mode", JOG_DRO_SMOOTHING_OFF))
    if jog_dro_smoothing_mode not in JOG_DRO_SMOOTHING_CHOICES:
        jog_dro_smoothing_mode = JOG_DRO_SMOOTHING_OFF
    app.jog_dro_smoothing_mode = tk.StringVar(value=jog_dro_smoothing_mode)
    app.dry_run_sanitize_stream = tk.BooleanVar(
        value=setting("dry_run_sanitize_stream", False)
    )
    app.homing_watchdog_enabled = tk.BooleanVar(
        value=setting("homing_watchdog_enabled", True)
    )
    app.homing_watchdog_timeout = tk.DoubleVar(
        value=setting("homing_watchdog_timeout", watchdog_homing_timeout)
    )
    app.joystick_safety_enabled = tk.BooleanVar(
        value=setting("joystick_safety_enabled", False)
    )
    try:
        joystick_hold_miss_limit = int(
            setting(
                "joystick_hold_miss_limit",
                default_settings.get("joystick_hold_miss_limit", 2),
            )
        )
    except Exception:
        joystick_hold_miss_limit = int(
            default_settings.get("joystick_hold_miss_limit", 2)
        )
    if joystick_hold_miss_limit < 1:
        joystick_hold_miss_limit = 1
    if joystick_hold_miss_limit > 8:
        joystick_hold_miss_limit = 8
    app.joystick_hold_miss_limit = tk.IntVar(value=joystick_hold_miss_limit)
    app.kasa_enabled = tk.BooleanVar(value=setting("kasa_enabled", False))
    app.kasa_device_identifier = tk.StringVar(
        value=str(setting("kasa_device_identifier", "") or "").strip()
    )
    app.vacuum_enabled = tk.BooleanVar(value=setting("vacuum_enabled", False))
    app.kasa_confirm_stream_directives = tk.BooleanVar(
        value=setting("kasa_confirm_stream_directives", False)
    )
    app.vacuum_off_delay_sec = tk.DoubleVar(
        value=setting(
            "vacuum_off_delay_sec",
            default_settings.get("vacuum_off_delay_sec", 0.0),
        )
    )
    app.vacuum_outlet = tk.IntVar(value=_outlet_setting("vacuum_outlet", 1))
    app.light_enabled = tk.BooleanVar(value=setting("light_enabled", False))
    app.light_outlet = tk.IntVar(value=_outlet_setting("light_outlet", 2))
    if not sys.platform.startswith("linux"):
        app.kasa_enabled.set(False)
        app.vacuum_enabled.set(False)
        app.light_enabled.set(False)
        app.kasa_device_identifier.set("")
    if app.joystick_bindings_enabled.get() and not pygame_available:
        app.joystick_bindings_enabled.set(False)
    app._joystick_auto_enable_requested = bool(app.joystick_bindings_enabled.get())
    app.job_completion_popup = tk.BooleanVar(value=setting("job_completion_popup", True))
    app.job_completion_beep = tk.BooleanVar(value=setting("job_completion_beep", False))
    app.xyz_plate_thickness = tk.DoubleVar(
        value=setting(
            "xyz_plate_thickness",
            default_settings.get(
                "xyz_plate_thickness",
                tool_measurement.DEFAULT_XYZ_PLATE_THICKNESS_MM,
            ),
        )
    )
    app.xyz_plate_min_safe_probe_distance = tk.DoubleVar(
        value=setting(
            "xyz_plate_min_safe_probe_distance",
            default_settings.get(
                "xyz_plate_min_safe_probe_distance",
                tool_measurement.DEFAULT_XYZ_PLATE_MIN_SAFE_PROBE_DISTANCE_MM,
            ),
        )
    )
    app.xyz_plate_x_offset = tk.DoubleVar(
        value=setting(
            "xyz_plate_x_offset",
            default_settings.get(
                "xyz_plate_x_offset",
                tool_measurement.DEFAULT_XYZ_PLATE_X_OFFSET_MM,
            ),
        )
    )
    app.xyz_plate_y_offset = tk.DoubleVar(
        value=setting(
            "xyz_plate_y_offset",
            default_settings.get(
                "xyz_plate_y_offset",
                tool_measurement.DEFAULT_XYZ_PLATE_Y_OFFSET_MM,
            ),
        )
    )
    app.xyz_plate_side_clearance_distance = tk.DoubleVar(
        value=setting(
            "xyz_plate_side_clearance_distance",
            default_settings.get(
                "xyz_plate_side_clearance_distance",
                tool_measurement.DEFAULT_XYZ_PLATE_SIDE_CLEARANCE_DISTANCE_MM,
            ),
        )
    )
    app.xyz_plate_z_rough_probe_speed = tk.DoubleVar(
        value=setting(
            "xyz_plate_z_rough_probe_speed",
            default_settings.get(
                "xyz_plate_z_rough_probe_speed",
                tool_measurement.DEFAULT_XYZ_PLATE_Z_ROUGH_PROBE_FEED_MM_MIN,
            ),
        )
    )
    app.xyz_plate_z_reprobe_speed = tk.DoubleVar(
        value=setting(
            "xyz_plate_z_reprobe_speed",
            default_settings.get(
                "xyz_plate_z_reprobe_speed",
                tool_measurement.DEFAULT_XYZ_PLATE_Z_REPROBE_FEED_MM_MIN,
            ),
        )
    )
    app.xyz_plate_z_fine_probe_speed = tk.DoubleVar(
        value=setting(
            "xyz_plate_z_fine_probe_speed",
            default_settings.get(
                "xyz_plate_z_fine_probe_speed",
                tool_measurement.DEFAULT_XYZ_PLATE_Z_FINE_PROBE_FEED_MM_MIN,
            ),
        )
    )
    app.xyz_plate_xy_rough_probe_speed = tk.DoubleVar(
        value=setting(
            "xyz_plate_xy_rough_probe_speed",
            default_settings.get(
                "xyz_plate_xy_rough_probe_speed",
                tool_measurement.DEFAULT_XYZ_PLATE_XY_ROUGH_PROBE_FEED_MM_MIN,
            ),
        )
    )
    app.xyz_plate_xy_fine_probe_speed = tk.DoubleVar(
        value=setting(
            "xyz_plate_xy_fine_probe_speed",
            default_settings.get(
                "xyz_plate_xy_fine_probe_speed",
                tool_measurement.DEFAULT_XYZ_PLATE_XY_FINE_PROBE_FEED_MM_MIN,
            ),
        )
    )
    app.xyz_plate_probe_dwell = tk.DoubleVar(
        value=setting(
            "xyz_plate_probe_dwell",
            default_settings.get(
                "xyz_plate_probe_dwell",
                tool_measurement.DEFAULT_XYZ_PLATE_PROBE_DWELL_S,
            ),
        )
    )
    app.console_positions_enabled = tk.BooleanVar(
        value=bool(setting("console_positions_enabled", True))
    )
    app.ui_scale = tk.DoubleVar(value=setting("ui_scale", 1.0))
    app.linux_file_dialog_scale = tk.DoubleVar(
        value=setting(
            "linux_file_dialog_scale",
            default_settings.get("linux_file_dialog_scale", 1.4),
        )
    )
    if sys.platform.startswith("linux"):
        linux_file_dialog_default_path = str(
            setting(
                "linux_file_dialog_default_path",
                default_settings.get("linux_file_dialog_default_path", "/root/CNC_Jobs"),
            )
            or ""
        ).strip()
        if not linux_file_dialog_default_path:
            linux_file_dialog_default_path = str(
                default_settings.get("linux_file_dialog_default_path", "/root/CNC_Jobs")
            ).strip() or "/root/CNC_Jobs"
        app.linux_file_dialog_default_path = tk.StringVar(
            value=linux_file_dialog_default_path
        )
    app.scrollbar_width = tk.StringVar(value=setting("scrollbar_width", "wide"))
    touch_scroll_mode_raw = str(
        setting(
            "touch_scroll_mode",
            default_settings.get("touch_scroll_mode", "thumb_and_swipe"),
        )
        or ""
    ).strip().lower()
    if touch_scroll_mode_raw not in {"thumb_only", "thumb_and_swipe"}:
        touch_scroll_mode_raw = "thumb_and_swipe"
    app.touch_scroll_mode = tk.StringVar(value=touch_scroll_mode_raw)


def _init_style_preferences(app, *, tkfont, ttk) -> None:
    app.style = ttk.Style()
    app.theme_palettes = register_simple_sender_themes(app.style)
    try:
        default_scrollbar = app.style.lookup("TScrollbar", "width")
    except Exception:
        default_scrollbar = None
    if default_scrollbar in ("", None):
        try:
            default_scrollbar = app.style.lookup("TScrollbar", "arrowsize")
        except Exception:
            default_scrollbar = None
    try:
        app._scrollbar_width_default = int(default_scrollbar)
    except Exception:
        app._scrollbar_width_default = None
    app._scrollbar_width_px = app._scrollbar_width_default
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
        padding=_toolbar_button_padding(app.style),
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
    toolbar_size = home_size + 1
    if toolbar_size < 1:
        toolbar_size = 1
    app.top_toolbar_button_font = tkfont.Font(
        family=default_font.cget("family"),
        size=toolbar_size,
        weight="bold",
    )
    app.top_toolbar_button_style = "SimpleSender.ToolbarButton.TButton"
    app.top_toolbar_button_styles = {
        "connection": "SimpleSender.ToolbarConnection.TButton",
        "job": "SimpleSender.ToolbarJob.TButton",
        "run": "SimpleSender.ToolbarRun.TButton",
        "pause": "SimpleSender.ToolbarPause.TButton",
        "resume": "SimpleSender.ToolbarResume.TButton",
        "stop": "SimpleSender.ToolbarStop.TButton",
        "recovery": "SimpleSender.ToolbarRecovery.TButton",
    }
    toolbar_button_padding = (10, 12)
    for style_name in (
        app.top_toolbar_button_style,
        *app.top_toolbar_button_styles.values(),
    ):
        app.style.configure(
            style_name,
            anchor="center",
            justify="center",
            padding=toolbar_button_padding,
            font=app.top_toolbar_button_font,
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
        except Exception as exc:
            _log_suppressed("Failed caching named-font base size for UI scaling", exc)
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
        except Exception as exc:
            _log_suppressed("Failed caching custom-font base size for UI scaling", exc)


def _init_visibility_preferences(app, *, setting, app_version: str, tk) -> None:
    app.version_var = tk.StringVar(
        value=f"Simple Sender  -  Version: v{app_version}"
    )
    app.show_resume_from_button = tk.BooleanVar(value=setting("show_resume_from_button", True))
    app.show_recover_button = tk.BooleanVar(value=setting("show_recover_button", True))
    app.show_top_toolbar_text = tk.BooleanVar(
        value=setting("show_top_toolbar_text", True)
    )
    app.show_endstop_indicator = tk.BooleanVar(value=setting("show_endstop_indicator", True))
    app.show_probe_indicator = tk.BooleanVar(value=setting("show_probe_indicator", True))
    app.show_hold_indicator = tk.BooleanVar(value=setting("show_hold_indicator", True))
    app.show_logs_button = tk.BooleanVar(
        value=setting("show_logs_button", False)
    )
    app.show_raw_grbl_button = tk.BooleanVar(
        value=setting("show_raw_grbl_button", False)
    )
    app.show_checklists_button = tk.BooleanVar(
        value=setting("show_checklists_button", True)
    )
    app.auto_level_enabled = tk.BooleanVar(value=setting("auto_level_enabled", True))
    app.show_autolevel_overlay = tk.BooleanVar(value=setting("show_autolevel_overlay", True))
    app.show_quick_tips_button = tk.BooleanVar(value=setting("show_quick_tips_button", True))
    app.show_quick_keys_button = tk.BooleanVar(value=setting("show_quick_keys_button", True))
    app.show_quick_alo_button = tk.BooleanVar(value=setting("show_quick_alo_button", True))
    app.show_quick_vac_button = tk.BooleanVar(value=setting("show_quick_vac_button", True))
    app.show_quick_light_button = tk.BooleanVar(value=setting("show_quick_light_button", True))
    app.show_quick_release_button = tk.BooleanVar(value=setting("show_quick_release_button", True))


def init_basic_preferences(app, app_version: str, module):
    deps = module
    default_settings = deps.DEFAULT_SETTINGS
    gcode_streaming_line_threshold = deps.GCODE_STREAMING_LINE_THRESHOLD
    gcode_ultra_large_size_threshold_mb = max(
        0,
        int(getattr(deps, "GCODE_ULTRA_LARGE_SIZE_THRESHOLD", 0) or 0) // (1024 * 1024),
    )
    pygame_available = deps.PYGAME_AVAILABLE
    watchdog_homing_timeout = deps.WATCHDOG_HOMING_TIMEOUT
    tk = deps.tk
    ttk = deps.ttk
    tkfont = deps.tkfont

    def setting(key: str, fallback):
        return app.settings.get(key, default_settings.get(key, fallback))

    _init_behavior_preferences(
        app,
        setting=setting,
        default_settings=default_settings,
        gcode_streaming_line_threshold=gcode_streaming_line_threshold,
        gcode_ultra_large_size_threshold_mb=gcode_ultra_large_size_threshold_mb,
        pygame_available=pygame_available,
        watchdog_homing_timeout=watchdog_homing_timeout,
        tk=tk,
    )
    _init_style_preferences(app, tkfont=tkfont, ttk=ttk)
    app.available_themes = list(app.style.theme_names())
    app.default_theme_name = str(default_settings.get("theme", "") or "").strip()
    requested_theme = str(setting("theme", app.style.theme_use()) or "").strip()
    theme_choice = resolve_theme_choice(
        app,
        requested_theme,
        default_theme=app.default_theme_name,
    )
    app.selected_theme = tk.StringVar(value=theme_choice)
    applied_theme = str(app._apply_theme(theme_choice) or theme_choice).strip()
    actual_theme = resolve_theme_choice(
        app,
        applied_theme,
        default_theme=theme_choice,
    )
    if actual_theme and actual_theme != app.selected_theme.get():
        app.selected_theme.set(actual_theme)
    if isinstance(getattr(app, "settings", None), dict) and actual_theme:
        app.settings["theme"] = actual_theme
    try:
        app._apply_scrollbar_width()
    except Exception as exc:
        _log_suppressed("Failed applying configured scrollbar width during app init preferences", exc)
    _init_visibility_preferences(app, setting=setting, app_version=app_version, tk=tk)
