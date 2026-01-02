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
# SPDX-License-Identifier: GPL-3.0-or-later
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import os
import sys
import time
import threading
from datetime import datetime
import queue
import re
import types
import csv
import math
import shlex
import traceback
import hashlib
import logging
from collections import deque
from typing import Callable
from contextlib import contextmanager

# GUI imports
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

# Refactored module imports
from simple_sender.grbl_worker import GrblWorker
from simple_sender.gcode_parser import clean_gcode_line, parse_gcode_lines
from simple_sender.utils import Settings
from simple_sender.utils.constants import *
from simple_sender.utils.exceptions import SettingsLoadError, SettingsSaveError
from simple_sender.ui.gcode_viewer import GcodeViewer
from simple_sender.ui.console import Console

logger = logging.getLogger(__name__)
APP_VERSION = "0.1.0"

SERIAL_IMPORT_ERROR = ""

# Serial imports
try:
    import serial
    from serial.tools import list_ports
    SERIAL_AVAILABLE = True
except ImportError as exc:
    serial = None
    list_ports = None
    SERIAL_AVAILABLE = False
    SERIAL_IMPORT_ERROR = str(exc)


# Utility functions

def _hash_lines(lines: list[str] | None) -> str | None:
    if not lines:
        return None
    hasher = hashlib.sha256()
    for ln in lines:
        hasher.update(ln.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _default_settings_store_dir() -> str:
    env_dir = os.getenv("SIMPLE_SENDER_CONFIG_DIR")
    if env_dir:
        return env_dir
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    else:
        base = os.getenv("XDG_CONFIG_HOME")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "SimpleSender")


def _resolve_settings_path() -> str:
    base_dir = _default_settings_store_dir()
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as exc:
        logger.exception("Failed to create settings directory '%s': %s", base_dir, exc)
        fallback_dir = os.path.join(os.path.expanduser("~"), ".simple_sender")
        try:
            os.makedirs(fallback_dir, exist_ok=True)
            base_dir = fallback_dir
        except Exception as exc:
            logger.exception("Failed to create fallback settings directory '%s': %s", fallback_dir, exc)
            base_dir = os.path.dirname(__file__)
    return os.path.join(base_dir, "settings.json")


def _discover_macro_dirs() -> tuple[str, ...]:
    dirs: list[str] = []
    pkg = sys.modules.get("simple_sender")
    if pkg and getattr(pkg, "__file__", None):
        pkg_dir = os.path.dirname(pkg.__file__)
        macros_dir = os.path.join(pkg_dir, "macros")
        if os.path.isdir(macros_dir):
            dirs.append(macros_dir)
    script_dir = os.path.dirname(__file__)
    root_macros = os.path.join(script_dir, "macros")
    if os.path.isdir(root_macros) and root_macros not in dirs:
        dirs.append(root_macros)
    if script_dir not in dirs:
        dirs.append(script_dir)
    return tuple(dirs)


_MACRO_SEARCH_DIRS = _discover_macro_dirs()


class MacroExecutor:
    def __init__(self, app):
        self.app = app
        self.ui_q = app.ui_q
        self.grbl = app.grbl
        self._macro_lock = threading.Lock()
        self._macro_vars_lock = threading.Lock()
        self._macro_local_vars = {"app": app, "os": os}
        self._macro_vars = {
            "prbx": 0.0,
            "prby": 0.0,
            "prbz": 0.0,
            "prbcmd": "G38.2",
            "prbfeed": 10.0,
            "errline": "",
            "wx": 0.0,
            "wy": 0.0,
            "wz": 0.0,
            "wa": 0.0,
            "wb": 0.0,
            "wc": 0.0,
            "mx": 0.0,
            "my": 0.0,
            "mz": 0.0,
            "ma": 0.0,
            "mb": 0.0,
            "mc": 0.0,
            "wcox": 0.0,
            "wcoy": 0.0,
            "wcoz": 0.0,
            "wcoa": 0.0,
            "wcob": 0.0,
            "wcoc": 0.0,
            "curfeed": 0.0,
            "curspindle": 0.0,
            "_camwx": 0.0,
            "_camwy": 0.0,
            "G": [],
            "TLO": 0.0,
            "motion": "G0",
            "WCS": "G54",
            "plane": "G17",
            "feedmode": "G94",
            "distance": "G90",
            "arc": "G91.1",
            "units": "G21",
            "cutter": "",
            "tlo": "",
            "program": "M0",
            "spindle": "M5",
            "coolant": "M9",
            "tool": 0,
            "feed": 0.0,
            "rpm": 0.0,
            "planner": 0,
            "rxbytes": 0,
            "OvFeed": 100,
            "OvRapid": 100,
            "OvSpindle": 100,
            "_OvChanged": False,
            "_OvFeed": 100,
            "_OvRapid": 100,
            "_OvSpindle": 100,
            "diameter": 3.175,
            "cutfeed": 1000.0,
            "cutfeedz": 500.0,
            "safe": 3.0,
            "state": "",
            "pins": "",
            "msg": "",
            "stepz": 1.0,
            "surface": 0.0,
            "thickness": 5.0,
            "stepover": 40.0,
            "PRB": None,
            "version": "",
            "controller": "",
            "running": False,
            "prompt_choice": "",
            "prompt_index": -1,
            "prompt_cancelled": False,
        }

    @contextmanager
    def macro_vars(self):
        with self._macro_vars_lock:
            yield self._macro_vars

    def macro_path(self, index: int) -> str | None:
        for macro_dir in _MACRO_SEARCH_DIRS:
            for prefix in MACRO_PREFIXES:
                for ext in MACRO_EXTS:
                    candidate = os.path.join(macro_dir, f"{prefix}{index}{ext}")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    def run_macro(self, index: int):
        if self.grbl.is_streaming():
            messagebox.showwarning("Macro blocked", "Stop the stream before running a macro.")
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
        t = threading.Thread(target=self._run_macro_worker, args=(lines,), daemon=True)
        t.start()

    def _run_macro_worker(self, lines: list[str]):
        start = time.perf_counter()
        executed = 0
        try:
            with self._macro_vars_lock:
                self._macro_local_vars = {"app": self.app, "os": os}
                self._macro_vars["app"] = self.app
                self._macro_vars["os"] = os
            for raw in lines[2:]:
                line = raw.strip()
                if not line:
                    continue
                executed += 1
                compiled = self._bcnc_compile_line(line)
                if isinstance(compiled, tuple) and compiled and compiled[0] == "COMPILE_ERROR":
                    self.ui_q.put(("log", f"[macro] Compile error: {compiled[1]}"))
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
                            self.ui_q.put(("log", f"[macro] {msg}"))
                    elif kind == "UPDATE":
                        self.grbl.send_realtime(RT_STATUS)
                    continue
                evaluated = self._bcnc_evaluate_line(compiled)
                if evaluated is None:
                    continue
                if not self._execute_bcnc_command(evaluated):
                    break
        except Exception as exc:
            self.app._log_exception("Macro error", exc, show_dialog=True, dialog_title="Macro error")
        finally:
            self._macro_lock.release()
            duration = time.perf_counter() - start
            if duration >= 0.2:
                avg = duration / executed if executed else duration
                self.ui_q.put((
                    "log",
                    f"[macro] Executed {executed} line(s) in {duration:.2f}s ({avg:.3f}s/line)",
                ))

    def _macro_wait_for_idle(self, timeout_s: float = 30.0):
        if not self.grbl.is_connected():
            return
        start = time.time()
        while True:
            if not self.grbl.is_connected():
                return
            if not self.grbl.is_streaming() and str(self.app._machine_state_text).startswith("Idle"):
                return
            if timeout_s and (time.time() - start) > timeout_s:
                self.ui_q.put(("log", "[macro] %wait timeout"))
                return
            time.sleep(0.1)

    def _parse_macro_prompt(self, line: str):
        title = "Macro Pause"
        message = ""
        buttons: list[str] = []
        show_resume = True
        resume_label = "Resume"
        cancel_label = "Cancel"

        match = re.search(r"\((.*?)\)", line)
        if match:
            message = match.group(1).strip()
        try:
            tokens = shlex.split(line)
        except Exception:
            tokens = line.split()
        tokens = tokens[1:] if tokens else []
        msg_parts: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in ("noresume", "no-resume"):
                show_resume = False
                continue
            if "=" in tok:
                key, val = tok.split("=", 1)
                key = key.lower()
                if key in ("title", "t"):
                    title = val
                elif key in ("msg", "message", "text"):
                    message = val
                elif key in ("buttons", "btns"):
                    raw = val.replace("|", ",")
                    buttons = [b.strip() for b in raw.split(",") if b.strip()]
                elif key in ("resume", "resumelabel"):
                    if val.lower() in ("0", "false", "no", "off"):
                        show_resume = False
                    else:
                        resume_label = val
                elif key in ("cancel", "cancellabel"):
                    cancel_label = val
                continue
            msg_parts.append(tok)
        if not message and msg_parts:
            message = " ".join(msg_parts)
        if not message:
            message = "Macro paused."
        extras = [b for b in buttons if b and b not in (resume_label, cancel_label)]
        choices = []
        if show_resume:
            choices.append(resume_label)
        choices.extend(extras)
        choices.append(cancel_label)
        return title, message, choices, cancel_label

    def _execute_bcnc_command(self, line: str):
        if line is None:
            return True
        if isinstance(line, tuple):
            return True
        s = str(line).strip()
        if not s:
            return True
        cmd_parts = s.replace(",", " ").split()
        cmd = cmd_parts[0].upper()

        if cmd in ("M0", "M00", "PROMPT"):
            title, message, choices, cancel_label = self._parse_macro_prompt(s)
            result_q: queue.Queue[str] = queue.Queue()
            self.ui_q.put(("macro_prompt", title, message, choices, cancel_label, result_q))
            while True:
                try:
                    choice = result_q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if self.app._closing:
                        choice = cancel_label
                        break
            if choice not in choices:
                choice = cancel_label
            with self._macro_vars_lock:
                self._macro_vars["prompt_choice"] = choice
                self._macro_vars["prompt_index"] = choices.index(choice) if choice in choices else -1
                self._macro_vars["prompt_cancelled"] = (choice == cancel_label)
            self.ui_q.put(("log", f"[macro] Prompt: {message} | Selected: {choice}"))
            if choice == cancel_label:
                self.ui_q.put(("log", "[macro] Prompt canceled; macro aborted."))
                return False
            return True

        if cmd in ("ABSOLUTE", "ABS"):
            self.grbl.send_immediate("G90")
            return True
        if cmd in ("RELATIVE", "REL"):
            self.grbl.send_immediate("G91")
            return True
        if cmd == "HOME":
            self.grbl.home()
            return True
        if cmd == "OPEN":
            if not self.app.connected:
                self.app._call_on_ui_thread(self.app.toggle_connect)
            return True
        if cmd == "CLOSE":
            if self.app.connected:
                self.app._call_on_ui_thread(self.app.toggle_connect)
            return True
        if cmd == "HELP":
            self.app._call_on_ui_thread(
                messagebox.showinfo,
                "Macro",
                "Help is not available in this sender.",
                timeout=None,
            )
            return True
        if cmd in ("QUIT", "EXIT"):
            self.app._call_on_ui_thread(self.app._on_close)
            return True
        if cmd == "LOAD" and len(cmd_parts) > 1:
            self.app._call_on_ui_thread(
                self.app._load_gcode_from_path,
                " ".join(cmd_parts[1:]),
                timeout=None,
            )
            return True
        if cmd == "UNLOCK":
            self.grbl.unlock()
            return True
        if cmd == "RESET":
            self.grbl.reset()
            return True
        if cmd == "PAUSE":
            self.grbl.hold()
            return True
        if cmd == "RESUME":
            self.grbl.resume()
            return True
        if cmd == "FEEDHOLD":
            self.grbl.hold()
            return True
        if cmd == "STOP":
            self.grbl.stop_stream()
            return True
        if cmd == "RUN":
            self.grbl.start_stream()
            return True
        if cmd == "SAVE":
            self.ui_q.put(("log", "[macro] SAVE is not supported."))
            return True
        if cmd == "SENDHEX" and len(cmd_parts) > 1:
            try:
                b = bytes([int(cmd_parts[1], 16)])
                self.grbl.send_realtime(b)
            except Exception as exc:
                logger.exception("Macro SENDHEX failed: %s", exc)
                self.ui_q.put(("log", f"[macro] SENDHEX failed: {exc}"))
            return True
        if cmd == "SAFE" and len(cmd_parts) > 1:
            try:
                with self._macro_vars_lock:
                    self._macro_vars["safe"] = float(cmd_parts[1])
            except Exception as exc:
                logger.exception("Macro SAFE failed: %s", exc)
                self.ui_q.put(("log", f"[macro] SAFE failed: {exc}"))
            return True
        if cmd == "SET0":
            self.grbl.send_immediate("G92 X0 Y0 Z0")
            return True
        if cmd == "SETX" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 X{cmd_parts[1]}")
            return True
        if cmd == "SETY" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 Y{cmd_parts[1]}")
            return True
        if cmd == "SETZ" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 Z{cmd_parts[1]}")
            return True
        if cmd == "SET":
            parts = []
            if len(cmd_parts) > 1:
                parts.append(f"X{cmd_parts[1]}")
            if len(cmd_parts) > 2:
                parts.append(f"Y{cmd_parts[2]}")
            if len(cmd_parts) > 3:
                parts.append(f"Z{cmd_parts[3]}")
            if parts:
                self.grbl.send_immediate("G92 " + " ".join(parts))
            return True

        if s.startswith("!"):
            self.grbl.hold()
            return True
        if s.startswith("~"):
            self.grbl.resume()
            return True
        if s.startswith("?"):
            self.grbl.send_realtime(RT_STATUS)
            return True
        if s.startswith("\x18"):
            self.grbl.reset()
            return True

        if s.startswith("$") or s.startswith("@") or s.startswith("{"):
            self.grbl.send_immediate(s)
            return True
        if s.startswith("(") or MACRO_GPAT.match(s):
            self.grbl.send_immediate(s)
            return True
        self.grbl.send_immediate(s)
        return True

    def _bcnc_compile_line(self, line: str):
        line = line.strip()
        if not line:
            return None
        if not bool(self.app.macros_allow_python.get()):
            if line.startswith(("%", "_")) or ("[" in line) or ("]" in line) or ("=" in line):
                return ("COMPILE_ERROR", "Macro scripting disabled in settings.")
        if line[0] == "$":
            return line
        line = line.replace("#", "_")
        if line[0] == "%":
            pat = MACRO_AUXPAT.match(line.strip())
            if pat:
                cmd = pat.group(1)
                args = pat.group(2)
            else:
                cmd = None
                args = None
            if cmd == "%wait":
                return ("WAIT",)
            if cmd == "%msg":
                return ("MSG", args if args else "")
            if cmd == "%update":
                return ("UPDATE", args if args else "")
            if line.startswith("%if running"):
                with self._macro_vars_lock:
                    if not self._macro_vars.get("running"):
                        return None
            try:
                return compile(line[1:], "", "exec")
            except Exception as exc:
                return ("COMPILE_ERROR", f"{line} ({exc})")
        if line[0] == "_":
            try:
                return compile(line, "", "exec")
            except Exception as exc:
                return ("COMPILE_ERROR", f"{line} ({exc})")
        if line[0] == ";":
            return None
        out: list[str] = []
        bracket = 0
        paren = 0
        expr = ""
        cmd = ""
        in_comment = False
        for i, ch in enumerate(line):
            if ch == "(":
                paren += 1
                in_comment = bracket == 0
                if not in_comment:
                    expr += ch
            elif ch == ")":
                paren -= 1
                if not in_comment:
                    expr += ch
                if paren == 0 and in_comment:
                    in_comment = False
            elif ch == "[":
                if not in_comment:
                    if MACRO_STDEXPR:
                        ch = "("
                    bracket += 1
                    if bracket == 1:
                        if cmd:
                            out.append(cmd)
                            cmd = ""
                    else:
                        expr += ch
                else:
                    pass
            elif ch == "]":
                if not in_comment:
                    if MACRO_STDEXPR:
                        ch = ")"
                    bracket -= 1
                    if bracket == 0:
                        try:
                            out.append(compile(expr, "", "eval"))
                        except Exception as exc:
                            return ("COMPILE_ERROR", f"[{expr}] in '{line}' ({exc})")
                        expr = ""
                    else:
                        expr += ch
            elif ch == "=":
                if not out and bracket == 0 and paren == 0:
                    for t in " ()-+*/^$":
                        if t in cmd:
                            cmd += ch
                            break
                    else:
                        try:
                            return compile(line, "", "exec")
                        except Exception as exc:
                            return ("COMPILE_ERROR", f"{line} ({exc})")
                else:
                    cmd += ch
            elif ch == ";":
                if not in_comment and paren == 0 and bracket == 0:
                    break
                else:
                    expr += ch
            elif bracket > 0:
                expr += ch
            elif not in_comment:
                cmd += ch
            else:
                pass
        if cmd:
            out.append(cmd)
        if not out:
            return None
        if len(out) > 1:
            return out
        return out[0]

    def _bcnc_evaluate_line(self, compiled):
        if isinstance(compiled, int):
            return None
        if isinstance(compiled, list):
            for i, expr in enumerate(compiled):
                if isinstance(expr, types.CodeType):
                    with self._macro_vars_lock:
                        globals_ctx = self._macro_eval_globals()
                        result = eval(expr, globals_ctx, self._macro_local_vars)
                    if isinstance(result, float):
                        compiled[i] = str(round(result, 4))
                    else:
                        compiled[i] = str(result)
            return "".join(compiled)
        if isinstance(compiled, types.CodeType):
            with self._macro_vars_lock:
                globals_ctx = self._macro_exec_globals()
                exec(compiled, globals_ctx, globals_ctx)
                return None

    def _macro_eval_globals(self) -> dict:
        return self._macro_vars

    def _macro_exec_globals(self) -> dict:
        return self._macro_vars


class StreamingController:
    def __init__(self, app):
        self.app = app
        self.console: tk.Text | None = None
        self.gview = None
        self.progress_pct = None
        self.buffer_fill = None
        self.buffer_fill_pct = None
        self.throughput_var = None
        self._console_lines: list[tuple[str, str | None]] = []
        self._console_filter: str | None = None
        self._pending_console_entries: list[tuple[str, str | None]] = []
        self._pending_console_trim = 0
        self._console_after_id = None
        self._console_render_pending = False
        self._pending_marks_after_id = None
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_buffer = None
        self._progress_after_id = None
        self._buffer_after_id = None

    def attach_widgets(
        self,
        console: tk.Text,
        gview,
        progress_pct: tk.IntVar,
        buffer_fill: tk.StringVar,
        buffer_fill_pct: tk.IntVar,
        throughput_var: tk.StringVar,
    ):
        self.console = console
        self.gview = gview
        self.progress_pct = progress_pct
        self.buffer_fill = buffer_fill
        self.buffer_fill_pct = buffer_fill_pct
        self.throughput_var = throughput_var

    def _console_tag_for_line(self, s: str) -> str | None:
        u = s.upper()
        if "ALARM" in u:
            return "console_alarm"
        if "ERROR" in u:
            return "console_error"
        stripped = s.strip()
        if stripped.upper() in ("<< OK", "OK"):
            return "console_ok"
        if stripped.startswith(">>"):
            return "console_tx"
        if stripped.startswith("<<") and "<" in stripped and ">" in stripped:
            return "console_status"
        return None

    def _is_position_line(self, s: str) -> bool:
        u = s.upper()
        return ("WPOS:" in u) or ("MPOS:" in u)

    def _is_status_line(self, s: str) -> bool:
        stripped = s.strip()
        return (
            stripped.startswith("<< <")
            or (stripped.startswith("<") and stripped.endswith(">"))
            or ("<" in stripped and ">" in stripped)
        )

    def _should_skip_console_entry_for_toggles(self, entry) -> bool:
        if isinstance(entry, tuple):
            s, _ = entry
        else:
            s = str(entry)
        enabled = bool(self.app.console_positions_enabled.get())
        if not enabled and self._is_position_line(s):
            return True
        upper = s.upper()
        if not enabled and self._is_status_line(s):
            if ("ALARM" not in upper) and ("ERROR" not in upper):
                return True
        return False

    def _console_filter_match(self, entry, for_save: bool = False) -> bool:
        if isinstance(entry, tuple):
            s, tag = entry
        else:
            s, tag = str(entry), None
        upper = s.upper()
        if self._console_filter == "alarms" and "ALARM" not in upper:
            return False
        if self._console_filter == "errors" and "ERROR" not in upper:
            return False
        if for_save and self._is_position_line(s):
            return False
        is_pos = self._is_position_line(s)
        enabled = bool(self.app.console_positions_enabled.get())
        is_status = self._is_status_line(s)
        if (not for_save) and not enabled:
            if is_pos:
                return False
            if is_status and ("ALARM" not in upper) and ("ERROR" not in upper):
                return False
        return True

    def _should_suppress_rx_log(self, raw: str) -> bool:
        if not bool(self.app.performance_mode.get()):
            return False
        if self.app._stream_state != "running":
            return False
        upper = raw.upper()
        if ("ALARM" in upper) or ("ERROR" in upper):
            return False
        if "[MSG" in upper or "RESET TO CONTINUE" in upper:
            return False
        return True

    def log(self, s: str, tag: str | None = None):
        if tag is None:
            tag = self._console_tag_for_line(s)
        entry = (s, tag)
        if self._should_skip_console_entry_for_toggles(entry):
            return
        self._console_lines.append(entry)
        overflow = len(self._console_lines) - MAX_CONSOLE_LINES
        if overflow > 0:
            self._console_lines = self._console_lines[overflow:]
            if self._console_filter is not None:
                if bool(self.app.performance_mode.get()):
                    self._queue_console_render()
                    return
                self._render_console()
                return
            if bool(self.app.performance_mode.get()):
                self._pending_console_trim += overflow
            else:
                self._trim_console_widget(overflow)
        if not self._console_filter_match(entry):
            return
        if bool(self.app.performance_mode.get()):
            self._pending_console_entries.append(entry)
            self._schedule_console_flush()
            return
        self._append_to_console(entry)

    def _append_to_console(self, entry):
        if not self.console:
            return
        line, tag = entry
        self.console.config(state="normal")
        if tag:
            self.console.insert("end", line + "\n", (tag,))
        else:
            self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _queue_console_render(self):
        self._console_render_pending = True
        self._schedule_console_flush()

    def _schedule_console_flush(self):
        if self._console_after_id is not None:
            return
        self._console_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_console_updates)

    def _flush_console_updates(self):
        self._console_after_id = None
        if self._console_render_pending:
            self._console_render_pending = False
            self._pending_console_entries = []
            self._pending_console_trim = 0
            self._render_console()
            return
        if (not self._pending_console_entries) and (self._pending_console_trim <= 0):
            return
        if not self.console:
            return
        self.console.config(state="normal")
        if self._pending_console_trim > 0:
            self._trim_console_widget_unlocked(self._pending_console_trim)
            self._pending_console_trim = 0
        for line, tag in self._pending_console_entries:
            if tag:
                self.console.insert("end", line + "\n", (tag,))
            else:
                self.console.insert("end", line + "\n")
        self._pending_console_entries = []
        self.console.see("end")
        self.console.config(state="disabled")

    def _render_console(self):
        if not self.console:
            return
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        for line, tag in self._console_lines:
            if self._console_filter_match((line, tag)):
                if tag:
                    self.console.insert("end", line + "\n", (tag,))
                else:
                    self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _trim_console_widget(self, count: int):
        if count <= 0 or not self.console:
            return
        self.console.config(state="normal")
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def _trim_console_widget_unlocked(self, count: int):
        if count <= 0 or not self.console:
            return
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")

    def set_console_filter(self, mode):
        self._console_filter = mode
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        self._render_console()

    def clear_console(self):
        self._console_lines = []
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        if self.console:
            self.console.config(state="normal")
            self.console.delete("1.0", "end")
            self.console.config(state="disabled")

    def get_console_lines(self) -> list[tuple[str, str | None]]:
        return list(self._console_lines)

    def matches_filter(self, entry, for_save: bool = False) -> bool:
        return self._console_filter_match(entry, for_save=for_save)

    def is_position_line(self, s: str) -> bool:
        return self._is_position_line(s)

    def flush_console(self):
        self._flush_console_updates()

    def render_console(self):
        self._render_console()

    def bind_button_logging(self):
        self.app.bind_class("TButton", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Button", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Canvas", "<Button-1>", self._on_button_press, add="+")

    def _on_button_press(self, event):
        w = event.widget
        if isinstance(w, tk.Canvas) and not getattr(w, "_log_button", False):
            return
        try:
            if w.cget("state") == "disabled":
                return
        except Exception as exc:
            logger.debug("Failed to read widget state for logging: %s", exc)
        if not bool(self.app.gui_logging_enabled.get()):
            return
        label = ""
        try:
            label = w.cget("text")
        except Exception as exc:
            logger.debug("Failed to read widget text for logging: %s", exc)
        if not label:
            label = w.winfo_name()
        tip = ""
        try:
            tip = getattr(w, "_tooltip_text", "")
        except Exception:
            tip = ""
        gcode = ""
        try:
            getter = getattr(w, "_log_gcode_get", None)
            if callable(getter):
                gcode = getter()
            elif isinstance(getter, str):
                gcode = getter
        except Exception:
            gcode = ""
        ts = time.strftime("%H:%M:%S")
        if tip and gcode:
            self.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self.log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self.log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self.log(f"[{ts}] Button: {label}")

    def _schedule_gcode_mark_flush(self):
        if self._pending_marks_after_id is not None:
            return
        self._pending_marks_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_gcode_marks)

    def _flush_gcode_marks(self):
        self._pending_marks_after_id = None
        sent_idx = self._pending_sent_index
        acked_idx = self._pending_acked_index
        self._pending_sent_index = None
        self._pending_acked_index = None
        if sent_idx is not None:
            self.app.gview.mark_sent_upto(sent_idx)
            self.app._last_sent_index = sent_idx
        if acked_idx is not None:
            self.app.gview.mark_acked_upto(acked_idx)
            self.app._last_acked_index = acked_idx
            if sent_idx is None and acked_idx > self.app._last_sent_index:
                self.app._last_sent_index = acked_idx
        if sent_idx is not None or acked_idx is not None:
            self.app._update_current_highlight()

    def _schedule_progress_flush(self):
        if self._progress_after_id is not None:
            return
        self._progress_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_progress)

    def _flush_progress(self):
        self._progress_after_id = None
        if not self._pending_progress:
            return
        done, total = self._pending_progress
        self._pending_progress = None
        if self.progress_pct:
            self.progress_pct.set(int(round((done / total) * 100)) if total else 0)
        if done and total:
            self.app._update_live_estimate(done, total)
        self.app._maybe_notify_job_completion(done, total)

    def _schedule_buffer_flush(self):
        if self._buffer_after_id is not None:
            return
        self._buffer_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_buffer_fill)

    def _flush_buffer_fill(self):
        self._buffer_after_id = None
        if not self._pending_buffer:
            return
        pct, used, window = self._pending_buffer
        self._pending_buffer = None
        if self.buffer_fill:
            self.buffer_fill.set(f"Buffer: {pct}% ({used}/{window})")
        if self.buffer_fill_pct:
            self.buffer_fill_pct.set(pct)

    def clear_pending_ui_updates(self):
        for attr in (
            "_pending_marks_after_id",
            "_progress_after_id",
            "_buffer_after_id",
            "_console_after_id",
        ):
            val = getattr(self, attr, None)
            if val is None:
                continue
            try:
                self.app.after_cancel(val)
            except Exception:
                pass
            setattr(self, attr, None)
        self._pending_marks_after_id = None
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_buffer = None
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False

    def handle_log_rx(self, raw: str):
        if self._should_suppress_rx_log(raw):
            return
        self.log(f"<< {raw}", self._console_tag_for_line(raw))

    def handle_log_tx(self, message: str):
        self.log(f">> {message}")

    def handle_log(self, message: str):
        self.log(message)

    def handle_buffer_fill(self, pct: int, used: int, window: int):
        self._pending_buffer = (pct, used, window)
        self._schedule_buffer_flush()

    def handle_throughput(self, bps: float):
        if self.throughput_var:
            self.throughput_var.set(self.app._format_throughput(float(bps)))

    def handle_gcode_sent(self, idx: int):
        if self._pending_sent_index is None or idx > self._pending_sent_index:
            self._pending_sent_index = idx
        self._schedule_gcode_mark_flush()

    def handle_gcode_acked(self, idx: int):
        if self._pending_acked_index is None or idx > self._pending_acked_index:
            self._pending_acked_index = idx
        self._schedule_gcode_mark_flush()

    def handle_progress(self, done: int, total: int):
        self._pending_progress = (done, total)
        self._schedule_progress_flush()


class MacroPanel:
    def __init__(self, app):
        self.app = app
        self._left_frame: ttk.Frame | None = None
        self._right_frame: ttk.Frame | None = None
        self._macro_buttons: list[tk.Widget] = []

    def attach_frames(self, left: ttk.Frame, right: ttk.Frame):
        self._left_frame = left
        self._right_frame = right
        self._load_macro_buttons()

    def _macro_path(self, index: int) -> str | None:
        return self.app.macro_executor.macro_path(index)

    def _read_macro_header(self, path: str, index: int) -> tuple[str, str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                name = f.readline().strip()
                tip = f.readline().strip()
            if not name:
                name = f"Macro {index}"
            return name, tip
        except Exception:
            return f"Macro {index}", ""

    def _show_macro_preview(self, name: str, lines: list[str]) -> None:
        body = "".join(lines[2:]) if len(lines) > 2 else ""
        dlg = tk.Toplevel(self.app)
        dlg.title(f"Macro Preview - {name}")
        dlg.transient(self.app)
        dlg.grab_set()
        dlg.resizable(True, True)
        frame = ttk.Frame(dlg, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=name, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 6))
        text = tk.Text(frame, wrap="word", height=14, width=80, state="normal")
        text.insert("end", body)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="left", padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.wait_window()

    def _preview_macro(self, index: int):
        path = self._macro_path(index)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Macro error", str(e))
            return
        name = lines[0].strip() if lines else f"Macro {index}"
        self._show_macro_preview(name, lines)

    def _run_macro(self, index: int):
        self.app.macro_executor.run_macro(index)

    def _load_macro_buttons(self):
        if not self._left_frame or not self._right_frame:
            return
        if self._macro_buttons:
            self.app._manual_controls = [w for w in self.app._manual_controls if w not in self._macro_buttons]
        self._macro_buttons = []
        for w in self._left_frame.winfo_children():
            w.destroy()
        for w in self._right_frame.winfo_children():
            w.destroy()

        for idx in (1, 2, 3):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(self._left_frame, text=name, command=lambda i=idx: self._run_macro(i))
            btn._kb_id = f"macro_{idx}"
            btn.pack(fill="x", pady=(0, 4))
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self.app._manual_controls.append(btn)
            self._macro_buttons.append(btn)

        col = 0
        row = 0
        for idx in (4, 5, 6, 7):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(self._right_frame, text=name, command=lambda i=idx: self._run_macro(i))
            btn._kb_id = f"macro_{idx}"
            btn.grid(row=row, column=col, padx=4, pady=2, sticky="ew")
            self._right_frame.grid_columnconfigure(col, weight=1)
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self.app._manual_controls.append(btn)
            self._macro_buttons.append(btn)
            col += 1
            if col > 1:
                col = 0
                row += 1
        self.app._refresh_keyboard_table()


class ToolpathPanel:
    def __init__(self, app):
        self.app = app
        self.view: Toolpath3D | None = None
        self.tab: ttk.Frame | None = None

    def build_tab(self, notebook: ttk.Notebook):
        tab = ttk.Frame(notebook, padding=6)
        notebook.add(tab, text="3D View")
        self.tab = tab
        self.view = Toolpath3D(
            tab,
            on_save_view=self.app._save_3d_view,
            on_load_view=self.app._load_3d_view,
            perf_callback=self._toolpath_perf_logger,
        )
        self.view.pack(fill="both", expand=True)
        self._configure_view()
        self.app._load_3d_view(show_status=False)

    def _configure_view(self):
        if not self.view:
            return
        self.view.set_display_options(
            rapid=bool(self.app.settings.get("toolpath_show_rapid", False)),
            feed=bool(self.app.settings.get("toolpath_show_feed", True)),
            arc=bool(self.app.settings.get("toolpath_show_arc", False)),
        )
        self.view.set_enabled(bool(self.app.render3d_enabled.get()))
        self.view.set_lightweight_mode(bool(self.app.toolpath_lightweight.get()))
        self.view.set_draw_limits(
            self.app._toolpath_limit_value(self.app.toolpath_full_limit.get(), self.app._toolpath_full_limit_default),
            self.app._toolpath_limit_value(self.app.toolpath_interactive_limit.get(), self.app._toolpath_interactive_limit_default),
        )
        self.view.set_arc_detail_override(math.radians(self.app.toolpath_arc_detail.get()))

    def _toolpath_perf_logger(self, label: str, duration: float):
        if duration < 0.05:
            return
        try:
            self.app.ui_q.put(("log", f"[toolpath] {label} took {duration:.2f}s"))
        except Exception:
            pass

    def set_gcode_lines(self, lines: list[str]):
        if self.view:
            self.view.set_gcode_async(lines)

    def clear(self):
        if self.view:
            self.view.set_gcode_async([])
            self.view.set_job_name("")

    def set_job_name(self, name: str):
        if self.view:
            self.view.set_job_name(name)

    def set_visible(self, visible: bool):
        if self.view:
            self.view.set_visible(visible)

    def set_enabled(self, enabled: bool):
        if self.view:
            self.view.set_enabled(enabled)

    def set_lightweight(self, value: bool):
        if self.view:
            self.view.set_lightweight_mode(value)

    def set_draw_limits(self, full: int, interactive: int):
        if self.view:
            self.view.set_draw_limits(full, interactive)

    def set_arc_detail(self, deg: float):
        if self.view:
            self.view.set_arc_detail_override(math.radians(deg))

    def reparse_lines(self, lines: list[str]):
        if self.view:
            self.view.set_gcode_async(lines)

    def set_position(self, x: float, y: float, z: float):
        if self.view:
            self.view.set_position(x, y, z)

    def get_view_state(self):
        if self.view:
            return self.view.get_view()
        return None

    def apply_view_state(self, state):
        if self.view and state:
            self.view.apply_view(state)

    def get_display_options(self):
        if self.view:
            return self.view.get_display_options()
        return (False, False, False)


class GRBLSettingsController:
    def __init__(self, app):
        self.app = app
        self.settings_tree: ttk.Treeview | None = None
        self.settings_raw_text: tk.Text | None = None
        self.settings_tip: ToolTip | None = None
        self.btn_refresh: ttk.Button | None = None
        self.btn_save: ttk.Button | None = None
        self._settings_capture = False
        self._settings_data: dict[str, tuple[str, int | None]] = {}
        self._settings_values: dict[str, str] = {}
        self._settings_edited: dict[str, str] = {}
        self._settings_edit_entry: ttk.Entry | None = None
        self._settings_baseline: dict[str, str] = {}
        self._settings_items: dict[str, str] = {}
        self._settings_raw_lines: list[str] = []

    def build_tabs(self, notebook: ttk.Notebook):
        rtab = ttk.Frame(notebook, padding=6)
        notebook.add(rtab, text="Raw $$")
        self.settings_raw_text = tk.Text(rtab, wrap="word", height=12, state="disabled")
        rsb = ttk.Scrollbar(rtab, orient="vertical", command=self.settings_raw_text.yview)
        self.settings_raw_text.configure(yscrollcommand=rsb.set)
        self.settings_raw_text.grid(row=0, column=0, sticky="nsew")
        rsb.grid(row=0, column=1, sticky="ns")
        rtab.grid_rowconfigure(0, weight=1)
        rtab.grid_columnconfigure(0, weight=1)

        stab = ttk.Frame(notebook, padding=6)
        notebook.add(stab, text="GRBL Settings")
        sbar = ttk.Frame(stab)
        sbar.pack(fill="x", pady=(0, 6))
        self.btn_refresh = ttk.Button(
            sbar,
            text="Refresh $$",
            command=self.app._request_settings_dump,
        )
        set_kb_id(self.btn_refresh, "grbl_settings_refresh")
        self.btn_refresh.pack(side="left")
        apply_tooltip(self.btn_refresh, "Request $$ settings from GRBL.")
        attach_log_gcode(self.btn_refresh, "$$")
        self.app._manual_controls.append(self.btn_refresh)
        self.btn_save = ttk.Button(
            sbar,
            text="Save Changes",
            command=self.save_changes,
        )
        set_kb_id(self.btn_save, "grbl_settings_save")
        self.btn_save.pack(side="left", padx=(8, 0))
        apply_tooltip(self.btn_save, "Send edited settings to GRBL.")
        self.app._manual_controls.append(self.btn_save)

        self.settings_tree = ttk.Treeview(
            stab,
            columns=("setting", "name", "value", "units", "desc"),
            show="headings",
            height=12,
        )
        self.settings_tree.heading("setting", text="Setting")
        self.settings_tree.heading("name", text="Name")
        self.settings_tree.heading("value", text="Value")
        self.settings_tree.heading("units", text="Units")
        self.settings_tree.heading("desc", text="Description")
        self.settings_tree.column("setting", width=80, anchor="w")
        self.settings_tree.column("name", width=200, anchor="w")
        self.settings_tree.column("value", width=120, anchor="w")
        self.settings_tree.column("units", width=100, anchor="w")
        self.settings_tree.column("desc", width=420, anchor="w")
        self.settings_tree.pack(fill="both", expand=True)
        self.settings_tree.bind("<Double-1>", self._edit_setting_value)
        self.settings_tree.bind("<Motion>", self._settings_tooltip_motion)
        self.settings_tree.bind("<Leave>", self._settings_tooltip_hide)
        self.settings_tip = ToolTip(self.settings_tree, "")
        self.settings_tree.tag_configure("edited", background="#fff5c2")

    def start_capture(self, header: str = "Requesting $$..."):
        self._settings_capture = True
        self._settings_data = {}
        self._settings_edited = {}
        self._settings_raw_lines = []
        self._render_settings_raw(header)

    def handle_line(self, line: str):
        if not self._settings_capture:
            return
        s = line.strip()
        if s.startswith("<") and s.endswith(">"):
            return
        low = s.lower()
        if low != "ok" and not low.startswith("error"):
            self._settings_raw_lines.append(s)
        if s.startswith("$") and "=" in s:
            key, value = s.split("=", 1)
            try:
                idx = int(key[1:])
            except Exception:
                idx = None
            self._settings_data[key] = (value.strip(), idx)
            return
        if low == "ok":
            self._settings_capture = False
            self._render_settings()
            self._update_rapid_rates()
            self._update_accel_rates()
            if self.app._last_gcode_lines:
                self.app._update_gcode_stats(self.app._last_gcode_lines)
            self._render_settings_raw()
        elif low.startswith("error"):
            self._settings_capture = False
            self.app.status.config(text=f"Settings error: {s}")
            self._render_settings_raw()

    def save_changes(self):
        self._commit_pending_setting_edit()
        if not self.app.grbl.is_connected():
            messagebox.showwarning("Not connected", "Connect to GRBL first.")
            return
        if self.app.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before saving settings.")
            return
        if not self._settings_edited:
            messagebox.showinfo("No changes", "No settings have been edited.")
            return
        if not messagebox.askyesno("Confirm save", "Send edited settings to GRBL?"):
            return
        changes: list[tuple[str, str]] = []
        for key, value in self._settings_edited.items():
            val = "" if value is None else str(value).strip()
            if val == "":
                continue
            changes.append((key, val))
        if not changes:
            messagebox.showinfo("No changes", "No non-empty settings to send.")
            return

        def worker():
            sent = 0
            for key, val in changes:
                self.app.grbl.send_immediate(f"{key}={val}")
                sent += 1
                time.sleep(0.05)
            self._settings_edited = {}
            self.app.ui_q.put(("log", f"[settings] Sent {sent} change(s)."))
            try:
                self.app.after(
                    0,
                    lambda sent_count=sent: self._mark_settings_saved(
                        changes, sent_count, refresh=True
                    ),
                )
            except Exception as exc:
                logger.exception("Failed to schedule settings refresh: %s", exc)

        threading.Thread(target=worker, daemon=True).start()

    def _mark_settings_saved(self, changes, sent_count: int, refresh: bool = False):
        if refresh:
            try:
                self.app.status.config(
                    text=f"Settings: sent {sent_count} change(s); refreshing $$ for confirmation"
                )
            except Exception as exc:
                logger.exception("Failed to update settings status: %s", exc)
            try:
                self.app._request_settings_dump()
            except Exception as exc:
                logger.exception("Failed to request settings dump: %s", exc)
            return
        for key, _ in changes:
            if key in self._settings_values:
                self._settings_baseline[key] = self._settings_values[key]
                self._update_setting_row_tags(key)
        try:
            self.app.status.config(text=f"Settings: sent {sent_count} change(s)")
        except Exception as exc:
            logger.exception("Failed to update settings status: %s", exc)

    def _render_settings(self):
        if not self.settings_tree:
            return
        self._settings_items = {}
        for item in self.settings_tree.get_children():
            self.settings_tree.delete(item)
        items: list[tuple[int, str, str, str, str, str]] = []
        self._settings_values = {}
        for key, (value, idx) in self._settings_data.items():
            self._settings_values[key] = value
            info = self.app._grbl_setting_info.get(key, {})
            name = info.get("name", "")
            units = info.get("units", "")
            desc = info.get("desc", "")
            items.append((idx if idx is not None else 9999, key, name, value, units, desc))
        for idx in self.app._grbl_setting_keys:
            key = f"${idx}"
            if key not in self._settings_values:
                self._settings_values[key] = ""
                info = self.app._grbl_setting_info.get(key, {})
                name = info.get("name", "")
                units = info.get("units", "")
                desc = info.get("desc", "")
                items.append((idx, key, name, "", units, desc))
        for _, key, name, value, units, desc in sorted(items):
            item_id = self.settings_tree.insert("", "end", values=(key, name, value, units, desc))
            self._settings_items[key] = item_id
        self._settings_baseline = dict(self._settings_values)
        for key in self._settings_items:
            self._update_setting_row_tags(key)
        self.app.status.config(text=f"Settings: {len(items)} values")

    def _update_rapid_rates(self):
        try:
            rx = float(self._settings_data.get("$110", ("", None))[0])
            ry = float(self._settings_data.get("$111", ("", None))[0])
            rz = float(self._settings_data.get("$112", ("", None))[0])
            if rx > 0 and ry > 0 and rz > 0:
                self.app._rapid_rates = (rx, ry, rz)
                self.app._rapid_rates_source = "grbl"
                return
        except Exception:
            pass
        self.app._rapid_rates = None
        self.app._rapid_rates_source = None

    def _update_accel_rates(self):
        try:
            ax = float(self._settings_data.get("$120", ("", None))[0])
            ay = float(self._settings_data.get("$121", ("", None))[0])
            az = float(self._settings_data.get("$122", ("", None))[0])
            if ax > 0 and ay > 0 and az > 0:
                self.app._accel_rates = (ax, ay, az)
                return
        except Exception:
            pass
        self.app._accel_rates = None

    def _render_settings_raw(self, header: str | None = None):
        if not self.settings_raw_text:
            return
        lines = []
        if header:
            lines.append(header)
        if self._settings_raw_lines:
            lines.extend(self._settings_raw_lines)
        self.settings_raw_text.config(state="normal")
        self.settings_raw_text.delete("1.0", "end")
        self.settings_raw_text.insert("end", "\n".join(lines).strip() + "\n")
        self.settings_raw_text.config(state="disabled")

    def _edit_setting_value(self, event):
        if not self.settings_tree:
            return
        item = self.settings_tree.identify_row(event.y)
        col = self.settings_tree.identify_column(event.x)
        if not item or col != "#3":
            return
        bbox = self.settings_tree.bbox(item, column=col)
        if not bbox:
            return
        x, y, w, h = bbox
        values = self.settings_tree.item(item, "values")
        if not values:
            return
        key = values[0]
        current = values[2]
        self._commit_pending_setting_edit()
        entry = ttk.Entry(self.settings_tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, current)
        entry.focus_set()
        entry._item = item
        entry._key = key
        self._settings_edit_entry = entry

        def commit(_event=None):
            self._commit_pending_setting_edit()

        def cancel(_event=None):
            self._cancel_pending_setting_edit()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _commit_pending_setting_edit(self):
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        key = getattr(entry, "_key", None)
        item = getattr(entry, "_item", None)
        try:
            if key and item:
                new_val = entry.get().strip()
                if new_val:
                    try:
                        idx = int(key[1:]) if key.startswith("$") else None
                    except Exception:
                        idx = None
                    if idx not in GRBL_NON_NUMERIC_SETTINGS:
                        try:
                            val_num = float(new_val)
                        except Exception:
                            messagebox.showwarning("Invalid value", f"Setting {key} must be numeric.")
                            return
                        limits = GRBL_SETTING_LIMITS.get(idx, None)
                        if limits:
                            lo, hi = limits
                            if lo is not None and val_num < lo:
                                messagebox.showwarning("Out of range", f"Setting {key} must be >= {lo}.")
                                return
                            if hi is not None and val_num > hi:
                                messagebox.showwarning("Out of range", f"Setting {key} must be <= {hi}.")
                                return
                self.settings_tree.set(item, "value", new_val)
                self._settings_values[key] = new_val
                baseline = self._settings_baseline.get(key, "")
                if new_val == baseline and key in self._settings_edited:
                    self._settings_edited.pop(key, None)
                else:
                    self._settings_edited[key] = new_val
                self._update_setting_row_tags(key)
        finally:
            try:
                entry.destroy()
            except Exception:
                pass
            self._settings_edit_entry = None

    def _cancel_pending_setting_edit(self):
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        try:
            entry.destroy()
        except Exception:
            pass
        self._settings_edit_entry = None

    def _update_setting_row_tags(self, key: str):
        if not self.settings_tree:
            return
        item = self._settings_items.get(key)
        if not item:
            return
        current = self._settings_values.get(key, "")
        baseline = self._settings_baseline.get(key, "")
        tags = list(self.settings_tree.item(item, "tags"))
        if current != baseline:
            if "edited" not in tags:
                tags.append("edited")
        else:
            tags = [t for t in tags if t != "edited"]
        self.settings_tree.item(item, tags=tuple(tags))

    def _settings_tooltip_motion(self, event):
        if not self.settings_tree or not self.settings_tip:
            return
        item = self.settings_tree.identify_row(event.y)
        if not item:
            self._settings_tooltip_hide()
            return
        values = self.settings_tree.item(item, "values")
        if not values:
            self._settings_tooltip_hide()
            return
        key = values[0]
        try:
            idx = int(key[1:])
        except Exception:
            idx = None
        info = self.app._grbl_setting_info.get(key, {})
        desc = info.get("desc", "")
        units = info.get("units", "")
        tooltip = info.get("tooltip", "")
        baseline_val = self._settings_baseline.get(key, "")
        current_val = self._settings_values.get(key, "")
        limits = None
        try:
            limits = GRBL_SETTING_LIMITS.get(int(key[1:]), None)
        except Exception:
            limits = None
        allow_text = False
        try:
            allow_text = int(key[1:]) in GRBL_NON_NUMERIC_SETTINGS
        except Exception:
            allow_text = False
        value_line = (
            f"Pending: {current_val} (last saved: {baseline_val})"
            if current_val != baseline_val
            else f"Value: {baseline_val}"
        )
        parts = []
        if tooltip:
            parts.append(tooltip)
        elif desc:
            parts.append(desc)
        if units:
            parts.append(f"Units: {units}")
        if allow_text:
            parts.append("Allows text values")
        if limits:
            lo, hi = limits
            if lo is not None and hi is not None:
                parts.append(f"Allowed: {lo} .. {hi}")
            elif lo is not None:
                parts.append(f"Allowed: >= {lo}")
            elif hi is not None:
                parts.append(f"Allowed: <= {hi}")
        parts.append(value_line)
        parts.append("Typical: machine-specific")
        self.settings_tip.set_text("\n".join([p for p in parts if p]))
        self.settings_tip._schedule_show()

    def _settings_tooltip_hide(self, _event=None):
        if self.settings_tip:
            self.settings_tip._hide()

def compute_gcode_stats(
    lines: list[str],
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    if not lines:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    result = parse_gcode_lines(lines)
    if result is None:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    bounds = result.bounds
    total_time_min = 0.0
    has_time = False
    total_rapid_min = 0.0
    has_rapid = False
    last_f = None

    def axis_limits(dx: float, dy: float, dz: float):
        max_feed = None
        min_accel = None
        if rapid_rates:
            candidates = []
            if dx:
                candidates.append(rapid_rates[0])
            if dy:
                candidates.append(rapid_rates[1])
            if dz:
                candidates.append(rapid_rates[2])
            if candidates:
                max_feed = min(candidates)
        if accel_rates:
            candidates = []
            if dx:
                candidates.append(accel_rates[0])
            if dy:
                candidates.append(accel_rates[1])
            if dz:
                candidates.append(accel_rates[2])
            if candidates:
                min_accel = min(candidates)
        return max_feed, min_accel

    def move_duration(dist: float, feed_mm_min: float | None, min_accel: float | None, last_feed: float | None):
        if dist <= 0:
            return 0.0, last_feed
        if feed_mm_min is None or feed_mm_min <= 0:
            return None, last_feed
        f = feed_mm_min / 60.0
        if f <= 0:
            return None, last_feed
        accel = min_accel if (min_accel and min_accel > 0) else 0.0
        if accel <= 0:
            return dist / f, f
        if last_feed is not None and abs(f - last_feed) < 1e-6:
            return dist / f, f
        accel = accel if accel > 0 else 750.0
        half_len = dist / 2.0
        init_time = f / accel
        init_dx = 0.5 * f * init_time
        time_sec = 0.0
        if half_len >= init_dx:
            half_len -= init_dx
            time_sec += init_time
        time_sec += half_len / f
        return 2 * time_sec, f

    for move in result.moves:
        if move.motion == 0 and rapid_rates:
            max_feed, min_accel = axis_limits(move.dx, move.dy, move.dz)
            if max_feed:
                t_sec, last_f = move_duration(move.dist, max_feed, min_accel, last_f)
                if t_sec is not None:
                    total_rapid_min += t_sec / 60.0
                    has_rapid = True
        if move.motion in (1, 2, 3):
            if move.feed and move.feed > 0:
                if move.feed_mode == "G93":
                    total_time_min += 1.0 / move.feed
                else:
                    max_feed, min_accel = axis_limits(move.dx, move.dy, move.dz)
                    use_feed = move.feed
                    if max_feed and use_feed > max_feed:
                        use_feed = max_feed
                    t_sec, last_f = move_duration(move.dist, use_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_time_min += t_sec / 60.0
                has_time = True
    return {
        "bounds": bounds,
        "time_min": total_time_min if has_time else None,
        "rapid_min": total_rapid_min if has_rapid else None,
    }



class Toolpath3D(ttk.Frame):
    def __init__(
        self,
        parent,
        on_save_view=None,
        on_load_view=None,
        perf_callback: Callable[[str, float], None] | None = None,
    ):
        super().__init__(parent)
        bg = "SystemButtonFace"
        try:
            bg = parent.cget("background")
        except Exception:
            pass
        self.show_rapid = tk.BooleanVar(value=False)
        self.show_feed = tk.BooleanVar(value=True)
        self.show_arc = tk.BooleanVar(value=False)

        self.on_save_view = on_save_view
        self.on_load_view = on_load_view

        legend = ttk.Frame(self)
        legend.pack(side="top", fill="x")
        self._legend_label(legend, "#8a8a8a", "Rapid", self.show_rapid)
        self._legend_label(legend, "#2c6dd2", "Feed", self.show_feed)
        self._legend_label(legend, "#2aa876", "Arc", self.show_arc)
        self.btn_reset_view = ttk.Button(legend, text="Reset View", command=self._reset_view)
        set_kb_id(self.btn_reset_view, "view_reset")
        self.btn_reset_view.pack(side="right", padx=(6, 0))
        self.btn_load_view = ttk.Button(legend, text="Load View", command=self._load_view)
        set_kb_id(self.btn_load_view, "view_load")
        self.btn_load_view.pack(side="right", padx=(6, 0))
        self.btn_save_view = ttk.Button(legend, text="Save View", command=self._save_view)
        set_kb_id(self.btn_save_view, "view_save")
        self.btn_save_view.pack(side="right", padx=(6, 0))
        apply_tooltip(self.btn_save_view, "Save the current 3D view.")
        apply_tooltip(self.btn_load_view, "Load the saved 3D view.")
        apply_tooltip(self.btn_reset_view, "Reset the 3D view.")

        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Shift-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Shift-B1-Motion>", self._on_pan)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds = None
        self.position = None
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
        self._drag_start = None
        self._pan_start = None
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.enabled = True
        self._pending_lines = None
        self._parse_token = 0
        self._render_pending = False
        self._render_interval = 0.1
        self._last_render_ts = 0.0
        self._visible = True
        self._colors = {
            "rapid": "#8a8a8a",
            "feed": "#2c6dd2",
            "arc": "#2aa876",
        }
        self._preview_target = 1000
        self._full_parse_limit = 20000
        self._arc_step_default = math.pi / 18
        self._arc_step_fast = math.pi / 12
        self._arc_step_large = math.pi / 8
        self._arc_step_rad = self._arc_step_default
        self._arc_step_override_rad = None
        self._max_draw_segments = 40000
        self._interactive_max_draw_segments = 5000
        self._fast_mode = False
        self._fast_mode_after_id = None
        self._fast_mode_duration = 0.3
        self._render_params = None
        self._position_item = None
        self._last_lines_hash = None
        self._last_segments = None
        self._last_bounds = None
        self._lightweight_mode = False
        self._lightweight_preview_target = 400
        self._job_name = ""
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None
        self._perf_callback = perf_callback
        self._perf_threshold = 0.05

    def _legend_label(self, parent, color, text, var):
        swatch = tk.Label(parent, width=2, background=color)
        swatch.pack(side="left", padx=(0, 4), pady=(2, 2))
        chk = ttk.Checkbutton(parent, text=text, variable=var, command=self._schedule_render)
        chk.pack(side="left", padx=(0, 10))

    def _report_perf(self, label: str, duration: float):
        if not self._perf_callback:
            return
        if duration < self._perf_threshold:
            return
        try:
            self._perf_callback(label, duration)
        except Exception:
            pass

    def _invalidate_render_cache(self):
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None

    def _build_projection_cache(self, filters: tuple[bool, bool, bool], max_draw: int | None):
        start = time.perf_counter()
        try:
            segments = self.segments
            total_segments = len(segments)
            draw_segments = segments
            if max_draw and total_segments > max_draw:
                step = max(2, total_segments // max_draw)
                draw_segments = segments[::step]
            proj: list[tuple[float, float, float, float, str]] = []
            minx = miny = float("inf")
            maxx = maxy = float("-inf")
            drawn = 0
            for x1, y1, z1, x2, y2, z2, color in draw_segments:
                if color == "rapid" and not filters[0]:
                    continue
                if color == "feed" and not filters[1]:
                    continue
                if color == "arc" and not filters[2]:
                    continue
                px1, py1 = self._project(x1, y1, z1)
                px2, py2 = self._project(x2, y2, z2)
                minx = min(minx, px1, px2)
                miny = min(miny, py1, py2)
                maxx = max(maxx, px1, px2)
                maxy = max(maxy, py1, py2)
                proj.append((px1, py1, px2, py2, color))
                drawn += 1
            bounds = None
            if proj and (minx < float("inf")):
                bounds = (minx, maxx, miny, maxy)
            return proj, bounds, drawn, total_segments
        finally:
            self._report_perf("build_projection", time.perf_counter() - start)

    def set_display_options(
        self,
        rapid: bool | None = None,
        feed: bool | None = None,
        arc: bool | None = None,
    ):
        changed = False
        if rapid is not None:
            self.show_rapid.set(bool(rapid))
            changed = True
        if feed is not None:
            self.show_feed.set(bool(feed))
            changed = True
        if arc is not None:
            self.show_arc.set(bool(arc))
            changed = True
        if changed:
            self._schedule_render()
            self._invalidate_render_cache()

    def get_display_options(self) -> tuple[bool, bool, bool]:
        return (
            bool(self.show_rapid.get()),
            bool(self.show_feed.get()),
            bool(self.show_arc.get()),
        )

    def set_gcode(self, lines: list[str]):
        segs, bnds = self._parse_gcode(lines)
        if segs is not None:
            self.segments, self.bounds = segs, bnds
            self._invalidate_render_cache()
        self._schedule_render()

    def set_gcode_async(self, lines: list[str]):
        self._parse_token += 1
        token = self._parse_token
        lines_hash = _hash_lines(lines)
        if lines_hash and (lines_hash == self._last_lines_hash) and self._last_segments is not None:
            self.segments = self._last_segments
            self.bounds = self._last_bounds
            self._schedule_render()
            return
        line_count = len(lines)
        if line_count > self._full_parse_limit:
            base_step = self._arc_step_large
        elif line_count > 5000:
            base_step = self._arc_step_fast
        else:
            base_step = self._arc_step_default
        if self._arc_step_override_rad is not None:
            self._arc_step_rad = self._arc_step_override_rad
        else:
            self._arc_step_rad = base_step
        if not self.enabled:
            self._pending_lines = lines
            return
        self._pending_lines = None
        if not lines:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        preview_target = self._lightweight_preview_target if self._lightweight_mode else self._preview_target
        quick_lines = lines
        if len(lines) > preview_target:
            step = max(2, len(lines) // preview_target)
            quick_lines = lines[::step]
        res = self._parse_gcode(quick_lines, token)
        if res[0] is None:
            return
        self.segments, self.bounds = res
        if quick_lines is lines:
            self._cache_parse_results(lines_hash, self.segments, self.bounds)
        self._schedule_render()
        if len(lines) > self._full_parse_limit:
            return
        def worker():
            segs, bnds = self._parse_gcode(lines, token)
            if segs is None:
                return
            if not self.winfo_exists():
                return
            root = self.winfo_toplevel()
            if getattr(root, "_closing", False):
                return
            self.after(0, lambda: self._apply_full_parse(token, segs, bnds, lines_hash))

        threading.Thread(target=worker, daemon=True).start()

    def _cache_parse_results(self, lines_hash: str | None, segments, bounds):
        if not lines_hash:
            return
        self._last_lines_hash = lines_hash
        self._last_segments = segments
        self._last_bounds = bounds

    def set_lightweight_mode(self, lightweight: bool):
        new_mode = bool(lightweight)
        if self._lightweight_mode == new_mode:
            return
        self._lightweight_mode = new_mode
        self._schedule_render()

    def set_job_name(self, name: str | None):
        self._job_name = str(name) if name else ""
        self._schedule_render()


    def _apply_full_parse(self, token, segments, bounds, parse_hash: str | None = None):
        if not self.winfo_exists():
            return
        root = self.winfo_toplevel()
        if getattr(root, "_closing", False):
            return
        if token != self._parse_token:
            return
        if not self.enabled:
            self._pending_lines = None
            return
        self._cache_parse_results(parse_hash, segments, bounds)
        self.segments = segments
        self.bounds = bounds
        self._invalidate_render_cache()
        self._schedule_render()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        if self._pending_lines is not None:
            pending = self._pending_lines
            self._pending_lines = None
            self.set_gcode_async(pending)

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._visible:
            self._schedule_render()

    def set_position(self, x: float, y: float, z: float):
        self.position = (x, y, z)
        if self._visible and self.enabled:
            if not self.segments:
                return
            if self._render_params and not self._render_pending:
                self._update_position_marker()
            else:
                self._schedule_render()

    def _update_position_marker(self):
        if not self._render_params:
            return
        if not self.position:
            if self._position_item is not None:
                try:
                    self.canvas.delete(self._position_item)
                except Exception:
                    pass
                self._position_item = None
            return
        params = self._render_params
        px, py = self._project(*self.position)
        cx = (px - params["minx"]) * params["scale"] + params["margin"]
        cy = (py - params["miny"]) * params["scale"] + params["margin"]
        cx = cx + params["pan_x"]
        cy = params["height"] - cy + params["pan_y"]
        r = 4
        if self._position_item is None:
            self._position_item = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#d64545", outline=""
            )
        else:
            self.canvas.coords(self._position_item, cx - r, cy - r, cx + r, cy + r)

    def _on_resize(self, _event=None):
        self._schedule_render()

    def _on_drag_start(self, event):
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self._drag_start = (event.x, event.y)
        self.azimuth += dx * 0.01
        self.elevation += dy * 0.01
        limit = math.pi / 2 - 0.1
        self.elevation = max(-limit, min(limit, self.elevation))
        self._schedule_render()
        self._enter_fast_mode()

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y)

    def _on_pan(self, event):
        if not self._pan_start:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self._pan_start = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self._schedule_render()
        self._enter_fast_mode()

    def _on_mousewheel(self, event):
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        else:
            direction = 1 if event.num == 4 else -1
        if direction > 0:
            self.zoom *= 1.1
        else:
            self.zoom /= 1.1
        self.zoom = max(0.2, min(5.0, self.zoom))
        self._schedule_render()
        self._enter_fast_mode()

    def _reset_view(self):
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._schedule_render()

    def _save_view(self):
        if callable(self.on_save_view):
            self.on_save_view()

    def _load_view(self):
        if callable(self.on_load_view):
            self.on_load_view()

    def get_view(self) -> dict:
        return {
            "azimuth": self.azimuth,
            "elevation": self.elevation,
            "zoom": self.zoom,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

    def apply_view(self, view: dict):
        if not view:
            return
        try:
            self.azimuth = float(view.get("azimuth", self.azimuth))
            self.elevation = float(view.get("elevation", self.elevation))
            self.zoom = float(view.get("zoom", self.zoom))
            self.pan_x = float(view.get("pan_x", self.pan_x))
            self.pan_y = float(view.get("pan_y", self.pan_y))
        except Exception as exc:
            logger.exception("Failed to apply 3D view state: %s", exc)
            return
        self._schedule_render()

    def _schedule_render(self):
        if not self._visible:
            return
        if self._render_pending:
            return
        self._render_pending = True
        now = time.time()
        delay = max(0.0, self._render_interval - (now - self._last_render_ts))
        self.after(int(delay * 1000), self._render)

    def set_draw_limits(self, full_limit: int | None = None, interactive_limit: int | None = None):
        if full_limit is not None:
            if full_limit <= 0:
                self._max_draw_segments = None
            else:
                self._max_draw_segments = int(full_limit)
        if interactive_limit is not None:
            if interactive_limit <= 0:
                self._interactive_max_draw_segments = None
            else:
                self._interactive_max_draw_segments = int(interactive_limit)
        self._invalidate_render_cache()
        self._schedule_render()

    def set_arc_detail_override(self, step_rad: float | None):
        if step_rad is None or step_rad <= 0:
            self._arc_step_override_rad = None
        else:
            self._arc_step_override_rad = float(step_rad)
        self._schedule_render()

    def _enter_fast_mode(self):
        self._fast_mode = True
        if self._fast_mode_after_id is not None:
            try:
                self.after_cancel(self._fast_mode_after_id)
            except Exception:
                pass
        self._fast_mode_after_id = self.after(int(self._fast_mode_duration * 1000), self._exit_fast_mode)

    def _exit_fast_mode(self):
        self._fast_mode_after_id = None
        if not self._fast_mode:
            return
        self._fast_mode = False
        self._schedule_render()

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ca = math.cos(self.azimuth)
        sa = math.sin(self.azimuth)
        ce = math.cos(self.elevation)
        se = math.sin(self.elevation)
        x1 = x * ca - y * sa
        y1 = x * sa + y * ca
        y2 = y1 * ce - z * se
        return x1, y2

    def _segments_bounds(self, segments):
        if not segments:
            return None
        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")
        for x1, y1, z1, x2, y2, z2, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            minz = min(minz, z1, z2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
            maxz = max(maxz, z1, z2)
        return minx, maxx, miny, maxy, minz, maxz

    def _parse_gcode(self, lines: list[str], token: int | None = None):
        start = time.perf_counter()
        try:
            def keep_running() -> bool:
                return token is None or token == self._parse_token

            result = parse_gcode_lines(lines, self._arc_step_rad, keep_running=keep_running)
            if result is None:
                return None, None
            return result.segments, result.bounds
        finally:
            self._report_perf("parse_gcode", time.perf_counter() - start)

    def _render(self):
        self._render_pending = False
        if not self._visible:
            return
        self._last_render_ts = time.time()
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
        if not self.enabled:
            job_txt = f" (Job: {self._job_name})" if self._job_name else ""
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text=f"3D render disabled{job_txt}",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text="Enable 3D render for the full preview.",
                    fill="#666666",
                )
            return
        if not self.segments:
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="No G-code loaded",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text=f"Last job: {self._job_name}",
                    fill="#666666",
                )
            return

        total_segments = len(self.segments)
        segments = self.segments
        max_draw = self._max_draw_segments
        if self._fast_mode and self._interactive_max_draw_segments:
            if max_draw:
                max_draw = min(max_draw, self._interactive_max_draw_segments)
            else:
                max_draw = self._interactive_max_draw_segments
        if max_draw and total_segments > max_draw:
            step = max(2, total_segments // max_draw)
            segments = segments[::step]
        proj = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for x1, y1, z1, x2, y2, z2, color in segments:
            if color == "rapid" and not self.show_rapid.get():
                continue
            if color == "feed" and not self.show_feed.get():
                continue
            if color == "arc" and not self.show_arc.get():
                continue
            px1, py1 = self._project(x1, y1, z1)
            px2, py2 = self._project(x2, y2, z2)
            minx = min(minx, px1, px2)
            miny = min(miny, py1, py2)
            maxx = max(maxx, px1, px2)
            maxy = max(maxy, py1, py2)
            proj.append((px1, py1, px2, py2, color))

        if not proj:
            self.canvas.create_text(w / 2, h / 2, text="No toolpath selected", fill="#666666")
            return

        if maxx - minx == 0 or maxy - miny == 0:
            return
        margin = 20
        sx = (w - 2 * margin) / (maxx - minx)
        sy = (h - 2 * margin) / (maxy - miny)
        scale = min(sx, sy) * self.zoom

        def to_canvas(px, py):
            cx = (px - minx) * scale + margin
            cy = (py - miny) * scale + margin
            return cx + self.pan_x, h - cy + self.pan_y

        self._render_params = {
            "minx": minx,
            "miny": miny,
            "scale": scale,
            "margin": margin,
            "height": h,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

        runs: dict[str, list[list[float]]] = {}
        cur_color = None
        cur_pts: list[float] = []
        last_end = None

        def flush_run():
            nonlocal cur_color, cur_pts, last_end
            if cur_color and len(cur_pts) >= 4:
                runs.setdefault(cur_color, []).append(cur_pts)
            cur_color = None
            cur_pts = []
            last_end = None

        eps = 1e-6
        for px1, py1, px2, py2, color in proj:
            x1, y1 = to_canvas(px1, py1)
            x2, y2 = to_canvas(px2, py2)
            continuous = (
                cur_color == color
                and last_end is not None
                and abs(x1 - last_end[0]) <= eps
                and abs(y1 - last_end[1]) <= eps
            )
            if not continuous:
                flush_run()
                cur_color = color
                cur_pts = [x1, y1, x2, y2]
            else:
                cur_pts.extend([x2, y2])
            last_end = (x2, y2)
        flush_run()

        for color, polylines in runs.items():
            color_hex = self._colors.get(color, "#2c6dd2")
            for pts in polylines:
                self.canvas.create_line(*pts, fill=color_hex)

        x0, y0 = to_canvas(minx, miny)
        x1, y1 = to_canvas(maxx, maxy)
        x_low, x_high = min(x0, x1), max(x0, x1)
        y_low, y_high = min(y0, y1), max(y0, y1)
        self.canvas.create_rectangle(
            x_low,
            y_low,
            x_high,
            y_high,
            outline="#ffffff",
            width=1,
        )

        origin = self._project(0.0, 0.0, 0.0)
        ox, oy = to_canvas(*origin)
        cross = 6
        self.canvas.create_line(ox - cross, oy, ox + cross, oy, fill="#ffffff")
        self.canvas.create_line(ox, oy - cross, ox, oy + cross, fill="#ffffff")

        drawn = len(proj)
        filters = []
        if self.show_rapid.get():
            filters.append("Rapid")
        if self.show_feed.get():
            filters.append("Feed")
        if self.show_arc.get():
            filters.append("Arc")
        filters_text = ", ".join(filters) if filters else "None"
        az_deg = math.degrees(self.azimuth)
        el_deg = math.degrees(self.elevation)
        mode_text = "Fast preview" if self._fast_mode else "Full quality"
        overlay = "\n".join(
            [
                f"Segments: {drawn:,}/{total_segments:,}",
                f"View: Az {az_deg:.0f}° El {el_deg:.0f}° Zoom {self.zoom:.2f}x",
                f"Filters: {filters_text}",
                f"Mode: {mode_text}",
            ]
        )
        self.canvas.create_text(
            margin + 6,
            margin + 6,
            text=overlay,
            fill="#ffffff",
            anchor="nw",
            justify="left",
        )

        self._update_position_marker()

class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = 1000):
        self.widget = widget
        self.text = text
        self._tip = None
        self.delay_ms = delay_ms
        self._after_id = None
        widget.bind("<Enter>", self._schedule_show)
        widget.bind("<Leave>", self._hide)

    def _schedule_show(self, _event=None):
        # Always reset any existing tooltip so movement can update content/position.
        self._hide()
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        try:
            if not self.widget.winfo_exists():
                return
        except tk.TclError:
            return
        top = self.widget.winfo_toplevel()
        enabled = True
        try:
            enabled = bool(top.tooltip_enabled.get())
        except Exception:
            pass
        if not enabled:
            return
        if not self.text or self._tip is not None:
            return
        # Position near the current pointer location for consistent placement with other tooltips.
        try:
            x = self.widget.winfo_pointerx() + 16
            y = self.widget.winfo_pointery() + 12
        except Exception:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        try:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            label = ttk.Label(
                self._tip,
                text=self.text,
                background="#ffffe0",
                relief="solid",
                padding=(6, 3),
            )
            label.pack()
        except tk.TclError:
            self._tip = None

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def set_text(self, text: str):
        self.text = text


def _resolve_widget_bg(widget):
    if widget:
        try:
            bg = widget.cget("background")
        except Exception:
            bg = ""
        if bg:
            return bg
    style = ttk.Style()
    for target in (
        "TFrame",
        "TLabelframe",
        "TButton",
        "TLabel",
        "Entry",
        "TEntry",
        "TCombobox",
        "TLabelframe.Label",
    ):
        cfg = style.configure(target)
        if isinstance(cfg, dict):
            bg = cfg.get("background") or cfg.get("fieldbackground")
            if bg:
                return bg
        else:
            try:
                lookup = style.lookup(target, "background")
            except tk.TclError:
                lookup = ""
            if lookup:
                return lookup
    if widget:
        try:
            root = widget.winfo_toplevel()
            bg = root.cget("background")
            if bg:
                return bg
        except Exception:
            pass
    return "#f0f0f0"


class StopSignButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        fill: str,
        text_color: str,
        command=None,
        size: int = 60,
        outline: str = "#2f2f2f",
        **kwargs,
    ):
        bg = kwargs.pop("bg", None)
        if bg is None:
            bg = kwargs.pop("background", None)
        if not bg:
            bg = _resolve_widget_bg(master)
        super().__init__(
            master,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=bg,
            **kwargs,
        )
        self._default_bg = bg
        self._text = text
        self._fill = fill
        self._text_color = text_color
        self._outline = outline
        self._command = command
        self._size = size
        self._state = "normal"
        self._poly = None
        self._text_id = None
        self._disabled_fill = self._blend_color(fill, "#f0f0f0", 0.55)
        self._disabled_text = self._blend_color(text_color, "#808080", 0.55)
        self._draw_octagon()
        self._apply_state()
        self._log_button = True
        self.bind("<Button-1>", self._on_click, add="+")

    def _blend_color(self, base: str, target: str, factor: float) -> str:
        base = base.lstrip("#")
        target = target.lstrip("#")
        if len(base) != 6 or len(target) != 6:
            return base if base.startswith("#") else f"#{base}"
        br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
        tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
        r = int(br + (tr - br) * factor)
        g = int(bg + (tg - bg) * factor)
        b = int(bb + (tb - bb) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def refresh_background(self):
        bg = _resolve_widget_bg(self.master)
        self._default_bg = bg
        try:
            self.config(bg=bg)
        except Exception:
            pass

    def _draw_octagon(self):
        size = self._size
        pad = 2
        s = size - pad * 2
        cut = s * 0.2929
        x0, y0 = pad, pad
        x1, y1 = pad + s, pad + s
        points = [
            x0 + cut, y0,
            x1 - cut, y0,
            x1, y0 + cut,
            x1, y1 - cut,
            x1 - cut, y1,
            x0 + cut, y1,
            x0, y1 - cut,
            x0, y0 + cut,
        ]
        self._poly = self.create_polygon(points, fill=self._fill, outline=self._outline, width=1)
        self._text_id = self.create_text(
            size / 2,
            size / 2,
            text=self._text,
            fill=self._text_color,
            justify="center",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _apply_state(self):
        is_disabled = self._state == "disabled"
        fill = self._disabled_fill if is_disabled else self._fill
        text_color = self._disabled_text if is_disabled else self._text_color
        if self._poly is not None:
            self.itemconfig(self._poly, fill=fill)
        if self._text_id is not None:
            self.itemconfig(self._text_id, fill=text_color)
        self.config(cursor="arrow" if is_disabled else "hand2")

    def _on_click(self, event=None):
        if self._state == "disabled":
            return
        if callable(self._command):
            self._command()

    def configure(self, **kwargs):
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            if self._text_id is not None:
                self.itemconfig(self._text_id, text=self._text)
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            self._apply_state()
        return super().configure(**kwargs)

    def config(self, **kwargs):
        return self.configure(**kwargs)

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self):
        self._on_click()


def apply_tooltip(widget, text: str):
    if not text:
        return
    try:
        widget._tooltip_text = text
    except Exception:
        pass
    ToolTip(widget, text)


def attach_log_gcode(widget, gcode_or_func):
    try:
        widget._log_gcode_get = gcode_or_func
    except Exception:
        pass


def set_kb_id(widget, kb_id: str):
    try:
        widget._kb_id = kb_id
    except Exception:
        pass
    return widget


class App(tk.Tk):
    HIDDEN_MPOS_BUTTON_STYLE = "SimpleSender.HiddenMpos.TButton"
    def __init__(self):
        super().__init__()
        self.title("Simple Streamer")
        self.minsize(980, 620)
        self.settings_path = _resolve_settings_path()
        self.settings_dir = os.path.dirname(self.settings_path)
        self._settings_store = Settings(self.settings_path)
        self.settings = self._load_settings()
        # Migrate jog feed defaults: keep legacy jog_feed for XY, force Z to its own default when absent.
        legacy_jog_feed = self.settings.get("jog_feed")
        has_jog_xy = "jog_feed_xy" in self.settings
        has_jog_z = "jog_feed_z" in self.settings
        default_jog_feed_xy = (
            self.settings["jog_feed_xy"]
            if has_jog_xy
            else (legacy_jog_feed if legacy_jog_feed is not None else 4000.0)
        )
        default_jog_feed_z = self.settings["jog_feed_z"] if has_jog_z else 500.0
        if has_jog_z and (legacy_jog_feed is not None) and (self.settings["jog_feed_z"] == legacy_jog_feed) and (not has_jog_xy):
            # Likely legacy single value carried over; reset to Z default.
            default_jog_feed_z = 500.0
            self.settings["jog_feed_z"] = default_jog_feed_z

        self.tooltip_enabled = tk.BooleanVar(value=self.settings.get("tooltips_enabled", True))
        self.gui_logging_enabled = tk.BooleanVar(value=self.settings.get("gui_logging_enabled", True))
        self.error_dialogs_enabled = tk.BooleanVar(value=self.settings.get("error_dialogs_enabled", True))
        self.macros_allow_python = tk.BooleanVar(value=self.settings.get("macros_allow_python", True))
        self.performance_mode = tk.BooleanVar(value=self.settings.get("performance_mode", False))
        self.render3d_enabled = tk.BooleanVar(value=self.settings.get("render3d_enabled", True))
        self.all_stop_mode = tk.StringVar(value=self.settings.get("all_stop_mode", "stop_reset"))
        self.training_wheels = tk.BooleanVar(value=self.settings.get("training_wheels", True))
        self.reconnect_on_open = tk.BooleanVar(value=self.settings.get("reconnect_on_open", True))
        self.keyboard_bindings_enabled = tk.BooleanVar(
            value=self.settings.get("keyboard_bindings_enabled", True)
        )
        self.job_completion_popup = tk.BooleanVar(value=self.settings.get("job_completion_popup", True))
        self.job_completion_beep = tk.BooleanVar(value=self.settings.get("job_completion_beep", False))
        pos_enabled = bool(self.settings.get("console_positions_enabled", True))
        status_enabled = bool(self.settings.get("console_status_enabled", True))
        combined_console_enabled = pos_enabled or status_enabled
        self.console_positions_enabled = tk.BooleanVar(value=combined_console_enabled)
        self.console_status_enabled = tk.BooleanVar(value=combined_console_enabled)
        self.style = ttk.Style()
        self.available_themes = list(self.style.theme_names())
        theme_choice = self.settings.get("theme", self.style.theme_use())
        self.selected_theme = tk.StringVar(value=theme_choice)
        self._apply_theme(theme_choice)
        self.version_var = tk.StringVar(value=f"Simple Sender  -  Version: v{APP_VERSION}")
        self.show_resume_from_button = tk.BooleanVar(value=self.settings.get("show_resume_from_button", True))
        self.show_recover_button = tk.BooleanVar(value=self.settings.get("show_recover_button", True))
        self.current_line_mode = tk.StringVar(
            value=self.settings.get("current_line_mode", "acked")
        )
        self._toolpath_full_limit_default = 40000
        self._toolpath_interactive_limit_default = 5000
        self._toolpath_arc_detail_min = 2.0
        self._toolpath_arc_detail_max = 45.0
        self._toolpath_arc_detail_default = math.degrees(math.pi / 18)
        try:
            saved_full = int(self.settings.get("toolpath_full_limit", self._toolpath_full_limit_default))
        except Exception:
            saved_full = self._toolpath_full_limit_default
        try:
            saved_interactive = int(
                self.settings.get("toolpath_interactive_limit", self._toolpath_interactive_limit_default)
            )
        except Exception:
            saved_interactive = self._toolpath_interactive_limit_default
        try:
            saved_arc = float(self.settings.get("toolpath_arc_detail_deg", self._toolpath_arc_detail_default))
        except Exception:
            saved_arc = self._toolpath_arc_detail_default
        saved_arc = max(self._toolpath_arc_detail_min, min(saved_arc, self._toolpath_arc_detail_max))
        self.toolpath_full_limit = tk.StringVar(value=str(saved_full))
        self.toolpath_interactive_limit = tk.StringVar(value=str(saved_interactive))
        self.toolpath_arc_detail = tk.DoubleVar(value=saved_arc)
        self.toolpath_lightweight = tk.BooleanVar(value=self.settings.get("toolpath_lightweight", False))
        self._toolpath_arc_detail_value = tk.StringVar(value=f"{saved_arc:.1f}°")
        self._toolpath_arc_detail_reparse_after_id = None
        self._toolpath_arc_detail_reparse_delay = 300
        raw_bindings = self.settings.get("key_bindings", {})
        if isinstance(raw_bindings, dict):
            self._key_bindings = {}
            for k, v in raw_bindings.items():
                self._key_bindings[str(k)] = self._normalize_key_label(str(v))
        else:
            self._key_bindings = {}
        self._machine_profiles = self._load_machine_profiles()
        active_profile = self.settings.get("active_profile", "")
        if active_profile and not self._get_profile_by_name(active_profile):
            active_profile = ""
        if not active_profile and self._machine_profiles:
            active_profile = self._machine_profiles[0]["name"]
        self.active_profile_name = tk.StringVar(value=active_profile)
        self.profile_name_var = tk.StringVar()
        self.profile_units_var = tk.StringVar(value="mm")
        self.profile_rate_x_var = tk.StringVar()
        self.profile_rate_y_var = tk.StringVar()
        self.profile_rate_z_var = tk.StringVar()
        self._apply_profile_to_vars(self._get_profile_by_name(active_profile))
        self._bound_key_sequences = set()
        self._key_sequence_map = {}
        self._kb_conflicts = set()
        self._key_sequence_buffer = []
        self._key_sequence_last_time = 0.0
        self._key_sequence_timeout = 0.8
        self._key_sequence_after_id = None
        self._kb_item_to_button = {}
        self._kb_edit = None
        self._closing = False
        self._connecting = False
        self._disconnecting = False
        self._connect_thread: threading.Thread | None = None
        self._disconnect_thread: threading.Thread | None = None
        self._error_dialog_last_ts = 0.0
        self._error_dialog_window_start = 0.0
        self._error_dialog_count = 0
        self._error_dialog_suppressed = False
        try:
            interval = float(self.settings.get("error_dialog_interval", 2.0))
        except Exception:
            interval = 2.0
        try:
            burst_window = float(self.settings.get("error_dialog_burst_window", 30.0))
        except Exception:
            burst_window = 30.0
        try:
            burst_limit = int(self.settings.get("error_dialog_burst_limit", 3))
        except Exception:
            burst_limit = 3
        if interval <= 0:
            interval = 2.0
        if burst_window <= 0:
            burst_window = 30.0
        if burst_limit <= 0:
            burst_limit = 3
        self._error_dialog_interval = interval
        self._error_dialog_burst_window = burst_window
        self._error_dialog_burst_limit = burst_limit
        self.error_dialog_interval_var = tk.DoubleVar(value=self._error_dialog_interval)
        self.error_dialog_burst_window_var = tk.DoubleVar(value=self._error_dialog_burst_window)
        self.error_dialog_burst_limit_var = tk.IntVar(value=self._error_dialog_burst_limit)
        self.error_dialog_status_var = tk.StringVar(value="")
        self.ui_q: queue.Queue = queue.Queue()
        self.status_poll_interval = tk.DoubleVar(
            value=self.settings.get("status_poll_interval", STATUS_POLL_DEFAULT)
        )
        try:
            failure_limit = int(self.settings.get("status_query_failure_limit", 3))
        except Exception:
            failure_limit = 3
        if failure_limit < 1:
            failure_limit = 1
        if failure_limit > 10:
            failure_limit = 10
        self.status_query_failure_limit = tk.IntVar(value=failure_limit)
        self.grbl = GrblWorker(self.ui_q)
        self.grbl.set_status_query_failure_limit(self.status_query_failure_limit.get())
        self.macro_executor = MacroExecutor(self)
        self.streaming_controller = StreamingController(self)
        self.macro_panel = MacroPanel(self)
        self.toolpath_panel = ToolpathPanel(self)
        self.settings_controller = GRBLSettingsController(self)
        self._install_dialog_loggers()
        self.report_callback_exception = self._tk_report_callback_exception
        self._apply_status_poll_profile()

        self.unit_mode = tk.StringVar(value=self.settings.get("unit_mode", "mm"))
        self.step_xy = tk.DoubleVar(value=self.settings.get("step_xy", 1.0))
        self.step_z = tk.DoubleVar(value=self.settings.get("step_z", 1.0))
        self.jog_feed_xy = tk.DoubleVar(value=default_jog_feed_xy)
        self.jog_feed_z = tk.DoubleVar(value=default_jog_feed_z)

        self.connected = False
        self.current_port = tk.StringVar(value=self.settings.get("last_port", ""))

        # State
        self.machine_state = tk.StringVar(value="DISCONNECTED")
        self.wpos_x = tk.StringVar(value="0.000")
        self.wpos_y = tk.StringVar(value="0.000")
        self.wpos_z = tk.StringVar(value="0.000")
        self.mpos_x = tk.StringVar(value="0.000")
        self.mpos_y = tk.StringVar(value="0.000")
        self.mpos_z = tk.StringVar(value="0.000")
        self._last_gcode_lines = []
        self._gcode_hash = None
        self._gcode_loading = False
        self._gcode_load_token = 0
        self.gcode_stats_var = tk.StringVar(value="No file loaded")
        self.gcode_load_var = tk.StringVar(value="")
        self._rapid_rates = None
        self._rapid_rates_source = None
        self.fallback_rapid_rate = tk.StringVar(value=self.settings.get("fallback_rapid_rate", ""))
        self.estimate_factor = tk.DoubleVar(value=self.settings.get("estimate_factor", 1.0))
        self._estimate_factor_label = tk.StringVar(value=f"{self.estimate_factor.get():.2f}x")
        self._accel_rates = None
        self._stats_token = 0
        self._last_stats = None
        self._last_rate_source = None
        self._stats_cache: dict = {}
        self._live_estimate_min = None
        self._stream_state = None
        self._stream_start_ts = None
        self._stream_pause_total = 0.0
        self._stream_paused_at = None
        self._job_started_at: datetime | None = None
        self._job_completion_notified = False
        self._grbl_ready = False
        self._alarm_locked = False
        self._alarm_message = ""
        self._pending_settings_refresh = False
        self._connected_port = None
        self._status_seen = False
        self.progress_pct = tk.IntVar(value=0)
        self.buffer_fill = tk.StringVar(value="Buffer: 0%")
        self.throughput_var = tk.StringVar(value="TX: 0 B/s")
        self.buffer_fill_pct = tk.IntVar(value=0)
        self._manual_controls = []
        self._override_controls = []
        self._xy_step_buttons = []
        self._z_step_buttons = []
        self.feed_override_scale = None
        self.spindle_override_scale = None
        self.feed_override_display = tk.StringVar(value="100%")
        self.spindle_override_display = tk.StringVar(value="100%")
        self.override_info_var = tk.StringVar(value="Overrides: Feed 100% | Rapid 100% | Spindle 100%")
        self._feed_override_slider_locked = False
        self._spindle_override_slider_locked = False
        self._feed_override_slider_last_position = 100
        self._spindle_override_slider_last_position = 100
        self._machine_state_text = "DISCONNECTED"
        self._grbl_setting_info = {}
        self._grbl_setting_keys = []
        self._last_sent_index = -1
        self._last_acked_index = -1
        self._confirm_last_time = {}
        self._confirm_debounce_sec = 0.8
        self._auto_reconnect_last_port = self.settings.get("last_port", "")
        self._auto_reconnect_last_attempt = 0.0
        self._auto_reconnect_pending = False
        self._auto_reconnect_retry = 0
        self._auto_reconnect_delay = 3.0
        self._auto_reconnect_max_retry = 5
        self._auto_reconnect_next_ts = 0.0
        self._user_disconnect = False
        self._ui_throttle_ms = 100
        self._state_flash_after_id = None
        self._state_flash_color = None
        self._state_flash_on = False
        self._state_default_fg = None

        # Top + main layout
        self._build_toolbar()
        self._build_main()

        self.after(50, self._drain_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_ports(auto_connect=bool(self.reconnect_on_open.get()))
        geometry = self.settings.get("window_geometry", "")
        if isinstance(geometry, str) and geometry:
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass
        self._load_grbl_setting_info()
        self.streaming_controller.bind_button_logging()
        self._apply_keyboard_bindings()

    # ---------- UI ----------
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Port:").pack(side="left")
        self.port_combo = ttk.Combobox(bar, width=18, textvariable=self.current_port, state="readonly")
        self.port_combo.pack(side="left", padx=(6, 4))

        self.btn_refresh = ttk.Button(bar, text="Refresh", command=self.refresh_ports)
        set_kb_id(self.btn_refresh, "port_refresh")
        self.btn_refresh.pack(side="left", padx=(0, 10))
        apply_tooltip(self.btn_refresh, "Refresh the list of serial ports.")
        self.btn_conn = ttk.Button(bar, text="Connect", command=lambda: self._confirm_and_run("Connect/Disconnect", self.toggle_connect))
        set_kb_id(self.btn_conn, "port_connect")
        self.btn_conn.pack(side="left")
        apply_tooltip(self.btn_conn, "Connect or disconnect from the selected serial port.")
        attach_log_gcode(self.btn_conn, "")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_open = ttk.Button(bar, text="Read Job", command=self.open_gcode)
        set_kb_id(self.btn_open, "gcode_open")
        self.btn_open.pack(side="left")
        self._manual_controls.append(self.btn_open)
        apply_tooltip(self.btn_open, "Load a G-code job for streaming (read-only).")
        self.btn_clear = ttk.Button(bar, text="Clear Job", command=lambda: self._confirm_and_run("Clear Job", self._clear_gcode))
        set_kb_id(self.btn_clear, "gcode_clear")
        self.btn_clear.pack(side="left", padx=(6, 0))
        self._manual_controls.append(self.btn_clear)
        apply_tooltip(self.btn_clear, "Unload the current job and reset the viewer.")
        self.btn_run = ttk.Button(bar, text="Run", command=lambda: self._confirm_and_run("Run job", self.run_job), state="disabled")
        set_kb_id(self.btn_run, "job_run")
        self.btn_run.pack(side="left", padx=(8, 0))
        apply_tooltip(self.btn_run, "Start streaming the loaded G-code.")
        attach_log_gcode(self.btn_run, "Cycle Start")
        self.btn_pause = ttk.Button(bar, text="Pause", command=lambda: self._confirm_and_run("Pause job", self.pause_job), state="disabled")
        set_kb_id(self.btn_pause, "job_pause")
        self.btn_pause.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_pause, "Feed hold the running job.")
        attach_log_gcode(self.btn_pause, "!")
        self.btn_resume = ttk.Button(bar, text="Resume", command=lambda: self._confirm_and_run("Resume job", self.resume_job), state="disabled")
        set_kb_id(self.btn_resume, "job_resume")
        self.btn_resume.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_resume, "Resume a paused job.")
        attach_log_gcode(self.btn_resume, "~")
        self.btn_stop = ttk.Button(bar, text="Stop/Reset", command=lambda: self._confirm_and_run("Stop/Reset", self.stop_job), state="disabled")
        set_kb_id(self.btn_stop, "job_stop_reset")
        self.btn_stop.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_stop, "Stop the job and soft reset GRBL.")
        attach_log_gcode(self.btn_stop, "Ctrl-X")
        self.btn_resume_from = ttk.Button(
            bar,
            text="Resume From...",
            command=lambda: self._confirm_and_run("Resume from line", self._show_resume_dialog),
            state="disabled",
        )
        set_kb_id(self.btn_resume_from, "job_resume_from")
        self.btn_resume_from.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_resume_from, "Resume from a specific line with modal re-sync.")
        self.btn_unlock_top = ttk.Button(bar, text="Unlock", command=lambda: self._confirm_and_run("Unlock ($X)", self.grbl.unlock), state="disabled")
        set_kb_id(self.btn_unlock_top, "unlock_top")
        self.btn_unlock_top.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_unlock_top, "Send $X to clear alarm (top-bar).")
        self.btn_alarm_recover = ttk.Button(
            bar,
            text="Recover",
            command=self._show_alarm_recovery,
            state="disabled",
        )
        set_kb_id(self.btn_alarm_recover, "alarm_recover")
        self.btn_alarm_recover.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_alarm_recover, "Show alarm recovery steps.")

        self._recover_separator = ttk.Separator(bar, orient="vertical")
        self._recover_separator.pack(side="left", fill="y", padx=10)

        self._update_resume_button_visibility()
        self._update_recover_button_visibility()

        self.btn_unit_toggle = ttk.Button(bar, text="mm", command=self._toggle_unit_mode)
        set_kb_id(self.btn_unit_toggle, "unit_toggle")
        self.btn_unit_toggle.pack(side="left", padx=(0, 0))
        self._manual_controls.append(self.btn_unit_toggle)
        apply_tooltip(self.btn_unit_toggle, "Toggle units between mm and inch.")

        # right side status
        self.machine_state_label = ttk.Label(bar, textvariable=self.machine_state)
        self.machine_state_label.pack(side="right")

    def _build_main(self):
        style = self.style
        hidden_style = self.HIDDEN_MPOS_BUTTON_STYLE
        bg_color = (
            self.cget("background")
            or style.lookup("TLabelframe", "background")
            or style.lookup("TFrame", "background")
            or "#f0f0f0"
        )
        style.configure(
            hidden_style,
            relief="flat",
            borderwidth=0,
            padding=0,
            background=bg_color,
            foreground=bg_color,
        )
        style.map(
            hidden_style,
            background=[("active", bg_color), ("disabled", bg_color), ("!disabled", bg_color)],
            foreground=[("active", bg_color), ("disabled", bg_color), ("!disabled", bg_color)],
        )
        style.configure(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            troughcolor="#e3f2fd",
            background="#1976d2",
            bordercolor="#90caf9",
            lightcolor="#64b5f6",
            darkcolor="#1565c0",
        )
        style.map(
            "SimpleSender.Blue.Horizontal.TProgressbar",
            background=[("disabled", "#90caf9"), ("!disabled", "#1976d2")],
        )
        body = ttk.Frame(self, padding=(8, 8))
        body.pack(side="top", fill="both", expand=True)

        top = ttk.Frame(body)
        top.pack(side="top", fill="x")

        # Left: DRO
        mpos = ttk.Labelframe(top, text="Machine Position (MPos)", padding=8)
        mpos.pack(side="left", fill="y", padx=(0, 10))

        self._dro_value_row(mpos, "X", self.mpos_x)
        self._dro_value_row(mpos, "Y", self.mpos_y)
        self._dro_value_row(mpos, "Z", self.mpos_z)
        self.btn_home_mpos = ttk.Button(mpos, text="Home", command=self.grbl.home)
        set_kb_id(self.btn_home_mpos, "home")
        self.btn_home_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_home_mpos)
        apply_tooltip(self.btn_home_mpos, "Run the homing cycle.")
        attach_log_gcode(self.btn_home_mpos, "$H")
        self.btn_unlock_mpos = ttk.Button(mpos, text="Unlock", command=self.grbl.unlock)
        set_kb_id(self.btn_unlock_mpos, "unlock")
        self.btn_unlock_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_unlock_mpos)
        apply_tooltip(self.btn_unlock_mpos, "Clear alarm lock ($X).")
        attach_log_gcode(self.btn_unlock_mpos, "$X")
        self.btn_hold_mpos = ttk.Button(mpos, text="Hold", command=self.grbl.hold)
        set_kb_id(self.btn_hold_mpos, "feed_hold")
        self.btn_hold_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_hold_mpos)
        apply_tooltip(self.btn_hold_mpos, "Feed hold.")
        attach_log_gcode(self.btn_hold_mpos, "!")
        self.btn_resume_mpos = ttk.Button(mpos, text="Resume", command=self.grbl.resume)
        set_kb_id(self.btn_resume_mpos, "feed_resume")
        self.btn_resume_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_resume_mpos)
        apply_tooltip(self.btn_resume_mpos, "Resume after hold.")
        attach_log_gcode(self.btn_resume_mpos, "~")

        dro = ttk.Labelframe(top, text="Work Position (WPos)", padding=8)
        dro.pack(side="left", fill="y", padx=(0, 10))

        self.btn_zero_x = self._dro_row(dro, "X", self.wpos_x, self.zero_x)
        self.btn_zero_y = self._dro_row(dro, "Y", self.wpos_y, self.zero_y)
        self.btn_zero_z = self._dro_row(dro, "Z", self.wpos_z, self.zero_z)
        self._manual_controls.extend([self.btn_zero_x, self.btn_zero_y, self.btn_zero_z])
        apply_tooltip(self.btn_zero_x, "Zero the WCS X axis (G92 X0).")
        apply_tooltip(self.btn_zero_y, "Zero the WCS Y axis (G92 Y0).")
        apply_tooltip(self.btn_zero_z, "Zero the WCS Z axis (G92 Z0).")
        attach_log_gcode(self.btn_zero_x, "G92 X0")
        attach_log_gcode(self.btn_zero_y, "G92 Y0")
        attach_log_gcode(self.btn_zero_z, "G92 Z0")

        btns = ttk.Frame(dro)
        btns.pack(fill="x", pady=(6, 0))
        self.btn_zero_all = ttk.Button(btns, text="Zero All", command=self.zero_all)
        set_kb_id(self.btn_zero_all, "zero_all")
        self.btn_zero_all.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_zero_all)
        apply_tooltip(self.btn_zero_all, "Zero all WCS axes (G92 X0 Y0 Z0).")
        attach_log_gcode(self.btn_zero_all, "G92 X0 Y0 Z0")
        self.btn_goto_zero = ttk.Button(btns, text="Goto Zero", command=self.goto_zero)
        set_kb_id(self.btn_goto_zero, "goto_zero")
        self.btn_goto_zero.pack(side="left", expand=True, fill="x", padx=(6, 0))
        self._manual_controls.append(self.btn_goto_zero)
        apply_tooltip(self.btn_goto_zero, "Rapid move to WCS X0 Y0.")
        attach_log_gcode(self.btn_goto_zero, "G0 X0 Y0")
        macro_left = ttk.Frame(dro)
        macro_left.pack(fill="x", pady=(6, 0))

        # Center: Jog
        jog = ttk.Labelframe(top, text="Jog", padding=8)
        jog.pack(side="left", fill="both", expand=True, padx=(10, 10))

        pad = ttk.Frame(jog)
        pad.pack(side="left", padx=(0, 12))

        def _jog_feed_for_move(dx, dy, dz) -> float:
            # Use Z feed only for pure Z moves; otherwise use XY feed.
            if abs(dz) > 0 and abs(dx) < 1e-9 and abs(dy) < 1e-9:
                return float(self.jog_feed_z.get())
            return float(self.jog_feed_xy.get())

        def j(dx, dy, dz):
            if not self.grbl.is_connected():
                self.streaming_controller.log("Jog ignored – GRBL is not connected.")
                return
            feed = _jog_feed_for_move(dx, dy, dz)
            self.grbl.jog(dx, dy, dz, feed, self.unit_mode.get())

        def jog_cmd(dx, dy, dz):
            feed = _jog_feed_for_move(dx, dy, dz)
            gunit = "G21" if self.unit_mode.get() == "mm" else "G20"
            return f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"

        # 3x3 pad for XY
        self.btn_jog_y_plus = ttk.Button(pad, text="Y+", width=6, command=lambda: j(0, self.step_xy.get(), 0))
        set_kb_id(self.btn_jog_y_plus, "jog_y_plus")
        self.btn_jog_y_plus.grid(row=0, column=1, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_y_plus, lambda: jog_cmd(0, self.step_xy.get(), 0))
        self.btn_jog_x_minus = ttk.Button(pad, text="X-", width=6, command=lambda: j(-self.step_xy.get(), 0, 0))
        set_kb_id(self.btn_jog_x_minus, "jog_x_minus")
        self.btn_jog_x_minus.grid(row=1, column=0, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_x_minus, lambda: jog_cmd(-self.step_xy.get(), 0, 0))
        self.btn_jog_x_plus = ttk.Button(pad, text="X+", width=6, command=lambda: j(self.step_xy.get(), 0, 0))
        set_kb_id(self.btn_jog_x_plus, "jog_x_plus")
        self.btn_jog_x_plus.grid(row=1, column=2, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_x_plus, lambda: jog_cmd(self.step_xy.get(), 0, 0))
        self.btn_jog_y_minus = ttk.Button(pad, text="Y-", width=6, command=lambda: j(0, -self.step_xy.get(), 0))
        set_kb_id(self.btn_jog_y_minus, "jog_y_minus")
        self.btn_jog_y_minus.grid(row=2, column=1, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_y_minus, lambda: jog_cmd(0, -self.step_xy.get(), 0))
        apply_tooltip(self.btn_jog_y_plus, "Jog +Y by the selected step.")
        apply_tooltip(self.btn_jog_y_minus, "Jog -Y by the selected step.")
        apply_tooltip(self.btn_jog_x_minus, "Jog -X by the selected step.")
        apply_tooltip(self.btn_jog_x_plus, "Jog +X by the selected step.")

        style = ttk.Style()
        sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background")
        pad_bg = style.lookup("TFrame", "background") or self.cget("bg")
        style.configure("JogSeparator.TSeparator", background=sep_color)
        # Z
        self.btn_jog_cancel = StopSignButton(
            pad,
            text="JOG\nSTOP",
            fill="#f2b200",
            text_color="#000000",
            command=self.grbl.jog_cancel,
            bg=pad_bg,
        )
        set_kb_id(self.btn_jog_cancel, "jog_stop")
        self.btn_jog_cancel.grid(row=0, column=3, rowspan=3, padx=(6, 2), pady=2, sticky="ns")
        apply_tooltip(self.btn_jog_cancel, "Cancel an active jog ($J cancel).")
        attach_log_gcode(self.btn_jog_cancel, "RT 0x85")

        sep = ttk.Separator(pad, orient="vertical", style="JogSeparator.TSeparator")
        sep.grid(row=0, column=4, rowspan=5, sticky="ns", padx=(6, 6))

        self.btn_jog_z_plus = ttk.Button(pad, text="Z+", width=6, command=lambda: j(0, 0, self.step_z.get()))
        set_kb_id(self.btn_jog_z_plus, "jog_z_plus")
        self.btn_jog_z_plus.grid(row=0, column=5, padx=(6, 2), pady=2)
        self.btn_jog_z_minus = ttk.Button(pad, text="Z-", width=6, command=lambda: j(0, 0, -self.step_z.get()))
        set_kb_id(self.btn_jog_z_minus, "jog_z_minus")
        self.btn_jog_z_minus.grid(row=2, column=5, padx=(6, 2), pady=2)
        apply_tooltip(self.btn_jog_z_plus, "Jog +Z by the selected step.")
        apply_tooltip(self.btn_jog_z_minus, "Jog -Z by the selected step.")
        attach_log_gcode(self.btn_jog_z_plus, lambda: jog_cmd(0, 0, self.step_z.get()))
        attach_log_gcode(self.btn_jog_z_minus, lambda: jog_cmd(0, 0, -self.step_z.get()))

        self.btn_all_stop = StopSignButton(
            pad,
            text="ALL\nSTOP",
            fill="#d83b2d",
            text_color="#ffffff",
            command=self._all_stop_action,
            bg=pad_bg,
        )
        set_kb_id(self.btn_all_stop, "all_stop")
        self.btn_all_stop.grid(row=0, column=6, rowspan=3, padx=(6, 0), pady=2, sticky="ns")
        apply_tooltip(self.btn_all_stop, "Immediate stop (behavior from App Settings).")
        attach_log_gcode(self.btn_all_stop, self._all_stop_gcode_label)

        self._manual_controls.extend([
            self.btn_jog_y_plus,
            self.btn_jog_x_minus,
            self.btn_jog_x_plus,
            self.btn_jog_y_minus,
            self.btn_jog_z_plus,
            self.btn_jog_z_minus,
        ])
        self._manual_controls.append(self.btn_jog_cancel)
        self._manual_controls.append(self.btn_all_stop)

        spacer = ttk.Frame(pad, height=6)
        spacer.grid(row=3, column=0, columnspan=6)

        xy_steps = ttk.Frame(pad)
        xy_steps.grid(row=4, column=0, columnspan=4, pady=(6, 0))
        xy_values = [0.1, 1.0, 5.0, 10, 25, 50, 100, 400]
        for i, v in enumerate(xy_values):
            r = i // 4
            c = i % 4
            btn = ttk.Button(
                xy_steps,
                text=f"{v:g}",
                command=lambda value=v: self._set_step_xy(value),
            )
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            btn._kb_id = f"step_xy_{v:g}"
            self._xy_step_buttons.append((v, btn))
            apply_tooltip(btn, f"Set XY step to {v:g}.")

        z_steps = ttk.Frame(pad)
        z_steps.grid(row=4, column=5, padx=(6, 0), pady=(6, 0))
        z_values = [0.05, 0.1, 0.5, 1, 5, 10, 25, 50]
        for i, v in enumerate(z_values):
            r = 0 if i < 4 else 1
            c = i if i < 4 else i - 4
            btn = ttk.Button(
                z_steps,
                text=f"{v:g}",
                command=lambda value=v: self._set_step_z(value),
            )
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            btn._kb_id = f"step_z_{v:g}"
            self._z_step_buttons.append((v, btn))
            apply_tooltip(btn, f"Set Z step to {v:g}.")

        self._manual_controls.extend([btn for _, btn in self._xy_step_buttons])
        self._manual_controls.extend([btn for _, btn in self._z_step_buttons])
        self._set_step_xy(self.step_xy.get())
        self._set_step_z(self.step_z.get())

        macro_right = ttk.Frame(pad)
        macro_right.grid(row=5, column=0, columnspan=6, pady=(6, 0), sticky="ew")

        self.macro_panel.attach_frames(macro_left, macro_right)

        self._set_unit_mode(self.unit_mode.get())

        # Bottom notebook: G-code + Console + Settings
        nb = ttk.Notebook(body)
        self.notebook = nb
        nb.pack(side="top", fill="both", expand=True, pady=(10, 0))
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Gcode tab
        gtab = ttk.Frame(nb, padding=6)
        nb.add(gtab, text="G-code")
        stats_row = ttk.Frame(gtab)
        stats_row.pack(fill="x", pady=(0, 6))
        self.gcode_stats_label = ttk.Label(stats_row, textvariable=self.gcode_stats_var, anchor="w")
        self.gcode_stats_label.pack(side="left", fill="x", expand=True)
        self.gcode_load_bar = ttk.Progressbar(stats_row, length=140, mode="determinate")
        self.gcode_load_label = ttk.Label(stats_row, textvariable=self.gcode_load_var, anchor="e")
        self._hide_gcode_loading()
        self.gview = GcodeViewer(gtab)  # Using refactored GcodeViewer
        self.gview.pack(fill="both", expand=True)

        # Console tab
        ctab = ttk.Frame(nb, padding=6)
        nb.add(ctab, text="Console")

        self.console = tk.Text(ctab, wrap="word", height=12, state="disabled")
        csb = ttk.Scrollbar(ctab, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=csb.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        csb.grid(row=0, column=1, sticky="ns")
        self._setup_console_tags()
        ctab.grid_rowconfigure(0, weight=1)
        ctab.grid_columnconfigure(0, weight=1)

        entry_row = ttk.Frame(ctab)
        entry_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        entry_row.grid_columnconfigure(0, weight=1)

        self.cmd_entry = ttk.Entry(entry_row)
        self.cmd_entry.grid(row=0, column=0, sticky="ew")
        self.btn_send = ttk.Button(entry_row, text="Send", command=self._send_console)
        set_kb_id(self.btn_send, "console_send")
        self.btn_send.grid(row=0, column=1, padx=(8, 0))
        self._manual_controls.extend([self.cmd_entry, self.btn_send])
        apply_tooltip(self.btn_send, "Send the command from the console input.")
        attach_log_gcode(self.btn_send, lambda: self.cmd_entry.get().strip())
        self.btn_console_save = ttk.Button(entry_row, text="Save", command=self._save_console_log)
        set_kb_id(self.btn_console_save, "console_save")
        self.btn_console_save.grid(row=0, column=2, padx=(8, 0))
        apply_tooltip(self.btn_console_save, "Save the console log to a text file.")
        self.btn_console_clear = ttk.Button(entry_row, text="Clear", command=self._clear_console_log)
        set_kb_id(self.btn_console_clear, "console_clear")
        self.btn_console_clear.grid(row=0, column=3, padx=(8, 0))
        apply_tooltip(self.btn_console_clear, "Clear the console log.")
        self.console_filter_sep = ttk.Separator(entry_row, orient="vertical")
        self.console_filter_sep.grid(row=0, column=4, sticky="ns", padx=(8, 6))
        self.btn_console_all = ttk.Button(entry_row, text="ALL", command=lambda: self.streaming_controller.set_console_filter(None))
        set_kb_id(self.btn_console_all, "console_filter_all")
        self.btn_console_all.grid(row=0, column=5, padx=(0, 0))
        apply_tooltip(self.btn_console_all, "Show all console log entries.")
        self.btn_console_errors = ttk.Button(entry_row, text="ERRORS", command=lambda: self.streaming_controller.set_console_filter("errors"))
        set_kb_id(self.btn_console_errors, "console_filter_errors")
        self.btn_console_errors.grid(row=0, column=6, padx=(1, 0))
        apply_tooltip(self.btn_console_errors, "Show only error entries in the console log.")
        self.btn_console_alarms = ttk.Button(entry_row, text="ALARMS", command=lambda: self.streaming_controller.set_console_filter("alarms"))
        set_kb_id(self.btn_console_alarms, "console_filter_alarms")
        self.btn_console_alarms.grid(row=0, column=7, padx=(1, 0))
        apply_tooltip(self.btn_console_alarms, "Show only alarm entries in the console log.")
        self.btn_console_pos = ttk.Button(
            entry_row,
            text="Pos/Status: On" if bool(self.console_positions_enabled.get()) else "Pos/Status: Off",
            command=self._toggle_console_pos_status,
        )
        set_kb_id(self.btn_console_pos, "console_pos_toggle")
        self.btn_console_pos.grid(row=0, column=8, padx=(10, 0))
        apply_tooltip(
            self.btn_console_pos,
            "Show/hide position and status reports in the console (not saved to log).",
        )

        self.cmd_entry.bind("<Return>", lambda e: self._send_console())
        self.streaming_controller.attach_widgets(
            console=self.console,
            gview=self.gview,
            progress_pct=self.progress_pct,
            buffer_fill=self.buffer_fill,
            buffer_fill_pct=self.buffer_fill_pct,
            throughput_var=self.throughput_var,
        )

        otab = ttk.Frame(nb, padding=6)
        nb.add(otab, text="Overdrive")
        self._build_overdrive_tab(otab)
        self.settings_controller.build_tabs(nb)

        # App Settings tab
        sstab = ttk.Frame(nb, padding=8)
        nb.add(sstab, text="App Settings")
        sstab.grid_columnconfigure(0, weight=1)
        sstab.grid_rowconfigure(0, weight=1)
        self.app_settings_canvas = tk.Canvas(sstab, highlightthickness=0)
        self.app_settings_canvas.grid(row=0, column=0, sticky="nsew")
        self.app_settings_scroll = ttk.Scrollbar(
            sstab, orient="vertical", command=self.app_settings_canvas.yview
        )
        self.app_settings_scroll.grid(row=0, column=1, sticky="ns")
        self.app_settings_canvas.configure(yscrollcommand=self.app_settings_scroll.set)
        self._app_settings_inner = ttk.Frame(self.app_settings_canvas)
        self._app_settings_window = self.app_settings_canvas.create_window(
            (0, 0), window=self._app_settings_inner, anchor="nw"
        )
        self._app_settings_inner.bind("<Configure>", lambda event: self._update_app_settings_scrollregion())
        self.app_settings_canvas.bind("<Configure>", lambda event: self.app_settings_canvas.itemconfig(
            self._app_settings_window, width=event.width
        ))
        self._app_settings_inner.bind("<Enter>", lambda event: self._bind_app_settings_mousewheel())
        self._app_settings_inner.bind("<Leave>", lambda event: self._unbind_app_settings_mousewheel())
        self._app_settings_inner.grid_columnconfigure(0, weight=1)

        version_label = ttk.Label(
            self._app_settings_inner,
            textvariable=self.version_var,
            font=("TkDefaultFont", 10, "bold"),
        )
        version_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

        theme_frame = ttk.LabelFrame(self._app_settings_inner, text="Theme", padding=8)
        theme_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        theme_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(theme_frame, text="UI theme").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.theme_combo = ttk.Combobox(
            theme_frame,
            state="readonly",
            values=self.available_themes,
            textvariable=self.selected_theme,
            width=28,
        )
        self.theme_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.theme_combo.bind("<<ComboboxSelected>>", self._on_theme_change)
        apply_tooltip(
            self.theme_combo,
            "Pick a ttk theme; some themes require a restart for best results.",
        )

        safety = ttk.LabelFrame(self._app_settings_inner, text="Safety", padding=8)
        safety.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        safety.grid_columnconfigure(1, weight=1)
        ttk.Label(safety, text="All Stop behavior").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.all_stop_combo = ttk.Combobox(
            safety,
            state="readonly",
            values=[label for label, _ in ALL_STOP_CHOICES],
            width=32,
        )
        self.all_stop_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.all_stop_combo.bind("<<ComboboxSelected>>", self._on_all_stop_mode_change)
        apply_tooltip(self.all_stop_combo, "Select how the ALL STOP button behaves.")
        self._sync_all_stop_mode_combo()
        self.all_stop_desc = ttk.Label(
            safety,
            text="Soft Reset (Ctrl-X) stops GRBL immediately. Stop Stream + Reset halts sending first, then resets.",
            wraplength=560,
            justify="left",
        )
        self.all_stop_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        estimation = ttk.LabelFrame(self._app_settings_inner, text="Estimation", padding=8)
        estimation.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        estimation.grid_columnconfigure(1, weight=1)
        ttk.Label(estimation, text="Fallback rapid rate (mm/min)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.fallback_rapid_entry = ttk.Entry(estimation, textvariable=self.fallback_rapid_rate, width=12)
        self.fallback_rapid_entry.grid(row=0, column=1, sticky="w", pady=4)
        self.fallback_rapid_entry.bind("<Return>", self._on_fallback_rate_change)
        self.fallback_rapid_entry.bind("<FocusOut>", self._on_fallback_rate_change)
        apply_tooltip(
            self.fallback_rapid_entry,
            "Used for time estimates when GRBL max rates ($110-$112) are not available.",
        )
        ttk.Label(estimation, text="Estimator adjustment").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.estimate_factor_scale = ttk.Scale(
            estimation,
            from_=0.5,
            to=2.0,
            orient="horizontal",
            variable=self.estimate_factor,
            command=self._on_estimate_factor_change,
        )
        self.estimate_factor_scale.grid(row=1, column=1, sticky="ew", pady=4)
        self.estimate_factor_value = ttk.Label(estimation, textvariable=self._estimate_factor_label)
        self.estimate_factor_value.grid(row=1, column=2, sticky="w", padx=(8, 0))
        apply_tooltip(
            self.estimate_factor_scale,
            "Scale time estimates up or down (1.00x = default).",
        )

        status_frame = ttk.LabelFrame(self._app_settings_inner, text="Status polling", padding=8)
        status_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        status_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(status_frame, text="Status report interval (seconds)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.status_poll_entry = ttk.Entry(
            status_frame, textvariable=self.status_poll_interval, width=12
        )
        self.status_poll_entry.grid(row=0, column=1, sticky="w", pady=4)
        self.status_poll_entry.bind("<Return>", self._on_status_interval_change)
        self.status_poll_entry.bind("<FocusOut>", self._on_status_interval_change)
        apply_tooltip(
            self.status_poll_entry,
            "Set how often GRBL status reports are requested (seconds).",
        )
        self._on_status_interval_change()
        ttk.Label(status_frame, text="Disconnect after failures").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.status_fail_limit_entry = ttk.Entry(
            status_frame, textvariable=self.status_query_failure_limit, width=12
        )
        self.status_fail_limit_entry.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(status_frame, text="(1-10)").grid(row=1, column=2, sticky="w", padx=(6, 0))
        self.status_fail_limit_entry.bind("<Return>", self._on_status_failure_limit_change)
        self.status_fail_limit_entry.bind("<FocusOut>", self._on_status_failure_limit_change)
        apply_tooltip(
            self.status_fail_limit_entry,
            "Consecutive status send failures before disconnecting (clamped to 1-10).",
        )

        dialog_frame = ttk.LabelFrame(self._app_settings_inner, text="Error dialogs", padding=8)
        dialog_frame.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        dialog_frame.grid_columnconfigure(1, weight=1)
        self.error_dialogs_check = ttk.Checkbutton(
            dialog_frame,
            text="Enable error dialogs",
            variable=self.error_dialogs_enabled,
            command=self._on_error_dialogs_enabled_change,
        )
        self.error_dialogs_check.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        apply_tooltip(self.error_dialogs_check, "Show modal dialogs for errors (tracebacks still log to console).")
        ttk.Label(dialog_frame, text="Minimum interval (seconds)").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.error_dialog_interval_entry = ttk.Entry(
            dialog_frame, textvariable=self.error_dialog_interval_var, width=12
        )
        self.error_dialog_interval_entry.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(dialog_frame, text="sec").grid(row=1, column=2, sticky="w", padx=(6, 0))
        self.error_dialog_interval_entry.bind("<Return>", self._apply_error_dialog_settings)
        self.error_dialog_interval_entry.bind("<FocusOut>", self._apply_error_dialog_settings)
        ttk.Label(dialog_frame, text="Burst window (seconds)").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.error_dialog_window_entry = ttk.Entry(
            dialog_frame, textvariable=self.error_dialog_burst_window_var, width=12
        )
        self.error_dialog_window_entry.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(dialog_frame, text="sec").grid(row=2, column=2, sticky="w", padx=(6, 0))
        self.error_dialog_window_entry.bind("<Return>", self._apply_error_dialog_settings)
        self.error_dialog_window_entry.bind("<FocusOut>", self._apply_error_dialog_settings)
        ttk.Label(dialog_frame, text="Max dialogs per window").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.error_dialog_limit_entry = ttk.Entry(
            dialog_frame, textvariable=self.error_dialog_burst_limit_var, width=12
        )
        self.error_dialog_limit_entry.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(dialog_frame, text="count").grid(row=3, column=2, sticky="w", padx=(6, 0))
        self.error_dialog_limit_entry.bind("<Return>", self._apply_error_dialog_settings)
        self.error_dialog_limit_entry.bind("<FocusOut>", self._apply_error_dialog_settings)
        apply_tooltip(
            self.error_dialog_interval_entry,
            "Minimum seconds between modal error dialogs.",
        )
        apply_tooltip(
            self.error_dialog_window_entry,
            "Time window for counting dialog bursts.",
        )
        apply_tooltip(
            self.error_dialog_limit_entry,
            "Maximum dialogs allowed inside the burst window before suppressing.",
        )
        self.job_completion_popup_check = ttk.Checkbutton(
            dialog_frame,
            text="Show job completion dialog",
            variable=self.job_completion_popup,
        )
        self.job_completion_popup_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 2))
        apply_tooltip(
            self.job_completion_popup_check,
            "Pop up an alert when a job completes, summarizing start/finish/elapsed times.",
        )
        self.job_completion_beep_check = ttk.Checkbutton(
            dialog_frame,
            text="Play reminder beep on completion",
            variable=self.job_completion_beep,
        )
        self.job_completion_beep_check.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 4))
        apply_tooltip(
            self.job_completion_beep_check,
            "Ring the system bell when a job has finished streaming.",
        )

        macro_frame = ttk.LabelFrame(self._app_settings_inner, text="Macros", padding=8)
        macro_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        macro_frame.grid_columnconfigure(0, weight=1)
        self.macros_allow_python_check = ttk.Checkbutton(
            macro_frame,
            text="Allow macro scripting (Python/eval)",
            variable=self.macros_allow_python,
        )
        self.macros_allow_python_check.grid(row=0, column=0, sticky="w", pady=(0, 4))
        apply_tooltip(
            self.macros_allow_python_check,
            "Disable to allow only plain G-code lines in macros (no scripting or expressions).",
        )
        ttk.Label(
            macro_frame,
            text="Warning: enabled macros can execute arbitrary Python; disable for plain G-code macros.",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, sticky="w")

        jog_frame = ttk.LabelFrame(self._app_settings_inner, text="Jogging", padding=8)
        jog_frame.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        jog_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(jog_frame, text="Default jog feed (X/Y)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.jog_feed_xy_entry = ttk.Entry(jog_frame, textvariable=self.jog_feed_xy, width=12)
        self.jog_feed_xy_entry.grid(row=0, column=1, sticky="w", pady=4)
        self.jog_feed_xy_entry.bind("<Return>", self._on_jog_feed_change_xy)
        self.jog_feed_xy_entry.bind("<FocusOut>", self._on_jog_feed_change_xy)
        ttk.Label(jog_frame, text="Default jog feed (Z)").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.jog_feed_z_entry = ttk.Entry(jog_frame, textvariable=self.jog_feed_z, width=12)
        self.jog_feed_z_entry.grid(row=1, column=1, sticky="w", pady=4)
        self.jog_feed_z_entry.bind("<Return>", self._on_jog_feed_change_z)
        self.jog_feed_z_entry.bind("<FocusOut>", self._on_jog_feed_change_z)
        ttk.Label(jog_frame, text="Units: mm/min (in/min when in inches mode)").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=4
        )
        ttk.Label(
            jog_frame,
            text="Used by the jog buttons. Enter positive values.",
            wraplength=560,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 2))
        apply_tooltip(
            self.jog_feed_xy_entry,
            "Default speed for X/Y jog buttons (mm/min when in metric, in/min when in inches).",
        )
        apply_tooltip(
            self.jog_feed_z_entry,
            "Default speed for Z jog buttons (mm/min when in metric, in/min when in inches).",
        )

        kb_frame = ttk.LabelFrame(self._app_settings_inner, text="Keyboard shortcuts", padding=8)
        kb_frame.grid(row=8, column=0, sticky="nsew", pady=(0, 8))
        kb_frame.grid_columnconfigure(0, weight=1)
        kb_frame.grid_rowconfigure(1, weight=1)
        self.kb_enable_check = ttk.Checkbutton(
            kb_frame,
            text="Enabled",
            variable=self.keyboard_bindings_enabled,
            command=self._on_keyboard_bindings_check,
        )
        self.kb_enable_check.grid(row=0, column=0, columnspan=2, sticky="w", padx=(6, 10), pady=(4, 2))
        apply_tooltip(self.kb_enable_check, "Toggle keyboard shortcuts.")

        self.kb_table = ttk.Treeview(
            kb_frame, columns=("button", "axis", "key", "clear"), show="headings", height=6
        )
        self.kb_table.heading("button", text="Button")
        self.kb_table.heading("axis", text="Axis")
        self.kb_table.heading("key", text="Key")
        self.kb_table.heading("clear", text="")
        self.kb_table.column("button", width=220, anchor="w")
        self.kb_table.column("axis", width=50, anchor="center")
        self.kb_table.column("key", width=140, anchor="w")
        self.kb_table.column("clear", width=160, anchor="w")
        self.kb_table.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self.kb_table_scroll = ttk.Scrollbar(kb_frame, orient="vertical", command=self.kb_table.yview)
        self.kb_table.configure(yscrollcommand=self.kb_table_scroll.set)
        self.kb_table_scroll.grid(row=1, column=1, sticky="ns", padx=(4, 6), pady=(0, 6))
        self.kb_table.bind("<Double-1>", self._on_kb_table_double_click)
        self.kb_table.bind("<Button-1>", self._on_kb_table_click, add="+")

        self.kb_note = ttk.Label(
            kb_frame,
            text="Press up to three keys to bind a shortcut. Bindings are ignored while typing in text fields.",
            wraplength=560,
            justify="left",
        )
        self.kb_note.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        view_frame = ttk.LabelFrame(self._app_settings_inner, text="G-code view", padding=8)
        view_frame.grid(row=9, column=0, sticky="ew")
        view_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(view_frame, text="Current line highlight").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.current_line_combo = ttk.Combobox(
            view_frame,
            state="readonly",
            values=[label for label, _ in CURRENT_LINE_CHOICES],
            width=32,
        )
        self.current_line_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.current_line_combo.bind("<<ComboboxSelected>>", self._on_current_line_mode_change)
        apply_tooltip(self.current_line_combo, "Select which line is highlighted as current.")
        self._sync_current_line_mode_combo()
        self.current_line_desc = ttk.Label(
            view_frame,
            text=(
                "Processing highlights the line currently executing "
                "(the next line queued after the last ack). "
                "Sent highlights the most recently queued line."
            ),
            wraplength=560,
            justify="left",
        )
        self.current_line_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        toolpath_frame = ttk.LabelFrame(self._app_settings_inner, text="3D view quality", padding=8)
        toolpath_frame.grid(row=10, column=0, sticky="ew", pady=(0, 8))
        toolpath_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(toolpath_frame, text="Full draw limit (segments, 0=unlimited)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        full_limit_entry = ttk.Entry(toolpath_frame, textvariable=self.toolpath_full_limit, width=14)
        full_limit_entry.grid(row=0, column=1, sticky="w", pady=4)
        full_limit_entry.bind("<Return>", self._apply_toolpath_draw_limits)
        full_limit_entry.bind("<FocusOut>", self._apply_toolpath_draw_limits)
        apply_tooltip(
            full_limit_entry,
            "Sets the maximum number of segments drawn when the view is static (0 = unlimited).",
        )
        ttk.Label(toolpath_frame, text="Interactive draw limit (segments, 0=unlimited)").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        interactive_limit_entry = ttk.Entry(
            toolpath_frame, textvariable=self.toolpath_interactive_limit, width=14
        )
        interactive_limit_entry.grid(row=1, column=1, sticky="w", pady=4)
        interactive_limit_entry.bind("<Return>", self._apply_toolpath_draw_limits)
        interactive_limit_entry.bind("<FocusOut>", self._apply_toolpath_draw_limits)
        apply_tooltip(
            interactive_limit_entry,
            "Limits segment count during drags/pans (0 = unlimited, but may be slower).",
        )
        ttk.Label(toolpath_frame, text="Arc detail (degrees per step)").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        arc_scale = ttk.Scale(
            toolpath_frame,
            from_=self._toolpath_arc_detail_min,
            to=self._toolpath_arc_detail_max,
            orient="horizontal",
            variable=self.toolpath_arc_detail,
            command=self._on_arc_detail_scale_move,
        )
        arc_scale.grid(row=2, column=1, sticky="ew", pady=4)
        arc_scale.bind("<ButtonRelease-1>", self._apply_toolpath_arc_detail)
        arc_scale.bind("<KeyRelease>", self._on_arc_detail_scale_key_release)
        ttk.Label(toolpath_frame, textvariable=self._toolpath_arc_detail_value).grid(
            row=2, column=2, sticky="w", padx=(8, 0)
        )
        apply_tooltip(
            arc_scale,
            "Use smaller degrees when you want smoother arcs (more segments); larger values are faster.",
        )
        lightweight_chk = ttk.Checkbutton(
            toolpath_frame,
            text="Use lightweight preview (faster)",
            variable=self.toolpath_lightweight,
            command=self._on_toolpath_lightweight_change,
        )
        lightweight_chk.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        apply_tooltip(
            lightweight_chk,
            "Render a smaller preview (fewer segments) to keep the view responsive on weaker hardware.",
        )

        tw_frame = ttk.LabelFrame(self._app_settings_inner, text="Safety Aids", padding=8)
        tw_frame.grid(row=11, column=0, sticky="ew", pady=(8, 0))
        self.training_wheels_check = ttk.Checkbutton(
            tw_frame,
            text="Training Wheels (confirm top-bar actions)",
            variable=self.training_wheels,
        )
        self.training_wheels_check.grid(row=0, column=0, sticky="w")
        apply_tooltip(self.training_wheels_check, "Show confirmation dialogs for top toolbar actions.")

        self.reconnect_check = ttk.Checkbutton(
            tw_frame,
            text="Reconnect to last port on open",
            variable=self.reconnect_on_open,
        )
        self.reconnect_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        apply_tooltip(self.reconnect_check, "Auto-connect to the last used port when the app starts.")

        profile_frame = ttk.LabelFrame(self._app_settings_inner, text="Machine profile", padding=8)
        profile_frame.grid(row=12, column=0, sticky="ew", pady=(8, 0))
        profile_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(profile_frame, text="Active profile").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.profile_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.active_profile_name,
            state="readonly",
            values=[p.get("name", "") for p in self._machine_profiles],
            width=28,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_select)
        self.btn_profile_new = ttk.Button(profile_frame, text="New", command=self._new_profile)
        self.btn_profile_new.grid(row=0, column=2, sticky="w", padx=(8, 0), pady=4)
        self.btn_profile_save = ttk.Button(profile_frame, text="Save", command=self._save_profile)
        self.btn_profile_save.grid(row=0, column=3, sticky="w", padx=(6, 0), pady=4)
        self.btn_profile_delete = ttk.Button(profile_frame, text="Delete", command=self._delete_profile)
        self.btn_profile_delete.grid(row=0, column=4, sticky="w", padx=(6, 0), pady=4)
        ttk.Label(profile_frame, text="Name").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.profile_name_entry = ttk.Entry(profile_frame, textvariable=self.profile_name_var, width=24)
        self.profile_name_entry.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(profile_frame, text="Units").grid(
            row=1, column=2, sticky="w", padx=(8, 0), pady=4
        )
        self.profile_units_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.profile_units_var,
            state="readonly",
            values=("mm", "inch"),
            width=8,
        )
        self.profile_units_combo.grid(row=1, column=3, sticky="w", pady=4)
        self.profile_units_combo.bind("<<ComboboxSelected>>", self._on_profile_units_change)
        ttk.Label(profile_frame, text="Max rates (X/Y/Z)").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        rates_frame = ttk.Frame(profile_frame)
        rates_frame.grid(row=2, column=1, columnspan=3, sticky="w", pady=4)
        ttk.Label(rates_frame, text="X").pack(side="left")
        self.profile_rate_x_entry = ttk.Entry(rates_frame, textvariable=self.profile_rate_x_var, width=8)
        self.profile_rate_x_entry.pack(side="left", padx=(4, 8))
        ttk.Label(rates_frame, text="Y").pack(side="left")
        self.profile_rate_y_entry = ttk.Entry(rates_frame, textvariable=self.profile_rate_y_var, width=8)
        self.profile_rate_y_entry.pack(side="left", padx=(4, 8))
        ttk.Label(rates_frame, text="Z").pack(side="left")
        self.profile_rate_z_entry = ttk.Entry(rates_frame, textvariable=self.profile_rate_z_var, width=8)
        self.profile_rate_z_entry.pack(side="left", padx=(4, 8))
        self.profile_rate_units = ttk.Label(rates_frame, text="mm/min")
        self.profile_rate_units.pack(side="left", padx=(8, 0))
        apply_tooltip(
            self.profile_combo,
            "Select a machine profile for units and max rates used in time estimates.",
        )
        apply_tooltip(
            self.profile_rate_x_entry,
            "Set machine max rate for X (used in time estimates).",
        )
        apply_tooltip(
            self.profile_rate_y_entry,
            "Set machine max rate for Y (used in time estimates).",
        )
        apply_tooltip(
            self.profile_rate_z_entry,
            "Set machine max rate for Z (used in time estimates).",
        )
        self._refresh_profile_combo()
        if self.active_profile_name.get():
            self.profile_combo.set(self.active_profile_name.get())
            self._on_profile_select()

        interface_frame = ttk.LabelFrame(self._app_settings_inner, text="Interface", padding=8)
        interface_frame.grid(row=13, column=0, sticky="ew", pady=(8, 0))
        interface_frame.grid_columnconfigure(0, weight=1)
        self.resume_button_check = ttk.Checkbutton(
            interface_frame,
            text="Show 'Resume From...' button",
            variable=self.show_resume_from_button,
            command=self._on_resume_button_visibility_change,
        )
        self.resume_button_check.grid(row=0, column=0, sticky="w")
        apply_tooltip(
            self.resume_button_check,
            "Toggle the visibility of the toolbar button that lets you resume from a specific line.",
        )
        self.recover_button_check = ttk.Checkbutton(
            interface_frame,
            text="Show 'Recover' button",
            variable=self.show_recover_button,
            command=self._on_recover_button_visibility_change,
        )
        self.recover_button_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        apply_tooltip(
            self.recover_button_check,
            "Show or hide the Recover button that brings up the alarm recovery dialog.",
        )
        perf_btn_frame = ttk.Frame(interface_frame)
        perf_btn_frame.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.btn_performance_mode = ttk.Button(
            perf_btn_frame,
            text="Performance: On" if self.performance_mode.get() else "Performance: Off",
            command=self._toggle_performance,
        )
        set_kb_id(self.btn_performance_mode, "toggle_performance")
        self.btn_performance_mode.pack(side="left")
        apply_tooltip(self.btn_performance_mode, "Enable performance mode (batch console updates).")

        self.logging_check = ttk.Checkbutton(
            interface_frame,
            text="Log GUI button actions",
            variable=self.gui_logging_enabled,
            command=self._on_gui_logging_change,
        )
        self.logging_check.grid(row=3, column=0, sticky="w", pady=(8, 0))
        apply_tooltip(
            self.logging_check,
            "Record GUI button actions in the console log when enabled.",
        )

        # 3D tab
        self.toolpath_panel.build_tab(nb)

        # Status bar
        status_bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        status_bar.pack(side="bottom", fill="x", before=body)
        self.status = ttk.Label(status_bar, text="Disconnected", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Label(status_bar, text="Progress").pack(side="right")
        self.progress_bar = ttk.Progressbar(
            status_bar,
            orient="horizontal",
            length=140,
            mode="determinate",
            maximum=100,
            variable=self.progress_pct,
            style="SimpleSender.Blue.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(side="right", padx=(6, 12))
        self.buffer_bar = ttk.Progressbar(
            status_bar,
            orient="horizontal",
            length=120,
            mode="determinate",
            maximum=100,
            variable=self.buffer_fill_pct,
            style="SimpleSender.Blue.Horizontal.TProgressbar",
        )
        self.buffer_bar.pack(side="right", padx=(6, 0))
        self.error_dialog_status_label = ttk.Label(
            status_bar,
            textvariable=self.error_dialog_status_var,
            anchor="e",
        )
        self.error_dialog_status_label.pack(side="right", padx=(6, 0))
        apply_tooltip(
            self.error_dialog_status_label,
            "Shows when error dialogs are disabled or suppressed.",
        )
        ttk.Label(status_bar, textvariable=self.buffer_fill, anchor="e").pack(side="right")
        self.throughput_label = ttk.Label(
            status_bar,
            textvariable=self.throughput_var,
            anchor="e",
        )
        self.throughput_label.pack(side="right", padx=(6, 0))
        self._build_led_panel(status_bar)
        self.btn_toggle_tips = ttk.Button(
            status_bar,
            text="Tool Tips: On",
            command=self._toggle_tooltips,
        )
        set_kb_id(self.btn_toggle_tips, "toggle_tooltips")
        self.btn_toggle_tips.pack(side="right", padx=(8, 0))
        self.btn_toggle_tips.config(
            text="Tool Tips: On" if self.tooltip_enabled.get() else "Tool Tips: Off"
        )
        self.btn_toggle_3d = ttk.Button(
            status_bar,
            text="3D Render: On",
            command=self._toggle_render_3d,
        )
        set_kb_id(self.btn_toggle_3d, "toggle_render_3d")
        self.btn_toggle_3d.pack(side="right", padx=(8, 0))
        apply_tooltip(self.btn_toggle_3d, "Toggle 3D toolpath rendering.")
        self.btn_toggle_3d.config(
            text="3D Render: On" if self.render3d_enabled.get() else "3D Render: Off"
        )
        self.btn_toggle_keybinds = ttk.Button(
            status_bar,
            text="Keybindings: On",
            command=self._toggle_keyboard_bindings,
        )
        set_kb_id(self.btn_toggle_keybinds, "toggle_keybindings")
        self.btn_toggle_keybinds.pack(side="right", padx=(8, 0))
        apply_tooltip(self.btn_toggle_keybinds, "Toggle keyboard shortcuts.")
        self.btn_toggle_keybinds.config(
            text="Keybindings: On" if self.keyboard_bindings_enabled.get() else "Keybindings: Off"
        )
        self._on_error_dialogs_enabled_change()
        self._state_default_fg = self.status.cget("foreground") or "#000000"
        self._apply_state_fg(None)

    def _build_overdrive_tab(self, parent):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)

        spindle_frame = ttk.Labelframe(container, text="Spindle Control", padding=8)
        spindle_frame.pack(fill="x", pady=(0, 10))
        self.btn_spindle_on = ttk.Button(
            spindle_frame,
            text="Spindle ON",
            command=lambda: self._confirm_and_run("Spindle ON", lambda: self.grbl.spindle_on(DEFAULT_SPINDLE_RPM)),
        )
        set_kb_id(self.btn_spindle_on, "spindle_on")
        self.btn_spindle_on.pack(side="left", padx=(0, 6))
        self._manual_controls.append(self.btn_spindle_on)
        apply_tooltip(self.btn_spindle_on, "Turn spindle on at default RPM.")
        attach_log_gcode(self.btn_spindle_on, f"M3 S{DEFAULT_SPINDLE_RPM}")

        self.btn_spindle_off = ttk.Button(
            spindle_frame,
            text="Spindle OFF",
            command=lambda: self._confirm_and_run("Spindle OFF", self.grbl.spindle_off),
        )
        set_kb_id(self.btn_spindle_off, "spindle_off")
        self.btn_spindle_off.pack(side="left")
        self._manual_controls.append(self.btn_spindle_off)
        apply_tooltip(self.btn_spindle_off, "Turn spindle off.")
        attach_log_gcode(self.btn_spindle_off, "M5")

        info_label = ttk.Label(container, textvariable=self.override_info_var, anchor="center")
        info_label.pack(fill="x", pady=(0, 10))

        feed_frame = ttk.Labelframe(container, text="Feed Override", padding=8)
        feed_frame.pack(fill="x", pady=(0, 10))
        feed_slider_row = ttk.Frame(feed_frame)
        feed_slider_row.pack(fill="x", pady=(0, 6))
        self.feed_override_scale = ttk.Scale(
            feed_slider_row,
            from_=50,
            to=150,
            orient="horizontal",
            command=self._on_feed_override_slider,
        )
        self.feed_override_scale.pack(side="left", fill="x", expand=True)
        self.feed_override_scale.set(100)
        ttk.Label(feed_slider_row, textvariable=self.feed_override_display).pack(side="right", padx=(10, 0))

        feed_btn_row = ttk.Frame(feed_frame)
        feed_btn_row.pack(fill="x")
        self.btn_fo_plus = ttk.Button(feed_btn_row, text="+10%", command=lambda: self.grbl.send_realtime(RT_FO_PLUS_10))
        set_kb_id(self.btn_fo_plus, "feed_override_plus_10")
        self.btn_fo_plus.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_fo_plus)
        self._override_controls.append(self.btn_fo_plus)
        apply_tooltip(self.btn_fo_plus, "Increase feed override by 10%.")
        attach_log_gcode(self.btn_fo_plus, "RT 0x91")

        self.btn_fo_minus = ttk.Button(feed_btn_row, text="-10%", command=lambda: self.grbl.send_realtime(RT_FO_MINUS_10))
        set_kb_id(self.btn_fo_minus, "feed_override_minus_10")
        self.btn_fo_minus.pack(side="left", expand=True, fill="x", padx=6)
        self._manual_controls.append(self.btn_fo_minus)
        self._override_controls.append(self.btn_fo_minus)
        apply_tooltip(self.btn_fo_minus, "Decrease feed override by 10%.")
        attach_log_gcode(self.btn_fo_minus, "RT 0x92")

        self.btn_fo_reset = ttk.Button(feed_btn_row, text="Reset", command=lambda: self.grbl.send_realtime(RT_FO_RESET))
        set_kb_id(self.btn_fo_reset, "feed_override_reset")
        self.btn_fo_reset.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_fo_reset)
        self._override_controls.append(self.btn_fo_reset)
        apply_tooltip(self.btn_fo_reset, "Reset feed override to 100%.")
        attach_log_gcode(self.btn_fo_reset, "RT 0x90")

        spindle_override_frame = ttk.Labelframe(container, text="Spindle Override", padding=8)
        spindle_override_frame.pack(fill="x", pady=(0, 10))
        spindle_slider_row = ttk.Frame(spindle_override_frame)
        spindle_slider_row.pack(fill="x", pady=(0, 6))
        self.spindle_override_scale = ttk.Scale(
            spindle_slider_row,
            from_=50,
            to=150,
            orient="horizontal",
            command=self._on_spindle_override_slider,
        )
        self.spindle_override_scale.pack(side="left", fill="x", expand=True)
        self.spindle_override_scale.set(100)
        ttk.Label(spindle_slider_row, textvariable=self.spindle_override_display).pack(side="right", padx=(10, 0))

        spindle_btn_row = ttk.Frame(spindle_override_frame)
        spindle_btn_row.pack(fill="x")
        self.btn_so_plus = ttk.Button(spindle_btn_row, text="+10%", command=lambda: self.grbl.send_realtime(RT_SO_PLUS_10))
        set_kb_id(self.btn_so_plus, "spindle_override_plus_10")
        self.btn_so_plus.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_so_plus)
        self._override_controls.append(self.btn_so_plus)
        apply_tooltip(self.btn_so_plus, "Increase spindle override by 10%.")
        attach_log_gcode(self.btn_so_plus, "RT 0x9A")

        self.btn_so_minus = ttk.Button(spindle_btn_row, text="-10%", command=lambda: self.grbl.send_realtime(RT_SO_MINUS_10))
        set_kb_id(self.btn_so_minus, "spindle_override_minus_10")
        self.btn_so_minus.pack(side="left", expand=True, fill="x", padx=6)
        self._manual_controls.append(self.btn_so_minus)
        self._override_controls.append(self.btn_so_minus)
        apply_tooltip(self.btn_so_minus, "Decrease spindle override by 10%.")
        attach_log_gcode(self.btn_so_minus, "RT 0x9B")

        self.btn_so_reset = ttk.Button(spindle_btn_row, text="Reset", command=lambda: self.grbl.send_realtime(RT_SO_RESET))
        set_kb_id(self.btn_so_reset, "spindle_override_reset")
        self.btn_so_reset.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_so_reset)
        self._override_controls.append(self.btn_so_reset)
        apply_tooltip(self.btn_so_reset, "Reset spindle override to 100%.")
        attach_log_gcode(self.btn_so_reset, "RT 0x99")
        self._set_feed_override_slider_value(100)
        self._set_spindle_override_slider_value(100)
        self._refresh_override_info()

    def _normalize_override_slider_value(self, raw_value, minimum=50, maximum=150):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        value = max(minimum, min(maximum, value))
        rounded = int(round(value / 10.0)) * 10
        if rounded < minimum:
            rounded = minimum
        if rounded > maximum:
            rounded = maximum
        return rounded

    def _set_override_scale(self, scale_attr, value, lock_attr):
        scale = getattr(self, scale_attr, None)
        if not scale:
            return
        setattr(self, lock_attr, True)
        try:
            scale.set(value)
        finally:
            setattr(self, lock_attr, False)

    def _handle_override_slider_change(
        self,
        raw_value,
        last_attr,
        scale_attr,
        lock_attr,
        display_var,
        plus_cmd,
        minus_cmd,
    ):
        if getattr(self, lock_attr):
            return
        target = self._normalize_override_slider_value(raw_value)
        if target is None:
            return
        last = getattr(self, last_attr, 100)
        if target == last:
            return
        delta = target - last
        self._send_override_delta(delta, plus_cmd, minus_cmd)
        setattr(self, last_attr, target)
        display_var.set(f"{target}%")
        self._set_override_scale(scale_attr, target, lock_attr)

    def _on_feed_override_slider(self, raw_value):
        self._handle_override_slider_change(
            raw_value,
            "_feed_override_slider_last_position",
            "feed_override_scale",
            "_feed_override_slider_locked",
            self.feed_override_display,
            RT_FO_PLUS_10,
            RT_FO_MINUS_10,
        )

    def _on_spindle_override_slider(self, raw_value):
        self._handle_override_slider_change(
            raw_value,
            "_spindle_override_slider_last_position",
            "spindle_override_scale",
            "_spindle_override_slider_locked",
            self.spindle_override_display,
            RT_SO_PLUS_10,
            RT_SO_MINUS_10,
        )

    def _send_override_delta(self, delta, plus_cmd, minus_cmd):
        if not self.grbl.is_connected() or delta == 0:
            return
        step = 10
        while delta >= step:
            self.grbl.send_realtime(plus_cmd)
            delta -= step
        while delta <= -step:
            self.grbl.send_realtime(minus_cmd)
            delta += step

    def _set_feed_override_slider_value(self, value):
        self.feed_override_display.set(f"{value}%")
        self._feed_override_slider_last_position = value
        self._set_override_scale("feed_override_scale", value, "_feed_override_slider_locked")

    def _set_spindle_override_slider_value(self, value):
        self.spindle_override_display.set(f"{value}%")
        self._spindle_override_slider_last_position = value
        self._set_override_scale("spindle_override_scale", value, "_spindle_override_slider_locked")

    def _refresh_override_info(self):
        with self.macro_executor.macro_vars() as macro_vars:
            feed = macro_vars.get("OvFeed", 100)
            rapid = macro_vars.get("OvRapid", 100)
            spindle = macro_vars.get("OvSpindle", 100)
        self.override_info_var.set(
            f"Overrides — Feed: {feed}% | Rapid: {rapid}% | Spindle: {spindle}%"
        )

    def _dro_value_row(self, parent, axis, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=f"{axis}:", width=3).pack(side="left")
        ttk.Label(row, textvariable=var, width=10).pack(side="left")
        # Keep a hidden button area so the MPos rows mirror the WPos layout.
        btn = ttk.Button(
            row,
            text="",
            style=self.HIDDEN_MPOS_BUTTON_STYLE,
            state="disabled",
            width=9,
            takefocus=False,
        )
        btn.pack(side="right")

    def _dro_row(self, parent, axis, var, zero_cmd):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=f"{axis}:", width=3).pack(side="left")
        ttk.Label(row, textvariable=var, width=10).pack(side="left")
        btn = ttk.Button(row, text=f"Zero {axis}", command=zero_cmd)
        btn.pack(side="right")
        set_kb_id(btn, f"zero_{axis.lower()}")
        return btn

    # ---------- UI actions ----------
    def refresh_ports(self, auto_connect: bool = False):
        ports = self.grbl.list_ports()
        self.port_combo["values"] = ports
        if ports and self.current_port.get() not in ports:
            self.current_port.set(ports[0])
        if not ports:
            self.current_port.set("")
        if auto_connect and (not self.connected):
            last = self.settings.get("last_port", "").strip()
            if last and last in ports:
                self.current_port.set(last)
                try:
                    self.toggle_connect()
                except Exception:
                    pass

    def toggle_connect(self):
        if not self._ensure_serial_available():
            return
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before disconnecting.")
            return
        if self.connected:
            self._user_disconnect = True
            self._start_disconnect_worker()
            return
        else:
            port = self.current_port.get().strip()
        if not port:
            messagebox.showwarning("No port", "No serial port selected.")
            return
        self._start_connect_worker(port)

    def _start_connect_worker(self, port: str, *, show_error: bool = True, on_failure=None):
        if self._connecting:
            return
        def worker():
            try:
                self.grbl.connect(port, BAUD_DEFAULT)
            except Exception as exc:
                if show_error:
                    try:
                        self.after(0, lambda: messagebox.showerror("Connect failed", str(exc)))
                    except Exception:
                        pass
                if on_failure:
                    try:
                        self.after(0, lambda exc=exc: on_failure(exc))
                    except Exception:
                        pass
            finally:
                self._connecting = False

        self._connecting = True
        self._connect_thread = threading.Thread(target=worker, daemon=True)
        self._connect_thread.start()

    def _start_disconnect_worker(self):
        if self._disconnecting:
            return
        def worker():
            try:
                self.grbl.disconnect()
            except Exception as exc:
                self.ui_q.put(("log", f"[disconnect] {exc}"))
            finally:
                self._disconnecting = False

        self._disconnecting = True
        self._disconnect_thread = threading.Thread(target=worker, daemon=True)
        self._disconnect_thread.start()

    def _ensure_serial_available(self) -> bool:
        if serial is not None:
            return True
        msg = (
            "pyserial is required to communicate with GRBL. Install pyserial (pip install pyserial) "
            "and restart the application."
        )
        if SERIAL_IMPORT_ERROR:
            msg += f"\n{SERIAL_IMPORT_ERROR}"
        messagebox.showerror("Missing dependency", msg)
        return False

    def open_gcode(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            return
        path = filedialog.askopenfilename(
            title="Open G-code",
            initialdir=self.settings.get("last_gcode_dir", ""),
            filetypes=[("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        self._load_gcode_from_path(path)

    def run_job(self):
        self._reset_gcode_view_for_run()
        self._job_started_at = datetime.now()
        self._job_completion_notified = False
        self.grbl.start_stream()

    def pause_job(self):
        self.grbl.pause_stream()

    def resume_job(self):
        self.grbl.resume_stream()

    def stop_job(self):
        self.grbl.stop_stream()

    def _show_resume_dialog(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before resuming from a line.")
            return
        if not self.connected or not self._grbl_ready:
            messagebox.showwarning("Not ready", "Connect to GRBL before resuming.")
            return
        if self._alarm_locked:
            messagebox.showwarning("Alarm", "Clear the alarm before resuming.")
            return
        if not self._last_gcode_lines:
            messagebox.showwarning("No G-code", "Load a G-code file first.")
            return
        total_lines = len(self._last_gcode_lines)
        default_line = 1
        if self._last_acked_index >= 0:
            default_line = min(total_lines, self._last_acked_index + 2)

        dlg = tk.Toplevel(self)
        dlg.title("Resume from line")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Line number (1-{total_lines})").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        line_var = tk.StringVar(value=str(default_line))
        line_entry = ttk.Entry(frm, textvariable=line_var, width=10)
        line_entry.grid(row=0, column=1, sticky="w", pady=4)

        def use_last_acked():
            if self._last_acked_index >= 0:
                line_var.set(str(min(total_lines, self._last_acked_index + 2)))
                update_preview()

        ttk.Button(frm, text="Use last acked", command=use_last_acked).grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=4
        )
        sync_var = tk.BooleanVar(value=True)
        sync_chk = ttk.Checkbutton(frm, text="Send modal re-sync before resuming", variable=sync_var)
        sync_chk.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))
        preview_var = tk.StringVar(value="")
        warning_var = tk.StringVar(value="")
        preview_lbl = ttk.Label(frm, textvariable=preview_var, wraplength=460, justify="left")
        preview_lbl.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 2))
        warning_lbl = ttk.Label(frm, textvariable=warning_var, foreground="#b00020", wraplength=460, justify="left")
        warning_lbl.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))

        def update_preview():
            try:
                line_no = int(line_var.get())
            except Exception:
                preview_var.set("Enter a valid line number.")
                warning_var.set("")
                return
            if line_no < 1 or line_no > total_lines:
                preview_var.set("Line number is out of range.")
                warning_var.set("")
                return
            preamble, has_g92 = self._build_resume_preamble(self._last_gcode_lines, line_no - 1)
            if sync_var.get():
                if preamble:
                    preview_var.set("Modal re-sync: " + " ".join(preamble))
                else:
                    preview_var.set("Modal re-sync: (none)")
            else:
                preview_var.set("Modal re-sync: disabled")
            if has_g92:
                warning_var.set(
                    "Warning: G92 offsets appear before this line. Confirm work zero before resuming."
                )
            else:
                warning_var.set("")

        def on_start():
            try:
                line_no = int(line_var.get())
            except Exception:
                messagebox.showwarning("Resume", "Enter a valid line number.")
                return
            if line_no < 1 or line_no > total_lines:
                messagebox.showwarning("Resume", "Line number is out of range.")
                return
            preamble = []
            if sync_var.get():
                preamble, _ = self._build_resume_preamble(self._last_gcode_lines, line_no - 1)
            self._resume_from_line(line_no - 1, preamble)
            dlg.destroy()

        update_preview()
        line_entry.bind("<KeyRelease>", lambda _evt: update_preview())
        sync_chk.config(command=update_preview)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Button(btn_row, text="Start Resume", command=on_start).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _build_resume_preamble(self, lines: list[str], stop_index: int) -> tuple[list[str], bool]:
        units = None
        distance = None
        plane = None
        feed_mode = None
        arc_mode = None
        coord = None
        spindle = None
        coolant = None
        feed = None
        spindle_speed = None
        has_g92 = False

        def is_code(code: float, target: float) -> bool:
            return abs(code - target) < 1e-3

        for raw in lines[: max(0, stop_index)]:
            s = clean_gcode_line(raw)
            if not s:
                continue
            s = s.upper()
            for w, val in RESUME_WORD_PAT.findall(s):
                if w == "G":
                    try:
                        code = float(val)
                    except Exception:
                        continue
                    if is_code(code, 92) or is_code(code, 92.1) or is_code(code, 92.2) or is_code(code, 92.3):
                        has_g92 = True
                        continue
                    gstr = f"G{val}"
                    if is_code(code, 20) or is_code(code, 21):
                        units = gstr
                    elif is_code(code, 90) or is_code(code, 91):
                        distance = gstr
                    elif is_code(code, 17) or is_code(code, 18) or is_code(code, 19):
                        plane = gstr
                    elif is_code(code, 93) or is_code(code, 94):
                        feed_mode = gstr
                    elif is_code(code, 90.1) or is_code(code, 91.1):
                        arc_mode = gstr
                    elif (
                        is_code(code, 54)
                        or is_code(code, 55)
                        or is_code(code, 56)
                        or is_code(code, 57)
                        or is_code(code, 58)
                        or is_code(code, 59)
                        or is_code(code, 59.1)
                        or is_code(code, 59.2)
                        or is_code(code, 59.3)
                    ):
                        coord = gstr
                elif w == "M":
                    try:
                        code = int(float(val))
                    except Exception:
                        continue
                    if code in (3, 4, 5):
                        spindle = code
                    elif code in (7, 8, 9):
                        coolant = code
                elif w == "F":
                    try:
                        feed = float(val)
                    except Exception:
                        pass
                elif w == "S":
                    try:
                        spindle_speed = float(val)
                    except Exception:
                        pass

        preamble = []
        for item in (units, distance, plane, arc_mode, feed_mode, coord):
            if item:
                preamble.append(item)
        if feed is not None:
            preamble.append(f"F{feed:g}")
        if spindle is not None:
            if spindle in (3, 4):
                if spindle_speed is not None:
                    preamble.append(f"M{spindle} S{spindle_speed:g}")
                else:
                    preamble.append(f"M{spindle}")
            else:
                preamble.append("M5")
        if coolant is not None:
            preamble.append(f"M{coolant}")
        return preamble, has_g92

    def _resume_from_line(self, start_index: int, preamble: list[str]):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before resuming.")
            return
        if not self.connected or not self._grbl_ready:
            messagebox.showwarning("Not ready", "Connect to GRBL before resuming.")
            return
        if self._alarm_locked:
            messagebox.showwarning("Alarm", "Clear the alarm before resuming.")
            return
        if not self._last_gcode_lines:
            messagebox.showwarning("No G-code", "Load a G-code file first.")
            return
        if start_index < 0 or start_index >= len(self._last_gcode_lines):
            messagebox.showwarning("Resume", "Line number is out of range.")
            return
        self._clear_pending_ui_updates()
        self.gview.clear_highlights()
        self._last_sent_index = start_index - 1
        self._last_acked_index = start_index - 1
        if start_index > 0:
            self.gview.mark_acked_upto(start_index - 1)
        self.gview.highlight_current(start_index)
        if len(self._last_gcode_lines) > 0:
            pct = int(round((start_index / len(self._last_gcode_lines)) * 100))
            self.progress_pct.set(pct)
        self.status.config(text=f"Resuming at line {start_index + 1}")
        self.grbl.start_stream_from(start_index, preamble)

    def _reset_gcode_view_for_run(self):
        if not hasattr(self, "gview") or self.gview.lines_count <= 0:
            return
        self._clear_pending_ui_updates()
        self.gview.clear_highlights()
        self._last_sent_index = -1
        self._last_acked_index = -1
        self.gview.highlight_current(0)

    def _load_gcode_from_path(self, path: str):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Open G-code", "File not found.")
            return
        self.settings["last_gcode_dir"] = os.path.dirname(path)
        self._gcode_load_token += 1
        token = self._gcode_load_token
        self._gcode_loading = True
        self.btn_run.config(state="disabled")
        self.btn_pause.config(state="disabled")
        self.btn_resume.config(state="disabled")
        self.btn_resume_from.config(state="disabled")
        self.gcode_stats_var.set("Loading...")
        self.status.config(text=f"Loading: {os.path.basename(path)}")
        self._set_gcode_loading_indeterminate(f"Reading {os.path.basename(path)}")
        self.gview.set_lines_chunked([])

        def worker():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = []
                    for ln in f:
                        c = clean_gcode_line(ln)
                        if c:
                            lines.append(c)
                self.ui_q.put(("gcode_loaded", token, path, lines))
            except Exception as exc:
                self.ui_q.put(("gcode_load_error", token, path, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_loaded_gcode(self, path: str, lines: list[str]):
        if self.grbl.is_streaming():
            self._gcode_loading = False
            self._finish_gcode_loading()
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            self.status.config(text="G-code load skipped (streaming)")
            return
        self._clear_pending_ui_updates()
        self._last_gcode_lines = lines
        self._gcode_hash = _hash_lines(lines)
        self._stats_cache.clear()
        self._live_estimate_min = None
        self._refresh_gcode_stats_display()
        self.grbl.load_gcode(lines)
        self._last_sent_index = -1
        self._last_acked_index = -1
        self._update_gcode_stats(lines)
        if bool(self.render3d_enabled.get()):
            self.toolpath_panel.set_gcode_lines(lines)
        else:
            self.toolpath_panel.set_enabled(False)
        self.toolpath_panel.set_job_name(os.path.basename(path))
        self.status.config(text=f"Loaded: {os.path.basename(path)}  ({len(lines)} lines)")

        name = os.path.basename(path)

        def on_done():
            self._gcode_loading = False
            self._finish_gcode_loading()
            if (
                self.connected
                and lines
                and self._grbl_ready
                and self._status_seen
                and not self._alarm_locked
            ):
                self.btn_run.config(state="normal")
                self.btn_resume_from.config(state="normal")
            else:
                self.btn_run.config(state="disabled")
                self.btn_resume_from.config(state="disabled")

        def on_progress(done, total):
            self._set_gcode_loading_progress(done, total, name)

        if not lines:
            self.gview.set_lines([])
            self._set_gcode_loading_progress(0, 0, name)
            on_done()
            return

        chunk_size = 300 if len(lines) > 2000 else GCODE_VIEWER_CHUNK_SIZE_SMALL
        self._set_gcode_loading_progress(0, len(lines), name)
        self.gview.set_lines_chunked(
            lines,
            chunk_size=chunk_size,
            on_done=on_done,
            on_progress=on_progress,
        )

    def _clear_gcode(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before clearing the G-code file.")
            return
        self._last_gcode_lines = []
        self._gcode_hash = None
        self._stats_cache.clear()
        self.grbl.load_gcode([])
        self.gview.set_lines([])
        self.gcode_stats_var.set("No file loaded")
        self.progress_pct.set(0)
        self.status.config(text="G-code cleared")
        self.btn_run.config(state="disabled")
        self.btn_pause.config(state="disabled")
        self.btn_resume.config(state="disabled")
        self.btn_resume_from.config(state="disabled")
        self.toolpath_panel.clear()
        self._job_started_at = None
        self._job_completion_notified = False

    def _show_gcode_loading(self):
        if not hasattr(self, "gcode_load_bar"):
            return
        if self.gcode_load_bar.winfo_ismapped():
            return
        self.gcode_load_label.pack(side="right", padx=(6, 0))
        self.gcode_load_bar.pack(side="right")

    def _hide_gcode_loading(self):
        if not hasattr(self, "gcode_load_bar"):
            return
        if self.gcode_load_bar.winfo_ismapped():
            self.gcode_load_bar.stop()
            self.gcode_load_bar.pack_forget()
            self.gcode_load_label.pack_forget()
        self.gcode_load_var.set("")

    def _set_gcode_loading_indeterminate(self, text: str):
        self._show_gcode_loading()
        self.gcode_load_var.set(text)
        self.gcode_load_bar.config(mode="indeterminate")
        self.gcode_load_bar.start(10)

    def _set_gcode_loading_progress(self, done: int, total: int, name: str = ""):
        self._show_gcode_loading()
        self.gcode_load_bar.stop()
        display_total = int(total)
        bar_total = max(1, display_total)
        done = max(0, min(int(done), bar_total))
        display_done = min(int(done), display_total) if display_total > 0 else 0
        self.gcode_load_bar.config(mode="determinate", maximum=bar_total, value=done)
        if name:
            self.gcode_load_var.set(f"Loading {name}: {display_done}/{display_total}")
        else:
            self.gcode_load_var.set(f"Loading {display_done}/{display_total}")

    def _finish_gcode_loading(self):
        self._hide_gcode_loading()

    def _format_duration(self, seconds: int) -> str:
        total_minutes = int(round(seconds / 60)) if seconds else 0
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"Hours:{hours:02d} Minutes:{minutes:02d}"

    def _format_throughput(self, bps: float) -> str:
        if bps <= 0:
            return "TX: 0 B/s"
        if bps < 1024:
            return f"TX: {bps:.0f} B/s"
        if bps < 1024 * 1024:
            return f"TX: {bps / 1024.0:.1f} KB/s"
        return f"TX: {bps / (1024.0 * 1024.0):.2f} MB/s"

    def _estimate_factor_value(self) -> float:
        try:
            val = float(self.estimate_factor.get())
        except Exception:
            return 1.0
        if val <= 0:
            return 1.0
        return val

    def _refresh_gcode_stats_display(self):
        if not self._last_stats:
            return
        self.gcode_stats_var.set(self._format_gcode_stats_text(self._last_stats, self._last_rate_source))

    def _on_estimate_factor_change(self, _value=None):
        factor = self._estimate_factor_value()
        self._estimate_factor_label.set(f"{factor:.2f}x")
        self._refresh_gcode_stats_display()

    def _update_live_estimate(self, done: int, total: int):
        if self._stream_start_ts is None or done <= 0 or total <= 0:
            return
        now = time.time()
        paused_total = self._stream_pause_total
        if self._stream_paused_at is not None:
            paused_total += max(0.0, now - self._stream_paused_at)
        elapsed = max(0.0, now - self._stream_start_ts - paused_total)
        if elapsed < 1.0:
            return
        remaining = (elapsed / done) * total - elapsed
        if remaining < 0:
            remaining = 0.0
        self._live_estimate_min = remaining / 60.0
        self._refresh_gcode_stats_display()

    def _maybe_notify_job_completion(self, done: int, total: int) -> None:
        if (
            self._job_started_at is None
            or self._job_completion_notified
            or total <= 0
            or done < total
        ):
            return
        start_time = self._job_started_at
        finish_time = datetime.now()
        elapsed = finish_time - start_time
        elapsed_str = str(elapsed).split(".")[0]
        self._job_completion_notified = True
        self._job_started_at = None
        start_text = start_time.strftime("%Y-%m-%d %H:%M:%S")
        finish_text = finish_time.strftime("%Y-%m-%d %H:%M:%S")
        summary = (
            f"Job completed in {elapsed_str} "
            f"(started {start_text}, finished {finish_text})."
        )
        try:
            self.streaming_controller.handle_log(f"[job] {summary}")
        except Exception:
            pass
        message = (
            "Job completed.\n\n"
            f"Started: {start_text}\n"
            f"Finished: {finish_text}\n"
            f"Elapsed: {elapsed_str}"
        )
        if bool(self.job_completion_popup.get()):
            try:
                messagebox.showinfo("Job completed", message)
            except Exception:
                pass
        if bool(self.job_completion_beep.get()):
            try:
                self.bell()
            except Exception:
                pass

    def _format_gcode_stats_text(self, stats: dict, rate_source: str | None) -> str:
        bounds = stats.get("bounds")
        if not bounds:
            return "No toolpath data"
        minx, maxx, miny, maxy, minz, maxz = bounds
        factor = self._estimate_factor_value()
        time_min = stats.get("time_min")
        rapid_min = stats.get("rapid_min")
        if time_min is None:
            time_txt = "n/a"
        else:
            seconds = int(round(time_min * factor * 60))
            time_txt = self._format_duration(seconds)
        if rapid_min is None or time_min is None:
            total_txt = "n/a"
            if rate_source is None:
                total_txt = "n/a (not connected)"
        else:
            seconds = int(round((time_min + rapid_min) * factor * 60))
            total_txt = self._format_duration(seconds)
            if rate_source == "fallback":
                total_txt = f"{total_txt} (fallback)"
            elif rate_source == "profile":
                total_txt = f"{total_txt} (profile)"
        live_txt = ""
        if self._live_estimate_min is not None:
            live_seconds = int(round(self._live_estimate_min * factor * 60))
            live_txt = f" | Live est (stream): {self._format_duration(live_seconds)}"
        return (
            f"Bounds X[{minx:.3f}..{maxx:.3f}] "
            f"Y[{miny:.3f}..{maxy:.3f}] "
            f"Z[{minz:.3f}..{maxz:.3f}] | "
            f"Est time (feed only): {time_txt} | "
            f"Est time (with rapids): {total_txt}"
            f"{live_txt} | "
            "Approx"
        )

    def _apply_gcode_stats(self, token: int, stats: dict | None, rate_source: str | None):
        if token != self._stats_token:
            return
        self._last_stats = stats
        self._last_rate_source = rate_source
        if stats is None:
            self.gcode_stats_var.set("Estimate unavailable")
            return
        self._refresh_gcode_stats_display()

    def _get_fallback_rapid_rate(self) -> float | None:
        raw = self.fallback_rapid_rate.get().strip()
        if not raw:
            return None
        try:
            rate = float(raw)
        except Exception:
            return None
        if rate <= 0:
            return None
        return rate

    def _get_rapid_rates_for_estimate(self):
        if self._rapid_rates:
            return self._rapid_rates, "grbl"
        profile_rates = self._get_profile_rapid_rates()
        if profile_rates:
            return profile_rates, "profile"
        fallback = self._get_fallback_rapid_rate()
        if fallback:
            return (fallback, fallback, fallback), "fallback"
        return None, None

    def _get_accel_rates_for_estimate(self):
        return self._accel_rates

    def _make_stats_cache_key(
        self,
        rapid_rates: tuple[float, float, float] | None,
        accel_rates: tuple[float, float, float] | None,
    ):
        if not self._gcode_hash:
            return None
        rapid = tuple(rapid_rates) if rapid_rates is not None else None
        accel = tuple(accel_rates) if accel_rates is not None else None
        return (self._gcode_hash, rapid, accel)

    def _update_gcode_stats(self, lines: list[str]):
        if not lines:
            self._last_stats = None
            self._last_rate_source = None
            self.gcode_stats_var.set("No file loaded")
            return
        self._last_stats = None
        self._last_rate_source = None
        self._stats_token += 1
        token = self._stats_token
        rapid_rates, rate_source = self._get_rapid_rates_for_estimate()
        accel_rates = self._get_accel_rates_for_estimate()
        cache_key = self._make_stats_cache_key(rapid_rates, accel_rates)
        if cache_key and cache_key in self._stats_cache:
            stats, cached_source = self._stats_cache[cache_key]
            self._apply_gcode_stats(token, stats, cached_source)
            return
        if len(lines) > 2000:
            self.gcode_stats_var.set("Calculating stats...")

            def worker():
                try:
                    stats = compute_gcode_stats(lines, rapid_rates, accel_rates)
                except Exception as exc:
                    self.after(0, lambda: self._apply_gcode_stats(token, None, rate_source))
                    self.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
                    return
                if cache_key:
                    self._stats_cache[cache_key] = (stats, rate_source)
                self.after(0, lambda: self._apply_gcode_stats(token, stats, rate_source))

            threading.Thread(target=worker, daemon=True).start()
            return
        try:
            stats = compute_gcode_stats(lines, rapid_rates, accel_rates)
        except Exception as exc:
            self._apply_gcode_stats(token, None, rate_source)
            self.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
            return
        if cache_key:
            self._stats_cache[cache_key] = (stats, rate_source)
        self._apply_gcode_stats(token, stats, rate_source)

    def _load_grbl_setting_info(self):
        info = {}
        keys = []
        base_dir = os.path.dirname(__file__)
        csv_path = os.path.join(
            base_dir,
            "ref",
            "grbl-master",
            "grbl-master",
            "doc",
            "csv",
            "setting_codes_en_US.csv",
        )
        if os.path.isfile(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        code = row.get("$-Code", "").strip().strip('"')
                        if not code:
                            continue
                        try:
                            idx = int(code)
                        except Exception:
                            continue
                        key = f"${idx}"
                        name = (row.get(" Setting", "") or row.get("Setting", "")).strip().strip('"')
                        units = (row.get(" Units", "") or row.get("Units", "")).strip().strip('"')
                        desc = (row.get(" Setting Description", "") or row.get("Setting Description", "")).strip().strip('"')
                        info[key] = {
                            "name": name,
                            "units": units,
                            "desc": desc,
                            "tooltip": "",
                            "idx": idx,
                        }
                        keys.append(idx)
            except Exception:
                info = {}
                keys = []

        self._load_grbl_setting_tooltips(info, base_dir)

        if not info:
            for idx in GRBL_SETTING_KEYS:
                key = f"${idx}"
                info[key] = {
                    "name": GRBL_SETTING_DESC.get(idx, ""),
                    "units": "",
                    "desc": GRBL_SETTING_DESC.get(idx, ""),
                    "tooltip": "",
                    "idx": idx,
                }
            keys = GRBL_SETTING_KEYS[:]

        pocket_overrides = {
            0: ("Step Pulse Length", "Length of the step pulse delivered to drivers."),
            1: ("Step Idle Delay", "Time before steppers disable after motion (255 keeps enabled)."),
            2: ("Step Pulse Invert", "Invert step pulse signal. See axis config table."),
            3: ("Direction Invert", "Invert axis directions. See axis config table."),
            4: ("Step Enable Invert", "Invert the enable pin signal for drivers."),
            5: ("Limit Pins Invert", "Invert limit switch pins (requires pull-down)."),
            6: ("Probe Pin Invert", "Invert probe input (requires pull-down)."),
            10: ("Status Report Mask", "Select status report fields via bitmask."),
            11: ("Junction Deviation", "Cornering speed control; higher is faster, more risk."),
            12: ("Arc Tolerance", "Arc smoothing tolerance; lower is smoother."),
            13: ("Report Inches", "Status report units (0=mm, 1=inch)."),
            20: ("Soft Limits", "Enable soft limits (requires homing)."),
            21: ("Hard Limits", "Enable limit switch alarms."),
            22: ("Homing Cycle", "Enable the homing cycle."),
            23: ("Homing Dir Invert", "Homing direction mask. See axis config table."),
            24: ("Homing Feed", "Feed rate used for final homing locate."),
            25: ("Homing Seek", "Seek rate used to find the limit switch."),
            26: ("Homing Debounce", "Debounce delay for limit switches."),
            27: ("Homing Pull-off", "Pull-off distance after homing."),
            100: ("X Steps/mm", "Steps per mm for X axis."),
            101: ("Y Steps/mm", "Steps per mm for Y axis."),
            102: ("Z Steps/mm", "Steps per mm for Z axis."),
            110: ("X Max Rate", "Maximum rate for X axis."),
            111: ("Y Max Rate", "Maximum rate for Y axis."),
            112: ("Z Max Rate", "Maximum rate for Z axis."),
            120: ("X Max Accel", "Maximum acceleration for X axis."),
            121: ("Y Max Accel", "Maximum acceleration for Y axis."),
            122: ("Z Max Accel", "Maximum acceleration for Z axis."),
            130: ("X Max Travel", "Maximum travel for X axis."),
            131: ("Y Max Travel", "Maximum travel for Y axis."),
            132: ("Z Max Travel", "Maximum travel for Z axis."),
        }
        for idx, (name, desc) in pocket_overrides.items():
            key = f"${idx}"
            if key not in info:
                info[key] = {
                    "name": name,
                    "units": "",
                    "desc": desc,
                    "tooltip": "",
                    "idx": idx,
                }
            else:
                info[key]["name"] = name
                info[key]["desc"] = desc
            keys.append(idx)

        self._grbl_setting_info = info
        self._grbl_setting_keys = sorted(set(keys))

    def _load_grbl_setting_tooltips(self, info: dict, base_dir: str):
        md_path = os.path.join(
            base_dir,
            "ref",
            "grbl-master",
            "grbl-master",
            "doc",
            "markdown",
            "settings.md",
        )
        if not os.path.isfile(md_path):
            return
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                md = f.read()
        except Exception:
            return
        pattern = re.compile(r"^#### \$(\d+)[^\n]*\n(.*?)(?=^#### \$|\Z)", re.M | re.S)
        for match in pattern.finditer(md):
            idx = int(match.group(1))
            body = match.group(2).strip()
            if not body:
                continue
            lines = []
            for raw in body.splitlines():
                s = raw.strip()
                if not s:
                    if lines and lines[-1] != "":
                        lines.append("")
                    continue
                if s.startswith("|"):
                    continue
                if s.startswith(":"):
                    continue
                s = s.replace("`", "")
                lines.append(s)
            tooltip = "\n".join([ln for ln in lines if ln != ""]).strip()
            key = f"${idx}"
            if key in info and tooltip:
                info[key]["tooltip"] = tooltip

    def _setup_console_tags(self):
        text_fg = "#111111"
        try:
            self.console.tag_configure("console_tx", background="#e5efff", foreground=text_fg)       # light blue
            self.console.tag_configure("console_ok", background="#e6f7ed", foreground=text_fg)       # light green
            self.console.tag_configure("console_status", background="#fff4d8", foreground=text_fg)   # light orange
            self.console.tag_configure("console_error", background="#ffe5e5", foreground=text_fg)    # light red
            self.console.tag_configure("console_alarm", background="#ffd8d8", foreground=text_fg)    # light red/darker
        except Exception as exc:
            logger.exception("Failed to configure console tags: %s", exc)

    def _send_console(self):
        s = self.cmd_entry.get().strip()
        if not s:
            return
        self.grbl.send_immediate(s)
        self.cmd_entry.delete(0, "end")
    def _clear_console_log(self):
        if not messagebox.askyesno("Clear console", "Clear the console log?"):
            return
        self.streaming_controller.clear_console()

    def _save_console_log(self):
        path = filedialog.asksaveasfilename(
            title="Save console log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        # Save from stored console lines (position reports are excluded)
        data_lines = [
            text
            for text, tag in self.streaming_controller.get_console_lines()
            if self.streaming_controller.matches_filter((text, tag), for_save=True)
            and (not self.streaming_controller.is_position_line(text))
        ]
        data = "\n".join(data_lines)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _request_settings_dump(self):
        if not self.grbl.is_connected():
            messagebox.showwarning("Not connected", "Connect to GRBL first.")
            return
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before requesting settings.")
            return
        if not self._grbl_ready:
            self._pending_settings_refresh = True
            self.status.config(text="Waiting for Grbl startup...")
            return
        if self._alarm_locked:
            messagebox.showwarning("Alarm", "Clear alarm before requesting settings.")
            return
        self.streaming_controller.log(f"[{time.strftime('%H:%M:%S')}] Settings refresh requested ($$).")
        self.settings_controller.start_capture("Requesting $$...")
        self.grbl.send_immediate("$$")



    def _maybe_auto_reconnect(self):
        if self.connected or self._closing or (not self._auto_reconnect_pending):
            return
        if self._connecting:
            return
        if not self._auto_reconnect_last_port:
            return
        try:
            if not bool(self.reconnect_on_open.get()):
                self._auto_reconnect_pending = False
                return
        except Exception:
            pass
        now = time.time()
        if now < self._auto_reconnect_next_ts:
            return
        ports = self.grbl.list_ports()
        if self._auto_reconnect_last_port not in ports:
            # If we've exceeded retries, allow a cool-down retry later.
            if self._auto_reconnect_retry >= self._auto_reconnect_max_retry:
                self._auto_reconnect_next_ts = now + max(30.0, self._auto_reconnect_delay)
                self._auto_reconnect_pending = True
            else:
                self._auto_reconnect_next_ts = now + self._auto_reconnect_delay
            return
        self._auto_reconnect_last_attempt = now
        self.current_port.set(self._auto_reconnect_last_port)
        self._auto_reconnect_next_ts = now + self._auto_reconnect_delay
        self._start_connect_worker(
            self._auto_reconnect_last_port,
            show_error=False,
            on_failure=self._handle_auto_reconnect_failure,
        )

    def _handle_auto_reconnect_failure(self, exc: Exception):
        now = time.time()
        self.ui_q.put(("log", f"[auto-reconnect] Attempt failed: {exc}"))
        self._auto_reconnect_retry += 1
        if self._auto_reconnect_retry > self._auto_reconnect_max_retry:
            self._auto_reconnect_delay = 30.0
        else:
            self._auto_reconnect_delay = min(30.0, self._auto_reconnect_delay * 1.5)
        self._auto_reconnect_next_ts = now + self._auto_reconnect_delay
        self._auto_reconnect_pending = True

    def _set_unit_mode(self, mode: str):
        self.unit_mode.set(mode)
        with self.macro_executor.macro_vars() as macro_vars:
            macro_vars["units"] = "G21" if mode == "mm" else "G20"
        try:
            self.btn_unit_toggle.config(text="mm" if mode == "mm" else "inch")
        except Exception:
            pass

    def _set_step_xy(self, value: float):
        self.step_xy.set(value)
        for v, btn in self._xy_step_buttons:
            btn.config(state="disabled" if v == value else "normal")

    def _set_step_z(self, value: float):
        self.step_z.set(value)
        for v, btn in self._z_step_buttons:
            btn.config(state="disabled" if v == value else "normal")

    def _set_manual_controls_enabled(self, enabled: bool):
        if getattr(self, "_alarm_locked", False):
            for w in self._manual_controls:
                try:
                    if w is getattr(self, "btn_all_stop", None):
                        continue
                    if w is getattr(self, "btn_home_mpos", None):
                        w.config(state="normal")
                        continue
                    if w is getattr(self, "btn_unlock_mpos", None):
                        w.config(state="normal")
                        continue
                    if w is getattr(self, "btn_unlock_top", None):
                        w.config(state="normal")
                        continue
                    w.config(state="disabled")
                except tk.TclError:
                    pass
            return
        state = "normal" if enabled else "disabled"
        for w in self._manual_controls:
            try:
                if not enabled and w is getattr(self, "btn_all_stop", None):
                    continue
                if not enabled and w in self._override_controls:
                    continue
                w.config(state=state)
            except tk.TclError:
                pass
        if enabled:
            self._set_unit_mode(self.unit_mode.get())
            self._set_step_xy(self.step_xy.get())
            self._set_step_z(self.step_z.get())

    def _set_streaming_lock(self, locked: bool):
        state = "disabled" if locked else "normal"
        try:
            self.btn_conn.config(state=state)
        except Exception:
            pass
        try:
            self.btn_refresh.config(state=state)
        except Exception:
            pass
        try:
            self.port_combo.config(state="disabled" if locked else "readonly")
        except Exception:
            pass

    def _format_alarm_message(self, message: str | None) -> str:
        if not message:
            return "ALARM"
        text = str(message).strip()
        if text.lower().startswith("alarm"):
            return text
        if "reset to continue" in text.lower():
            return "ALARM: Reset to continue"
        if text.startswith("[MSG:"):
            return f"ALARM: {text}"
        return f"ALARM: {text}"

    def _set_alarm_lock(self, locked: bool, message: str | None = None):
        if locked:
            self._alarm_locked = True
            if message:
                self._alarm_message = message
            self.btn_run.config(state="disabled")
            self.btn_pause.config(state="disabled")
            self.btn_resume.config(state="disabled")
            try:
                self.btn_resume_from.config(state="disabled")
            except Exception:
                pass
            try:
                self.btn_alarm_recover.config(state="normal")
            except Exception:
                pass
            self._set_manual_controls_enabled(True)
            try:
                self.status.config(text=self._format_alarm_message(message or self._alarm_message))
            except Exception:
                pass
            self._machine_state_text = "Alarm"
            self.machine_state.set("Alarm")
            self._start_state_flash("#ff5252")
            return

        if not self._alarm_locked:
            return
        self._alarm_locked = False
        self._alarm_message = ""
        try:
            self.btn_alarm_recover.config(state="disabled")
        except Exception:
            pass
        if (
            self.connected
            and self._grbl_ready
            and self._status_seen
            and self._stream_state not in ("running", "paused")
        ):
            self._set_manual_controls_enabled(True)
            if self.gview.lines_count:
                self.btn_run.config(state="normal")
                self.btn_resume_from.config(state="normal")
        status_text = ""
        try:
            status_text = self.status.cget("text")
        except Exception:
            pass
        if self.connected and status_text.startswith("ALARM"):
            self.status.config(text=f"Connected: {self._connected_port}")
        if self._pending_settings_refresh and self._grbl_ready:
            self._pending_settings_refresh = False
            self._request_settings_dump()
        self.machine_state.set(self._machine_state_text)
        self._update_state_highlight(self._machine_state_text)
        self._apply_status_poll_profile()

    def _show_alarm_recovery(self):
        if not self._alarm_locked:
            messagebox.showinfo("Alarm recovery", "No active alarm.")
            return
        msg = self._format_alarm_message(self._alarm_message)
        dlg = tk.Toplevel(self)
        dlg.title("Alarm recovery")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=msg, wraplength=460, justify="left").pack(fill="x", pady=(0, 8))
        ttk.Label(
            frm,
            text="Suggested steps: Unlock ($X) to clear the alarm, then Home ($H) if required. "
            "If motion feels unsafe, use Reset (Ctrl-X).",
            wraplength=460,
            justify="left",
        ).pack(fill="x", pady=(0, 10))
        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x")

        def run_and_close(action):
            try:
                action()
            except Exception as exc:
                logger.exception("Alarm recovery action failed: %s", exc)
            try:
                dlg.destroy()
            except Exception as exc:
                logger.exception("Failed to close alarm recovery dialog: %s", exc)

        ttk.Button(btn_row, text="Unlock ($X)", command=lambda: run_and_close(self.grbl.unlock)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="Home ($H)", command=lambda: run_and_close(self.grbl.home)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="Reset", command=lambda: run_and_close(self.grbl.reset)).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side="left")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _sync_all_stop_mode_combo(self):
        mode = self.all_stop_mode.get()
        label = None
        for lbl, code in ALL_STOP_CHOICES:
            if code == mode:
                label = lbl
                break
        if label is None and ALL_STOP_CHOICES:
            label = ALL_STOP_CHOICES[0][0]
            self.all_stop_mode.set(ALL_STOP_CHOICES[0][1])
        if hasattr(self, "all_stop_combo"):
            self.all_stop_combo.set(label if label else "")

    def _on_all_stop_mode_change(self, _event=None):
        label = ""
        if hasattr(self, "all_stop_combo"):
            label = self.all_stop_combo.get()
        mode = next((code for lbl, code in ALL_STOP_CHOICES if lbl == label), "stop_reset")
        self.all_stop_mode.set(mode)
        self.status.config(text=f"All Stop mode: {label}")

    def _sync_current_line_mode_combo(self):
        mode = self.current_line_mode.get()
        label = None
        for lbl, code in CURRENT_LINE_CHOICES:
            if code == mode:
                label = lbl
                break
        if label is None and CURRENT_LINE_CHOICES:
            label = CURRENT_LINE_CHOICES[0][0]
            self.current_line_mode.set(CURRENT_LINE_CHOICES[0][1])
        if hasattr(self, "current_line_combo"):
            self.current_line_combo.set(label if label else "")

    def _on_current_line_mode_change(self, _event=None):
        label = ""
        if hasattr(self, "current_line_combo"):
            label = self.current_line_combo.get()
        mode = next((code for lbl, code in CURRENT_LINE_CHOICES if lbl == label), "acked")
        self.current_line_mode.set(mode)
        self._update_current_highlight()

    def _toggle_keyboard_bindings(self):
        current = bool(self.keyboard_bindings_enabled.get())
        new_val = not current
        self.keyboard_bindings_enabled.set(new_val)
        if hasattr(self, "btn_toggle_keybinds"):
            self.btn_toggle_keybinds.config(text="Keybindings: On" if new_val else "Keybindings: Off")
        self._apply_keyboard_bindings()

    def _on_keyboard_bindings_check(self):
        new_val = bool(self.keyboard_bindings_enabled.get())
        if hasattr(self, "btn_toggle_keybinds"):
            self.btn_toggle_keybinds.config(text="Keybindings: On" if new_val else "Keybindings: Off")
        self._apply_keyboard_bindings()

    def _apply_keyboard_bindings(self):
        if "<KeyPress>" in self._bound_key_sequences:
            self.unbind_all("<KeyPress>")
            self._bound_key_sequences.clear()
        self._key_sequence_map = {}
        self._kb_conflicts = set()
        for btn in self._collect_buttons():
            binding_id = self._button_binding_id(btn)
            if binding_id in self._key_bindings:
                label = self._normalize_key_label(str(self._key_bindings.get(binding_id, "")).strip())
                if not label:
                    continue
                is_custom = True
            else:
                label = self._default_key_for_button(btn)
                if not label:
                    continue
                is_custom = False
            seq = self._key_sequence_tuple(label)
            if not seq:
                continue
            conflict_seq = self._sequence_conflict(seq, self._key_sequence_map)
            if conflict_seq:
                other_btn = self._key_sequence_map.get(conflict_seq)
                other_id = self._button_binding_id(other_btn) if other_btn else ""
                if binding_id in self._key_bindings:
                    self._key_bindings[binding_id] = ""
                self._kb_conflicts.add(binding_id)
                if other_id:
                    if other_id in self._key_bindings:
                        self._key_bindings[other_id] = ""
                    self._kb_conflicts.add(other_id)
                    self._key_sequence_map.pop(conflict_seq, None)
                continue
            self._key_sequence_map[seq] = btn
        self._refresh_keyboard_table()
        if not bool(self.keyboard_bindings_enabled.get()):
            self._clear_key_sequence_buffer()
            return
        self._bound_key_sequences.add("<KeyPress>")
        self.bind_all("<KeyPress>", self._on_key_sequence, add="+")

    def _refresh_keyboard_table(self):
        if not hasattr(self, "kb_table"):
            return
        self.kb_table.delete(*self.kb_table.get_children())
        self.kb_table.tag_configure("conflict", background="#f7d6d6")
        self._kb_item_to_button = {}
        for btn in self._collect_buttons():
            binding_id = self._button_binding_id(btn)
            label = self._button_label(btn)
            tip = getattr(btn, "_tooltip_text", "")
            if tip:
                label = f"{label} - {tip}"
            axis = self._button_axis_name(btn)
            key = self._keyboard_key_for_button(btn)
            if not key:
                key = "None"
            tags = ("conflict",) if binding_id in self._kb_conflicts else ()
            item = self.kb_table.insert(
                "",
                "end",
                values=(label, axis, key, f"{CLEAR_ICON}  Remove/Clear Binding"),
                tags=tags,
            )
            self._kb_item_to_button[item] = btn

    def _collect_buttons(self) -> list:
        buttons = []
        seen = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, tk.Button, StopSignButton)):
                    if child not in seen:
                        seen.add(child)
                        buttons.append(child)
                walk(child)

        walk(self)
        buttons.sort(key=self._button_label)
        return buttons

    def _button_label(self, btn) -> str:
        label = ""
        try:
            label = btn.cget("text")
        except Exception:
            label = ""
        if not label:
            label = getattr(btn, "_text", "")
        if not label:
            label = getattr(btn, "_label", "")
        if not label:
            label = btn.winfo_name()
        label = label.replace("\n", " ").strip()
        if label.startswith("!"):
            tooltip = getattr(btn, "_tooltip_text", "")
            kb_id = getattr(btn, "_kb_id", "")
            meta = tooltip or kb_id or label
            label = f"{btn.winfo_class()} ({meta})"
        return label

    def _keyboard_key_for_button(self, btn) -> str:
        binding_id = self._button_binding_id(btn)
        if binding_id in self._kb_conflicts:
            return ""
        if binding_id in self._key_bindings:
            return self._normalize_key_label(str(self._key_bindings.get(binding_id, "")).strip())
        return self._default_key_for_button(btn)

    def _button_axis_name(self, btn) -> str:
        xy_buttons = {b for _, b in self._xy_step_buttons}
        z_buttons = {b for _, b in self._z_step_buttons}
        if btn in xy_buttons:
            return "XY"
        if btn in z_buttons:
            return "Z"
        return ""

    def _button_binding_id(self, btn) -> str:
        kb_id = getattr(btn, "_kb_id", "")
        if kb_id:
            return kb_id
        label = self._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        name = btn.winfo_name()
        return f"{label}|{tip}|{name}"

    def _find_binding_conflict(self, target_btn, label: str):
        seq = self._key_sequence_tuple(label)
        if not seq:
            return None
        for btn in self._collect_buttons():
            if btn is target_btn:
                continue
            other_seq = self._key_sequence_tuple(self._keyboard_key_for_button(btn))
            if other_seq and self._sequence_conflict_pair(seq, other_seq):
                return btn
        return None

    def _default_key_for_button(self, btn) -> str:
        if btn is getattr(self, "btn_jog_cancel", None):
            return "Space"
        if btn is getattr(self, "btn_all_stop", None):
            return "Enter"
        return ""

    def _on_kb_table_double_click(self, event):
        if not hasattr(self, "kb_table"):
            return
        row = self.kb_table.identify_row(event.y)
        col = self.kb_table.identify_column(event.x)
        if not row or col != "#3":
            return
        self._start_kb_edit(row, col)

    def _on_kb_table_click(self, event):
        if not hasattr(self, "kb_table"):
            return
        row = self.kb_table.identify_row(event.y)
        col = self.kb_table.identify_column(event.x)
        if not row or col != "#4":
            if row and col == "#3":
                self._start_kb_edit(row, col)
            return
        btn = self._kb_item_to_button.get(row)
        if btn is None:
            return
        binding_id = self._button_binding_id(btn)
        self._key_bindings[binding_id] = ""
        self._apply_keyboard_bindings()

    def _start_kb_edit(self, row, col):
        bbox = self.kb_table.bbox(row, col)
        if not bbox:
            return
        if self._kb_edit is not None:
            try:
                self._kb_edit.destroy()
            except Exception:
                pass
            self._kb_edit = None
        x, y, w, h = bbox
        value = self.kb_table.set(row, "key")
        entry = ttk.Entry(self.kb_table)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, "Press keys...")
        entry._kb_prev = "" if value == "None" else value
        entry._kb_placeholder = True
        entry._kb_seq = []
        entry._kb_after_id = None
        entry.focus()
        entry.bind("<KeyPress>", lambda e: self._kb_capture_key(e, row, entry))
        entry.bind("<FocusOut>", lambda e: self._commit_kb_edit(row, entry))
        self._kb_edit = entry

    def _kb_capture_key(self, event, row, entry):
        if event.keysym in ("Escape",):
            try:
                entry.destroy()
            except Exception:
                pass
            self._kb_edit = None
            return "break"
        if event.keysym in ("BackSpace", "Delete"):
            self._commit_kb_edit(row, entry, label_override="")
            return "break"
        label = self._event_to_binding_label(event)
        if not label:
            return "break"
        seq = getattr(entry, "_kb_seq", [])
        if len(seq) >= 3:
            return "break"
        seq.append(label)
        entry._kb_seq = seq
        entry._kb_placeholder = False
        entry.delete(0, "end")
        entry.insert(0, " ".join(seq))
        if entry._kb_after_id is not None:
            entry.after_cancel(entry._kb_after_id)
        if len(seq) >= 3:
            self._commit_kb_edit(row, entry, label_override=" ".join(seq))
            return "break"
        entry._kb_after_id = entry.after(
            int(self._key_sequence_timeout * 1000),
            lambda: self._commit_kb_edit(row, entry, label_override=" ".join(seq)),
        )
        return "break"

    def _commit_kb_edit(self, row, entry, label_override: str | None = None):
        if self._kb_edit is None:
            return
        if label_override is None:
            try:
                new_val = entry.get()
            except Exception:
                new_val = ""
        else:
            new_val = label_override
        try:
            if hasattr(entry, "_kb_after_id") and entry._kb_after_id is not None:
                entry.after_cancel(entry._kb_after_id)
            entry.destroy()
        except Exception:
            pass
        self._kb_edit = None
        if label_override is None and getattr(entry, "_kb_placeholder", False):
            if new_val.strip() == "Press keys...":
                return
        btn = self._kb_item_to_button.get(row)
        if btn is None:
            return
        label = self._normalize_key_label(new_val)
        binding_id = self._button_binding_id(btn)
        self._key_bindings[binding_id] = label
        self._apply_keyboard_bindings()

    def _normalize_key_label(self, text: str) -> str:
        raw = text.strip()
        if not raw:
            return ""
        chunks = [c for c in raw.replace(",", " ").split() if c.strip()]
        seq = []
        for chunk in chunks:
            chord = self._normalize_key_chord(chunk)
            if chord:
                seq.append(chord)
            if len(seq) >= 3:
                break
        return " ".join(seq)

    def _normalize_key_chord(self, text: str) -> str:
        raw = text.strip()
        if not raw:
            return ""
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        aliases = {
            "SPACE": "Space",
            "SPC": "Space",
            "ENTER": "Enter",
            "RETURN": "Enter",
            "ESC": "Escape",
            "ESCAPE": "Escape",
            "TAB": "Tab",
            "BACKSPACE": "Backspace",
            "DEL": "Delete",
            "DELETE": "Delete",
            "NONE": "",
            "CTRL": "Ctrl",
            "CONTROL": "Ctrl",
            "SHIFT": "Shift",
            "ALT": "Alt",
            "OPTION": "Alt",
        }
        mods = []
        key = ""
        for part in parts:
            up = part.upper()
            if up in aliases:
                mapped = aliases[up]
                if mapped in ("Ctrl", "Shift", "Alt"):
                    if mapped not in mods:
                        mods.append(mapped)
                elif mapped:
                    key = mapped
                continue
            if len(part) == 1:
                key = part.upper()
            else:
                key = part
        if not key:
            return ""
        mod_order = ("Ctrl", "Shift", "Alt")
        ordered_mods = [m for m in mod_order if m in mods]
        return "+".join(ordered_mods + [key])

    def _key_sequence_tuple(self, label: str) -> tuple[str, ...] | None:
        normalized = self._normalize_key_label(label)
        if not normalized:
            return None
        parts = [p for p in normalized.split(" ") if p]
        return tuple(parts[:3])

    def _event_to_binding_label(self, event) -> str:
        keysym = event.keysym
        if keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return ""
        mods = []
        if event.state & 0x4:
            mods.append("Ctrl")
        if event.state & 0x1:
            mods.append("Shift")
        alt_mask = 0x8 | 0x20000
        if event.state & alt_mask:
            mods.append("Alt")
        key_label = self._normalize_key_chord(keysym)
        if not key_label:
            return ""
        if mods:
            return "+".join(mods + [key_label])
        return key_label

    def _sequence_conflict_pair(self, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
        if not seq_a or not seq_b:
            return False
        min_len = min(len(seq_a), len(seq_b))
        return seq_a[:min_len] == seq_b[:min_len]

    def _sequence_conflict(self, seq: tuple[str, ...], existing: dict):
        for other_seq in existing.keys():
            if self._sequence_conflict_pair(seq, other_seq):
                return other_seq
        return None

    def _on_key_sequence(self, event):
        if not self._keyboard_binding_allowed():
            return
        label = self._event_to_binding_label(event)
        if not label:
            return
        now = time.time()
        if now - self._key_sequence_last_time > self._key_sequence_timeout:
            self._key_sequence_buffer = []
        self._key_sequence_last_time = now
        self._key_sequence_buffer.append(label)
        if len(self._key_sequence_buffer) > 3:
            self._key_sequence_buffer = self._key_sequence_buffer[-3:]
        if self._key_sequence_after_id is not None:
            self.after_cancel(self._key_sequence_after_id)
            self._key_sequence_after_id = None
        seq = tuple(self._key_sequence_buffer)
        btn = self._key_sequence_map.get(seq)
        if btn is not None:
            self._key_sequence_buffer = []
            self._on_key_binding(btn)
            return
        self._key_sequence_after_id = self.after(
            int(self._key_sequence_timeout * 1000),
            self._clear_key_sequence_buffer,
        )

    def _clear_key_sequence_buffer(self):
        self._key_sequence_buffer = []
        if self._key_sequence_after_id is not None:
            try:
                self.after_cancel(self._key_sequence_after_id)
            except Exception:
                pass
        self._key_sequence_after_id = None

    def _keyboard_binding_allowed(self) -> bool:
        if not bool(self.keyboard_bindings_enabled.get()):
            return False
        try:
            current_grab = self.grab_current()
        except Exception:
            current_grab = None
        if current_grab is not None:
            return False
        try:
            widget = self.focus_get()
        except Exception:
            return False
        if widget is None:
            return True
        try:
            if widget.winfo_toplevel() is not self:
                return False
        except Exception:
            return False
        cls = widget.winfo_class()
        if cls in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox"):
            return False
        return True

    def _on_key_jog_stop(self, _event=None):
        if not self._keyboard_binding_allowed():
            return
        try:
            if self.btn_jog_cancel.cget("state") == "disabled":
                return
        except Exception:
            return
        self.grbl.jog_cancel()

    def _on_key_all_stop(self, _event=None):
        if not self._keyboard_binding_allowed():
            return
        try:
            if self.btn_all_stop.cget("state") == "disabled":
                return
        except Exception:
            return
        self._all_stop_action()

    def _on_key_binding(self, btn):
        if not self._keyboard_binding_allowed():
            return
        try:
            if btn.cget("state") == "disabled":
                return
        except Exception:
            return
        self._log_button_action(btn)
        self._invoke_button(btn)

    def _invoke_button(self, btn):
        if hasattr(btn, "invoke"):
            try:
                btn.invoke()
                return
            except Exception:
                pass
        try:
            cmd = btn.cget("command")
        except Exception:
            cmd = None
        if callable(cmd):
            cmd()

    def _log_button_action(self, btn):
        if not bool(self.gui_logging_enabled.get()):
            return
        label = self._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        gcode = ""
        try:
            getter = getattr(btn, "_log_gcode_get", None)
            if callable(getter):
                gcode = getter()
            elif isinstance(getter, str):
                gcode = getter
        except Exception:
            gcode = ""
        ts = time.strftime("%H:%M:%S")
        if tip and gcode:
            self.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self.streaming_controller.log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self.streaming_controller.log(f"[{ts}] Button: {label}")

    def _update_current_highlight(self):
        if not hasattr(self, "gview") or self.gview is None or self.gview.lines_count <= 0:
            return
        max_idx = self.gview.lines_count - 1
        mode = self.current_line_mode.get()
        target_idx = None
        if mode == "acked":
            desired = self._last_acked_index + 1
            if desired < 0:
                desired = 0
            target_idx = min(desired, max_idx)
        else:
            if self._last_sent_index >= 0:
                target_idx = min(self._last_sent_index, max_idx)
            elif self._last_acked_index >= 0:
                candidate = self._last_acked_index + 1
                target_idx = min(candidate, max_idx)
        if target_idx is not None:
            self.gview.highlight_current(target_idx)

    def _all_stop_action(self):
        mode = self.all_stop_mode.get()
        if mode == "reset":
            self.grbl.reset()
        elif mode == "stop_reset":
            self.grbl.stop_stream()
            self.grbl.reset()
        else:
            self.grbl.stop_stream()

    def _all_stop_gcode_label(self):
        mode = self.all_stop_mode.get()
        if mode == "reset":
            return "Ctrl-X"
        return "Stop stream + Ctrl-X"

    def _on_fallback_rate_change(self, _event=None):
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)

    def _validate_jog_feed_var(self, var: tk.DoubleVar, fallback_default: float):
        try:
            val = float(var.get())
        except Exception:
            val = None
        if val is None or val <= 0:
            try:
                fallback = float(fallback_default)
            except Exception:
                fallback = fallback_default
            var.set(fallback)
            return
        var.set(val)

    def _on_jog_feed_change_xy(self, _event=None):
        self._validate_jog_feed_var(self.jog_feed_xy, self.settings.get("jog_feed_xy", 4000.0))

    def _on_jog_feed_change_z(self, _event=None):
        self._validate_jog_feed_var(self.jog_feed_z, self.settings.get("jog_feed_z", 500.0))

    def _on_status_interval_change(self, _event=None):
        try:
            val = float(self.status_poll_interval.get())
        except Exception:
            val = self.settings.get("status_poll_interval", STATUS_POLL_DEFAULT)
        if val <= 0:
            val = STATUS_POLL_DEFAULT
        if val < 0.05:
            val = 0.05
        self.status_poll_interval.set(val)
        self._apply_status_poll_profile()

    def _on_status_failure_limit_change(self, _event=None):
        try:
            limit = int(self.status_query_failure_limit.get())
        except Exception:
            limit = self.settings.get("status_query_failure_limit", 3)
        if limit < 1:
            limit = 1
        if limit > 10:
            limit = 10
        self.status_query_failure_limit.set(limit)
        try:
            self.grbl.set_status_query_failure_limit(limit)
        except Exception as exc:
            logger.exception("Failed to set status failure limit: %s", exc)

    def _apply_error_dialog_settings(self, _event=None):
        def coerce_float(var, fallback):
            try:
                value = float(var.get())
            except Exception:
                value = fallback
            if value <= 0:
                value = fallback
            return value

        def coerce_int(var, fallback):
            try:
                value = int(var.get())
            except Exception:
                value = fallback
            if value <= 0:
                value = fallback
            return value

        interval = coerce_float(self.error_dialog_interval_var, self._error_dialog_interval)
        burst_window = coerce_float(self.error_dialog_burst_window_var, self._error_dialog_burst_window)
        burst_limit = coerce_int(self.error_dialog_burst_limit_var, self._error_dialog_burst_limit)
        self._error_dialog_interval = interval
        self._error_dialog_burst_window = burst_window
        self._error_dialog_burst_limit = burst_limit
        self.error_dialog_interval_var.set(interval)
        self.error_dialog_burst_window_var.set(burst_window)
        self.error_dialog_burst_limit_var.set(burst_limit)
        self._reset_error_dialog_state()

    def _effective_status_poll_interval(self) -> float:
        try:
            base = float(self.status_poll_interval.get())
        except Exception:
            base = STATUS_POLL_DEFAULT
        if base <= 0:
            base = STATUS_POLL_DEFAULT
        if not bool(self.performance_mode.get()):
            return base
        stream_state = getattr(self, "_stream_state", None)
        alarm_locked = getattr(self, "_alarm_locked", False)
        is_connected = getattr(self, "connected", False)
        if stream_state == "running":
            return max(base, 0.25)
        if stream_state == "paused":
            return max(base, 0.3)
        if alarm_locked:
            return max(base, 0.5)
        if is_connected:
            return max(base * 2.0, 0.5)
        return base

    def _apply_status_poll_profile(self):
        interval = self._effective_status_poll_interval()
        self.grbl.set_status_poll_interval(interval)

    def _load_machine_profiles(self) -> list[dict]:
        raw = self.settings.get("machine_profiles", [])
        profiles: list[dict] = []
        if not isinstance(raw, list):
            return profiles
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            units = str(item.get("units", "mm")).lower()
            units = "inch" if units.startswith("in") else "mm"
            rates = item.get("max_rates", {})
            if not isinstance(rates, dict):
                rates = {}
            def to_float(value):
                try:
                    return float(value)
                except Exception:
                    return None
            rx = to_float(rates.get("x"))
            ry = to_float(rates.get("y"))
            rz = to_float(rates.get("z"))
            profiles.append(
                {
                    "name": name,
                    "units": units,
                    "max_rates": {"x": rx, "y": ry, "z": rz},
                }
            )
        return profiles

    def _get_profile_by_name(self, name: str):
        if not name:
            return None
        name = str(name).strip()
        for profile in self._machine_profiles:
            if profile.get("name") == name:
                return profile
        return None

    def _profile_units_scale(self, units: str) -> float:
        return 25.4 if str(units).lower().startswith("in") else 1.0

    def _get_profile_rapid_rates(self):
        profile = self._get_profile_by_name(self.active_profile_name.get())
        if not profile:
            return None
        rates = profile.get("max_rates", {})
        try:
            rx = float(rates.get("x"))
            ry = float(rates.get("y"))
            rz = float(rates.get("z"))
        except Exception:
            return None
        if rx <= 0 or ry <= 0 or rz <= 0:
            return None
        scale = self._profile_units_scale(profile.get("units", "mm"))
        return (rx * scale, ry * scale, rz * scale)

    def _refresh_profile_combo(self):
        names = [p.get("name", "") for p in self._machine_profiles]
        if hasattr(self, "profile_combo"):
            self.profile_combo["values"] = names
        current = self.active_profile_name.get()
        if current not in names:
            if names:
                self.active_profile_name.set(names[0])
            else:
                self.active_profile_name.set("")

    def _apply_profile_to_vars(self, profile: dict | None):
        if not profile:
            self.profile_name_var.set("")
            self.profile_units_var.set("mm")
            self.profile_rate_x_var.set("")
            self.profile_rate_y_var.set("")
            self.profile_rate_z_var.set("")
            if hasattr(self, "profile_rate_units"):
                self.profile_rate_units.config(text="mm/min")
            return
        self.profile_name_var.set(profile.get("name", ""))
        units = profile.get("units", "mm")
        self.profile_units_var.set(units)
        rates = profile.get("max_rates", {})
        self.profile_rate_x_var.set("" if rates.get("x") is None else str(rates.get("x")))
        self.profile_rate_y_var.set("" if rates.get("y") is None else str(rates.get("y")))
        self.profile_rate_z_var.set("" if rates.get("z") is None else str(rates.get("z")))
        self._update_profile_units_label()

    def _update_profile_units_label(self):
        units = str(self.profile_units_var.get()).lower()
        label = "in/min" if units.startswith("in") else "mm/min"
        if hasattr(self, "profile_rate_units"):
            try:
                self.profile_rate_units.config(text=label)
            except Exception:
                pass

    def _on_profile_units_change(self, _event=None):
        self._update_profile_units_label()

    def _apply_profile_units(self, profile: dict | None):
        if not profile:
            return
        units = profile.get("units", "mm")
        if units not in ("mm", "inch"):
            units = "mm"
        self._set_unit_mode(units)

    def _on_profile_select(self, _event=None):
        name = self.active_profile_name.get()
        profile = self._get_profile_by_name(name)
        if not profile:
            return
        self._apply_profile_to_vars(profile)
        self._apply_profile_units(profile)
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)

    def _new_profile(self):
        try:
            self.profile_combo.set("")
        except Exception:
            pass
        self.active_profile_name.set("")
        self.profile_name_var.set("")
        self.profile_units_var.set(self.unit_mode.get())
        rates = None
        if self._rapid_rates:
            scale = self._profile_units_scale(self.unit_mode.get())
            rates = (
                self._rapid_rates[0] / scale,
                self._rapid_rates[1] / scale,
                self._rapid_rates[2] / scale,
            )
        if rates:
            self.profile_rate_x_var.set(f"{rates[0]:.3f}")
            self.profile_rate_y_var.set(f"{rates[1]:.3f}")
            self.profile_rate_z_var.set(f"{rates[2]:.3f}")
        else:
            self.profile_rate_x_var.set("")
            self.profile_rate_y_var.set("")
            self.profile_rate_z_var.set("")
        self._update_profile_units_label()

    def _save_profile(self):
        name = self.profile_name_var.get().strip()
        if not name:
            messagebox.showwarning("Profile", "Enter a profile name.")
            return
        units = str(self.profile_units_var.get()).lower()
        units = "inch" if units.startswith("in") else "mm"

        def parse_rate(var, label):
            raw = var.get().strip()
            if not raw:
                raise ValueError(f"Missing {label} rate.")
            value = float(raw)
            if value <= 0:
                raise ValueError(f"{label} rate must be positive.")
            return value

        try:
            rx = parse_rate(self.profile_rate_x_var, "X")
            ry = parse_rate(self.profile_rate_y_var, "Y")
            rz = parse_rate(self.profile_rate_z_var, "Z")
        except Exception as exc:
            messagebox.showwarning("Profile", str(exc))
            return

        profile = {"name": name, "units": units, "max_rates": {"x": rx, "y": ry, "z": rz}}
        found = False
        for i, existing in enumerate(self._machine_profiles):
            if existing.get("name") == name:
                self._machine_profiles[i] = profile
                found = True
                break
        if not found:
            self._machine_profiles.append(profile)
        self.active_profile_name.set(name)
        self._refresh_profile_combo()
        try:
            self.profile_combo.set(name)
        except Exception:
            pass
        self._apply_profile_to_vars(profile)
        self._apply_profile_units(profile)
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)
        self.status.config(text=f"Profile saved: {name}")

    def _delete_profile(self):
        name = self.active_profile_name.get().strip()
        if not name:
            messagebox.showwarning("Profile", "Select a profile to delete.")
            return
        if not messagebox.askyesno("Profile", f"Delete profile '{name}'?"):
            return
        self._machine_profiles = [p for p in self._machine_profiles if p.get("name") != name]
        self._refresh_profile_combo()
        profile = self._get_profile_by_name(self.active_profile_name.get())
        self._apply_profile_to_vars(profile)
        if profile:
            self._apply_profile_units(profile)
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)
        self.status.config(text=f"Profile deleted: {name}")

    def _show_macro_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        cancel_label: str,
        result_q: queue.Queue,
    ) -> None:
        try:
            dlg = tk.Toplevel(self)
            dlg.title(title)
            dlg.transient(self)
            dlg.grab_set()
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=12)
            frm.pack(fill="both", expand=True)
            lbl = ttk.Label(frm, text=message, wraplength=460, justify="left")
            lbl.pack(fill="x", pady=(0, 10))
            btn_row = ttk.Frame(frm)
            btn_row.pack(fill="x")

            def choose(label: str):
                if result_q.empty():
                    result_q.put(label)
                try:
                    dlg.destroy()
                except Exception:
                    pass

            for idx, lbl_text in enumerate(choices):
                b = ttk.Button(btn_row, text=lbl_text, command=lambda t=lbl_text: choose(t))
                set_kb_id(b, f"macro_prompt_{idx}")
                b.pack(side="left", padx=(0, 6))

            def on_close():
                choose(cancel_label)

            dlg.protocol("WM_DELETE_WINDOW", on_close)
        except Exception as exc:
            try:
                self.streaming_controller.log(f"[macro] Prompt failed: {exc}")
            except Exception:
                pass
            if result_q.empty():
                result_q.put(cancel_label)

    # ---------- Zeroing (simple G92-based) ----------
    # If you prefer G10 L20 (persistent work offset), say so and Iâ€™ll swap these.
    def zero_x(self):
        self.grbl.send_immediate("G92 X0")

    def zero_y(self):
        self.grbl.send_immediate("G92 Y0")

    def zero_z(self):
        self.grbl.send_immediate("G92 Z0")

    def zero_all(self):
        self.grbl.send_immediate("G92 X0 Y0 Z0")

    def goto_zero(self):
        self.grbl.send_immediate("G0 X0 Y0")

    # ---------- UI event handling ----------
    def _update_tab_visibility(self, nb=None):
        if nb is None:
            nb = getattr(self, "notebook", None)
        if not nb or not self.toolpath_panel.view:
            return
        try:
            tab_id = nb.select()
            label = nb.tab(tab_id, "text")
        except Exception as exc:
            logger.exception("Failed to update tab visibility: %s", exc)
            return
        self.toolpath_panel.set_visible(label == "3D View")

    def _update_app_settings_scrollregion(self):
        if not hasattr(self, "app_settings_canvas"):
            return
        self.app_settings_canvas.configure(scrollregion=self.app_settings_canvas.bbox("all"))

    def _on_app_settings_mousewheel(self, event):
        if not hasattr(self, "app_settings_canvas"):
            return
        delta = 0
        if event.delta:
            delta = -int(event.delta / 120)
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            self.app_settings_canvas.yview_scroll(delta, "units")

    def _bind_app_settings_mousewheel(self):
        if not hasattr(self, "app_settings_canvas"):
            return
        self.app_settings_canvas.bind_all("<MouseWheel>", self._on_app_settings_mousewheel)
        self.app_settings_canvas.bind_all("<Button-4>", self._on_app_settings_mousewheel)
        self.app_settings_canvas.bind_all("<Button-5>", self._on_app_settings_mousewheel)

    def _unbind_app_settings_mousewheel(self):
        if not hasattr(self, "app_settings_canvas"):
            return
        self.app_settings_canvas.unbind_all("<MouseWheel>")
        self.app_settings_canvas.unbind_all("<Button-4>")
        self.app_settings_canvas.unbind_all("<Button-5>")

    def _on_tab_changed(self, event):
        self._update_tab_visibility(event.widget)
        if not bool(self.gui_logging_enabled.get()):
            return
        nb = event.widget
        try:
            tab_id = nb.select()
            label = nb.tab(tab_id, "text")
        except Exception:
            return
        if not label:
            return
        ts = time.strftime("%H:%M:%S")
        self.streaming_controller.log(f"[{ts}] Tab: {label}")

    def _build_led_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(side="right", padx=(8, 0))
        self._led_indicators = {}
        self._led_containers = []
        self._led_bg = _resolve_widget_bg(parent)
        labels = [
            ("endstop", "Endstops"),
            ("probe", "Probe"),
            ("hold", "Hold"),
        ]
        for key, text in labels:
            container = tk.Frame(frame, bg=self._led_bg)
            container.pack(side="left", padx=(0, 8))
            canvas = tk.Canvas(
                container,
                width=18,
                height=18,
                highlightthickness=0,
                bd=0,
                bg=self._led_bg,
            )
            canvas.pack(side="left")
            oval = canvas.create_oval(2, 2, 16, 16, fill="#b0b0b0", outline="#555")
            ttk.Label(container, text=text).pack(side="left", padx=(4, 0))
            self._led_indicators[key] = (canvas, oval)
            self._led_containers.append(container)
        self._led_states = {key: False for key in self._led_indicators}
        self._update_led_panel(False, False, False)

    def _set_led_state(self, key, on):
        entry = self._led_indicators.get(key)
        if not entry:
            return
        canvas, oval = entry
        color = "#00c853" if on else "#b0b0b0"
        canvas.itemconfig(oval, fill=color)
        self._led_states[key] = on

    def _update_led_panel(self, endstop: bool, probe: bool, hold: bool):
        self._set_led_state("endstop", endstop)
        self._set_led_state("probe", probe)
        self._set_led_state("hold", hold)

    def _apply_state_fg(self, color: str | None):
        target = color if color else (self._state_default_fg or "#000000")
        for lbl in (getattr(self, "machine_state_label", None), getattr(self, "status", None)):
            if lbl:
                try:
                    lbl.config(foreground=target)
                except tk.TclError:
                    pass

    def _cancel_state_flash(self):
        if self._state_flash_after_id:
            try:
                self.after_cancel(self._state_flash_after_id)
            except Exception:
                pass
        self._state_flash_after_id = None
        self._state_flash_color = None
        self._state_flash_on = False

    def _toggle_state_flash(self):
        if not self._state_flash_color:
            return
        self._state_flash_on = not self._state_flash_on
        color = self._state_flash_color if self._state_flash_on else (self._state_default_fg or "#000000")
        self._apply_state_fg(color)
        self._state_flash_after_id = self.after(500, self._toggle_state_flash)

    def _start_state_flash(self, color: str):
        self._cancel_state_flash()
        self._state_flash_color = color
        self._toggle_state_flash()

    def _update_state_highlight(self, state: str | None):
        text = str(state or "").lower()
        if not text:
            self._cancel_state_flash()
            self._apply_state_fg(None)
            return
        if text.startswith("run"):
            self._cancel_state_flash()
            self._apply_state_fg("#00c853")
        elif text.startswith("idle"):
            self._cancel_state_flash()
            self._apply_state_fg("#2196f3")
        elif text.startswith("alarm") or text.startswith("door"):
            self._start_state_flash("#ff5252")
        elif any(text.startswith(key) for key in ("hold", "jog", "check")):
            self._start_state_flash("#ffc107")
        else:
            self._cancel_state_flash()
            self._apply_state_fg(None)

    def _drain_ui_queue(self):
        for _ in range(100):
            try:
                evt = self.ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_evt(evt)
            except Exception as exc:
                self._log_exception("UI event error", exc)
        if self._closing:
            return
        self._maybe_auto_reconnect()
        self.after(50, self._drain_ui_queue)

    def _toggle_tooltips(self):
        current = bool(self.tooltip_enabled.get())
        new_val = not current
        self.tooltip_enabled.set(new_val)
        self.btn_toggle_tips.config(text="Tool Tips: On" if new_val else "Tool Tips: Off")

    def _apply_theme(self, theme: str):
        try:
            if theme in self.available_themes:
                self.style.theme_use(theme)
                self._refresh_stop_button_backgrounds()
                self._refresh_led_backgrounds()
        except tk.TclError:
            pass

    def _on_gui_logging_change(self):
        status = "enabled" if self.gui_logging_enabled.get() else "disabled"
        try:
            self.streaming_controller.handle_log(f"[settings] GUI logging {status}")
        except Exception:
            pass

    def _on_theme_change(self, *_):
        self._apply_theme(self.selected_theme.get())

    def _refresh_stop_button_backgrounds(self):
        for btn in (getattr(self, "btn_jog_cancel", None), getattr(self, "btn_all_stop", None)):
            if isinstance(btn, StopSignButton):
                btn.refresh_background()

    def _refresh_led_backgrounds(self):
        bg = _resolve_widget_bg(self)
        self._led_bg = bg
        for canvas, _ in getattr(self, "_led_indicators", {}).values():
            try:
                canvas.config(bg=bg)
            except Exception:
                pass
        for container in getattr(self, "_led_containers", []):
            try:
                container.config(bg=bg)
            except Exception:
                pass

    def _install_dialog_loggers(self):
        orig_error = messagebox.showerror

        def _showerror(title, message, **kwargs):
            try:
                self.streaming_controller.handle_log(f"[dialog] {title}: {message}")
            except Exception:
                pass
            return orig_error(title, message, **kwargs)

        messagebox.showerror = _showerror

    def _toggle_error_dialogs(self):
        self.error_dialogs_enabled.set(not bool(self.error_dialogs_enabled.get()))
        self._on_error_dialogs_enabled_change()

    def _on_error_dialogs_enabled_change(self):
        enabled = bool(self.error_dialogs_enabled.get())
        if enabled:
            self._reset_error_dialog_state()
        else:
            self._set_error_dialog_status("Dialogs: Off")

    def _toggle_performance(self):
        current = bool(self.performance_mode.get())
        new_val = not current
        self.performance_mode.set(new_val)
        try:
            self.btn_performance_mode.config(
                text="Performance: On" if new_val else "Performance: Off"
            )
        except Exception:
            pass
        if not new_val:
            self.streaming_controller.flush_console()
        self._apply_status_poll_profile()

    def _toggle_console_pos_status(self):
        current = bool(self.console_positions_enabled.get())
        new_val = not current
        self.console_positions_enabled.set(new_val)
        self.console_status_enabled.set(new_val)
        if hasattr(self, "btn_console_pos"):
            self.btn_console_pos.config(text="Pos/Status: On" if new_val else "Pos/Status: Off")
        self.streaming_controller.render_console()

    def _toggle_render_3d(self):
        current = bool(self.render3d_enabled.get())
        new_val = not current
        self.render3d_enabled.set(new_val)
        self.btn_toggle_3d.config(text="3D Render: On" if new_val else "3D Render: Off")
        self.toolpath_panel.set_enabled(new_val)
        if new_val and self._last_gcode_lines:
            self.toolpath_panel.set_gcode_lines(self._last_gcode_lines)

    def _toolpath_limit_value(self, raw, fallback):
        try:
            value = int(str(raw).strip())
        except Exception:
            value = fallback
        if value < 0:
            value = 0
        return value

    def _apply_toolpath_draw_limits(self, _event=None):
        full = self._toolpath_limit_value(self.toolpath_full_limit.get(), self._toolpath_full_limit_default)
        interactive = self._toolpath_limit_value(
            self.toolpath_interactive_limit.get(), self._toolpath_interactive_limit_default
        )
        self.toolpath_full_limit.set(str(full))
        self.toolpath_interactive_limit.set(str(interactive))
        self.toolpath_panel.set_draw_limits(full, interactive)

    def _on_arc_detail_scale_move(self, value):
        try:
            deg = float(value)
        except Exception:
            deg = self.toolpath_arc_detail.get()
        self._toolpath_arc_detail_value.set(f"{deg:.1f}°")

    def _on_arc_detail_scale_key_release(self, event):
        if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
            self._apply_toolpath_arc_detail()

    def _clamp_arc_detail(self, value):
        try:
            deg = float(value)
        except Exception:
            deg = self._toolpath_arc_detail_default
        deg = max(self._toolpath_arc_detail_min, min(deg, self._toolpath_arc_detail_max))
        return deg

    def _apply_toolpath_arc_detail(self, _event=None):
        deg = self._clamp_arc_detail(self.toolpath_arc_detail.get())
        self.toolpath_arc_detail.set(deg)
        self._toolpath_arc_detail_value.set(f"{deg:.1f}°")
        self.toolpath_panel.set_arc_detail(deg)
        self._schedule_toolpath_arc_detail_reparse()

    def _schedule_toolpath_arc_detail_reparse(self):
        if self._toolpath_arc_detail_reparse_after_id:
            try:
                self.after_cancel(self._toolpath_arc_detail_reparse_after_id)
            except Exception:
                pass
        self._toolpath_arc_detail_reparse_after_id = self.after(
            self._toolpath_arc_detail_reparse_delay, self._run_toolpath_arc_detail_reparse
        )

    def _run_toolpath_arc_detail_reparse(self):
        self._toolpath_arc_detail_reparse_after_id = None
        if self._last_gcode_lines:
            self.toolpath_panel.reparse_lines(self._last_gcode_lines)

    def _on_toolpath_lightweight_change(self):
        self.toolpath_panel.set_lightweight(bool(self.toolpath_lightweight.get()))
        if self._last_gcode_lines:
            self.toolpath_panel.set_gcode_lines(self._last_gcode_lines)

    def _toggle_unit_mode(self):
        new_mode = "inch" if self.unit_mode.get() == "mm" else "mm"
        self._set_unit_mode(new_mode)

    def _on_resume_button_visibility_change(self):
        self.settings["show_resume_from_button"] = bool(self.show_resume_from_button.get())
        self._update_resume_button_visibility()

    def _on_recover_button_visibility_change(self):
        self.settings["show_recover_button"] = bool(self.show_recover_button.get())
        self._update_recover_button_visibility()

    def _update_resume_button_visibility(self):
        if not hasattr(self, "btn_resume_from"):
            return
        visible = bool(self.show_resume_from_button.get())
        if visible:
            if not self.btn_resume_from.winfo_ismapped():
                pack_kwargs = {"side": "left", "padx": (6, 0)}
                before_widget = getattr(self, "btn_unlock_top", None)
                if before_widget and before_widget.winfo_exists():
                    pack_kwargs["before"] = before_widget
                self.btn_resume_from.pack(**pack_kwargs)
        else:
            self.btn_resume_from.pack_forget()

    def _update_recover_button_visibility(self):
        if not hasattr(self, "btn_alarm_recover"):
            return
        visible = bool(self.show_recover_button.get())
        if visible:
            if not self.btn_alarm_recover.winfo_ismapped():
                pack_kwargs = {"side": "left", "padx": (6, 0)}
                separator = getattr(self, "_recover_separator", None)
                if separator and separator.winfo_exists():
                    pack_kwargs["before"] = separator
                self.btn_alarm_recover.pack(**pack_kwargs)
        else:
            self.btn_alarm_recover.pack_forget()


    def _confirm_and_run(self, label: str, func):
        """Optionally wrap an action with a confirmation dialog based on Training Wheels setting."""
        try:
            need_confirm = bool(self.training_wheels.get())
        except Exception:
            need_confirm = False
        now = time.time()
        last_ts = self._confirm_last_time.get(label, 0.0)
        if need_confirm:
            if (now - last_ts) < self._confirm_debounce_sec:
                return
            if not messagebox.askyesno("Confirm", f"{label}?"):
                return
        self._confirm_last_time[label] = now
        func()

    def _save_3d_view(self):
        view = self.toolpath_panel.get_view_state()
        if not view:
            return
        self.settings["view_3d"] = view
        self.status.config(text="3D view saved")

    def _load_3d_view(self, show_status: bool = True):
        view = self.settings.get("view_3d")
        if not view:
            return
        self.toolpath_panel.apply_view_state(view)
        if show_status:
            self.status.config(text="3D view loaded")

    def _clear_pending_ui_updates(self):
        self.streaming_controller.clear_pending_ui_updates()

    def _handle_evt(self, evt):
        kind = evt[0]

        if kind == "conn":
            is_on = evt[1]
            port = evt[2]
            self.connected = is_on

            if is_on:
                self._auto_reconnect_last_port = port or self._auto_reconnect_last_port
                self._auto_reconnect_pending = False
                self._auto_reconnect_last_attempt = 0.0
                self._auto_reconnect_retry = 0
                self._auto_reconnect_delay = 3.0
                self._auto_reconnect_next_ts = 0.0
                self.btn_conn.config(text="Disconnect")
                self._connected_port = port
                self._grbl_ready = False
                self._alarm_locked = False
                self._alarm_message = ""
                self._pending_settings_refresh = True
                self._status_seen = False
                self.machine_state.set(f"CONNECTED ({port})")
                self._machine_state_text = f"CONNECTED ({port})"
                self.status.config(text=f"Connected: {port} (waiting for Grbl)")
                self.btn_stop.config(state="normal")
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_resume_from.config(state="disabled")
                self.btn_alarm_recover.config(state="disabled")
                self._set_manual_controls_enabled(False)
                self.throughput_var.set("TX: 0 B/s")
            else:
                self.btn_conn.config(text="Connect")
                self._connected_port = None
                self._grbl_ready = False
                self._alarm_locked = False
                self._alarm_message = ""
                self._pending_settings_refresh = False
                self._status_seen = False
                self.machine_state.set("DISCONNECTED")
                self._machine_state_text = "DISCONNECTED"
                self.status.config(text="Disconnected")
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_stop.config(state="disabled")
                self.btn_resume_from.config(state="disabled")
                self.btn_alarm_recover.config(state="disabled")
                self._set_manual_controls_enabled(True)
                self._rapid_rates = None
                self._rapid_rates_source = None
                self._accel_rates = None
                if self._last_gcode_lines:
                    self._update_gcode_stats(self._last_gcode_lines)
                if not self._user_disconnect:
                    self._auto_reconnect_pending = True
                    self._auto_reconnect_retry = 0
                    self._auto_reconnect_delay = 3.0
                    self._auto_reconnect_next_ts = 0.0
                self._user_disconnect = False
                self.throughput_var.set("TX: 0 B/s")
            self._apply_status_poll_profile()

        elif kind == "ui_call":
            func, args, kwargs, result_q = evt[1], evt[2], evt[3], evt[4]
            try:
                result_q.put((True, func(*args, **kwargs)))
            except Exception as exc:
                self._log_exception("UI action failed", exc)
                result_q.put((False, exc))
        elif kind == "ui_post":
            func, args, kwargs = evt[1], evt[2], evt[3]
            try:
                func(*args, **kwargs)
            except Exception as exc:
                self._log_exception("UI action failed", exc)

        elif kind == "macro_prompt":
            title, message, choices, cancel_label, result_q = evt[1], evt[2], evt[3], evt[4], evt[5]
            try:
                self._show_macro_prompt(title, message, choices, cancel_label, result_q)
            except Exception as exc:
                try:
                    self.streaming_controller.log(f"[macro] Prompt failed: {exc}")
                except Exception:
                    pass
                if result_q.empty():
                    result_q.put(cancel_label)

        elif kind == "gcode_loaded":
            token, path, lines = evt[1], evt[2], evt[3]
            if token != self._gcode_load_token:
                return
            self._apply_loaded_gcode(path, lines)

        elif kind == "gcode_load_error":
            token, path, err = evt[1], evt[2], evt[3]
            if token != self._gcode_load_token:
                return
            self._gcode_loading = False
            self._finish_gcode_loading()
            self.gcode_stats_var.set("No file loaded")
            messagebox.showerror("Open G-code", f"Failed to read file:\n{err}")
            self.status.config(text="G-code load failed")

        elif kind == "log":
            self.streaming_controller.handle_log(evt[1])

        elif kind == "log_tx":
            self.streaming_controller.handle_log_tx(evt[1])

        elif kind == "log_rx":
            raw = evt[1]
            self.settings_controller.handle_line(raw)
            self.streaming_controller.handle_log_rx(raw)

        elif kind == "ready":
            self._grbl_ready = bool(evt[1])
            if not self._grbl_ready:
                self._status_seen = False
                self._alarm_locked = False
                self._alarm_message = ""
                if self.connected:
                    self.btn_run.config(state="disabled")
                    self.btn_pause.config(state="disabled")
                    self.btn_resume.config(state="disabled")
                    self.btn_resume_from.config(state="disabled")
                    self._set_manual_controls_enabled(False)
                    if self._connected_port:
                        self.status.config(text=f"Connected: {self._connected_port} (waiting for Grbl)")
                self._apply_status_poll_profile()
                return
            if self._alarm_locked:
                return
            if self.connected and self._connected_port:
                self.status.config(text=f"Connected: {self._connected_port}")
            self._apply_status_poll_profile()

        elif kind == "alarm":
            msg = evt[1] if len(evt) > 1 else ""
            self._set_alarm_lock(True, msg)
            self._apply_status_poll_profile()

        elif kind == "status":
            # Parse minimal fields: state + WPos if present
            s = evt[1].strip("<>")
            parts = s.split("|")
            state = parts[0] if parts else "?"
            self._status_seen = True
            wpos = None
            mpos = None
            feed = None
            spindle = None
            planner = None
            rxbytes = None
            wco = None
            ov = None
            pins = None
            for p in parts:
                if p.startswith("WPos:"):
                    wpos = p[5:]
                elif p.startswith("MPos:"):
                    mpos = p[5:]
                elif p.startswith("FS:"):
                    try:
                        f_str, s_str = p[3:].split(",", 1)
                        feed = float(f_str)
                        spindle = float(s_str)
                    except Exception:
                        pass
                elif p.startswith("Bf:"):
                    try:
                        bf_planner, bf_rx = p[3:].split(",", 1)
                        planner = int(bf_planner)
                        rxbytes = int(bf_rx)
                    except Exception:
                        pass
                elif p.startswith("WCO:"):
                    wco = p[4:]
                elif p.startswith("Ov:"):
                    ov = p[3:]
                elif p.startswith("Pn:"):
                    pins = p[3:]

            state_lower = state.lower()
            self._machine_state_text = state
            if state_lower.startswith("alarm"):
                self._set_alarm_lock(True, state)
            else:
                if self._alarm_locked:
                    self._set_alarm_lock(False)
                else:
                    self.machine_state.set(state)
                    self._update_state_highlight(state)
            if self._grbl_ready and self._pending_settings_refresh and not self._alarm_locked:
                self._pending_settings_refresh = False
                self._request_settings_dump()
            if (
                self.connected
                and self._grbl_ready
                and self._status_seen
                and not self._alarm_locked
                and self._stream_state not in ("running", "paused")
            ):
                self._set_manual_controls_enabled(True)
                if self.gview.lines_count:
                    self.btn_run.config(state="normal")
                    self.btn_resume_from.config(state="normal")
            with self.macro_executor.macro_vars() as macro_vars:
                macro_vars["state"] = state
            def parse_xyz(text: str):
                parts = text.split(",")
                if len(parts) < 3:
                    return None
                try:
                    return [float(parts[0]), float(parts[1]), float(parts[2])]
                except Exception:
                    return None

            wco_vals = parse_xyz(wco) if wco else None
            mpos_vals = parse_xyz(mpos) if mpos else None
            wpos_vals = parse_xyz(wpos) if wpos else None

            if mpos_vals and wpos_vals is None and wco_vals:
                wpos_vals = [
                    mpos_vals[0] - wco_vals[0],
                    mpos_vals[1] - wco_vals[1],
                    mpos_vals[2] - wco_vals[2],
                ]
                wpos = ",".join(f"{v:.3f}" for v in wpos_vals)
            elif wpos_vals and mpos_vals is None and wco_vals:
                mpos_vals = [
                    wpos_vals[0] + wco_vals[0],
                    wpos_vals[1] + wco_vals[1],
                    wpos_vals[2] + wco_vals[2],
                ]
                mpos = ",".join(f"{v:.3f}" for v in mpos_vals)

            if mpos:
                try:
                    x, y, z = mpos.split(",")
                    self.mpos_x.set(x)
                    self.mpos_y.set(y)
                    self.mpos_z.set(z)
                    with self.macro_executor.macro_vars() as macro_vars:
                        macro_vars["mx"] = float(x)
                        macro_vars["my"] = float(y)
                        macro_vars["mz"] = float(z)
                except Exception:
                    pass
            if wpos:
                try:
                    x, y, z = wpos.split(",")
                    self.wpos_x.set(x)
                    self.wpos_y.set(y)
                    self.wpos_z.set(z)
                    with self.macro_executor.macro_vars() as macro_vars:
                        macro_vars["wx"] = float(x)
                        macro_vars["wy"] = float(y)
                        macro_vars["wz"] = float(z)
                    try:
                        self.toolpath_panel.set_position(float(x), float(y), float(z))
                    except Exception:
                        pass
                except Exception:
                    pass
            if feed is not None:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["curfeed"] = feed
            if spindle is not None:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["curspindle"] = spindle
            if planner is not None:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["planner"] = planner
            if rxbytes is not None:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["rxbytes"] = rxbytes
            if wco_vals:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["wcox"] = wco_vals[0]
                    macro_vars["wcoy"] = wco_vals[1]
                    macro_vars["wcoz"] = wco_vals[2]
            if pins is not None:
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["pins"] = pins
            if ov:
                feed_val = spindle_val = None
                try:
                    ov_parts = [int(float(v)) for v in ov.split(",")]
                    if len(ov_parts) >= 3:
                        feed_val, spindle_val = ov_parts[0], ov_parts[2]
                        with self.macro_executor.macro_vars() as macro_vars:
                            changed = (
                                macro_vars.get("OvFeed") != ov_parts[0]
                                or macro_vars.get("OvRapid") != ov_parts[1]
                                or macro_vars.get("OvSpindle") != ov_parts[2]
                            )
                            macro_vars["OvFeed"] = ov_parts[0]
                            macro_vars["OvRapid"] = ov_parts[1]
                            macro_vars["OvSpindle"] = ov_parts[2]
                            macro_vars["_OvChanged"] = bool(changed)
                except Exception:
                    pass
                else:
                    if feed_val is not None:
                        self._set_feed_override_slider_value(feed_val)
                    if spindle_val is not None:
                        self._set_spindle_override_slider_value(spindle_val)
                    self._refresh_override_info()
            pin_state = {c for c in (pins or "").upper() if c.isalpha()}
            endstop_active = bool(pin_state & {"X", "Y", "Z"})
            with self.macro_executor.macro_vars() as macro_vars:
                prb_value = macro_vars.get("PRB")
            probe_active = bool(pin_state & {"P"}) or bool(prb_value)
            hold_active = bool(pin_state & {"H"}) or "hold" in str(state).lower()
            self._update_led_panel(endstop_active, probe_active, hold_active)
        elif kind == "buffer_fill":
            pct, used, window = evt[1], evt[2], evt[3]
            self.streaming_controller.handle_buffer_fill(pct, used, window)

        elif kind == "throughput":
            bps = evt[1] if len(evt) > 1 else 0.0
            self.streaming_controller.handle_throughput(float(bps))

        elif kind == "stream_state":
            st = evt[1]
            prev = self._stream_state
            now = time.time()
            self._stream_state = st
            if st == "running":
                if prev == "paused":
                    if self._stream_paused_at is not None:
                        self._stream_pause_total += max(0.0, now - self._stream_paused_at)
                        self._stream_paused_at = None
                elif prev != "running":
                    self._stream_start_ts = now
                    self._stream_pause_total = 0.0
                    self._stream_paused_at = None
                    self._live_estimate_min = None
                    self._refresh_gcode_stats_display()
                    self.throughput_var.set("TX: 0 B/s")
            elif st == "paused":
                if self._stream_paused_at is None:
                    self._stream_paused_at = now
            elif st in ("done", "stopped", "error", "alarm"):
                self._stream_start_ts = None
                self._stream_pause_total = 0.0
                self._stream_paused_at = None
                self._live_estimate_min = None
                self._refresh_gcode_stats_display()
                self.throughput_var.set("TX: 0 B/s")
            if st == "loaded":
                self.progress_pct.set(0)
                total = evt[2]
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = False
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                if (
                    self.connected
                    and total
                    and self._grbl_ready
                    and self._status_seen
                    and not self._alarm_locked
                ):
                    self.btn_run.config(state="normal")
                    self.btn_resume_from.config(state="normal")
                else:
                    self.btn_run.config(state="disabled")
                    self.btn_resume_from.config(state="disabled")
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "running":
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = True
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="normal")
                self.btn_resume.config(state="disabled")
                self.btn_resume_from.config(state="disabled")
                self._set_manual_controls_enabled(False)
                self._set_streaming_lock(True)
            elif st == "paused":
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = True
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="normal")
                self.btn_resume_from.config(state="disabled")
                self._set_manual_controls_enabled(False)
                self._set_streaming_lock(True)
            elif st in ("done", "stopped"):
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = False
                if st == "done":
                    self.progress_pct.set(100)
                else:
                    self.progress_pct.set(0)
                self.btn_run.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_resume_from.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "error":
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = False
                self.progress_pct.set(0)
                self.btn_run.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_resume_from.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self.status.config(text=f"Stream error: {evt[2]}")
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "alarm":
                with self.macro_executor.macro_vars() as macro_vars:
                    macro_vars["running"] = False
                self.progress_pct.set(0)
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_resume_from.config(state="disabled")
                self._set_alarm_lock(True, evt[2] if len(evt) > 2 else None)
                self._set_streaming_lock(False)
            self._apply_status_poll_profile()

        elif kind == "gcode_sent":
            self.streaming_controller.handle_gcode_sent(evt[1])

        elif kind == "gcode_acked":
            self.streaming_controller.handle_gcode_acked(evt[1])

        elif kind == "progress":
            done, total = evt[1], evt[2]
            self.streaming_controller.handle_progress(done, total)

    def _on_close(self):
        self._closing = True
        try:
            self._save_settings()
            self.grbl.disconnect()
        except Exception as exc:
            self._log_exception("Shutdown failed", exc)
        self.destroy()

    def _call_on_ui_thread(self, func, *args, timeout: float | None = 5.0, **kwargs):
        if threading.current_thread() is threading.main_thread():
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                self._log_exception("UI action failed", exc)
                return None
        result_q: queue.Queue = queue.Queue()
        self.ui_q.put(("ui_call", func, args, kwargs, result_q))
        try:
            if timeout is None:
                while True:
                    try:
                        ok, value = result_q.get(timeout=0.2)
                        break
                    except queue.Empty:
                        if self._closing:
                            self.ui_q.put(("log", "[ui] Action canceled (closing)."))
                            return None
            else:
                ok, value = result_q.get(timeout=timeout)
        except queue.Empty:
            self.ui_q.put(("log", "[ui] Action timed out."))
            return None
        if ok:
            return value
        self.ui_q.put(("log", f"[ui] Action failed: {value}"))
        return None

    def _post_ui_thread(self, func, *args, **kwargs):
        self.ui_q.put(("ui_post", func, args, kwargs))

    def _log_exception(
        self,
        context: str,
        exc: BaseException,
        *,
        show_dialog: bool = False,
        dialog_title: str = "Error",
        traceback_text: str | None = None,
    ):
        tb = traceback_text or _format_exception(exc)
        header = f"[error] {context}: {exc}"
        if threading.current_thread() is threading.main_thread():
            try:
                self.streaming_controller.handle_log(header)
                for ln in tb.splitlines():
                    self.streaming_controller.handle_log(ln)
            except Exception:
                pass
        else:
            try:
                self.ui_q.put(("log", header))
                for ln in tb.splitlines():
                    self.ui_q.put(("log", ln))
            except Exception:
                pass
        if show_dialog:
            if self._should_show_error_dialog():
                self._post_ui_thread(messagebox.showerror, dialog_title, tb)

    def _tk_report_callback_exception(self, exc, val, tb):
        try:
            text = "".join(traceback.format_exception(exc, val, tb))
        except Exception:
            text = f"{val}"
        self._log_exception(
            "Unhandled UI exception",
            val or RuntimeError("Unknown UI exception"),
            show_dialog=True,
            dialog_title="Application error",
            traceback_text=text,
        )

    def _should_show_error_dialog(self) -> bool:
        if not bool(self.error_dialogs_enabled.get()):
            return False
        if self._closing:
            return False
        if self._error_dialog_suppressed:
            return False
        now = time.monotonic()
        if (now - self._error_dialog_last_ts) < self._error_dialog_interval:
            return False
        if (now - self._error_dialog_window_start) > self._error_dialog_burst_window:
            self._error_dialog_window_start = now
            self._error_dialog_count = 0
        self._error_dialog_count += 1
        self._error_dialog_last_ts = now
        if self._error_dialog_count > self._error_dialog_burst_limit:
            self._error_dialog_suppressed = True
            msg = "[error] Too many errors; suppressing dialogs for this session."
            try:
                if threading.current_thread() is threading.main_thread():
                    self.streaming_controller.handle_log(msg)
                else:
                    self.ui_q.put(("log", msg))
            except Exception:
                pass
            self._set_error_dialog_status("Dialogs: Suppressed")
            return False
        return True

    def _reset_error_dialog_state(self):
        self._error_dialog_last_ts = 0.0
        self._error_dialog_window_start = 0.0
        self._error_dialog_count = 0
        self._error_dialog_suppressed = False
        self._set_error_dialog_status("")

    def _set_error_dialog_status(self, text: str):
        def update():
            if hasattr(self, "error_dialog_status_var"):
                self.error_dialog_status_var.set(text)
        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self._post_ui_thread(update)

    def _load_settings(self) -> dict:
        try:
            loaded = self._settings_store.load()
            if not loaded:
                logger.info("No settings file found; using defaults.")
        except SettingsLoadError as exc:
            logger.error(f"Failed to load settings: {exc}")
            self._settings_store.reset_to_defaults()
        except Exception as exc:
            logger.error(f"Unexpected error loading settings: {exc}")
            self._settings_store.reset_to_defaults()
        return self._settings_store.data

    def _save_settings(self):
        def safe_float(var, default, label):
            try:
                return float(var.get())
            except Exception:
                try:
                    fallback = float(default)
                except Exception:
                    fallback = 0.0
                self.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
                return fallback

        def safe_int(var, default, label):
            try:
                return int(var.get())
            except Exception:
                try:
                    fallback = int(default)
                except Exception:
                    fallback = 0
                self.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
                return fallback

        show_rapid, show_feed, show_arc = self.toolpath_panel.get_display_options()

        full_limit = self._toolpath_limit_value(
            self.toolpath_full_limit.get(), self._toolpath_full_limit_default
        )
        interactive_limit = self._toolpath_limit_value(
            self.toolpath_interactive_limit.get(), self._toolpath_interactive_limit_default
        )
        arc_detail_deg = self._clamp_arc_detail(self.toolpath_arc_detail.get())
        self._apply_error_dialog_settings()
        self._on_status_failure_limit_change()

        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        except Exception as exc:
            logger.exception("Failed to create settings directory: %s", exc)

        pos_status_enabled = bool(self.console_positions_enabled.get())

        data = dict(self.settings) if isinstance(self.settings, dict) else {}
        data.update({
            "last_port": self.current_port.get(),
            "unit_mode": self.unit_mode.get(),
            "step_xy": safe_float(self.step_xy, self.settings.get("step_xy", 1.0), "step XY"),
            "step_z": safe_float(self.step_z, self.settings.get("step_z", 1.0), "step Z"),
            "jog_feed_xy": safe_float(
                self.jog_feed_xy, self.settings.get("jog_feed_xy", 4000.0), "jog feed XY"
            ),
            "jog_feed_z": safe_float(
                self.jog_feed_z, self.settings.get("jog_feed_z", 500.0), "jog feed Z"
            ),
            "last_gcode_dir": self.settings.get("last_gcode_dir", ""),
            "window_geometry": self.geometry(),
            "tooltips_enabled": bool(self.tooltip_enabled.get()),
            "gui_logging_enabled": bool(self.gui_logging_enabled.get()),
            "error_dialogs_enabled": bool(self.error_dialogs_enabled.get()),
            "performance_mode": bool(self.performance_mode.get()),
            "render3d_enabled": bool(self.render3d_enabled.get()),
            "status_poll_interval": safe_float(
                self.status_poll_interval,
                self.settings.get("status_poll_interval", STATUS_POLL_DEFAULT),
                "status interval",
            ),
            "status_query_failure_limit": safe_int(
                self.status_query_failure_limit,
                self.settings.get("status_query_failure_limit", 3),
                "status failure limit",
            ),
            "view_3d": self.settings.get("view_3d"),
            "all_stop_mode": self.all_stop_mode.get(),
            "training_wheels": bool(self.training_wheels.get()),
            "reconnect_on_open": bool(self.reconnect_on_open.get()),
            "theme": self.selected_theme.get(),
            "console_positions_enabled": pos_status_enabled,
            "console_status_enabled": pos_status_enabled,
            "show_resume_from_button": bool(self.show_resume_from_button.get()),
            "show_recover_button": bool(self.show_recover_button.get()),
            "fallback_rapid_rate": self.fallback_rapid_rate.get().strip(),
            "estimate_factor": safe_float(self.estimate_factor, self.settings.get("estimate_factor", 1.0), "estimate factor"),
            "keyboard_bindings_enabled": bool(self.keyboard_bindings_enabled.get()),
            "current_line_mode": self.current_line_mode.get(),
            "key_bindings": dict(self._key_bindings),
            "machine_profiles": list(self._machine_profiles),
            "active_profile": self.active_profile_name.get(),
            "toolpath_full_limit": full_limit,
            "toolpath_interactive_limit": interactive_limit,
            "toolpath_arc_detail_deg": arc_detail_deg,
            "toolpath_lightweight": bool(self.toolpath_lightweight.get()),
            "toolpath_show_rapid": show_rapid,
            "toolpath_show_feed": show_feed,
            "toolpath_show_arc": show_arc,
            "error_dialog_interval": self._error_dialog_interval,
            "error_dialog_burst_window": self._error_dialog_burst_window,
            "error_dialog_burst_limit": self._error_dialog_burst_limit,
            "job_completion_popup": bool(self.job_completion_popup.get()),
            "job_completion_beep": bool(self.job_completion_beep.get()),
            "macros_allow_python": bool(self.macros_allow_python.get()),
        })
        self.settings = data
        self._settings_store.data = self.settings
        try:
            self._settings_store.save()
        except SettingsSaveError as exc:
            try:
                self.ui_q.put(("log", f"[settings] Save failed: {exc}"))
                self.status.config(text="Settings save failed")
            except Exception:
                pass
        except Exception as exc:
            try:
                self.ui_q.put(("log", f"[settings] Save failed: {exc}"))
                self.status.config(text="Settings save failed")
            except Exception:
                pass


if __name__ == "__main__":
    App().mainloop()
