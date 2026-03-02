import queue

import pytest

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.utils.exceptions import SerialConnectionError, SerialWriteError


class _DummySerial:
    def __init__(self, read_exc: Exception | None = None) -> None:
        self.is_open = True
        self._read_exc = read_exc
        self.read_calls = 0

    def write(self, data: bytes) -> int:
        return len(data)

    def read(self, _size: int) -> bytes:
        self.read_calls += 1
        if self._read_exc:
            raise self._read_exc
        return b""


class _LoopEvent:
    def __init__(self) -> None:
        self.calls = 0
        self._set = False

    def is_set(self) -> bool:
        self.calls += 1
        return self._set or self.calls > 1

    def set(self) -> None:
        self._set = True


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_send_realtime_raises_on_timeout(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    class _SerialImpl(_DummySerial):
        def write(self, _data: bytes) -> int:
            return 0

    worker = GrblWorker(queue.Queue())
    worker.ser = _SerialImpl()

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    with pytest.raises(SerialWriteError):
        worker.send_realtime(b"?")


def test_send_realtime_logs_realtime_payload(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    logged: list[str] = []
    worker._log_tx_line = lambda line: logged.append(line)

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    worker.send_realtime(b"\x85")

    assert logged == ["RT 0x85"]


def test_send_realtime_raises_on_serial_exception(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    class _SerialImpl(_DummySerial):
        def write(self, _data: bytes) -> int:
            raise _SerialStub.SerialException("bad")

    worker = GrblWorker(queue.Queue())
    worker.ser = _SerialImpl()

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    with pytest.raises(SerialWriteError):
        worker.send_realtime(b"?")


def test_send_realtime_raises_on_generic_exception(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    class _SerialImpl(_DummySerial):
        def write(self, _data: bytes) -> int:
            raise RuntimeError("boom")

    worker = GrblWorker(queue.Queue())
    worker.ser = _SerialImpl()

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    with pytest.raises(SerialWriteError):
        worker.send_realtime(b"?")


def test_process_stream_queue_aborts_rolls_back() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._abort_writes.clear()
    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_stream_queue()

    assert worker._send_index == 0
    assert worker._stream_buf_used == 0
    assert worker._stream_line_queue == queue.deque()


def test_process_stream_queue_write_failure_sets_error() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._write_line = lambda *_args, **_kwargs: False

    worker._process_stream_queue()

    assert worker._streaming is False
    assert worker._paused is False
    assert ("stream_state", "error", "Write failed") in list(worker.ui_q.queue)


def test_process_stream_queue_marks_done_when_complete() -> None:
    worker = _make_worker()
    worker._gcode = []
    worker._streaming = True
    worker._paused = False
    worker._send_index = 0
    worker._ack_index = -1

    worker._process_stream_queue()

    assert worker._streaming is False
    assert ("stream_state", "done", None) in list(worker.ui_q.queue)


def test_handle_rx_line_status_alarm_transitions(monkeypatch) -> None:
    worker = _make_worker()
    called = []
    worker._alarm_active = False

    monkeypatch.setattr(worker, "_handle_alarm", lambda _msg: called.append(True))

    worker._handle_rx_line("<Alarm|Bf:10,128|FS:0,0>")

    assert called == [True]

    worker._alarm_active = True
    worker._handle_rx_line("<Idle|Bf:10,128|FS:0,0>")

    assert worker._alarm_active is False


def test_handle_rx_line_status_bad_bf_ignored() -> None:
    worker = _make_worker()
    worker._rx_window = 123

    worker._handle_rx_line("<Idle|Bf:bad|FS:0,0>")

    assert worker._rx_window == 123
    assert ("status", "<Idle|Bf:bad|FS:0,0>") in list(worker.ui_q.queue)


def test_rx_loop_serial_exception_disconnects(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser = _DummySerial(read_exc=_SerialStub.SerialException("bad"))
    worker._signal_disconnect = lambda *_args, **_kwargs: None

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    stop_evt = _LoopEvent()
    worker._rx_loop(stop_evt)

    assert stop_evt._set is True
    assert any(evt[0] == "log" and "read error" in evt[1] for evt in worker.ui_q.queue)


def test_rx_loop_timeout_continues(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser = _DummySerial(read_exc=_SerialStub.SerialTimeoutException("timeout"))
    disconnected = []
    worker._signal_disconnect = lambda *_args, **_kwargs: disconnected.append(True)

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    stop_evt = _LoopEvent()
    worker._rx_loop(stop_evt)

    assert disconnected == []


def test_connect_serial_exception_raises(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())

    class _SerialStub:
        class SerialException(Exception):
            pass

        def __init__(self, *_args, **_kwargs):
            raise _SerialStub.SerialException("bad")

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)

    with pytest.raises(SerialConnectionError):
        worker.connect("COM3")


def test_connect_thread_start_failure_closes_serial(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())
    closed: list[bool] = []

    class _SerialPort:
        def __init__(self, *_args, **_kwargs) -> None:
            self.is_open = True

        def close(self) -> None:
            closed.append(True)
            self.is_open = False

        def reset_input_buffer(self) -> None:
            pass

        def reset_output_buffer(self) -> None:
            pass

    class _SerialStub:
        class SerialException(Exception):
            pass

        Serial = _SerialPort

    class _BadThread:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs

        def start(self) -> None:
            raise RuntimeError("thread failed")

        def is_alive(self) -> bool:
            return False

        def join(self, timeout=None) -> None:
            _ = timeout

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker.threading, "Thread", _BadThread)
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    with pytest.raises(SerialConnectionError):
        worker.connect("COM3")

    assert closed == [True]
    assert worker.ser is None


def test_disconnect_ignores_close_errors(monkeypatch) -> None:
    worker = _make_worker()

    class _SerialStub:
        class SerialException(Exception):
            pass

    class _SerialImpl(_DummySerial):
        def close(self) -> None:
            raise _SerialStub.SerialException("bad")

    worker.ser = _SerialImpl()

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    worker.disconnect()
    assert worker.ser is None
