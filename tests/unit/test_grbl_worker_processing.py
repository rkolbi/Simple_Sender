import queue

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.types import ManualPendingItem, StreamQueueItem
from simple_sender.utils.constants import MAX_LINE_LENGTH, RX_BUFFER_SAFETY


class _DummySerial:
    def __init__(self, write_result=None, write_exc: Exception | None = None) -> None:
        self.is_open = True
        self._write_result = write_result
        self._write_exc = write_exc

    def write(self, _data: bytes) -> int:
        if self._write_exc:
            raise self._write_exc
        if self._write_result is not None:
            return self._write_result
        return len(_data)


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_write_line_handles_timeout_and_disconnect(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser = _DummySerial(write_result=0)
    reasons = []
    worker._signal_disconnect = lambda reason=None: reasons.append(reason)

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    assert worker._write_line("G0 X0") is False
    assert any("Serial write timeout" in (reason or "") for reason in reasons)
    assert any(evt[0] == "log" and "write timeout" in evt[1] for evt in worker.ui_q.queue)


def test_write_line_handles_serial_exception(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser = _DummySerial(write_exc=_SerialStub.SerialException("bad"))
    reasons = []
    worker._signal_disconnect = lambda reason=None: reasons.append(reason)

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    assert worker._write_line("G0 X0") is False
    assert any("Serial write error" in (reason or "") for reason in reasons)
    assert any(evt[0] == "log" and "write error" in evt[1] for evt in worker.ui_q.queue)


def test_write_line_handles_generic_exception(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser = _DummySerial(write_exc=RuntimeError("boom"))
    reasons = []
    worker._signal_disconnect = lambda reason=None: reasons.append(reason)

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    assert worker._write_line("G0 X0") is False
    assert any("Unexpected write error" in (reason or "") for reason in reasons)
    assert any(evt[0] == "log" and "write error" in evt[1] for evt in worker.ui_q.queue)


def test_process_manual_queue_purges_jogs() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._purge_jog_queue.set()
    worker._outgoing_q.put("$J=G91 X1 F100")
    worker._outgoing_q.put("G0 X0")
    worker._manual_pending_item = ManualPendingItem("$J=G91 X1", b"$J=G91 X1\n", 12)

    worker._process_manual_queue()

    assert worker._manual_pending_item is None
    assert list(worker._outgoing_q.queue) == ["G0 X0"]


def test_process_manual_queue_drops_long_line() -> None:
    worker = _make_worker()
    long_line = "G0 " + ("X" * (MAX_LINE_LENGTH + 10))
    worker._outgoing_q.put(long_line)

    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_manual_queue()

    assert worker._manual_pending_item is None
    assert any(
        evt[0] == "log" and "Line too long" in evt[1]
        for evt in list(worker.ui_q.queue)
    )


def test_process_manual_queue_drops_when_buffer_too_small() -> None:
    worker = _make_worker()
    worker._rx_window = RX_BUFFER_SAFETY + 2
    worker._stream_buf_used = 0
    worker._outgoing_q.put("G0 X0")

    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_manual_queue()

    assert worker._manual_pending_item is None
    assert any(
        evt[0] == "log" and "Line too long for buffer" in evt[1]
        for evt in list(worker.ui_q.queue)
    )


def test_process_manual_queue_sets_pending_when_buffer_full() -> None:
    worker = _make_worker()
    worker._rx_window = 20
    worker._stream_buf_used = 15
    worker._outgoing_q.put("G0 X0")

    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_manual_queue()

    assert worker._manual_pending_item is not None
    assert worker._outgoing_q.empty()


def test_handle_rx_line_ok_updates_ack_and_progress() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._stream_buf_used = 5
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))

    worker._handle_rx_line("ok")

    events = list(worker.ui_q.queue)
    assert ("gcode_acked", 0) in events
    assert ("progress", 1, 1) in events
    assert worker._ack_index == 0
    assert worker._stream_buf_used == 0


def test_handle_rx_line_manual_error_when_not_streaming() -> None:
    worker = _make_worker()
    worker._last_manual_source = "macro"

    worker._handle_rx_line("error:2")

    assert ("manual_error", "error:2", "macro") in list(worker.ui_q.queue)


def test_handle_rx_line_manual_error_uses_queued_source() -> None:
    worker = _make_worker()
    worker._streaming = False
    worker._paused = False
    worker._last_manual_source = "macro"
    worker._stream_buf_used = 6
    worker._stream_line_queue.append(StreamQueueItem(6, False, None, "$J=G91 X1", "jog_button"))

    worker._handle_rx_line("error:15")

    assert ("manual_error", "error:15", "jog_button") in list(worker.ui_q.queue)


def test_handle_rx_line_status_updates_rx_window() -> None:
    worker = _make_worker()
    worker._streaming = False
    worker._paused = False
    worker._stream_line_queue.clear()
    worker._stream_pending_item = None
    worker._manual_pending_item = None
    worker._resume_preamble.clear()
    worker._stream_buf_used = 0
    called = []
    worker._emit_buffer_fill = lambda: called.append(True)

    worker._handle_rx_line("<Idle|Bf:15,200|FS:0,0>")

    assert worker._rx_window == 200
    assert called == [True]
    assert ("status", "<Idle|Bf:15,200|FS:0,0>") in list(worker.ui_q.queue)


def test_handle_rx_line_grbl_banner_marks_ready() -> None:
    worker = _make_worker()

    worker._handle_rx_line("Grbl 1.1h ['$' for help]")

    assert worker._ready is True
    assert ("ready", True) in list(worker.ui_q.queue)
