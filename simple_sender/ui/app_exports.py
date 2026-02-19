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

"""Aggregated UI exports for Application."""

from simple_sender.ui.controls.toolbar import build_toolbar, on_recover_button_visibility_change, on_resume_button_visibility_change, update_job_button_mode, update_recover_button_visibility, update_resume_button_visibility
from simple_sender.ui.widgets import ensure_tooltips, StopSignButton, VirtualHoldButton, apply_tooltip, attach_log_gcode
from simple_sender.ui.led_panel import build_led_panel, on_led_visibility_change, refresh_led_backgrounds, set_led_state, update_led_panel, update_led_visibility
from simple_sender.ui.dialogs import show_alarm_recovery, show_auto_level_dialog, show_macro_prompt, show_resume_dialog
from simple_sender.ui.dialogs.logs import show_logs_dialog
from simple_sender.ui.dialogs.diagnostics import export_session_diagnostics, open_release_checklist, open_run_checklist, run_preflight_check
from simple_sender.ui.dialogs.backup_bundle import export_backup_bundle, import_backup_bundle
from simple_sender.ui.dialogs.macro_manager import show_macro_manager
from simple_sender.ui.dialogs.popup_utils import patch_messagebox, set_default_parent
from simple_sender.ui.gcode.loading import ensure_gcode_loading_popup, finish_gcode_loading, hide_gcode_loading, set_gcode_loading_indeterminate, set_gcode_loading_progress, show_gcode_loading
from simple_sender.ui.settings import bind_app_settings_mousewheel, bind_app_settings_touch_scroll, on_app_settings_mousewheel, on_app_settings_touch_end, on_app_settings_touch_move, on_app_settings_touch_start, unbind_app_settings_mousewheel, unbind_app_settings_touch_scroll, update_app_settings_scrollregion
from simple_sender.ui.status.state_flash import apply_state_fg, cancel_state_flash, ensure_state_label_width, start_state_flash, toggle_state_flash, update_state_highlight
from simple_sender.ui.status.bar import on_quick_button_visibility_change, update_quick_button_visibility
from simple_sender.ui.toggle_text import refresh_autolevel_overlay_toggle_text, refresh_keybindings_toggle_text, refresh_render_3d_toggle_text, refresh_tooltips_toggle_text
from simple_sender.ui.theme_helpers import apply_theme, refresh_stop_button_backgrounds
from simple_sender.ui.toolpath.toolpath_settings import apply_toolpath_arc_detail, apply_toolpath_draw_limits, apply_toolpath_performance, apply_toolpath_streaming_render_interval, clamp_arc_detail, clamp_toolpath_performance, clamp_toolpath_streaming_render_interval, init_toolpath_settings, load_3d_view, on_arc_detail_scale_key_release, on_arc_detail_scale_move, on_toolpath_lightweight_change, on_toolpath_performance_key_release, on_toolpath_performance_move, run_toolpath_arc_detail_reparse, save_3d_view, schedule_toolpath_arc_detail_reparse, toggle_render_3d, toolpath_limit_value, toolpath_perf_values
from simple_sender.ui.dialogs.error_dialogs_ui import apply_error_dialog_settings, install_dialog_loggers, on_error_dialogs_enabled_change, reset_error_dialog_state, set_error_dialog_status, should_show_error_dialog, toggle_error_dialogs
from simple_sender.ui.ui_actions import apply_scrollbar_width, apply_ui_scale, confirm_and_run, on_autolevel_overlay_change, on_gui_logging_change, on_performance_mode_change, on_scrollbar_width_change, on_theme_change, on_ui_scale_change, require_grbl_connection, run_if_connected, send_manual, start_homing, toggle_autolevel_overlay, toggle_console_pos_status, toggle_performance, toggle_tooltips, toggle_unit_mode
from simple_sender.ui.app_commands import open_gcode, pause_job, refresh_ports, resume_job, run_job, start_connect_worker, start_disconnect_worker, stop_job, toggle_connect
from simple_sender.ui.app_init import init_basic_preferences, init_runtime_state, init_settings_store
from simple_sender.ui.app_lifecycle import log_exception, on_close, tk_report_callback_exception
from simple_sender.ui.gcode.pipeline import apply_loaded_gcode, clear_gcode, load_gcode_from_path
from simple_sender.ui.viewer.gcode_viewer import reset_gcode_view_for_run
from simple_sender.ui.settings_persistence import load_settings, save_settings
from simple_sender.ui.grbl_settings.info import load_grbl_setting_info
from simple_sender.ui.alarm_state import format_alarm_message, set_alarm_lock
from simple_sender.ui.all_stop import all_stop_action, all_stop_gcode_label, position_all_stop_offset
from simple_sender.ui.dro import refresh_dro_display, dro_row, dro_value_row
from simple_sender.ui.grbl_lifecycle import apply_status_poll_profile, effective_status_poll_interval, handle_auto_reconnect_failure, maybe_auto_reconnect
from simple_sender.ui.events import handle_event
from simple_sender.ui.manual_controls import set_manual_controls_enabled
from simple_sender.ui.profiles.estimate_rates import convert_estimate_rates, on_estimate_rates_change, update_estimate_rate_units_label, validate_estimate_rate_text
from simple_sender.ui.gcode.stats import apply_gcode_stats, estimate_factor_value, format_gcode_stats_text, get_accel_rates_for_estimate, get_fallback_rapid_rate, get_rapid_rates_for_estimate, make_stats_cache_key, on_estimate_factor_change, refresh_gcode_stats_display, update_gcode_stats, update_live_estimate
from simple_sender.ui.override_controls import handle_override_slider_change, normalize_override_slider_value, on_feed_override_slider, on_spindle_override_slider, refresh_override_info, send_override_delta, set_feed_override_slider_value, set_override_scale, set_spindle_override_slider_value
from simple_sender.ui.grbl_settings.requests import request_settings_dump
from simple_sender.ui.controls.jog_controls import apply_safe_mode_profile, on_jog_feed_change_xy, on_jog_feed_change_z, set_step_xy, set_step_z, set_unit_mode, unit_toggle_label, update_unit_toggle_display, validate_jog_feed_var
from simple_sender.ui.main_layout import build_main_layout
from simple_sender.ui.dialogs.resume_from import build_resume_preamble, resume_from_line
from simple_sender.ui.app_commands import ensure_serial_available
from simple_sender.ui.status.state_display import on_all_stop_mode_change, on_current_line_mode_change, sync_all_stop_mode_combo, sync_current_line_mode_combo, update_current_highlight
from simple_sender.ui.status.polling import on_homing_watchdog_change, on_status_failure_limit_change, on_status_interval_change
from simple_sender.ui.dialogs.streaming_metrics import format_throughput, maybe_notify_job_completion
from simple_sender.ui.events import set_streaming_lock
from simple_sender.ui.threading_utils import call_on_ui_thread, post_ui_thread
from simple_sender.ui.ui_queue import drain_ui_queue
from simple_sender.ui.zeroing_actions import goto_zero, on_zeroing_mode_change, refresh_zeroing_ui, zero_all, zero_x, zero_y, zero_z
from simple_sender.ui.console import clear_console_log, save_console_log, send_console, setup_console_tags
from simple_sender.ui import bindings as input_bindings
from simple_sender.ui.main_tabs import on_tab_changed, update_tab_visibility

__all__ = [
    'build_toolbar',
    'update_job_button_mode',
    'ensure_tooltips',
    'build_led_panel',
    'on_led_visibility_change',
    'refresh_led_backgrounds',
    'set_led_state',
    'update_led_panel',
    'update_led_visibility',
    'show_alarm_recovery',
    'show_auto_level_dialog',
    'show_macro_prompt',
    'show_resume_dialog',
    'show_logs_dialog',
    'open_release_checklist',
    'open_run_checklist',
    'run_preflight_check',
    'export_session_diagnostics',
    'export_backup_bundle',
    'import_backup_bundle',
    'show_macro_manager',
    'patch_messagebox',
    'set_default_parent',
    'ensure_gcode_loading_popup',
    'finish_gcode_loading',
    'hide_gcode_loading',
    'set_gcode_loading_indeterminate',
    'set_gcode_loading_progress',
    'show_gcode_loading',
    'bind_app_settings_mousewheel',
    'bind_app_settings_touch_scroll',
    'on_app_settings_mousewheel',
    'on_app_settings_touch_start',
    'on_app_settings_touch_move',
    'on_app_settings_touch_end',
    'unbind_app_settings_mousewheel',
    'unbind_app_settings_touch_scroll',
    'update_app_settings_scrollregion',
    'apply_state_fg',
    'cancel_state_flash',
    'ensure_state_label_width',
    'start_state_flash',
    'toggle_state_flash',
    'update_state_highlight',
    'on_quick_button_visibility_change',
    'update_quick_button_visibility',
    'refresh_autolevel_overlay_toggle_text',
    'refresh_keybindings_toggle_text',
    'refresh_render_3d_toggle_text',
    'refresh_tooltips_toggle_text',
    'apply_theme',
    'refresh_stop_button_backgrounds',
    'on_recover_button_visibility_change',
    'on_resume_button_visibility_change',
    'update_recover_button_visibility',
    'update_resume_button_visibility',
    'apply_toolpath_arc_detail',
    'apply_toolpath_draw_limits',
    'apply_toolpath_performance',
    'apply_toolpath_streaming_render_interval',
    'clamp_arc_detail',
    'clamp_toolpath_performance',
    'clamp_toolpath_streaming_render_interval',
    'init_toolpath_settings',
    'load_3d_view',
    'on_arc_detail_scale_key_release',
    'on_arc_detail_scale_move',
    'on_toolpath_lightweight_change',
    'on_toolpath_performance_key_release',
    'on_toolpath_performance_move',
    'run_toolpath_arc_detail_reparse',
    'save_3d_view',
    'schedule_toolpath_arc_detail_reparse',
    'toggle_render_3d',
    'toolpath_limit_value',
    'toolpath_perf_values',
    'apply_error_dialog_settings',
    'install_dialog_loggers',
    'on_error_dialogs_enabled_change',
    'reset_error_dialog_state',
    'set_error_dialog_status',
    'should_show_error_dialog',
    'toggle_error_dialogs',
    'confirm_and_run',
    'on_autolevel_overlay_change',
    'on_gui_logging_change',
    'on_performance_mode_change',
    'on_theme_change',
    'apply_scrollbar_width',
    'apply_ui_scale',
    'on_scrollbar_width_change',
    'on_ui_scale_change',
    'require_grbl_connection',
    'run_if_connected',
    'send_manual',
    'start_homing',
    'toggle_autolevel_overlay',
    'toggle_console_pos_status',
    'toggle_performance',
    'toggle_tooltips',
    'toggle_unit_mode',
    'open_gcode',
    'pause_job',
    'refresh_ports',
    'resume_job',
    'run_job',
    'start_connect_worker',
    'start_disconnect_worker',
    'stop_job',
    'toggle_connect',
    'init_basic_preferences',
    'init_runtime_state',
    'init_settings_store',
    'log_exception',
    'on_close',
    'tk_report_callback_exception',
    'apply_loaded_gcode',
    'clear_gcode',
    'load_gcode_from_path',
    'reset_gcode_view_for_run',
    'load_settings',
    'save_settings',
    'load_grbl_setting_info',
    'format_alarm_message',
    'set_alarm_lock',
    'all_stop_action',
    'all_stop_gcode_label',
    'position_all_stop_offset',
    'refresh_dro_display',
    'dro_row',
    'dro_value_row',
    'apply_status_poll_profile',
    'effective_status_poll_interval',
    'handle_auto_reconnect_failure',
    'maybe_auto_reconnect',
    'handle_event',
    'set_manual_controls_enabled',
    'convert_estimate_rates',
    'on_estimate_rates_change',
    'update_estimate_rate_units_label',
    'validate_estimate_rate_text',
    'apply_gcode_stats',
    'estimate_factor_value',
    'format_gcode_stats_text',
    'get_accel_rates_for_estimate',
    'get_fallback_rapid_rate',
    'get_rapid_rates_for_estimate',
    'make_stats_cache_key',
    'on_estimate_factor_change',
    'refresh_gcode_stats_display',
    'update_gcode_stats',
    'update_live_estimate',
    'handle_override_slider_change',
    'normalize_override_slider_value',
    'on_feed_override_slider',
    'on_spindle_override_slider',
    'refresh_override_info',
    'send_override_delta',
    'set_feed_override_slider_value',
    'set_override_scale',
    'set_spindle_override_slider_value',
    'request_settings_dump',
    'set_step_xy',
    'set_step_z',
    'apply_safe_mode_profile',
    'set_unit_mode',
    'unit_toggle_label',
    'update_unit_toggle_display',
    'on_jog_feed_change_xy',
    'on_jog_feed_change_z',
    'validate_jog_feed_var',
    'build_main_layout',
    'build_resume_preamble',
    'resume_from_line',
    'ensure_serial_available',
    'on_all_stop_mode_change',
    'on_current_line_mode_change',
    'sync_all_stop_mode_combo',
    'sync_current_line_mode_combo',
    'update_current_highlight',
    'on_status_failure_limit_change',
    'on_status_interval_change',
    'on_homing_watchdog_change',
    'format_throughput',
    'maybe_notify_job_completion',
    'set_streaming_lock',
    'call_on_ui_thread',
    'post_ui_thread',
    'drain_ui_queue',
    'goto_zero',
    'on_zeroing_mode_change',
    'refresh_zeroing_ui',
    'zero_all',
    'zero_x',
    'zero_y',
    'zero_z',
    'clear_console_log',
    'save_console_log',
    'send_console',
    'setup_console_tags',
    'input_bindings',
    'on_tab_changed',
    'update_tab_visibility',
    'StopSignButton',
    'VirtualHoldButton',
    'apply_tooltip',
    'attach_log_gcode',
]
