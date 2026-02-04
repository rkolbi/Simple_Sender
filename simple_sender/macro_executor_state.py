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
import time

from simple_sender.macro_state import (
    macro_force_mm,
    macro_restore_state,
    macro_restore_units,
    macro_wait_for_idle,
    macro_wait_for_modal,
    macro_wait_for_status,
    snapshot_macro_state,
)
from simple_sender.types import MacroExecutorState


class MacroStateMixin(MacroExecutorState):
    def _macro_wait_for_idle(self, timeout_s: float = 30.0):
        macro_wait_for_idle(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            timeout_s=timeout_s,
        )

    def _macro_wait_for_status(self, timeout_s: float = 1.0) -> bool:
        return macro_wait_for_status(
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            timeout_s=timeout_s,
        )

    def _macro_wait_for_modal(self, seq: int | None = None, timeout_s: float = 1.0) -> bool:
        return macro_wait_for_modal(
            ui_q=self.ui_q,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            seq=seq,
            timeout_s=timeout_s,
        )

    def _snapshot_macro_state(self) -> dict[str, str]:
        return snapshot_macro_state(
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
        )

    def _macro_force_mm(self):
        macro_force_mm(
            app=self.app,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
        )

    def _macro_restore_units(self):
        macro_restore_units(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
        )

    def _macro_restore_state(self) -> bool:
        restored = macro_restore_state(
            app=self.app,
            grbl=self.grbl,
            ui_q=self.ui_q,
            macro_send=self._macro_send,
            macro_vars=self._macro_vars,
            macro_vars_lock=self._macro_vars_lock,
            state=self._macro_saved_state,
        )
        self._macro_state_restored = restored
        return restored

    def _parse_timeout(self, cmd_parts: list[str], default: float) -> float:
        if len(cmd_parts) > 1:
            try:
                value = float(cmd_parts[1])
            except Exception:
                value = default
            if value > 0:
                return value
        return default

    def _wait_for_connection_state(self, target: bool, timeout_s: float = 10.0) -> bool:
        start = time.time()
        while True:
            if getattr(self.app, "_closing", False):
                return False
            if bool(getattr(self.app, "connected", False)) is target:
                return True
            if timeout_s and (time.time() - start) > timeout_s:
                return False
            time.sleep(0.1)
