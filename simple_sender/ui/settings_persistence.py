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

    try:
        os.makedirs(os.path.dirname(app.settings_path), exist_ok=True)
    except Exception as exc:
        logger.exception("Failed to create settings directory: %s", exc)

    pos_status_enabled = bool(app.console_positions_enabled.get())

    data = dict(app.settings) if isinstance(app.settings, dict) else {}
    data.pop("keybindings_enabled", None)
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
        "view_3d": app.settings.get("view_3d"),
        "all_stop_mode": app.all_stop_mode.get(),
        "training_wheels": bool(app.training_wheels.get()),
        "reconnect_on_open": bool(app.reconnect_on_open.get()),
        "theme": app.selected_theme.get(),
        "console_positions_enabled": pos_status_enabled,
        "console_status_enabled": pos_status_enabled,
        "show_resume_from_button": bool(app.show_resume_from_button.get()),
        "show_recover_button": bool(app.show_recover_button.get()),
        "show_endstop_indicator": bool(app.show_endstop_indicator.get()),
        "show_probe_indicator": bool(app.show_probe_indicator.get()),
        "show_hold_indicator": bool(app.show_hold_indicator.get()),
        "show_quick_tips_button": bool(app.show_quick_tips_button.get()),
        "show_quick_3d_button": bool(app.show_quick_3d_button.get()),
        "show_quick_keys_button": bool(app.show_quick_keys_button.get()),
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
