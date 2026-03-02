import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.grbl_settings import requests as grbl_settings_requests

pytestmark = pytest.mark.ui


class _MessageBox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def showwarning(self, title: str, message: str) -> None:
        self.calls.append(("warning", title, message))


class _StreamingController:
    def __init__(self) -> None:
        self.logs = []

    def log(self, message: str) -> None:
        self.logs.append(message)


class _SettingsController:
    def __init__(self) -> None:
        self.captured = None

    def start_capture(self, label: str) -> None:
        self.captured = label


class _Grbl:
    def __init__(self, connected: bool = True, streaming: bool = False) -> None:
        self._connected = connected
        self._streaming = streaming

    def is_connected(self) -> bool:
        return self._connected

    def is_streaming(self) -> bool:
        return self._streaming


def test_request_settings_dump_warns_when_disconnected(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(grbl_settings_requests, "messagebox", msgbox)

    class _App:
        grbl = _Grbl(connected=False)

    grbl_settings_requests.request_settings_dump(_App())

    assert msgbox.calls
    assert msgbox.calls[0][1] == "Not connected"


def test_request_settings_dump_warns_when_streaming(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(grbl_settings_requests, "messagebox", msgbox)

    class _App:
        grbl = _Grbl(connected=True, streaming=True)

    grbl_settings_requests.request_settings_dump(_App())

    assert msgbox.calls
    assert msgbox.calls[0][1] == "Busy"


def test_request_settings_dump_defers_until_ready(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(grbl_settings_requests, "messagebox", msgbox)

    class _Status:
        text = ""

        def config(self, **kwargs) -> None:
            if "text" in kwargs:
                self.text = kwargs["text"]

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(connected=True, streaming=False)
            self._grbl_ready = False
            self._alarm_locked = False
            self._pending_settings_refresh = False
            self.status = _Status()

    app = _App()

    grbl_settings_requests.request_settings_dump(app)

    assert app._pending_settings_refresh
    assert "Waiting for Grbl startup" in app.status.text


def test_request_settings_dump_sends_command(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(grbl_settings_requests, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(connected=True, streaming=False)
            self._grbl_ready = True
            self._alarm_locked = False
            self.status = object()
            self.streaming_controller = _StreamingController()
            self.settings_controller = _SettingsController()
            self.sent = None

        def _send_manual(self, cmd: str, source: str) -> None:
            self.sent = (cmd, source)

    app = _App()

    grbl_settings_requests.request_settings_dump(app)

    assert msgbox.calls == []
    assert app.sent == ("$$", "settings")
    assert app.settings_controller.captured == "Requesting $$..."
