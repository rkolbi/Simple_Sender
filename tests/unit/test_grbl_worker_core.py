import queue

import pytest

from simple_sender import grbl_worker
from simple_sender.grbl_worker import GrblWorker
from simple_sender.types import ManualPendingItem, StreamPendingItem, StreamQueueItem
from simple_sender.utils.constants import (
    BUFFER_EMIT_INTERVAL,
    MANUAL_COMMAND_QUEUE_MAXSIZE,
    RX_BUFFER_SIZE,
    TX_THROUGHPUT_EMIT_INTERVAL,
)


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


def test_send_immediate_blocks_during_stream() -> None:
    worker = _make_worker()
    worker._streaming = True

    worker.send_immediate("G0 X0")

    assert worker._outgoing_q.empty()
    assert ("log", "[manual blocked] G0 X0 (streaming active)") in list(worker.ui_q.queue)


def test_send_immediate_allows_only_unlock_and_home_during_alarm() -> None:
    worker = _make_worker()
    worker._alarm_active = True

    worker.send_immediate("$X")
    worker.send_immediate("G0 X0")
    worker.send_immediate("$H")

    queued = list(worker._outgoing_q.queue)
    assert queued == ["$X", "$H"]


def test_send_immediate_drops_when_manual_queue_is_full(monkeypatch) -> None:
    worker = _make_worker()
    for idx in range(MANUAL_COMMAND_QUEUE_MAXSIZE):
        worker._outgoing_q.put_nowait(f"G0 X{idx}")
        worker._manual_source_queue.append("manual")
    monkeypatch.setattr(grbl_worker.time, "time", lambda: 100.0)

    worker.send_immediate("G0 X999", source="console")

    assert worker._outgoing_q.qsize() == MANUAL_COMMAND_QUEUE_MAXSIZE
    assert "G0 X999" not in list(worker._outgoing_q.queue)
    assert any(evt[0] == "manual_queue_drop" and evt[1] >= 1 for evt in worker.ui_q.queue)
    assert any(
        evt[0] == "log" and "queue is full" in evt[1]
        for evt in list(worker.ui_q.queue)
    )


def test_manual_queue_busy_and_backpressure() -> None:
    worker = _make_worker()
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 1)

    assert worker.manual_queue_busy() is True
    assert worker.manual_queue_backpressure() is True

    worker._manual_pending_item = None
    worker._stream_line_queue.append(StreamQueueItem(5, False, None, "G0 X0"))

    assert worker.manual_queue_busy() is True
    assert worker.manual_queue_backpressure() is False

    for idx in range(MANUAL_COMMAND_QUEUE_MAXSIZE):
        worker._outgoing_q.put_nowait(f"G0 X{idx}")
    assert worker.manual_queue_backpressure() is True


def test_sanitize_stream_line_removes_dry_run_tokens() -> None:
    worker = _make_worker()
    worker.set_dry_run_sanitize(True)

    sanitized = worker._sanitize_stream_line("G1 X1 M3 S1000 T2 M7")

    assert "M3" not in sanitized
    assert "M7" not in sanitized
    assert "S1000" not in sanitized
    assert "T2" not in sanitized
    assert "G1 X1" in sanitized

    worker.set_dry_run_sanitize(False)
    assert worker._sanitize_stream_line("G1 X1 M3") == "G1 X1 M3"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("G0 X0 M0", "M0"),
        ("M1", "M1"),
        ("M06", "M6"),
        ("G0 X0", None),
        ("M10", None),
    ],
)
def test_pause_reason_for_line(line: str, expected: str | None) -> None:
    worker = _make_worker()

    assert worker._pause_reason_for_line(line) == expected


def test_maybe_pause_after_ack_triggers_pause() -> None:
    worker = _make_worker()
    pauses = []
    worker._pause_stream = lambda reason=None: pauses.append(reason)
    worker._pause_after_idx = 2
    worker._pause_after_reason = None

    worker._maybe_pause_after_ack(2)

    assert pauses == ["M0/M1/M6"]
    assert worker._pause_after_idx is None
    assert worker._pause_after_reason is None
    assert ("log", "[stream] Paused on M0/M1/M6 at line 3") in list(worker.ui_q.queue)


def test_format_stream_error_includes_name_and_line(monkeypatch) -> None:
    worker = _make_worker()
    worker._gcode_name = "job.nc"

    monkeypatch.setattr(grbl_worker, "annotate_grbl_error", lambda _raw: "ERR")

    text = worker._format_stream_error("error:2", idx=4, line_text="G0 X0")

    assert text == "ERR | job.nc line 5 | G0 X0"


def test_emit_buffer_fill_rate_limits(monkeypatch) -> None:
    worker = _make_worker()
    worker._rx_window = 10
    worker._stream_buf_used = 5

    times = iter([100.0, 100.0 + BUFFER_EMIT_INTERVAL / 2, 100.0 + BUFFER_EMIT_INTERVAL + 0.1])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))

    worker._emit_buffer_fill()
    worker._emit_buffer_fill()
    worker._emit_buffer_fill()

    events = [evt for evt in list(worker.ui_q.queue) if evt[0] == "buffer_fill"]
    assert len(events) == 2


def test_record_tx_bytes_emits_throughput(monkeypatch) -> None:
    worker = _make_worker()
    times = iter([100.0, 100.0 + TX_THROUGHPUT_EMIT_INTERVAL + 0.1])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))

    worker._record_tx_bytes(100)

    event = worker.ui_q.get_nowait()
    assert event[0] == "throughput"
    assert event[1] > 0


def test_record_tx_bytes_ignores_zero() -> None:
    worker = _make_worker()
    worker._record_tx_bytes(0)

    assert worker.ui_q.empty()


def test_record_tx_line_updates_rate(monkeypatch) -> None:
    worker = _make_worker()
    times = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))

    worker._record_tx_line()
    worker._record_tx_line()
    worker._record_tx_line()

    assert worker._tx_lines_per_sec > 0


def test_record_ack_latency_updates_average() -> None:
    worker = _make_worker()

    worker._record_ack_latency(10.0)
    worker._record_ack_latency(30.0)

    assert worker._ok_latency_ms_last == 30.0
    assert worker._ok_latency_ms_avg == 20.0
    assert worker._ok_latency_sample_count == 2


def test_load_gcode_precomputes_send_cache_for_in_memory_lines() -> None:
    worker = _make_worker()

    worker.load_gcode(["G0 X0", "M0"])

    assert worker._gcode_payload_cache is not None
    assert worker._gcode_payload_cache[0] == b"G0 X0\n"
    assert worker._gcode_payload_cache[1] == b"M0\n"
    assert worker._gcode_pause_reason_cache is not None
    assert worker._gcode_pause_reason_cache[0] is None
    assert worker._gcode_pause_reason_cache[1] == "M0"


def test_process_stream_queue_uses_precomputed_payload_cache() -> None:
    worker = _make_worker()
    worker.load_gcode(["G0 X0"])
    worker.start_stream()

    def _should_not_encode(_line: str):
        raise AssertionError("Expected stream queue to use precomputed payload cache")

    worker._build_line_payload = _should_not_encode  # type: ignore[assignment]

    worker._process_stream_queue()

    assert any(evt[0] == "gcode_sent" for evt in list(worker.ui_q.queue))


def test_get_runtime_metrics_includes_queue_depth_snapshot(monkeypatch) -> None:
    worker = _make_worker()
    times = iter([100.0, 100.3, 100.6])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))
    worker._outgoing_q.put_nowait("G0 X1")

    metrics = worker.get_runtime_metrics()

    assert "queue_depth_last" in metrics
    queue_last = metrics["queue_depth_last"]
    assert queue_last["stream"] >= 1
    assert queue_last["manual"] >= 1
    assert metrics["tx_loop_cycles"] >= 0
    assert metrics["tx_loop_idle_cycles"] >= 0
    assert metrics["tx_loop_active_cycles"] >= 0
    assert metrics["tx_loop_idle_wait_total_s"] >= 0.0
    assert metrics["tx_loop_idle_ratio"] >= 0.0


def test_clear_outgoing_resets_pending_and_emits_buffer() -> None:
    worker = _make_worker()
    worker._outgoing_q.put("G0 X0")
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 1)
    called = []
    worker._emit_buffer_fill = lambda: called.append(True)

    worker._clear_outgoing()

    assert worker._manual_pending_item is None
    assert worker._outgoing_q.empty()
    assert called == [True]


def test_reset_stream_buffer_resets_state() -> None:
    worker = _make_worker()
    worker._stream_buf_used = 10
    worker._stream_line_queue.append(StreamQueueItem(5, True, 1, "G0 X0"))
    worker._stream_pending_item = StreamPendingItem("G0 X0", True, 1)
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 1)
    worker._resume_preamble.append("G21")
    worker._rx_window = 64
    worker._send_index = 5
    worker._ack_index = 3
    worker._pause_after_idx = 2
    worker._pause_after_reason = "M0"
    worker._tx_bytes_window.append((0.0, 10))
    worker._last_tx_emit_ts = 5.0

    worker._reset_stream_buffer()

    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue
    assert worker._stream_pending_item is None
    assert worker._manual_pending_item is None
    assert not worker._resume_preamble
    assert worker._rx_window == RX_BUFFER_SIZE
    assert worker._send_index == 0
    assert worker._ack_index == -1
    assert worker._pause_after_idx is None
    assert worker._pause_after_reason is None
    assert not worker._tx_bytes_window
    assert worker._last_tx_emit_ts == 0.0


def test_start_stream_from_clamps_and_sets_preamble() -> None:
    worker = _make_worker()
    worker.load_gcode(["G0 X0", "G0 X1", "G0 X2"])

    worker.start_stream_from(99, preamble=["", "G21", "  ", "G90"])

    assert worker._send_index == 2
    assert worker._ack_index == 1
    assert list(worker._resume_preamble) == ["G21", "G90"]
