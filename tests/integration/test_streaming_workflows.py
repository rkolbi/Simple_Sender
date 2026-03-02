import pytest

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.utils.constants import RT_HOLD, RT_RESUME

pytestmark = [pytest.mark.integration, pytest.mark.streaming]


def _drive_stream_to_completion(worker: GrblWorker, max_loops: int = 100) -> None:
    for _ in range(max_loops):
        worker._process_stream_queue()
        while worker._stream_line_queue:
            worker._handle_rx_line("ok")
        if worker._send_index >= len(worker._gcode) and not worker._stream_line_queue:
            break
    worker._process_stream_queue()


def test_streaming_lifecycle_completes(connected_worker) -> None:
    worker = connected_worker
    lines = ["G0 X0", "G0 X1", "G0 X2"]
    worker.load_gcode(lines)
    worker.start_stream()

    _drive_stream_to_completion(worker)

    assert worker._streaming is False
    assert worker._ack_index == len(lines) - 1
    assert ("stream_state", "done", None) in list(worker.ui_q.queue)


def test_pause_resume_streaming_cycle(connected_worker, dummy_serial) -> None:
    worker = connected_worker
    lines = ["G0 X0", "G0 X1", "G0 X2"]
    worker.load_gcode(lines)
    worker.start_stream()

    worker._process_stream_queue()
    worker.pause_stream()

    assert worker._paused is True
    assert RT_HOLD in dummy_serial.writes

    worker.resume_stream()

    assert worker._paused is False
    assert RT_RESUME in dummy_serial.writes

    _drive_stream_to_completion(worker)

    assert worker._streaming is False


def test_streaming_pauses_on_m0_and_resumes(connected_worker, dummy_serial) -> None:
    worker = connected_worker
    lines = ["G0 X0", "M0", "G0 X1"]
    worker.load_gcode(lines)
    worker.start_stream()

    worker._process_stream_queue()
    worker._handle_rx_line("ok")
    worker._handle_rx_line("ok")

    assert worker._paused is True
    assert ("stream_state", "paused", None) in list(worker.ui_q.queue)
    assert RT_HOLD in dummy_serial.writes

    worker.resume_stream()
    assert RT_RESUME in dummy_serial.writes

    _drive_stream_to_completion(worker)

    assert worker._streaming is False
    assert worker._ack_index == len(lines) - 1


def test_streaming_disconnects_on_serial_jitter_during_write(monkeypatch, connected_worker) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    class _JitterSerial:
        def __init__(self) -> None:
            self.is_open = True
            self.writes: list[bytes] = []
            self._calls = 0

        def write(self, data: bytes) -> int:
            self._calls += 1
            if self._calls >= 2:
                self.is_open = False
                raise _SerialStub.SerialException("usb jitter")
            self.writes.append(data)
            return len(data)

        def close(self) -> None:
            self.is_open = False

    worker = connected_worker
    worker.ser = _JitterSerial()
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    worker.load_gcode(["G0 X0", "G0 X1", "G0 X2"], name="jitter.nc")
    worker.start_stream()
    worker._process_stream_queue()

    assert worker.is_connected() is False
    assert worker._streaming is False
    events = list(worker.ui_q.queue)
    assert any(
        evt[0] == "stream_interrupted" and evt[1] is True and "Serial write error" in str(evt[2])
        for evt in events
    )
    assert ("conn", False, None) in events
    assert any(
        evt[0] == "stream_state" and evt[1] == "stopped" and "Serial write error" in str(evt[2])
        for evt in events
    )


def test_streaming_reset_to_continue_triggers_alarm_state(connected_worker) -> None:
    worker = connected_worker
    worker.load_gcode(["G0 X0"] * 5)
    worker.start_stream()
    worker._process_stream_queue()

    assert worker._stream_buf_used > 0

    worker._handle_rx_line("[MSG:Reset to continue]")

    assert worker._alarm_active
    assert worker._streaming is False
    assert worker._paused is False
    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue


def test_streaming_resume_after_disconnect_from_ack_index(connected_worker) -> None:
    worker = connected_worker
    lines = ["G0 X0", "G0 X1", "G0 X2", "G0 X3", "G0 X4"]
    worker.load_gcode(lines, name="resume.nc")
    worker.start_stream()
    worker._process_stream_queue()
    worker._handle_rx_line("ok")
    worker._handle_rx_line("ok")
    acked_before = worker._ack_index
    assert acked_before == 1

    worker._signal_disconnect("usb blip")
    assert worker.is_connected() is False
    assert worker._streaming is False

    class _SerialReconnect:
        def __init__(self) -> None:
            self.is_open = True
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return len(data)

    resumed_serial = _SerialReconnect()
    worker.ser = resumed_serial
    worker.start_stream_from(acked_before + 1, preamble=["G21", "G90"])
    _drive_stream_to_completion(worker)

    assert worker._streaming is False
    assert worker._ack_index == len(lines) - 1
    assert b"G21\n" in resumed_serial.writes
    assert b"G90\n" in resumed_serial.writes
