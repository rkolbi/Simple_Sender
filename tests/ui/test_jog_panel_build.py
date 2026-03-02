import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

from simple_sender.ui.controls import jog_panel

pytestmark = pytest.mark.ui


class _Grbl:
    def __init__(self) -> None:
        self.connected = True
        self.jog_calls: list[tuple[float, float, float, float, str, str]] = []
        self.jog_cancel_calls = 0
        self.cancel_pending_calls = 0

    def is_connected(self) -> bool:
        return self.connected

    def jog(self, dx: float, dy: float, dz: float, feed: float, unit_mode: str, *, source: str = "jog") -> None:
        self.jog_calls.append((dx, dy, dz, feed, unit_mode, source))

    def jog_cancel(self) -> None:
        self.jog_cancel_calls += 1

    def cancel_pending_jogs(self) -> None:
        self.cancel_pending_calls += 1


class _Stream:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, line: str) -> None:
        self.logs.append(str(line))


class _MacroPanel:
    def __init__(self) -> None:
        self.calls = 0

    def attach_frames(self, parent, _secondary) -> None:
        self.calls += 1
        ttk.Label(parent, text="Macro").pack()


class _JogApp(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.grbl = _Grbl()
        self.streaming_controller = _Stream()
        self.macro_panel = _MacroPanel()
        self._manual_controls: list[object] = []
        self._offline_controls: set[object] = set()
        self._manual_input_source = None
        self.step_xy = tk.DoubleVar(master=master, value=1.0)
        self.step_z = tk.DoubleVar(master=master, value=0.1)
        self.jog_feed_xy = tk.DoubleVar(master=master, value=2000.0)
        self.jog_feed_z = tk.DoubleVar(master=master, value=250.0)
        self.unit_mode = tk.StringVar(master=master, value="mm")
        self.mpos_x = tk.StringVar(master=master, value="0.000")
        self.mpos_y = tk.StringVar(master=master, value="0.000")
        self.mpos_z = tk.StringVar(master=master, value="0.000")
        self.wpos_x = tk.StringVar(master=master, value="0.000")
        self.wpos_y = tk.StringVar(master=master, value="0.000")
        self.wpos_z = tk.StringVar(master=master, value="0.000")
        self.tool_reference_var = tk.StringVar(master=master, value="TRef: 0.00")
        self._all_stop_calls = 0

    def _dro_value_row(
        self,
        parent,
        label: str,
        variable,
        grid_info=None,
        action_text: str | None = None,
        action_cmd=None,
        action_kb_id: str | None = None,
    ):
        row = ttk.Frame(parent)
        ttk.Label(row, text=label).pack(side="left")
        ttk.Label(row, textvariable=variable).pack(side="left")
        btn = ttk.Button(row, text=action_text or "", command=action_cmd)
        btn.pack(side="left")
        if action_kb_id:
            btn._kb_id = action_kb_id
        if grid_info:
            row.grid(**grid_info)
        return btn

    def _dro_row(self, parent, label: str, variable, command, grid_info=None):
        row = ttk.Frame(parent)
        ttk.Label(row, text=label).pack(side="left")
        ttk.Label(row, textvariable=variable).pack(side="left")
        btn = ttk.Button(row, text="Zero", command=command)
        btn.pack(side="left")
        if grid_info:
            row.grid(**grid_info)
        return btn

    def _unit_toggle_label(self) -> str:
        return f"Units: {self.unit_mode.get()}"

    def _toggle_unit_mode(self) -> None:
        self.unit_mode.set("inch" if self.unit_mode.get() == "mm" else "mm")

    def _update_unit_toggle_display(self) -> None:
        if hasattr(self, "btn_unit_toggle"):
            self.btn_unit_toggle.config(text=self._unit_toggle_label())

    def _refresh_zeroing_ui(self) -> None:
        return None

    def zero_x(self) -> None:
        self.wpos_x.set("0.000")

    def zero_y(self) -> None:
        self.wpos_y.set("0.000")

    def zero_z(self) -> None:
        self.wpos_z.set("0.000")

    def zero_all(self) -> None:
        self.zero_x()
        self.zero_y()
        self.zero_z()

    def goto_zero(self) -> None:
        self.streaming_controller.log("goto zero")

    def _position_all_stop_offset(self, _event=None) -> None:
        if hasattr(self, "_all_stop_slot") and hasattr(self, "btn_all_stop"):
            self.btn_all_stop.place(in_=self._all_stop_slot, relx=0.5, rely=0.5, anchor="center")

    def _all_stop_action(self) -> None:
        self._all_stop_calls += 1

    def _all_stop_gcode_label(self) -> str:
        return "RT 0x18"

    def _stop_joystick_hold(self) -> None:
        return None

    def _set_step_xy(self, value: float) -> None:
        self.step_xy.set(float(value))
        if hasattr(self, "_xy_step_values") and hasattr(self, "_xy_step_index"):
            idx = jog_panel._nearest_step_index(self._xy_step_values, float(value))
            self._xy_step_index.set(idx)

    def _set_step_z(self, value: float) -> None:
        self.step_z.set(float(value))
        if hasattr(self, "_z_step_values") and hasattr(self, "_z_step_index"):
            idx = jog_panel._nearest_step_index(self._z_step_values, float(value))
            self._z_step_index.set(idx)

    def _set_unit_mode(self, mode: str) -> None:
        self.unit_mode.set(str(mode))
        self._update_unit_toggle_display()


def test_build_jog_panel_wires_controls_and_uses_expected_feed_selection(tk_root) -> None:
    app = _JogApp(tk_root)
    parent = ttk.Frame(app)
    parent.pack(fill="both", expand=True)

    jog_panel.build_jog_panel(app, parent)
    tk_root.update_idletasks()

    app.btn_jog_x_plus.invoke()
    app.btn_jog_z_plus.invoke()
    app.btn_all_stop.invoke()
    app.btn_jog_cancel.invoke()

    assert app.macro_panel.calls == 1
    assert hasattr(app, "mpos_rpm")
    assert app.mpos_rpm.get() == "0"
    assert getattr(app.btn_unit_toggle, "_kb_id", "") == "unit_toggle"
    assert app._all_stop_calls == 1
    assert app.grbl.jog_cancel_calls == 1
    assert app.grbl.cancel_pending_calls == 1
    assert app.grbl.jog_calls[0][3] == 2000.0
    assert app.grbl.jog_calls[1][3] == 250.0
    zero_button_row = app.btn_goto_zero.master
    button_children = [widget for widget in zero_button_row.winfo_children() if isinstance(widget, ttk.Button)]
    assert button_children[:2] == [app.btn_goto_zero, app.btn_zero_all]


def test_build_jog_panel_logs_when_disconnected(tk_root) -> None:
    app = _JogApp(tk_root)
    parent = ttk.Frame(app)
    parent.pack(fill="both", expand=True)
    jog_panel.build_jog_panel(app, parent)
    tk_root.update_idletasks()

    app.grbl.connected = False
    app.btn_jog_x_minus.invoke()

    assert app.grbl.jog_calls == []
    assert any("Jog ignored - GRBL is not connected." in line for line in app.streaming_controller.logs)


def test_build_jog_panel_jog_mpos_to_buttons_invoke_keypad_and_send_absolute_target_jog(
    tk_root,
    monkeypatch,
) -> None:
    app = _JogApp(tk_root)
    parent = ttk.Frame(app)
    parent.pack(fill="both", expand=True)
    app._mpos_raw = (10.0, 20.0, 30.0)
    app._report_units = "mm"

    keypad_calls: list[tuple[str, str]] = []

    def _fake_prompt(_parent, **kwargs):
        keypad_calls.append((kwargs.get("title", ""), kwargs.get("initial_value", "")))
        kwargs["on_apply"]("14.500")

    monkeypatch.setattr(jog_panel, "prompt_numeric_keypad", _fake_prompt)

    jog_panel.build_jog_panel(app, parent)
    tk_root.update_idletasks()

    app.btn_jog_mpos_x_to.invoke()

    assert keypad_calls == [("Jog X to (MPos)", "10.000")]
    assert app.grbl.jog_calls[-1] == (4.5, 0.0, 0.0, 2000.0, "mm", "jog_to_target")


def test_build_jog_panel_jog_mpos_to_accepts_negative_targets_for_z_axis(
    tk_root,
    monkeypatch,
) -> None:
    app = _JogApp(tk_root)
    parent = ttk.Frame(app)
    parent.pack(fill="both", expand=True)
    app._mpos_raw = (0.0, 0.0, 2.0)
    app._report_units = "mm"

    def _fake_prompt(_parent, **kwargs):
        kwargs["on_apply"]("-1.000")

    monkeypatch.setattr(jog_panel, "prompt_numeric_keypad", _fake_prompt)

    jog_panel.build_jog_panel(app, parent)
    tk_root.update_idletasks()

    app.btn_jog_mpos_z_to.invoke()

    assert app.grbl.jog_calls[-1] == (0.0, 0.0, -3.0, 250.0, "mm", "jog_to_target")
