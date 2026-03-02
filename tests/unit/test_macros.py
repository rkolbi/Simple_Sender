import types
import queue
import time

import pytest

pytest.importorskip("tkinter")

pytestmark = pytest.mark.unit

from simple_sender.macro_executor import MacroExecutor


def test_macro_python_exec_enabled_allows_assignments(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    compiled = executor._bcnc_compile_line("_x = 1")
    assert isinstance(compiled, types.CodeType)

    executor._bcnc_evaluate_line(compiled)

    with executor.macro_vars() as vars_snapshot:
        assert vars_snapshot["_x"] == 1


def test_macro_python_disabled_blocks_expressions(macro_app_factory) -> None:
    app = macro_app_factory(False)
    executor = MacroExecutor(app)

    compiled = executor._bcnc_compile_line("G1 X[1+2]")
    assert isinstance(compiled, tuple)
    assert compiled[0] == "COMPILE_ERROR"


def test_macro_python_disabled_allows_grbl_commands(macro_app_factory) -> None:
    app = macro_app_factory(False)
    executor = MacroExecutor(app)

    compiled = executor._bcnc_compile_line("$J=G91 X1 Y0.5 F200")
    assert compiled == "$J=G91 X1 Y0.5 F200"


def test_parse_macro_prompt_handles_custom_buttons(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    title, message, choices, cancel_label, button_keys = executor._parse_macro_prompt(
        "PROMPT [title(Job Ready)] [btn(Continue)c] [btn(Stop)s] message=Proceed?"
    )

    assert title == "Job Ready"
    assert message == "Proceed?"
    assert choices == ["Continue", "Stop", "Cancel"]
    assert cancel_label == "Cancel"
    assert button_keys == {"Continue": "c", "Stop": "s"}


def test_parse_macro_prompt_defaults_resume_buttons(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    title, message, choices, cancel_label, _button_keys = executor._parse_macro_prompt(
        "PROMPT title=Confirm buttons=Run|Abort message=Go?"
    )

    assert title == "Confirm"
    assert message == "Go?"
    assert choices == ["Resume", "Run", "Abort", "Cancel"]
    assert cancel_label == "Cancel"


def test_parse_macro_prompt_job_setup_buttons(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    title, message, choices, cancel_label, button_keys = executor._parse_macro_prompt(
        "PROMPT [title(Job Setup)] Choose setup type. [btn(XYZ Plate)x] [btn(Z Plate)z] [btn(Manual)m]"
    )

    assert title == "Job Setup"
    assert message == "Choose setup type."
    assert choices == ["XYZ Plate", "Z Plate", "Manual", "Cancel"]
    assert cancel_label == "Cancel"
    assert button_keys == {"XYZ Plate": "x", "Z Plate": "z", "Manual": "m"}


def test_strip_prompt_tokens_removes_brackets(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    result = executor._strip_prompt_tokens("M0 [title(Hello)] [btn(Continue)c] message=Go")

    assert "[" not in result
    assert "title" not in result


def test_format_prompt_macros_expands_namespace(macro_app_factory) -> None:
    import types as py_types

    app = macro_app_factory(True)
    executor = MacroExecutor(app)
    macro_vars = {"macro": py_types.SimpleNamespace(foo="bar")}

    result = executor._format_prompt_macros("Value [macro.foo]", macro_vars)

    assert result == "Value bar"


def test_format_prompt_macros_falls_back_to_dict(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)
    macro_vars = {"foo": "bar"}

    result = executor._format_prompt_macros("Value [macro.foo]", macro_vars)

    assert result == "Value bar"


def test_format_prompt_macros_missing_returns_empty(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    result = executor._format_prompt_macros("Value [macro.missing]", {})

    assert result == "Value "


def test_format_macro_message_expands_expression(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    with executor.macro_vars() as vars_snapshot:
        vars_snapshot["wx"] = 1.23456

    result = executor._format_macro_message("Probe at [wx]")

    assert result == "Probe at 1.2346"


def test_format_macro_message_unclosed_bracket(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    result = executor._format_macro_message("Value [wx")

    assert result == "Value [wx"


def test_format_macro_message_logs_expression_errors(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    result = executor._format_macro_message("Value [1/0]")

    assert result == "Value "
    logs = list(app.ui_q.queue)
    assert any(
        entry[0] == "log" and "[macro] Message expression error [1/0]:" in entry[1]
        for entry in logs
    )


def test_bcnc_compile_line_handles_macro_directives(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    assert executor._bcnc_compile_line("%wait") == ("WAIT",)
    assert executor._bcnc_compile_line("%msg Hello") == ("MSG", "Hello")
    assert executor._bcnc_compile_line("%update") == ("UPDATE", "")
    assert executor._bcnc_compile_line("%state_return") == "STATE_RETURN"


def test_bcnc_compile_line_skips_when_not_running(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    compiled = executor._bcnc_compile_line("%if running G0 X0")

    assert compiled is None


def test_bcnc_compile_and_evaluate_expression_line(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    with executor.macro_vars() as vars_snapshot:
        vars_snapshot["wx"] = 3.333333

    compiled = executor._bcnc_compile_line("G1 X[1+2] Y[wx]")
    evaluated = executor._bcnc_evaluate_line(compiled)

    assert evaluated == "G1 X3 Y3.3333"


def test_bcnc_evaluate_line_logs_expression_errors(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    compiled = executor._bcnc_compile_line("G1 X[1/0]")

    with pytest.raises(ZeroDivisionError):
        executor._bcnc_evaluate_line(compiled)

    logs = list(app.ui_q.queue)
    assert any(
        entry[0] == "log" and entry[1].startswith("[macro] Expression evaluation failed:")
        for entry in logs
    )


def test_parse_macro_prompt_noresume_excludes_resume(macro_app_factory) -> None:
    app = macro_app_factory(True)
    executor = MacroExecutor(app)

    _title, _message, choices, cancel_label, _keys = executor._parse_macro_prompt(
        "PROMPT noresume buttons=Run|Abort message=Go"
    )

    assert choices == ["Run", "Abort", "Cancel"]
    assert cancel_label == "Cancel"


def test_macro_restore_state_skips_when_alarm(macro_app_factory) -> None:
    app = macro_app_factory(True)

    class _Grbl:
        def is_connected(self):
            return True

    app.grbl = _Grbl()
    app._alarm_locked = True
    executor = MacroExecutor(app)
    executor._macro_saved_state = {"units": "G21"}

    assert executor._macro_restore_state() is False
    logs = list(app.ui_q.queue)
    assert any("STATE_RETURN skipped" in entry[1] for entry in logs if entry[0] == "log")


def test_macro_restore_state_sends_tokens(macro_app_factory) -> None:
    app = macro_app_factory(True)
    sent = []

    class _Grbl:
        def is_connected(self):
            return True

    app.grbl = _Grbl()
    app._alarm_locked = False
    app._set_unit_mode = lambda _mode: None

    executor = MacroExecutor(app)
    executor._macro_saved_state = {
        "WCS": "G54",
        "plane": "G17",
        "units": "G21",
        "distance": "G90",
        "feedmode": "G94",
        "spindle": "M5",
        "coolant": "M9",
    }
    executor._macro_send = lambda command, wait_for_idle=True: sent.append(command)

    assert executor._macro_restore_state() is True
    assert sent == ["G54 G17 G21 G90 G94 M5 M9"]
    assert executor._macro_state_restored is True


class _BoolVar:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value


class _NumVar:
    def __init__(self, value: float) -> None:
        self._value = value

    def get(self) -> float:
        return self._value


class _MacroTestGrbl:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.connected = True
        self.manual_completion = True

    def is_connected(self) -> bool:
        return self.connected

    def is_streaming(self) -> bool:
        return False

    def send_immediate(self, command: str) -> None:
        self.sent.append(command)

    def send_realtime(self, _cmd) -> None:
        return None

    def wait_for_manual_completion(self, timeout_s: float = 0.0) -> bool:
        return self.manual_completion


class _MacroTestApp:
    def __init__(self, *, line_timeout: float, total_timeout: float, gui_logging: bool = True) -> None:
        self.ui_q = queue.Queue()
        self.grbl = _MacroTestGrbl()
        self.macros_allow_python = _BoolVar(True)
        self.gui_logging_enabled = _BoolVar(gui_logging)
        self._alarm_locked = False
        self._machine_state_text = "Idle"
        self._homing_in_progress = False
        self._closing = False
        self.macro_line_timeout_sec = _NumVar(line_timeout)
        self.macro_total_timeout_sec = _NumVar(total_timeout)
        self.logged_exceptions: list[tuple[str, str]] = []

    def _send_manual(self, command: str, _source: str) -> None:
        self.grbl.send_immediate(command)

    def _set_unit_mode(self, _mode: str) -> None:
        return None

    def _log_exception(self, title: str, exc: Exception, **_kwargs) -> None:
        self.logged_exceptions.append((title, str(exc)))


class _PromptQueue:
    def __init__(self, *, choice: str | None = None) -> None:
        self.queue: queue.Queue = queue.Queue()
        self._choice = choice

    def put(self, item, *args, **kwargs):
        _ = args, kwargs
        self.queue.put(item)
        if item and item[0] == "macro_prompt" and self._choice is not None:
            result_q = item[5]
            result_q.put(self._choice)


def _prepare_runtime_executor(app: _MacroTestApp) -> MacroExecutor:
    executor = MacroExecutor(app)
    executor._macro_wait_for_modal = lambda *_args, **_kwargs: True
    executor._macro_wait_for_status = lambda *_args, **_kwargs: True
    executor._macro_wait_for_idle = lambda *_args, **_kwargs: None
    executor._macro_force_mm = lambda: None
    executor._macro_restore_units = lambda: None
    executor._snapshot_macro_state = lambda: {"units": "G21"}
    return executor


def test_macro_worker_logs_line_failures_with_line_number() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=30.0)
    executor = _prepare_runtime_executor(app)
    executor._execute_command = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))

    executor._run_macro_worker(["Macro", "Tip", "G0 X1"], "Macro-1")

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("Line 3 failed: boom" in line for line in logs)
    assert any("[macro][audit] L3 error: boom" in line for line in logs)


def test_macro_worker_enforces_line_timeout() -> None:
    app = _MacroTestApp(line_timeout=0.01, total_timeout=30.0)
    executor = _prepare_runtime_executor(app)

    def _slow_command(*_args, **_kwargs):
        time.sleep(0.03)
        return True

    executor._execute_command = _slow_command

    executor._run_macro_worker(["Macro", "Tip", "G0 X1"], "Macro-1")

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("Line 3 timed out" in line for line in logs)
    assert any("[macro][audit] L3 timeout:" in line for line in logs)


def test_macro_worker_enforces_total_timeout() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=0.02)
    executor = _prepare_runtime_executor(app)

    def _slow_command(*_args, **_kwargs):
        time.sleep(0.015)
        return True

    executor._execute_command = _slow_command

    executor._run_macro_worker(["Macro", "Tip", "G0 X1", "G0 X2", "G0 X3"], "Macro-1")

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("Macro timed out after 0.0s; aborted." in line for line in logs)
    assert any("[macro][audit] L" in line and "total runtime exceeded" in line for line in logs)


def test_macro_worker_audit_logs_raw_and_successful_lines() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=30.0, gui_logging=True)
    executor = _prepare_runtime_executor(app)

    executor._run_macro_worker(["Macro", "Tip", "G0 X1"], "Macro-1")

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("[macro][audit] L3 raw: G0 X1" in line for line in logs)
    assert any("[macro][audit] L3 ok" in line for line in logs)


def test_macro_send_raises_when_disconnected() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=30.0)
    executor = _prepare_runtime_executor(app)
    app.grbl.connected = False

    with pytest.raises(RuntimeError, match="disconnected"):
        executor._macro_send("G0 X1")


def test_macro_send_raises_on_manual_completion_timeout() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=30.0)
    executor = _prepare_runtime_executor(app)
    app.grbl.manual_completion = False

    with pytest.raises(TimeoutError, match="timed out"):
        executor._macro_send("G0 X1")


def test_notify_macro_compile_error_without_post_ui_thread_logs_fallback() -> None:
    app = _MacroTestApp(line_timeout=5.0, total_timeout=30.0)
    executor = _prepare_runtime_executor(app)

    executor._notify_macro_compile_error("Macro-1", "G1 X[", 3, "Bad expression")

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("Compile error notification: Bad expression" in line for line in logs)


def test_execute_command_prompt_timeout_cancels_macro(macro_app_factory) -> None:
    app = macro_app_factory(True)
    app._macro_prompt_timeout_s = 0.01
    app.ui_q = _PromptQueue(choice=None)
    executor = MacroExecutor(app)
    executor.ui_q = app.ui_q

    result = executor._execute_command("PROMPT title=Hold message=Proceed?")

    assert result is False
    logs = [entry[1] for entry in list(app.ui_q.queue.queue) if entry[0] == "log"]
    assert any("Prompt timed out" in line for line in logs)


def test_execute_command_prompt_accepts_choice(macro_app_factory) -> None:
    app = macro_app_factory(True)
    app._macro_prompt_timeout_s = 0.5
    app.ui_q = _PromptQueue(choice="Resume")
    executor = MacroExecutor(app)
    executor.ui_q = app.ui_q

    result = executor._execute_command("PROMPT title=Hold message=Proceed?")

    assert result is True
    with executor.macro_vars() as vars_snapshot:
        assert vars_snapshot["prompt_choice"] == "Resume"
