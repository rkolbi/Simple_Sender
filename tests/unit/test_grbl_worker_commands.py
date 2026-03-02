import queue

import pytest

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.types import StreamQueueItem
from simple_sender.utils.exceptions import GrblNotConnectedException, SerialConnectionError, SerialWriteError


class _DummySerial:
    def __init__(self) -> None:
        self.is_open = True
        self.closed = False
        self.writes = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _DummyThread:
    def __init__(self, name: str = "thread") -> None:
        self.name = name
        self._alive = True
        self.joined = False

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self._alive = False


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_list_ports_returns_empty_when_serial_unavailable(monkeypatch) -> None:
    worker = _make_worker()
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", False)
    monkeypatch.setattr(grbl_worker, "list_ports", None)

    assert worker.list_ports() == []


def test_connect_raises_when_serial_unavailable(monkeypatch) -> None:
    worker = _make_worker()
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", False)

    with pytest.raises(SerialConnectionError):
        worker.connect("COM3")


def test_connect_starts_threads_and_emits_conn(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())

    class _SerialImpl:
        def __init__(self, *_args, **_kwargs):
            self.is_open = True

        def reset_input_buffer(self):
            return None

        def reset_output_buffer(self):
            return None

    class _SerialStub:
        class SerialException(Exception):
            pass

        Serial = _SerialImpl

    threads = []

    def _thread_factory(*_args, **kwargs):
        thread = _DummyThread(name=kwargs.get("name", "thread"))
        threads.append(thread)
        return thread

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker.threading, "Thread", _thread_factory)
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    worker.connect("COM3", baud=115200)

    assert worker.is_connected() is True
    assert [t.name for t in threads] == ["GRBL-RX", "GRBL-TX", "GRBL-Status"]
    assert ("conn", True, "COM3") in list(worker.ui_q.queue)


def test_disconnect_resets_state_and_closes_port() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = True
    worker._gcode = ["G0 X0"]
    worker._send_index = 2
    worker._ack_index = 1
    worker._stream_buf_used = 10
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))
    worker._ready = True
    worker._alarm_active = True
    worker._status_query_failures = 2
    worker._rx_thread = _DummyThread(name="rx")
    worker._tx_thread = _DummyThread(name="tx")
    worker._status_thread = _DummyThread(name="status")

    worker.disconnect()

    assert worker._streaming is False
    assert worker._paused is False
    assert worker._gcode == []
    assert worker._send_index == 0
    assert worker._ack_index == -1
    assert worker._ready is False
    assert worker._alarm_active is False
    assert worker._status_query_failures == 0
    assert worker.ser is None
    assert worker._rx_thread is None
    assert worker._tx_thread is None
    assert worker._status_thread is None
    assert ("ready", False) in list(worker.ui_q.queue)
    assert ("stream_state", "stopped", None) in list(worker.ui_q.queue)
    assert ("conn", False, None) in list(worker.ui_q.queue)


def test_unlock_home_spindle_and_jog_cancel_send() -> None:
    worker = _make_worker()
    sent = []
    worker.send_immediate = lambda cmd, **_kw: sent.append(cmd)
    worker.send_realtime = lambda cmd: sent.append(cmd)

    worker.unlock()
    worker.home()
    worker.spindle_on(5000)
    worker.spindle_off()
    worker.jog_cancel()

    assert sent == ["$X", "$H", "M3 S5000", "M5", grbl_worker.RT_JOG_CANCEL]


def test_jog_requires_connection() -> None:
    worker = GrblWorker(queue.Queue())

    with pytest.raises(GrblNotConnectedException):
        worker.jog(1, 2, 3, 100, "mm")


def test_jog_formats_command_for_units() -> None:
    worker = _make_worker()
    sent = []
    worker.send_immediate = lambda cmd, **_kw: sent.append(cmd)

    worker.jog(1, 2, 3, 100, "mm")
    worker.jog(1, 0, 0, 200, "inch")

    assert sent[0].startswith("$J=G21 G91 X1.0000 Y2.0000 Z3.0000 F100.0")
    assert sent[1].startswith("$J=G20 G91 X1.0000 Y0.0000 Z0.0000 F200.0")


def test_resume_stream_handles_serial_write_error() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = True
    worker.resume = lambda: (_ for _ in ()).throw(SerialWriteError("fail"))

    worker.resume_stream()

    assert worker._paused is True
    assert any(evt[0] == "log" and "resume failed" in evt[1] for evt in worker.ui_q.queue)


def test_pause_stream_logs_on_write_error() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    worker.hold = lambda: (_ for _ in ()).throw(SerialWriteError("fail"))

    worker.pause_stream()

    assert worker._paused is True
    assert ("stream_state", "paused", None) in list(worker.ui_q.queue)
    assert any(evt[0] == "log" and "pause failed" in evt[1] for evt in worker.ui_q.queue)
