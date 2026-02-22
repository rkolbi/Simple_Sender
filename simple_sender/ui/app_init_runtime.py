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

from typing import Any, cast


def _normalize_key_bindings(app) -> None:
    raw_bindings = app.settings.get("key_bindings", {})
    if isinstance(raw_bindings, dict):
        app._key_bindings = {}
        for key, value in raw_bindings.items():
            app._key_bindings[str(key)] = app._normalize_key_label(str(value))
    else:
        app._key_bindings = {}


def _normalize_joystick_bindings(app) -> None:
    raw_joystick_bindings = app.settings.get("joystick_bindings", {})
    normalized_joystick_bindings: dict[str, dict[str, Any]] = {}
    if isinstance(raw_joystick_bindings, dict):
        for key, binding in raw_joystick_bindings.items():
            if isinstance(binding, dict):
                normalized_joystick_bindings[str(key)] = dict(binding)
    app._joystick_bindings = normalized_joystick_bindings


def _init_keyboard_runtime_state(app) -> None:
    app._bound_key_sequences = set()
    app._key_sequence_map = {}
    app._kb_conflicts = set()
    app._key_sequence_buffer = []
    app._key_sequence_last_time = 0.0
    app._key_sequence_timeout = 0.8
    app._key_sequence_after_id = None
    app._macro_status_active = False
    app._macro_status_after_id = None
    app._macro_status_scroll_index = 0
    app._macro_status_text = ""
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
    app._kb_edit_state = cast(dict[Any, dict[str, Any]], {})


def _init_joystick_runtime_state(app, tk) -> None:
    app._joystick_binding_map = cast(dict[tuple, Any], {})
    app._joystick_capture_state = None
    app._joystick_poll_id = None
    app._joystick_backend_ready = False
    app._joystick_device_count = 0
    app._joystick_last_discovery = 0.0
    app._joystick_last_live_status = 0.0
    app._joystick_names = cast(dict[int, str], {})
    app._joystick_instances = cast(dict[int, Any], {})
    app._joystick_button_poll_state = cast(dict[tuple[int, int], bool], {})
    app._joystick_axis_poll_state = cast(dict[tuple[int, int], float], {})
    app._joystick_hat_poll_state = cast(dict[tuple[int, int], tuple[int, int]], {})
    app._joystick_axis_active = cast(set[tuple[int, int, int]], set())
    app._joystick_hat_active = cast(set[tuple[int, int, tuple[int, int]]], set())
    app._virtual_hold_buttons = []
    app._active_joystick_hold_binding = None
    app._joystick_hold_after_id = None
    app._joystick_hold_missed_polls = 0
    app._joystick_hold_last_ts = None
    app._joystick_hold_jog_sent = False
    raw_safety = app.settings.get("joystick_safety_binding")
    app._joystick_safety_binding = dict(raw_safety) if isinstance(raw_safety, dict) else None
    app._joystick_safety_active = False
    app.joystick_safety_status = tk.StringVar(value="Safety button: None")
    app.joystick_device_status = tk.StringVar(value="Hot-plug status: unknown")
    app.joystick_test_status = tk.StringVar(
        value="Press 'Refresh joystick list' to discover controllers."
    )
    app.joystick_event_status = tk.StringVar(
        value="Joystick events appear here while listening."
    )
    app.joystick_live_status = tk.StringVar(value="Joystick state: idle")
    app.keyboard_live_status = tk.StringVar(value="Keyboard state: idle")


def _init_connection_runtime_state(app) -> None:
    app._closing = False
    app._connecting = False
    app._disconnecting = False
    app._connect_thread = None
    app._disconnect_thread = None


def _clamp_float_setting(setting, key: str, fallback: float) -> float:
    try:
        value = float(setting(key, fallback))
    except Exception:
        value = fallback
    if value <= 0:
        value = fallback
    return value


def _clamp_int_setting(setting, key: str, fallback: int) -> int:
    try:
        value = int(setting(key, fallback))
    except Exception:
        value = fallback
    if value <= 0:
        value = fallback
    return value


def _init_error_dialog_runtime_state(app, setting, tk) -> None:
    app._error_dialog_last_ts = 0.0
    app._error_dialog_window_start = 0.0
    app._error_dialog_count = 0
    app._error_dialog_suppressed = False
    app._grbl_code_popup = None
    app._grbl_code_popup_vars = None
    app._grbl_code_popup_after_id = None
    app._grbl_code_popup_last_ts_by_code = {}
    app._grbl_code_popup_last_suppressed_log_ts_by_code = {}
    app._pending_force_g90 = False
    app._homing_in_progress = False
    app._homing_state_seen = False
    app._homing_start_ts = 0.0
    app._homing_timeout_s = 30.0

    app._error_dialog_interval = _clamp_float_setting(setting, "error_dialog_interval", 2.0)
    app._error_dialog_burst_window = _clamp_float_setting(setting, "error_dialog_burst_window", 30.0)
    app._error_dialog_burst_limit = _clamp_int_setting(setting, "error_dialog_burst_limit", 3)

    app.error_dialog_interval_var = tk.DoubleVar(value=app._error_dialog_interval)
    app.error_dialog_burst_window_var = tk.DoubleVar(value=app._error_dialog_burst_window)
    app.error_dialog_burst_limit_var = tk.IntVar(value=app._error_dialog_burst_limit)
    app.error_dialog_status_var = tk.StringVar(value="")


def _init_worker_and_runtime_controllers(
    app,
    *,
    deps,
    queue_module,
    tk,
    setting,
    default_settings: dict,
    status_poll_default: float,
    ui_event_queue_maxsize: int,
    macro_search_dirs: tuple[str, ...],
) -> None:
    ui_event_queue_cls = getattr(deps, "UiEventQueue", None)
    if ui_event_queue_cls is not None:
        app.ui_q = ui_event_queue_cls(maxsize=ui_event_queue_maxsize)
    else:
        app.ui_q = queue_module.Queue()

    app.status_poll_interval = tk.DoubleVar(
        value=app.settings.get(
            "status_poll_interval",
            default_settings.get("status_poll_interval", status_poll_default),
        )
    )
    failure_limit = _clamp_int_setting(setting, "status_query_failure_limit", 3)
    if failure_limit < 1:
        failure_limit = 1
    if failure_limit > 10:
        failure_limit = 10
    app.status_query_failure_limit = tk.IntVar(value=failure_limit)

    app.grbl = deps.GrblWorker(app.ui_q)
    app.grbl.set_status_query_failure_limit(app.status_query_failure_limit.get())
    try:
        app._on_homing_watchdog_change()
    except Exception:
        pass

    app.macro_executor = deps.MacroExecutor(app, macro_search_dirs=macro_search_dirs)
    app.probe_controller = deps.ProbeController(app)
    app.auto_level_runner = deps.AutoLevelProbeRunner(app)
    app.streaming_controller = deps.StreamingController(app)
    app.macro_panel = deps.MacroPanel(app)
    app.toolpath_panel = deps.ToolpathPanel(app)
    app.settings_controller = deps.GRBLSettingsController(app)
    app._install_dialog_loggers()
    app.report_callback_exception = app._tk_report_callback_exception
    app._apply_status_poll_profile()


def _init_units_and_estimation_state(
    app,
    *,
    tk,
    default_settings: dict,
    default_jog_feed_xy: float,
    default_jog_feed_z: float,
) -> None:
    app.unit_mode = tk.StringVar(value=app.settings.get("unit_mode", default_settings.get("unit_mode", "mm")))
    app._modal_units = app.unit_mode.get()
    app._report_units = None
    app.estimate_rate_x_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_x", default_settings.get("estimate_rate_x", "")))
    )
    app.estimate_rate_y_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_y", default_settings.get("estimate_rate_y", "")))
    )
    app.estimate_rate_z_var = tk.StringVar(
        value=str(app.settings.get("estimate_rate_z", default_settings.get("estimate_rate_z", "")))
    )
    app.step_xy = tk.DoubleVar(value=app.settings.get("step_xy", default_settings.get("step_xy", 1.0)))
    app.step_z = tk.DoubleVar(value=app.settings.get("step_z", default_settings.get("step_z", 1.0)))
    app.jog_feed_xy = tk.DoubleVar(value=default_jog_feed_xy)
    app.jog_feed_z = tk.DoubleVar(value=default_jog_feed_z)


def _init_machine_position_state(app, *, tk, default_settings: dict) -> None:
    app.connected = False
    app.current_port = tk.StringVar(value=app.settings.get("last_port", default_settings.get("last_port", "")))
    app.tool_reference_var = tk.StringVar(value="")
    app._tool_reference_last = None

    app.machine_state = tk.StringVar(value="DISCONNECTED")
    app.wpos_x = tk.StringVar(value="0.000")
    app.wpos_y = tk.StringVar(value="0.000")
    app.wpos_z = tk.StringVar(value="0.000")
    app.mpos_x = tk.StringVar(value="0.000")
    app.mpos_y = tk.StringVar(value="0.000")
    app.mpos_z = tk.StringVar(value="0.000")
    app._wpos_raw = (0.0, 0.0, 0.0)
    app._mpos_raw = (0.0, 0.0, 0.0)
    app._wco_raw = None
    app._planner_blocks_available = 15
    app._planner_blocks_capacity = 15
    app._wpos_value_labels = {}
    app._wpos_label_default_fg = {}
    app._wpos_flash_after_ids = {}


def _init_gcode_and_autolevel_state(app, *, copy_module, tk, default_settings: dict) -> None:
    app._last_gcode_lines = []
    app._gcode_source = None
    app._gcode_streaming_mode = False
    app._gcode_total_lines = 0
    app._last_gcode_path = None
    app._gcode_hash = None
    app._gcode_validation_report = None
    app._last_parse_result = None
    app._last_parse_hash = None

    app._auto_level_grid = None
    app._auto_level_height_map = None
    app._auto_level_bounds = None
    app._auto_level_original_lines = None
    app._auto_level_original_path = None
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None
    app._auto_level_restore = None
    app.auto_level_settings = dict(
        app.settings.get("auto_level_settings", default_settings.get("auto_level_settings", {}))
        or {}
    )

    raw_job_prefs = app.settings.get(
        "auto_level_job_prefs",
        default_settings.get("auto_level_job_prefs", {}),
    )
    if isinstance(raw_job_prefs, dict):
        app.auto_level_job_prefs = copy_module.deepcopy(raw_job_prefs)
    else:
        app.auto_level_job_prefs = copy_module.deepcopy(default_settings.get("auto_level_job_prefs", {}))

    raw_presets = app.settings.get("auto_level_presets", {})
    app.auto_level_presets = dict(raw_presets) if isinstance(raw_presets, dict) else {}

    app._gcode_loading = False
    app._gcode_load_token = 0
    app._gcode_parse_token = 0
    app.gcode_stats_var = tk.StringVar(value="No file loaded")
    app.gcode_load_var = tk.StringVar(value="")
    app._gcode_load_popup = None
    app._gcode_load_popup_label = None
    app._gcode_load_popup_bar = None


def _init_stream_and_override_state(app, *, tk, default_settings: dict) -> None:
    app._rapid_rates = None
    app._rapid_rates_source = None
    app.fallback_rapid_rate = tk.StringVar(
        value=app.settings.get("fallback_rapid_rate", default_settings.get("fallback_rapid_rate", ""))
    )
    app.estimate_factor = tk.DoubleVar(
        value=app.settings.get("estimate_factor", default_settings.get("estimate_factor", 1.0))
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
    app._stream_done_pending_idle = False
    app._resume_after_disconnect = False
    app._resume_from_index = None
    app._resume_job_name = None
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
    app.override_info_var = tk.StringVar(value="Overrides: Feed 100% | Spindle 100%")
    app._feed_override_slider_locked = False
    app._spindle_override_slider_locked = False
    app._feed_override_slider_last_position = 100
    app._spindle_override_slider_last_position = 100

    app._machine_state_text = "DISCONNECTED"
    app._grbl_setting_info = {}
    app._grbl_setting_keys = []
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1
    app._confirm_last_time = {}
    app._confirm_debounce_sec = 0.8


def _init_reconnect_and_ui_state(app, *, default_settings: dict) -> None:
    app._auto_reconnect_last_port = app.settings.get("last_port", default_settings.get("last_port", ""))
    app._auto_reconnect_last_attempt = 0.0
    app._auto_reconnect_pending = False
    app._auto_reconnect_retry = 0
    app._auto_reconnect_delay = 3.0
    app._auto_reconnect_max_retry = 5
    app._auto_reconnect_next_ts = 0.0
    app._auto_reconnect_blocked = False
    app._user_disconnect = False
    app._ui_throttle_ms = 100
    app._state_flash_after_id = None
    app._state_flash_color = None
    app._state_flash_on = False
    app._state_default_bg = None


def init_runtime_state(
    app,
    default_jog_feed_xy: float,
    default_jog_feed_z: float,
    macro_search_dirs: tuple[str, ...],
    module,
):
    deps = module
    copy_module = deps.copy
    queue_module = deps.queue
    tk = deps.tk
    default_settings = deps.DEFAULT_SETTINGS
    status_poll_default = deps.STATUS_POLL_DEFAULT
    ui_event_queue_maxsize = deps.UI_EVENT_QUEUE_MAXSIZE

    def setting(key: str, fallback):
        return app.settings.get(key, default_settings.get(key, fallback))

    _normalize_key_bindings(app)
    _normalize_joystick_bindings(app)
    _init_keyboard_runtime_state(app)
    _init_joystick_runtime_state(app, tk)
    _init_connection_runtime_state(app)
    _init_error_dialog_runtime_state(app, setting, tk)
    _init_worker_and_runtime_controllers(
        app,
        deps=deps,
        queue_module=queue_module,
        tk=tk,
        setting=setting,
        default_settings=default_settings,
        status_poll_default=status_poll_default,
        ui_event_queue_maxsize=ui_event_queue_maxsize,
        macro_search_dirs=macro_search_dirs,
    )
    _init_units_and_estimation_state(
        app,
        tk=tk,
        default_settings=default_settings,
        default_jog_feed_xy=default_jog_feed_xy,
        default_jog_feed_z=default_jog_feed_z,
    )
    _init_machine_position_state(app, tk=tk, default_settings=default_settings)
    _init_gcode_and_autolevel_state(
        app,
        copy_module=copy_module,
        tk=tk,
        default_settings=default_settings,
    )
    _init_stream_and_override_state(app, tk=tk, default_settings=default_settings)
    _init_reconnect_and_ui_state(app, default_settings=default_settings)
