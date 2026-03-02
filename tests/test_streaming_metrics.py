import pytest

pytest.importorskip("tkinter")

from datetime import datetime

from simple_sender.ui.dialogs import streaming_metrics


class _Flag:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value


class _Progress:
    def __init__(self) -> None:
        self.value = None

    def set(self, value: int) -> None:
        self.value = value


class _Controller:
    def __init__(self) -> None:
        self.logs = []

    def handle_log(self, message: str) -> None:
        self.logs.append(message)


class _App:
    def __init__(self) -> None:
        self._job_started_at = None
        self._job_completion_notified = False
        self._completion_dialog = None
        self._completion_flash_active = False
        self._completion_flash_on = False
        self._completion_flash_id = None
        self.streaming_controller = _Controller()
        self.job_completion_popup = _Flag(False)
        self.job_completion_beep = _Flag(False)
        self.progress_pct = _Progress()
        self.after_calls = []
        self.after_cancel_calls = []
        self.bell_calls = 0

    def bell(self) -> None:
        self.bell_calls += 1

    def after(self, delay: int, func) -> str:
        self.after_calls.append((delay, func))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, token: str) -> None:
        self.after_cancel_calls.append(token)


@pytest.mark.parametrize(
    "bps,expected",
    [
        (0, "TX: 0 B/s"),
        (512, "TX: 512 B/s"),
        (2048, "TX: 2.0 KB/s"),
        (1024 * 1024, "TX: 1.00 MB/s"),
    ],
)
def test_format_throughput(bps: float, expected: str) -> None:
    assert streaming_metrics.format_throughput(bps) == expected


def test_maybe_notify_job_completion_noop_when_incomplete() -> None:
    app = _App()
    app._job_started_at = datetime(2024, 1, 1, 0, 0, 0)

    streaming_metrics.maybe_notify_job_completion(app, done=1, total=2)

    assert app._job_completion_notified is False
    assert app.streaming_controller.logs == []
    assert app.bell_calls == 0


def test_maybe_notify_job_completion_logs_and_beeps(monkeypatch) -> None:
    app = _App()
    app._job_started_at = datetime(2024, 1, 1, 0, 0, 0)
    app.job_completion_beep = _Flag(True)
    app.job_completion_popup = _Flag(True)
    popup_calls = []

    monkeypatch.setattr(
        streaming_metrics,
        "_show_job_completion_dialog",
        lambda *_args, **_kwargs: popup_calls.append(True),
    )

    streaming_metrics.maybe_notify_job_completion(app, done=2, total=2)

    assert app._job_completion_notified is True
    assert app._job_started_at is None
    assert app.streaming_controller.logs
    assert app.streaming_controller.logs[0].startswith("[job] Job completed in")
    assert app.bell_calls == 1
    assert popup_calls == [True]


def test_maybe_notify_job_completion_waits_for_idle_state() -> None:
    app = _App()
    app._job_started_at = datetime(2024, 1, 1, 0, 0, 0)
    app._machine_state_text = "Run"

    streaming_metrics.maybe_notify_job_completion(app, done=2, total=2)

    assert app._job_completion_notified is False
    assert app._job_started_at is not None
    assert app.streaming_controller.logs == []


def test_start_completion_flash_schedules_once() -> None:
    app = _App()

    streaming_metrics._start_completion_flash(app)
    streaming_metrics._start_completion_flash(app)

    assert app._completion_flash_active is True
    assert len(app.after_calls) == 1


def test_stop_completion_flash_resets_state() -> None:
    app = _App()
    app._completion_flash_active = True
    app._completion_flash_on = True
    app._completion_flash_id = "flash-id"

    streaming_metrics._stop_completion_flash(app)

    assert app._completion_flash_active is False
    assert app._completion_flash_on is False
    assert app._completion_flash_id is None
    assert app.after_cancel_calls == ["flash-id"]
    assert app.progress_pct.value == 0
