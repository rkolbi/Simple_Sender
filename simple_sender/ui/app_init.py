import os
import queue
from typing import Any

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

from simple_sender.grbl_worker import GrblWorker
from simple_sender.macro_executor import MacroExecutor
from simple_sender.streaming_controller import StreamingController
from simple_sender.ui.grbl_settings import GRBLSettingsController
from simple_sender.ui.input_bindings import PYGAME_AVAILABLE
from simple_sender.ui.macro_panel import MacroPanel
from simple_sender.ui.toolpath import ToolpathPanel
from simple_sender.utils import Settings, get_settings_path
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import STATUS_POLL_DEFAULT


def init_settings_store(app, script_dir: str) -> tuple[float, float]:
    app.settings_path = get_settings_path()
    app.settings_dir = os.path.dirname(app.settings_path)
    app._settings_store = Settings(app.settings_path)
    app.settings = app._load_settings()
    # Backward compat: older builds stored this as "keybindings_enabled".
    if "keyboard_bindings_enabled" not in app.settings and "keybindings_enabled" in app.settings:
        app.settings["keyboard_bindings_enabled"] = bool(app.settings.get("keybindings_enabled", True))
    # Migrate jog feed defaults: keep legacy jog_feed for XY, force Z to its own default when absent.
    legacy_jog_feed = app.settings.get("jog_feed")
    has_jog_xy = "jog_feed_xy" in app.settings
    has_jog_z = "jog_feed_z" in app.settings
    default_jog_feed_xy = (
        app.settings["jog_feed_xy"]
        if has_jog_xy
        else (legacy_jog_feed if legacy_jog_feed is not None else 4000.0)
    )
    default_jog_feed_z = app.settings["jog_feed_z"] if has_jog_z else 500.0
    if (
        has_jog_z
        and (legacy_jog_feed is not None)
        and (app.settings["jog_feed_z"] == legacy_jog_feed)
        and (not has_jog_xy)
    ):
        # Likely legacy single value carried over; reset to Z default.
        default_jog_feed_z = 500.0
        app.settings["jog_feed_z"] = default_jog_feed_z
    return default_jog_feed_xy, default_jog_feed_z


def init_basic_preferences(app, app_version: str):
    def setting(key: str, fallback):
        return app.settings.get(key, DEFAULT_SETTINGS.get(key, fallback))

    app.tooltip_enabled = tk.BooleanVar(value=setting("tooltips_enabled", True))
    app.gui_logging_enabled = tk.BooleanVar(value=setting("gui_logging_enabled", True))
    app.error_dialogs_enabled = tk.BooleanVar(value=setting("error_dialogs_enabled", True))
    app.macros_allow_python = tk.BooleanVar(value=setting("macros_allow_python", False))
    app.performance_mode = tk.BooleanVar(value=setting("performance_mode", False))
    app.render3d_enabled = tk.BooleanVar(value=setting("render3d_enabled", True))
    app.all_stop_mode = tk.StringVar(value=setting("all_stop_mode", "stop_reset"))
    app.training_wheels = tk.BooleanVar(value=setting("training_wheels", True))
    app.reconnect_on_open = tk.BooleanVar(value=setting("reconnect_on_open", True))
    app.keyboard_bindings_enabled = tk.BooleanVar(
        value=setting("keyboard_bindings_enabled", True)
    )
    app.joystick_bindings_enabled = tk.BooleanVar(
        value=setting("joystick_bindings_enabled", False)
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
    status_enabled = bool(setting("console_status_enabled", True))
    combined_console_enabled = pos_enabled or status_enabled
    app.console_positions_enabled = tk.BooleanVar(value=combined_console_enabled)
    app.console_status_enabled = tk.BooleanVar(value=combined_console_enabled)
    app.style = ttk.Style()
    app.theme_palettes = {}
    default_font = tkfont.nametofont("TkDefaultFont")
    app.icon_button_font = tkfont.Font(
        family=default_font.cget("family"),
        size=default_font.cget("size"),
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
    app.available_themes = list(app.style.theme_names())
    theme_choice = setting("theme", app.style.theme_use())
    app.selected_theme = tk.StringVar(value=theme_choice)
    app._apply_theme(theme_choice)
    app.version_var = tk.StringVar(value=f"Simple Sender  -  Version: v{app_version}")
    app.show_resume_from_button = tk.BooleanVar(value=setting("show_resume_from_button", True))
    app.show_recover_button = tk.BooleanVar(value=setting("show_recover_button", True))
    app.current_line_mode = tk.StringVar(value=setting("current_line_mode", "acked"))


def init_runtime_state(
    app,
    default_jog_feed_xy: float,
    default_jog_feed_z: float,
    macro_search_dirs: tuple[str, ...],
):
    raw_bindings = app.settings.get("key_bindings", {})
    if isinstance(raw_bindings, dict):
        app._key_bindings = {}
        for k, v in raw_bindings.items():
            app._key_bindings[str(k)] = app._normalize_key_label(str(v))
    else:
        app._key_bindings = {}
    raw_joystick_bindings = app.settings.get("joystick_bindings", {})
    normalized_joystick_bindings: dict[str, dict[str, Any]] = {}
    if isinstance(raw_joystick_bindings, dict):
        for key, binding in raw_joystick_bindings.items():
            if isinstance(binding, dict):
                normalized_joystick_bindings[str(key)] = dict(binding)
    app._joystick_bindings: dict[str, dict[str, Any]] = normalized_joystick_bindings
    app._bound_key_sequences = set()
    app._key_sequence_map = {}
    app._kb_conflicts = set()
    app._key_sequence_buffer = []
    app._key_sequence_last_time = 0.0
    app._key_sequence_timeout = 0.8
    app._key_sequence_after_id = None
    app._kb_mod_keys_down = set()
    app._kb_mod_keysyms = {
        "Shift_L": "Shift",
        "Shift_R": "Shift",
        "Control_L": "Ctrl",
        "Control_R": "Ctrl",
        "Alt_L": "Alt",
        "Alt_R": "Alt",
    }
    app._kb_item_to_button = {}
    app._kb_edit = None
    app._kb_edit_state: dict[ttk.Entry, dict[str, Any]] = {}
    app._joystick_binding_map: dict[tuple, Any] = {}
    app._joystick_capture_state: dict[str, Any] | None = None
    app._joystick_poll_id: Any = None
    app._joystick_backend_ready = False
    app._joystick_names: dict[int, str] = {}
    app._joystick_instances: dict[int, Any] = {}
    app._joystick_button_poll_state: dict[tuple[int, int], bool] = {}
    app._joystick_axis_poll_state: dict[tuple[int, int], float] = {}
    app._joystick_hat_poll_state: dict[tuple[int, int], tuple[int, int]] = {}
    app._joystick_axis_active: set[tuple[int, int, int]] = set()
    app._joystick_hat_active: set[tuple[int, int, tuple[int, int]]] = set()
    app._virtual_hold_buttons = []
    app._active_joystick_hold_binding = None
    app._joystick_hold_after_id = None
    app._joystick_hold_missed_polls = 0
    raw_safety = app.settings.get("joystick_safety_binding")
    app._joystick_safety_binding = dict(raw_safety) if isinstance(raw_safety, dict) else None
    app._joystick_safety_active = False
    app.joystick_safety_status = tk.StringVar(value="Safety button: None")
    app.joystick_test_status = tk.StringVar(
        value="Press 'Refresh joystick list' to discover controllers."
    )
    app.joystick_event_status = tk.StringVar(
        value="Joystick events appear here while listening."
    )
    app._closing = False
    app._connecting = False
    app._disconnecting = False
    app._connect_thread = None
    app._disconnect_thread = None
    app._error_dialog_last_ts = 0.0
    app._error_dialog_window_start = 0.0
    app._error_dialog_count = 0
    app._error_dialog_suppressed = False
    app._homing_in_progress = False
    app._homing_state_seen = False
    app._homing_start_ts = 0.0
    app._homing_timeout_s = 30.0
    try:
        interval = float(setting("error_dialog_interval", 2.0))
    except Exception:
        interval = 2.0
    try:
        burst_window = float(setting("error_dialog_burst_window", 30.0))
    except Exception:
        burst_window = 30.0
    try:
        burst_limit = int(setting("error_dialog_burst_limit", 3))
    except Exception:
        burst_limit = 3
    if interval <= 0:
        interval = 2.0
    if burst_window <= 0:
        burst_window = 30.0
    if burst_limit <= 0:
        burst_limit = 3
    app._error_dialog_interval = interval
    app._error_dialog_burst_window = burst_window
    app._error_dialog_burst_limit = burst_limit
    app.error_dialog_interval_var = tk.DoubleVar(value=app._error_dialog_interval)
    app.error_dialog_burst_window_var = tk.DoubleVar(value=app._error_dialog_burst_window)
    app.error_dialog_burst_limit_var = tk.IntVar(value=app._error_dialog_burst_limit)
    app.error_dialog_status_var = tk.StringVar(value="")
    app.ui_q = queue.Queue()
    app.status_poll_interval = tk.DoubleVar(
        value=app.settings.get(
            "status_poll_interval",
            DEFAULT_SETTINGS.get("status_poll_interval", STATUS_POLL_DEFAULT),
        )
    )
    try:
        failure_limit = int(
            app.settings.get(
                "status_query_failure_limit",
                DEFAULT_SETTINGS.get("status_query_failure_limit", 3),
            )
        )
    except Exception:
        failure_limit = 3
    if failure_limit < 1:
        failure_limit = 1
    if failure_limit > 10:
        failure_limit = 10
    app.status_query_failure_limit = tk.IntVar(value=failure_limit)
    app.grbl = GrblWorker(app.ui_q)
    app.grbl.set_status_query_failure_limit(app.status_query_failure_limit.get())
    app.macro_executor = MacroExecutor(app, macro_search_dirs=macro_search_dirs)
    app.streaming_controller = StreamingController(app)
    app.macro_panel = MacroPanel(app)
    app.toolpath_panel = ToolpathPanel(app)
    app.settings_controller = GRBLSettingsController(app)
    app._install_dialog_loggers()
    app.report_callback_exception = app._tk_report_callback_exception
    app._apply_status_poll_profile()

    app.unit_mode = tk.StringVar(value=app.settings.get("unit_mode", DEFAULT_SETTINGS.get("unit_mode", "mm")))
    app._modal_units = app.unit_mode.get()
    app._report_units = None
    app.estimate_rate_x_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_x", DEFAULT_SETTINGS.get("estimate_rate_x", "")))
    )
    app.estimate_rate_y_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_y", DEFAULT_SETTINGS.get("estimate_rate_y", "")))
    )
    app.estimate_rate_z_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_z", DEFAULT_SETTINGS.get("estimate_rate_z", "")))
    )
    app.step_xy = tk.DoubleVar(value=app.settings.get("step_xy", DEFAULT_SETTINGS.get("step_xy", 1.0)))
    app.step_z = tk.DoubleVar(value=app.settings.get("step_z", DEFAULT_SETTINGS.get("step_z", 1.0)))
    app.jog_feed_xy = tk.DoubleVar(value=default_jog_feed_xy)
    app.jog_feed_z = tk.DoubleVar(value=default_jog_feed_z)

    app.connected = False
    app.current_port = tk.StringVar(value=app.settings.get("last_port", DEFAULT_SETTINGS.get("last_port", "")))

    # State
    app.machine_state = tk.StringVar(value="DISCONNECTED")
    app.wpos_x = tk.StringVar(value="0.000")
    app.wpos_y = tk.StringVar(value="0.000")
    app.wpos_z = tk.StringVar(value="0.000")
    app.mpos_x = tk.StringVar(value="0.000")
    app.mpos_y = tk.StringVar(value="0.000")
    app.mpos_z = tk.StringVar(value="0.000")
    app._wpos_raw = (0.0, 0.0, 0.0)
    app._mpos_raw = (0.0, 0.0, 0.0)
    app._last_gcode_lines = []
    app._last_gcode_path = None
    app._gcode_hash = None
    app._last_parse_result = None
    app._last_parse_hash = None
    app._gcode_loading = False
    app._gcode_load_token = 0
    app._gcode_parse_token = 0
    app.gcode_stats_var = tk.StringVar(value="No file loaded")
    app.gcode_load_var = tk.StringVar(value="")
    app._gcode_load_popup = None
    app._gcode_load_popup_label = None
    app._gcode_load_popup_bar = None
    app._rapid_rates = None
    app._rapid_rates_source = None
    app.fallback_rapid_rate = tk.StringVar(
        value=app.settings.get("fallback_rapid_rate", DEFAULT_SETTINGS.get("fallback_rapid_rate", ""))
    )
    app.estimate_factor = tk.DoubleVar(
        value=app.settings.get("estimate_factor", DEFAULT_SETTINGS.get("estimate_factor", 1.0))
    )
    app._estimate_factor_label = tk.StringVar(value=f"{app.estimate_factor.get():.2f}x")
    app._accel_rates = None
    app._stats_token = 0
    app._last_stats = None
    app._last_rate_source = None
    app._stats_cache = {}
    app._live_estimate_min = None
    app._stream_state = None
    app._stream_start_ts = None
    app._stream_pause_total = 0.0
    app._stream_paused_at = None
    app._toolpath_reparse_deferred = False
    app._job_started_at = None
    app._job_completion_notified = False
    app._grbl_ready = False
    app._alarm_locked = False
    app._alarm_message = ""
    app._pending_settings_refresh = False
    app._connected_port = None
    app._status_seen = False
    app.progress_pct = tk.IntVar(value=0)
    app.buffer_fill = tk.StringVar(value="Buffer: 0%")
    app.throughput_var = tk.StringVar(value="TX: 0 B/s")
    app.buffer_fill_pct = tk.IntVar(value=0)
    app._manual_controls = []
    app._offline_controls = set()
    app._override_controls = []
    app._xy_step_buttons = []
    app._z_step_buttons = []
    app.feed_override_scale = None
    app.spindle_override_scale = None
    app.feed_override_display = tk.StringVar(value="100%")
    app.spindle_override_display = tk.StringVar(value="100%")
    app.override_info_var = tk.StringVar(
        value="Overrides: Feed 100% | Rapid 100% | Spindle 100%"
    )
    app._feed_override_slider_locked = False
    app._spindle_override_slider_locked = False
    app._feed_override_slider_last_position = 100
    app._spindle_override_slider_last_position = 100
    app._machine_state_text = "DISCONNECTED"
    app._grbl_setting_info = {}
    app._grbl_setting_keys = []
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._confirm_last_time = {}
    app._confirm_debounce_sec = 0.8
    app._auto_reconnect_last_port = app.settings.get("last_port", DEFAULT_SETTINGS.get("last_port", ""))
    app._auto_reconnect_last_attempt = 0.0
    app._auto_reconnect_pending = False
    app._auto_reconnect_retry = 0
    app._auto_reconnect_delay = 3.0
    app._auto_reconnect_max_retry = 5
    app._auto_reconnect_next_ts = 0.0
    app._user_disconnect = False
    app._ui_throttle_ms = 100
    app._state_flash_after_id = None
    app._state_flash_color = None
    app._state_flash_on = False
    app._state_default_bg = None
