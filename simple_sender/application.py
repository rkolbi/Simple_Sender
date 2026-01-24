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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import os
import sys
from typing import Any, TYPE_CHECKING

# GUI imports
import tkinter as tk

# Refactored module imports
from simple_sender import __version__
from simple_sender.application_actions import ActionsMixin
from simple_sender.application_controls import ControlsMixin
from simple_sender.application_gcode import GcodeMixin
from simple_sender.application_input_bindings import InputBindingsMixin
from simple_sender.application_layout import LayoutMixin
from simple_sender.application_lifecycle import LifecycleMixin
from simple_sender.application_status import StatusMixin
from simple_sender.application_state_ui import StateUiMixin
from simple_sender.application_toolpath import ToolpathMixin
from simple_sender.application_ui_events import UiEventsMixin
from simple_sender.application_ui_toggles import UiTogglesMixin
from simple_sender.ui.app_exports import (
    build_toolbar,
    update_job_button_mode,
    ensure_tooltips,
    build_led_panel,
    on_led_visibility_change,
    refresh_led_backgrounds,
    set_led_state,
    update_led_panel,
    update_led_visibility,
    show_alarm_recovery,
    show_auto_level_dialog,
    show_macro_prompt,
    show_resume_dialog,
    open_release_checklist,
    open_run_checklist,
    run_preflight_check,
    export_session_diagnostics,
    patch_messagebox,
    set_default_parent,
    ensure_gcode_loading_popup,
    finish_gcode_loading,
    hide_gcode_loading,
    set_gcode_loading_indeterminate,
    set_gcode_loading_progress,
    show_gcode_loading,
    bind_app_settings_mousewheel,
    on_app_settings_mousewheel,
    unbind_app_settings_mousewheel,
    update_app_settings_scrollregion,
    apply_state_fg,
    cancel_state_flash,
    start_state_flash,
    toggle_state_flash,
    update_state_highlight,
    on_quick_button_visibility_change,
    update_quick_button_visibility,
    refresh_autolevel_overlay_toggle_text,
    refresh_keybindings_toggle_text,
    refresh_render_3d_toggle_text,
    refresh_tooltips_toggle_text,
    apply_theme,
    refresh_stop_button_backgrounds,
    on_recover_button_visibility_change,
    on_resume_button_visibility_change,
    update_recover_button_visibility,
    update_resume_button_visibility,
    apply_toolpath_arc_detail,
    apply_toolpath_draw_limits,
    apply_toolpath_performance,
    apply_toolpath_streaming_render_interval,
    clamp_arc_detail,
    clamp_toolpath_performance,
    clamp_toolpath_streaming_render_interval,
    init_toolpath_settings,
    load_3d_view,
    on_arc_detail_scale_key_release,
    on_arc_detail_scale_move,
    on_toolpath_lightweight_change,
    on_toolpath_performance_key_release,
    on_toolpath_performance_move,
    run_toolpath_arc_detail_reparse,
    save_3d_view,
    schedule_toolpath_arc_detail_reparse,
    toggle_render_3d,
    toolpath_limit_value,
    toolpath_perf_values,
    apply_error_dialog_settings,
    install_dialog_loggers,
    on_error_dialogs_enabled_change,
    reset_error_dialog_state,
    set_error_dialog_status,
    should_show_error_dialog,
    toggle_error_dialogs,
    confirm_and_run,
    on_autolevel_overlay_change,
    on_gui_logging_change,
    on_theme_change,
    require_grbl_connection,
    run_if_connected,
    send_manual,
    start_homing,
    toggle_autolevel_overlay,
    toggle_console_pos_status,
    toggle_performance,
    toggle_tooltips,
    toggle_unit_mode,
    open_gcode,
    pause_job,
    refresh_ports,
    resume_job,
    run_job,
    start_connect_worker,
    start_disconnect_worker,
    stop_job,
    toggle_connect,
    init_basic_preferences,
    init_runtime_state,
    init_settings_store,
    log_exception,
    on_close,
    tk_report_callback_exception,
    apply_loaded_gcode,
    clear_gcode,
    load_gcode_from_path,
    reset_gcode_view_for_run,
    load_settings,
    save_settings,
    load_grbl_setting_info,
    format_alarm_message,
    set_alarm_lock,
    all_stop_action,
    all_stop_gcode_label,
    position_all_stop_offset,
    refresh_dro_display,
    dro_row,
    dro_value_row,
    apply_status_poll_profile,
    effective_status_poll_interval,
    handle_auto_reconnect_failure,
    maybe_auto_reconnect,
    handle_event,
    set_manual_controls_enabled,
    convert_estimate_rates,
    on_estimate_rates_change,
    update_estimate_rate_units_label,
    validate_estimate_rate_text,
    apply_gcode_stats,
    estimate_factor_value,
    format_gcode_stats_text,
    get_accel_rates_for_estimate,
    get_fallback_rapid_rate,
    get_rapid_rates_for_estimate,
    make_stats_cache_key,
    on_estimate_factor_change,
    refresh_gcode_stats_display,
    update_gcode_stats,
    update_live_estimate,
    handle_override_slider_change,
    normalize_override_slider_value,
    on_feed_override_slider,
    on_spindle_override_slider,
    refresh_override_info,
    send_override_delta,
    set_feed_override_slider_value,
    set_override_scale,
    set_spindle_override_slider_value,
    request_settings_dump,
    set_step_xy,
    set_step_z,
    set_unit_mode,
    unit_toggle_label,
    update_unit_toggle_display,
    apply_safe_mode_profile,
    on_jog_feed_change_xy,
    on_jog_feed_change_z,
    validate_jog_feed_var,
    build_main_layout,
    build_resume_preamble,
    resume_from_line,
    ensure_serial_available,
    on_all_stop_mode_change,
    on_current_line_mode_change,
    sync_all_stop_mode_combo,
    sync_current_line_mode_combo,
    update_current_highlight,
    on_status_failure_limit_change,
    on_status_interval_change,
    on_homing_watchdog_change,
    format_throughput,
    maybe_notify_job_completion,
    set_streaming_lock,
    call_on_ui_thread,
    post_ui_thread,
    drain_ui_queue,
    goto_zero,
    on_zeroing_mode_change,
    refresh_zeroing_ui,
    zero_all,
    zero_x,
    zero_y,
    zero_z,
    clear_console_log,
    save_console_log,
    send_console,
    setup_console_tags,
    on_tab_changed,
    update_tab_visibility,
    apply_tooltip,
    attach_log_gcode,
)

if TYPE_CHECKING:
    from simple_sender.macro_executor import MacroExecutor
    from simple_sender.gcode_source import FileGcodeSource

SERIAL_IMPORT_ERROR = ""


def _resolve_script_file() -> str:
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.isfile(argv0):
        return os.path.abspath(argv0)
    if __file__:
        return os.path.abspath(__file__)
    return os.path.abspath(argv0)


_SCRIPT_FILE = _resolve_script_file()
_SCRIPT_DIR = os.path.dirname(_SCRIPT_FILE)


# Serial imports
try:
    import serial
    from serial.tools import list_ports
    SERIAL_AVAILABLE = True
except ImportError as exc:
    serial = None
    list_ports = None
    SERIAL_AVAILABLE = False
    SERIAL_IMPORT_ERROR = str(exc)

# Utility functions
def _discover_macro_dirs() -> tuple[str, ...]:
    dirs: list[str] = []
    pkg = sys.modules.get("simple_sender")
    pkg_file = getattr(pkg, "__file__", None) if pkg else None
    if pkg_file:
        pkg_dir = os.path.dirname(pkg_file)
        macros_dir = os.path.join(pkg_dir, "macros")
        if os.path.isdir(macros_dir):
            dirs.append(macros_dir)
    script_dir = _SCRIPT_DIR
    root_macros = os.path.join(script_dir, "macros")
    if os.path.isdir(root_macros) and root_macros not in dirs:
        dirs.append(root_macros)
    if script_dir not in dirs:
        dirs.append(script_dir)
    return tuple(dirs)

_MACRO_SEARCH_DIRS = _discover_macro_dirs()


class App(
    GcodeMixin,
    ControlsMixin,
    LayoutMixin,
    ActionsMixin,
    StatusMixin,
    UiEventsMixin,
    LifecycleMixin,
    InputBindingsMixin,
    ToolpathMixin,
    UiTogglesMixin,
    StateUiMixin,
    tk.Tk,
):
    HIDDEN_MPOS_BUTTON_STYLE = "SimpleSender.HiddenMpos.TButton"
    # Type hints for attributes initialized in helper modules.
    connected: bool
    macro_executor: "MacroExecutor"
    settings: dict[str, Any]
    reconnect_on_open: tk.BooleanVar
    stop_hold_on_focus_loss: tk.BooleanVar
    validate_streaming_gcode: tk.BooleanVar
    streaming_controller: Any
    tool_reference_var: tk.StringVar
    machine_state: tk.StringVar
    _machine_state_text: str
    _last_gcode_lines: list[str]
    _last_parse_result: Any
    _last_parse_hash: str | None
    _gcode_parse_token: int
    _gcode_source: "FileGcodeSource | None"
    _gcode_streaming_mode: bool
    _gcode_total_lines: int
    _resume_after_disconnect: bool
    _resume_from_index: int | None
    _resume_job_name: str | None
    def __init__(self):
        super().__init__()
        self.title("Simple Sender")
        self.minsize(980, 620)
        self.attributes("-fullscreen", True)
        self.bind("<Escape>", lambda _evt: self.attributes("-fullscreen", False))
        default_jog_feed_xy, default_jog_feed_z = init_settings_store(self, _SCRIPT_DIR)
        init_basic_preferences(self, __version__)
        self._apply_ui_scale(self.settings.get("ui_scale", 1.5))
        init_toolpath_settings(self)
        init_runtime_state(self, default_jog_feed_xy, default_jog_feed_z, _MACRO_SEARCH_DIRS)
        set_default_parent(self)
        patch_messagebox()

        # Top + main layout
        self._build_toolbar()
        self._build_main()
        self._set_manual_controls_enabled(False)

        self.after(50, self._drain_ui_queue)
        self.after(0, self._restore_joystick_bindings_on_start)
        self.bind_all("<FocusOut>", self._on_app_focus_out)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_ports(auto_connect=bool(self.reconnect_on_open.get()))
        if not self.connected and bool(self.reconnect_on_open.get()):
            last_port = (self.settings.get("last_port") or "").strip()
            if last_port:
                self._auto_reconnect_last_port = last_port
                self._auto_reconnect_pending = True
        geometry = self.settings.get("window_geometry", "")
        if isinstance(geometry, str) and geometry:
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass
        self._load_grbl_setting_info()
        self.streaming_controller.bind_button_logging()
        self._virtual_hold_buttons = self._create_virtual_hold_buttons()
        self._apply_keyboard_bindings()
