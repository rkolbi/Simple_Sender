import pytest

tk = pytest.importorskip("tkinter")

from tkinter import ttk

from simple_sender.ui.settings import build_app_settings_tab
from simple_sender.ui.settings import dialog as settings_dialog
from simple_sender.utils.constants import CURRENT_LINE_CHOICES


@pytest.mark.ui
def test_build_app_settings_tab_creates_controls(tk_root, monkeypatch) -> None:
    from simple_sender.ui.settings import sections_controls

    monkeypatch.setattr(sections_controls.sys, "platform", "linux")
    notebook = ttk.Notebook(tk_root)

    class _App:
        def __init__(self) -> None:
            self.register = tk_root.register
            self.calls = {}
            self.settings = {}
            self.version_var = tk.StringVar(master=tk_root, value="v1")
            self.available_themes = ["vista", "default"]
            self.selected_theme = tk.StringVar(master=tk_root, value="vista")
            self.dry_run_sanitize_stream = tk.BooleanVar(master=tk_root, value=False)
            self.homing_watchdog_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.homing_watchdog_timeout = tk.DoubleVar(master=tk_root, value=60.0)
            self.fallback_rapid_rate = tk.StringVar(master=tk_root, value="5000")
            self.estimate_factor = tk.DoubleVar(master=tk_root, value=1.0)
            self._estimate_factor_label = tk.StringVar(master=tk_root, value="1.0x")
            self.estimate_rate_x_var = tk.StringVar(master=tk_root, value="100")
            self.estimate_rate_y_var = tk.StringVar(master=tk_root, value="200")
            self.estimate_rate_z_var = tk.StringVar(master=tk_root, value="300")
            self.status_poll_interval = tk.StringVar(master=tk_root, value="0.2")
            self.status_query_failure_limit = tk.StringVar(master=tk_root, value="3")
            self.error_dialogs_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.error_dialog_interval_var = tk.StringVar(master=tk_root, value="2.0")
            self.error_dialog_burst_window_var = tk.StringVar(master=tk_root, value="30.0")
            self.error_dialog_burst_limit_var = tk.StringVar(master=tk_root, value="3")
            self.grbl_popup_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.grbl_popup_auto_dismiss_sec = tk.DoubleVar(master=tk_root, value=12.0)
            self.grbl_popup_dedupe_sec = tk.DoubleVar(master=tk_root, value=3.0)
            self.job_completion_popup = tk.BooleanVar(master=tk_root, value=True)
            self.job_completion_beep = tk.BooleanVar(master=tk_root, value=False)
            self.macros_allow_python = tk.BooleanVar(master=tk_root, value=True)
            self.macro_line_timeout_sec = tk.DoubleVar(master=tk_root, value=0.0)
            self.macro_total_timeout_sec = tk.DoubleVar(master=tk_root, value=0.0)
            self.macro_probe_z_location = tk.DoubleVar(master=tk_root, value=-5.0)
            self.macro_probe_safety_margin = tk.DoubleVar(master=tk_root, value=3.0)
            self.zeroing_persistent = tk.BooleanVar(master=tk_root, value=False)
            self.jog_feed_xy = tk.StringVar(master=tk_root, value="4000")
            self.jog_feed_z = tk.StringVar(master=tk_root, value="500")
            self.keyboard_bindings_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.joystick_test_status = tk.StringVar(master=tk_root, value="")
            self.joystick_device_status = tk.StringVar(master=tk_root, value="")
            self.joystick_event_status = tk.StringVar(master=tk_root, value="")
            self.joystick_safety_enabled = tk.BooleanVar(master=tk_root, value=False)
            self.joystick_safety_status = tk.StringVar(master=tk_root, value="")
            self.joystick_live_status = tk.StringVar(master=tk_root, value="")
            self.keyboard_live_status = tk.StringVar(master=tk_root, value="")
            self.current_line_mode = tk.StringVar(master=tk_root, value="sent")
            self.training_wheels = tk.BooleanVar(master=tk_root, value=True)
            self.stop_hold_on_focus_loss = tk.BooleanVar(master=tk_root, value=True)
            self.validate_streaming_gcode = tk.BooleanVar(master=tk_root, value=True)
            self.streaming_line_threshold = tk.IntVar(master=tk_root, value=250000)
            self.reconnect_on_open = tk.BooleanVar(master=tk_root, value=False)
            self.fullscreen_on_startup = tk.BooleanVar(master=tk_root, value=True)
            self.show_resume_from_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_recover_button = tk.BooleanVar(master=tk_root, value=False)
            self.performance_mode = tk.BooleanVar(master=tk_root, value=False)
            self.gui_logging_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.show_endstop_indicator = tk.BooleanVar(master=tk_root, value=True)
            self.show_probe_indicator = tk.BooleanVar(master=tk_root, value=True)
            self.show_hold_indicator = tk.BooleanVar(master=tk_root, value=True)
            self.auto_level_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.show_autolevel_overlay = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_tips_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_3d_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_keys_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_alo_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_vac_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_light_button = tk.BooleanVar(master=tk_root, value=True)
            self.show_quick_release_button = tk.BooleanVar(master=tk_root, value=True)
            self.toolpath_streaming_render_interval = tk.DoubleVar(master=tk_root, value=0.25)
            self.auto_level_job_prefs = {}
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=False)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="")
            self.kasa_device_choice = tk.StringVar(master=tk_root, value="")
            self.kasa_outlet_info_var = tk.StringVar(master=tk_root, value="")
            self.kasa_validation_var = tk.StringVar(master=tk_root, value="")
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.vacuum_outlet = tk.IntVar(master=tk_root, value=1)
            self.vacuum_outlet_label = tk.StringVar(master=tk_root, value="Outlet 1")
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_outlet = tk.IntVar(master=tk_root, value=2)
            self.light_outlet_label = tk.StringVar(master=tk_root, value="Outlet 2")
            self.kasa_outlet_1_status = tk.StringVar(master=tk_root, value="")
            self.kasa_outlet_2_status = tk.StringVar(master=tk_root, value="")

        def _mark(self, key: str) -> None:
            self.calls[key] = self.calls.get(key, 0) + 1

        def _update_app_settings_scrollregion(self):
            return None

        def _bind_app_settings_mousewheel(self):
            return None

        def _unbind_app_settings_mousewheel(self):
            return None

        def _on_theme_change(self, _event=None):
            return None

        def _on_all_stop_mode_change(self, _event=None):
            return None

        def _sync_all_stop_mode_combo(self):
            self._mark("sync_all_stop")

        def _on_fallback_rate_change(self, _event=None):
            return None

        def _on_estimate_factor_change(self, _value=None):
            return None

        def _validate_estimate_rate_text(self, _value):
            return True

        def _on_estimate_rates_change(self, _event=None):
            return None

        def _update_estimate_rate_units_label(self):
            self._mark("estimate_units")

        def _on_status_interval_change(self, _event=None):
            self._mark("status_interval")

        def _on_status_failure_limit_change(self, _event=None):
            return None

        def _on_homing_watchdog_change(self, _event=None):
            return None

        def _on_error_dialogs_enabled_change(self):
            return None

        def _apply_error_dialog_settings(self, _event=None):
            return None

        def _on_zeroing_mode_change(self):
            return None

        def _on_jog_feed_change_xy(self, _event=None):
            return None

        def _on_jog_feed_change_z(self, _event=None):
            return None

        def _on_keyboard_bindings_check(self):
            return None

        def _on_kb_table_double_click(self, _event):
            return None

        def _on_kb_table_click(self, _event):
            return None

        def _refresh_joystick_test_info(self):
            return None

        def _toggle_joystick_bindings(self):
            return None

        def _on_joystick_safety_toggle(self):
            return None

        def _start_joystick_safety_capture(self):
            return None

        def _clear_joystick_safety_binding(self):
            return None

        def _refresh_joystick_safety_display(self):
            self._mark("joystick_safety")

        def _on_current_line_mode_change(self, _event=None):
            return None

        def _sync_current_line_mode_combo(self):
            self._mark("sync_current_line")

        def _show_release_checklist(self):
            return None

        def _show_run_checklist(self):
            return None

        def _run_preflight_check(self):
            return None

        def _export_session_diagnostics(self):
            return None

        def _open_runtime_telemetry(self):
            return None

        def _export_backup_bundle(self):
            return None

        def _import_backup_bundle(self):
            return None

        def _open_macro_manager(self):
            return None

        def _apply_safe_mode_profile(self):
            return None

        def _on_resume_button_visibility_change(self):
            return None

        def _on_recover_button_visibility_change(self):
            return None

        def _on_auto_level_enabled_change(self):
            return None

        def _toggle_performance(self):
            return None

        def _on_gui_logging_change(self):
            return None

        def _on_led_visibility_change(self):
            return None

        def _on_quick_button_visibility_change(self):
            return None

        def _toggle_tooltips(self):
            return None

        def _toggle_render_3d(self):
            return None

        def _toggle_keyboard_bindings(self):
            return None

        def _toggle_autolevel_overlay(self):
            return None

        def _toggle_kasa_vacuum_quick(self):
            return None

        def _toggle_kasa_light_quick(self):
            return None

        def _apply_toolpath_streaming_render_interval(self, _event=None):
            return None

        def _on_kasa_master_change(self):
            return None

        def _discover_kasa_devices(self):
            return None

        def _on_kasa_device_selected(self, _event=None):
            return None

        def _on_kasa_mapping_change(self, _changed=None):
            return None

        def _refresh_kasa_outlet_list(self):
            return None

        def _test_kasa_outlet(self, _outlet_id: int, _on: bool):
            return None

        def _refresh_kasa_controls_state(self):
            self._mark("kasa_refresh")

    app = _App()
    build_app_settings_tab(app, notebook)

    assert isinstance(app.app_settings_canvas, tk.Canvas)
    assert app.app_settings_sticky_var.get() == "Interface & Viewer"
    assert app.theme_combo["values"] == tuple(app.available_themes)
    assert app.current_line_combo["values"] == tuple(label for label, _ in CURRENT_LINE_CHOICES)
    assert app.kb_table["columns"] == ("button", "axis", "key", "joystick", "clear")
    assert app.btn_toggle_joystick_bindings._kb_id == "toggle_joystick_bindings"
    assert app.btn_toggle_keybinds_settings._kb_id == "toggle_keybindings_settings"
    assert app.btn_toggle_kasa_vacuum_settings._kb_id == "toggle_kasa_vacuum_quick_settings"
    assert app.btn_toggle_kasa_light_settings._kb_id == "toggle_kasa_light_quick_settings"
    assert app.btn_performance_mode.cget("text") == "Performance: Off"
    assert app.macro_line_timeout_entry.cget("width") == 12
    assert app.macro_total_timeout_entry.cget("width") == 12
    assert app.macro_probe_z_location_entry.cget("width") == 12
    assert app.macro_probe_safety_margin_entry.cget("width") == 12
    assert app.vacuum_outlet_combo["values"] == ("Outlet 1", "Outlet 2")
    assert app.light_outlet_combo["values"] == ("Outlet 1", "Outlet 2")
    assert app.calls["status_interval"] == 1
    assert app.calls["estimate_units"] == 1
    assert app.calls["sync_current_line"] == 1
    assert app.calls["joystick_safety"] == 1
    assert app.calls["kasa_refresh"] == 1


@pytest.mark.ui
def test_app_settings_supports_search_and_basic_advanced_filtering(tk_root, monkeypatch) -> None:
    notebook = ttk.Notebook(tk_root)

    def _builder_factory(title: str):
        def _builder(_app, parent, row: int) -> int:
            frame = ttk.LabelFrame(parent, text=title, padding=6)
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 4))
            ttk.Label(frame, text=title).grid(row=0, column=0, sticky="w")
            return row + 1

        return _builder

    monkeypatch.setattr(settings_dialog, "build_interface_section", _builder_factory("Interface"))
    monkeypatch.setattr(settings_dialog, "build_theme_section", _builder_factory("Theme"))
    monkeypatch.setattr(settings_dialog, "build_viewer_section", _builder_factory("Viewer"))
    monkeypatch.setattr(settings_dialog, "build_jogging_section", _builder_factory("Jogging"))
    monkeypatch.setattr(settings_dialog, "build_zeroing_section", _builder_factory("Zeroing"))
    monkeypatch.setattr(settings_dialog, "build_keyboard_shortcuts_section", _builder_factory("Keyboard shortcuts"))
    monkeypatch.setattr(settings_dialog, "build_kasa_plug_section", _builder_factory("Kasa Plug"))
    monkeypatch.setattr(settings_dialog, "build_macros_section", _builder_factory("Macros"))
    monkeypatch.setattr(settings_dialog, "build_estimation_section", _builder_factory("Estimation"))
    monkeypatch.setattr(settings_dialog, "build_auto_level_section", _builder_factory("Auto-Level"))
    monkeypatch.setattr(settings_dialog, "build_diagnostics_section", _builder_factory("Diagnostics"))
    monkeypatch.setattr(settings_dialog, "build_safety_section", _builder_factory("Safety"))
    monkeypatch.setattr(settings_dialog, "build_safety_aids_section", _builder_factory("Safety Aids"))
    monkeypatch.setattr(settings_dialog, "build_status_polling_section", _builder_factory("Status polling"))
    monkeypatch.setattr(settings_dialog, "build_error_dialogs_section", _builder_factory("Error dialogs"))
    monkeypatch.setattr(settings_dialog, "build_power_section", _builder_factory("System"))

    class _App:
        def __init__(self) -> None:
            self.settings = {}
            self.version_var = tk.StringVar(master=tk_root, value="v1")

        def _update_app_settings_scrollregion(self):
            return None

        def _bind_app_settings_mousewheel(self):
            return None

        def _unbind_app_settings_mousewheel(self):
            return None

    app = _App()
    build_app_settings_tab(app, notebook)
    tk_root.update_idletasks()

    entries = {entry["title"]: entry for entry in app.app_settings_section_entries}
    assert entries["Theme"]["widget"].winfo_ismapped() == 1
    assert entries["Macros"]["widget"].winfo_ismapped() == 0
    assert entries["System"]["widget"].winfo_ismapped() == 1

    app.app_settings_view_mode_var.set("Advanced")
    settings_dialog._apply_app_settings_filters(app)
    tk_root.update_idletasks()
    assert entries["Macros"]["widget"].winfo_ismapped() == 1

    app.app_settings_search_var.set("macro")
    settings_dialog._apply_app_settings_filters(app)
    tk_root.update_idletasks()
    assert entries["Macros"]["widget"].winfo_ismapped() == 1
    assert entries["Theme"]["widget"].winfo_ismapped() == 0

    app.app_settings_search_var.set("not-a-real-setting")
    settings_dialog._apply_app_settings_filters(app)
    tk_root.update_idletasks()
    assert app.app_settings_empty_label.winfo_ismapped() == 1
    assert app.app_settings_sticky_var.get() == "No matching settings"
