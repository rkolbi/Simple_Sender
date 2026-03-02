import pytest

from simple_sender.ui.status import polling as status_polling
from simple_sender.utils.constants import STATUS_POLL_DEFAULT


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Grbl:
    def __init__(self, should_raise: bool = False) -> None:
        self.limit = None
        self._should_raise = should_raise

    def set_status_query_failure_limit(self, limit: int) -> None:
        if self._should_raise:
            raise RuntimeError("grbl unavailable")
        self.limit = limit


class _App:
    def __init__(self, interval_value, failure_value, settings, grbl=None):
        self.status_poll_interval = _Var(interval_value)
        self.status_query_failure_limit = _Var(failure_value)
        self.settings = settings
        self.grbl = grbl or _Grbl()
        self.applied = 0

    def _apply_status_poll_profile(self) -> None:
        self.applied += 1


def test_status_interval_clamps_minimum() -> None:
    app = _App("0.01", "3", {"status_poll_interval": 0.3})

    status_polling.on_status_interval_change(app)

    assert app.status_poll_interval.value == 0.05
    assert app.applied == 1


def test_status_interval_uses_default_when_non_positive() -> None:
    app = _App("0", "3", {"status_poll_interval": 0.3})

    status_polling.on_status_interval_change(app)

    assert app.status_poll_interval.value == STATUS_POLL_DEFAULT


def test_status_interval_uses_settings_on_invalid() -> None:
    app = _App("bad", "3", {"status_poll_interval": 0.3})

    status_polling.on_status_interval_change(app)

    assert app.status_poll_interval.value == 0.3


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 1),
        ("11", 10),
        ("5", 5),
    ],
)
def test_status_failure_limit_clamps_and_applies(value: str, expected: int) -> None:
    app = _App("0.2", value, {"status_query_failure_limit": 3})

    status_polling.on_status_failure_limit_change(app)

    assert app.status_query_failure_limit.value == expected
    assert app.grbl.limit == expected


def test_status_failure_limit_uses_settings_on_invalid() -> None:
    app = _App("0.2", "bad", {"status_query_failure_limit": 7})

    status_polling.on_status_failure_limit_change(app)

    assert app.status_query_failure_limit.value == 7
    assert app.grbl.limit == 7


def test_status_failure_limit_handles_grbl_failure() -> None:
    app = _App("0.2", "2", {"status_query_failure_limit": 3}, grbl=_Grbl(True))

    status_polling.on_status_failure_limit_change(app)

    assert app.status_query_failure_limit.value == 2
