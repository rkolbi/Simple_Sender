import queue
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.gcode import pipeline as gcode_pipeline
from simple_sender.ui.gcode import pipeline_loader
from simple_sender.utils.constants import MAX_LINE_LENGTH, TEMP_FILE_BUFFER_SIZE

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _Widget:
    def __init__(self) -> None:
        self.text = ""
        self.state = None

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "state" in kwargs:
            self.state = kwargs["state"]


class _MessageBox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def showwarning(self, title: str, message: str) -> None:
        self.calls.append(("warning", title, message))

    def showerror(self, title: str, message: str) -> None:
        self.calls.append(("error", title, message))


class _Grbl:
    def __init__(self, streaming: bool = False) -> None:
        self._streaming = streaming
        self.loaded = None
        self.outgoing_cleared = False
        self.prime_cache_payloads = []

    def is_streaming(self) -> bool:
        return self._streaming

    def load_gcode(self, payload, name: str | None = None) -> None:
        self.loaded = (payload, name)

    def prime_gcode_send_cache(self, lines) -> None:
        self.prime_cache_payloads.append(list(lines))

    def _clear_outgoing(self) -> None:
        self.outgoing_cleared = True


class _GView:
    def __init__(self) -> None:
        self.lines = None
        self.chunk_size = None
        self.virtualized_lines = None
        self.virtualized_window = None

    def set_lines_chunked(self, lines, *, chunk_size=None, on_done=None, on_progress=None):
        self.lines = list(lines)
        self.chunk_size = chunk_size
        if on_progress:
            on_progress(0, len(self.lines))
        if on_done:
            on_done()

    def set_lines_virtualized(self, lines, *, window_size, on_done=None, on_progress=None):
        self.virtualized_lines = list(lines)
        self.virtualized_window = int(window_size)
        if on_progress:
            on_progress(0, len(self.virtualized_lines))
            on_progress(len(self.virtualized_lines), len(self.virtualized_lines))
        if on_done:
            on_done()

    def set_lines(self, lines):
        self.lines = list(lines)


class _ToolpathPanel:
    def __init__(self) -> None:
        self.autolevel_overlay = None
        self.cleared = False

    def set_autolevel_overlay(self, overlay) -> None:
        self.autolevel_overlay = overlay

    def clear(self) -> None:
        self.cleared = True

    def get_arc_step_rad(self, _line_count: int) -> float:
        return 0.1


class _MacroExecutor:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    @contextmanager
    def macro_vars(self):
        yield self._payload


def test_apply_loaded_gcode_rejects_long_line(monkeypatch) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(gcode_pipeline, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self._gcode_loading = True
            self._finish_called = False
            self.status = _Widget()

        def _finish_gcode_loading(self) -> None:
            self._finish_called = True

    app = _App()
    long_line = "G1 X" + ("{" * (MAX_LINE_LENGTH + 10))

    gcode_pipeline.apply_loaded_gcode(app, "bad.gcode", [long_line], validated=False)

    assert msgbox.calls
    assert msgbox.calls[0][0] == "error"
    assert app._finish_called
    assert app._gcode_loading is False
    assert "failed" in app.status.text.lower()


def test_load_gcode_from_path_warns_when_streaming(monkeypatch, tmp_path) -> None:
    msgbox = _MessageBox()
    monkeypatch.setattr(gcode_pipeline, "messagebox", msgbox)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(streaming=True)

    app = _App()
    path = tmp_path / "job.gcode"
    path.write_text("G0 X0\n", encoding="utf-8")

    gcode_pipeline.load_gcode_from_path(app, str(path))

    assert msgbox.calls == [("warning", "Busy", "Stop the stream before loading a new G-code file.")]


def test_load_gcode_streaming_prompt_skip(monkeypatch, tmp_path) -> None:
    class _ImmediateThread:
        def __init__(self, target, daemon=False) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(gcode_pipeline, "disable_job_controls", lambda _app: None)
    monkeypatch.setattr(gcode_pipeline.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(gcode_pipeline, "GCODE_STREAMING_SIZE_THRESHOLD", 1)
    monkeypatch.setattr(gcode_pipeline, "STREAMING_VALIDATION_PROMPT_LINES", 1)
    monkeypatch.setattr(gcode_pipeline, "STREAMING_VALIDATION_PROMPT_TIMEOUT", 0.1)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.settings = {"last_gcode_dir": ""}
            self._gcode_load_token = 0
            self._gcode_loading = False
            self.validate_streaming_gcode = _Var(True)
            self.streaming_line_threshold = _Var(0)
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.gview = _GView()
            self.ui_q = queue.Queue()

        def _set_gcode_loading_indeterminate(self, _text: str) -> None:
            return None

    app = _App()
    path = tmp_path / "big.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")

    gcode_pipeline.load_gcode_from_path(app, str(path))

    events = list(app.ui_q.queue)
    loaded_event = next((evt for evt in events if evt[0] == "gcode_loaded_stream"), None)

    assert loaded_event is not None
    assert any(evt[0] == "streaming_validation_prompt" for evt in events)
    assert any(evt[0] == "log" and "Large file detected" in evt[1] for evt in events)
    assert any(evt[0] == "log" and "validation skipped" in evt[1] for evt in events)
    assert loaded_event[6] == 2
    assert loaded_event[7] is None
    assert loaded_event[8] is True


def test_streaming_invalid_command_cleans_temp_file(monkeypatch, tmp_path) -> None:
    captured_paths: list[str] = []
    orig_named_temporary_file = tempfile.NamedTemporaryFile

    def _recording_named_temporary_file(*args, **kwargs):
        kwargs.setdefault("dir", str(tmp_path))
        temp_file = orig_named_temporary_file(*args, **kwargs)
        captured_paths.append(temp_file.name)
        return temp_file

    monkeypatch.setattr(
        gcode_pipeline.tempfile,
        "NamedTemporaryFile",
        _recording_named_temporary_file,
    )

    class _App:
        def __init__(self) -> None:
            self.ui_q = queue.Queue()
            self._gcode_load_token = 1

    app = _App()
    path = Path(tmp_path) / "invalid.gcode"
    path.write_text("$X\n", encoding="utf-8")

    pipeline_loader._stream_from_disk(
        app,
        str(path),
        1,
        gcode_pipeline,
        file_size=None,
        validate_streaming=False,
        preview_only=True,
        streaming_line_threshold=None,
    )

    assert captured_paths
    assert not Path(captured_paths[0]).exists()
    events = list(app.ui_q.queue)
    assert any(evt[0] == "gcode_load_invalid_command" for evt in events)


def test_stream_from_disk_uses_preferred_temp_dir_and_buffer_size(monkeypatch, tmp_path) -> None:
    captured_kwargs: list[dict] = []
    orig_named_temporary_file = tempfile.NamedTemporaryFile
    monkeypatch.setattr(gcode_pipeline, "get_preferred_temp_dir", lambda: str(tmp_path))

    def _recording_named_temporary_file(*args, **kwargs):
        captured_kwargs.append(dict(kwargs))
        return orig_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        gcode_pipeline.tempfile,
        "NamedTemporaryFile",
        _recording_named_temporary_file,
    )

    class _App:
        def __init__(self) -> None:
            self.ui_q = queue.Queue()
            self._gcode_load_token = 1

    app = _App()
    path = Path(tmp_path) / "valid.gcode"
    path.write_text("G0 X0\n", encoding="utf-8")

    pipeline_loader._stream_from_disk(
        app,
        str(path),
        1,
        gcode_pipeline,
        file_size=None,
        validate_streaming=False,
        preview_only=True,
        streaming_line_threshold=None,
    )

    assert captured_kwargs
    kwargs = captured_kwargs[0]
    assert kwargs.get("dir") == str(tmp_path)
    assert int(kwargs.get("buffering", 0)) == TEMP_FILE_BUFFER_SIZE


def test_stream_from_disk_cancels_stale_token(tmp_path) -> None:
    class _App:
        def __init__(self) -> None:
            self.ui_q = queue.Queue()
            self._gcode_load_token = 2

    app = _App()
    path = tmp_path / "stale.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")

    pipeline_loader._stream_from_disk(
        app,
        str(path),
        1,
        gcode_pipeline,
        file_size=None,
        validate_streaming=False,
        preview_only=True,
        streaming_line_threshold=None,
    )

    assert list(app.ui_q.queue) == []


def test_load_gcode_non_streaming_large_fast_load_skips_validation(monkeypatch, tmp_path) -> None:
    class _ImmediateThread:
        def __init__(self, target, daemon=False) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    validate_calls = {"count": 0}

    def _count_validation(_lines):
        validate_calls["count"] += 1
        return object()

    monkeypatch.setattr(gcode_pipeline.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(gcode_pipeline, "disable_job_controls", lambda _app: None)
    monkeypatch.setattr(gcode_pipeline, "GCODE_STREAMING_SIZE_THRESHOLD", 10_000_000)
    monkeypatch.setattr(gcode_pipeline, "STREAMING_VALIDATION_PROMPT_LINES", 1)
    monkeypatch.setattr(gcode_pipeline, "validate_gcode_lines", _count_validation)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.settings = {"last_gcode_dir": ""}
            self._gcode_load_token = 0
            self._gcode_loading = False
            self.validate_streaming_gcode = _Var(False)
            self.streaming_line_threshold = _Var(0)
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.gview = _GView()
            self.ui_q = queue.Queue()

        def _set_gcode_loading_indeterminate(self, _text: str) -> None:
            return None

    app = _App()
    path = tmp_path / "large_non_stream.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")

    gcode_pipeline.load_gcode_from_path(app, str(path))

    events = list(app.ui_q.queue)
    loaded_event = next((evt for evt in events if evt[0] == "gcode_loaded_stream"), None)

    assert loaded_event is not None
    assert loaded_event[4] == ["G0 X0", "G1 X1"]
    assert loaded_event[6] == 2
    assert loaded_event[7] is None
    assert loaded_event[8] is False
    assert not any(evt[0] == "streaming_validation_prompt" for evt in events)
    assert any(evt[0] == "log" and "fast-load mode" in evt[1] for evt in events)
    assert validate_calls["count"] == 0


def test_apply_loaded_gcode_streaming_source_sets_preview(monkeypatch) -> None:
    calls = {"preview": None, "configure": None}
    monkeypatch.setattr(
        gcode_pipeline,
        "set_preview_streaming_state",
        lambda _app, state: calls.update(preview=state),
    )
    monkeypatch.setattr(
        gcode_pipeline,
        "configure_toolpath_preview",
        lambda _app, path, lines, source, preview_only: calls.update(
            configure=(path, lines, source, preview_only)
        ),
    )

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_token = 5
            self._stats_cache = {}
            self._stats_after_id = "stats-after"
            self._stats_pending_request = ("pending",)
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self.canceled_after_ids = []

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, mode: str) -> None:
            self.job_mode = mode

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

        def after_cancel(self, after_id) -> None:
            self.canceled_after_ids.append(after_id)

    app = _App()
    source = object()

    gcode_pipeline.apply_loaded_gcode(
        app,
        "stream.gcode",
        ["G0 X0"],
        streaming_source=source,
        total_lines=1,
        preview_only=True,
    )

    assert calls["preview"] is True
    assert calls["configure"] == ("stream.gcode", ["G0 X0"], source, True)
    assert app.grbl.loaded == (source, "stream.gcode")
    assert app.gcode_stats_var.value == "Preview only (streaming mode)"
    assert app._stats_token == 6
    assert app._stats_pending_request is None
    assert app._stats_after_id is None
    assert app.canceled_after_ids == ["stats-after"]
    assert app.grbl.prime_cache_payloads == []


def test_apply_loaded_gcode_file_backed_non_preview_primes_worker_cache(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "configure_toolpath_preview", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "schedule_gcode_parse", lambda *_args: None)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_token = 0
            self._stats_cache = {}
            self._stats_after_id = None
            self._stats_pending_request = None
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self._auto_level_restore = None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, _mode: str) -> None:
            return None

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

    app = _App()

    gcode_pipeline.apply_loaded_gcode(
        app,
        "job.gcode",
        ["G0 X0", "G1 X1"],
        lines_hash="hash",
        validated=True,
        streaming_source=object(),
        total_lines=2,
        preview_only=False,
    )

    assert app.grbl.prime_cache_payloads == [["G0 X0", "G1 X1"]]


def test_apply_loaded_gcode_file_backed_non_preview_skips_worker_cache_on_pi_profile(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "configure_toolpath_preview", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "schedule_gcode_parse", lambda *_args: None)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.settings = {"pi_profile_enabled": True}
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_token = 0
            self._stats_cache = {}
            self._stats_after_id = None
            self._stats_pending_request = None
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self._auto_level_restore = None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, _mode: str) -> None:
            return None

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

    app = _App()

    gcode_pipeline.apply_loaded_gcode(
        app,
        "job.gcode",
        ["G0 X0", "G1 X1"],
        lines_hash="hash",
        validated=True,
        streaming_source=object(),
        total_lines=2,
        preview_only=False,
    )

    assert app.grbl.prime_cache_payloads == []


def test_apply_loaded_gcode_uses_virtualized_viewer_for_large_jobs(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "configure_toolpath_preview", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "schedule_gcode_parse", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "GCODE_VIEWER_VIRTUALIZE_THRESHOLD_DEFAULT", 1000)
    monkeypatch.setattr(gcode_pipeline, "GCODE_VIEWER_VIRTUAL_WINDOW_SIZE_DEFAULT", 300)

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_token = 0
            self._stats_cache = {}
            self._stats_after_id = None
            self._stats_pending_request = None
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self._auto_level_restore = None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, _mode: str) -> None:
            return None

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

    app = _App()
    lines = [f"G1 X{idx}" for idx in range(1200)]

    gcode_pipeline.apply_loaded_gcode(
        app,
        "big.gcode",
        lines,
        validated=True,
    )

    assert app.gview.virtualized_lines == lines
    assert app.gview.virtualized_window == 300


def test_apply_loaded_gcode_validated_true_skips_fallback_validation(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "configure_toolpath_preview", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "schedule_gcode_parse", lambda *_args: None)
    monkeypatch.setattr(
        gcode_pipeline,
        "validate_gcode_lines",
        lambda _lines: (_ for _ in ()).throw(AssertionError("Validation should be skipped")),
    )

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_cache = {}
            self._stats_after_id = None
            self._stats_pending_request = None
            self._stats_token = 0
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self._auto_level_restore = None
            self._auto_level_leveled_path = None
            self._auto_level_leveled_temp = False
            self._auto_level_grid = None
            self._auto_level_height_map = None
            self._auto_level_bounds = None
            self._auto_level_original_lines = None
            self._auto_level_original_path = None
            self._auto_level_leveled_lines = None
            self._auto_level_leveled_name = None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, mode: str) -> None:
            self.job_mode = mode

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

    app = _App()

    gcode_pipeline.apply_loaded_gcode(
        app,
        "job.gcode",
        ["G0 X0"],
        lines_hash="hash",
        validated=True,
    )

    assert app._gcode_validation_report is None


def test_reset_autolevel_state_removes_temp_file(tmp_path) -> None:
    leveled_path = tmp_path / "leveled.gcode"
    leveled_path.write_text("G0 X0\n", encoding="utf-8")

    class _App:
        def __init__(self) -> None:
            self._auto_level_grid = object()
            self._auto_level_height_map = object()
            self._auto_level_bounds = object()
            self._auto_level_original_lines = ["G0 X0"]
            self._auto_level_original_path = "orig.gcode"
            self._auto_level_leveled_lines = ["G0 X0"]
            self._auto_level_leveled_path = str(leveled_path)
            self._auto_level_leveled_temp = True
            self._auto_level_leveled_name = "leveled.gcode"
            self._auto_level_restore = None
            self.toolpath_panel = _ToolpathPanel()

    app = _App()

    gcode_pipeline._reset_autolevel_state(app)

    assert not leveled_path.exists()
    assert app._auto_level_leveled_path is None
    assert app._auto_level_leveled_temp is False


def test_apply_loaded_gcode_restores_autolevel_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "configure_toolpath_preview", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "schedule_gcode_parse", lambda *_args: None)

    leveled_path = tmp_path / "leveled.gcode"
    leveled_path.write_text("G0 X0\n", encoding="utf-8")
    restore = {
        "original_lines": ["G0 X0"],
        "original_path": "orig.gcode",
        "leveled_lines": None,
        "leveled_path": str(leveled_path),
        "leveled_temp": True,
        "leveled_name": "leveled.gcode",
    }

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl()
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._gcode_loading = False
            self._gcode_validation_report = None
            self._gcode_parse_token = 0
            self._stats_cache = {}
            self._live_estimate_min = None
            self._last_stats = None
            self._last_rate_source = None
            self._gcode_source = None
            self._gcode_total_lines = None
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._last_gcode_lines = []
            self._last_gcode_path = None
            self._gcode_hash = None
            self._last_parse_result = None
            self._last_parse_hash = None
            self._gcode_load_token = 0
            self.gcode_stats_var = _Var("")
            self.status = _Widget()
            self.btn_run = _Widget()
            self.btn_resume_from = _Widget()
            self.progress_pct = _Var(0)
            self.gview = _GView()
            self.toolpath_panel = _ToolpathPanel()
            self._auto_level_restore = restore
            self._auto_level_leveled_path = str(leveled_path)
            self._auto_level_leveled_temp = True
            self._auto_level_grid = None
            self._auto_level_height_map = None
            self._auto_level_bounds = None
            self._auto_level_original_lines = None
            self._auto_level_original_path = None
            self._auto_level_leveled_lines = None
            self._auto_level_leveled_name = None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_job_button_mode(self, mode: str) -> None:
            self.job_mode = mode

        def _set_gcode_loading_progress(self, _done, _total, _name) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

    app = _App()

    gcode_pipeline.apply_loaded_gcode(
        app,
        str(leveled_path),
        ["G0 X0"],
        validated=True,
    )

    assert leveled_path.exists()
    assert app._auto_level_restore is None
    assert app._auto_level_original_lines == ["G0 X0"]
    assert app._auto_level_original_path == "orig.gcode"
    assert app._auto_level_leveled_path == str(leveled_path)
    assert app._auto_level_leveled_temp is True
    assert app._auto_level_leveled_name == "leveled.gcode"


def test_clear_gcode_resets_bounds_and_estimate_state(monkeypatch) -> None:
    called = {"preview": None, "disabled": False}
    monkeypatch.setattr(
        gcode_pipeline,
        "set_preview_streaming_state",
        lambda _app, state: called.update(preview=state),
    )
    monkeypatch.setattr(
        gcode_pipeline,
        "disable_job_controls",
        lambda _app: called.update(disabled=True),
    )

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(streaming=False)
            self._gcode_load_token = 3
            self._gcode_loading = True
            self._gcode_source = None
            self._gcode_total_lines = 3
            self._resume_after_disconnect = True
            self._resume_from_index = 2
            self._resume_job_name = "job.gcode"
            self._stream_state = "done"
            self._stream_start_ts = 12.5
            self._stream_pause_total = 8.0
            self._stream_paused_at = 21.0
            self._stream_done_pending_idle = True
            self._last_gcode_lines = ["G0 X0"]
            self._last_gcode_path = "job.gcode"
            self._gcode_hash = "hash"
            self._gcode_validation_report = object()
            self._last_parse_result = object()
            self._last_parse_hash = "hash"
            self._live_estimate_min = 1.5
            self._last_stats = {"bounds": (0.0, 1.0, 0.0, 1.0, 0.0, 0.0)}
            self._last_rate_source = "fallback"
            self._last_error_index = 99
            self._gcode_parse_token = 7
            self._stats_token = 11
            self._stats_cache = {"old": ("stats", "source")}
            self._stats_after_id = "stats-after"
            self._stats_pending_request = ("pending",)
            self.gview = _GView()
            self.gcode_stats_var = _Var("")
            self.progress_pct = _Var(99)
            self.buffer_fill = _Var("Buffer: 37%")
            self.buffer_fill_pct = _Var(37)
            self.throughput_var = _Var("TX: 123 B/s")
            self.status = _Widget()
            self.toolpath_panel = _ToolpathPanel()
            self._job_started_at = object()
            self._job_completion_notified = True
            self.connected = True
            self._grbl_ready = True
            self._status_seen = True
            self._alarm_locked = False
            self._auto_level_grid = object()
            self._auto_level_height_map = object()
            self._auto_level_bounds = object()
            self._auto_level_original_lines = ["G0 X0"]
            self._auto_level_original_path = "orig.gcode"
            self._auto_level_leveled_lines = ["G0 X0"]
            self._auto_level_leveled_path = None
            self._auto_level_leveled_temp = False
            self._auto_level_leveled_name = "leveled.gcode"
            self._auto_level_restore = None
            self.canceled_after_ids = []
            self.finish_calls = 0
            self.clear_pending_calls = 0
            self.manual_state = None
            self.streaming_lock = None
            self.focus_refreshes = 0

        def _set_job_button_mode(self, mode: str) -> None:
            self.job_mode = mode

        def after_cancel(self, after_id) -> None:
            self.canceled_after_ids.append(after_id)

        def _finish_gcode_loading(self) -> None:
            self.finish_calls += 1

        def _clear_pending_ui_updates(self) -> None:
            self.clear_pending_calls += 1

        def _set_manual_controls_enabled(self, enabled: bool) -> None:
            self.manual_state = bool(enabled)

        def _set_streaming_lock(self, locked: bool) -> None:
            self.streaming_lock = bool(locked)

        def _refresh_toolbar_action_focus(self) -> None:
            self.focus_refreshes += 1

    app = _App()

    gcode_pipeline.clear_gcode(app)

    assert called["preview"] is False
    assert called["disabled"] is True
    assert app.job_mode == "read_job"
    assert app._gcode_load_token == 4
    assert app._gcode_loading is False
    assert app.finish_calls == 1
    assert app.clear_pending_calls == 1
    assert app.grbl.outgoing_cleared is True
    assert app._gcode_parse_token == 8
    assert app._stats_token == 12
    assert app._stats_pending_request is None
    assert app._stats_after_id is None
    assert app.canceled_after_ids == ["stats-after"]
    assert app._stream_state == "loaded"
    assert app._stream_done_pending_idle is False
    assert app._stream_start_ts is None
    assert app._stream_pause_total == 0.0
    assert app._stream_paused_at is None
    assert app._live_estimate_min is None
    assert app._last_stats is None
    assert app._last_rate_source is None
    assert app.gcode_stats_var.value == "No file loaded"
    assert app.buffer_fill.value == "Buffer: 0%"
    assert app.buffer_fill_pct.value == 0
    assert app.throughput_var.value == "TX: 0 B/s"
    assert app.manual_state is True
    assert app.streaming_lock is False
    assert app.focus_refreshes == 1
    assert app.grbl.loaded == ([], None)


def test_clear_gcode_preserves_macro_state_values(monkeypatch) -> None:
    monkeypatch.setattr(gcode_pipeline, "set_preview_streaming_state", lambda *_args: None)
    monkeypatch.setattr(gcode_pipeline, "disable_job_controls", lambda *_args: None)

    macro_state = types.SimpleNamespace(TOOL_REFERENCE=12.34, SETUP_MODE="xyz")
    macro_vars = {"macro": types.SimpleNamespace(state=macro_state)}

    class _App:
        def __init__(self) -> None:
            self.grbl = _Grbl(streaming=False)
            self.macro_executor = _MacroExecutor(macro_vars)
            self._gcode_load_token = 1
            self._gcode_loading = False
            self._gcode_source = None
            self._gcode_total_lines = 1
            self._resume_after_disconnect = False
            self._resume_from_index = None
            self._resume_job_name = None
            self._stream_state = "loaded"
            self._stream_start_ts = None
            self._stream_pause_total = 0.0
            self._stream_paused_at = None
            self._stream_done_pending_idle = False
            self._last_gcode_lines = ["G0 X0"]
            self._last_gcode_path = "job.gcode"
            self._gcode_hash = "hash"
            self._gcode_validation_report = object()
            self._last_parse_result = object()
            self._last_parse_hash = "hash"
            self._live_estimate_min = None
            self._last_stats = {}
            self._last_rate_source = None
            self._last_error_index = -1
            self._manual_queue_drop_total = 0
            self._gcode_parse_token = 0
            self._stats_token = 0
            self._stats_cache = {}
            self._stats_after_id = None
            self._stats_pending_request = None
            self.gview = _GView()
            self.gcode_stats_var = _Var("")
            self.progress_pct = _Var(0)
            self.buffer_fill = _Var("")
            self.buffer_fill_pct = _Var(0)
            self.throughput_var = _Var("")
            self.status = _Widget()
            self.toolpath_panel = _ToolpathPanel()
            self._job_started_at = None
            self._job_completion_notified = False
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._auto_level_grid = None
            self._auto_level_height_map = None
            self._auto_level_bounds = None
            self._auto_level_original_lines = None
            self._auto_level_original_path = None
            self._auto_level_leveled_lines = None
            self._auto_level_leveled_path = None
            self._auto_level_leveled_temp = False
            self._auto_level_leveled_name = None
            self._auto_level_restore = None

        def _set_job_button_mode(self, _mode: str) -> None:
            return None

        def _finish_gcode_loading(self) -> None:
            return None

        def _clear_pending_ui_updates(self) -> None:
            return None

        def _set_manual_controls_enabled(self, _enabled: bool) -> None:
            return None

        def _set_streaming_lock(self, _locked: bool) -> None:
            return None

        def _refresh_toolbar_action_focus(self) -> None:
            return None

    app = _App()
    gcode_pipeline.clear_gcode(app)

    with app.macro_executor.macro_vars() as restored:
        restored_state = restored["macro"].state
        assert restored_state.TOOL_REFERENCE == 12.34
        assert restored_state.SETUP_MODE == "xyz"
