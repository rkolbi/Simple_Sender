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
from typing import Any

from simple_sender.macro_prompt import (
    format_prompt_macros,
    parse_macro_prompt,
    strip_prompt_tokens,
)
from simple_sender.types import MacroExecutorState

logger = logging.getLogger(__name__)


class MacroPromptMixin(MacroExecutorState):
    def _parse_macro_prompt(
        self,
        line: str,
        macro_vars: dict[str, Any] | None = None,
    ):
        return parse_macro_prompt(line, macro_vars)

    def _format_prompt_macros(self, text: str, macro_vars: dict[str, Any]) -> str:
        return format_prompt_macros(text, macro_vars)

    def _format_macro_message(self, text: str) -> str:
        if "[" not in text:
            return text
        out: list[str] = []
        expr: list[str] = []
        bracket = 0
        for ch in text:
            if ch == "[":
                if bracket == 0:
                    bracket = 1
                    expr = []
                else:
                    expr.append(ch)
                    bracket += 1
                continue
            if ch == "]":
                if bracket == 0:
                    out.append(ch)
                    continue
                bracket -= 1
                if bracket == 0:
                    expr_text = "".join(expr)
                    try:
                        with self._macro_vars_lock:
                            globals_ctx = self._macro_eval_globals()
                            result = eval(expr_text, globals_ctx, self._macro_local_vars)
                    except Exception as exc:
                        logger.exception("Macro message expression failed: [%s]", expr_text)
                        try:
                            self.ui_q.put(
                                ("log", f"[macro] Message expression error [{expr_text}]: {exc}")
                            )
                        except Exception:
                            pass
                        result = ""
                    if isinstance(result, float):
                        out.append(str(round(result, 4)))
                    else:
                        out.append(str(result))
                    expr = []
                else:
                    expr.append(ch)
                continue
            if bracket > 0:
                expr.append(ch)
            else:
                out.append(ch)
        if bracket > 0:
            out.append("[" + "".join(expr))
        return "".join(out)

    def _strip_prompt_tokens(self, line: str) -> str:
        return strip_prompt_tokens(line)
