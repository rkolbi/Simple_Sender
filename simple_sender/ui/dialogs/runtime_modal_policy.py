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
"""Developer note: runtime modal recovery policy.

This module exists to prevent a specific deadlock class: a grabbed dialog can
appear while screen lock is active, own input via ``grab_set()``, block access
to the main-window unlock control, and then have its own controls suppressed by
the lock guard. In that state the operator can be trapped.

Rule:
- Every grabbed dialog in ``simple_sender/ui`` must be explicitly classified in
  ``RUNTIME_MODAL_DIALOG_POLICIES``.
- If a dialog can appear asynchronously during active runtime while the screen
  may already be locked, mark it ``runtime_recovery_required`` and use
  ``RuntimeModalRecoveryController``.
- Otherwise mark it ``runtime_recovery_not_needed`` and give a real reason.

Use ``RuntimeModalRecoveryController`` for recovery-required dialogs. It keeps a
popup-local Lock/Unlock path working under grab, parents the confirmation to
the active popup, and disables only the dialog actions you pass in. It preserves
modal behavior; it does not broadly exempt popup controls from screen lock.

Enforcement:
- ``tests/unit/test_runtime_modal_policy.py`` fails if a new ``grab_set()``
  dialog under ``simple_sender/ui`` is added without an explicit classification.

Scope:
- This is a narrow safeguard for the screen-lock + grabbed-runtime-popup bug
  class. It currently covers ``grab_set()`` dialogs under ``simple_sender/ui``;
  it is not a general popup framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from tkinter import ttk

from simple_sender.ui.screen_lock import (
    register_screen_lock_toggle_widget,
    toggle_screen_lock,
    unregister_screen_lock_toggle_widget,
)
from simple_sender.ui.widgets_common import set_kb_id

RUNTIME_RECOVERY_REQUIRED = "runtime_recovery_required"
RUNTIME_RECOVERY_NOT_NEEDED = "runtime_recovery_not_needed"


@dataclass(frozen=True)
class RuntimeModalDialogPolicy:
    dialog_id: str
    classification: str
    reason: str


RUNTIME_MODAL_DIALOG_POLICIES: dict[str, RuntimeModalDialogPolicy] = {
    "simple_sender.ui.autolevel_dialog.dialog_controller.AutoLevelDialogController._create_dialog": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.autolevel_dialog.dialog_controller.AutoLevelDialogController._create_dialog",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="User-invoked setup dialog; not opened asynchronously during locked runtime flow.",
    ),
    "simple_sender.ui.autolevel_dialog.profiles._prompt_auto_level_profile_choice": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.autolevel_dialog.profiles._prompt_auto_level_profile_choice",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Preset choice is part of operator-invoked auto-level setup and cannot appear unexpectedly while locked.",
    ),
    "simple_sender.ui.dialogs.__init__.show_resume_dialog": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.__init__.show_resume_dialog",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Resume-from-line is operator-invoked and screen lock blocks opening it in the first place.",
    ),
    "simple_sender.ui.dialogs.alarm_recovery_dialog.show_alarm_recovery": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.alarm_recovery_dialog.show_alarm_recovery",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Alarm recovery is an explicit operator action rather than an asynchronous popup raised during lock.",
    ),
    "simple_sender.ui.dialogs.execution_recovery_dialog.show_execution_recovery": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.execution_recovery_dialog.show_execution_recovery",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Execution recovery is operator-invoked; its entry point explicitly refuses to open while screen lock is active.",
    ),
    "simple_sender.ui.dialogs.macro_prompt_dialog.show_macro_prompt": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.macro_prompt_dialog.show_macro_prompt",
        classification=RUNTIME_RECOVERY_REQUIRED,
        reason="Asynchronous workflow prompt can appear during locked runtime tool-change/operator-assist flows.",
    ),
    "simple_sender.ui.dialogs.spoilboard_generator._show_post_generate_options": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.spoilboard_generator._show_post_generate_options",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Post-generate choice dialog is setup-only and not reachable from locked runtime execution.",
    ),
    "simple_sender.ui.dialogs.spoilboard_generator.show_spoilboard_generator_dialog": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.spoilboard_generator.show_spoilboard_generator_dialog",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Spoilboard generator is operator-invoked tooling, not an async runtime popup.",
    ),
    "simple_sender.ui.dialogs.streaming_metrics._show_job_completion_dialog": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dialogs.streaming_metrics._show_job_completion_dialog",
        classification=RUNTIME_RECOVERY_REQUIRED,
        reason="Job completion dialog can appear asynchronously at runtime while screen lock is still active.",
    ),
    "simple_sender.ui.dry_run_start_prompt.confirm_dry_run_start_mode": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.dry_run_start_prompt.confirm_dry_run_start_mode",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Run-start confirmation is only opened by an operator action that screen lock already blocks.",
    ),
    "simple_sender.ui.job_setup_state.confirm_job_start_without_setup": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.job_setup_state.confirm_job_start_without_setup",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Job-setup warning is only reachable from a run-start request that cannot begin while locked.",
    ),
    "simple_sender.ui.macro_panel.MacroPanel._show_macro_sample": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.macro_panel.MacroPanel._show_macro_sample",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Macro sample dialog is editor UI only and not part of active runtime popup flows.",
    ),
    "simple_sender.ui.ui_actions._confirm_run_job": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.ui_actions._confirm_run_job",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Run/resume confirmation is user-invoked and cannot be raised once screen lock is suppressing input.",
    ),
    "simple_sender.ui.widgets_keypad._show_numeric_keypad": RuntimeModalDialogPolicy(
        dialog_id="simple_sender.ui.widgets_keypad._show_numeric_keypad",
        classification=RUNTIME_RECOVERY_NOT_NEEDED,
        reason="Numeric keypad is a field-editor helper, not a runtime async popup.",
    ),
}


def get_runtime_modal_dialog_policy(dialog_id: str) -> RuntimeModalDialogPolicy:
    try:
        return RUNTIME_MODAL_DIALOG_POLICIES[str(dialog_id)]
    except KeyError as exc:
        raise KeyError(f"Runtime modal dialog policy missing for {dialog_id!r}") from exc


class RuntimeModalRecoveryController:
    """Popup-local screen-lock recovery path for grabbed runtime dialogs."""

    def __init__(
        self,
        app: Any,
        dialog: Any,
        *,
        dialog_id: str,
        action_buttons: Iterable[Any],
    ) -> None:
        policy = get_runtime_modal_dialog_policy(dialog_id)
        if policy.classification != RUNTIME_RECOVERY_REQUIRED:
            raise ValueError(
                f"{dialog_id} is classified as {policy.classification!r}, not recovery-required."
            )
        self.app = app
        self.dialog = dialog
        self.dialog_id = str(dialog_id)
        self.policy = policy
        self._action_buttons = [button for button in action_buttons if button is not None]
        self._lock_button: Any | None = None

    def attach_lock_toggle(
        self,
        parent: Any,
        *,
        kb_id: str | None = None,
        pack_kwargs: dict[str, Any] | None = None,
    ) -> Any:
        button = ttk.Button(parent, command=self.toggle_screen_lock)
        if kb_id:
            set_kb_id(button, kb_id)
        if pack_kwargs is None:
            pack_kwargs = {"side": "right"}
        button.pack(**dict(pack_kwargs))
        self._lock_button = button
        register_screen_lock_toggle_widget(self.app, button)
        self.sync()
        try:
            self.dialog.bind("<Destroy>", lambda _event: self.cleanup(), add="+")
        except Exception:
            pass
        return button

    def cleanup(self) -> None:
        if self._lock_button is None:
            return
        unregister_screen_lock_toggle_widget(self.app, self._lock_button)
        self._lock_button = None

    def sync(self) -> None:
        locked = bool(getattr(self.app, "_screen_lock_active", False))
        state = ["disabled"] if locked else ["!disabled"]
        for button in self._action_buttons:
            try:
                button.state(state)
            except Exception:
                continue
        if self._lock_button is not None:
            try:
                self._lock_button.state(["!disabled"])
            except Exception:
                pass

    def toggle_screen_lock(self) -> None:
        toggle_screen_lock(self.app, parent=self.dialog)
        self.sync()


__all__ = [
    "RUNTIME_MODAL_DIALOG_POLICIES",
    "RUNTIME_RECOVERY_NOT_NEEDED",
    "RUNTIME_RECOVERY_REQUIRED",
    "RuntimeModalDialogPolicy",
    "RuntimeModalRecoveryController",
    "get_runtime_modal_dialog_policy",
]
