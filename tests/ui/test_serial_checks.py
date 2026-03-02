import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import app_commands

pytestmark = pytest.mark.ui


class _MessageBox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def showerror(self, title: str, message: str) -> None:
        self.calls.append((title, message))


def test_ensure_serial_available_true() -> None:
    assert app_commands.ensure_serial_available(object(), True) is True


def test_ensure_serial_available_false_shows_message(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(app_commands, "messagebox", msgbox)

    result = app_commands.ensure_serial_available(object(), False, "missing")

    assert result is False
    assert msgbox.calls
    assert "Missing dependency" in msgbox.calls[0][0]
