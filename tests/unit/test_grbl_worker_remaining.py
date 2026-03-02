import collections
import queue
import re

import pytest

from simple_sender import grbl_worker
from simple_sender import grbl_worker_status
from simple_sender.grbl_worker import GrblWorker
from simple_sender.types import ManualPendingItem, StreamQueueItem
from simple_sender.utils.constants import WATCHDOG_HOMING_TIMEOUT
from simple_sender.utils.exceptions import SerialConnectionError, SerialWriteError


class _DummySerial:
    def __init__(self, *args, is_open: bool = True, **kwargs) -> None:
        self.is_open = is_open
        self.closed = False

    def write(self, data: bytes):
        return len(data)

    def reset_input_buffer(self):
        return None

    def reset_output_buffer(self):
        return None

    def close(self) -> None:
        self.closed = True


class _DummyThread:
    def __init__(self, name: str = "thread", alive: bool = True) -> None:
        self.name = name
        self._alive = alive
        self.joined = False

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


class _ToggleEvent:
    def __init__(self) -> None:
        self.calls = 0
        self._set = False

    def is_set(self) -> bool:
        self.calls += 1
        return self._set or self.calls > 1

    def set(self) -> None:
        self._set = True

    def wait(self, _timeout: float | None = None) -> bool:
        self._set = True
        return True


class _WaitEvent:
    def __init__(self) -> None:
        self.waited = None

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float | None = None) -> bool:
        self.waited = timeout
        return True


class _RaisingQueue:
    def __init__(self) -> None:
        self.items = []

    def put(self, item):
        self.items.append(item)
        raise RuntimeError("boom")


class _FlakyQueue:
    def __init__(self) -> None:
        self.count = 0

    def put(self, _item):
        self.count += 1
        if self.count == 2:
            raise RuntimeError("boom")


class _FalseDeque:
    def __init__(self) -> None:
        self.items = []

    def append(self, item) -> None:
        self.items.append(item)

    def __bool__(self) -> bool:
        return False


class _BadPopDeque:
    def __init__(self) -> None:
        self.items = []

    def append(self, item) -> None:
        self.items.append(item)

    def pop(self):
        raise RuntimeError("bad pop")

    def __bool__(self) -> bool:
        return True


class _BreakLock:
    def __init__(self, worker: GrblWorker) -> None:
        self.worker = worker

    def __enter__(self):
        self.worker._streaming = False
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


def _make_worker() -> GrblWorker:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    return worker


def test_context_manager_calls_disconnect() -> None:
    worker = GrblWorker(queue.Queue())
    calls = []
    worker.disconnect = lambda: calls.append(True)

    with worker as ctx:
        assert ctx is worker

    assert calls == [True]


def test_context_manager_logs_disconnect_error() -> None:
    worker = GrblWorker(queue.Queue())
    worker.disconnect = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    assert worker.__exit__(None, None, None) is False


def test_list_ports_returns_devices(monkeypatch) -> None:
    class _Ports:
        @staticmethod
        def comports():
            return [type("P", (), {"device": "COM9"})()]

    worker = _make_worker()
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker, "list_ports", _Ports)

    assert worker.list_ports() == ["COM9"]


def test_list_ports_returns_empty_on_provider_error(monkeypatch) -> None:
    class _Ports:
        @staticmethod
        def comports():
            raise RuntimeError("no backend")

    worker = _make_worker()
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker, "list_ports", _Ports)

    assert worker.list_ports() == []


def test_import_without_serial_sets_flag(monkeypatch) -> None:
    import builtins
    import importlib.util
    import sys

    original_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "serial" or name.startswith("serial."):
            raise ImportError("no serial")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "serial", raising=False)
    monkeypatch.delitem(sys.modules, "serial.tools", raising=False)

    spec = importlib.util.spec_from_file_location(
        "simple_sender._grbl_worker_no_serial",
        grbl_worker.__file__,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.SERIAL_AVAILABLE is False
    finally:
        sys.modules.pop(spec.name, None)


def test_is_streaming_reflects_state() -> None:
    worker = _make_worker()
    worker._streaming = True

    assert worker.is_streaming() is True


def test_connect_serial_exception_raises(monkeypatch) -> None:
    class _SerialStub:
        class SerialException(Exception):
            pass

        class Serial:
            def __init__(self, *args, **kwargs) -> None:
                raise _SerialStub.SerialException("bad")

    worker = GrblWorker(queue.Queue())
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)

    with pytest.raises(SerialConnectionError):
        worker.connect("COM7")

    assert worker.ser is None


def test_connect_disconnect_when_already_connected(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial()
    disconnected = []

    def _disconnect():
        disconnected.append(True)
        worker.ser = None

    class _SerialStub:
        class SerialException(Exception):
            pass

        Serial = _DummySerial

    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker.threading, "Thread", lambda *args, **kwargs: _DummyThread(kwargs.get("name", "thread")))
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)
    worker.disconnect = _disconnect

    worker.connect("COM3")

    assert disconnected == [True]


def test_disconnect_logs_unexpected_error(monkeypatch) -> None:
    class _SerialStub:
        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser.close = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    worker.disconnect()


def test_connect_reset_buffer_error(monkeypatch) -> None:
    class _SerialImpl(_DummySerial):
        def reset_input_buffer(self):
            raise _SerialStub.SerialException("bad")

    class _SerialStub:
        class SerialException(Exception):
            pass

        Serial = _SerialImpl

    worker = GrblWorker(queue.Queue())
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)
    monkeypatch.setattr(grbl_worker, "SERIAL_AVAILABLE", True)
    monkeypatch.setattr(grbl_worker.threading, "Thread", lambda *args, **kwargs: _DummyThread(kwargs.get("name", "thread")))
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    worker.connect("COM3")

    assert worker.is_connected() is True


def test_disconnect_thread_join_warning(monkeypatch) -> None:
    worker = _make_worker()
    worker._rx_thread = _DummyThread(name="rx", alive=True)
    worker._tx_thread = _DummyThread(name="tx", alive=True)
    worker._status_thread = _DummyThread(name="status", alive=True)
    monkeypatch.setattr(grbl_worker, "THREAD_JOIN_TIMEOUT", 0)

    worker.disconnect()

    assert worker._rx_thread is None


def test_send_immediate_not_connected() -> None:
    worker = GrblWorker(queue.Queue())

    worker.send_immediate("G0 X0")

    assert worker._outgoing_q.empty()


def test_send_immediate_source_and_blank() -> None:
    worker = _make_worker()

    worker.send_immediate("  G0 X0  ", source="ui")
    worker.send_immediate("   ")

    assert worker._last_manual_source == "ui"
    assert worker._outgoing_q.get_nowait() == "G0 X0"
    assert worker._outgoing_q.empty()


def test_send_immediate_home_suspends_watchdog(monkeypatch) -> None:
    worker = _make_worker()
    monkeypatch.setattr(grbl_worker_status.time, "time", lambda: 100.0)

    worker.send_immediate("$H")

    assert worker._watchdog_ignore_until == 100.0 + WATCHDOG_HOMING_TIMEOUT
    assert worker._watchdog_ignore_reason == "homing"


def test_send_immediate_streaming_uiq_failure() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker.ui_q = _RaisingQueue()

    worker.send_immediate("G0 X0")


def test_send_realtime_written_none(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    worker = _make_worker()
    worker.ser.write = lambda _data: None
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    with pytest.raises(SerialWriteError):
        worker.send_realtime(b"?")


def test_send_realtime_not_connected() -> None:
    worker = GrblWorker(queue.Queue())

    worker.send_realtime(b"?")


def test_reset_logs_on_serial_write_error() -> None:
    worker = _make_worker()
    worker.send_realtime = lambda _cmd: (_ for _ in ()).throw(SerialWriteError("bad"))

    worker.reset()

    assert any(evt[0] == "log" and "reset failed" in evt[1] for evt in worker.ui_q.queue)


def test_resume_sends_realtime() -> None:
    worker = _make_worker()
    calls = []
    worker.send_realtime = lambda cmd: calls.append(cmd)

    worker.resume()

    assert calls == [grbl_worker.RT_RESUME]


def test_cancel_pending_jogs_sets_flag() -> None:
    worker = _make_worker()
    called = []
    worker._emit_buffer_fill = lambda: called.append(True)

    worker.cancel_pending_jogs()

    assert worker._purge_jog_queue.is_set() is True
    assert called == [True]


def test_manual_queue_busy_false_when_empty() -> None:
    worker = _make_worker()
    worker._manual_pending_item = None
    worker._stream_line_queue.clear()

    assert worker.manual_queue_busy() is False


def test_start_stream_early_returns() -> None:
    worker = GrblWorker(queue.Queue())
    worker.start_stream()
    assert worker._streaming is False

    worker.ser = _DummySerial()
    worker.start_stream()
    assert worker._streaming is False


def test_start_stream_logs_dry_run_message() -> None:
    worker = _make_worker()
    worker.load_gcode(["G0 X0"])
    worker.set_dry_run_sanitize(True)

    worker.start_stream()

    assert any(
        evt == ("log", "[dry run] Spindle/coolant/tool changes removed while streaming.")
        for evt in worker.ui_q.queue
    )


def test_start_stream_from_early_returns() -> None:
    worker = GrblWorker(queue.Queue())
    worker.start_stream_from(0)
    assert worker._streaming is False

    worker.ser = _DummySerial()
    worker.start_stream_from(0)
    assert worker._streaming is False


def test_start_stream_from_logs_dry_run() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker.set_dry_run_sanitize(True)

    worker.start_stream_from(0)

    assert any("[dry run]" in evt[1] for evt in worker.ui_q.queue if evt[0] == "log")


def test_resume_stream_success() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = True
    worker.resume = lambda: None

    worker.resume_stream()

    assert worker._paused is False
    assert ("stream_state", "running", None) in list(worker.ui_q.queue)


def test_pause_stream_noop_when_not_streaming() -> None:
    worker = _make_worker()
    worker._streaming = False

    worker._pause_stream()

    assert ("stream_state", "paused", None) not in list(worker.ui_q.queue)


def test_stop_stream_calls_reset() -> None:
    worker = _make_worker()
    calls = []
    worker.reset = lambda emit_state=False: calls.append(emit_state)

    worker.stop_stream()

    assert calls == [False]
    assert ("stream_state", "stopped", None) in list(worker.ui_q.queue)


def test_signal_disconnect_handles_close_errors() -> None:
    worker = GrblWorker(queue.Queue())

    class _BadSerial(_DummySerial):
        def close(self) -> None:
            raise RuntimeError("bad")

    worker.ser = _BadSerial()

    worker._signal_disconnect("reason")

    assert worker.ser is None
    assert ("log", "[disconnect] reason") in list(worker.ui_q.queue)


def test_set_status_query_failure_limit_invalid_value() -> None:
    worker = _make_worker()

    worker.set_status_query_failure_limit("bad")

    assert worker._status_query_failure_limit == 3


def test_handle_alarm_logs_failure(monkeypatch) -> None:
    worker = _make_worker()
    worker.ui_q = _RaisingQueue()

    worker._handle_alarm("ALARM:1")


def test_signal_disconnect_handles_ui_queue_failure() -> None:
    worker = _make_worker()
    worker.ui_q = _RaisingQueue()
    worker._emit_buffer_fill = lambda: None

    worker._signal_disconnect("reason")


def test_emit_exception_handles_ui_queue_failure() -> None:
    worker = _make_worker()
    worker.ui_q = _RaisingQueue()

    worker._emit_exception("context", RuntimeError("boom"))


def test_emit_exception_logs_traceback() -> None:
    worker = _make_worker()

    worker._emit_exception("context", RuntimeError("boom"))

    assert any(evt[0] == "log" and "context" in evt[1] for evt in worker.ui_q.queue)


def test_sanitize_stream_line_invalid_m_token(monkeypatch) -> None:
    worker = _make_worker()
    worker.set_dry_run_sanitize(True)
    monkeypatch.setattr(grbl_worker, "_SANITIZE_TOKEN_PAT", re.compile(r"Mbad"))

    assert worker._sanitize_stream_line("Mbad") == "Mbad"


def test_pause_reason_for_line_empty() -> None:
    worker = _make_worker()
    assert worker._pause_reason_for_line("") is None


def test_maybe_pause_after_ack_none() -> None:
    worker = _make_worker()
    worker._pause_after_idx = 1

    worker._maybe_pause_after_ack(None)

    assert worker._pause_after_idx == 1


def test_maybe_pause_after_ack_mismatch() -> None:
    worker = _make_worker()
    worker._pause_after_idx = 1

    worker._maybe_pause_after_ack(2)

    assert worker._pause_after_idx == 1


def test_write_line_not_connected_and_abort(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())
    assert worker._write_line("G0 X0") is False

    worker.ser = _DummySerial()
    worker._abort_writes.set()
    assert worker._write_line("G0 X0") is False


def test_write_line_written_none(monkeypatch) -> None:
    class _SerialStub:
        class SerialTimeoutException(Exception):
            pass

        class SerialException(Exception):
            pass

    class _SerialImpl(_DummySerial):
        def write(self, _data: bytes):
            return None

    worker = _make_worker()
    worker.ser = _SerialImpl()
    monkeypatch.setattr(grbl_worker, "serial", _SerialStub)

    assert worker._write_line("G0 X0") is False


def test_emit_buffer_fill_clamps_used(monkeypatch) -> None:
    worker = _make_worker()
    worker._rx_window = 5
    worker._stream_buf_used = 10
    monkeypatch.setattr(grbl_worker.time, "time", lambda: 0.0)

    worker._emit_buffer_fill()

    event = worker.ui_q.get_nowait()
    assert event == ("buffer_fill", 100, 5, 5)


def test_record_tx_bytes_drops_old_samples(monkeypatch) -> None:
    class _NoAppendDeque(collections.deque):
        def append(self, _item):
            return None

    worker = _make_worker()
    worker._tx_bytes_window = _NoAppendDeque([(0.0, 1)])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: 100.0)

    worker._record_tx_bytes(5)

    assert not worker._tx_bytes_window


def test_tx_loop_not_connected_branch(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())
    stop_evt = _ToggleEvent()
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    worker._tx_loop(stop_evt)

    assert stop_evt.is_set() is True


def test_tx_loop_wait_break() -> None:
    worker = _make_worker()
    stop_evt = _WaitEvent()
    worker._process_stream_queue = lambda: None
    worker._process_manual_queue = lambda: None

    worker._tx_loop(stop_evt)

    assert stop_evt.waited is not None


def test_process_stream_queue_breaks_when_not_streaming() -> None:
    worker = _make_worker()
    worker._streaming = False
    worker._paused = False

    worker._process_stream_queue()


def test_process_stream_queue_breaks_inside_lock() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    worker._stream_lock = _BreakLock(worker)

    worker._process_stream_queue()


def test_process_stream_queue_pause_after_idx_break() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._pause_after_idx = 0
    worker._send_index = 1

    worker._process_stream_queue()

    assert not worker._stream_line_queue


def test_process_stream_queue_resume_preamble_pops() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    worker._resume_preamble = collections.deque(["G21"])
    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_stream_queue()

    assert not worker._resume_preamble


def test_process_stream_queue_emits_spindle_state_from_raw_line_in_dry_run() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    worker._dry_run_sanitize = True
    worker._gcode = ["M3 S12000"]
    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_stream_queue()

    assert ("spindle_state", True, 0) in list(worker.ui_q.queue)


def test_process_stream_queue_emits_spindle_state_for_resume_preamble() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._paused = False
    worker._gcode = ["G1 X1"]
    worker._resume_preamble = collections.deque(["M3 S12000"])
    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_stream_queue()

    assert ("spindle_state", True, None) in list(worker.ui_q.queue)


def test_process_stream_queue_buffer_full_sets_pending() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._rx_window = 10
    worker._stream_buf_used = 5

    worker._process_stream_queue()

    assert worker._stream_pending_item is not None


def test_process_stream_queue_sets_pause_after_idx() -> None:
    worker = _make_worker()
    worker._gcode = ["M0"]
    worker._streaming = True
    worker._paused = False
    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_stream_queue()

    assert worker._pause_after_idx == 0
    assert worker._pause_after_reason == "M0"


def test_process_stream_queue_abort_pop_exception() -> None:
    class _BadDeque:
        def __init__(self) -> None:
            self.items = []

        def append(self, item):
            self.items.append(item)

        def pop(self):
            raise RuntimeError("bad pop")

        def __bool__(self):
            return bool(self.items)

    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._stream_line_queue = _BadDeque()

    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_stream_queue()

    assert worker._stream_buf_used == 0


def test_process_stream_queue_write_failure_pop_exception() -> None:
    class _BadDeque:
        def __init__(self) -> None:
            self.items = []

        def append(self, item):
            self.items.append(item)

        def pop(self):
            raise RuntimeError("bad pop")

        def __bool__(self):
            return bool(self.items)

    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._stream_line_queue = _BadDeque()
    worker._write_line = lambda *_args, **_kwargs: False

    worker._process_stream_queue()

    assert worker._stream_pending_item is not None


def test_process_stream_queue_abort_rollback_empty_queue() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._stream_line_queue = _FalseDeque()

    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_stream_queue()


def test_process_stream_queue_write_failure_disconnect() -> None:
    worker = _make_worker()
    worker._gcode = ["G0 X0"]
    worker._streaming = True
    worker._paused = False
    worker._stream_line_queue = _FalseDeque()
    worker._write_line = lambda *_args, **_kwargs: False
    worker.is_connected = lambda: False

    worker._process_stream_queue()


def test_process_manual_queue_alarm_clears_outgoing() -> None:
    worker = _make_worker()
    worker._alarm_active = True
    worker._outgoing_q.put("G0 X0")
    called = []
    worker._clear_outgoing = lambda: called.append(True)

    worker._process_manual_queue()

    assert called == [True]


def test_process_manual_queue_abort_after_encode() -> None:
    worker = _make_worker()
    worker._outgoing_q.put("G0 X0")

    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_manual_queue()

    assert worker._manual_pending_item is None


def test_process_manual_queue_write_failure_when_disconnected() -> None:
    worker = _make_worker()
    worker._outgoing_q.put("G0 X0")
    worker._write_line = lambda *_args, **_kwargs: False
    calls = {"count": 0}

    def _is_connected():
        calls["count"] += 1
        return calls["count"] == 1

    worker.is_connected = _is_connected

    worker._process_manual_queue()

    assert worker._manual_pending_item is None


def test_process_manual_queue_skips_blank_line() -> None:
    worker = _make_worker()
    worker._outgoing_q.put("   ")

    worker._process_manual_queue()

    assert worker._outgoing_q.empty()


def test_process_manual_queue_returns_when_not_connected() -> None:
    worker = GrblWorker(queue.Queue())

    worker._process_manual_queue()


def test_process_manual_queue_returns_when_abort() -> None:
    worker = _make_worker()
    worker._abort_writes.set()

    worker._process_manual_queue()


def test_process_manual_queue_uses_pending_item() -> None:
    worker = _make_worker()
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 6)
    worker._write_line = lambda *_args, **_kwargs: True

    worker._process_manual_queue()

    assert ("log_tx", "G0 X0") in list(worker.ui_q.queue)


def test_process_manual_queue_abort_pop_exception() -> None:
    worker = _make_worker()
    worker._stream_line_queue = _BadPopDeque()
    worker._outgoing_q.put("G0 X0")

    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_manual_queue()


def test_process_manual_queue_abort_empty_queue() -> None:
    worker = _make_worker()
    worker._stream_line_queue = _FalseDeque()
    worker._outgoing_q.put("G0 X0")

    original_encode = worker._encode_line_payload

    def _encode(line: str) -> bytes:
        worker._abort_writes.set()
        return original_encode(line)

    worker._encode_line_payload = _encode

    worker._process_manual_queue()


def test_process_manual_queue_write_failure_pop_exception() -> None:
    worker = _make_worker()
    worker._stream_line_queue = _BadPopDeque()
    worker._outgoing_q.put("G0 X0")
    worker._write_line = lambda *_args, **_kwargs: False
    worker.is_connected = lambda: True

    worker._process_manual_queue()

    assert worker._manual_pending_item is not None


def test_process_manual_queue_write_failure_empty_queue() -> None:
    worker = _make_worker()
    worker._stream_line_queue = _FalseDeque()
    worker._outgoing_q.put("G0 X0")
    worker._write_line = lambda *_args, **_kwargs: False
    worker.is_connected = lambda: True

    worker._process_manual_queue()

    assert worker._manual_pending_item is not None


def test_wait_for_manual_completion_timeout(monkeypatch) -> None:
    worker = _make_worker()
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 6)
    times = iter([0.0, 1.0])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))

    assert worker.wait_for_manual_completion(timeout_s=0.1) is False


def test_wait_for_manual_completion_sleeps(monkeypatch) -> None:
    worker = _make_worker()
    worker._manual_pending_item = ManualPendingItem("G0 X0", b"G0 X0\n", 6)

    def _sleep(_seconds: float) -> None:
        worker._manual_pending_item = None

    times = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(grbl_worker.time, "time", lambda: next(times))
    monkeypatch.setattr(grbl_worker.time, "sleep", _sleep)

    assert worker.wait_for_manual_completion(timeout_s=1.0) is True


def test_rx_loop_not_connected_branch(monkeypatch) -> None:
    worker = GrblWorker(queue.Queue())
    worker.ser = _DummySerial(is_open=False)
    stop_evt = _ToggleEvent()
    monkeypatch.setattr(grbl_worker.time, "sleep", lambda _seconds: None)

    worker._rx_loop(stop_evt)


def test_rx_loop_unexpected_read_error(monkeypatch) -> None:
    worker = _make_worker()
    worker.ser = _DummySerial()
    worker.ser.read = lambda _size: (_ for _ in ()).throw(RuntimeError("boom"))
    called = []
    worker._signal_disconnect = lambda *_args, **_kwargs: called.append(True)

    stop_evt = _ToggleEvent()
    worker._rx_loop(stop_evt)

    assert called == [True]


def test_rx_loop_empty_chunk(monkeypatch) -> None:
    worker = _make_worker()
    worker.ser.read = lambda _size: b""
    stop_evt = _ToggleEvent()

    worker._rx_loop(stop_evt)


def test_rx_loop_processes_lines(monkeypatch) -> None:
    worker = _make_worker()
    calls = []
    worker.ser.read = lambda _size: b"ok\n"
    worker._handle_rx_line = lambda line: calls.append(line)
    stop_evt = _ToggleEvent()

    worker._rx_loop(stop_evt)

    assert calls == ["ok"]


def test_rx_loop_outer_exception() -> None:
    worker = _make_worker()
    worker.is_connected = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    called = []
    worker._emit_exception = lambda *_args, **_kwargs: called.append("emit")
    worker._signal_disconnect = lambda *_args, **_kwargs: called.append("disconnect")
    stop_evt = _ToggleEvent()

    worker._rx_loop(stop_evt)

    assert "emit" in called
    assert "disconnect" in called


def test_handle_rx_line_reset_to_continue(monkeypatch) -> None:
    worker = _make_worker()
    called = []
    monkeypatch.setattr(worker, "_handle_alarm", lambda _msg: called.append(True))

    worker._handle_rx_line("[MSG:Reset to continue]")

    assert called == [True]


def test_handle_rx_line_error_clears_pause_after() -> None:
    worker = _make_worker()
    worker._streaming = True
    worker._pause_after_idx = 0
    worker._pause_after_reason = "M0"
    worker._stream_line_queue.append(StreamQueueItem(5, True, 0, "G0 X0"))

    worker._handle_rx_line("error:2")

    assert worker._pause_after_idx is None
    assert worker._pause_after_reason is None


def test_status_loop_skips_watchdog_during_alarm(monkeypatch) -> None:
    worker = _make_worker()
    worker._alarm_active = True
    worker._ready = True
    worker._last_rx_ts = 0.0
    worker.send_realtime = lambda _cmd: None
    disconnected = []
    worker._signal_disconnect = lambda reason=None: disconnected.append(reason)
    monkeypatch.setattr(grbl_worker_status.time, "time", lambda: 10.0)
    stop_evt = _ToggleEvent()

    worker._status_loop(stop_evt)

    assert disconnected == []


def test_status_loop_disconnects_after_alarm_timeout(monkeypatch) -> None:
    worker = _make_worker()
    worker._alarm_active = True
    worker._ready = True
    worker._last_rx_ts = 0.0
    worker.send_realtime = lambda _cmd: None
    disconnected = []
    worker._signal_disconnect = lambda reason=None: disconnected.append(reason)
    monkeypatch.setattr(grbl_worker_status.time, "time", lambda: 100.0)
    stop_evt = _ToggleEvent()

    worker._status_loop(stop_evt)

    assert disconnected


def test_status_loop_ui_q_put_failure() -> None:
    worker = _make_worker()
    worker.ui_q = _FlakyQueue()
    worker.send_realtime = lambda _cmd: (_ for _ in ()).throw(RuntimeError("fail"))
    worker._emit_exception = lambda *_args, **_kwargs: None
    worker._status_query_failure_limit = 3
    stop_evt = _ToggleEvent()

    worker._status_loop(stop_evt)

    assert worker._status_query_failures == 1


def test_status_loop_outer_exception() -> None:
    worker = _make_worker()
    worker.send_realtime = lambda _cmd: None

    class _BadLock:
        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    worker._status_interval_lock = _BadLock()
    called = []
    worker._emit_exception = lambda *_args, **_kwargs: called.append("emit")
    worker._signal_disconnect = lambda *_args, **_kwargs: called.append("disconnect")
    stop_evt = _ToggleEvent()

    worker._status_loop(stop_evt)

    assert "emit" in called
    assert "disconnect" in called
