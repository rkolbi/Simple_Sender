import pytest

pytest.importorskip("tkinter")

from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.ui.dialogs import diagnostics


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value


class _Queue:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def put(self, item) -> None:
        self.events.append(item)


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, *, text: str) -> None:
        self.text = text


class _StreamingController:
    def get_console_lines(self):
        return []


class _Grbl:
    def get_runtime_metrics(self):
        return {
            "tx_lines_per_sec": 12.5,
            "ok_latency_ms_last": 8.0,
            "ok_latency_ms_avg": 9.5,
            "ok_latency_samples": 25,
            "tx_loop_cycles": 120,
            "tx_loop_idle_cycles": 90,
            "tx_loop_active_cycles": 30,
            "tx_loop_idle_wait_total_s": 18.0,
            "tx_loop_idle_ratio": 0.75,
            "queue_depth_last": {
                "stream": 1,
                "manual": 2,
                "ui": 3,
                "timestamp": "2026-03-01T10:00:00",
            },
            "queue_depth_samples": [
                {
                    "timestamp": "2026-03-01T10:00:00",
                    "stream": 1,
                    "manual": 2,
                    "ui": 3,
                }
            ],
        }


class _ParseResult:
    def __init__(self) -> None:
        self.bounds = (0.0, 10.0, 0.0, 10.0, 0.0, 0.0)


class _App:
    def __init__(self) -> None:
        self.version_var = _Var("v1.8.0")
        self.connected = True
        self._connected_port = "COM9"
        self._stream_state = "idle"
        self._last_gcode_path = "job.nc"
        self._gcode_hash = "abc123"
        self._last_parse_result = _ParseResult()
        self._gcode_total_lines = 10
        self._gcode_streaming_mode = True
        self.validate_streaming_gcode = _Var(True)
        self._gcode_validation_report = None
        self._last_status_raw = ""
        self._status_history: list[tuple[float, str]] = []
        self._preflight_run_decisions: list[dict] = []
        self._grbl_ready = True
        self._status_seen = True
        self._alarm_locked = False
        self._homing_in_progress = False
        self.settings = {"example": True}
        self.streaming_controller = _StreamingController()
        self.grbl = _Grbl()
        self.settings_controller = None
        self.ui_q = _Queue()
        self.status = _Status()


def test_preflight_warns_when_validation_missing(monkeypatch) -> None:
    app = _App()
    calls = {}

    def _showwarning(_title, msg):
        calls["msg"] = msg

    monkeypatch.setattr(diagnostics.messagebox, "showwarning", _showwarning)
    monkeypatch.setattr(diagnostics.messagebox, "showinfo", lambda *_args, **_kwargs: None)

    diagnostics.run_preflight_check(app)

    assert "Validation report is unavailable" in calls["msg"]


def test_run_preflight_gate_blocks_on_failures(monkeypatch) -> None:
    app = _App()
    calls = {}

    def _askyesno(_title, msg):
        calls["msg"] = msg
        return False

    monkeypatch.setattr(
        diagnostics.messagebox,
        "askyesno",
        _askyesno,
    )

    ok = diagnostics.run_preflight_gate(app)

    assert ok is False
    assert "Run blocked by preflight safety gate" in calls["msg"]
    assert app._preflight_run_decisions
    assert app._preflight_run_decisions[-1]["decision"] == "blocked"
    assert app.status.text == "Run blocked: preflight gate failed"


def test_run_preflight_gate_allows_override_on_failures(monkeypatch) -> None:
    app = _App()
    calls = {}

    def _askyesno(_title, msg):
        calls["msg"] = msg
        return True

    monkeypatch.setattr(
        diagnostics.messagebox,
        "askyesno",
        _askyesno,
    )

    ok = diagnostics.run_preflight_gate(app)

    assert ok is True
    assert "Continue anyway?" in calls["msg"]
    assert app._preflight_run_decisions
    assert app._preflight_run_decisions[-1]["decision"] == "override"
    assert app._preflight_run_decisions[-1]["job_hash"] == "abc123"


def test_run_preflight_gate_allows_when_validation_clean(monkeypatch) -> None:
    app = _App()
    app._gcode_streaming_mode = False
    app._gcode_validation_report = type(
        "Report",
        (),
        {"line_issue_count": 0, "grbl_warnings": {}},
    )()
    warned = {"called": False}
    monkeypatch.setattr(
        diagnostics.messagebox,
        "showwarning",
        lambda *_args, **_kwargs: warned.__setitem__("called", True),
    )

    ok = diagnostics.run_preflight_gate(app)

    assert ok is True
    assert warned["called"] is False


def test_run_preflight_gate_allows_incremental_hazard_only(monkeypatch) -> None:
    app = _App()
    app._gcode_streaming_mode = False
    app._gcode_validation_report = validate_gcode_lines(
        ["G21", "G90", "G91", "G0 Z10.000", "G90", "G91", "G0 Z10.000", "G90"]
    )
    monkeypatch.setattr(
        diagnostics.messagebox,
        "askyesno",
        lambda *_args, **_kwargs: pytest.fail("preflight gate should not block incremental-only hazard"),
    )

    ok = diagnostics.run_preflight_gate(app)

    assert ok is True


def test_export_session_diagnostics_includes_preflight_decisions(tmp_path, monkeypatch) -> None:
    app = _App()
    app._gcode_streaming_mode = False
    app._gcode_validation_report = type(
        "Report",
        (),
        {"line_issue_count": 0, "grbl_warnings": {}},
    )()
    app._preflight_run_decisions = [
        {
            "timestamp": "2026-03-01T10:00:00",
            "decision": "override",
            "job_path": "job.nc",
            "job_name": "job.nc",
            "job_hash": "abc123",
            "stream_state": "idle",
            "streaming_mode": False,
            "failure_count": 2,
            "warning_count": 1,
            "failures": ["Validation report is unavailable."],
            "warnings": ["Machine travel settings ($130/$131/$132) are unavailable."],
        }
    ]
    output = tmp_path / "diagnostics.txt"
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(diagnostics, "run_file_dialog", lambda *_args, **_kwargs: str(output))
    monkeypatch.setattr(
        diagnostics.messagebox,
        "showinfo",
        lambda title, message: shown.append((title, message)),
    )
    monkeypatch.setattr(
        diagnostics.messagebox,
        "showerror",
        lambda *_args, **_kwargs: pytest.fail("unexpected export error"),
    )

    diagnostics.export_session_diagnostics(app)

    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "Preflight run decisions:" in text
    assert "decision=override" in text
    assert "job=job.nc" in text
    assert "hash=abc123" in text
    assert "FAIL: Validation report is unavailable." in text
    assert "Runtime telemetry:" in text
    assert "TX lines/sec: 12.50" in text
    assert "TX loop: cycles=120, active=30, idle=90, idle_ratio=0.750, idle_wait_s=18.00" in text
    assert "Queue depth last: stream=1, manual=2, ui=3 @ 2026-03-01T10:00:00" in text
    assert shown


def test_open_runtime_telemetry_singleton_and_close_cleans_up(tk_root, monkeypatch) -> None:
    app = tk_root
    app.grbl = _Grbl()
    monkeypatch.setattr(diagnostics, "center_window", lambda *_args, **_kwargs: None)

    diagnostics.open_runtime_telemetry(app)
    first = app._runtime_telemetry_window
    assert first is not None
    assert first.winfo_exists()
    assert app._runtime_telemetry_after_id is not None

    diagnostics.open_runtime_telemetry(app)
    second = app._runtime_telemetry_window
    assert second is first

    close_button = None
    pending = list(first.winfo_children())
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if widget.winfo_class() == "TButton" and widget.cget("text") == "Close":
            close_button = widget
            break
    assert close_button is not None
    close_button.invoke()

    assert app._runtime_telemetry_window is None
    assert app._runtime_telemetry_after_id is None
