import pytest

from simple_sender.utils.constants import RX_BUFFER_SAFETY

pytestmark = [pytest.mark.integration, pytest.mark.streaming]


def test_stream_buffer_prevents_overflow(connected_worker) -> None:
    worker = connected_worker
    worker.load_gcode(["G0 X10"] * 200)
    worker.start_stream()

    for _ in range(200):
        worker._process_stream_queue()
        if worker._stream_pending_item is not None:
            break

    usable = max(1, int(worker._rx_window) - RX_BUFFER_SAFETY)
    assert worker._stream_buf_used <= usable


def test_stream_buffer_recovers_after_ack(connected_worker, dummy_serial) -> None:
    worker = connected_worker
    worker._rx_window = 64
    worker.load_gcode(["G0 X10"] * 50)
    worker.start_stream()

    for _ in range(200):
        worker._process_stream_queue()
        if worker._stream_pending_item is not None:
            break

    assert worker._stream_pending_item is not None

    writes_before = len(dummy_serial.writes)
    send_before = worker._send_index

    for _ in range(10):
        if not worker._stream_line_queue:
            break
        worker._handle_rx_line("ok")
        worker._process_stream_queue()

    writes_after = len(dummy_serial.writes)
    assert writes_after > writes_before
    assert worker._send_index > send_before


def test_stream_buffer_tracks_bytes(connected_worker) -> None:
    worker = connected_worker
    commands = ["G0 X10 Y20", "G1 X30 F100", "G2 X40 I5"]
    worker.load_gcode(commands)
    worker.start_stream()

    worker._process_stream_queue()

    expected_size = sum(len(worker._encode_line_payload(cmd)) for cmd in commands)
    assert worker._stream_buf_used == expected_size

    for _ in range(len(commands)):
        worker._handle_rx_line("ok")

    assert worker._stream_buf_used == 0


def test_stream_error_pauses_stream(connected_worker) -> None:
    worker = connected_worker
    worker.load_gcode(["G0 X0", "G0 X1", "G0 X2"])
    worker.start_stream()
    worker._process_stream_queue()

    worker._handle_rx_line("ok")
    worker._handle_rx_line("error:2")

    assert worker._streaming
    assert worker._paused
    assert worker._ack_index == 1


def test_alarm_clears_stream_state(connected_worker) -> None:
    worker = connected_worker
    worker.load_gcode(["G0 X0"] * 5)
    worker.start_stream()
    worker._process_stream_queue()

    assert worker._stream_buf_used > 0

    worker._handle_rx_line("ALARM:1")

    assert worker._alarm_active
    assert worker._streaming is False
    assert worker._paused is False
    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue
