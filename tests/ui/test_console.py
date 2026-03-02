import pytest
from collections import deque

pytest.importorskip("tkinter")

from simple_sender.ui.console import Console
from simple_sender.utils.constants import MAX_CONSOLE_LINES

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Entry:
    def __init__(self) -> None:
        self.cursor = None

    def icursor(self, value) -> None:
        self.cursor = value


class _Text:
    def __init__(self) -> None:
        self.deleted = None

    def config(self, **_kwargs) -> None:
        return None

    def delete(self, start: str, end: str) -> None:
        self.deleted = (start, end)


def _make_console() -> Console:
    console = Console.__new__(Console)
    console._filter_keywords = set()
    console._show_rx = True
    console._show_tx = True
    console._show_info = True
    console._show_alarm = True
    console._line_count = 0
    console.text = _Text()
    return console


def test_should_show_respects_filters() -> None:
    console = _make_console()
    console._show_rx = False

    assert console._should_show("msg", "rx") is False
    assert console._should_show("msg", "tx") is True

    console._filter_keywords = {"skip"}
    assert console._should_show("please skip this", None) is False


def test_trim_if_needed_deletes_overflow() -> None:
    console = _make_console()
    console._line_count = MAX_CONSOLE_LINES + 2

    console._trim_if_needed()

    assert console.text.deleted == ("1.0", f"{2 + 1}.0")


def test_on_entry_return_logs_and_executes() -> None:
    console = _make_console()
    console.entry_var = _Var("G0 X0")
    console.entry = _Entry()
    console._command_history = deque(maxlen=100)
    console._history_index = -1
    logged = []
    executed = []
    console.log = lambda message, tag=None: logged.append((message, tag))
    console.on_command = lambda cmd: executed.append(cmd)

    result = console._on_entry_return(None)

    assert result == "break"
    assert console.entry_var.value == ""
    assert logged == [("> G0 X0", "tx")]
    assert executed == ["G0 X0"]
