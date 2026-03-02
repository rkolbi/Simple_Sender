import pytest

from simple_sender.ui import grbl_lifecycle

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Widget:
    def __init__(self) -> None:
        self.state = None
        self.text = ""

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]


class _Grbl:
    def __init__(self) -> None:
        self.interval = None
        self._ports = ["COM3"]
        self.loaded = None
        self.prime_calls = []

    def set_status_poll_interval(self, value: float) -> None:
        self.interval = value

    def list_ports(self) -> list[str]:
        return list(self._ports)

    def load_gcode(self, payload, *, name=None) -> None:
        self.loaded = (payload, name)

    def prime_gcode_send_cache(self, lines) -> None:
        self.prime_calls.append(list(lines))


def _make_app() -> object:
    class _App:
        def __init__(self) -> None:
            self.connected = False
            self._homing_in_progress = False
            self._homing_state_seen = False
            self._auto_reconnect_last_port = "COM3"
            self._auto_reconnect_pending = False
            self._auto_reconnect_last_attempt = 0.0
            self._auto_reconnect_retry = 0
            self._auto_reconnect_delay = 3.0
            self._auto_reconnect_next_ts = 0.0
            self._report_units = None
            self._grbl_ready = False
            self._alarm_locked = False
            self._alarm_message = ""
            self._pending_settings_refresh = False
            self._status_seen = False
            self._connected_port = None
            self._user_disconnect = False
            self._rapid_rates = None
            self._rapid_rates_source = None
            self._accel_rates = None
            self._last_gcode_lines = []
            self._gcode_source = None
            self._gcode_streaming_mode = False
            self._last_gcode_path = "job.gcode"
            self._closing = False
            self._connecting = False
            self._auto_reconnect_max_retry = 3
            self.grbl = _Grbl()
            self.btn_conn = _Widget()
            self.btn_stop = _Widget()
            self.btn_run = _Widget()
            self.btn_pause = _Widget()
            self.btn_resume = _Widget()
            self.btn_resume_from = _Widget()
            self.btn_alarm_recover = _Widget()
            self.machine_state = _Var("")
            self.status = _Widget()
            self.throughput_var = _Var("")
            self.reconnect_on_open = _Var(True)
            self.current_port = _Var("")

        def _update_unit_toggle_display(self) -> None:
            return None

        def _update_state_highlight(self, _text: str) -> None:
            return None

        def _set_manual_controls_enabled(self, _enabled: bool) -> None:
            return None

        def _update_gcode_stats(self, _lines) -> None:
            self._stats_updated = True

        def _send_manual(self, _cmd: str, _source: str) -> None:
            self._manual_sent = True

        def _handle_auto_reconnect_failure(self, _exc: Exception) -> None:
            return None

        def _start_connect_worker(self, _port: str, **_kwargs) -> None:
            self._connect_called = _port

    return _App()


def test_handle_connection_event_connects() -> None:
    app = _make_app()

    grbl_lifecycle.handle_connection_event(app, True, "COM3")

    assert app.connected
    assert app._connected_port == "COM3"
    assert "Connected" in app.status.text
    assert app.btn_conn.text.endswith("Disconnect")


def test_handle_connection_event_disconnects() -> None:
    app = _make_app()
    app.connected = True
    app._last_gcode_lines = ["G0 X0"]

    grbl_lifecycle.handle_connection_event(app, False, None)

    assert app.connected is False
    assert app.status.text == "Disconnected"
    assert app.btn_conn.text.endswith("Connect")
    assert app._auto_reconnect_pending
    assert getattr(app, "_stats_updated", False)


def test_handle_ready_event_sends_status_requests() -> None:
    app = _make_app()
    app.connected = True
    app._connected_port = "COM3"

    grbl_lifecycle.handle_ready_event(app, True)

    assert app._grbl_ready
    assert getattr(app, "_manual_sent", False)


def test_handle_connection_event_primes_cache_for_non_preview_source() -> None:
    app = _make_app()
    app._gcode_source = object()
    app._gcode_streaming_mode = False
    app._last_gcode_lines = ["G0 X0", "G1 X1"]
    app._last_gcode_path = "job.gcode"

    grbl_lifecycle.handle_connection_event(app, True, "COM3")

    assert app.grbl.loaded == (app._gcode_source, "job.gcode")
    assert app.grbl.prime_calls == [["G0 X0", "G1 X1"]]


def test_handle_connection_event_skips_cache_prime_with_pi_profile() -> None:
    app = _make_app()
    app.settings = {"pi_profile_enabled": True}
    app._gcode_source = object()
    app._gcode_streaming_mode = False
    app._last_gcode_lines = ["G0 X0", "G1 X1"]
    app._last_gcode_path = "job.gcode"

    grbl_lifecycle.handle_connection_event(app, True, "COM3")

    assert app.grbl.loaded == (app._gcode_source, "job.gcode")
    assert app.grbl.prime_calls == []


def test_effective_status_poll_interval_uses_default() -> None:
    app = _make_app()
    app.status_poll_interval = _Var("bad")

    interval = grbl_lifecycle.effective_status_poll_interval(app)

    assert interval == grbl_lifecycle.STATUS_POLL_DEFAULT


def test_maybe_auto_reconnect_calls_worker(monkeypatch) -> None:
    app = _make_app()
    app._auto_reconnect_pending = True
    app._auto_reconnect_last_port = "COM3"
    app._auto_reconnect_delay = 1.0

    monkeypatch.setattr(grbl_lifecycle.time, "time", lambda: 1000.0)

    grbl_lifecycle.maybe_auto_reconnect(app)

    assert app._connect_called == "COM3"
