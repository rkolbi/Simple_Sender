import queue

from simple_sender.grbl_worker import GrblWorker


class _DummySerial:
    def __init__(self) -> None:
        self.is_open = True

    def write(self, data: bytes) -> int:
        return len(data)


class _DummyEvent:
    def __init__(self) -> None:
        self._set = False
        self.wait_calls = []

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        self._set = True
        return True


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_tx_loop_emits_exception_and_disconnect() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    called = {"emit": 0, "disconnect": 0}
    worker._emit_exception = lambda *_args, **_kwargs: called.__setitem__("emit", 1)
    worker._signal_disconnect = lambda *_args, **_kwargs: called.__setitem__("disconnect", 1)
    worker._process_stream_queue = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    worker._process_manual_queue = lambda: None
    stop_evt = _DummyEvent()

    worker._tx_loop(stop_evt)

    assert called["emit"] == 1
    assert called["disconnect"] == 1
    assert stop_evt.is_set() is True


def test_tx_loop_uses_idle_wait_when_no_work() -> None:
    worker = _make_worker()
    worker._tx_loop_idle_wait_s = 0.3
    worker._process_manual_queue = lambda: None
    stop_evt = _DummyEvent()

    worker._tx_loop(stop_evt)

    assert stop_evt.wait_calls == [0.3]
    assert worker._tx_loop_cycles >= 1
    assert worker._tx_loop_idle_cycles >= 1
    assert worker._tx_loop_active_cycles == 0
    assert worker._tx_loop_idle_wait_total_s >= 0.3


def test_status_loop_success_resets_failures() -> None:
    worker = _make_worker()
    worker._status_query_failures = 2
    worker._status_poll_interval = 0.2
    sent = []
    worker.send_realtime = lambda _cmd: sent.append(True)
    stop_evt = _DummyEvent()

    worker._status_loop(stop_evt)

    assert sent == [True]
    assert worker._status_query_failures == 0
    assert stop_evt.wait_calls == [0.2]


def test_status_loop_failure_backoff_no_disconnect() -> None:
    worker = _make_worker()
    worker._status_query_failure_limit = 2
    worker._status_query_failures = 0
    worker.send_realtime = lambda _cmd: (_ for _ in ()).throw(RuntimeError("fail"))
    disconnect = []
    worker._signal_disconnect = lambda reason=None: disconnect.append(reason)
    worker._emit_exception = lambda *_args, **_kwargs: None
    stop_evt = _DummyEvent()

    worker._status_loop(stop_evt)

    assert disconnect == []
    assert worker._status_query_failures == 1
    assert stop_evt.wait_calls == [worker._status_query_backoff_base]


def test_status_loop_failure_disconnects_at_limit() -> None:
    worker = _make_worker()
    worker._status_query_failure_limit = 1
    worker.send_realtime = lambda _cmd: (_ for _ in ()).throw(RuntimeError("fail"))
    disconnect = []
    worker._signal_disconnect = lambda reason=None: disconnect.append(reason)
    worker._emit_exception = lambda *_args, **_kwargs: None
    stop_evt = _DummyEvent()

    worker._status_loop(stop_evt)

    assert disconnect
    assert stop_evt.is_set() is True
