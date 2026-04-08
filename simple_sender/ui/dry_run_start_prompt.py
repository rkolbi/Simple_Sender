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

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from simple_sender.services.job_service import DryRunStartDecision
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window

logger = logging.getLogger(__name__)

_TITLE = "Dry Run Enabled"
_BODY = "Dry Run is currently enabled.\n\nHow would you like to continue?"
_BTN_DRY_RUN = "Continue in Dry Run"
_BTN_CANCEL = "Cancel"


def _log_suppressed(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _normal_run_button_text(action_label: str) -> str:
    action = str(action_label or "").strip() or "Start"
    return f"Switch to Normal Run and {action}"


def _fallback_prompt(
    *,
    normal_run_action: str,
    messagebox_module=messagebox,
) -> DryRunStartDecision:
    normal_run_text = _normal_run_button_text(normal_run_action)
    try:
        choice = messagebox_module.askyesnocancel(
            _TITLE,
            (
                f"{_BODY}\n\n"
                f"Yes: {_BTN_DRY_RUN}\n"
                f"No: {normal_run_text}\n"
                f"Cancel: {_BTN_CANCEL}"
            ),
        )
    except Exception as exc:
        _log_suppressed("Failed showing fallback Dry Run confirmation dialog", exc)
        return DryRunStartDecision.CANCEL
    if choice is True:
        return DryRunStartDecision.CONTINUE_DRY_RUN
    if choice is False:
        return DryRunStartDecision.SWITCH_TO_NORMAL_RUN
    return DryRunStartDecision.CANCEL


def confirm_dry_run_start_mode(
    app: Any,
    *,
    normal_run_action: str = "Start",
    messagebox_module=messagebox,
) -> DryRunStartDecision:
    normal_run_text = _normal_run_button_text(normal_run_action)
    try:
        dialog = tk.Toplevel(app)
        dialog.title(_TITLE)
        dialog.transient(app)
        dialog.resizable(False, False)
        dialog.configure(padx=16, pady=12)
        apply_toplevel_theme(dialog, app)
    except Exception as exc:
        _log_suppressed("Failed creating Dry Run confirmation dialog; using fallback", exc)
        return _fallback_prompt(
            normal_run_action=normal_run_action,
            messagebox_module=messagebox_module,
        )

    result = {"decision": DryRunStartDecision.CANCEL}
    body = ttk.Frame(dialog)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=_BODY, wraplength=520, justify="left").pack(fill="x")
    btn_row = ttk.Frame(body)
    btn_row.pack(fill="x", pady=(12, 0))

    def _choose(decision: DryRunStartDecision) -> None:
        result["decision"] = decision
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed closing Dry Run confirmation dialog", exc)

    ttk.Button(
        btn_row,
        text=_BTN_DRY_RUN,
        command=lambda: _choose(DryRunStartDecision.CONTINUE_DRY_RUN),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        btn_row,
        text=normal_run_text,
        command=lambda: _choose(DryRunStartDecision.SWITCH_TO_NORMAL_RUN),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        btn_row,
        text=_BTN_CANCEL,
        command=lambda: _choose(DryRunStartDecision.CANCEL),
    ).pack(side="left")

    dialog.protocol(
        "WM_DELETE_WINDOW",
        lambda: _choose(DryRunStartDecision.CANCEL),
    )
    center_window(dialog, app)
    try:
        dialog.grab_set()
    except Exception as exc:
        _log_suppressed("Failed setting Dry Run confirmation dialog grab", exc)
    dialog.wait_window()
    return result["decision"]


__all__ = [
    "confirm_dry_run_start_mode",
]
