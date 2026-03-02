import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk
from types import SimpleNamespace

from simple_sender.ui.autolevel_dialog import dialog_controller
from simple_sender.ui.autolevel_dialog.workflow import _apply_auto_level_to_path
from simple_sender.utils.config import DEFAULT_SETTINGS

pytestmark = pytest.mark.ui


class _AutoLevelRunner:
    def __init__(self) -> None:
        self.start_calls = []
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def start(self, *args, **kwargs) -> bool:
        self.start_calls.append((args, kwargs))
        return True


def _walk_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _find_toplevel(root: tk.Tk) -> tk.Toplevel | None:
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.winfo_exists():
            return child
    return None


def _find_widget(widget, klass):
    for child in _walk_widgets(widget):
        if isinstance(child, klass):
            return child
    return None


def _find_button_by_text(widget, text: str) -> ttk.Button | None:
    for child in _walk_widgets(widget):
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            return child
    return None


def _make_app(root: tk.Tk):
    root._gcode_streaming_mode = False
    root._last_gcode_path = "job.gcode"
    root._last_parse_result = SimpleNamespace(
        bounds=(0.0, 10.0, 0.0, 10.0, 0.0, 0.0)
    )
    root._last_gcode_lines = []
    root._gcode_total_lines = 0
    root.auto_level_job_prefs = dict(DEFAULT_SETTINGS["auto_level_job_prefs"])
    root.auto_level_presets = {}
    root.auto_level_settings = dict(DEFAULT_SETTINGS["auto_level_settings"])
    root.settings = {"auto_level_settings": dict(root.auto_level_settings)}
    root.show_autolevel_overlay = tk.BooleanVar(master=root, value=True)
    root.auto_level_runner = _AutoLevelRunner()
    root.connected = True
    root._grbl_ready = True
    root._status_seen = True
    root._alarm_locked = False
    root._auto_level_height_map = None
    root._auto_level_leveled_lines = None
    root._auto_level_leveled_path = None
    root._auto_level_original_lines = None
    root._auto_level_original_path = None
    root._auto_level_grid = None
    root._auto_level_bounds = None
    root._auto_level_restore = None
    root._require_grbl_connection = lambda: True
    root._load_gcode_from_path = lambda *_args, **_kwargs: None
    root._apply_loaded_gcode = lambda *_args, **_kwargs: None
    root.toolpath_panel = SimpleNamespace(
        get_arc_step_rad=lambda _lines: 0.1,
        set_autolevel_overlay=lambda _grid: None,
    )
    return root


def _make_deps(*, center_window_fn, askokcancel=None):
    messagebox_obj = dialog_controller.messagebox
    if askokcancel is not None:
        messagebox_obj = SimpleNamespace(
            askokcancel=askokcancel,
            showerror=messagebox_obj.showerror,
            showwarning=messagebox_obj.showwarning,
            askyesno=messagebox_obj.askyesno,
        )
    return dialog_controller.AutoLevelDialogDependencies(
        tk_module=tk,
        ttk_module=ttk,
        messagebox=messagebox_obj,
        simpledialog=dialog_controller.simpledialog,
        center_window_fn=center_window_fn,
        set_tab_tooltip_fn=dialog_controller.build_auto_level_dialog_dependencies().set_tab_tooltip_fn,
        apply_auto_level_to_path_fn=_apply_auto_level_to_path,
    )


def test_auto_level_dialog_uses_tabs(tk_root, monkeypatch) -> None:
    app = _make_app(tk_root)
    deps = _make_deps(center_window_fn=lambda *_args, **_kwargs: None)

    dialog_controller.show_auto_level_dialog(app, deps)
    dlg = _find_toplevel(tk_root)
    try:
        assert dlg is not None
        notebook = _find_widget(dlg, ttk.Notebook)
        assert notebook is not None
        labels = [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()]
        assert labels == ["Settings", "Avoidance Areas"]
        avoidance_id = notebook.tabs()[1]
        avoidance_tab = notebook.nametowidget(avoidance_id)
        checks = [
            widget
            for widget in _walk_widgets(avoidance_tab)
            if isinstance(widget, ttk.Checkbutton)
        ]
        texts = [check.cget("text") for check in checks]
        assert texts.count("Area 1") == 1
        assert sum(1 for text in texts if text.startswith("Area ")) == 8
    finally:
        if dlg is not None:
            dlg.destroy()


def test_start_probe_warns_when_no_avoidance(tk_root, monkeypatch) -> None:
    app = _make_app(tk_root)
    prompts = []

    def fake_askokcancel(title, message):
        prompts.append((title, message))
        return False

    deps = _make_deps(
        center_window_fn=lambda *_args, **_kwargs: None,
        askokcancel=fake_askokcancel,
    )

    dialog_controller.show_auto_level_dialog(app, deps)
    dlg = _find_toplevel(tk_root)
    try:
        assert dlg is not None
        start_btn = _find_button_by_text(dlg, "Start Probe")
        assert start_btn is not None
        start_btn.invoke()
        assert prompts
        assert "No avoidance areas are configured" in prompts[0][1]
        assert app.auto_level_runner.start_calls == []
    finally:
        if dlg is not None:
            dlg.destroy()
