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
# SPDX-License-Identifier: GPL-3.0-or-later
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import os
import queue
import sys
from types import ModuleType
from typing import Any, Callable, TYPE_CHECKING

# GUI imports
import tkinter as tk

# Refactored module imports
from simple_sender.utils.constants import *
from simple_sender.ui.toolbar import build_toolbar
from simple_sender.ui.led_panel import (
    build_led_panel,
    on_led_visibility_change,
    refresh_led_backgrounds,
    set_led_state,
    update_led_panel,
    update_led_visibility,
)
from simple_sender.ui.dialogs import show_alarm_recovery, show_macro_prompt, show_resume_dialog
from simple_sender.ui.diagnostics import open_release_checklist
from simple_sender.ui.popup_utils import patch_messagebox, set_default_parent
from simple_sender.ui.gcode_loading import ensure_gcode_loading_popup, finish_gcode_loading, hide_gcode_loading, set_gcode_loading_indeterminate, set_gcode_loading_progress, show_gcode_loading
from simple_sender.ui.app_settings_scroll import bind_app_settings_mousewheel, on_app_settings_mousewheel, unbind_app_settings_mousewheel, update_app_settings_scrollregion
from simple_sender.ui.state_flash import apply_state_fg, cancel_state_flash, start_state_flash, toggle_state_flash, update_state_highlight
from simple_sender.ui.status_bar import on_quick_button_visibility_change, update_quick_button_visibility
from simple_sender.ui.toggle_text import refresh_keybindings_toggle_text, refresh_render_3d_toggle_text, refresh_tooltips_toggle_text
from simple_sender.ui.theme_helpers import apply_theme, refresh_stop_button_backgrounds
from simple_sender.ui.toolbar_visibility import on_recover_button_visibility_change, on_resume_button_visibility_change, update_recover_button_visibility, update_resume_button_visibility
from simple_sender.ui.toolpath_settings import apply_toolpath_arc_detail, apply_toolpath_draw_limits, apply_toolpath_performance, apply_toolpath_streaming_render_interval, clamp_arc_detail, clamp_toolpath_performance, clamp_toolpath_streaming_render_interval, init_toolpath_settings, load_3d_view, on_arc_detail_scale_key_release, on_arc_detail_scale_move, on_toolpath_lightweight_change, on_toolpath_performance_key_release, on_toolpath_performance_move, run_toolpath_arc_detail_reparse, save_3d_view, schedule_toolpath_arc_detail_reparse, toggle_render_3d, toolpath_limit_value, toolpath_perf_values
from simple_sender.ui.error_dialogs_ui import apply_error_dialog_settings, install_dialog_loggers, on_error_dialogs_enabled_change, reset_error_dialog_state, set_error_dialog_status, should_show_error_dialog, toggle_error_dialogs
from simple_sender.ui.ui_actions import confirm_and_run, on_gui_logging_change, on_theme_change, require_grbl_connection, run_if_connected, send_manual, start_homing, toggle_console_pos_status, toggle_performance, toggle_tooltips, toggle_unit_mode
from simple_sender.ui.app_commands import (
    open_gcode,
    pause_job,
    refresh_ports,
    resume_job,
    run_job,
    start_connect_worker,
    start_disconnect_worker,
    stop_job,
    toggle_connect,
)
from simple_sender.ui.app_init import init_basic_preferences, init_runtime_state, init_settings_store
from simple_sender.ui.app_lifecycle import log_exception, on_close, tk_report_callback_exception
from simple_sender.ui.gcode_pipeline import (
    apply_loaded_gcode,
    clear_gcode,
    load_gcode_from_path,
)
from simple_sender.ui.gcode_view import reset_gcode_view_for_run
from simple_sender.ui.settings_persistence import load_settings, save_settings
from simple_sender.ui.grbl_settings_info import load_grbl_setting_info
from simple_sender.ui.alarm_state import format_alarm_message, set_alarm_lock
from simple_sender.ui.all_stop_actions import all_stop_action, all_stop_gcode_label
from simple_sender.ui.all_stop_layout import position_all_stop_offset
from simple_sender.ui.dro_display import refresh_dro_display
from simple_sender.ui.dro_ui import dro_row, dro_value_row
from simple_sender.ui.grbl_lifecycle import (
    apply_status_poll_profile,
    effective_status_poll_interval,
    handle_auto_reconnect_failure,
    maybe_auto_reconnect,
)
from simple_sender.ui.event_router import handle_event
from simple_sender.ui.manual_controls import set_manual_controls_enabled
from simple_sender.ui.estimate_rates import (
    convert_estimate_rates,
    on_estimate_rates_change,
    update_estimate_rate_units_label,
    validate_estimate_rate_text,
)
from simple_sender.ui.gcode_stats import (
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
)
from simple_sender.ui.override_controls import (
    handle_override_slider_change,
    normalize_override_slider_value,
    on_feed_override_slider,
    on_spindle_override_slider,
    refresh_override_info,
    send_override_delta,
    set_feed_override_slider_value,
    set_override_scale,
    set_spindle_override_slider_value,
)

if TYPE_CHECKING:
    from simple_sender.macro_executor import MacroExecutor
from simple_sender.ui.grbl_settings_requests import request_settings_dump
from simple_sender.ui.jog_control_state import (
    set_step_xy,
    set_step_z,
    set_unit_mode,
    unit_toggle_label,
    update_unit_toggle_display,
)
from simple_sender.ui.jog_feed_settings import on_jog_feed_change_xy, on_jog_feed_change_z, validate_jog_feed_var
from simple_sender.ui.main_layout import build_main_layout
from simple_sender.ui.resume_from import build_resume_preamble, resume_from_line
from simple_sender.ui.serial_checks import ensure_serial_available
from simple_sender.ui.state_display import (
    on_all_stop_mode_change,
    on_current_line_mode_change,
    sync_all_stop_mode_combo,
    sync_current_line_mode_combo,
    update_current_highlight,
)
from simple_sender.ui.status_polling import on_status_failure_limit_change, on_status_interval_change
from simple_sender.ui.streaming_metrics import format_throughput, maybe_notify_job_completion
from simple_sender.ui.streaming_lock import set_streaming_lock
from simple_sender.ui.threading_utils import call_on_ui_thread, post_ui_thread
from simple_sender.ui.ui_queue import drain_ui_queue
from simple_sender.ui.zeroing_actions import (
    goto_zero,
    on_zeroing_mode_change,
    refresh_zeroing_ui,
    zero_all,
    zero_x,
    zero_y,
    zero_z,
)
from simple_sender.ui.console_utils import clear_console_log, save_console_log, send_console, setup_console_tags
from simple_sender.ui.input_bindings import (
    toggle_keyboard_bindings,
    toggle_joystick_bindings,
    on_keyboard_bindings_check,
    refresh_joystick_toggle_text,
    refresh_joystick_safety_display,
    update_joystick_polling_state,
    restore_joystick_bindings_on_start,
    get_pygame_module,
    discover_joysticks,
    refresh_joystick_test_info,
    ensure_joystick_backend,
    start_joystick_polling,
    stop_joystick_polling,
    ensure_joystick_polling_running,
    poll_joystick_events,
    describe_joystick_event,
    set_joystick_event_status,
    handle_joystick_event,
    is_virtual_hold_button,
    handle_joystick_button_release,
    start_joystick_hold,
    send_hold_jog,
    stop_joystick_hold,
    clear_duplicate_joystick_binding,
    start_joystick_safety_capture,
    cancel_joystick_safety_capture,
    clear_joystick_safety_binding,
    on_joystick_safety_toggle,
    poll_joystick_states_from_hardware,
    reset_joystick_axis_state,
    reset_joystick_hat_state,
    apply_keyboard_bindings,
    refresh_keyboard_table,
    create_virtual_hold_buttons,
    collect_buttons,
    button_label,
    keyboard_key_for_button,
    joystick_binding_display,
    joystick_binding_key,
    button_axis_name,
    button_binding_id,
    find_binding_conflict,
    default_key_for_button,
    on_kb_table_double_click,
    on_kb_table_click,
    start_kb_edit,
    start_joystick_capture,
    cancel_joystick_capture,
    joystick_binding_from_event,
    kb_capture_key,
    commit_kb_edit,
    normalize_key_label,
    normalize_key_chord,
    key_sequence_tuple,
    update_modifier_state,
    modifier_active,
    event_to_binding_label,
    on_key_modifier_release,
    sequence_conflict_pair,
    sequence_conflict,
    on_key_sequence,
    clear_key_sequence_buffer,
    keyboard_binding_allowed,
    on_key_jog_stop,
    on_key_all_stop,
    on_key_binding,
    invoke_button,
    log_button_action,
)
from simple_sender.ui.tab_events import on_tab_changed, update_tab_visibility
from simple_sender.ui.widgets import (
    StopSignButton,
    VirtualHoldButton,
    apply_tooltip,
    attach_log_gcode,
)

APP_VERSION = "1.2"

SERIAL_IMPORT_ERROR = ""

_SCRIPT_FILE = __file__ if __file__ is not None else os.path.abspath(sys.argv[0])
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

class App(tk.Tk):
    HIDDEN_MPOS_BUTTON_STYLE = "SimpleSender.HiddenMpos.TButton"
    # Type hints for attributes initialized in helper modules.
    connected: bool
    macro_executor: "MacroExecutor"
    settings: dict[str, Any]
    reconnect_on_open: tk.BooleanVar
    streaming_controller: Any
    tool_reference_var: tk.StringVar
    machine_state: tk.StringVar
    _machine_state_text: str
    _last_gcode_lines: list[str]
    _last_parse_result: Any
    _last_parse_hash: str | None
    _gcode_parse_token: int
    def __init__(self):
        super().__init__()
        self.title("Simple Sender")
        self.minsize(980, 620)
        default_jog_feed_xy, default_jog_feed_z = init_settings_store(self, _SCRIPT_DIR)
        init_basic_preferences(self, APP_VERSION)
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
        self.bind("<FocusOut>", self._on_app_focus_out)
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

    def _unit_toggle_label(self, mode: str | None = None) -> str:
        return unit_toggle_label(self, mode)

    # ---------- UI ----------
    def _build_toolbar(self):
        build_toolbar(self)

    def _build_main(self):
        build_main_layout(self)

    def _on_app_focus_out(self, event=None):
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_active_joystick_hold_binding", None):
            self._stop_joystick_hold()

    def _show_release_checklist(self):
        open_release_checklist(self)

    def _position_all_stop_offset(self, event=None):
        position_all_stop_offset(self, event)

    def _normalize_override_slider_value(self, raw_value, minimum=50, maximum=150):
        return normalize_override_slider_value(raw_value, minimum=minimum, maximum=maximum)

    def _set_override_scale(self, scale_attr, value, lock_attr):
        set_override_scale(self, scale_attr, value, lock_attr)

    def _handle_override_slider_change(
        self,
        raw_value,
        last_attr,
        scale_attr,
        lock_attr,
        display_var,
        plus_cmd,
        minus_cmd,
    ):
        handle_override_slider_change(
            self,
            raw_value,
            last_attr,
            scale_attr,
            lock_attr,
            display_var,
            plus_cmd,
            minus_cmd,
        )

    def _on_feed_override_slider(self, raw_value):
        on_feed_override_slider(self, raw_value)

    def _on_spindle_override_slider(self, raw_value):
        on_spindle_override_slider(self, raw_value)

    def _send_override_delta(self, delta, plus_cmd, minus_cmd):
        send_override_delta(self, delta, plus_cmd, minus_cmd)

    def _set_feed_override_slider_value(self, value):
        set_feed_override_slider_value(self, value)

    def _set_spindle_override_slider_value(self, value):
        set_spindle_override_slider_value(self, value)

    def _refresh_override_info(self):
        refresh_override_info(self)

    def _dro_value_row(self, parent, axis, var):
        dro_value_row(self, parent, axis, var)

    def _dro_row(self, parent, axis, var, zero_cmd):
        return dro_row(self, parent, axis, var, zero_cmd)

    # ---------- UI actions ----------
    def refresh_ports(self, auto_connect: bool = False):
        refresh_ports(self, auto_connect)

    def toggle_connect(self):
        toggle_connect(self)

    def _start_connect_worker(
        self,
        port: str,
        *,
        show_error: bool = True,
        on_failure: Callable[[Exception], Any] | None = None,
    ):
        start_connect_worker(self, port, show_error=show_error, on_failure=on_failure)

    def _start_disconnect_worker(self):
        start_disconnect_worker(self)

    def _ensure_serial_available(self) -> bool:
        return ensure_serial_available(self, serial is not None, SERIAL_IMPORT_ERROR)

    def open_gcode(self):
        open_gcode(self)

    def run_job(self):
        run_job(self)

    def pause_job(self):
        pause_job(self)

    def resume_job(self):
        resume_job(self)

    def stop_job(self):
        stop_job(self)

    def _show_resume_dialog(self):
        show_resume_dialog(self)

    def _build_resume_preamble(self, lines: list[str], stop_index: int) -> tuple[list[str], bool]:
        return build_resume_preamble(lines, stop_index)

    def _resume_from_line(self, start_index: int, preamble: list[str]):
        resume_from_line(self, start_index, preamble)

    def _reset_gcode_view_for_run(self):
        reset_gcode_view_for_run(self)

    def _load_gcode_from_path(self, path: str):
        load_gcode_from_path(self, path)

    def _apply_loaded_gcode(
        self,
        path: str,
        lines: list[str],
        *,
        lines_hash: str | None = None,
        validated: bool = False,
    ):
        apply_loaded_gcode(self, path, lines, lines_hash=lines_hash, validated=validated)

    def _clear_gcode(self):
        clear_gcode(self)

    def _ensure_gcode_loading_popup(self):
        ensure_gcode_loading_popup(self)

    def _show_gcode_loading(self):
        show_gcode_loading(self)

    def _hide_gcode_loading(self):
        hide_gcode_loading(self)

    def _set_gcode_loading_indeterminate(self, text: str):
        set_gcode_loading_indeterminate(self, text)

    def _set_gcode_loading_progress(self, done: int, total: int, name: str = ""):
        set_gcode_loading_progress(self, done, total, name)

    def _finish_gcode_loading(self):
        finish_gcode_loading(self)

    def _format_throughput(self, bps: float) -> str:
        return format_throughput(bps)

    def _estimate_factor_value(self) -> float:
        return estimate_factor_value(self)

    def _refresh_gcode_stats_display(self):
        refresh_gcode_stats_display(self)

    def _refresh_dro_display(self):
        refresh_dro_display(self)

    def _sync_tool_reference_label(self):
        try:
            with self.macro_executor.macro_vars() as macro_vars:
                macro_ns = macro_vars.get("macro")
                state = getattr(macro_ns, "state", None)
                tool_ref = getattr(state, "TOOL_REFERENCE", None) if state is not None else None
        except Exception:
            return
        if tool_ref == getattr(self, "_tool_reference_last", None):
            return
        self._tool_reference_last = tool_ref
        if tool_ref is None:
            self.tool_reference_var.set("")
            return
        try:
            value = float(tool_ref)
        except Exception:
            text = str(tool_ref)
        else:
            text = f"{value:.4f}"
        self.tool_reference_var.set(f"Tool Ref: {text}")

    def _on_estimate_factor_change(self, _value=None):
        on_estimate_factor_change(self, _value)

    def _update_live_estimate(self, done: int, total: int):
        update_live_estimate(self, done, total)

    def _maybe_notify_job_completion(self, done: int, total: int) -> None:
        maybe_notify_job_completion(self, done, total)

    def _format_gcode_stats_text(self, stats: dict, rate_source: str | None) -> str:
        return format_gcode_stats_text(self, stats, rate_source)

    def _apply_gcode_stats(self, token: int, stats: dict | None, rate_source: str | None):
        apply_gcode_stats(self, token, stats, rate_source)

    def _get_fallback_rapid_rate(self) -> float | None:
        return get_fallback_rapid_rate(self)

    def _get_rapid_rates_for_estimate(self):
        return get_rapid_rates_for_estimate(self)

    def _get_accel_rates_for_estimate(self):
        return get_accel_rates_for_estimate(self)

    def _make_stats_cache_key(
        self,
        rapid_rates: tuple[float, float, float] | None,
        accel_rates: tuple[float, float, float] | None,
    ):
        return make_stats_cache_key(self, rapid_rates, accel_rates)

    def _update_gcode_stats(self, lines: list[str], parse_result=None):
        update_gcode_stats(self, lines, parse_result=parse_result)

    def _load_grbl_setting_info(self):
        load_grbl_setting_info(self, _SCRIPT_DIR)

    def _setup_console_tags(self):
        setup_console_tags(self)

    def _send_console(self):
        send_console(self)

    def _clear_console_log(self):
        clear_console_log(self)

    def _save_console_log(self):
        save_console_log(self)

    def _request_settings_dump(self):
        request_settings_dump(self)

    def _maybe_auto_reconnect(self):
        maybe_auto_reconnect(self)

    def _handle_auto_reconnect_failure(self, exc: Exception):
        handle_auto_reconnect_failure(self, exc)

    def _set_unit_mode(self, mode: str):
        set_unit_mode(self, mode)

    def _update_unit_toggle_display(self):
        update_unit_toggle_display(self)

    def _start_homing(self):
        start_homing(self)

    def _set_step_xy(self, value: float):
        set_step_xy(self, value)

    def _set_step_z(self, value: float):
        set_step_z(self, value)

    def _set_manual_controls_enabled(self, enabled: bool):
        set_manual_controls_enabled(self, enabled)

    def _set_streaming_lock(self, locked: bool):
        set_streaming_lock(self, locked)

    def _format_alarm_message(self, message: str | None) -> str:
        return format_alarm_message(message)

    def _set_alarm_lock(self, locked: bool, message: str | None = None):
        set_alarm_lock(self, locked, message)
        self.machine_state.set(self._machine_state_text)
        self._update_state_highlight(self._machine_state_text)
        self._apply_status_poll_profile()

    def _show_alarm_recovery(self):
        show_alarm_recovery(self)

    def _sync_all_stop_mode_combo(self):
        sync_all_stop_mode_combo(self)

    def _on_all_stop_mode_change(self, _event=None):
        on_all_stop_mode_change(self, _event)

    def _sync_current_line_mode_combo(self):
        sync_current_line_mode_combo(self)

    def _on_current_line_mode_change(self, _event=None):
        on_current_line_mode_change(self, _event)

    def _toggle_keyboard_bindings(self):
        toggle_keyboard_bindings(self)

    def _toggle_joystick_bindings(self):
        toggle_joystick_bindings(self)

    def _on_keyboard_bindings_check(self):
        on_keyboard_bindings_check(self)

    def _refresh_joystick_toggle_text(self):
        refresh_joystick_toggle_text(self)

    def _refresh_joystick_safety_display(self):
        refresh_joystick_safety_display(self)

    def _update_joystick_polling_state(self):
        update_joystick_polling_state(self)

    def _restore_joystick_bindings_on_start(self):
        restore_joystick_bindings_on_start(self)

    def _get_pygame_module(self) -> ModuleType | None:
        return get_pygame_module(self)

    def _discover_joysticks(self, py, count: int) -> list[str]:
        return discover_joysticks(self, py, count)

    def _refresh_joystick_test_info(self):
        refresh_joystick_test_info(self)

    def _ensure_joystick_backend(self):
        return ensure_joystick_backend(self)

    def _start_joystick_polling(self):
        start_joystick_polling(self)

    def _stop_joystick_polling(self):
        stop_joystick_polling(self)

    def _ensure_joystick_polling_running(self):
        ensure_joystick_polling_running(self)

    def _poll_joystick_events(self):
        poll_joystick_events(self)

    def _describe_joystick_event(self, event) -> str | None:
        return describe_joystick_event(self, event)

    def _set_joystick_event_status(self, text: str):
        set_joystick_event_status(self, text)

    def _handle_joystick_event(self, event):
        handle_joystick_event(self, event)

    def _is_virtual_hold_button(self, btn) -> bool:
        return is_virtual_hold_button(self, btn)

    def _handle_joystick_button_release(self, key: tuple):
        handle_joystick_button_release(self, key)

    def _start_joystick_hold(self, binding_id: str):
        start_joystick_hold(self, binding_id)

    def _send_hold_jog(self):
        send_hold_jog(self)

    def _stop_joystick_hold(self, binding_id: str | None = None):
        stop_joystick_hold(self, binding_id)

    def _start_joystick_safety_capture(self):
        start_joystick_safety_capture(self)

    def _cancel_joystick_safety_capture(self):
        cancel_joystick_safety_capture(self)

    def _clear_joystick_safety_binding(self):
        clear_joystick_safety_binding(self)

    def _on_joystick_safety_toggle(self):
        on_joystick_safety_toggle(self)

    def _clear_duplicate_joystick_binding(self, key: tuple, keep_binding_id: str):
        clear_duplicate_joystick_binding(self, key, keep_binding_id)

    def _poll_joystick_states_from_hardware(self, py) -> bool:
        return poll_joystick_states_from_hardware(self, py)

    def _reset_joystick_axis_state(self, joy_id, axis):
        reset_joystick_axis_state(self, joy_id, axis)

    def _reset_joystick_hat_state(self, joy_id, hat_index):
        reset_joystick_hat_state(self, joy_id, hat_index)

    def _apply_keyboard_bindings(self):
        apply_keyboard_bindings(self)

    def _refresh_keyboard_table(self):
        refresh_keyboard_table(self)

    def _create_virtual_hold_buttons(self) -> list[VirtualHoldButton]:
        return create_virtual_hold_buttons(self)

    def _collect_buttons(self) -> list:
        return collect_buttons(self)

    def _button_label(self, btn) -> str:
        return button_label(self, btn)

    def _keyboard_key_for_button(self, btn) -> str:
        return keyboard_key_for_button(self, btn)

    def _joystick_binding_display(self, binding: dict[str, Any]) -> str:
        return joystick_binding_display(self, binding)

    def _joystick_binding_key(self, binding: dict[str, Any]):
        return joystick_binding_key(self, binding)

    def _button_axis_name(self, btn) -> str:
        return button_axis_name(self, btn)

    def _button_binding_id(self, btn) -> str:
        return button_binding_id(self, btn)

    def _find_binding_conflict(self, target_btn, label: str):
        return find_binding_conflict(self, target_btn, label)

    def _default_key_for_button(self, btn) -> str:
        return default_key_for_button(self, btn)

    def _on_kb_table_double_click(self, event):
        on_kb_table_double_click(self, event)

    def _on_kb_table_click(self, event):
        on_kb_table_click(self, event)

    def _start_kb_edit(self, row, col):
        start_kb_edit(self, row, col)

    def _start_joystick_capture(self, row):
        start_joystick_capture(self, row)

    def _cancel_joystick_capture(self):
        cancel_joystick_capture(self)

    def _joystick_binding_from_event(self, key):
        return joystick_binding_from_event(self, key)

    def _kb_capture_key(self, event, row, entry):
        kb_capture_key(self, event, row, entry)

    def _commit_kb_edit(self, row, entry, label_override: str | None = None):
        commit_kb_edit(self, row, entry, label_override)

    def _normalize_key_label(self, text: str) -> str:
        return normalize_key_label(self, text)

    def _normalize_key_chord(self, text: str) -> str:
        return normalize_key_chord(self, text)

    def _key_sequence_tuple(self, label: str) -> tuple[str, ...] | None:
        return key_sequence_tuple(self, label)

    def _update_modifier_state(self, event, pressed: bool) -> bool:
        return update_modifier_state(self, event, pressed)

    def _modifier_active(self, name: str, event_state: int | None = None) -> bool:
        return modifier_active(self, name, event_state)

    def _event_to_binding_label(self, event) -> str:
        return event_to_binding_label(self, event)

    def _on_key_modifier_release(self, event):
        on_key_modifier_release(self, event)

    def _sequence_conflict_pair(self, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
        return sequence_conflict_pair(self, seq_a, seq_b)

    def _sequence_conflict(self, seq: tuple[str, ...], existing: dict):
        return sequence_conflict(self, seq, existing)

    def _on_key_sequence(self, event):
        on_key_sequence(self, event)

    def _clear_key_sequence_buffer(self):
        clear_key_sequence_buffer(self)

    def _keyboard_binding_allowed(self) -> bool:
        return keyboard_binding_allowed(self)

    def _on_key_jog_stop(self, _event=None):
        on_key_jog_stop(self, _event)

    def _on_key_all_stop(self, _event=None):
        on_key_all_stop(self, _event)

    def _on_key_binding(self, btn):
        on_key_binding(self, btn)

    def _invoke_button(self, btn):
        invoke_button(self, btn)

    def _log_button_action(self, btn):
        log_button_action(self, btn)

    def _update_current_highlight(self):
        update_current_highlight(self)

    def _all_stop_action(self):
        all_stop_action(self)

    def _all_stop_gcode_label(self):
        return all_stop_gcode_label(self)

    def _on_fallback_rate_change(self, _event=None):
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)

    def _validate_jog_feed_var(self, var: tk.DoubleVar, fallback_default: float):
        validate_jog_feed_var(self, var, fallback_default)

    def _on_jog_feed_change_xy(self, _event=None):
        on_jog_feed_change_xy(self, _event)

    def _on_jog_feed_change_z(self, _event=None):
        on_jog_feed_change_z(self, _event)

    def _on_status_interval_change(self, _event=None):
        on_status_interval_change(self, _event)

    def _on_status_failure_limit_change(self, _event=None):
        on_status_failure_limit_change(self, _event)

    def _apply_error_dialog_settings(self, _event=None):
        apply_error_dialog_settings(self, _event)

    def _effective_status_poll_interval(self) -> float:
        return effective_status_poll_interval(self)

    def _apply_status_poll_profile(self):
        apply_status_poll_profile(self)

    def _update_estimate_rate_units_label(self):
        update_estimate_rate_units_label(self)

    def _on_estimate_rates_change(self, _event=None):
        on_estimate_rates_change(self, _event)

    def _validate_estimate_rate_text(self, text: str) -> bool:
        return validate_estimate_rate_text(text)

    def _convert_estimate_rates(self, old_units: str, new_units: str):
        convert_estimate_rates(self, old_units, new_units)

    def _show_macro_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        cancel_label: str,
        result_q: queue.Queue,
    ) -> None:
        show_macro_prompt(self, title, message, choices, cancel_label, result_q)

    # ---------- Zeroing ----------
    def _refresh_zeroing_ui(self):
        refresh_zeroing_ui(self)

    def _on_zeroing_mode_change(self):
        on_zeroing_mode_change(self)

    def zero_x(self):
        zero_x(self)

    def zero_y(self):
        zero_y(self)

    def zero_z(self):
        zero_z(self)

    def zero_all(self):
        zero_all(self)

    def goto_zero(self):
        goto_zero(self)

    # ---------- UI event handling ----------
    def _update_tab_visibility(self, nb=None):
        update_tab_visibility(self, nb)

    def _update_app_settings_scrollregion(self):
        update_app_settings_scrollregion(self)

    def _on_app_settings_mousewheel(self, event):
        on_app_settings_mousewheel(self, event)

    def _bind_app_settings_mousewheel(self):
        bind_app_settings_mousewheel(self)

    def _unbind_app_settings_mousewheel(self):
        unbind_app_settings_mousewheel(self)

    def _on_tab_changed(self, event):
        on_tab_changed(self, event)

    def _build_led_panel(self, parent):
        build_led_panel(self, parent)

    def _set_led_state(self, key, on):
        set_led_state(self, key, on)

    def _update_led_panel(self, endstop: bool, probe: bool, hold: bool):
        update_led_panel(self, endstop, probe, hold)

    def _update_led_visibility(self):
        update_led_visibility(self)

    def _on_led_visibility_change(self):
        on_led_visibility_change(self)

    def _apply_state_fg(self, color: str | None, fg: str | None = None):
        apply_state_fg(self, color, fg=fg)

    def _cancel_state_flash(self):
        cancel_state_flash(self)

    def _toggle_state_flash(self):
        toggle_state_flash(self)

    def _start_state_flash(self, color: str):
        start_state_flash(self, color)

    def _update_state_highlight(self, state: str | None):
        update_state_highlight(self, state)

    def _drain_ui_queue(self):
        drain_ui_queue(self)

    def _refresh_tooltips_toggle_text(self):
        refresh_tooltips_toggle_text(self)

    def _refresh_render_3d_toggle_text(self):
        refresh_render_3d_toggle_text(self)

    def _refresh_keybindings_toggle_text(self):
        refresh_keybindings_toggle_text(self)

    def _update_quick_button_visibility(self):
        update_quick_button_visibility(self)

    def _on_quick_button_visibility_change(self):
        on_quick_button_visibility_change(self)

    def _toggle_tooltips(self):
        toggle_tooltips(self)

    def _apply_theme(self, theme: str):
        apply_theme(self, theme)

    def _on_gui_logging_change(self):
        on_gui_logging_change(self)

    def _on_theme_change(self, *_):
        on_theme_change(self)

    def _refresh_stop_button_backgrounds(self):
        refresh_stop_button_backgrounds(self)

    def _refresh_led_backgrounds(self):
        refresh_led_backgrounds(self)

    def _install_dialog_loggers(self):
        install_dialog_loggers(self)

    def _toggle_error_dialogs(self):
        toggle_error_dialogs(self)

    def _on_error_dialogs_enabled_change(self):
        on_error_dialogs_enabled_change(self)

    def _toggle_performance(self):
        toggle_performance(self)

    def _toggle_console_pos_status(self):
        toggle_console_pos_status(self)

    def _toggle_render_3d(self):
        toggle_render_3d(self)

    def _toolpath_limit_value(self, raw, fallback):
        return toolpath_limit_value(self, raw, fallback)

    def _clamp_toolpath_performance(self, value):
        return clamp_toolpath_performance(self, value)

    def _clamp_toolpath_streaming_render_interval(self, value):
        return clamp_toolpath_streaming_render_interval(self, value)

    def _apply_toolpath_streaming_render_interval(self, _event=None):
        apply_toolpath_streaming_render_interval(self, _event)

    def _toolpath_perf_values(self, perf: float):
        return toolpath_perf_values(self, perf)

    def _on_toolpath_performance_move(self, value):
        on_toolpath_performance_move(self, value)

    def _on_toolpath_performance_key_release(self, event):
        on_toolpath_performance_key_release(self, event)

    def _apply_toolpath_performance(self, _event=None):
        apply_toolpath_performance(self, _event)

    def _apply_toolpath_draw_limits(self, _event=None):
        apply_toolpath_draw_limits(self, _event)

    def _on_arc_detail_scale_move(self, value):
        on_arc_detail_scale_move(self, value)

    def _on_arc_detail_scale_key_release(self, event):
        on_arc_detail_scale_key_release(self, event)

    def _clamp_arc_detail(self, value):
        return clamp_arc_detail(self, value)

    def _apply_toolpath_arc_detail(self, _event=None):
        apply_toolpath_arc_detail(self, _event)

    def _schedule_toolpath_arc_detail_reparse(self):
        schedule_toolpath_arc_detail_reparse(self)

    def _run_toolpath_arc_detail_reparse(self):
        run_toolpath_arc_detail_reparse(self)

    def _on_toolpath_lightweight_change(self):
        on_toolpath_lightweight_change(self)

    def _toggle_unit_mode(self):
        toggle_unit_mode(self)

    def _on_resume_button_visibility_change(self):
        on_resume_button_visibility_change(self)

    def _on_recover_button_visibility_change(self):
        on_recover_button_visibility_change(self)

    def _update_resume_button_visibility(self):
        update_resume_button_visibility(self)

    def _update_recover_button_visibility(self):
        update_recover_button_visibility(self)


    def _confirm_and_run(self, label: str, func):
        confirm_and_run(self, label, func)

    def _require_grbl_connection(self) -> bool:
        return require_grbl_connection(self)

    def _run_if_connected(self, func):
        run_if_connected(self, func)

    def _send_manual(self, command: str, source: str):
        send_manual(self, command, source)

    def _save_3d_view(self):
        save_3d_view(self)

    def _load_3d_view(self, show_status: bool = True):
        load_3d_view(self, show_status)

    def _clear_pending_ui_updates(self):
        self.streaming_controller.clear_pending_ui_updates()

    def _handle_evt(self, evt):
        handle_event(self, evt)

    def _on_close(self):
        on_close(self)

    def _call_on_ui_thread(self, func, *args, timeout: float | None = 5.0, **kwargs):
        return call_on_ui_thread(self, func, *args, timeout=timeout, **kwargs)

    def _post_ui_thread(self, func, *args, **kwargs):
        post_ui_thread(self, func, *args, **kwargs)

    def _log_exception(
        self,
        context: str,
        exc: BaseException,
        *,
        show_dialog: bool = False,
        dialog_title: str = "Error",
        traceback_text: str | None = None,
    ):
        log_exception(
            self,
            context,
            exc,
            show_dialog=show_dialog,
            dialog_title=dialog_title,
            traceback_text=traceback_text,
        )

    def _tk_report_callback_exception(self, exc, val, tb):
        tk_report_callback_exception(self, exc, val, tb)

    def _should_show_error_dialog(self) -> bool:
        return should_show_error_dialog(self)

    def _reset_error_dialog_state(self):
        reset_error_dialog_state(self)

    def _set_error_dialog_status(self, text: str):
        set_error_dialog_status(self, text)

    def _load_settings(self) -> dict:
        return load_settings(self)

    def _save_settings(self):
        save_settings(self)


if __name__ == "__main__":
    App().mainloop()
