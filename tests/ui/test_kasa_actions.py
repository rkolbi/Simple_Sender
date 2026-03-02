import pytest

tk = pytest.importorskip("tkinter")

from tkinter import ttk

from simple_sender.kasa_accessory import OutletCommandResult
from simple_sender.ui import kasa_actions
from simple_sender.ui.kasa_actions import (
    handle_stream_spindle_state,
    handle_outgoing_gcode_line,
    refresh_kasa_controls_state,
    start_job_accessories,
    stop_job_accessories,
    toggle_kasa_vacuum_quick,
    validate_kasa_outlet_mapping,
)


pytestmark = pytest.mark.ui


def test_validate_kasa_outlet_mapping_rejects_collision_and_reverts(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _App:
        def __init__(self) -> None:
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.vacuum_outlet = tk.IntVar(master=tk_root, value=1)
            self.light_outlet = tk.IntVar(master=tk_root, value=2)
            self.vacuum_outlet_label = tk.StringVar(master=tk_root, value="Outlet 1")
            self.light_outlet_label = tk.StringVar(master=tk_root, value="Outlet 2")
            self.kasa_validation_var = tk.StringVar(master=tk_root, value="")
            self._kasa_last_valid_outlets = (1, 2)
            self._kasa_outlet_updating = False
            self.logs = []
            self.ui_q = type("Q", (), {"put": lambda _self, evt: self.logs.append(evt)})()

    app = _App()
    app.light_outlet.set(1)
    app.light_outlet_label.set("Outlet 1")

    valid = validate_kasa_outlet_mapping(app, changed="light")

    assert valid is False
    assert app.light_outlet.get() == 2
    assert app.light_outlet_label.get() == "Outlet 2"
    assert "cannot use the same outlet" in app.kasa_validation_var.get().lower()


def test_refresh_kasa_controls_state_disables_test_buttons_without_master_or_device(
    tk_root, monkeypatch
) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")
    frame = ttk.Frame(tk_root)

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=False)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="")
            self._kasa_outlet_count = 2
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.btn_kasa_discover = ttk.Button(frame, text="Discover")
            self.kasa_device_combo = ttk.Combobox(frame, values=("device",), state="readonly")
            self.vacuum_check = ttk.Checkbutton(frame, text="Vacuum")
            self.light_check = ttk.Checkbutton(frame, text="Light")
            self.vacuum_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.light_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.btn_kasa_refresh_outlets = ttk.Button(frame, text="Refresh")
            self.btn_kasa_outlet1_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet1_off = ttk.Button(frame, text="OFF")
            self.btn_kasa_outlet2_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet2_off = ttk.Button(frame, text="OFF")

    app = _App()

    refresh_kasa_controls_state(app)
    assert str(app.btn_kasa_discover.cget("state")) == "disabled"
    assert str(app.btn_kasa_outlet1_on.cget("state")) == "disabled"

    app.kasa_enabled.set(True)
    app.kasa_device_identifier.set("device")
    refresh_kasa_controls_state(app)
    assert str(app.btn_kasa_discover.cget("state")) == "normal"
    assert str(app.btn_kasa_outlet1_on.cget("state")) == "normal"


def test_single_outlet_disables_light_controls(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")
    frame = ttk.Frame(tk_root)

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="device")
            self._kasa_outlet_count = 1
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.btn_kasa_discover = ttk.Button(frame, text="Discover")
            self.kasa_device_combo = ttk.Combobox(frame, values=("device",), state="readonly")
            self.vacuum_check = ttk.Checkbutton(frame, text="Vacuum")
            self.light_check = ttk.Checkbutton(frame, text="Light")
            self.vacuum_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.light_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.btn_kasa_refresh_outlets = ttk.Button(frame, text="Refresh")
            self.btn_kasa_outlet1_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet1_off = ttk.Button(frame, text="OFF")
            self.btn_kasa_outlet2_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet2_off = ttk.Button(frame, text="OFF")

    app = _App()
    refresh_kasa_controls_state(app)

    assert str(app.light_check.cget("state")) == "disabled"
    assert str(app.light_outlet_combo.cget("state")) == "disabled"
    assert str(app.btn_kasa_outlet2_on.cget("state")) == "disabled"


def test_refresh_kasa_controls_state_non_linux_disables_all_controls(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "win32")
    frame = ttk.Frame(tk_root)

    class _App:
        def __init__(self) -> None:
            self.kasa_validation_var = tk.StringVar(master=tk_root, value="")
            self.btn_kasa_discover = ttk.Button(frame, text="Discover")
            self.kasa_device_combo = ttk.Combobox(frame, values=("device",), state="readonly")
            self.vacuum_check = ttk.Checkbutton(frame, text="Vacuum")
            self.light_check = ttk.Checkbutton(frame, text="Light")
            self.vacuum_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.light_outlet_combo = ttk.Combobox(frame, values=("Outlet 1", "Outlet 2"), state="readonly")
            self.btn_kasa_refresh_outlets = ttk.Button(frame, text="Refresh")
            self.btn_kasa_outlet1_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet1_off = ttk.Button(frame, text="OFF")
            self.btn_kasa_outlet2_on = ttk.Button(frame, text="ON")
            self.btn_kasa_outlet2_off = ttk.Button(frame, text="OFF")

    app = _App()
    refresh_kasa_controls_state(app)

    assert "Linux only" in app.kasa_validation_var.get()
    assert str(app.btn_kasa_discover.cget("state")) == "disabled"
    assert str(app.btn_kasa_outlet1_on.cget("state")) == "disabled"


def test_handle_outgoing_gcode_line_uses_raw_stream_line_during_dry_run(
    tk_root, monkeypatch
) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _Detector:
        def __init__(self) -> None:
            self.lines = []

        def detect_state_change(self, line: str):
            self.lines.append(line)
            if "M3" in line.upper():
                return True
            return None

    class _Router:
        def __init__(self) -> None:
            self.states = []

        def on_spindle_state_change(self, state) -> None:
            self.states.append(state)

    class _Source:
        def __getitem__(self, idx: int) -> str:
            assert idx == 7
            return "M3 S12000"

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.dry_run_sanitize_stream = tk.BooleanVar(master=tk_root, value=True)
            self.accessory_router = _Router()
            self.spindle_command_detector = _Detector()
            self._gcode_source = _Source()
            self._last_gcode_lines = ["G1 X10"]

    app = _App()

    handle_outgoing_gcode_line(app, "G1 X10", "stream", line_index=7)

    assert app.spindle_command_detector.lines[-1] == "M3 S12000"
    assert app.accessory_router.states == [True]


def test_handle_outgoing_gcode_line_replays_skipped_stream_indices(
    tk_root, monkeypatch
) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _Detector:
        def __init__(self) -> None:
            self.lines = []

        def detect_state_change(self, line: str):
            self.lines.append(line)
            text = str(line).upper()
            if "M3" in text:
                return True
            if "M5" in text:
                return False
            return None

    class _Router:
        def __init__(self) -> None:
            self.states = []

        def on_spindle_state_change(self, state) -> None:
            self.states.append(bool(state))

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.dry_run_sanitize_stream = tk.BooleanVar(master=tk_root, value=False)
            self.accessory_router = _Router()
            self.spindle_command_detector = _Detector()
            self._gcode_source = None
            self._last_gcode_lines = ["G0 X0", "M3 S12000", "G1 X10", "M5"]
            self._kasa_last_stream_line_index = -1

    app = _App()

    # Simulate UI queue coalescing: only latest gcode_sent event is delivered.
    handle_outgoing_gcode_line(app, "M5", "stream", line_index=3)

    assert app.accessory_router.states == [True, False]
    assert app._kasa_last_stream_line_index == 3


def test_start_and_stop_job_accessories_control_selected_outlets(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _Router:
        def __init__(self) -> None:
            self.calls = []

        def request_outlet_state(self, outlet_id: int, on: bool, *, source: str):
            self.calls.append((int(outlet_id), bool(on), str(source)))
            return True

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="device@192.168.0.2")
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.vacuum_outlet = tk.IntVar(master=tk_root, value=1)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_outlet = tk.IntVar(master=tk_root, value=2)
            self._kasa_outlet_count = 2
            self.accessory_router = _Router()
            self._kasa_job_active_outlets = set()

    app = _App()

    start_job_accessories(app, source="job_run")
    stop_job_accessories(app, source="job_done")

    assert app.accessory_router.calls == [
        (1, True, "job_run"),
        (2, True, "job_run"),
        (1, False, "job_done"),
        (2, False, "job_done"),
    ]
    assert app._kasa_job_active_outlets == set()


def test_handle_stream_spindle_state_is_noop_for_job_lifecycle_mode(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _Router:
        def __init__(self) -> None:
            self.states = []

        def on_spindle_state_change(self, is_on: bool) -> None:
            self.states.append(bool(is_on))

    class _App:
        def __init__(self) -> None:
            self.accessory_router = _Router()

    app = _App()

    handle_stream_spindle_state(app, True)
    handle_stream_spindle_state(app, False)

    assert app.accessory_router.states == []


def test_toggle_kasa_vacuum_quick_requests_mapped_outlet(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _Router:
        def __init__(self) -> None:
            self.calls = []

        def request_outlet_state(self, outlet_id: int, on: bool, *, source: str):
            self.calls.append((int(outlet_id), bool(on), str(source)))
            return True

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="device@192.168.0.2")
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.vacuum_outlet = tk.IntVar(master=tk_root, value=1)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_outlet = tk.IntVar(master=tk_root, value=2)
            self._kasa_outlet_count = 2
            self._kasa_vacuum_quick_on = False
            self._kasa_light_quick_on = False
            self.accessory_router = _Router()
            self.refresh_calls = 0
            self.ui_q = type("Q", (), {"put": lambda *_args, **_kwargs: None})()

        def _refresh_kasa_quick_toggle_text(self) -> None:
            self.refresh_calls += 1

        def _update_quick_button_visibility(self) -> None:
            self.refresh_calls += 1

    app = _App()

    toggle_kasa_vacuum_quick(app)
    toggle_kasa_vacuum_quick(app)

    assert app.accessory_router.calls == [
        (1, True, "quick_vac"),
        (1, False, "quick_vac"),
    ]
    assert app._kasa_vacuum_quick_on is False
    assert app.refresh_calls >= 2


def test_on_kasa_command_result_reverts_failed_quick_toggle(tk_root, monkeypatch) -> None:
    monkeypatch.setattr(kasa_actions.sys, "platform", "linux")

    class _App:
        def __init__(self) -> None:
            self.kasa_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.kasa_device_identifier = tk.StringVar(master=tk_root, value="device@192.168.0.2")
            self.vacuum_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.vacuum_outlet = tk.IntVar(master=tk_root, value=1)
            self.light_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.light_outlet = tk.IntVar(master=tk_root, value=2)
            self._kasa_outlet_count = 2
            self._kasa_vacuum_quick_on = True
            self._kasa_light_quick_on = False
            self.kasa_outlet_1_status = tk.StringVar(master=tk_root, value="")
            self.kasa_outlet_2_status = tk.StringVar(master=tk_root, value="")
            self.refresh_calls = 0
            self.logged = []
            self.ui_q = type("Q", (), {"put": lambda _self, evt: self.logged.append(evt)})()

        def _refresh_kasa_quick_toggle_text(self) -> None:
            self.refresh_calls += 1

        def _update_quick_button_visibility(self) -> None:
            self.refresh_calls += 1

    app = _App()
    result = OutletCommandResult(
        outlet_id=1,
        on=True,
        success=False,
        source="quick_vac",
        error="network timeout",
    )

    kasa_actions.on_kasa_command_result(app, result)

    assert app._kasa_vacuum_quick_on is False
    assert app.refresh_calls >= 1
