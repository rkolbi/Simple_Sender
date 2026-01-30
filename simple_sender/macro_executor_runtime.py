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

from simple_sender.types import MacroExecutorState
logger = logging.getLogger(__name__)


class MacroRunnerMixin(MacroExecutorState):
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
        name = lines[0].strip() if lines else f"Macro {index}"
        tip = lines[1].strip() if len(lines) > 1 else ""
        ts = time.strftime("%H:%M:%S")
        if bool(self.app.gui_logging_enabled.get()):
            if tip:
                self.app.streaming_controller.log(f"[{ts}] Macro: {name} | Tip: {tip}")
            else:
                self.app.streaming_controller.log(f"[{ts}] Macro: {name}")
            self.app.streaming_controller.log(f"[{ts}] Macro contents:")
            for raw in lines[2:]:
                self.app.streaming_controller.log(f"[{ts}]   {raw.rstrip()}")
        t = threading.Thread(target=self._run_macro_worker, args=(lines, path), daemon=True)
        t.start()

    def _run_macro_worker(self, lines: list[str], path: str | None):
        start = time.perf_counter()
        executed = 0
        self._alarm_event.clear()
        self._alarm_notified = False
        name = lines[0].strip() if lines else "Macro"
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
                return
            self._macro_saved_state = self._snapshot_macro_state()
            if self.grbl.is_connected():
                self._macro_force_mm()
            for idx in range(2, len(lines)):
                raw = lines[idx]
                raw_line = raw.rstrip("\r\n")
                line = raw_line.strip()
                self._current_macro_line = raw_line
                if self._alarm_event.is_set():
                    break
                if not line:
                    continue
                executed += 1
                compiled = self._bcnc_compile_line(self._strip_prompt_tokens(line))
                if isinstance(compiled, tuple) and compiled and compiled[0] == "COMPILE_ERROR":
                    self.ui_q.put(("log", f"[macro] Compile error: {compiled[1]}"))
                    self._notify_macro_compile_error(path, raw_line, idx + 1, compiled[1])
                    break
                if compiled is None:
                    continue
                if isinstance(compiled, tuple):
                    kind = compiled[0]
                    if kind == "WAIT":
                        self._macro_wait_for_idle()
                    elif kind == "MSG":
                        msg = compiled[1] if len(compiled) > 1 else ""
                        if msg:
                            msg = self._format_macro_message(str(msg))
                            self.ui_q.put(("log", f"[macro] {msg}"))
                    elif kind == "UPDATE":
                        self._macro_wait_for_status()
                    continue
                evaluated = self._bcnc_evaluate_line(compiled)
                if evaluated is None:
                    continue
                if not self._execute_command(evaluated, raw_line):
                    break
                if getattr(self.app, "_alarm_locked", False):
                    self.ui_q.put(("log", "[macro] Alarm detected; aborting macro."))
                    break
        except Exception as exc:
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
            self._macro_lock.release()
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
