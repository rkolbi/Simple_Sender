import queue

import pytest

from simple_sender.grbl_worker import GrblWorker, serial as serial_mod
from simple_sender.utils.constants import RX_BUFFER_SIZE, RX_BUFFER_SAFETY

pytestmark = pytest.mark.unit


def test_write_timeout_disconnects(ui_queue) -> None:
    if serial_mod is None:
        pytest.skip("pyserial not available")

    class _TimeoutSerial:
        def __init__(self) -> None:
            self.is_open = True

        def write(self, _data: bytes) -> int:
            raise serial_mod.SerialTimeoutException("timeout")

        def close(self) -> None:
            self.is_open = False

    worker = GrblWorker(ui_queue)
    worker.ser = _TimeoutSerial()

    assert worker._write_line("G0 X0") is False
    assert worker.ser is None

    events = []
    while True:
        try:
            events.append(ui_queue.get_nowait())
        except queue.Empty:
            break

    assert ("conn", False, None) in events
    assert any(evt[0] == "stream_state" and evt[1] == "stopped" for evt in events)


def test_manual_error_emits_event(ui_queue) -> None:
    worker = GrblWorker(ui_queue)
    worker._last_manual_source = "unit"

    worker._handle_rx_line("error:2")

    events = []
    while True:
        try:
            events.append(ui_queue.get_nowait())
        except queue.Empty:
            break

    assert ("manual_error", "error:2", "unit") in events


def test_manual_queue_blocked_during_alarm(connected_worker, dummy_serial) -> None:
    worker = connected_worker
    worker._alarm_active = True
    worker._outgoing_q.put("G0 X0")

    worker._process_manual_queue()

    assert dummy_serial.writes == []
    with pytest.raises(queue.Empty):
        worker._outgoing_q.get_nowait()
    assert worker._manual_pending_item is None


def test_grbl_banner_sets_ready(ui_queue) -> None:
    worker = GrblWorker(ui_queue)

    worker._handle_rx_line("Grbl 1.1h ['$' for help]")

    assert worker._ready
    assert ("ready", True) in list(ui_queue.queue)


def test_status_buffer_report_updates_window(ui_queue) -> None:
    worker = GrblWorker(ui_queue)
    worker._rx_window = 16

    worker._handle_rx_line("<Idle|Bf:15,100|FS:0,0>")

    assert worker._rx_window == RX_BUFFER_SIZE


def test_status_alarm_report_sets_alarm(ui_queue) -> None:
    worker = GrblWorker(ui_queue)

    worker._handle_rx_line("<Alarm:1|MPos:0,0,0|FS:0,0>")

    assert worker._alarm_active
    assert any(evt[0] == "alarm" for evt in list(ui_queue.queue))


def test_status_buffer_report_ignored_when_busy(ui_queue) -> None:
    worker = GrblWorker(ui_queue)
    worker._rx_window = 64
    worker._stream_buf_used = 10

    worker._handle_rx_line("<Idle|Bf:15,100|FS:0,0>")

    assert worker._rx_window == 64


def test_status_query_failure_limit_clamps(ui_queue) -> None:
    worker = GrblWorker(ui_queue)

    worker.set_status_query_failure_limit(0)
    assert worker._status_query_failure_limit == 1

    worker.set_status_query_failure_limit(42)
    assert worker._status_query_failure_limit == 10


def test_manual_queue_drops_if_buffer_too_small(connected_worker, dummy_serial) -> None:
    worker = connected_worker
    worker._rx_window = RX_BUFFER_SAFETY + 1
    worker._outgoing_q.put("G0 X0")

    worker._process_manual_queue()

    assert dummy_serial.writes == []
    assert worker._manual_pending_item is None
    assert any(
        evt[0] == "log" and "Line too long for buffer" in evt[1]
        for evt in list(worker.ui_q.queue)
    )
