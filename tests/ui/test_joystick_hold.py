import time

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.bindings import joystick
from simple_sender.ui.bindings import joystick_hold

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Joy:
    def __init__(self, pressed: bool = True) -> None:
        self._pressed = pressed

    def get_button(self, _index: int) -> int:
        return int(self._pressed)

    def get_axis(self, _index: int) -> float:
        return 0.0

    def get_hat(self, _index: int):
        return (0, 0)


class _Grbl:
    def __init__(self) -> None:
        self.jogs: list[tuple[float, float, float, float, str, str | None]] = []
        self.jog_cancel_calls = 0
        self.cancel_pending_calls = 0
        self.hold_calls = 0

    def manual_queue_backpressure(self) -> bool:
        return False

    def manual_queue_busy(self) -> bool:
        return False

    def jog(
        self,
        dx: float,
        dy: float,
        dz: float,
        feed: float,
        unit_mode: str,
        *,
        source: str | None = None,
    ) -> None:
        self.jogs.append((dx, dy, dz, feed, unit_mode, source))

    def jog_cancel(self) -> None:
        self.jog_cancel_calls += 1

    def cancel_pending_jogs(self) -> None:
        self.cancel_pending_calls += 1

    def hold(self) -> None:
        self.hold_calls += 1


def test_max_hold_distance_uses_axis_limits() -> None:
    class _Settings:
        _settings_data = {"$130": ("300", 130), "$22": ("0", 22)}

    class _App:
        unit_mode = _Var("mm")
        settings_controller = _Settings()
        _report_units = "mm"
        _mpos_raw = (100.0, 0.0, 0.0)

    app = _App()

    distance = joystick_hold.max_hold_distance(app, "X", 1)

    assert distance == pytest.approx(199.75)


def test_max_hold_distance_uses_negative_machine_space_when_homing_enabled() -> None:
    class _Settings:
        _settings_data = {"$130": ("300", 130), "$22": ("1", 22)}

    class _App:
        unit_mode = _Var("mm")
        settings_controller = _Settings()
        _report_units = "mm"
        _mpos_raw = (-100.0, 0.0, 0.0)

    app = _App()

    distance_plus = joystick_hold.max_hold_distance(app, "X", 1)
    distance_minus = joystick_hold.max_hold_distance(app, "X", -1)

    assert distance_plus == pytest.approx(99.75)
    assert distance_minus == pytest.approx(199.75)


def test_start_hold_sends_single_long_jog() -> None:
    class _Settings:
        _settings_data = {"$130": ("300", 130)}

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = None
            self._joystick_hold_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = None
            self._joystick_hold_jog_sent = False
            self._machine_state_text = "Idle"
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {"jog_hold_x_plus": {"kind": "button", "joy_id": 0, "index": 0}}
            self._joystick_instances = {0: _Joy(pressed=True)}
            self.jog_feed_xy = _Var(3000.0)
            self.jog_feed_z = _Var(400.0)
            self.unit_mode = _Var("mm")
            self.settings_controller = _Settings()
            self._report_units = "mm"
            self._mpos_raw = (100.0, 0.0, 0.0)
            self.grbl = _Grbl()
            self._send_hold_jog = lambda: joystick_hold.send_hold_jog(self)

        def _get_pygame_module(self):
            return None

        def after(self, _delay_ms: int, _func):
            self._joystick_hold_after_id = "after_id"
            return self._joystick_hold_after_id

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_after_id = None

    app = _App()

    joystick_hold.start_hold(app, "jog_hold_x_plus")
    joystick_hold.send_hold_jog(app)

    assert len(app.grbl.jogs) == 1
    dx, dy, dz, feed, unit_mode, source = app.grbl.jogs[0]
    assert dy == 0.0 and dz == 0.0
    assert dx > 100.0
    assert feed == 3000.0
    assert unit_mode == "mm"
    assert source == "joystick"


def test_poll_joystick_events_stops_hold_when_devices_lost() -> None:
    class _Event:
        @staticmethod
        def pump() -> None:
            return None

        @staticmethod
        def get():
            return []

    class _Py:
        event = _Event
        JOYDEVICEADDED = 1
        JOYDEVICEREMOVED = 2

    class _App:
        def __init__(self) -> None:
            self._joystick_poll_id = None
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_instances = {}
            self._joystick_capture_state = None
            self.joystick_bindings_enabled = _Var(False)
            self.joystick_safety_enabled = _Var(False)
            self._stop_called = False

        def _get_pygame_module(self):
            return _Py

        def _ensure_joystick_backend(self):
            return True

        def _handle_joystick_event(self, _event) -> None:
            return None

        def _stop_joystick_hold(self, _binding_id=None) -> None:
            self._stop_called = True

        def after(self, _delay_ms: int, _func):
            return "after_id"

    app = _App()

    joystick.poll_joystick_events(
        app,
        maybe_refresh_joystick_devices=lambda *_args, **_kwargs: True,
        update_joystick_live_status=lambda *_args, **_kwargs: None,
        joystick_safety_ready=lambda *_args, **_kwargs: True,
    )

    assert app._stop_called


def test_send_hold_jog_failure_does_not_mark_sent_and_retries() -> None:
    class _Settings:
        _settings_data = {"$130": ("300", 130)}

    class _FailingGrbl(_Grbl):
        def jog(
            self,
            dx: float,
            dy: float,
            dz: float,
            feed: float,
            unit_mode: str,
            *,
            source: str | None = None,
        ) -> None:
            raise RuntimeError("jog failed")

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = None
            self._joystick_hold_jog_sent = False
            self._machine_state_text = "Idle"
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {"jog_hold_x_plus": {"kind": "button", "joy_id": 0, "index": 0}}
            self._joystick_instances = {0: _Joy(pressed=True)}
            self.jog_feed_xy = _Var(3000.0)
            self.jog_feed_z = _Var(400.0)
            self.unit_mode = _Var("mm")
            self.settings_controller = _Settings()
            self._report_units = "mm"
            self._mpos_raw = (100.0, 0.0, 0.0)
            self.grbl = _FailingGrbl()
            self._send_hold_jog = lambda: joystick_hold.send_hold_jog(self)

        def _get_pygame_module(self):
            return None

        def after(self, _delay_ms: int, _func):
            self._joystick_hold_after_id = "after_id"
            return self._joystick_hold_after_id

    app = _App()

    joystick_hold.send_hold_jog(app)

    assert app._joystick_hold_jog_sent is False
    assert app._joystick_hold_after_id == "after_id"


def test_check_release_stops_axis_hold_when_stick_recenters_with_drift() -> None:
    class _AxisJoy:
        def __init__(self, value: float) -> None:
            self._value = float(value)

        def get_axis(self, _index: int) -> float:
            return self._value

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = None
            self._joystick_hold_jog_sent = True
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {
                "jog_hold_x_plus": {
                    "kind": "axis",
                    "joy_id": 0,
                    "index": 0,
                    "direction": 1,
                }
            }
            self._joystick_instances = {0: _AxisJoy(0.6)}
            self.grbl = _Grbl()

        def _get_pygame_module(self):
            return None

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_after_id = None

    app = _App()

    joystick_hold.check_release(app)

    assert app._active_joystick_hold_binding is None
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS
    assert app.grbl.cancel_pending_calls == 1


def test_send_hold_jog_stops_immediately_when_axis_released() -> None:
    class _Settings:
        _settings_data = {"$130": ("300", 130)}

    class _AxisJoy:
        def __init__(self, value: float) -> None:
            self._value = float(value)

        def get_axis(self, _index: int) -> float:
            return self._value

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = None
            self._joystick_hold_jog_sent = False
            self._machine_state_text = "Idle"
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {
                "jog_hold_x_plus": {
                    "kind": "axis",
                    "joy_id": 0,
                    "index": 0,
                    "direction": 1,
                }
            }
            self._joystick_instances = {0: _AxisJoy(0.6)}
            self.jog_feed_xy = _Var(3000.0)
            self.jog_feed_z = _Var(400.0)
            self.unit_mode = _Var("mm")
            self.settings_controller = _Settings()
            self._report_units = "mm"
            self._mpos_raw = (100.0, 0.0, 0.0)
            self.grbl = _Grbl()
            self._send_hold_jog = lambda: joystick_hold.send_hold_jog(self)

        def _get_pygame_module(self):
            return None

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_after_id = None

    app = _App()

    joystick_hold.send_hold_jog(app)

    assert app._active_joystick_hold_binding is None
    assert app.grbl.jogs == []
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS


def test_check_release_deadman_cancels_hold_after_poll_gap() -> None:
    class _AxisJoy:
        def __init__(self, value: float) -> None:
            self._value = float(value)

        def get_axis(self, _index: int) -> float:
            return self._value

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_fallback_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = time.monotonic() - 1.0
            self._joystick_hold_jog_sent = True
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {
                "jog_hold_x_plus": {
                    "kind": "axis",
                    "joy_id": 0,
                    "index": 0,
                    "direction": 1,
                }
            }
            self._joystick_instances = {0: _AxisJoy(1.0)}
            self.grbl = _Grbl()

        def _get_pygame_module(self):
            return None

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_after_id = None

    app = _App()

    joystick_hold.check_release(app)

    assert app._active_joystick_hold_binding is None
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS


def test_stop_hold_schedules_jog_cancel_fallback_for_stuck_jog_state() -> None:
    callbacks = {}

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_fallback_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = time.monotonic()
            self._joystick_hold_jog_sent = True
            self._machine_state_text = "Jog"
            self.grbl = _Grbl()

        def after(self, delay_ms: int, callback):
            callbacks["delay_ms"] = delay_ms
            callbacks["callback"] = callback
            self._joystick_hold_fallback_after_id = "fallback-id"
            return self._joystick_hold_fallback_after_id

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_fallback_after_id = None

    app = _App()

    joystick_hold.stop_hold(app)

    assert callbacks["delay_ms"] == joystick_hold.JOYSTICK_HOLD_FEED_HOLD_FALLBACK_DELAY_MS
    callbacks["callback"]()
    assert app.grbl.hold_calls == 0
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS * 2
    assert app.grbl.cancel_pending_calls == 2


def test_release_path_uses_jog_cancel_only_and_never_feed_hold() -> None:
    callbacks = {}

    class _ReleasedJoy:
        def get_button(self, _index: int) -> int:
            return 0

    class _App:
        def __init__(self) -> None:
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_fallback_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = time.monotonic()
            self._joystick_hold_jog_sent = True
            self.joystick_bindings_enabled = _Var(True)
            self._joystick_bindings = {
                "jog_hold_x_plus": {"kind": "button", "joy_id": 0, "index": 1}
            }
            self._joystick_instances = {0: _ReleasedJoy()}
            self.grbl = _Grbl()

        def _get_pygame_module(self):
            return None

        def after(self, delay_ms: int, callback):
            callbacks["delay_ms"] = delay_ms
            callbacks["callback"] = callback
            self._joystick_hold_fallback_after_id = "fallback-id"
            return self._joystick_hold_fallback_after_id

        def after_cancel(self, _after_id) -> None:
            self._joystick_hold_fallback_after_id = None

    app = _App()

    joystick_hold.check_release(app)

    assert app.grbl.hold_calls == 0
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS
    assert app.grbl.cancel_pending_calls == 1

    callbacks["callback"]()

    assert app.grbl.hold_calls == 0
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS * 2
    assert app.grbl.cancel_pending_calls == 2


def test_poll_joystick_events_stops_hold_when_release_event_is_missing() -> None:
    class _Event:
        @staticmethod
        def pump() -> None:
            return None

        @staticmethod
        def get():
            # Simulate dropped release events from the backend.
            return []

    class _Py:
        event = _Event
        JOYDEVICEADDED = 1
        JOYDEVICEREMOVED = 2

    class _ReleasedJoy:
        def get_button(self, _index: int) -> int:
            return 0

    class _App:
        def __init__(self) -> None:
            self._joystick_poll_id = None
            self._active_joystick_hold_binding = "jog_hold_x_plus"
            self._joystick_hold_after_id = None
            self._joystick_hold_fallback_after_id = None
            self._joystick_hold_missed_polls = 0
            self._joystick_hold_last_ts = time.monotonic()
            self._joystick_hold_jog_sent = True
            self._joystick_capture_state = None
            self._joystick_instances = {0: _ReleasedJoy()}
            self._joystick_bindings = {
                "jog_hold_x_plus": {
                    "kind": "button",
                    "joy_id": 0,
                    "index": 1,
                }
            }
            self.joystick_bindings_enabled = _Var(True)
            self.joystick_safety_enabled = _Var(False)
            self.grbl = _Grbl()

        def _get_pygame_module(self):
            return _Py

        def _ensure_joystick_backend(self):
            return True

        def _handle_joystick_event(self, _event) -> None:
            return None

        def _poll_joystick_events(self) -> None:
            return None

        def after(self, _delay_ms: int, _func):
            return "after-id"

        def after_cancel(self, _after_id) -> None:
            return None

    app = _App()

    joystick.poll_joystick_events(
        app,
        maybe_refresh_joystick_devices=lambda *_args, **_kwargs: True,
        update_joystick_live_status=lambda *_args, **_kwargs: None,
        joystick_safety_ready=lambda *_args, **_kwargs: True,
    )

    assert app._active_joystick_hold_binding is None
    assert app.grbl.jog_cancel_calls == joystick_hold.JOYSTICK_HOLD_CANCEL_ATTEMPTS
