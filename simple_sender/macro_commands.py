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
"""Macro command execution helpers shared by the macro executor."""

from __future__ import annotations

import logging
import queue
import time
import types
from typing import Any, Callable
from tkinter import messagebox

from simple_sender.macro_timeouts import macro_timeouts_disabled
from simple_sender.utils.constants import MACRO_GPAT, MACRO_PROMPT_TIMEOUT, RT_STATUS

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_MACRO_LOAD_TIMEOUT_S = 120.0


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)

def _maybe_set_unit_mode(app, unit_mode: str | None) -> None:
    if not unit_mode:
        return
    try:
        app._set_unit_mode(unit_mode)
    except Exception as exc:
        _log_suppressed("Failed applying macro-requested unit mode", exc)


def _handle_prompt_command(
    *,
    cmd: str,
    s: str,
    raw_line: str | None,
    app,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    parse_macro_prompt: Callable[[str, dict[str, Any] | None], tuple[str, str, list[str], str, dict[str, str | None]]],
    macro_cancelled: Callable[[], bool] | None = None,
) -> bool | None:
    if cmd not in ("M0", "M00", "PROMPT"):
        return None
    prompt_source = raw_line or s
    if raw_line:
        stripped = raw_line.lstrip()
        if not stripped.upper().startswith(("M0", "M00", "PROMPT")):
            prompt_source = s
    with macro_vars_lock:
        macro_snapshot = dict(macro_vars)
    title, message, choices, cancel_label, button_keys = parse_macro_prompt(
        prompt_source,
        macro_snapshot,
    )
    prompt_timeout_s = float(getattr(app, "_macro_prompt_timeout_s", MACRO_PROMPT_TIMEOUT))
    if macro_timeouts_disabled(app):
        prompt_timeout_s = 0.0
    if prompt_timeout_s < 0:
        prompt_timeout_s = 0.0
    prompt_started = time.monotonic()
    result_q: queue.Queue[str] = queue.Queue(maxsize=1)
    ui_q.put(("macro_prompt", title, message, choices, cancel_label, result_q))
    while True:
        try:
            choice = result_q.get(timeout=0.2)
            break
        except queue.Empty:
            if callable(macro_cancelled):
                try:
                    if bool(macro_cancelled()):
                        choice = cancel_label
                        ui_q.put(("log", "[macro] Prompt canceled by ALL STOP; macro aborted."))
                        break
                except Exception as exc:
                    _log_suppressed("Failed evaluating macro cancellation during prompt wait", exc)
            if getattr(app, "_closing", False):
                choice = cancel_label
                break
            if prompt_timeout_s and (time.monotonic() - prompt_started) >= prompt_timeout_s:
                choice = cancel_label
                ui_q.put(
                    ("log", f"[macro] Prompt timed out after {prompt_timeout_s:.1f}s; macro canceled."),
                )
                break
    if choice not in choices:
        choice = cancel_label
    button_key = button_keys.get(choice) if choice in button_keys else None
    with macro_vars_lock:
        macro_vars["prompt_choice"] = choice
        macro_vars["prompt_choice_key"] = button_key
        macro_vars["prompt_choice_label"] = choice
        macro_vars["prompt_index"] = choices.index(choice) if choice in choices else -1
        macro_vars["prompt_cancelled"] = (choice == cancel_label)
        macro_ns = macro_vars.get("macro")
        if isinstance(macro_ns, types.SimpleNamespace):
            setattr(macro_ns, "prompt_choice", choice)
            setattr(macro_ns, "prompt_choice_key", button_key)
            setattr(macro_ns, "prompt_choice_label", choice)
            setattr(macro_ns, "prompt_index", macro_vars["prompt_index"])
            setattr(macro_ns, "prompt_cancelled", macro_vars["prompt_cancelled"])
    ui_q.put(("log", f"[macro] Prompt: {message} | Selected: {choice}"))
    if choice == cancel_label:
        ui_q.put(("log", "[macro] Prompt canceled; macro aborted."))
        return False
    return True


def _handle_named_macro_command(
    *,
    cmd: str,
    cmd_parts: list[str],
    app,
    grbl,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    macro_send: Callable[[str], Any],
    parse_timeout: Callable[[list[str], float], float],
    wait_for_connection_state: Callable[[bool, float], bool],
    wait_for_ready_state: Callable[[float], bool],
    wait_for_gcode_load_result: Callable[[int, float], bool],
    macro_restore_state: Callable[[], bool],
) -> bool | None:
    if cmd in ("ABSOLUTE", "ABS"):
        macro_send("G90")
        return True
    if cmd in ("RELATIVE", "REL"):
        macro_send("G91")
        return True
    if cmd == "HOME":
        started = bool(app._call_on_ui_thread(app._start_homing, timeout=None))
        if not started:
            ui_q.put(("log", "[macro] HOME blocked or rejected; homing did not start."))
            return False
        return True
    if cmd == "OPEN":
        if not app.connected:
            started = app._call_on_ui_thread(app.toggle_connect)
            if started is False:
                ui_q.put(("log", "[macro] OPEN blocked; connection transition did not start."))
                return False
            timeout_s = parse_timeout(cmd_parts, 10.0)
            if not wait_for_connection_state(True, timeout_s):
                ui_q.put(("log", f"[macro] OPEN timed out after {timeout_s:.1f}s"))
                return False
        else:
            timeout_s = parse_timeout(cmd_parts, 10.0)
        if not wait_for_ready_state(timeout_s):
            ui_q.put(("log", f"[macro] OPEN timed out waiting for GRBL ready after {timeout_s:.1f}s"))
            return False
        return True
    if cmd == "CLOSE":
        if app.connected:
            started = app._call_on_ui_thread(app.toggle_connect)
            if started is False:
                ui_q.put(("log", "[macro] CLOSE blocked; disconnect transition did not start."))
                return False
            timeout_s = parse_timeout(cmd_parts, 10.0)
            if not wait_for_connection_state(False, timeout_s):
                ui_q.put(("log", f"[macro] CLOSE timed out after {timeout_s:.1f}s"))
                return False
        return True
    if cmd == "HELP":
        app._call_on_ui_thread(
            messagebox.showinfo,
            "Macro",
            "Help is not available in this sender.",
            timeout=None,
        )
        return True
    if cmd in ("QUIT", "EXIT"):
        closed = app._call_on_ui_thread(app._on_close)
        if closed is False:
            ui_q.put(("log", f"[macro] {cmd} canceled; application remained open."))
            return False
        return True
    if cmd == "LOAD" and len(cmd_parts) > 1:
        path = " ".join(cmd_parts[1:]).strip()
        token = app._call_on_ui_thread(
            app._load_gcode_from_path,
            path,
            timeout=None,
        )
        if not token:
            ui_q.put(("log", f"[macro] LOAD blocked or failed to start for: {path}"))
            return False
        if not wait_for_gcode_load_result(int(token), _MACRO_LOAD_TIMEOUT_S):
            result_token = int(getattr(app, "_gcode_load_last_result_token", -1) or -1)
            result_success = getattr(app, "_gcode_load_last_result_success", None)
            detail = ""
            if result_token == int(token) and result_success is False:
                detail = str(getattr(app, "_gcode_load_last_result_error", "") or "").strip()
            if not detail:
                detail = f"G-code load did not complete within {_MACRO_LOAD_TIMEOUT_S:.1f}s."
            ui_q.put(("log", f"[macro] LOAD failed for '{path}': {detail}"))
            return False
        return True
    if cmd == "UNLOCK":
        accepted = bool(grbl.unlock())
        if not accepted:
            ui_q.put(("log", "[macro] UNLOCK blocked or rejected by GRBL."))
            return False
        return True
    if cmd == "RESET":
        try:
            if hasattr(app, "_stop_job_accessories"):
                app._stop_job_accessories("job_reset")
        except Exception:
            pass
        accepted = grbl.reset()
        if accepted is False:
            ui_q.put(("log", "[macro] RESET blocked or rejected; Ctrl-X was not sent."))
            return False
        return True
    if cmd in ("PAUSE", "FEEDHOLD"):
        accepted = grbl.hold()
        if accepted is False:
            ui_q.put(("log", "[macro] PAUSE blocked or rejected; feed hold was not sent."))
            return False
        return True
    if cmd == "RESUME":
        accepted = grbl.resume()
        if accepted is False:
            ui_q.put(("log", "[macro] RESUME blocked or rejected; cycle start was not sent."))
            return False
        return True
    if cmd == "STOP":
        try:
            if hasattr(app, "_stop_job_accessories"):
                app._stop_job_accessories("job_stop")
        except Exception:
            pass
        accepted = grbl.stop_stream()
        if accepted is not True:
            ui_q.put(("log", "[macro] STOP blocked or failed; no active stream was stopped."))
            return False
        return True
    if cmd == "RUN":
        app._call_on_ui_thread(app.run_job, timeout=None)
        try:
            started = bool(grbl.is_streaming())
        except Exception:
            started = False
        if not started:
            ui_q.put(("log", "[macro] RUN blocked or failed to enter streaming state."))
            return False
        return True
    if cmd in ("STATE_RETURN", "STATE-RETURN"):
        return macro_restore_state()
    if cmd == "SAVE":
        ui_q.put(("log", "[macro] SAVE is not supported."))
        return True
    if cmd == "SENDHEX" and len(cmd_parts) > 1:
        try:
            b = bytes([int(cmd_parts[1], 16)])
            accepted = grbl.send_realtime(b)
            if accepted is False:
                ui_q.put(("log", "[macro] SENDHEX failed: realtime command was not sent."))
                return False
        except Exception as exc:
            logger.exception("Macro SENDHEX failed: %s", exc)
            ui_q.put(("log", f"[macro] SENDHEX failed: {exc}"))
            return False
        return True
    if cmd == "SAFE" and len(cmd_parts) > 1:
        try:
            with macro_vars_lock:
                macro_vars["safe"] = float(cmd_parts[1])
        except Exception as exc:
            logger.exception("Macro SAFE failed: %s", exc)
            ui_q.put(("log", f"[macro] SAFE failed: {exc}"))
            return False
        return True
    if cmd == "SET0":
        macro_send("G92 X0 Y0 Z0")
        return True
    if cmd == "SETX" and len(cmd_parts) > 1:
        macro_send(f"G92 X{cmd_parts[1]}")
        return True
    if cmd == "SETY" and len(cmd_parts) > 1:
        macro_send(f"G92 Y{cmd_parts[1]}")
        return True
    if cmd == "SETZ" and len(cmd_parts) > 1:
        macro_send(f"G92 Z{cmd_parts[1]}")
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
            macro_send("G92 " + " ".join(parts))
        return True
    return None


def execute_macro_command(
    line: Any,
    *,
    raw_line: str | None,
    app,
    grbl,
    ui_q,
    macro_vars: dict[str, Any],
    macro_vars_lock,
    macro_send: Callable[[str], Any],
    parse_timeout: Callable[[list[str], float], float],
    wait_for_connection_state: Callable[[bool, float], bool],
    wait_for_ready_state: Callable[[float], bool],
    wait_for_gcode_load_result: Callable[[int, float], bool],
    macro_restore_state: Callable[[], bool],
    parse_macro_prompt: Callable[[str, dict[str, Any] | None], tuple[str, str, list[str], str, dict[str, str | None]]],
    macro_cancelled: Callable[[], bool] | None = None,
    format_macro_message: Callable[[str], str] | None = None,
) -> bool:
    if line is None:
        return True
    if isinstance(line, tuple):
        return True
    s = str(line).strip()
    if not s:
        return True
    cmd_parts = s.replace(",", " ").split()
    cmd = cmd_parts[0].upper()
    unit_mode = None
    for part in cmd_parts:
        upper = part.upper()
        if upper == "G20":
            unit_mode = "inch"
        elif upper == "G21":
            unit_mode = "mm"

    prompt_result = _handle_prompt_command(
        cmd=cmd,
        s=s,
        raw_line=raw_line,
        app=app,
        ui_q=ui_q,
        macro_vars=macro_vars,
        macro_vars_lock=macro_vars_lock,
        parse_macro_prompt=parse_macro_prompt,
        macro_cancelled=macro_cancelled,
    )
    if prompt_result is not None:
        return prompt_result
    if "[" in s and callable(format_macro_message):
        try:
            expanded = str(format_macro_message(s))
        except Exception as exc:
            _log_suppressed("Failed expanding inline macro expressions in command line", exc)
            expanded = s
        s = expanded.strip()
        if not s:
            return True
        cmd_parts = s.replace(",", " ").split()
        cmd = cmd_parts[0].upper()
    command_result = _handle_named_macro_command(
        cmd=cmd,
        cmd_parts=cmd_parts,
        app=app,
        grbl=grbl,
        ui_q=ui_q,
        macro_vars=macro_vars,
        macro_vars_lock=macro_vars_lock,
        macro_send=macro_send,
        parse_timeout=parse_timeout,
        wait_for_connection_state=wait_for_connection_state,
        wait_for_ready_state=wait_for_ready_state,
        wait_for_gcode_load_result=wait_for_gcode_load_result,
        macro_restore_state=macro_restore_state,
    )
    if command_result is not None:
        return command_result

    if s.startswith("!"):
        accepted = grbl.hold()
        if accepted is False:
            ui_q.put(("log", "[macro] FEEDHOLD blocked or rejected; feed hold was not sent."))
            return False
        return True
    if s.startswith("~"):
        accepted = grbl.resume()
        if accepted is False:
            ui_q.put(("log", "[macro] RESUME blocked or rejected; cycle start was not sent."))
            return False
        return True
    if s.startswith("?"):
        accepted = grbl.send_realtime(RT_STATUS)
        if accepted is False:
            ui_q.put(("log", "[macro] STATUS blocked or rejected; realtime status query was not sent."))
            return False
        return True
    if s.startswith("\x18"):
        accepted = grbl.reset()
        if accepted is False:
            ui_q.put(("log", "[macro] RESET blocked or rejected; Ctrl-X was not sent."))
            return False
        return True

    if s.startswith("$") or s.startswith("@") or s.startswith("{"):
        macro_send(s)
        _maybe_set_unit_mode(app, unit_mode)
        return True
    if s.startswith("(") or MACRO_GPAT.match(s):
        macro_send(s)
        _maybe_set_unit_mode(app, unit_mode)
        return True
    macro_send(s)
    _maybe_set_unit_mode(app, unit_mode)
    return True
