import pytest

from simple_sender.ui import override_controls
from simple_sender.utils.constants import RT_FO_PLUS_10, RT_FO_MINUS_10

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = value


class _Scale:
    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = value


class _Grbl:
    def __init__(self) -> None:
        self.calls = []

    def is_connected(self) -> bool:
        return True

    def send_realtime(self, cmd: bytes) -> None:
        self.calls.append(cmd)


def test_normalize_override_slider_value_rounds() -> None:
    assert override_controls.normalize_override_slider_value(115) == 120
    assert override_controls.normalize_override_slider_value(5, minimum=10, maximum=200) == 10


def test_send_override_delta_sends_steps() -> None:
    app = type("App", (), {"grbl": _Grbl()})()

    override_controls.send_override_delta(app, 25, RT_FO_PLUS_10, RT_FO_MINUS_10)

    assert app.grbl.calls == [RT_FO_PLUS_10, RT_FO_PLUS_10]


def test_handle_override_slider_change_updates_state(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(override_controls, "send_override_delta", lambda *_args: calls.append("sent"))

    app = type(
        "App",
        (),
        {
            "_feed_override_slider_locked": False,
            "_feed_override_slider_last_position": 100,
            "feed_override_scale": _Scale(),
            "feed_override_display": _Var(),
        },
    )()

    override_controls.handle_override_slider_change(
        app,
        130,
        "_feed_override_slider_last_position",
        "feed_override_scale",
        "_feed_override_slider_locked",
        app.feed_override_display,
        RT_FO_PLUS_10,
        RT_FO_MINUS_10,
    )

    assert calls == ["sent"]
    assert app._feed_override_slider_last_position == 130
    assert app.feed_override_display.value == "130%"
    assert app.feed_override_scale.value == 130
