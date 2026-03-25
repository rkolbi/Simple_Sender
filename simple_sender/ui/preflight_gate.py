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

"""Shared UI-facing preflight gate helpers."""

from __future__ import annotations

from tkinter import messagebox as tk_messagebox

from simple_sender.services.preflight_service import PreflightService

_PREFLIGHT_SERVICE = PreflightService()


def _report_operator_message(
    app,
    *,
    status_text: str | None = None,
    log_text: str | None = None,
) -> None:
    if status_text:
        try:
            app.status.config(text=status_text)
        except Exception:
            pass
    if log_text:
        ui_q = getattr(app, "ui_q", None)
        if ui_q is None:
            return
        try:
            ui_q.put(("log", log_text))
        except Exception:
            return


def run_preflight_gate(app, *, action_label: str, messagebox_module=tk_messagebox) -> bool:
    label = str(action_label or "").strip() or "Run"
    label_lower = label.lower()
    result = _PREFLIGHT_SERVICE.validate_job(app)
    if result.failures:
        message = "\n".join(result.failures)
        _report_operator_message(
            app,
            status_text=f"{label} blocked: {result.failures[0]}",
            log_text=f"[{label_lower}] Preflight blocked {label_lower}: {message}",
        )
        messagebox_module.showwarning(f"{label} blocked", message)
        return False
    if result.warnings:
        message = "\n".join(result.warnings)
        _report_operator_message(
            app,
            status_text=f"{label} warning: {result.warnings[0]}",
            log_text=f"[{label_lower}] Preflight warning: {message}",
        )
        if not messagebox_module.askyesno(
            f"{label} warning",
            f"{message}\n\nContinue with {label}?",
        ):
            _report_operator_message(
                app,
                status_text=f"{label} canceled after preflight warning",
                log_text=f"[{label_lower}] Operator canceled after preflight warning.",
            )
            return False
    return True


__all__ = ["run_preflight_gate"]
