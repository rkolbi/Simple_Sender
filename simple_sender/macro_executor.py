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

import os
import threading
import types
from contextlib import contextmanager

from simple_sender import tool_measurement
from simple_sender.utils.constants import (
    MACRO_EXTS,
    MACRO_PREFIXES,
    USER_MACRO_SLOT_COUNT,
)

from simple_sender.macro_executor_commands import MacroCommandMixin
from simple_sender.macro_executor_prompting import MacroPromptMixin
from simple_sender.macro_executor_runtime import MacroRunnerMixin
from simple_sender.macro_executor_state import MacroStateMixin


class MacroExecutor(MacroPromptMixin, MacroStateMixin, MacroCommandMixin, MacroRunnerMixin):
    def __init__(self, app, macro_search_dirs: tuple[str, ...] | None = None):
        self.app = app
        try:
            setattr(app, "macro_executor", self)
        except Exception:
            pass
        self.ui_q = app.ui_q
        self.grbl = app.grbl
        self._macro_lock = threading.Lock()
        self._macro_vars_lock = threading.Lock()
        self._macro_search_dirs = macro_search_dirs or ()
        self._macro_local_vars = {"app": app, "os": os}
        self._current_macro_line: str = ""
        self._alarm_event = threading.Event()
        self._alarm_notified = False
        self._manual_error_event = threading.Event()
        self._manual_error_message = ""
        self._macro_saved_state = None
        self._macro_state_restored = False
        self._last_macro_run_success: bool | None = None
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
            "_status_seq": 0,
            "_status_coords_seq": 0,
            "_modal_seq": 0,
            "tool_measurement": tool_measurement,
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

    @staticmethod
    def user_macro_storage_index(slot: int) -> int:
        return int(slot)

    def user_macro_path(self, slot: int) -> str | None:
        slot_num = int(slot)
        if slot_num < 1 or slot_num > int(USER_MACRO_SLOT_COUNT):
            return None
        return self.macro_path(self.user_macro_storage_index(slot_num))
