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

import logging
from typing import cast

from simple_sender.macro_commands import execute_macro_command
from simple_sender.macro_parser import bcnc_compile_line, bcnc_evaluate_line
from simple_sender.types import MacroExecutorState

logger = logging.getLogger(__name__)


class MacroCommandMixin(MacroExecutorState):
    def _execute_bcnc_command(self, line: str, raw_line: str | None = None):
        return self._execute_command(line, raw_line)

    def _execute_command(self, line: str, raw_line: str | None = None):
        return execute_macro_command(
            line,
            raw_line=raw_line,
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            macro_send=self._macro_send,
            parse_timeout=self._parse_timeout,
            wait_for_connection_state=self._wait_for_connection_state,
            macro_restore_state=self._macro_restore_state,
            parse_macro_prompt=self._parse_macro_prompt,
        )

    def _bcnc_compile_line(self, line: str):
        return bcnc_compile_line(
            line,
            macros_allow_python=bool(self.app.macros_allow_python.get()),
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
        )

    def _bcnc_evaluate_line(self, compiled):
        try:
            return bcnc_evaluate_line(
                compiled,
                macro_vars_lock=self._macro_vars_lock,
                macro_local_vars=self._macro_local_vars,
                eval_globals=self._macro_eval_globals,
                exec_globals=self._macro_exec_globals,
            )
        except Exception as exc:
            logger.exception("Macro expression evaluation failed")
            try:
                self.ui_q.put(("log", f"[macro] Expression evaluation failed: {exc}"))
            except Exception:
                pass
            raise

    def _macro_eval_globals(self) -> dict:
        return cast(dict, self._macro_vars)

    def _macro_exec_globals(self) -> dict:
        return cast(dict, self._macro_vars)
