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
from tkinter import messagebox

from simple_sender.utils.constants import (
    MACRO_LINE_TIMEOUT,
    MACRO_TOTAL_TIMEOUT,
)
from simple_sender.utils.macro_headers import parse_macro_header
from simple_sender.types import MacroExecutorState
logger = logging.getLogger(__name__)


class MacroRunnerMixin(MacroExecutorState):
    def _validate_macro_color(self, color: str) -> bool:
        checker = getattr(self.app, "winfo_rgb", None)
        if not callable(checker):
            return False
        try:
            checker(color)
            return True
        except Exception:
            return False

    def _macro_timeout_setting(self, attr_name: str, default_value: float) -> float:
        value = getattr(self.app, attr_name, default_value)
        try:
            if hasattr(value, "get"):
                value = value.get()
            timeout_s = float(value)
        except Exception:
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
        except Exception:
            return True

    def _macro_audit(self, message: str, *, force: bool = False) -> None:
        if not force and not self._macro_audit_enabled():
            return
        self.ui_q.put(("log", f"[macro][audit] {message}"))

    def run_macro(self, index: int):
        if not self.grbl.is_connected():
            messagebox.showwarning("Macro blocked", "Connect to GRBL first.")
            return
        if self.grbl.is_streaming():
            messagebox.showwarning("Macro blocked", "Stop the stream before running a macro.")
            return
        if bool(getattr(self.app, "_alarm_locked", False)):
            messagebox.showwarning("Macro blocked", "Clear the alarm before running a macro.")
            return
        path = self.macro_path(index)
        if not path:
            return
        if not self._macro_lock.acquire(blocking=False):
            messagebox.showwarning("Macro busy", "Another macro is running.")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as exc:
            messagebox.showerror("Macro error", str(exc))
            self._macro_lock.release()
            return
        name, tip, _color, _text_color, body_start = parse_macro_header(
            lines,
            color_validator=self._validate_macro_color,
        )
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
        t = threading.Thread(
            target=self._run_macro_worker,
            args=(lines, path, body_start),
            daemon=True,
        )
        t.start()

    def _run_macro_worker(self, lines: list[str], path: str | None, body_start: int = 2):
        start = time.perf_counter()
        executed = 0
        line_timeout_s = self._macro_line_timeout_s()
        total_timeout_s = self._macro_total_timeout_s()
        self._alarm_event.clear()
        self._alarm_notified = False
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
            except Exception:
                pass
        try:
            with self._macro_vars_lock:
                self._macro_local_vars = {"app": self.app, "os": os}
                self._macro_vars["app"] = self.app
                self._macro_vars["os"] = os
            self._macro_state_restored = False
            self._macro_saved_state = None
            with self._macro_vars_lock:
                modal_seq = int(self._macro_vars.get("_modal_seq", 0) or 0)
            self._macro_send("$G")
            modal_ok = self._macro_wait_for_modal(modal_seq)
            status_ok = self._macro_wait_for_status()
            if not modal_ok or not status_ok:
                self.ui_q.put(("log", "[macro] Snapshot failed; macro aborted."))
                self._macro_audit("Snapshot failed; aborting.", force=True)
                return
            self._macro_saved_state = self._snapshot_macro_state()
            if self.grbl.is_connected():
                self._macro_force_mm()
            for idx in range(body_start, len(lines)):
                now = time.perf_counter()
                if total_timeout_s > 0 and (now - start) > total_timeout_s:
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
                        self._macro_audit(f"L{line_no} aborted by command", force=True)
                        break
                    self._macro_audit(f"L{line_no} ok")
                    if getattr(self.app, "_alarm_locked", False):
                        self.ui_q.put(("log", "[macro] Alarm detected; aborting macro."))
                        self._macro_audit(f"L{line_no} abort: alarm lock active", force=True)
                        break
                except Exception as exc:
                    logger.exception("Macro line %d failed", line_no)
                    self.ui_q.put(("log", f"[macro] Line {line_no} failed: {exc}"))
                    self._macro_audit(f"L{line_no} error: {exc}", force=True)
                    break
                elapsed_line = time.perf_counter() - line_start
                if line_timeout_s > 0 and elapsed_line > line_timeout_s:
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
            self.ui_q.put(("log", f"[macro] Runtime error: {exc}"))
            self._macro_audit(f"Runtime error: {exc}", force=True)
            self.app._log_exception("Macro error", exc, show_dialog=True, dialog_title="Macro error")
        finally:
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
                except Exception:
                    pass
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
        self.app._post_ui_thread(messagebox.showerror, "Macro compile error", text)

    def notify_alarm(self, message: str | None):
        if self._alarm_notified:
            return
        self._alarm_notified = True
        snippet = self._current_macro_line.strip()
        desc = f"[macro] Alarm during '{snippet}'" if snippet else "[macro] Alarm occurred"
        self.ui_q.put(("log", f"{desc}: {message or 'alarm'}"))
        self._alarm_event.set()

    def clear_alarm_notification(self):
        if self._alarm_event.is_set():
            self._alarm_event.clear()
        self._alarm_notified = False

    def _macro_send(self, command: str, *, wait_for_idle: bool = True):
        if hasattr(self.app, "_send_manual"):
            self.app._send_manual(command, "macro")
        else:
            self.grbl.send_immediate(command)
        if wait_for_idle:
            completed = self.grbl.wait_for_manual_completion()
            if not completed:
                self.ui_q.put(("log", "[macro] Command completion timed out"))
            self._macro_wait_for_idle()
