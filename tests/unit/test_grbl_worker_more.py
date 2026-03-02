import queue

import pytest

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.types import StreamQueueItem
from simple_sender.utils.constants import RX_BUFFER_SIZE
from simple_sender.utils.exceptions import InvalidParameterError


class _DummySerial:
    def __init__(self) -> None:
        self.is_open = True
        self.writes = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_handle_alarm_resets_streaming_state(monkeypatch) -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = True
    worker._stream_buf_used = 10
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))
    worker._outgoing_q.put("G0 X0")

    monkeypatch.setattr(grbl_worker, "annotate_grbl_alarm", lambda msg: f"ALARM::{msg}")

    worker._handle_alarm("ALARM:1")

    assert worker._alarm_active is True
    assert worker._streaming is False
    assert worker._paused is False
    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue
    assert worker._outgoing_q.empty()
    assert worker._abort_writes.is_set()
    assert ("stream_state", "alarm", "ALARM::ALARM:1") in list(worker.ui_q.queue)


def test_handle_rx_line_error_pauses_stream_and_emits() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))
    worker.send_realtime = lambda *_args, **_kwargs: None
    worker._format_stream_error = lambda *_args, **_kwargs: "ERR"

    worker._handle_rx_line("error:2")

    events = list(worker.ui_q.queue)
    assert ("stream_state", "paused", None) in events
    assert ("stream_error", "ERR", 0, "G0 X0", worker._gcode_name) in events
    assert ("log", "[stream error] ERR") in events
    assert worker._paused is True
    assert worker._ack_index == 0


def test_handle_rx_line_status_does_not_update_rx_window_when_pending() -> None:
    worker = _make_worker()
    worker._rx_window = 123
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))

    worker._handle_rx_line("<Idle|Bf:5,10|FS:0,0>")

    assert worker._rx_window == 123


def test_handle_rx_line_status_updates_min_capacity() -> None:
    worker = _make_worker()
    worker._stream_line_queue.clear()
    worker._stream_pending_item = None
    worker._manual_pending_item = None
    worker._resume_preamble.clear()
    worker._stream_buf_used = 0

    worker._handle_rx_line("<Idle|Bf:5,10|FS:0,0>")

    assert worker._rx_window == RX_BUFFER_SIZE


def test_set_status_poll_interval_validates() -> None:
    worker = _make_worker()

    with pytest.raises(InvalidParameterError):
        worker.set_status_poll_interval(0)

    worker.set_status_poll_interval(0.5)
    assert worker._status_poll_interval == 0.5


def test_set_status_query_failure_limit_clamps() -> None:
    worker = _make_worker()

    worker.set_status_query_failure_limit(-1)
    assert worker._status_query_failure_limit == 1

    worker.set_status_query_failure_limit(100)
    assert worker._status_query_failure_limit == 10


def test_reset_clears_streaming_and_emits_state() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = True
    worker._ready = True
    worker._stream_buf_used = 5
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))
    worker.send_realtime = lambda *_args, **_kwargs: None

    worker.reset()

    assert worker._ready is False
    assert worker._streaming is False
    assert worker._paused is False
    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue
    assert ("ready", False) in list(worker.ui_q.queue)
    assert ("stream_state", "stopped", None) in list(worker.ui_q.queue)
