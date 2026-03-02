import contextlib

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.events import status as status_events

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _MacroExecutor:
    def __init__(self) -> None:
        self._vars = {}

    @contextlib.contextmanager
    def macro_vars(self):
        yield self._vars


class _ToolpathPanel:
    def __init__(self) -> None:
        self.calls = 0

    def set_position(self, _x, _y, _z) -> None:
        self.calls += 1


def _make_app():
    class _App:
        def __init__(self) -> None:
            self._status_seen = False
            self._homing_in_progress = False
            self._homing_state_seen = False
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self._grbl_ready = True
            self._stream_state = "idle"
            self.connected = True
            self._machine_state_text = ""
            self.machine_state = _Var("")
            self.unit_mode = _Var("mm")
            self._report_units = None
            self.mpos_x = _Var("")
            self.mpos_y = _Var("")
            self.mpos_z = _Var("")
            self.wpos_x = _Var("")
            self.wpos_y = _Var("")
            self.wpos_z = _Var("")
            self._wpos_value_labels = {}
            self._wpos_label_default_fg = {}
            self._wpos_flash_after_ids = {}
            self.macro_executor = _MacroExecutor()
            self.toolpath_panel = _ToolpathPanel()
            self._planner_blocks_capacity = 15

        def _update_state_highlight(self, _text: str) -> None:
            return None

        def _set_manual_controls_enabled(self, _enabled: bool) -> None:
            return None

        def _set_alarm_lock(self, locked: bool, message: str | None = None) -> None:
            self._alarm_locked = locked
            self._alarm_message = message or ""

        def _set_feed_override_slider_value(self, _value: int) -> None:
            return None

        def _set_spindle_override_slider_value(self, _value: int) -> None:
            return None

        def _refresh_override_info(self) -> None:
            return None

        def _update_led_panel(self, _endstop: bool, _probe: bool, _hold: bool) -> None:
            return None

    return _App()


def test_repeated_status_updates_skip_redundant_dro_formatting(monkeypatch) -> None:
    app = _make_app()
    format_calls: list[float] = []

    def _track_format(value: float, _report_units: str, _modal_units: str) -> str:
        format_calls.append(float(value))
        return f"{float(value):.3f}"

    monkeypatch.setattr(status_events, "format_dro_value", _track_format)

    fields = status_events._StatusFields(
        state="Idle",
        wpos="1.000,2.000,3.000",
        mpos="1.100,2.200,3.300",
        wco="0.100,0.200,0.300",
    )

    status_events._update_positions_and_macro_state(app, fields)
    status_events._update_positions_and_macro_state(app, fields)

    # First update formats X/Y/Z for MPos and WPos (6 values); second identical update skips formatting.
    assert len(format_calls) == 6
    assert app.toolpath_panel.calls == 1
