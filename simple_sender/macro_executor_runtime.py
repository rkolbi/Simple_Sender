#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import logging
import os
import queue
import threading
import time
import types
from tkinter import messagebox

from simple_sender.builtin_workflow_runtime import execute_builtin_workflow
from simple_sender.builtin_workflows import (
    builtin_workflow_action,
)
from simple_sender.macro_timeouts import macro_timeouts_disabled
from simple_sender.macro_state import macro_fast_poll_scope
from simple_sender.utils.constants import (
    MACRO_LINE_TIMEOUT,
    MACRO_TOTAL_TIMEOUT,
    USER_MACRO_SLOT_COUNT,
)
from simple_sender.utils.macro_headers import MacroFormatError, parse_macro_header
from simple_sender.types import MacroExecutorState
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)

class MacroRunnerMixin(MacroExecutorState):
    _last_macro_run_success: bool | None

    def _capture_builtin_workflow_snapshot(self, workflow_name: str) -> bool:
        attempts = (
            (1.0, 1.0, False),
            (1.5, 1.5, True),
        )
        for modal_timeout_s, status_timeout_s, is_retry in attempts:
            if is_retry:
                if self._alarm_event.is_set() or getattr(self.app, "_closing", False):
                    break
                if not self.grbl.is_connected():
                    break
                self.ui_q.put(
                    ("log", f"[workflow] {workflow_name} startup snapshot was delayed; retrying once."),
                )
                self._workflow_audit("Startup snapshot delayed; retrying once.", force=True)
            with self._macro_vars_lock:
                modal_seq = int(self._macro_vars.get("_modal_seq", 0) or 0)
            self._macro_send("$G")
            modal_ok = self._macro_wait_for_modal(modal_seq, timeout_s=float(modal_timeout_s))
            status_ok = self._macro_wait_for_status(timeout_s=float(status_timeout_s))
            if modal_ok and status_ok:
                if is_retry:
                    self._workflow_audit("Startup snapshot recovered on retry.", force=True)
                return True
        return False

    def is_macro_active(self) -> bool:
        try:
            return bool(getattr(self._macro_lock, "locked", lambda: False)())
        except Exception as exc:
            _log_suppressed("Failed checking macro/workflow runtime lock", exc)
            return False

    @staticmethod
    def _format_macro_file_error(path: str | None, exc: MacroFormatError) -> str:
        filename = os.path.basename(str(path or "").strip()) or "macro file"
        return (
            f"Unsupported macro format in {filename}.\n\n"
            "Supported format:\n"
            "- line 1: label\n"
            "- line 2: tooltip\n"
            "- line 3: button color or blank\n"
            "- line 4: text color or blank\n"
            "- line 5+: macro body\n\n"
            f"{exc.format_details()}"
        )

    def _validate_macro_color(self, color: str) -> bool:
        checker = getattr(self.app, "winfo_rgb", None)
        if not callable(checker):
            return False
        try:
            checker(color)
            return True
        except (TypeError, ValueError, RuntimeError):
            return False

    def _macro_timeout_setting(self, attr_name: str, default_value: float) -> float:
        if macro_timeouts_disabled(self.app):
            return 0.0
        value = getattr(self.app, attr_name, default_value)
        try:
            if hasattr(value, "get"):
                value = value.get()
            timeout_s = float(value)
        except (TypeError, ValueError):
            return float(default_value)
        if timeout_s <= 0:
            return 0.0
        return timeout_s

    def _macro_line_timeout_s(self) -> float:
        return self._macro_timeout_setting("macro_line_timeout_sec", MACRO_LINE_TIMEOUT)

    def _macro_total_timeout_s(self) -> float:
        return self._macro_timeout_setting("macro_total_timeout_sec", MACRO_TOTAL_TIMEOUT)

    def _macro_audit_enabled(self) -> bool:
        enabled = getattr(self.app, "gui_logging_enabled", True)
        try:
            if hasattr(enabled, "get"):
                enabled = enabled.get()
            return bool(enabled)
        except (AttributeError, TypeError, ValueError):
            return True

    def _macro_audit(self, message: str, *, force: bool = False) -> None:
        if not force and not self._macro_audit_enabled():
            return
        self.ui_q.put(("log", f"[macro][audit] {message}"))

    def _workflow_audit(self, message: str, *, force: bool = False) -> None:
        if not force and not self._macro_audit_enabled():
            return
        self.ui_q.put(("log", f"[workflow][audit] {message}"))

    def _reset_prompt_state(self) -> None:
        with self._macro_vars_lock:
            self._macro_local_vars = {"app": self.app, "os": os}
            self._macro_vars["app"] = self.app
            self._macro_vars["os"] = os
            self._macro_vars["prompt_choice"] = ""
            self._macro_vars["prompt_choice_key"] = None
            self._macro_vars["prompt_choice_label"] = ""
            self._macro_vars["prompt_index"] = -1
            self._macro_vars["prompt_cancelled"] = False
            macro_ns = self._macro_vars.get("macro")
            if isinstance(macro_ns, types.SimpleNamespace):
                setattr(macro_ns, "prompt_choice", "")
                setattr(macro_ns, "prompt_choice_key", None)
                setattr(macro_ns, "prompt_choice_label", "")
                setattr(macro_ns, "prompt_index", -1)
                setattr(macro_ns, "prompt_cancelled", False)

    def _workflow_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        *,
        cancel_label: str = "Cancel",
        button_keys: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        prompt_choices = [str(choice) for choice in choices]
        cancel_text = str(cancel_label or "Cancel").strip() or "Cancel"
        if cancel_text not in prompt_choices:
            prompt_choices.append(cancel_text)
        prompt_timeout_s = float(getattr(self.app, "_macro_prompt_timeout_s", 0.0) or 0.0)
        if macro_timeouts_disabled(self.app):
            prompt_timeout_s = 0.0
        if prompt_timeout_s < 0.0:
            prompt_timeout_s = 0.0
        result_q: queue.Queue[str] = queue.Queue(maxsize=1)
        self.ui_q.put(("macro_prompt", str(title), str(message), prompt_choices, cancel_text, result_q))
        prompt_started = time.monotonic()
        while True:
            try:
                choice = str(result_q.get(timeout=0.2))
                break
            except queue.Empty:
                if self._alarm_event.is_set():
                    choice = cancel_text
                    self.ui_q.put(("log", "[workflow] Prompt canceled by ALL STOP; workflow aborted."))
                    break
                if getattr(self.app, "_closing", False):
                    choice = cancel_text
                    break
                if prompt_timeout_s and (time.monotonic() - prompt_started) >= prompt_timeout_s:
                    choice = cancel_text
                    self.ui_q.put(
                        ("log", f"[workflow] Prompt timed out after {prompt_timeout_s:.1f}s; workflow canceled."),
                    )
                    break
        if choice not in prompt_choices:
            choice = cancel_text
        choice_index = prompt_choices.index(choice) if choice in prompt_choices else -1
        key = None
        if isinstance(button_keys, dict):
            key = button_keys.get(choice)
        with self._macro_vars_lock:
            self._macro_vars["prompt_choice"] = choice
            self._macro_vars["prompt_choice_key"] = key
            self._macro_vars["prompt_choice_label"] = choice
            self._macro_vars["prompt_index"] = choice_index
            self._macro_vars["prompt_cancelled"] = choice == cancel_text
            macro_ns = self._macro_vars.get("macro")
            if isinstance(macro_ns, types.SimpleNamespace):
                setattr(macro_ns, "prompt_choice", choice)
                setattr(macro_ns, "prompt_choice_key", key)
                setattr(macro_ns, "prompt_choice_label", choice)
                setattr(macro_ns, "prompt_index", choice_index)
                setattr(macro_ns, "prompt_cancelled", choice == cancel_text)
        self.ui_q.put(("log", f"[workflow] Prompt: {message} | Selected: {choice}"))
        if choice == cancel_text:
            self.ui_q.put(("log", "[workflow] Prompt canceled; workflow aborted."))
            raise RuntimeError("Workflow canceled.")
        return {
            "choice": choice,
            "key": key,
            "index": choice_index,
            "cancelled": False,
        }

    def _start_status_indicator(self, label: str, *, workflow: bool) -> None:
        post_ui = getattr(self.app, "_post_ui_thread", None)
        if workflow and hasattr(self.app, "_start_workflow_status"):
            callback = self.app._start_workflow_status
        else:
            callback = getattr(self.app, "_start_macro_status", None)
        if not callable(callback):
            return
        if callable(post_ui):
            post_ui(callback, label)
            return
        try:
            callback(label)
        except (AttributeError, RuntimeError) as exc:
            _log_suppressed("Failed starting status indicator on UI thread", exc)

    def _stop_status_indicator(self, *, workflow: bool) -> None:
        post_ui = getattr(self.app, "_post_ui_thread", None)
        if workflow and hasattr(self.app, "_stop_workflow_status"):
            callback = self.app._stop_workflow_status
        else:
            callback = getattr(self.app, "_stop_macro_status", None)
        if not callable(callback):
            return
        if callable(post_ui):
            post_ui(callback)
            return
        try:
            callback()
        except (AttributeError, RuntimeError) as exc:
            _log_suppressed("Failed stopping status indicator on UI thread", exc)

    def _prepare_tool_change_workflow_context(
        self,
        *,
        allow_streaming_paused: bool,
    ) -> None:
        stream_tool_change_active = bool(
            allow_streaming_paused
            and bool(getattr(self.grbl, "_stream_tool_change_active", False))
        )
        context = "stream" if stream_tool_change_active else "manual"
        try:
            with self._macro_vars_lock:
                self._macro_vars["tool_change_context"] = context
                macro_ns = self._macro_vars.get("macro")
                state_ns = getattr(macro_ns, "state", None)
                if state_ns is not None:
                    setattr(state_ns, "TOOL_CHANGE_CONTEXT", context)
                if not stream_tool_change_active:
                    self._macro_vars["tool_change_required_tool_name"] = ""
        except Exception as exc:
            _log_suppressed("Failed preparing tool-change prompt context", exc)

    @staticmethod
    def _is_editable_user_macro_index(index: int) -> bool:
        return 1 <= int(index) <= int(USER_MACRO_SLOT_COUNT)

    def _macro_start_blocked(
        self,
        *,
        allow_streaming_paused: bool,
        title: str,
        noun: str,
    ) -> bool:
        if not self.grbl.is_connected():
            messagebox.showwarning(title, "Connect to GRBL first.")
            return True
        if bool(getattr(self.app, "_stream_done_pending_idle", False)):
            messagebox.showwarning(
                title,
                f"Wait for the previous job to fully finish before running a {noun}.",
            )
            return True
        if self.grbl.is_streaming():
            allow_during_tool_change = bool(
                allow_streaming_paused
                and bool(getattr(self.grbl, "_paused", False))
                and bool(getattr(self.grbl, "_stream_tool_change_active", False))
            )
            if not allow_during_tool_change:
                messagebox.showwarning(title, f"Stop the stream before running a {noun}.")
                return True
        if bool(getattr(self.app, "_alarm_locked", False)):
            messagebox.showwarning(title, "Clear the alarm before running a workflow.")
            return True
        if not self._macro_lock.acquire(blocking=False):
            messagebox.showwarning(title, "Another workflow is already running.")
            return True
        return False

    def _start_macro_execution(
        self,
        *,
        lines: list[str],
        path: str | None,
        body_start: int,
        name: str,
        tip: str,
        log_label: str,
        unlimited_time_override: bool,
    ) -> bool:
        ts = time.strftime("%H:%M:%S")
        if bool(self.app.gui_logging_enabled.get()):
            if tip:
                self.app.streaming_controller.log(
                    f"[{ts}] {log_label}: {name} | Tip: {tip}"
                )
            else:
                self.app.streaming_controller.log(f"[{ts}] {log_label}: {name}")
            self.app.streaming_controller.log(f"[{ts}] {log_label} contents:")
            for raw in lines[body_start:]:
                self.app.streaming_controller.log(f"[{ts}]   {raw.rstrip()}")
        self._last_macro_run_success = None
        t = threading.Thread(
            target=self._run_macro_worker,
            args=(
                lines,
                path,
                body_start,
                unlimited_time_override,
            ),
            daemon=True,
        )
        t.start()
        return True

    def _start_builtin_workflow_execution(
        self,
        *,
        action,
    ) -> bool:
        ts = time.strftime("%H:%M:%S")
        if bool(self.app.gui_logging_enabled.get()):
            self.app.streaming_controller.log(
                f"[{ts}] Workflow: {action.label} | Tip: {action.tooltip}"
            )
        self._last_macro_run_success = None
        t = threading.Thread(
            target=self._run_builtin_workflow_worker,
            args=(action,),
            daemon=True,
        )
        t.start()
        return True

    def run_macro(self, index: int, allow_streaming_paused: bool = False) -> bool:
        index = int(index)
        if not self._is_editable_user_macro_index(index):
            self._last_macro_run_success = False
            messagebox.showerror(
                "Macro error",
                "Only editable user macros can run through the generic macro path.",
            )
            return False
        if self._macro_start_blocked(
            allow_streaming_paused=bool(allow_streaming_paused),
            title="Macro blocked",
            noun="macro",
        ):
            return False
        path = self.macro_path(index)
        if not path:
            self._macro_lock.release()
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("Macro error", str(exc))
            self._macro_lock.release()
            return False
        try:
            name, tip, _color, _text_color, body_start = parse_macro_header(
                lines,
                color_validator=self._validate_macro_color,
            )
        except MacroFormatError as exc:
            self._last_macro_run_success = False
            messagebox.showerror("Macro error", self._format_macro_file_error(path, exc))
            self._macro_lock.release()
            return False
        if not name:
            name = f"Macro {index}"
        return self._start_macro_execution(
            lines=lines,
            path=path,
            body_start=body_start,
            name=name,
            tip=tip,
            log_label="Macro",
            unlimited_time_override=False,
        )

    def run_builtin_workflow(
        self,
        workflow_id: str,
        allow_streaming_paused: bool = False,
    ) -> bool:
        action = builtin_workflow_action(workflow_id)
        if action.kind == "direct":
            command = getattr(self.app, str(action.command_attr or ""), None)
            if not callable(command):
                messagebox.showerror(
                    "Workflow error",
                    f"Built-in workflow '{action.label}' is unavailable.",
                )
                return False
            try:
                return bool(command())
            except Exception as exc:
                messagebox.showerror(
                    "Workflow error",
                    f"Built-in workflow '{action.label}' failed:\n{exc}",
                )
                return False

        if self._macro_start_blocked(
            allow_streaming_paused=bool(allow_streaming_paused),
            title="Workflow blocked",
            noun="workflow",
        ):
            return False
        if str(workflow_id) == "tool_change":
            self._prepare_tool_change_workflow_context(
                allow_streaming_paused=bool(allow_streaming_paused),
            )
        return self._start_builtin_workflow_execution(action=action)

    def _run_builtin_workflow_worker(self, action) -> None:
        start = time.perf_counter()
        previous_workflow_override = bool(
            getattr(self.app, "_builtin_workflow_unlimited_time_active", False)
        )
        previous_operator_override = bool(
            getattr(self.app, "_operator_assisted_workflow_unlimited_time_active", False)
        )
        try:
            self.app._builtin_workflow_unlimited_time_active = True
            self.app._operator_assisted_workflow_unlimited_time_active = bool(
                getattr(action, "operator_assisted", False)
            )
        except Exception as exc:
            _log_suppressed("Failed enabling workflow no-timeout override", exc)
        self._alarm_event.clear()
        self._alarm_notified = False
        self._manual_error_event.clear()
        self._manual_error_message = ""
        aborted = False
        workflow_name = str(getattr(action, "label", "Workflow") or "Workflow")
        self._workflow_audit(f"Start workflow_id={action.workflow_id!r} name={workflow_name!r}", force=True)
        self._start_status_indicator(workflow_name, workflow=True)
        try:
            with macro_fast_poll_scope(self.app, "_macro_active_fast_poll_count"):
                self._reset_prompt_state()
                self._macro_state_restored = False
                self._macro_saved_state = None
                if not self._capture_builtin_workflow_snapshot(workflow_name):
                    aborted = True
                    self.ui_q.put(("log", "[workflow] Snapshot failed; workflow aborted."))
                    self._workflow_audit("Snapshot failed; aborting.", force=True)
                    return
                self._macro_saved_state = self._snapshot_macro_state()
                if self.grbl.is_connected():
                    self._macro_force_mm()
                execute_builtin_workflow(self, action.workflow_id)
                self._workflow_audit("Workflow finished normally.", force=True)
        except Exception as exc:
            aborted = True
            canceled = bool(
                isinstance(exc, RuntimeError)
                and str(exc).strip().lower() == "workflow canceled."
            )
            if canceled:
                self._workflow_audit("Runtime canceled by operator.", force=True)
            else:
                logger.exception("Built-in workflow '%s' failed", action.workflow_id)
                self.ui_q.put(("log", f"[workflow] {workflow_name} failed: {exc}"))
                self._workflow_audit(f"Runtime error: {exc}", force=True)
                self.app._log_exception(
                    "Workflow error",
                    exc,
                    show_dialog=True,
                    dialog_title="Workflow error",
                )
        finally:
            try:
                self.app._builtin_workflow_unlimited_time_active = bool(previous_workflow_override)
                self.app._operator_assisted_workflow_unlimited_time_active = bool(previous_operator_override)
            except Exception as exc:
                _log_suppressed("Failed restoring workflow timeout overrides", exc)
            self._last_macro_run_success = not aborted
            try:
                if not self._macro_state_restored:
                    self._workflow_restore_units()
            except Exception as exc:
                logger.exception("Workflow unit restore failed: %s", exc)
                self.ui_q.put(("log", f"[workflow] Unit restore failed: {exc}"))
            self._macro_saved_state = None
            self._macro_state_restored = False
            if self._macro_lock.locked():
                self._macro_lock.release()
            else:
                logger.warning("Built-in workflow finished without a held workflow lock.")
            self._stop_status_indicator(workflow=True)
            duration = time.perf_counter() - start
            self._workflow_audit(
                f"Done workflow_id={action.workflow_id!r} name={workflow_name!r} duration={duration:.2f}s",
                force=True,
            )

    def _run_macro_worker(
        self,
        lines: list[str],
        path: str | None,
        body_start: int = 4,
        unlimited_time_override: bool = False,
    ):
        start = time.perf_counter()
        executed = 0
        previous_operator_override = bool(
            getattr(self.app, "_operator_assisted_workflow_unlimited_time_active", False)
        )
        previous_workflow_override = bool(
            getattr(self.app, "_builtin_workflow_unlimited_time_active", False)
        )
        if unlimited_time_override:
            try:
                self.app._builtin_workflow_unlimited_time_active = True
            except Exception as exc:
                _log_suppressed(
                    "Failed enabling workflow no-timeout override",
                    exc,
                )
            try:
                self.app._operator_assisted_workflow_unlimited_time_active = False
            except Exception as exc:
                _log_suppressed(
                    "Failed applying operator-assisted workflow timeout override",
                    exc,
                )
        line_timeout_s = self._macro_line_timeout_s()
        total_timeout_s = self._macro_total_timeout_s()
        self._alarm_event.clear()
        self._alarm_notified = False
        self._manual_error_event.clear()
        self._manual_error_message = ""
        aborted = False
        name = lines[0].strip() if lines else "Macro"
        self._macro_audit(
            (
                f"Start name={name!r} path={path or '<unknown>'} "
                f"line_timeout={line_timeout_s:.1f}s total_timeout={total_timeout_s:.1f}s"
            ),
            force=True,
        )
        self._start_status_indicator(name, workflow=False)
        try:
            with macro_fast_poll_scope(self.app, "_macro_active_fast_poll_count"):
                try:
                    self._reset_prompt_state()
                    self._macro_state_restored = False
                    self._macro_saved_state = None
                    with self._macro_vars_lock:
                        modal_seq = int(self._macro_vars.get("_modal_seq", 0) or 0)
                    self._macro_send("$G")
                    modal_ok = self._macro_wait_for_modal(modal_seq)
                    status_ok = self._macro_wait_for_status()
                    if not modal_ok or not status_ok:
                        aborted = True
                        self.ui_q.put(("log", "[macro] Snapshot failed; macro aborted."))
                        self._macro_audit("Snapshot failed; aborting.", force=True)
                        return
                    self._macro_saved_state = self._snapshot_macro_state()
                    if self.grbl.is_connected():
                        self._macro_force_mm()
                    for idx in range(body_start, len(lines)):
                        now = time.perf_counter()
                        if total_timeout_s > 0 and (now - start) > total_timeout_s:
                            aborted = True
                            self.ui_q.put(
                                ("log", f"[macro] Macro timed out after {total_timeout_s:.1f}s; aborted."),
                            )
                            self._macro_audit(
                                f"L{idx + 1} timeout: total runtime exceeded {total_timeout_s:.1f}s",
                                force=True,
                            )
                            break
                        raw = lines[idx]
                        raw_line = raw.rstrip("\r\n")
                        line = raw_line.strip()
                        self._current_macro_line = raw_line
                        if self._alarm_event.is_set():
                            aborted = True
                            self._macro_audit(f"L{idx + 1} abort: alarm event set", force=True)
                            break
                        if not line:
                            continue
                        executed += 1
                        line_no = idx + 1
                        self._macro_audit(f"L{line_no} raw: {raw_line}")
                        line_start = time.perf_counter()
                        try:
                            compiled = self._bcnc_compile_line(self._strip_prompt_tokens(line))
                            if isinstance(compiled, tuple) and compiled and compiled[0] == "COMPILE_ERROR":
                                aborted = True
                                self.ui_q.put(("log", f"[macro] Compile error: {compiled[1]}"))
                                self._macro_audit(f"L{line_no} compile_error: {compiled[1]}", force=True)
                                self._notify_macro_compile_error(path, raw_line, line_no, compiled[1])
                                break
                            if compiled is None:
                                self._macro_audit(f"L{line_no} skipped")
                                continue
                            if isinstance(compiled, tuple):
                                kind = compiled[0]
                                self._macro_audit(f"L{line_no} directive: {kind}")
                                if kind == "WAIT":
                                    wait_timeout_s = line_timeout_s if line_timeout_s > 0 else 30.0
                                    self._macro_wait_for_idle(timeout_s=wait_timeout_s)
                                elif kind == "MSG":
                                    msg = compiled[1] if len(compiled) > 1 else ""
                                    if msg:
                                        msg = self._format_macro_message(str(msg))
                                        self.ui_q.put(("log", f"[macro] {msg}"))
                                elif kind == "UPDATE":
                                    update_timeout_s = min(5.0, line_timeout_s) if line_timeout_s > 0 else 1.0
                                    self._macro_wait_for_status(timeout_s=max(update_timeout_s, 0.1))
                                self._macro_audit(f"L{line_no} ok")
                                continue
                            evaluated = self._bcnc_evaluate_line(compiled)
                            if evaluated is None:
                                self._macro_audit(f"L{line_no} python_exec_ok")
                                continue
                            self._macro_audit(f"L{line_no} eval: {evaluated}")
                            if not self._execute_command(evaluated, raw_line):
                                aborted = True
                                self._macro_audit(f"L{line_no} aborted by command", force=True)
                                break
                            self._macro_audit(f"L{line_no} ok")
                            if getattr(self.app, "_alarm_locked", False):
                                aborted = True
                                self.ui_q.put(("log", "[macro] Alarm detected; aborting macro."))
                                self._macro_audit(f"L{line_no} abort: alarm lock active", force=True)
                                break
                        except Exception as exc:
                            aborted = True
                            logger.exception("Macro line %d failed", line_no)
                            self.ui_q.put(("log", f"[macro] Line {line_no} failed: {exc}"))
                            self._macro_audit(f"L{line_no} error: {exc}", force=True)
                            break
                        elapsed_line = time.perf_counter() - line_start
                        if line_timeout_s > 0 and elapsed_line > line_timeout_s:
                            aborted = True
                            self.ui_q.put(
                                (
                                    "log",
                                    f"[macro] Line {line_no} timed out after {elapsed_line:.2f}s "
                                    f"(limit {line_timeout_s:.2f}s); aborted.",
                                )
                            )
                            self._macro_audit(
                                f"L{line_no} timeout: {elapsed_line:.2f}s > {line_timeout_s:.2f}s",
                                force=True,
                            )
                            break
                except Exception as exc:
                    aborted = True
                    canceled = bool(
                        isinstance(exc, RuntimeError)
                        and str(exc).strip().lower() == "macro canceled."
                        and bool(self._alarm_event.is_set())
                    )
                    if canceled:
                        self._macro_audit("Runtime canceled by operator.", force=True)
                    else:
                        self.ui_q.put(("log", f"[macro] Runtime error: {exc}"))
                        self._macro_audit(f"Runtime error: {exc}", force=True)
                        self.app._log_exception(
                            "Macro error",
                            exc,
                            show_dialog=True,
                            dialog_title="Macro error",
                        )
        finally:
            if unlimited_time_override:
                try:
                    self.app._builtin_workflow_unlimited_time_active = bool(
                        previous_workflow_override
                    )
                except Exception as exc:
                    _log_suppressed(
                        "Failed restoring workflow no-timeout override",
                        exc,
                    )
                try:
                    self.app._operator_assisted_workflow_unlimited_time_active = bool(
                        previous_operator_override
                    )
                except Exception as exc:
                    _log_suppressed(
                        "Failed restoring operator-assisted workflow no-timeout override",
                        exc,
                    )
            self._last_macro_run_success = not aborted
            try:
                if not self._macro_state_restored:
                    self._macro_restore_units()
            except Exception as exc:
                logger.exception("Macro unit restore failed: %s", exc)
                self.ui_q.put(("log", f"[macro] Unit restore failed: {exc}"))
            self._macro_saved_state = None
            self._macro_state_restored = False
            if self._macro_lock.locked():
                self._macro_lock.release()
            else:
                logger.warning("Macro worker finished without a held macro lock.")
            self._stop_status_indicator(workflow=False)
            duration = time.perf_counter() - start
            if duration >= 0.2:
                avg = duration / executed if executed else duration
                self.ui_q.put((
                    "log",
                    f"[macro] Executed {executed} line(s) in {duration:.2f}s ({avg:.3f}s/line)",
                ))
            self._macro_audit(
                f"Done name={name!r} executed={executed} duration={duration:.2f}s",
                force=True,
            )

    def _notify_macro_compile_error(
        self,
        path: str | None,
        raw_line: str,
        line_no: int,
        message: str,
    ):
        if not message:
            message = "Unknown compile error"
        location = f"File: {path or 'Unknown macro file'}\nLine {line_no}: {raw_line.strip() or '<empty line>'}"
        text = f"{message}\n\n{location}"
        post_ui = getattr(self.app, "_post_ui_thread", None)
        if callable(post_ui):
            post_ui(messagebox.showerror, "Macro compile error", text)
            return
        self.ui_q.put(("log", f"[macro] Compile error notification: {message}"))

    def notify_alarm(self, message: str | None):
        if self._alarm_notified:
            return
        self._alarm_notified = True
        snippet = self._current_macro_line.strip()
        desc = f"[macro] Alarm during '{snippet}'" if snippet else "[macro] Alarm occurred"
        self.ui_q.put(("log", f"{desc}: {message or 'alarm'}"))
        self._alarm_event.set()

    def notify_manual_error(self, message: str | None):
        text = str(message or "").strip() or "GRBL error"
        self._manual_error_message = text
        self._manual_error_event.set()

    def clear_alarm_notification(self):
        if self._alarm_event.is_set():
            self._alarm_event.clear()
        if self._manual_error_event.is_set():
            self._manual_error_event.clear()
        self._manual_error_message = ""
        self._alarm_notified = False

    def cancel_macro(self, reason: str | None = None) -> bool:
        self._alarm_event.set()
        active = bool(getattr(self._macro_lock, "locked", lambda: False)())
        if not active:
            return False
        text = str(reason or "").strip() or "Canceled."
        self.ui_q.put(("log", f"[macro] {text}"))
        self._last_macro_run_success = False
        return True

    def _macro_send(self, command: str, *, wait_for_idle: bool = True):
        if self._alarm_event.is_set():
            raise RuntimeError("Macro canceled.")
        if not self.grbl.is_connected():
            raise RuntimeError("Controller disconnected during macro execution.")
        self._manual_error_event.clear()
        self._manual_error_message = ""
        tracker = None
        send_tracked = getattr(self.grbl, "send_immediate_tracked", None)
        if callable(send_tracked):
            tracker = send_tracked(command, source="macro")
            if tracker is None:
                raise RuntimeError(f"Controller rejected immediate macro command before send: {command}")
        else:
            accepted = True
            if hasattr(self.app, "_send_manual"):
                accepted = self.app._send_manual(command, "macro")
            else:
                accepted = self.grbl.send_immediate(command)
            if accepted is False:
                raise RuntimeError(f"Controller rejected immediate macro command before send: {command}")
        if wait_for_idle:
            line_timeout_s = self._macro_line_timeout_s()
            completion_timeout_s = 0.0
            if line_timeout_s > 0:
                completion_timeout_s = max(30.0, float(line_timeout_s))
            if tracker is not None:
                completed = tracker.wait(timeout_s=completion_timeout_s)
                if not completed:
                    if self._alarm_event.is_set():
                        raise RuntimeError("Macro canceled.")
                    raise TimeoutError("Command completion timed out.")
                if not bool(getattr(tracker, "success", False)):
                    detail = str(getattr(tracker, "error", "") or "").strip() or "GRBL command rejected."
                    raise RuntimeError(f"Macro command failed: {detail}")
            else:
                completed = self.grbl.wait_for_manual_completion(timeout_s=completion_timeout_s)
                if not completed:
                    if self._alarm_event.is_set():
                        raise RuntimeError("Macro canceled.")
                    if self._manual_error_event.is_set():
                        detail = self._manual_error_message or "GRBL command rejected."
                        raise RuntimeError(f"Macro command failed: {detail}")
                    raise TimeoutError("Command completion timed out.")
            self._macro_wait_for_idle(timeout_s=max(0.0, float(line_timeout_s)))
            if self._alarm_event.is_set():
                raise RuntimeError("Macro canceled.")
            if tracker is None and self._manual_error_event.is_set():
                detail = self._manual_error_message or "GRBL command rejected."
                raise RuntimeError(f"Macro command failed: {detail}")
