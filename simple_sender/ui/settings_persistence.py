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
import os
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import STATUS_POLL_DEFAULT
from simple_sender.utils.exceptions import SettingsLoadError, SettingsSaveError

logger = logging.getLogger(__name__)


def load_settings(app) -> dict:
    try:
        loaded = app._settings_store.load()
        if not loaded:
            logger.info("No settings file found; using defaults.")
    except SettingsLoadError as exc:
        logger.error(f"Failed to load settings: {exc}")
        app._settings_store.reset_to_defaults()
    except Exception as exc:
        logger.error(f"Unexpected error loading settings: {exc}")
        app._settings_store.reset_to_defaults()
    return app._settings_store.data


def save_settings(app):
    def safe_float(var, default, label):
        try:
            return float(var.get())
        except Exception:
            try:
                fallback = float(default)
            except Exception:
                fallback = 0.0
            app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
            return fallback

    def safe_int(var, default, label):
        try:
            return int(var.get())
        except Exception:
            try:
                fallback = int(default)
            except Exception:
                fallback = 0
            app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
            return fallback

    show_rapid, show_feed, show_arc = app.toolpath_panel.get_display_options()
    draw_percent = app.toolpath_panel.get_draw_percent()
    performance = app._clamp_toolpath_performance(app.toolpath_performance.get())
    performance = min(90.0, performance)

    full_limit = app._toolpath_limit_value(
        app.toolpath_full_limit.get(), app._toolpath_full_limit_default
    )
    interactive_limit = app._toolpath_limit_value(
        app.toolpath_interactive_limit.get(), app._toolpath_interactive_limit_default
    )
    arc_detail_deg = app._clamp_arc_detail(app.toolpath_arc_detail.get())
    app._apply_error_dialog_settings()
    app._on_status_failure_limit_change()
    app._on_homing_watchdog_change()

    try:
        os.makedirs(os.path.dirname(app.settings_path), exist_ok=True)
    except Exception as exc:
        logger.exception("Failed to create settings directory: %s", exc)

    pos_status_enabled = bool(app.console_positions_enabled.get())

    data = dict(app.settings) if isinstance(app.settings, dict) else {}
    data.pop("keybindings_enabled", None)
    data.pop("console_status_enabled", None)
    data.update({
        "last_port": app.current_port.get(),
        "unit_mode": app.unit_mode.get(),
        "step_xy": safe_float(
            app.step_xy,
            app.settings.get("step_xy", DEFAULT_SETTINGS.get("step_xy", 1.0)),
            "step XY",
        ),
        "step_z": safe_float(
            app.step_z,
            app.settings.get("step_z", DEFAULT_SETTINGS.get("step_z", 1.0)),
            "step Z",
        ),
        "jog_feed_xy": safe_float(
            app.jog_feed_xy,
            app.settings.get("jog_feed_xy", DEFAULT_SETTINGS.get("jog_feed_xy", 4000.0)),
            "jog feed XY",
        ),
        "jog_feed_z": safe_float(
            app.jog_feed_z,
            app.settings.get("jog_feed_z", DEFAULT_SETTINGS.get("jog_feed_z", 500.0)),
            "jog feed Z",
        ),
        "last_gcode_dir": app.settings.get("last_gcode_dir", ""),
        "window_geometry": app.geometry(),
        "tooltips_enabled": bool(app.tooltip_enabled.get()),
        "numeric_keypad_enabled": bool(
            app.numeric_keypad_enabled.get()
            if hasattr(app, "numeric_keypad_enabled")
            else app.settings.get(
                "numeric_keypad_enabled",
                DEFAULT_SETTINGS.get("numeric_keypad_enabled", True),
            )
        ),
        "gui_logging_enabled": bool(app.gui_logging_enabled.get()),
        "error_dialogs_enabled": bool(app.error_dialogs_enabled.get()),
        "performance_mode": bool(app.performance_mode.get()),
        "render3d_enabled": bool(app.render3d_enabled.get()),
        "status_poll_interval": safe_float(
            app.status_poll_interval,
            app.settings.get(
                "status_poll_interval",
                DEFAULT_SETTINGS.get("status_poll_interval", STATUS_POLL_DEFAULT),
            ),
            "status interval",
        ),
        "status_query_failure_limit": safe_int(
            app.status_query_failure_limit,
            app.settings.get(
                "status_query_failure_limit",
                DEFAULT_SETTINGS.get("status_query_failure_limit", 3),
            ),
            "status failure limit",
        ),
        "homing_watchdog_enabled": bool(app.homing_watchdog_enabled.get()),
        "homing_watchdog_timeout": safe_float(
            app.homing_watchdog_timeout,
            app.settings.get(
                "homing_watchdog_timeout",
                DEFAULT_SETTINGS.get("homing_watchdog_timeout", 60.0),
            ),
            "homing watchdog timeout",
        ),
        "view_3d": app.settings.get("view_3d"),
        "all_stop_mode": app.all_stop_mode.get(),
        "training_wheels": bool(app.training_wheels.get()),
        "stop_joystick_hold_on_focus_loss": bool(app.stop_hold_on_focus_loss.get()),
        "validate_streaming_gcode": bool(app.validate_streaming_gcode.get()),
        "streaming_line_threshold": safe_int(
            app.streaming_line_threshold,
            app.settings.get("streaming_line_threshold", DEFAULT_SETTINGS.get("streaming_line_threshold", 0)),
            "streaming line threshold",
        ),
        "reconnect_on_open": bool(app.reconnect_on_open.get()),
        "theme": app.selected_theme.get(),
        "ui_scale": (
            safe_float(
                app.ui_scale,
                app.settings.get("ui_scale", DEFAULT_SETTINGS.get("ui_scale", 1.0)),
                "ui scale",
            )
            if hasattr(app, "ui_scale")
            else app.settings.get("ui_scale", DEFAULT_SETTINGS.get("ui_scale", 1.0))
        ),
        "scrollbar_width": str(
            app.scrollbar_width.get()
            if hasattr(app, "scrollbar_width")
            else app.settings.get("scrollbar_width", DEFAULT_SETTINGS.get("scrollbar_width", "wide"))
        ).strip().lower(),
        "console_positions_enabled": pos_status_enabled,
        "show_resume_from_button": bool(app.show_resume_from_button.get()),
        "show_recover_button": bool(app.show_recover_button.get()),
        "show_endstop_indicator": bool(app.show_endstop_indicator.get()),
        "show_probe_indicator": bool(app.show_probe_indicator.get()),
        "show_hold_indicator": bool(app.show_hold_indicator.get()),
        "auto_level_enabled": bool(app.auto_level_enabled.get()),
        "show_autolevel_overlay": bool(app.show_autolevel_overlay.get()),
        "show_quick_tips_button": bool(app.show_quick_tips_button.get()),
        "show_quick_3d_button": bool(app.show_quick_3d_button.get()),
        "show_quick_keys_button": bool(app.show_quick_keys_button.get()),
        "show_quick_alo_button": bool(app.show_quick_alo_button.get()),
        "show_quick_release_button": bool(app.show_quick_release_button.get()),
        "fallback_rapid_rate": app.fallback_rapid_rate.get().strip(),
        "estimate_factor": safe_float(
            app.estimate_factor,
            app.settings.get("estimate_factor", DEFAULT_SETTINGS.get("estimate_factor", 1.0)),
            "estimate factor",
        ),
        "estimate_rate_x": app.estimate_rate_x_var.get().strip(),
        "estimate_rate_y": app.estimate_rate_y_var.get().strip(),
        "estimate_rate_z": app.estimate_rate_z_var.get().strip(),
        "keyboard_bindings_enabled": bool(app.keyboard_bindings_enabled.get()),
        "joystick_bindings_enabled": bool(app.joystick_bindings_enabled.get()),
        "joystick_safety_enabled": bool(app.joystick_safety_enabled.get()),
        "dry_run_sanitize_stream": bool(app.dry_run_sanitize_stream.get()),
        "joystick_bindings": dict(app._joystick_bindings),
        "joystick_safety_binding": dict(app._joystick_safety_binding) if app._joystick_safety_binding else None,
        "current_line_mode": app.current_line_mode.get(),
        "key_bindings": dict(app._key_bindings),
        "toolpath_full_limit": full_limit,
        "toolpath_interactive_limit": interactive_limit,
        "toolpath_arc_detail_deg": arc_detail_deg,
        "toolpath_lightweight": bool(app.toolpath_lightweight.get()),
        "toolpath_draw_percent": draw_percent,
        "toolpath_performance": performance,
        "toolpath_streaming_render_interval": safe_float(
            app.toolpath_streaming_render_interval,
            app.settings.get(
                "toolpath_streaming_render_interval",
                app._toolpath_streaming_render_interval_default,
            ),
            "3D streaming refresh interval",
        ),
        "toolpath_show_rapid": show_rapid,
        "toolpath_show_feed": show_feed,
        "toolpath_show_arc": show_arc,
        "error_dialog_interval": app._error_dialog_interval,
        "error_dialog_burst_window": app._error_dialog_burst_window,
        "error_dialog_burst_limit": app._error_dialog_burst_limit,
        "job_completion_popup": bool(app.job_completion_popup.get()),
        "job_completion_beep": bool(app.job_completion_beep.get()),
        "macros_allow_python": bool(app.macros_allow_python.get()),
        "zeroing_persistent": bool(app.zeroing_persistent.get()),
        "auto_level_settings": dict(getattr(app, "auto_level_settings", {})),
        "auto_level_job_prefs": dict(getattr(app, "auto_level_job_prefs", {})),
        "auto_level_presets": dict(getattr(app, "auto_level_presets", {})),
    })
    app.settings = data
    app._settings_store.data = app.settings
    try:
        app._settings_store.save()
    except SettingsSaveError as exc:
        try:
            app.ui_q.put(("log", f"[settings] Save failed: {exc}"))
            app.status.config(text="Settings save failed")
        except Exception:
            pass
    except Exception as exc:
        try:
            app.ui_q.put(("log", f"[settings] Save failed: {exc}"))
            app.status.config(text="Settings save failed")
        except Exception:
            pass
