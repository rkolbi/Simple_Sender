from simple_sender.ui import zeroing_actions


class _App:
    def __init__(self, connected: bool) -> None:
        self._connected = connected
        self.sent: list[tuple[str, str]] = []

    def _require_grbl_connection(self) -> bool:
        return self._connected

    def _send_manual(self, command: str, source: str) -> None:
        self.sent.append((command, source))


def test_goto_zero_sends_xy_then_z_commands() -> None:
    app = _App(connected=True)

    zeroing_actions.goto_zero(app)

    assert app.sent == [
        ("G90 G0 X0 Y0", "zero"),
        ("G0 Z0", "zero"),
    ]


def test_goto_zero_skips_when_not_connected() -> None:
    app = _App(connected=False)

    zeroing_actions.goto_zero(app)

    assert app.sent == []
