import queue
from pathlib import Path

import pytest

from simple_sender.ui.settings_persistence import load_settings, save_settings
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.exceptions import SettingsLoadError, SettingsSaveError

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self, value) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _ToolpathPanel:
    def get_display_options(self):
        return True, False, True

    def get_draw_percent(self):
        return 75


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]


class _SettingsStore:
    def __init__(self, data: dict, raise_on_load: bool = False, raise_on_save: bool = False) -> None:
        self.data = data
        self._raise_on_load = raise_on_load
        self._raise_on_save = raise_on_save
        self.reset_called = False
        self.save_called = False

    def load(self) -> bool:
        if self._raise_on_load:
            raise SettingsLoadError("bad")
        return True

    def save(self) -> None:
        if self._raise_on_save:
            raise SettingsSaveError("bad")
        self.save_called = True

    def reset_to_defaults(self) -> None:
        self.reset_called = True
        self.data = dict(DEFAULT_SETTINGS)


def test_load_settings_resets_on_error() -> None:
    store = _SettingsStore({"baud_rate": 9600}, raise_on_load=True)

    class _App:
        _settings_store = store

    data = load_settings(_App())

    assert store.reset_called
    assert data["baud_rate"] == DEFAULT_SETTINGS["baud_rate"]


def test_save_settings_logs_invalid_values(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    store = _SettingsStore({})
    ui_q = queue.Queue()

    class _App:
        def __init__(self) -> None:
            self._settings_store = store
            self.ui_q = ui_q
            self.status = _Status()
            self.toolpath_panel = _ToolpathPanel()
            self.toolpath_performance = _Var(50.0)
            self._clamp_toolpath_performance = lambda value: float(value)
            self._toolpath_full_limit_default = "100"
            self._toolpath_interactive_limit_default = "50"
            self.toolpath_full_limit = _Var("100")
            self.toolpath_interactive_limit = _Var("50")
            self.toolpath_arc_detail = _Var("10")
            self._clamp_arc_detail = lambda value: float(value)
            self._apply_error_dialog_settings = lambda: None
            self._on_status_failure_limit_change = lambda: None
            self._on_homing_watchdog_change = lambda: None
            self.settings_path = str(settings_path)
            self.console_positions_enabled = _Var(True)
            self.settings = {}
            self.current_port = _Var("COM3")
            self.unit_mode = _Var("mm")
            self.step_xy = _Var("bad")
            self.step_z = _Var("1.0")
            self.jog_feed_xy = _Var("4000")
            self.jog_feed_z = _Var("500")
            self._toolpath_limit_value = lambda value, _default: value
            self.geometry = lambda: "800x600"
            self.tooltip_enabled = _Var(True)
            self.gui_logging_enabled = _Var(True)
            self.pi_profile_enabled = _Var(True)
            self.error_dialogs_enabled = _Var(True)
            self.grbl_popup_enabled = _Var(True)
            self.grbl_popup_auto_dismiss_sec = _Var("12")
            self.grbl_popup_dedupe_sec = _Var("3")
            self.performance_mode = _Var(False)
            self.render3d_enabled = _Var(False)
            self.status_poll_interval = _Var("bad")
            self.status_query_failure_limit = _Var("3")
            self.homing_watchdog_enabled = _Var(True)
            self.homing_watchdog_timeout = _Var("60")
            self.all_stop_mode = _Var("stop_reset")
            self.training_wheels = _Var(True)
            self.stop_hold_on_focus_loss = _Var(True)
            self.validate_streaming_gcode = _Var(True)
            self.streaming_line_threshold = _Var("250000")
            self.reconnect_on_open = _Var(False)
            self.fullscreen_on_startup = _Var(True)
            self.selected_theme = _Var("default")
            self.show_resume_from_button = _Var(True)
            self.show_recover_button = _Var(True)
            self.show_endstop_indicator = _Var(True)
            self.show_probe_indicator = _Var(True)
            self.show_hold_indicator = _Var(True)
            self.auto_level_enabled = _Var(True)
            self.show_autolevel_overlay = _Var(True)
            self.show_quick_tips_button = _Var(True)
            self.show_quick_3d_button = _Var(True)
            self.show_quick_keys_button = _Var(True)
            self.show_quick_alo_button = _Var(True)
            self.show_quick_vac_button = _Var(True)
            self.show_quick_light_button = _Var(True)
            self.show_quick_release_button = _Var(True)
            self.fallback_rapid_rate = _Var("1000")
            self.estimate_factor = _Var("1.0")
            self.estimate_rate_x_var = _Var("100")
            self.estimate_rate_y_var = _Var("100")
            self.estimate_rate_z_var = _Var("100")
            self.keyboard_bindings_enabled = _Var(True)
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(False)
            self.dry_run_sanitize_stream = _Var(False)
            self._joystick_bindings = {}
            self._joystick_safety_binding = None
            self.current_line_mode = _Var("sent")
            self._key_bindings = {}
            self.toolpath_lightweight = _Var(False)
            self.toolpath_streaming_render_interval = _Var("0.25")
            self._toolpath_streaming_render_interval_default = 0.25
            self._error_dialog_interval = 2.0
            self._error_dialog_burst_window = 30.0
            self._error_dialog_burst_limit = 3
            self.job_completion_popup = _Var(True)
            self.job_completion_beep = _Var(False)
            self.macros_allow_python = _Var(True)
            self.macro_line_timeout_sec = _Var("0")
            self.macro_total_timeout_sec = _Var("0")
            self.zeroing_persistent = _Var(False)
            self.auto_level_job_prefs = {}

    app = _App()

    save_settings(app)

    logs = [evt for evt in list(ui_q.queue) if evt[0] == "log"]
    assert any("Invalid step XY" in evt[1] for evt in logs)
    assert app.settings["show_quick_vac_button"] is True
    assert app.settings["show_quick_light_button"] is True
    assert app.settings["macro_line_timeout_sec"] == 0.0
    assert app.settings["macro_total_timeout_sec"] == 0.0
    assert app.settings["macro_probe_z_location"] == DEFAULT_SETTINGS["macro_probe_z_location"]
    assert app.settings["macro_probe_safety_margin"] == DEFAULT_SETTINGS["macro_probe_safety_margin"]
    assert app.settings["grbl_popup_enabled"] is True
    assert app.settings["grbl_popup_auto_dismiss_sec"] == 12.0
    assert app.settings["grbl_popup_dedupe_sec"] == 3.0
    assert app.settings["pi_profile_enabled"] is True
    assert app.settings["kasa_enabled"] is False
    assert app.settings["kasa_device_identifier"] == ""
    assert app.settings["vacuum_enabled"] is False
    assert app.settings["vacuum_outlet"] == 1
    assert app.settings["light_enabled"] is False
    assert app.settings["light_outlet"] == 2
    assert store.save_called
