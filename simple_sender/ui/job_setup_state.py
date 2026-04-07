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

from simple_sender.services.job_setup_service import JobSetupService
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window

logger = logging.getLogger(__name__)
_WARNING_TITLE = "Job Setup Not Completed"
_WARNING_BODY = (
    "Job Setup is missing a current valid tool reference for this session. Work zero and tool "
    "reference data may not be set correctly for the current tool-change workflow. Starting now "
    "could cause the job to run at the wrong position or depth, and later tool changes may fail "
    "or be compensated incorrectly.\n\n"
    "Do you want to start the job anyway?"
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _job_setup_service() -> JobSetupService:
    return JobSetupService(log_suppressed=_log_suppressed)


def has_valid_job_setup_state(app: Any) -> bool:
    return _job_setup_service().has_valid_setup_state(app)


def invalidate_job_setup_state(app: Any) -> None:
    _job_setup_service().invalidate_setup_state(app)


def confirm_job_start_without_setup(app: Any) -> bool:
    try:
        dialog = tk.Toplevel(app)
        dialog.title(_WARNING_TITLE)
        dialog.transient(app)
        dialog.resizable(False, False)
        dialog.configure(padx=16, pady=12)
        apply_toplevel_theme(dialog, app)
    except Exception as exc:
        _log_suppressed("Failed creating Job Setup warning dialog; using askyesno fallback", exc)
        return bool(messagebox.askyesno(_WARNING_TITLE, _WARNING_BODY))

    body = ttk.Frame(dialog)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=_WARNING_BODY, wraplength=560, justify="left").pack(fill="x")
    btn_row = ttk.Frame(body)
    btn_row.pack(fill="x", pady=(12, 0))

    result = {"start_anyway": False}

    def _start_anyway() -> None:
        result["start_anyway"] = True
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed closing Job Setup warning dialog on Start Anyway", exc)

    def _cancel() -> None:
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed closing Job Setup warning dialog on Cancel", exc)

    ttk.Button(btn_row, text="Start Anyway", command=_start_anyway).pack(
        side="left",
        padx=(0, 6),
    )
    ttk.Button(btn_row, text="Cancel", command=_cancel).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    center_window(dialog, app)
    try:
        dialog.grab_set()
    except Exception as exc:
        _log_suppressed("Failed setting Job Setup warning dialog grab", exc)
    dialog.wait_window()
    return bool(result["start_anyway"])
