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
"""Macro parsing helpers shared by the macro executor."""

from __future__ import annotations

import types
from typing import Any, Callable

from simple_sender.utils.constants import (
    MACRO_AUXPAT,
    MACRO_STDEXPR,
)


def bcnc_compile_line(
    line: str,
    *,
    macros_allow_python: bool,
    macro_vars: dict[str, Any],
    macro_vars_lock,
):
    line = line.strip()
    if not line:
        return None
    # Always allow raw GRBL $-commands (including $J=...) even when macro scripting is disabled.
    if line[0] == "$":
        return line
    if line[0] == ";":
        return None
    if not macros_allow_python:
        if line.startswith("%"):
            pat = MACRO_AUXPAT.match(line.strip())
            cmd = pat.group(1) if pat else None
            if cmd not in ("%wait", "%msg", "%update"):
                return ("COMPILE_ERROR", "Macro scripting disabled in settings.")
        if line.startswith("_") or ("[" in line) or ("]" in line):
            return ("COMPILE_ERROR", "Macro scripting disabled in settings.")
        if "=" in line:
            stripped = line.lstrip()
            if stripped.startswith(";"):
                return None
            if stripped.startswith("(") and stripped.endswith(")"):
                return None
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
        if cmd in ("%state_return", "%state-return"):
            return "STATE_RETURN"
        if line.startswith("%if running"):
            with macro_vars_lock:
                if not macro_vars.get("running"):
                    return None
        if line.startswith("%if not running"):
            with macro_vars_lock:
                if macro_vars.get("running"):
                    return None
        if line.startswith("%if paused"):
            with macro_vars_lock:
                if not macro_vars.get("paused"):
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
    out: list[str | types.CodeType] = []
    bracket = 0
    paren = 0
    expr = ""
    cmd = ""
    in_comment = False
    in_quote: str | None = None
    escape = False
    for _i, ch in enumerate(line):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_quote:
                in_quote = None
            if bracket > 0:
                expr += ch
            elif not in_comment:
                cmd += ch
            continue
        if ch in ("'", '"') and not in_comment:
            in_quote = ch
            if bracket > 0:
                expr += ch
            else:
                cmd += ch
            continue
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


def bcnc_evaluate_line(
    compiled,
    *,
    macro_vars_lock,
    macro_local_vars: dict[str, Any],
    eval_globals: Callable[[], dict[str, Any]],
    exec_globals: Callable[[], dict[str, Any]],
):
    if isinstance(compiled, int):
        return None
    if isinstance(compiled, str):
        return compiled
    if isinstance(compiled, list):
        for i, expr in enumerate(compiled):
            if isinstance(expr, types.CodeType):
                with macro_vars_lock:
                    globals_ctx = eval_globals()
                    result = eval(expr, globals_ctx, macro_local_vars)
                if isinstance(result, float):
                    compiled[i] = str(round(result, 4))
                else:
                    compiled[i] = str(result)
        return "".join(compiled)
    if isinstance(compiled, types.CodeType):
        with macro_vars_lock:
            globals_ctx = exec_globals()
            exec(compiled, globals_ctx, globals_ctx)
            return None
