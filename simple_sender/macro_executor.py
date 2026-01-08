import os
import queue
import re
import shlex
import threading
import time
import types
import logging
from contextlib import contextmanager
from typing import Any
from tkinter import messagebox

from simple_sender.utils.constants import (
    MACRO_AUXPAT,
    MACRO_EXTS,
    MACRO_GPAT,
    MACRO_PREFIXES,
    MACRO_STDEXPR,
    RT_STATUS,
)

logger = logging.getLogger(__name__)


class MacroExecutor:
    def __init__(self, app, macro_search_dirs: tuple[str, ...] | None = None):
        self.app = app
        self.ui_q = app.ui_q
        self.grbl = app.grbl
        self._macro_lock = threading.Lock()
        self._macro_vars_lock = threading.Lock()
        self._macro_search_dirs = macro_search_dirs or ()
        self._macro_local_vars = {"app": app, "os": os}
        self._current_macro_line: str = ""
        self._alarm_event = threading.Event()
        self._alarm_notified = False
        self._current_macro_line: str = ""
        self._alarm_notified = False
        macro_namespace = types.SimpleNamespace(state=types.SimpleNamespace())
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
            "paused": False,
            "prompt_choice": "",
            "prompt_index": -1,
            "prompt_cancelled": False,
            "macro": macro_namespace,
        }

    @contextmanager
    def macro_vars(self):
        with self._macro_vars_lock:
            yield self._macro_vars

    def macro_path(self, index: int) -> str | None:
        for macro_dir in self._macro_search_dirs:
            for prefix in MACRO_PREFIXES:
                for ext in MACRO_EXTS:
                    candidate = os.path.join(macro_dir, f"{prefix}{index}{ext}")
                    if os.path.isfile(candidate):
                        return candidate
        return None

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
        try:
            with self._macro_vars_lock:
                self._macro_local_vars = {"app": self.app, "os": os}
                self._macro_vars["app"] = self.app
                self._macro_vars["os"] = os
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
                            self.ui_q.put(("log", f"[macro] {msg}"))
                    elif kind == "UPDATE":
                        self.grbl.send_realtime(RT_STATUS)
                    continue
                evaluated = self._bcnc_evaluate_line(compiled)
                if evaluated is None:
                    continue
                if not self._execute_bcnc_command(evaluated, raw_line):
                    break
                if getattr(self.app, "_alarm_locked", False):
                    self.ui_q.put(("log", "[macro] Alarm detected; aborting macro."))
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

    def _macro_wait_for_idle(self, timeout_s: float = 30.0):
        if not self.grbl.is_connected():
            return
        start = time.time()
        seen_busy = False
        while True:
            if not self.grbl.is_connected():
                return
            state = str(self.app._machine_state_text).strip()
            is_idle = state.upper().startswith("IDLE")
            if getattr(self.app, "_homing_in_progress", False):
                is_idle = False
            if not self.grbl.is_streaming():
                if not is_idle:
                    seen_busy = True
                elif is_idle and (seen_busy or (time.time() - start) > 0.2):
                    return
            if timeout_s and (time.time() - start) > timeout_s:
                self.ui_q.put(("log", "[macro] %wait timeout"))
                return
            time.sleep(0.1)

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

    PROMPT_BRACKET_PAT = re.compile(
        r"\s*\[(?:title\([^]]*\)|btn\([^]]*\)\s*[A-Za-z0-9]?)\]\s*", re.IGNORECASE
    )

    def _parse_macro_prompt(
        self,
        line: str,
        macro_vars: dict[str, Any] | None = None,
    ):
        title = "Macro Pause"
        message = ""
        buttons: list[str] = []
        show_resume = True
        resume_label = "Resume"
        cancel_label = "Cancel"
        custom_btns: list[tuple[str, str | None]] = []
        button_keys: dict[str, str | None] = {}

        fragments = []
        bracket_matches = []
        last = 0
        for match in re.finditer(r"\[(.*?)\]", line):
            bracket_text = match.group(0)
            if self.PROMPT_BRACKET_PAT.fullmatch(bracket_text):
                fragments.append(line[last: match.start()])
                fragments.append(" ")
                bracket_matches.append(match.group(1).strip())
                last = match.end()
            else:
                fragments.append(line[last: match.end()])
                last = match.end()
        fragments.append(line[last:])
        parsed_line = "".join(fragments)

        for token in bracket_matches:
            if not token:
                continue
            title_match = re.fullmatch(r"title\((.*?)\)", token, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip() or title
                continue
            btn_match = re.fullmatch(r"btn\((.*?)\)\s*([A-Za-z0-9])?", token, re.IGNORECASE)
            if btn_match:
                custom_btns.append((btn_match.group(1).strip(), btn_match.group(2)))
                continue

        match = re.search(r"\((.*?)\)", parsed_line)
        if match:
            message = match.group(1).strip()
        try:
            tokens = shlex.split(parsed_line)
        except Exception:
            tokens = parsed_line.split()
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
        if macro_vars:
            message = self._format_prompt_macros(message, macro_vars)
        extras = [b for b in buttons if b and b not in (resume_label, cancel_label)]
        choices: list[str] = []
        if custom_btns:
            for label, key in custom_btns:
                if not label:
                    continue
                choices.append(label)
                button_keys[label] = key
        else:
            if show_resume:
                choices.append(resume_label)
            choices.extend(extras)
        choices.append(cancel_label)
        return title, message, choices, cancel_label, button_keys

    def _format_prompt_macros(self, text: str, macro_vars: dict[str, Any]) -> str:
        def replace(match: re.Match[str]) -> str:
            attr = match.group(1)
            parts = attr.split(".", 1)
            if len(parts) == 2 and parts[0] == "macro":
                macro_ns = macro_vars.get("macro")
                if isinstance(macro_ns, types.SimpleNamespace):
                    value = getattr(macro_ns, parts[1], None)
                else:
                    value = None
            else:
                value = macro_vars.get(attr)
            if value is None:
                return ""
            return str(value)

        return re.sub(r"\[macro\.([A-Za-z_]\w*)\]", replace, text)

    def _strip_prompt_tokens(self, line: str) -> str:
        return self.PROMPT_BRACKET_PAT.sub(" ", line).strip()

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

    def _execute_bcnc_command(self, line: str, raw_line: str | None = None):
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
            prompt_source = raw_line or s
            with self._macro_vars_lock:
                macro_snapshot = dict(self._macro_vars)
            title, message, choices, cancel_label, button_keys = self._parse_macro_prompt(
                prompt_source,
                macro_snapshot,
            )
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
            button_key = button_keys.get(choice) if choice in button_keys else None
            with self._macro_vars_lock:
                self._macro_vars["prompt_choice"] = choice
                self._macro_vars["prompt_choice_key"] = button_key
                self._macro_vars["prompt_choice_label"] = choice
                self._macro_vars["prompt_index"] = choices.index(choice) if choice in choices else -1
                self._macro_vars["prompt_cancelled"] = (choice == cancel_label)
                macro_ns = self._macro_vars.get("macro")
                if isinstance(macro_ns, types.SimpleNamespace):
                    setattr(macro_ns, "prompt_choice", choice)
                    setattr(macro_ns, "prompt_choice_key", button_key)
                    setattr(macro_ns, "prompt_choice_label", choice)
                    setattr(macro_ns, "prompt_index", self._macro_vars["prompt_index"])
                    setattr(macro_ns, "prompt_cancelled", self._macro_vars["prompt_cancelled"])
            self.ui_q.put(("log", f"[macro] Prompt: {message} | Selected: {choice}"))
            if choice == cancel_label:
                self.ui_q.put(("log", "[macro] Prompt canceled; macro aborted."))
                return False
            return True

        if cmd in ("ABSOLUTE", "ABS"):
            self._macro_send("G90")
            return True
        if cmd in ("RELATIVE", "REL"):
            self._macro_send("G91")
            return True
        if cmd == "HOME":
            self.app._call_on_ui_thread(self.app._start_homing, timeout=None)
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
            self._macro_send("G92 X0 Y0 Z0")
            return True
        if cmd == "SETX" and len(cmd_parts) > 1:
            self._macro_send(f"G92 X{cmd_parts[1]}")
            return True
        if cmd == "SETY" and len(cmd_parts) > 1:
            self._macro_send(f"G92 Y{cmd_parts[1]}")
            return True
        if cmd == "SETZ" and len(cmd_parts) > 1:
            self._macro_send(f"G92 Z{cmd_parts[1]}")
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
                self._macro_send("G92 " + " ".join(parts))
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
            self._macro_send(s)
            return True
        if s.startswith("(") or MACRO_GPAT.match(s):
            self._macro_send(s)
            return True
        self._macro_send(s)
        return True

    def _bcnc_compile_line(self, line: str):
        line = line.strip()
        if not line:
            return None
        # Always allow raw GRBL $-commands (including $J=...) even when macro scripting is disabled.
        if line[0] == "$":
            return line
        if not bool(self.app.macros_allow_python.get()):
            if line.startswith(("%", "_")) or ("[" in line) or ("]" in line) or ("=" in line):
                return ("COMPILE_ERROR", "Macro scripting disabled in settings.")
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
            if line.startswith("%if not running"):
                with self._macro_vars_lock:
                    if self._macro_vars.get("running"):
                        return None
            if line.startswith("%if paused"):
                with self._macro_vars_lock:
                    if not self._macro_vars.get("paused"):
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
        out: list[str | types.CodeType] = []
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
        if isinstance(compiled, str):
            return compiled
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
