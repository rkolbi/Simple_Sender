import queue

import pytest

from simple_sender import grbl_worker
from simple_sender import grbl_worker_connection
from simple_sender.grbl_worker import GrblWorker
from simple_sender.utils.exceptions import SerialConnectionError


def test_serial_exception_type_fallbacks_when_serial_module_missing() -> None:
    serial_exc = grbl_worker_connection._serial_exception_type(None)
    timeout_exc = grbl_worker_connection._serial_timeout_exception_type(None)

    assert serial_exc is grbl_worker_connection._FallbackSerialException
    assert timeout_exc is grbl_worker_connection._FallbackSerialTimeout


def test_log_suppressed_deduplicates_identical_error_keys(monkeypatch) -> None:
    grbl_worker_connection._logged_suppressed.clear()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(grbl_worker_connection.logger, "debug", lambda *args, **kwargs: calls.append(args))

    exc = RuntimeError("boom")
    grbl_worker_connection._log_suppressed("ctx", exc)
    grbl_worker_connection._log_suppressed("ctx", exc)

    assert len(calls) == 1


def test_connect_cleanup_suppresses_close_and_join_errors(monkeypatch) -> None:
    class _SerialPort:
        def __init__(self, *_args, **_kwargs) -> None:
            self.is_open = True

        def close(self) -> None:
            raise RuntimeError("close failed")

        def reset_input_buffer(self) -> None:
            pass

        def reset_output_buffer(self) -> None:
            pass

    class _SerialStub:
        class SerialException(Exception):
            pass

        class SerialTimeoutException(Exception):
            pass

        Serial = _SerialPort

    class _BadThread:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            self.name = kwargs.get("name", "thread")

        def start(self) -> None:
            raise RuntimeError("thread start failed")

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            _ = timeout
            raise RuntimeError("join failed")

    worker = GrblWorker(queue.Queue())
    grbl_worker_connection._logged_suppressed.clear()
    debug_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(grbl_worker_connection.logger, "debug", lambda *args, **kwargs: debug_calls.append(args))
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker.threading, "Thread", _BadThread)
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    with pytest.raises(SerialConnectionError):
        worker.connect("COM3")

    assert worker.ser is None
    assert worker._rx_thread is None
    assert worker._tx_thread is None
    assert worker._status_thread is None
    # Only first occurrence per context/type should be logged:
    # one for close cleanup, one for join cleanup (despite three join failures).
    assert len(debug_calls) == 2


def test_is_connected_handles_is_open_attribute_errors(monkeypatch) -> None:
    class _BrokenSerial:
        @property
        def is_open(self) -> bool:
            raise RuntimeError("bad attr")

    worker = GrblWorker(queue.Queue())
    worker.ser = _BrokenSerial()
    grbl_worker_connection._logged_suppressed.clear()
    debug_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(grbl_worker_connection.logger, "debug", lambda *args, **kwargs: debug_calls.append(args))

    assert worker.is_connected() is False
    assert worker.is_connected() is False
    assert len(debug_calls) == 1

