import pytest

tk = pytest.importorskip("tkinter")

from tkinter import ttk

from simple_sender.ui.settings import sections_controls
from simple_sender.utils.constants import CURRENT_LINE_CHOICES

pytestmark = pytest.mark.ui


class _SectionApp:
    def __init__(self, tk_root) -> None:
        self.calls: dict[str, int] = {}
        self.jog_feed_xy = tk.StringVar(master=tk_root, value="4000")
        self.jog_feed_z = tk.StringVar(master=tk_root, value="500")
        self.keyboard_bindings_enabled = tk.BooleanVar(master=tk_root, value=True)
        self.joystick_test_status = tk.StringVar(master=tk_root, value="")
        self.joystick_device_status = tk.StringVar(master=tk_root, value="")
        self.joystick_event_status = tk.StringVar(master=tk_root, value="")
        self.joystick_safety_enabled = tk.BooleanVar(master=tk_root, value=False)
        self.joystick_safety_status = tk.StringVar(master=tk_root, value="")
        self.stop_hold_on_focus_loss = tk.BooleanVar(master=tk_root, value=True)
        self.joystick_live_status = tk.StringVar(master=tk_root, value="")
        self.keyboard_live_status = tk.StringVar(master=tk_root, value="")
        self.current_line_mode = tk.StringVar(master=tk_root, value="sent")
        self.toolpath_streaming_render_interval = tk.DoubleVar(master=tk_root, value=0.25)
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

    def _on_jog_feed_change_xy(self, _event=None) -> None:
        self._mark("jog_xy")

    def _on_jog_feed_change_z(self, _event=None) -> None:
        self._mark("jog_z")

    def _apply_safe_mode_profile(self) -> None:
        self._mark("safe_mode")

    def _on_keyboard_bindings_check(self) -> None:
        self._mark("kb_toggle")

    def _on_kb_table_double_click(self, _event) -> None:
        self._mark("kb_double")

    def _on_kb_table_click(self, _event) -> None:
        self._mark("kb_click")

    def _refresh_joystick_test_info(self) -> None:
        self._mark("joy_refresh")

    def _toggle_joystick_bindings(self) -> None:
        self._mark("joy_toggle")

    def _on_joystick_safety_toggle(self) -> None:
        self._mark("joy_safety_toggle")

    def _start_joystick_safety_capture(self) -> None:
        self._mark("joy_capture")

    def _clear_joystick_safety_binding(self) -> None:
        self._mark("joy_clear")

    def _refresh_joystick_safety_display(self) -> None:
        self._mark("joy_safety_display")

    def _on_current_line_mode_change(self, _event=None) -> None:
        self._mark("current_line_change")

    def _sync_current_line_mode_combo(self) -> None:
        self._mark("current_line_sync")

    def _apply_toolpath_streaming_render_interval(self, _event=None) -> None:
        self._mark("toolpath_interval")

    def _on_kasa_master_change(self) -> None:
        self._mark("kasa_master")

    def _discover_kasa_devices(self) -> None:
        self._mark("kasa_discover")

    def _on_kasa_device_selected(self, _event=None) -> None:
        self._mark("kasa_selected")

    def _on_kasa_mapping_change(self, changed=None) -> None:
        self._mark(f"kasa_mapping:{changed}")

    def _refresh_kasa_outlet_list(self) -> None:
        self._mark("kasa_refresh_outlets")

    def _test_kasa_outlet(self, outlet_id: int, on: bool) -> None:
        self._mark(f"kasa_test:{outlet_id}:{on}")

    def _refresh_kasa_controls_state(self) -> None:
        self._mark("kasa_refresh_controls")


def test_build_jogging_section_wires_safe_mode_button(tk_root) -> None:
    app = _SectionApp(tk_root)
    parent = ttk.Frame(tk_root)

    row = sections_controls.build_jogging_section(app, parent, row=2)
    app.btn_safe_mode_profile.invoke()

    assert row == 3
    assert app.calls["safe_mode"] == 1
    assert app.jog_feed_xy_entry.cget("width") == 12
    assert app.jog_feed_z_entry.cget("width") == 12


def test_build_keyboard_shortcuts_section_assigns_controls_and_refreshes_safety(tk_root) -> None:
    app = _SectionApp(tk_root)
    parent = ttk.Frame(tk_root)

    row = sections_controls.build_keyboard_shortcuts_section(app, parent, row=0)
    app.btn_toggle_joystick_bindings.invoke()
    app.btn_refresh_joysticks.invoke()

    assert row == 1
    assert app.calls["joy_safety_display"] == 1
    assert app.calls["joy_toggle"] == 1
    assert app.calls["joy_refresh"] == 1
    assert app.kb_table["columns"] == ("button", "axis", "key", "joystick", "clear")
    assert app.btn_toggle_joystick_bindings._kb_id == "toggle_joystick_bindings"


def test_build_viewer_section_sets_combo_values_and_syncs_mode(tk_root) -> None:
    app = _SectionApp(tk_root)
    parent = ttk.Frame(tk_root)

    row = sections_controls.build_viewer_section(app, parent, row=4)

    assert row == 5
    assert app.calls["current_line_sync"] == 1
    assert app.current_line_combo["values"] == tuple(label for label, _ in CURRENT_LINE_CHOICES)


def test_build_kasa_section_wires_discovery_and_test_buttons(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(sections_controls.sys, "platform", "linux")
    app = _SectionApp(tk_root)
    parent = ttk.Frame(tk_root)

    row = sections_controls.build_kasa_plug_section(app, parent, row=7)
    app.btn_kasa_discover.invoke()
    app.btn_kasa_refresh_outlets.invoke()
    app.btn_kasa_outlet1_on.invoke()
    app.btn_kasa_outlet2_off.invoke()

    assert row == 8
    assert app.calls["kasa_refresh_controls"] == 1
    assert app.calls["kasa_discover"] == 1
    assert app.calls["kasa_refresh_outlets"] == 1
    assert app.calls["kasa_test:1:True"] == 1
    assert app.calls["kasa_test:2:False"] == 1


def test_build_kasa_section_skips_non_linux(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(sections_controls.sys, "platform", "win32")
    app = _SectionApp(tk_root)
    parent = ttk.Frame(tk_root)

    row = sections_controls.build_kasa_plug_section(app, parent, row=7)

    assert row == 7
    assert not hasattr(app, "btn_kasa_discover")
