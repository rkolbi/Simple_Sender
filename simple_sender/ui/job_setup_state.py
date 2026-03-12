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
import math
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from simple_sender.ui.dialogs.popup_utils import center_window

logger = logging.getLogger(__name__)
_UNAVAILABLE = object()
_INVALID_TOOL_REF_TEXT = frozenset(
    {
        "",
        "none",
        "null",
        "unknown",
        "unset",
        "uninitialized",
        "n/a",
        "na",
        "--",
        "---",
        "nan",
    }
)
_WARNING_TITLE = "Job Setup Not Completed"
_WARNING_BODY = (
    "Job Setup has not been completed for this session. Work zero and tool reference may not "
    "be set correctly. Starting now could cause the job to run at the wrong position or depth, "
    "and tool changes may not be compensated correctly.\n\n"
    "Do you want to start the job anyway?"
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _acquire_lock(lock: Any, *, blocking: bool) -> bool:
    acquire = getattr(lock, "acquire", None)
    if not callable(acquire):
        return False
    try:
        return bool(acquire(blocking=blocking))
    except TypeError:
        try:
            return bool(acquire(blocking))
        except TypeError:
            try:
                return bool(acquire())
            except Exception:
                return False
    except Exception:
        return False


def _release_lock(lock: Any) -> None:
    release = getattr(lock, "release", None)
    if not callable(release):
        return
    try:
        release()
    except Exception as exc:
        _log_suppressed("Failed releasing macro vars lock", exc)


def _read_tool_reference_from_macro_state(app: Any, *, blocking: bool) -> object:
    macro_executor = getattr(app, "macro_executor", None)
    lock = getattr(macro_executor, "_macro_vars_lock", None)
    macro_vars = getattr(macro_executor, "_macro_vars", None)
    if (
        lock is not None
        and isinstance(macro_vars, dict)
        and hasattr(lock, "acquire")
        and hasattr(lock, "release")
    ):
        acquired = _acquire_lock(lock, blocking=blocking)
        if not acquired:
            return _UNAVAILABLE
        try:
            macro_ns = macro_vars.get("macro")
            state = getattr(macro_ns, "state", None)
            return getattr(state, "TOOL_REFERENCE", None) if state is not None else None
        except Exception:
            return _UNAVAILABLE
        finally:
            _release_lock(lock)
    if macro_executor is not None and hasattr(macro_executor, "macro_vars"):
        try:
            with macro_executor.macro_vars() as macro_vars_ctx:
                if not isinstance(macro_vars_ctx, dict):
                    return _UNAVAILABLE
                macro_ns = macro_vars_ctx.get("macro")
                state = getattr(macro_ns, "state", None)
                return getattr(state, "TOOL_REFERENCE", None) if state is not None else None
        except Exception:
            return _UNAVAILABLE
    return _UNAVAILABLE


def _clear_tool_reference_macro_state(app: Any) -> None:
    macro_executor = getattr(app, "macro_executor", None)
    if macro_executor is None:
        return
    lock = getattr(macro_executor, "_macro_vars_lock", None)
    macro_vars = getattr(macro_executor, "_macro_vars", None)
    if (
        lock is not None
        and isinstance(macro_vars, dict)
        and hasattr(lock, "acquire")
        and hasattr(lock, "release")
    ):
        acquired = _acquire_lock(lock, blocking=True)
        if not acquired:
            return
        try:
            macro_ns = macro_vars.get("macro")
            state = getattr(macro_ns, "state", None)
            if state is not None:
                setattr(state, "TOOL_REFERENCE", None)
        except Exception as exc:
            _log_suppressed("Failed clearing TOOL_REFERENCE from macro state", exc)
        finally:
            _release_lock(lock)
        return
    if hasattr(macro_executor, "macro_vars"):
        try:
            with macro_executor.macro_vars() as macro_vars_ctx:
                if not isinstance(macro_vars_ctx, dict):
                    return
                macro_ns = macro_vars_ctx.get("macro")
                state = getattr(macro_ns, "state", None)
                if state is not None:
                    setattr(state, "TOOL_REFERENCE", None)
        except Exception as exc:
            _log_suppressed("Failed clearing TOOL_REFERENCE via macro_vars context", exc)


def _label_tool_reference_fallback(app: Any) -> object:
    var = getattr(app, "tool_reference_var", None)
    getter = getattr(var, "get", None)
    if not callable(getter):
        return _UNAVAILABLE
    try:
        return getter()
    except Exception:
        return _UNAVAILABLE


def _coerce_tool_reference_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except Exception:
            return None
        if not math.isfinite(number):
            return None
        return number
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("tool ref"):
        _prefix, _sep, candidate = text.partition(":")
        text = candidate.strip()
        if not text:
            return None
    if text.lower() in _INVALID_TOOL_REF_TEXT:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def has_valid_job_setup_state(app: Any) -> bool:
    value = _read_tool_reference_from_macro_state(app, blocking=False)
    if value is _UNAVAILABLE:
        value = _label_tool_reference_fallback(app)
    return _coerce_tool_reference_number(value) is not None


def invalidate_job_setup_state(app: Any) -> None:
    _clear_tool_reference_macro_state(app)
    try:
        setattr(app, "_tool_reference_last", None)
    except Exception:
        pass
    try:
        tool_reference_var = getattr(app, "tool_reference_var", None)
        setter = getattr(tool_reference_var, "set", None)
        if callable(setter):
            setter("")
    except Exception as exc:
        _log_suppressed("Failed clearing tool_reference_var text", exc)


def confirm_job_start_without_setup(app: Any) -> bool:
    try:
        dialog = tk.Toplevel(app)
        dialog.title(_WARNING_TITLE)
        dialog.transient(app)
        dialog.resizable(False, False)
        dialog.configure(padx=16, pady=12)
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
