import os

import pytest

tk = pytest.importorskip("tkinter")

from simple_sender.ui import app_init


def _make_root():
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("tkinter Tcl/Tk not available")
    return root


def test_init_settings_store_uses_current_jog_feed_settings(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    seen = {}

    class _Settings:
        def __init__(self, path: str) -> None:
            seen["path"] = path

    app = type("App", (), {})()
    app._load_settings = lambda: {
        "keyboard_bindings_enabled": False,
        "jog_feed_xy": 1234.0,
        "jog_feed_z": 321.0,
    }

    monkeypatch.setattr(app_init, "Settings", _Settings)
    monkeypatch.setattr(app_init, "get_settings_path", lambda: str(settings_path))

    jog_xy, jog_z = app_init.init_settings_store(app, str(tmp_path))

    assert seen["path"] == str(settings_path)
    assert app.settings_path == str(settings_path)
    assert app.settings_dir == os.path.dirname(str(settings_path))
    assert app.settings["keyboard_bindings_enabled"] is False
    assert jog_xy == 1234.0
    assert jog_z == 321.0


def test_init_settings_store_does_not_migrate_legacy_keys(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"

    class _Settings:
        def __init__(self, _path: str) -> None:
            return None

    app = type("App", (), {})()
    app._load_settings = lambda: {
        "keybindings_enabled": False,
        "jog_feed": 1234.0,
        "jog_feed_z": 1234.0,
    }

    monkeypatch.setattr(app_init, "Settings", _Settings)
    monkeypatch.setattr(app_init, "get_settings_path", lambda: str(settings_path))

    jog_xy, jog_z = app_init.init_settings_store(app, str(tmp_path))

    assert "keyboard_bindings_enabled" not in app.settings
    assert jog_xy == 4000.0
    assert jog_z == 1234.0


def test_init_basic_preferences_disables_joystick_when_no_pygame(monkeypatch) -> None:
    root = _make_root()

    class _App:
        def __init__(self) -> None:
            self.settings = {
                "joystick_bindings_enabled": True,
                "current_line_mode": "sent",
                "console_positions_enabled": False,
                "theme": "default",
            }
            self.applied_theme = None

        def _apply_theme(self, theme: str) -> None:
            self.applied_theme = theme

    app = _App()
    try:
        monkeypatch.setattr(app_init, "PYGAME_AVAILABLE", False)

        app_init.init_basic_preferences(app, "1.2.3")

        assert app.joystick_bindings_enabled.get() is False
        assert app._joystick_auto_enable_requested is False
        assert app.console_positions_enabled.get() is False
        assert not hasattr(app, "console_status_enabled")
        assert app.current_line_mode.get() == "sent"
        assert app.selected_theme.get() == "default"
        assert app.applied_theme == "default"
        assert app.version_var.get().endswith("v1.2.3")
    finally:
        root.destroy()


def test_init_runtime_state_clamps_limits_and_normalizes_keys(monkeypatch) -> None:
    root = _make_root()

    class _Grbl:
        def __init__(self, ui_q) -> None:
            self.ui_q = ui_q
            self.limit = None

        def set_status_query_failure_limit(self, limit: int) -> None:
            self.limit = limit

    class _MacroExecutor:
        def __init__(self, _app, macro_search_dirs=()) -> None:
            self.macro_search_dirs = macro_search_dirs

    class _Controller:
        def __init__(self, _app) -> None:
            return None

    class _App:
        def __init__(self) -> None:
            self.settings = {
                "key_bindings": {1: "a"},
                "joystick_bindings": {"joy1": {"axis": 1}},
                "joystick_safety_binding": {"button": 2},
                "error_dialog_interval": 0,
                "error_dialog_burst_window": -1,
                "error_dialog_burst_limit": 0,
                "status_query_failure_limit": 99,
                "status_poll_interval": 0.3,
                "unit_mode": "inch",
                "estimate_rate_x": "10",
                "estimate_rate_y": "20",
                "estimate_rate_z": "30",
            }
            self._apply_status_poll_profile_called = False

        def _normalize_key_label(self, value: str) -> str:
            return value.upper()

        def _install_dialog_loggers(self) -> None:
            self.dialoggers_installed = True

        def _tk_report_callback_exception(self, *args, **kwargs) -> None:
            return None

        def _apply_status_poll_profile(self) -> None:
            self._apply_status_poll_profile_called = True

    app = _App()
    try:
        monkeypatch.setattr(app_init, "GrblWorker", _Grbl)
        monkeypatch.setattr(app_init, "MacroExecutor", _MacroExecutor)
        monkeypatch.setattr(app_init, "StreamingController", _Controller)
        monkeypatch.setattr(app_init, "MacroPanel", _Controller)
        monkeypatch.setattr(app_init, "ToolpathPanel", _Controller)
        monkeypatch.setattr(app_init, "GRBLSettingsController", _Controller)
        monkeypatch.setattr(
            app_init,
            "setting",
            lambda key, fallback: app.settings.get(key, fallback),
            raising=False,
        )

        app_init.init_runtime_state(app, 12.0, 34.0, ("macros",))

        assert app._key_bindings == {"1": "A"}
        assert app._joystick_bindings == {"joy1": {"axis": 1}}
        assert app._joystick_safety_binding == {"button": 2}
        assert app.status_query_failure_limit.get() == 10
        assert app.grbl.limit == 10
        assert app.jog_feed_xy.get() == 12.0
        assert app.jog_feed_z.get() == 34.0
        assert app.macro_executor.macro_search_dirs == ("macros",)
        assert app._error_dialog_interval == 2.0
        assert app._error_dialog_burst_window == 30.0
        assert app._error_dialog_burst_limit == 3
        assert app._apply_status_poll_profile_called is True
    finally:
        root.destroy()
