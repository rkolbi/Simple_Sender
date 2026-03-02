import os
import types

import pytest

pytest.importorskip("tkinter")

from simple_sender import (
    application,
    application_actions,
    application_controls,
    application_gcode,
    application_layout,
)

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self, value) -> None:
        self._value = value

    def get(self):
        return self._value


def _bare_app():
    return application.App.__new__(application.App)


def test_resolve_script_file_prefers_argv(monkeypatch, tmp_path) -> None:
    script = tmp_path / "script.py"
    script.write_text("pass", encoding="utf-8")

    monkeypatch.setattr(application.sys, "argv", [str(script)])
    monkeypatch.setattr(application, "__file__", "ignored")
    monkeypatch.setattr(
        application.os.path,
        "isfile",
        lambda p: os.path.abspath(p) == os.path.abspath(str(script)),
    )

    assert application._resolve_script_file() == os.path.abspath(str(script))


def test_resolve_script_file_falls_back_to_dunder_file(monkeypatch, tmp_path) -> None:
    script = tmp_path / "module.py"
    script.write_text("pass", encoding="utf-8")

    monkeypatch.setattr(application.sys, "argv", ["missing"])
    monkeypatch.setattr(application, "__file__", str(script))
    monkeypatch.setattr(application.os.path, "isfile", lambda _p: False)

    assert application._resolve_script_file() == os.path.abspath(str(script))


def test_discover_macro_dirs(monkeypatch, tmp_path) -> None:
    pkg_dir = tmp_path / "pkg"
    macros_dir = pkg_dir / "macros"
    macros_dir.mkdir(parents=True)
    pkg_file = pkg_dir / "__init__.py"
    pkg_file.write_text("pass", encoding="utf-8")
    script_dir = tmp_path / "script"
    script_macros = script_dir / "macros"
    script_macros.mkdir(parents=True)

    dummy_pkg = types.SimpleNamespace(__file__=str(pkg_file))
    monkeypatch.setitem(application.sys.modules, "simple_sender", dummy_pkg)
    monkeypatch.setattr(application, "_SCRIPT_DIR", str(script_dir))
    monkeypatch.setattr(application.os.path, "isdir", lambda p: True)

    found = application._discover_macro_dirs()

    assert str(macros_dir) in found
    assert str(script_macros) in found
    assert str(script_dir) in found


def test_app_on_app_focus_out_triggers_hold(monkeypatch) -> None:
    app = _bare_app()
    app.stop_hold_on_focus_loss = _Var(True)
    called = {}

    def _after_idle(func):
        called["after_idle"] = True
        func()
        return None

    app.after_idle = _after_idle
    app._maybe_stop_joystick_hold_on_focus_loss = lambda: called.setdefault("ran", True)

    application.App._on_app_focus_out(app)

    assert called == {"after_idle": True, "ran": True}


def test_app_maybe_stop_joystick_hold_on_focus_loss(monkeypatch) -> None:
    app = _bare_app()
    app.stop_hold_on_focus_loss = _Var(True)
    app._active_joystick_hold_binding = "hold"
    app.focus_get = lambda: None
    called = {}
    app._stop_joystick_hold = lambda: called.setdefault("stopped", True)

    application.App._maybe_stop_joystick_hold_on_focus_loss(app)

    assert called == {"stopped": True}


def test_app_maybe_stop_joystick_hold_no_action_when_focused() -> None:
    app = _bare_app()
    app.stop_hold_on_focus_loss = _Var(True)
    app._active_joystick_hold_binding = "hold"
    app.focus_get = lambda: object()
    called = {}
    app._stop_joystick_hold = lambda: called.setdefault("stopped", True)

    application.App._maybe_stop_joystick_hold_on_focus_loss(app)

    assert called == {}


def test_app_unit_toggle_label_delegates(monkeypatch) -> None:
    app = _bare_app()
    monkeypatch.setattr(
        application_controls,
        "unit_toggle_label",
        lambda _app, mode=None: f"mode:{mode}",
    )

    assert application.App._unit_toggle_label(app, "inch") == "mode:inch"


def test_app_normalize_override_slider_value_delegates(monkeypatch) -> None:
    app = _bare_app()
    monkeypatch.setattr(
        application_controls,
        "normalize_override_slider_value",
        lambda raw_value, minimum=50, maximum=150: (raw_value, minimum, maximum),
    )

    assert application.App._normalize_override_slider_value(app, 80, minimum=10, maximum=20) == (80, 10, 20)


def test_app_ensure_serial_available_delegates(monkeypatch) -> None:
    app = _bare_app()
    app._serial_available = True
    app._serial_import_error = "missing"
    monkeypatch.setattr(
        application_actions,
        "ensure_serial_available",
        lambda _app, available, err: (available, err),
    )

    assert application.App._ensure_serial_available(app) == (True, "missing")


def test_app_build_toolbar_and_main_delegates(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(application_layout, "build_toolbar", lambda _app: called.setdefault("toolbar", _app))
    monkeypatch.setattr(application_layout, "build_main_layout", lambda _app: called.setdefault("main", _app))
    app._ensure_tooltips = lambda: called.setdefault("tooltips", True)

    application.App._build_toolbar(app)
    application.App._build_main(app)

    assert called == {"toolbar": app, "main": app, "tooltips": True}


def test_app_release_checklist_and_all_stop_offset(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(
        application_layout,
        "open_release_checklist",
        lambda _app: called.setdefault("checklist", _app),
    )
    monkeypatch.setattr(
        application_layout,
        "position_all_stop_offset",
        lambda _app, event=None: called.setdefault("offset", event),
    )

    application.App._show_release_checklist(app)
    application.App._position_all_stop_offset(app, event="evt")

    assert called == {"checklist": app, "offset": "evt"}


def test_app_override_and_dro_delegates(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(
        application_controls,
        "set_override_scale",
        lambda _app, axis, value, lock_var: called.setdefault("set", (axis, value, lock_var)),
    )
    monkeypatch.setattr(
        application_controls,
        "handle_override_slider_change",
        lambda _app, raw, last, scale, lock, display, plus, minus: called.setdefault(
            "handle", (raw, last, scale, lock, display, plus, minus)
        ),
    )
    monkeypatch.setattr(
        application_controls,
        "on_feed_override_slider",
        lambda _app, raw: called.setdefault("feed", raw),
    )
    monkeypatch.setattr(
        application_controls,
        "on_spindle_override_slider",
        lambda _app, raw: called.setdefault("spindle", raw),
    )
    monkeypatch.setattr(
        application_controls,
        "send_override_delta",
        lambda _app, d, p, m: called.setdefault("delta", d),
    )
    monkeypatch.setattr(
        application_controls,
        "set_feed_override_slider_value",
        lambda _app, v: called.setdefault("feed_val", v),
    )
    monkeypatch.setattr(
        application_controls,
        "set_spindle_override_slider_value",
        lambda _app, v: called.setdefault("spindle_val", v),
    )
    monkeypatch.setattr(
        application_controls,
        "refresh_override_info",
        lambda _app: called.setdefault("refresh", True),
    )
    monkeypatch.setattr(
        application_controls,
        "dro_value_row",
        lambda _app, parent, axis, var: called.setdefault("dro_val", f"row:{axis}"),
    )
    monkeypatch.setattr(
        application_controls,
        "dro_row",
        lambda _app, parent, axis, var, zero: (axis, zero),
    )

    application.App._set_override_scale(app, "scale", 10, "lock")
    application.App._handle_override_slider_change(app, 5, "last", "scale", "lock", "var", "+", "-")
    application.App._on_feed_override_slider(app, 90)
    application.App._on_spindle_override_slider(app, 110)
    application.App._send_override_delta(app, 5, "plus", "minus")
    application.App._set_feed_override_slider_value(app, 99)
    application.App._set_spindle_override_slider_value(app, 101)
    application.App._refresh_override_info(app)
    assert application.App._dro_value_row(app, "parent", "X", "var") == "row:X"

    assert application.App._dro_row(app, "parent", "Y", "var", "zero") == ("Y", "zero")
    assert called["set"] == ("scale", 10, "lock")
    assert called["handle"][0] == 5
    assert called["feed"] == 90
    assert called["spindle"] == 110
    assert called["delta"] == 5
    assert called["feed_val"] == 99
    assert called["spindle_val"] == 101
    assert called["refresh"] is True
    assert called["dro_val"] == "row:X"


def test_app_connect_workers_delegate(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(
        application_actions,
        "start_connect_worker",
        lambda _app, port, show_error=True, on_failure=None: called.setdefault(
            "connect", (port, show_error, on_failure)
        ),
    )
    monkeypatch.setattr(
        application_actions,
        "start_disconnect_worker",
        lambda _app: called.setdefault("disconnect", True),
    )

    application.App._start_connect_worker(app, "COM3", show_error=False, on_failure="handler")
    application.App._start_disconnect_worker(app)

    assert called["connect"] == ("COM3", False, "handler")
    assert called["disconnect"] is True


def test_app_dialog_and_gcode_actions_delegate(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(application_gcode, "show_resume_dialog", lambda _app: called.setdefault("resume", True))
    monkeypatch.setattr(application_gcode, "show_auto_level_dialog", lambda _app: called.setdefault("auto", True))
    monkeypatch.setattr(
        application_gcode,
        "show_spoilboard_generator_dialog",
        lambda _app: called.setdefault("spoilboard", True),
    )
    monkeypatch.setattr(application_gcode, "reset_gcode_view_for_run", lambda _app: called.setdefault("reset", True))
    monkeypatch.setattr(application_gcode, "load_gcode_from_path", lambda _app, path: called.setdefault("load", path))
    monkeypatch.setattr(application_gcode, "clear_gcode", lambda _app: called.setdefault("clear", True))

    application.App._show_resume_dialog(app)
    application.App._show_auto_level_dialog(app)
    application.App._show_spoilboard_generator_dialog(app)
    application.App._reset_gcode_view_for_run(app)
    application.App._load_gcode_from_path(app, "file.gcode")
    application.App._clear_gcode(app)

    assert called == {
        "resume": True,
        "auto": True,
        "spoilboard": True,
        "reset": True,
        "load": "file.gcode",
        "clear": True,
    }


def test_app_gcode_loading_helpers_delegate(monkeypatch) -> None:
    app = _bare_app()
    called = {}
    monkeypatch.setattr(application_gcode, "ensure_gcode_loading_popup", lambda _app: called.setdefault("ensure", True))
    monkeypatch.setattr(application_gcode, "show_gcode_loading", lambda _app: called.setdefault("show", True))
    monkeypatch.setattr(application_gcode, "hide_gcode_loading", lambda _app: called.setdefault("hide", True))
    monkeypatch.setattr(
        application_gcode,
        "set_gcode_loading_indeterminate",
        lambda _app, text: called.setdefault("indeterminate", text),
    )
    monkeypatch.setattr(
        application_gcode,
        "set_gcode_loading_progress",
        lambda _app, done, total, name="": called.setdefault("progress", (done, total, name)),
    )
    monkeypatch.setattr(application_gcode, "finish_gcode_loading", lambda _app: called.setdefault("finish", True))

    application.App._ensure_gcode_loading_popup(app)
    application.App._show_gcode_loading(app)
    application.App._hide_gcode_loading(app)
    application.App._set_gcode_loading_indeterminate(app, "Loading")
    application.App._set_gcode_loading_progress(app, 1, 2, "name")
    application.App._finish_gcode_loading(app)

    assert called == {
        "ensure": True,
        "show": True,
        "hide": True,
        "indeterminate": "Loading",
        "progress": (1, 2, "name"),
        "finish": True,
    }


def test_app_formatters_delegate(monkeypatch) -> None:
    app = _bare_app()
    monkeypatch.setattr(application_gcode, "format_throughput", lambda bps: f"{bps} bps")
    monkeypatch.setattr(application_gcode, "estimate_factor_value", lambda _app: 1.5)
    monkeypatch.setattr(
        application_gcode,
        "refresh_gcode_stats_display",
        lambda _app: setattr(_app, "stats_refreshed", True),
    )
    monkeypatch.setattr(
        application_gcode,
        "refresh_dro_display",
        lambda _app: setattr(_app, "dro_refreshed", True),
    )

    assert application.App._format_throughput(app, 10.0) == "10.0 bps"
    assert application.App._estimate_factor_value(app) == 1.5
    application.App._refresh_gcode_stats_display(app)
    application.App._refresh_dro_display(app)

    assert app.stats_refreshed is True
    assert app.dro_refreshed is True
