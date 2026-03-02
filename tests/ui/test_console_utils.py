import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.console import utils as console_utils

pytestmark = pytest.mark.ui


class _Entry:
    def __init__(self, value: str) -> None:
        self.value = value
        self.deleted = False

    def get(self) -> str:
        return self.value

    def delete(self, _start, _end) -> None:
        self.deleted = True


class _StreamingController:
    def __init__(self, lines=None) -> None:
        self._lines = lines or []
        self.cleared = False

    def clear_console(self) -> None:
        self.cleared = True

    def get_console_lines(self):
        return list(self._lines)

    def matches_filter(self, _entry, for_save: bool = False) -> bool:
        return True

    def is_position_line(self, text: str) -> bool:
        return "WPOS" in text.upper()


def test_send_console_sends_and_clears() -> None:
    calls = []

    class _App:
        cmd_entry = _Entry("G0 X0")

        def _send_manual(self, cmd: str, source: str) -> None:
            calls.append((cmd, source))

    app = _App()

    console_utils.send_console(app)

    assert calls == [("G0 X0", "console")]
    assert app.cmd_entry.deleted


def test_send_console_routes_settings_dump() -> None:
    calls = {"request": 0, "manual": 0}

    class _App:
        cmd_entry = _Entry("$$")

        def _request_settings_dump(self) -> None:
            calls["request"] += 1

        def _send_manual(self, _cmd: str, _source: str) -> None:
            calls["manual"] += 1

    app = _App()

    console_utils.send_console(app)

    assert calls["request"] == 1
    assert calls["manual"] == 0
    assert app.cmd_entry.deleted


def test_send_console_skips_empty() -> None:
    calls = []

    class _App:
        cmd_entry = _Entry("   ")

        def _send_manual(self, _cmd: str, _source: str) -> None:
            calls.append("called")

    app = _App()

    console_utils.send_console(app)

    assert calls == []


def test_clear_console_log_respects_confirmation(monkeypatch) -> None:
    controller = _StreamingController()
    monkeypatch.setattr(console_utils, "messagebox", type("MB", (), {"askyesno": lambda *_: False})())

    class _App:
        streaming_controller = controller

    console_utils.clear_console_log(_App())

    assert controller.cleared is False


def test_save_console_log_writes_file(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "log.txt"
    controller = _StreamingController(
        [
            ("<< <Idle|WPos:0,0,0>", None),
            ("<< ok", None),
            (">> G0 X0", None),
        ]
    )
    monkeypatch.setattr(
        console_utils,
        "filedialog",
        type("FD", (), {"asksaveasfilename": lambda *_args, **_kwargs: str(log_path)})(),
    )

    class _App:
        streaming_controller = controller

    console_utils.save_console_log(_App())

    data = log_path.read_text(encoding="utf-8")
    assert "<< ok" in data
    assert "G0 X0" in data
    assert "WPos" not in data


def test_save_console_log_prefills_default_filename(monkeypatch) -> None:
    controller = _StreamingController([("<< ok", None)])
    captured: dict[str, str] = {}

    def _fake_save(*_args, **kwargs):
        captured["initialfile"] = kwargs.get("initialfile", "")
        captured["initialdir"] = kwargs.get("initialdir", "")
        return ""

    monkeypatch.setattr(
        console_utils,
        "filedialog",
        type("FD", (), {"asksaveasfilename": _fake_save})(),
    )

    class _App:
        streaming_controller = controller

    console_utils.save_console_log(_App())

    assert captured["initialfile"].startswith("simple_sender_console_")
    assert captured["initialfile"].endswith(".txt")
    assert captured["initialdir"]
