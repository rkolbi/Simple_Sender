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


def init_runtime_state(
    app,
    default_jog_feed_xy: float,
    default_jog_feed_z: float,
    macro_search_dirs: tuple[str, ...],
    module,
):
    deps = module
    copy = deps.copy
    queue = deps.queue
    tk = deps.tk
    ttk = deps.ttk
    DEFAULT_SETTINGS = deps.DEFAULT_SETTINGS
    STATUS_POLL_DEFAULT = deps.STATUS_POLL_DEFAULT
    UI_EVENT_QUEUE_MAXSIZE = deps.UI_EVENT_QUEUE_MAXSIZE
    GrblWorker = deps.GrblWorker
    MacroExecutor = deps.MacroExecutor
    StreamingController = deps.StreamingController
    MacroPanel = deps.MacroPanel
    ToolpathPanel = deps.ToolpathPanel
    GRBLSettingsController = deps.GRBLSettingsController
    ProbeController = deps.ProbeController
    AutoLevelProbeRunner = deps.AutoLevelProbeRunner

    def setting(key: str, fallback):
        return app.settings.get(key, DEFAULT_SETTINGS.get(key, fallback))

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
    app._joystick_bindings = normalized_joystick_bindings
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
    app._closing = False
    app._connecting = False
    app._disconnecting = False
    app._connect_thread = None
    app._disconnect_thread = None
    app._error_dialog_last_ts = 0.0
    app._error_dialog_window_start = 0.0
    app._error_dialog_count = 0
    app._error_dialog_suppressed = False
    app._pending_force_g90 = False
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
    UiEventQueue = getattr(deps, "UiEventQueue", None)
    if UiEventQueue is not None:
        app.ui_q = UiEventQueue(maxsize=UI_EVENT_QUEUE_MAXSIZE)
    else:
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
    try:
        app._on_homing_watchdog_change()
    except Exception:
        pass
    app.macro_executor = MacroExecutor(app, macro_search_dirs=macro_search_dirs)
    app.probe_controller = ProbeController(app)
    app.auto_level_runner = AutoLevelProbeRunner(app)
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
    app.tool_reference_var = tk.StringVar(value="")
    app._tool_reference_last = None

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
    app._wco_raw = None
    app._wpos_value_labels = {}
    app._wpos_label_default_fg = {}
    app._wpos_flash_after_ids = {}
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
        app.settings.get("auto_level_settings", DEFAULT_SETTINGS.get("auto_level_settings", {}))
        or {}
    )
    raw_job_prefs = app.settings.get(
        "auto_level_job_prefs",
        DEFAULT_SETTINGS.get("auto_level_job_prefs", {}),
    )
    if isinstance(raw_job_prefs, dict):
        app.auto_level_job_prefs = copy.deepcopy(raw_job_prefs)
    else:
        app.auto_level_job_prefs = copy.deepcopy(
            DEFAULT_SETTINGS.get("auto_level_job_prefs", {})
        )
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
