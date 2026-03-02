from types import SimpleNamespace

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import app_commands
from simple_sender.ui import bindings as input_bindings

pytestmark = pytest.mark.ui


class _Bool:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value


class _Grbl:
    def __init__(self) -> None:
        self.calls = []
        self._streaming = False

    def set_dry_run_sanitize(self, value: bool) -> None:
        self.calls.append(("sanitize", value))

    def start_stream(self) -> None:
        self.calls.append("start")
        self._streaming = True

    def pause_stream(self) -> None:
        self.calls.append("pause")

    def resume_stream(self) -> None:
        self.calls.append("resume")
        self._streaming = True

    def stop_stream(self) -> None:
        self.calls.append("stop")
        self._streaming = False

    def is_streaming(self) -> bool:
        return self._streaming


class _App:
    def __init__(self) -> None:
        self.grbl = _Grbl()
        self.dry_run_sanitize_stream = _Bool(True)
        self._job_started_at = None
        self._job_completion_notified = True
        self._reset_called = False
        self._last_gcode_path = "job.nc"
        self._last_parse_result = SimpleNamespace(bounds=(0.0, 10.0, 0.0, 10.0, -1.0, 0.0))
        self._gcode_streaming_mode = False
        self.validate_streaming_gcode = _Bool(True)
        self._gcode_validation_report = SimpleNamespace(line_issue_count=0, grbl_warnings={})
        self._grbl_ready = True
        self._status_seen = True
        self._alarm_locked = False
        self._homing_in_progress = False
        self.settings_controller = None
        self.ui_q = SimpleNamespace(put=lambda *_args, **_kwargs: None)
        self.status = SimpleNamespace(config=lambda **_kwargs: None)

    def _require_grbl_connection(self) -> bool:
        return True

    def _reset_gcode_view_for_run(self) -> None:
        self._reset_called = True


class _FakePygame:
    JOYBUTTONDOWN = 10
    JOYBUTTONUP = 11
    JOYAXISMOTION = 12
    JOYHATMOTION = 13

    class event:
        @staticmethod
        def event_name(_event_type: int) -> str:
            return "JOYBUTTONDOWN"


class _JoystickApp:
    def __init__(self, py) -> None:
        self._py = py
        self._joystick_capture_state = {
            "mode": "binding",
            "binding_id": "jog_x",
            "timer": None,
        }
        self._joystick_bindings = {}
        self._joystick_safety_binding = None
        self._joystick_safety_active = False
        self._joystick_axis_active = set()
        self._joystick_hat_active = set()
        self.joystick_bindings_enabled = _Bool(True)
        self._clear_duplicate_called = None
        self._apply_called = False

    def _get_pygame_module(self):
        return self._py

    def _describe_joystick_event(self, _event):
        return None

    def _set_joystick_event_status(self, _text: str) -> None:
        return None

    def _joystick_binding_key(self, binding):
        return input_bindings.joystick_binding_key(self, binding)

    def _joystick_binding_from_event(self, key):
        return input_bindings.joystick_binding_from_event(self, key)

    def _clear_duplicate_joystick_binding(self, key, binding_id: str) -> None:
        self._clear_duplicate_called = (key, binding_id)

    def _apply_keyboard_bindings(self) -> None:
        self._apply_called = True

    def _reset_joystick_axis_state(self, _joy, _axis) -> None:
        return None

    def _reset_joystick_hat_state(self, _joy, _hat) -> None:
        return None


def test_stream_controls_call_grbl() -> None:
    app = _App()

    app_commands.run_job(app)
    assert app._reset_called
    assert ("sanitize", True) in app.grbl.calls
    assert "start" in app.grbl.calls
    assert app._job_started_at is not None
    assert app._job_completion_notified is False

    app_commands.pause_job(app)
    app_commands.resume_job(app)
    app_commands.stop_job(app)

    assert app.grbl.calls.count("pause") == 1
    assert app.grbl.calls.count("resume") == 1
    assert app.grbl.calls.count("stop") == 1


def test_joystick_capture_maps_button_binding() -> None:
    py = _FakePygame()
    app = _JoystickApp(py)
    event = SimpleNamespace(type=py.JOYBUTTONDOWN, joy=1, button=2)

    input_bindings.handle_joystick_event(app, event)

    assert app._joystick_capture_state is None
    assert app._joystick_bindings["jog_x"] == {
        "kind": "button",
        "joy_id": 1,
        "index": 2,
    }
    assert app._clear_duplicate_called == (("button", 1, 2), "jog_x")
    assert app._apply_called
