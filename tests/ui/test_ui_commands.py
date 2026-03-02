import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import app_commands

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _Grbl:
    def __init__(self, streaming: bool = False, connected: bool = False) -> None:
        self._streaming = streaming
        self._connected = connected

    def is_streaming(self) -> bool:
        return self._streaming

    def is_connected(self) -> bool:
        return self._connected

    def list_ports(self) -> list[str]:
        return ["COM1", "COM2"]


class _MessageBox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def showwarning(self, title: str, message: str) -> None:
        self.calls.append(("warning", title, message))

    def showerror(self, title: str, message: str) -> None:
        self.calls.append(("error", title, message))


class _Widget:
    def __init__(self) -> None:
        self.state = None
        self.text = ""

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]


class _UiQ:
    def __init__(self) -> None:
        self.items: list[tuple] = []

    def put(self, item) -> None:
        self.items.append(item)


class _RunGrbl:
    def __init__(self, *, start_ok: bool = True) -> None:
        self.sanitize = None
        self.started = False
        self._start_ok = bool(start_ok)
        self._streaming = False

    def set_dry_run_sanitize(self, value: bool) -> None:
        self.sanitize = value

    def start_stream(self) -> None:
        self.started = True
        self._streaming = bool(self._start_ok)

    def is_streaming(self) -> bool:
        return bool(self._streaming)


class _Router:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_debounce(self) -> None:
        self.reset_calls += 1


def test_toggle_connect_warns_when_streaming(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(app_commands, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.connected = False
            self.grbl = _Grbl(streaming=True)
            self._ensure_serial_available = lambda: True

    app = _App()

    app_commands.toggle_connect(app)

    assert msgbox.calls
    assert msgbox.calls[0][0] == "warning"


def test_toggle_connect_disconnects_when_connected(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(app_commands, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.connected = True
            self._user_disconnect = False
            self._disconnect_called = False
            self.grbl = _Grbl(streaming=False)
            self._ensure_serial_available = lambda: True

        def _start_disconnect_worker(self) -> None:
            self._disconnect_called = True

    app = _App()

    app_commands.toggle_connect(app)

    assert app._user_disconnect
    assert app._disconnect_called
    assert msgbox.calls == []


def test_toggle_connect_warns_when_no_port(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(app_commands, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.connected = False
            self.grbl = _Grbl(streaming=False)
            self.current_port = _Var("")
            self._ensure_serial_available = lambda: True
            self._start_connect_worker = lambda _port: None

    app = _App()

    app_commands.toggle_connect(app)

    assert msgbox.calls
    assert msgbox.calls[0][0] == "warning"


def test_open_gcode_calls_loader(monkeypatch) -> None:
    monkeypatch.setattr(
        app_commands,
        "choose_gcode_path",
        lambda _app, _initial_dir: "C:/tmp/sample.gcode",
    )

    class _App:
        def __init__(self) -> None:
            self._loaded = None
            self.grbl = _Grbl(streaming=False)
            self.settings = {"last_gcode_dir": "C:/tmp"}

        def _load_gcode_from_path(self, path: str) -> None:
            self._loaded = path

    app = _App()

    app_commands.open_gcode(app)

    assert app._loaded == "C:/tmp/sample.gcode"


def test_choose_gcode_path_uses_native_picker(monkeypatch) -> None:
    called = {"value": False}

    def _fake_run_file_dialog(_app, func, **kwargs) -> str:
        called["value"] = True
        assert callable(func)
        assert kwargs["title"] == "Open G-code"
        return "C:/tmp/system.nc"

    monkeypatch.setattr(app_commands, "run_file_dialog", _fake_run_file_dialog)

    assert app_commands.choose_gcode_path(object(), "C:/tmp") == "C:/tmp/system.nc"
    assert called["value"] is True


def test_choose_gcode_path_returns_empty_when_cancelled(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_file_dialog", lambda *_args, **_kwargs: "")

    assert app_commands.choose_gcode_path(object(), "C:/tmp") == ""


def test_refresh_ports_auto_connect() -> None:
    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(streaming=False)
            self.port_combo = {}
            self.current_port = _Var("COM9")
            self.settings = {"last_port": "COM2"}
            self.connected = False
            self._toggle_called = False

        def toggle_connect(self) -> None:
            self._toggle_called = True

    app = _App()

    app_commands.refresh_ports(app, auto_connect=True)

    assert app.current_port.get() == "COM2"
    assert app._toggle_called


def test_toggle_connect_ignores_while_connecting() -> None:
    class _App:
        def __init__(self) -> None:
            self.connected = False
            self._connecting = True
            self._disconnecting = False
            self.grbl = _Grbl(streaming=False)
            self.btn_conn = _Widget()
            self.btn_refresh = _Widget()
            self.port_combo = _Widget()
            self._ensure_serial_available = lambda: True
            self._start_connect_worker = lambda _port: (_ for _ in ()).throw(
                AssertionError("unexpected connect")
            )

    app = _App()

    app_commands.toggle_connect(app)

    assert app.btn_conn.state == "disabled"
    assert app.btn_conn.text.endswith("Connecting...")


def test_start_connect_worker_restores_controls_after_failure(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(app_commands, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.connected = False
            self._connecting = False
            self.grbl = _Grbl(streaming=False)
            self.grbl.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            self.settings = {}
            self.btn_conn = _Widget()
            self.btn_refresh = _Widget()
            self.port_combo = _Widget()

        def after(self, _ms: int, func):
            func()

    app = _App()

    app_commands.start_connect_worker(app, "COM3", show_error=False)
    app._connect_thread.join(timeout=1.0)

    assert app._connecting is False
    assert app.btn_conn.state == "normal"
    assert app.btn_conn.text.endswith("Connect")
    assert app.btn_refresh.state == "normal"
    assert app.port_combo.state == "readonly"


def test_start_disconnect_worker_restores_controls_after_failure() -> None:
    class _App:
        def __init__(self) -> None:
            self.connected = True
            self._disconnecting = False
            self.grbl = _Grbl(streaming=False, connected=True)
            self.grbl.disconnect = lambda: (_ for _ in ()).throw(RuntimeError("nope"))
            self.btn_conn = _Widget()
            self.btn_refresh = _Widget()
            self.port_combo = _Widget()
            self.ui_q = _UiQ()

        def after(self, _ms: int, func):
            func()

    app = _App()

    app_commands.start_disconnect_worker(app)
    app._disconnect_thread.join(timeout=1.0)

    assert app._disconnecting is False
    assert app.btn_conn.state == "normal"
    assert app.btn_conn.text.endswith("Disconnect")
    assert app.btn_refresh.state == "normal"
    assert app.port_combo.state == "readonly"


def test_run_job_blocks_when_preflight_gate_fails(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_preflight_gate", lambda _app: False)

    class _App:
        def __init__(self) -> None:
            self.grbl = _RunGrbl()
            self.dry_run_sanitize_stream = _Var(True)
            self._reset_called = False
            self._job_started_at = None
            self._job_completion_notified = True

        def _require_grbl_connection(self) -> bool:
            return True

        def _reset_gcode_view_for_run(self) -> None:
            self._reset_called = True

    app = _App()

    app_commands.run_job(app)

    assert app.grbl.started is False
    assert app._reset_called is False


def test_run_job_starts_stream_when_preflight_gate_passes(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_preflight_gate", lambda _app: True)

    class _App:
        def __init__(self) -> None:
            self.grbl = _RunGrbl()
            self.dry_run_sanitize_stream = _Var(True)
            self._reset_called = False
            self._job_started_at = None
            self._job_completion_notified = True

        def _require_grbl_connection(self) -> bool:
            return True

        def _reset_gcode_view_for_run(self) -> None:
            self._reset_called = True

    app = _App()

    app_commands.run_job(app)

    assert app.grbl.sanitize is True
    assert app.grbl.started is True
    assert app._reset_called is True
    assert app._job_started_at is not None
    assert app._job_completion_notified is False


def test_run_job_starts_job_accessories_when_available(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_preflight_gate", lambda _app: True)

    class _App:
        def __init__(self) -> None:
            self.grbl = _RunGrbl()
            self.dry_run_sanitize_stream = _Var(True)
            self._reset_called = False
            self._job_started_at = None
            self._job_completion_notified = True
            self.kasa_calls = []

        def _require_grbl_connection(self) -> bool:
            return True

        def _reset_gcode_view_for_run(self) -> None:
            self._reset_called = True

        def _start_job_accessories(self, source: str) -> None:
            self.kasa_calls.append(source)

    app = _App()

    app_commands.run_job(app)

    assert app.kasa_calls == ["job_run"]
    assert app.grbl.started is True


def test_run_job_skips_job_accessories_when_stream_does_not_start(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_preflight_gate", lambda _app: True)

    class _App:
        def __init__(self) -> None:
            self.grbl = _RunGrbl(start_ok=False)
            self.dry_run_sanitize_stream = _Var(True)
            self._reset_called = False
            self._job_started_at = None
            self._job_completion_notified = True
            self.kasa_calls = []

        def _require_grbl_connection(self) -> bool:
            return True

        def _reset_gcode_view_for_run(self) -> None:
            self._reset_called = True

        def _start_job_accessories(self, source: str) -> None:
            self.kasa_calls.append(source)

    app = _App()

    app_commands.run_job(app)

    assert app.grbl.started is True
    assert app.grbl.is_streaming() is False
    assert app.kasa_calls == []
    assert app._job_started_at is None
    assert app._job_completion_notified is True


def test_run_job_resets_accessory_router_debounce(monkeypatch) -> None:
    monkeypatch.setattr(app_commands, "run_preflight_gate", lambda _app: True)

    class _App:
        def __init__(self) -> None:
            self.grbl = _RunGrbl()
            self.dry_run_sanitize_stream = _Var(True)
            self._reset_called = False
            self._job_started_at = None
            self._job_completion_notified = True
            self.accessory_router = _Router()
            self._kasa_last_stream_line_index = 42

        def _require_grbl_connection(self) -> bool:
            return True

        def _reset_gcode_view_for_run(self) -> None:
            self._reset_called = True

    app = _App()

    app_commands.run_job(app)

    assert app.accessory_router.reset_calls == 1
    assert app._kasa_last_stream_line_index == -1
    assert app.grbl.started is True


def test_stop_job_stops_job_accessories_when_available() -> None:
    class _Grbl:
        def __init__(self) -> None:
            self.stopped = False

        def stop_stream(self) -> None:
            self.stopped = True

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.kasa_calls = []

        def _require_grbl_connection(self) -> bool:
            return True

        def _stop_job_accessories(self, source: str) -> None:
            self.kasa_calls.append(source)

    app = _App()

    app_commands.stop_job(app)

    assert app.kasa_calls == ["job_stop"]
    assert app.grbl.stopped is True
