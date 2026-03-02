import pytest

from simple_sender.ui.all_stop import all_stop_action


pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _Grbl:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def stop_stream(self) -> None:
        self.calls.append("stop")

    def reset(self) -> None:
        self.calls.append("reset")


def test_all_stop_turns_off_job_accessories_before_reset() -> None:
    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.all_stop_mode = _Var("reset")
            self.kasa_calls: list[str] = []

        def _stop_joystick_hold(self) -> None:
            return None

        def _require_grbl_connection(self) -> bool:
            return True

        def _stop_job_accessories(self, source: str) -> None:
            self.kasa_calls.append(source)

    app = _App()

    all_stop_action(app)

    assert app.kasa_calls == ["job_all_stop"]
    assert app.grbl.calls == ["reset"]


def test_all_stop_stop_reset_mode_runs_stop_then_reset() -> None:
    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.all_stop_mode = _Var("stop_reset")
            self.kasa_calls: list[str] = []

        def _stop_joystick_hold(self) -> None:
            return None

        def _require_grbl_connection(self) -> bool:
            return True

        def _stop_job_accessories(self, source: str) -> None:
            self.kasa_calls.append(source)

    app = _App()

    all_stop_action(app)

    assert app.kasa_calls == ["job_all_stop"]
    assert app.grbl.calls == ["stop", "reset"]
