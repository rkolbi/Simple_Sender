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
import types
from typing import Any, Callable
from tkinter import messagebox

from simple_sender.utils.constants import MACRO_GPAT, RT_STATUS

logger = logging.getLogger(__name__)


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
    macro_restore_state: Callable[[], bool],
    parse_macro_prompt: Callable[[str, dict[str, Any] | None], tuple[str, str, list[str], str, dict[str, str | None]]],
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

    if cmd in ("M0", "M00", "PROMPT"):
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
        result_q: queue.Queue[str] = queue.Queue()
        ui_q.put(("macro_prompt", title, message, choices, cancel_label, result_q))
        while True:
            try:
                choice = result_q.get(timeout=0.2)
                break
            except queue.Empty:
                if getattr(app, "_closing", False):
                    choice = cancel_label
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

    if cmd in ("ABSOLUTE", "ABS"):
        macro_send("G90")
        return True
    if cmd in ("RELATIVE", "REL"):
        macro_send("G91")
        return True
    if cmd == "HOME":
        app._call_on_ui_thread(app._start_homing, timeout=None)
        return True
    if cmd == "OPEN":
        if not app.connected:
            app._call_on_ui_thread(app.toggle_connect)
            timeout_s = parse_timeout(cmd_parts, 10.0)
            if not wait_for_connection_state(True, timeout_s):
                ui_q.put(("log", f"[macro] OPEN timed out after {timeout_s:.1f}s"))
                return False
        return True
    if cmd == "CLOSE":
        if app.connected:
            app._call_on_ui_thread(app.toggle_connect)
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
        app._call_on_ui_thread(app._on_close)
        return True
    if cmd == "LOAD" and len(cmd_parts) > 1:
        app._call_on_ui_thread(
            app._load_gcode_from_path,
            " ".join(cmd_parts[1:]),
            timeout=None,
        )
        return True
    if cmd == "UNLOCK":
        grbl.unlock()
        return True
    if cmd == "RESET":
        grbl.reset()
        return True
    if cmd == "PAUSE":
        grbl.hold()
        return True
    if cmd == "RESUME":
        grbl.resume()
        return True
    if cmd == "FEEDHOLD":
        grbl.hold()
        return True
    if cmd == "STOP":
        grbl.stop_stream()
        return True
    if cmd == "RUN":
        grbl.start_stream()
        return True
    if cmd in ("STATE_RETURN", "STATE-RETURN"):
        return macro_restore_state()
    if cmd == "SAVE":
        ui_q.put(("log", "[macro] SAVE is not supported."))
        return True
    if cmd == "SENDHEX" and len(cmd_parts) > 1:
        try:
            b = bytes([int(cmd_parts[1], 16)])
            grbl.send_realtime(b)
        except Exception as exc:
            logger.exception("Macro SENDHEX failed: %s", exc)
            ui_q.put(("log", f"[macro] SENDHEX failed: {exc}"))
        return True
    if cmd == "SAFE" and len(cmd_parts) > 1:
        try:
            with macro_vars_lock:
                macro_vars["safe"] = float(cmd_parts[1])
        except Exception as exc:
            logger.exception("Macro SAFE failed: %s", exc)
            ui_q.put(("log", f"[macro] SAFE failed: {exc}"))
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

    if s.startswith("!"):
        grbl.hold()
        return True
    if s.startswith("~"):
        grbl.resume()
        return True
    if s.startswith("?"):
        grbl.send_realtime(RT_STATUS)
        return True
    if s.startswith("\x18"):
        grbl.reset()
        return True

    if s.startswith("$") or s.startswith("@") or s.startswith("{"):
        macro_send(s)
        if unit_mode:
            try:
                app._set_unit_mode(unit_mode)
            except Exception:
                pass
        return True
    if s.startswith("(") or MACRO_GPAT.match(s):
        macro_send(s)
        if unit_mode:
            try:
                app._set_unit_mode(unit_mode)
            except Exception:
                pass
        return True
    macro_send(s)
    if unit_mode:
        try:
            app._set_unit_mode(unit_mode)
        except Exception:
            pass
    return True
