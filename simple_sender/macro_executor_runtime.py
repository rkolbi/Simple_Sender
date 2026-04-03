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
import threading
import time
import types
from tkinter import messagebox

from simple_sender.macro_timeouts import macro_timeouts_disabled
from simple_sender.utils.constants import (
    MACRO_LINE_TIMEOUT,
    MACRO_TOTAL_TIMEOUT,
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

    def _prepare_tool_change_prompt_context(
        self,
        *,
        index: int,
        allow_streaming_paused: bool,
    ) -> None:
        if int(index) != 4:
            return
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
    def _operator_assisted_macro_has_unlimited_wait(index: int) -> bool:
        return int(index) in {3, 4}

    def run_macro(self, index: int, allow_streaming_paused: bool = False) -> bool:
        if not self.grbl.is_connected():
            messagebox.showwarning("Macro blocked", "Connect to GRBL first.")
            return False
        if bool(getattr(self.app, "_stream_done_pending_idle", False)):
            messagebox.showwarning(
                "Macro blocked",
                "Wait for the previous job to fully finish before running a macro.",
            )
            return False
        if self.grbl.is_streaming():
            allow_during_tool_change = bool(
                allow_streaming_paused
                and bool(getattr(self.grbl, "_paused", False))
                and bool(getattr(self.grbl, "_stream_tool_change_active", False))
            )
            if not allow_during_tool_change:
                messagebox.showwarning("Macro blocked", "Stop the stream before running a macro.")
                return False
        if bool(getattr(self.app, "_alarm_locked", False)):
            messagebox.showwarning("Macro blocked", "Clear the alarm before running a macro.")
            return False
        self._prepare_tool_change_prompt_context(
            index=int(index),
            allow_streaming_paused=bool(allow_streaming_paused),
        )
        path = self.macro_path(index)
        if not path:
            return False
        if not self._macro_lock.acquire(blocking=False):
            messagebox.showwarning("Macro busy", "Another macro is running.")
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
        ts = time.strftime("%H:%M:%S")
        if bool(self.app.gui_logging_enabled.get()):
            if tip:
                self.app.streaming_controller.log(f"[{ts}] Macro: {name} | Tip: {tip}")
            else:
                self.app.streaming_controller.log(f"[{ts}] Macro: {name}")
            self.app.streaming_controller.log(f"[{ts}] Macro contents:")
            for raw in lines[body_start:]:
                self.app.streaming_controller.log(f"[{ts}]   {raw.rstrip()}")
        self._last_macro_run_success = None
        t = threading.Thread(
            target=self._run_macro_worker,
            args=(
                lines,
                path,
                body_start,
                self._operator_assisted_macro_has_unlimited_wait(int(index)),
            ),
            daemon=True,
        )
        t.start()
        return True

    def _run_macro_worker(
        self,
        lines: list[str],
        path: str | None,
        body_start: int = 4,
        unlimited_time_override: bool = False,
    ):
        start = time.perf_counter()
        executed = 0
        previous_unlimited_override = bool(
            getattr(self.app, "_macro_operator_assisted_unlimited_time_active", False)
        )
        if unlimited_time_override:
            try:
                self.app._macro_operator_assisted_unlimited_time_active = True
            except Exception as exc:
                _log_suppressed(
                    "Failed enabling operator-assisted macro no-timeout override",
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
        post_ui = getattr(self.app, "_post_ui_thread", None)
        if callable(post_ui) and hasattr(self.app, "_start_macro_status"):
            post_ui(self.app._start_macro_status, name)
        elif hasattr(self.app, "_start_macro_status"):
            try:
                self.app._start_macro_status(name)
            except (AttributeError, RuntimeError) as exc:
                _log_suppressed("Failed starting macro status indicator on UI thread", exc)
        try:
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
                    self.app._macro_operator_assisted_unlimited_time_active = bool(
                        previous_unlimited_override
                    )
                except Exception as exc:
                    _log_suppressed(
                        "Failed restoring operator-assisted macro no-timeout override",
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
            if callable(post_ui) and hasattr(self.app, "_stop_macro_status"):
                post_ui(self.app._stop_macro_status)
            elif hasattr(self.app, "_stop_macro_status"):
                try:
                    self.app._stop_macro_status()
                except (AttributeError, RuntimeError) as exc:
                    _log_suppressed("Failed stopping macro status indicator on UI thread", exc)
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
