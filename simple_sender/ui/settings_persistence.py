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
from typing import cast
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
    return cast(dict, app._settings_store.data)


def _safe_float(app, var, default, label: str) -> float:
    try:
        return float(var.get())
    except Exception:
        try:
            fallback = float(default)
        except Exception:
            fallback = 0.0
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
        return fallback


def _safe_int(app, var, default, label: str) -> int:
    try:
        return int(var.get())
    except Exception:
        try:
            fallback = int(default)
        except Exception:
            fallback = 0
        app.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
        return fallback


def _read_nonnegative_float_setting(
    app,
    *,
    attr_name: str,
    key: str,
    default: float,
    label: str,
) -> float:
    var = getattr(app, attr_name, None)
    fallback = app.settings.get(key, DEFAULT_SETTINGS.get(key, default))
    if var is None:
        value = fallback
    else:
        value = _safe_float(app, var, fallback, label)
    return max(0.0, float(value))


def _build_motion_and_connection_settings(app, last_port: str) -> dict[str, object]:
    return {
        "last_port": str(last_port or ""),
        "unit_mode": app.unit_mode.get(),
        "step_xy": _safe_float(
            app,
            app.step_xy,
            app.settings.get("step_xy", DEFAULT_SETTINGS.get("step_xy", 1.0)),
            "step XY",
        ),
        "step_z": _safe_float(
            app,
            app.step_z,
            app.settings.get("step_z", DEFAULT_SETTINGS.get("step_z", 1.0)),
            "step Z",
        ),
        "jog_feed_xy": _safe_float(
            app,
            app.jog_feed_xy,
            app.settings.get("jog_feed_xy", DEFAULT_SETTINGS.get("jog_feed_xy", 4000.0)),
            "jog feed XY",
        ),
        "jog_feed_z": _safe_float(
            app,
            app.jog_feed_z,
            app.settings.get("jog_feed_z", DEFAULT_SETTINGS.get("jog_feed_z", 500.0)),
            "jog feed Z",
        ),
        "last_gcode_dir": app.settings.get("last_gcode_dir", ""),
        "window_geometry": app.geometry(),
        "status_poll_interval": _safe_float(
            app,
            app.status_poll_interval,
            app.settings.get(
                "status_poll_interval",
                DEFAULT_SETTINGS.get("status_poll_interval", STATUS_POLL_DEFAULT),
            ),
            "status interval",
        ),
        "status_query_failure_limit": _safe_int(
            app,
            app.status_query_failure_limit,
            app.settings.get(
                "status_query_failure_limit",
                DEFAULT_SETTINGS.get("status_query_failure_limit", 3),
            ),
            "status failure limit",
        ),
        "homing_watchdog_enabled": bool(app.homing_watchdog_enabled.get()),
        "homing_watchdog_timeout": _safe_float(
            app,
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
        "streaming_line_threshold": _safe_int(
            app,
            app.streaming_line_threshold,
            app.settings.get(
                "streaming_line_threshold",
                DEFAULT_SETTINGS.get("streaming_line_threshold", 0),
            ),
            "streaming line threshold",
        ),
        "reconnect_on_open": bool(app.reconnect_on_open.get()),
        "fullscreen_on_startup": bool(app.fullscreen_on_startup.get()),
    }


def _build_ui_settings(
    app,
    *,
    tooltip_timeout_value: float,
    grbl_popup_auto_dismiss_value: float,
    grbl_popup_dedupe_value: float,
    pos_status_enabled: bool,
) -> dict[str, object]:
    return {
        "tooltips_enabled": bool(app.tooltip_enabled.get()),
        "tooltip_timeout_sec": tooltip_timeout_value,
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
        "grbl_popup_enabled": bool(
            app.grbl_popup_enabled.get()
            if hasattr(app, "grbl_popup_enabled")
            else app.settings.get(
                "grbl_popup_enabled",
                DEFAULT_SETTINGS.get("grbl_popup_enabled", True),
            )
        ),
        "grbl_popup_auto_dismiss_sec": grbl_popup_auto_dismiss_value,
        "grbl_popup_dedupe_sec": grbl_popup_dedupe_value,
        "performance_mode": bool(app.performance_mode.get()),
        "render3d_enabled": bool(app.render3d_enabled.get()),
        "theme": app.selected_theme.get(),
        "ui_scale": (
            _safe_float(
                app,
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
            else app.settings.get(
                "scrollbar_width",
                DEFAULT_SETTINGS.get("scrollbar_width", "wide"),
            )
        ).strip().lower(),
        "console_positions_enabled": pos_status_enabled,
        "show_resume_from_button": bool(app.show_resume_from_button.get()),
        "show_recover_button": bool(app.show_recover_button.get()),
        "show_endstop_indicator": bool(app.show_endstop_indicator.get()),
        "show_probe_indicator": bool(app.show_probe_indicator.get()),
        "show_hold_indicator": bool(app.show_hold_indicator.get()),
        "show_quick_tips_button": bool(app.show_quick_tips_button.get()),
        "show_quick_3d_button": bool(app.show_quick_3d_button.get()),
        "show_quick_keys_button": bool(app.show_quick_keys_button.get()),
        "show_quick_alo_button": bool(app.show_quick_alo_button.get()),
        "show_quick_release_button": bool(app.show_quick_release_button.get()),
        "error_dialog_interval": app._error_dialog_interval,
        "error_dialog_burst_window": app._error_dialog_burst_window,
        "error_dialog_burst_limit": app._error_dialog_burst_limit,
        "job_completion_popup": bool(app.job_completion_popup.get()),
        "job_completion_beep": bool(app.job_completion_beep.get()),
    }


def _build_estimation_and_bindings_settings(app) -> dict[str, object]:
    return {
        "fallback_rapid_rate": app.fallback_rapid_rate.get().strip(),
        "estimate_factor": _safe_float(
            app,
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
        "joystick_safety_binding": (
            dict(app._joystick_safety_binding) if app._joystick_safety_binding else None
        ),
        "current_line_mode": app.current_line_mode.get(),
        "key_bindings": dict(app._key_bindings),
    }


def _build_toolpath_settings(
    app,
    *,
    full_limit,
    interactive_limit,
    arc_detail_deg: float,
    draw_percent: int,
    performance: float,
    show_rapid: bool,
    show_feed: bool,
    show_arc: bool,
) -> dict[str, object]:
    return {
        "toolpath_full_limit": full_limit,
        "toolpath_interactive_limit": interactive_limit,
        "toolpath_arc_detail_deg": arc_detail_deg,
        "toolpath_lightweight": bool(app.toolpath_lightweight.get()),
        "toolpath_draw_percent": draw_percent,
        "toolpath_performance": performance,
        "toolpath_streaming_render_interval": _safe_float(
            app,
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
    }


def _build_macro_and_autolevel_settings(
    app,
    *,
    macro_line_timeout_value: float,
    macro_total_timeout_value: float,
    macro_probe_z_value: float,
    macro_probe_margin_value: float,
) -> dict[str, object]:
    return {
        "auto_level_enabled": bool(app.auto_level_enabled.get()),
        "show_autolevel_overlay": bool(app.show_autolevel_overlay.get()),
        "macros_allow_python": bool(app.macros_allow_python.get()),
        "macro_line_timeout_sec": macro_line_timeout_value,
        "macro_total_timeout_sec": macro_total_timeout_value,
        "macro_probe_z_location": macro_probe_z_value,
        "macro_probe_safety_margin": macro_probe_margin_value,
        "zeroing_persistent": bool(app.zeroing_persistent.get()),
        "auto_level_settings": dict(getattr(app, "auto_level_settings", {})),
        "auto_level_job_prefs": dict(getattr(app, "auto_level_job_prefs", {})),
        "auto_level_presets": dict(getattr(app, "auto_level_presets", {})),
    }


def save_settings(app):
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
    tooltip_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="tooltip_timeout_sec",
        key="tooltip_timeout_sec",
        default=10.0,
        label="tooltip timeout",
    )
    macro_line_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_line_timeout_sec",
        key="macro_line_timeout_sec",
        default=0.0,
        label="macro line timeout",
    )
    macro_total_timeout_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_total_timeout_sec",
        key="macro_total_timeout_sec",
        default=0.0,
        label="macro total timeout",
    )
    macro_probe_z_value = _safe_float(
        app,
        getattr(app, "macro_probe_z_location", None),
        app.settings.get(
            "macro_probe_z_location",
            DEFAULT_SETTINGS.get("macro_probe_z_location", -5.0),
        ),
        "macro probe Z start",
    ) if getattr(app, "macro_probe_z_location", None) is not None else float(
        app.settings.get(
            "macro_probe_z_location",
            DEFAULT_SETTINGS.get("macro_probe_z_location", -5.0),
        )
    )
    macro_probe_margin_value = _read_nonnegative_float_setting(
        app,
        attr_name="macro_probe_safety_margin",
        key="macro_probe_safety_margin",
        default=3.0,
        label="macro probe safety margin",
    )
    grbl_popup_auto_dismiss_value = _read_nonnegative_float_setting(
        app,
        attr_name="grbl_popup_auto_dismiss_sec",
        key="grbl_popup_auto_dismiss_sec",
        default=12.0,
        label="GRBL popup auto-dismiss",
    )
    grbl_popup_dedupe_value = _read_nonnegative_float_setting(
        app,
        attr_name="grbl_popup_dedupe_sec",
        key="grbl_popup_dedupe_sec",
        default=3.0,
        label="GRBL popup dedupe",
    )

    data = dict(app.settings) if isinstance(app.settings, dict) else {}
    data.pop("keybindings_enabled", None)
    data.pop("console_status_enabled", None)
    last_port = ""
    try:
        last_port = getattr(app, "_auto_reconnect_last_port", "") or ""
    except Exception:
        last_port = ""
    if not last_port:
        try:
            last_port = app.current_port.get()
        except Exception:
            last_port = ""
    data.update(_build_motion_and_connection_settings(app, str(last_port or "")))
    data.update(
        _build_ui_settings(
            app,
            tooltip_timeout_value=tooltip_timeout_value,
            grbl_popup_auto_dismiss_value=grbl_popup_auto_dismiss_value,
            grbl_popup_dedupe_value=grbl_popup_dedupe_value,
            pos_status_enabled=pos_status_enabled,
        )
    )
    data.update(_build_estimation_and_bindings_settings(app))
    data.update(
        _build_toolpath_settings(
            app,
            full_limit=full_limit,
            interactive_limit=interactive_limit,
            arc_detail_deg=arc_detail_deg,
            draw_percent=draw_percent,
            performance=performance,
            show_rapid=show_rapid,
            show_feed=show_feed,
            show_arc=show_arc,
        )
    )
    data.update(
        _build_macro_and_autolevel_settings(
            app,
            macro_line_timeout_value=macro_line_timeout_value,
            macro_total_timeout_value=macro_total_timeout_value,
            macro_probe_z_value=float(macro_probe_z_value),
            macro_probe_margin_value=macro_probe_margin_value,
        )
    )
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
