import math
import queue
from pathlib import Path

import pytest

from simple_sender.gcode_parser import clean_gcode_line, parse_gcode_lines
from simple_sender.grbl_worker import GrblWorker
from simple_sender.utils.config import Settings

pytestmark = pytest.mark.unit


def test_clean_gcode_line_strips_comments_and_metadata() -> None:
    line = "G0 X0 Y0 ; move to origin"
    assert clean_gcode_line(line) == "G0 X0 Y0"
    assert clean_gcode_line("  (comment) G1 X1 Y1 ") == "G1 X1 Y1"
    assert clean_gcode_line("%") == ""


def test_parse_gcode_lines_basic_travel() -> None:
    lines = ["G21", "G0 X0 Y0", "G1 X10 Y5 F100"]
    result = parse_gcode_lines(lines, arc_step_rad=math.pi / 18)
    assert result is not None
    assert len(result.moves) == 2
    assert len(result.segments) == 2
    bounds = result.bounds
    assert bounds is not None
    assert bounds[0] == pytest.approx(0.0)
    assert bounds[1] == pytest.approx(10.0)
    assert bounds[2] == pytest.approx(0.0)
    assert bounds[3] == pytest.approx(5.0)
    assert bounds[4] == pytest.approx(0.0)
    assert bounds[5] == pytest.approx(0.0)


def test_settings_save_and_load(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    store = Settings(str(settings_path))
    assert store.data["baud_rate"] == 115200
    store.data["last_port"] = "COM1"
    store.save()

    reloaded = Settings(str(settings_path))
    reloaded.load()
    assert reloaded.data["last_port"] == "COM1"


def test_grbl_worker_state_reset() -> None:
    ui_q = queue.Queue()
    worker = GrblWorker(ui_q)
    worker.load_gcode(["G0 X0 Y0"])
    assert worker._gcode == ["G0 X0 Y0"]
    assert not worker._streaming
    worker._stream_buf_used = 100
    worker._stream_line_queue.append((5, True, 0, "G0 X0 Y0"))
    worker._reset_stream_buffer()
    assert worker._stream_buf_used == 0
    assert not worker._stream_line_queue
    assert worker.wait_for_manual_completion(timeout_s=0.01)
    assert worker._encode_line_payload("G0").endswith(b"\n")


def test_gcode_load_invalid_reports_original_line(tmp_path: Path) -> None:
    pytest.importorskip("tkinter")
    from simple_sender.ui.gcode.pipeline import load_gcode_from_path

    class _Var:
        def __init__(self, value=None):
            self.value = value

        def set(self, value):
            self.value = value

    class _Widget:
        def __init__(self):
            self.state = None
            self.text = ""

        def config(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]

    class _Grbl:
        def is_streaming(self):
            return False

    class _Gview:
        def set_lines_chunked(self, *_args, **_kwargs):
            return None

    class _App:
        def __init__(self):
            self.grbl = _Grbl()
            self.settings = {"last_gcode_dir": ""}
            self._gcode_load_token = 0
            self._gcode_loading = False
            self.btn_run = _Widget()
            self.btn_pause = _Widget()
            self.btn_resume = _Widget()
            self.btn_resume_from = _Widget()
            self.gcode_stats_var = _Var()
            self.status = _Widget()
            self.gview = _Gview()
            self.ui_q = queue.Queue()
            self.streaming_line_threshold = _Var(250000)

        def _set_gcode_loading_indeterminate(self, _text: str):
            return None

    app = _App()
    path = tmp_path / "bad.gcode"
    long_num = "1" * 90
    content = "\n".join(
        [
            "(comment line)",
            "G1 X0 Y0",
            f"G93 G1 X{long_num}",
        ]
    )
    path.write_text(content, encoding="utf-8")

    load_gcode_from_path(app, str(path))

    evt = None
    for _ in range(100):
        try:
            evt = app.ui_q.get(timeout=0.1)
            if evt and evt[0] == "gcode_load_invalid":
                break
        except queue.Empty:
            continue

    assert evt is not None
    assert evt[0] == "gcode_load_invalid"
    first_idx = evt[4]
    total_lines = evt[6]
    cleaned_lines = evt[7]
    assert first_idx == 2
    assert total_lines == 3
    assert cleaned_lines == 2


def test_gcode_load_large_line_count_uses_streaming(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("tkinter")
    from simple_sender.ui.gcode import pipeline as gcode_pipeline

    monkeypatch.setattr(gcode_pipeline, "GCODE_STREAMING_LINE_THRESHOLD", 2)

    class _Var:
        def __init__(self, value=None):
            self.value = value

        def set(self, value):
            self.value = value

    class _Widget:
        def __init__(self):
            self.state = None
            self.text = ""

        def config(self, **kwargs):
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]

    class _Grbl:
        def is_streaming(self):
            return False

    class _Gview:
        def set_lines_chunked(self, *_args, **_kwargs):
            return None

    class _App:
        def __init__(self):
            self.grbl = _Grbl()
            self.settings = {"last_gcode_dir": ""}
            self._gcode_load_token = 0
            self._gcode_loading = False
            self.btn_run = _Widget()
            self.btn_pause = _Widget()
            self.btn_resume = _Widget()
            self.btn_resume_from = _Widget()
            self.gcode_stats_var = _Var()
            self.status = _Widget()
            self.gview = _Gview()
            self.ui_q = queue.Queue()
            self.streaming_line_threshold = _Var(2)

        def _set_gcode_loading_indeterminate(self, _text: str):
            return None

    app = _App()
    path = tmp_path / "line_threshold.gcode"
    path.write_text("G0 X0\nG0 X1\nG0 X2\n", encoding="utf-8")

    gcode_pipeline.load_gcode_from_path(app, str(path))

    evt = None
    for _ in range(100):
        try:
            evt = app.ui_q.get(timeout=0.1)
            if evt and evt[0] == "gcode_loaded_stream":
                break
        except queue.Empty:
            continue

    assert evt is not None
    assert evt[0] == "gcode_loaded_stream"
    assert evt[6] == 3
    assert evt[8] is True


def test_manual_command_too_long_is_dropped() -> None:
    class _Serial:
        def __init__(self):
            self.is_open = True
            self.writes = 0

        def write(self, data):
            self.writes += 1
            return len(data)

    ui_q = queue.Queue()
    worker = GrblWorker(ui_q)
    worker.ser = _Serial()
    worker._rx_window = 8
    worker._outgoing_q.put("G0 X0")
    worker._process_manual_queue()

    assert worker.ser.writes == 0
    logs = []
    while True:
        try:
            logs.append(ui_q.get_nowait())
        except queue.Empty:
            break
    assert any(evt[0] == "log" and "[manual] Line too long" in evt[1] for evt in logs)


def test_macro_scripting_blocked_when_disabled() -> None:
    pytest.importorskip("tkinter")
    from simple_sender.macro_executor import MacroExecutor

    class _Bool:
        def __init__(self, value: bool):
            self._value = value

        def get(self):
            return self._value

    class _App:
        def __init__(self):
            self.ui_q = queue.Queue()
            self.grbl = object()
            self.macros_allow_python = _Bool(False)

    executor = MacroExecutor(_App())
    compiled = executor._bcnc_compile_line("_x = 1")
    assert isinstance(compiled, tuple)
    assert compiled[0] == "COMPILE_ERROR"
