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

from __future__ import annotations

import logging
import threading
import time

from simple_sender.utils.constants import MACRO_PROMPT_TIMEOUT

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _all_stop_cancel_requested(app) -> bool:
    macro_executor = getattr(app, "macro_executor", None)
    cancel_event = getattr(macro_executor, "_alarm_event", None)
    if cancel_event is None or not hasattr(cancel_event, "is_set"):
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return False


def _snapshot_macro_timeout_state(app) -> tuple[float | None, float | None, float, bool]:
    line_timeout: float | None = None
    total_timeout: float | None = None
    line_var = getattr(app, "macro_line_timeout_sec", None)
    if line_var is not None and hasattr(line_var, "get"):
        try:
            line_timeout = float(line_var.get())
        except Exception:
            line_timeout = 0.0
    total_var = getattr(app, "macro_total_timeout_sec", None)
    if total_var is not None and hasattr(total_var, "get"):
        try:
            total_timeout = float(total_var.get())
        except Exception:
            total_timeout = 0.0
    try:
        prompt_timeout = float(getattr(app, "_macro_prompt_timeout_s", MACRO_PROMPT_TIMEOUT))
    except Exception:
        prompt_timeout = float(MACRO_PROMPT_TIMEOUT)
    no_timeout_override = bool(getattr(app, "_tool_change_unlimited_time_active", False))
    return line_timeout, total_timeout, prompt_timeout, no_timeout_override


def _apply_macro_timeout_state(
    app,
    *,
    line_timeout: float | None,
    total_timeout: float | None,
    prompt_timeout: float,
    no_timeout_override: bool,
) -> None:
    line_var = getattr(app, "macro_line_timeout_sec", None)
    if line_timeout is not None and line_var is not None and hasattr(line_var, "set"):
        try:
            line_var.set(float(line_timeout))
        except Exception as exc:
            _log_suppressed("Failed restoring macro line timeout", exc)
    total_var = getattr(app, "macro_total_timeout_sec", None)
    if total_timeout is not None and total_var is not None and hasattr(total_var, "set"):
        try:
            total_var.set(float(total_timeout))
        except Exception as exc:
            _log_suppressed("Failed restoring macro total timeout", exc)
    try:
        app._macro_prompt_timeout_s = float(prompt_timeout)
    except Exception as exc:
        _log_suppressed("Failed restoring macro prompt timeout", exc)
    try:
        app._tool_change_unlimited_time_active = bool(no_timeout_override)
    except Exception as exc:
        _log_suppressed("Failed restoring tool-change no-timeout override", exc)


def _disable_macro_timeouts_for_tool_change(
    app,
) -> tuple[float | None, float | None, float, bool]:
    saved_raw = app._call_on_ui_thread(_snapshot_macro_timeout_state, app, timeout=None)
    saved: tuple[float | None, float | None, float, bool]
    if isinstance(saved_raw, tuple) and len(saved_raw) == 4:
        raw_line_timeout, raw_total_timeout, raw_prompt_timeout, raw_no_timeout_override = saved_raw
        try:
            line_timeout = float(raw_line_timeout) if raw_line_timeout is not None else None
        except Exception:
            line_timeout = None
        try:
            total_timeout = float(raw_total_timeout) if raw_total_timeout is not None else None
        except Exception:
            total_timeout = None
        try:
            prompt_timeout = float(raw_prompt_timeout)
        except Exception:
            prompt_timeout = float(MACRO_PROMPT_TIMEOUT)
        no_timeout_override = bool(raw_no_timeout_override)
        saved = (line_timeout, total_timeout, prompt_timeout, no_timeout_override)
    else:
        saved = (
            None,
            None,
            float(MACRO_PROMPT_TIMEOUT),
            bool(getattr(app, "_tool_change_unlimited_time_active", False)),
        )
    line_var = getattr(app, "macro_line_timeout_sec", None)
    if line_var is not None and hasattr(line_var, "set"):
        app._call_on_ui_thread(line_var.set, 0.0, timeout=None)
    total_var = getattr(app, "macro_total_timeout_sec", None)
    if total_var is not None and hasattr(total_var, "set"):
        app._call_on_ui_thread(total_var.set, 0.0, timeout=None)
    try:
        app._macro_prompt_timeout_s = 0.0
    except Exception as exc:
        _log_suppressed("Failed disabling macro prompt timeout for tool change", exc)
    try:
        app._tool_change_unlimited_time_active = True
    except Exception as exc:
        _log_suppressed("Failed enabling tool-change no-timeout override", exc)
    return saved


def _restore_macro_timeouts_for_tool_change(
    app,
    saved: tuple[float | None, float | None, float, bool],
) -> None:
    line_timeout, total_timeout, prompt_timeout, no_timeout_override = saved
    app._call_on_ui_thread(
        _apply_macro_timeout_state,
        app,
        line_timeout=line_timeout,
        total_timeout=total_timeout,
        prompt_timeout=prompt_timeout,
        no_timeout_override=no_timeout_override,
        timeout=None,
    )


def _wait_for_macro_finish(app) -> bool:
    executor = getattr(app, "macro_executor", None)
    lock = getattr(executor, "_macro_lock", None)
    if lock is None or not hasattr(lock, "locked"):
        return False
    while True:
        try:
            if not bool(lock.locked()):
                break
        except Exception:
            return False
        if _all_stop_cancel_requested(app):
            return False
        if bool(getattr(app, "_closing", False)):
            return False
        time.sleep(0.1)
    return bool(getattr(executor, "_last_macro_run_success", False))


def _tool_change_macro_prompt_cancelled(app) -> bool:
    executor = getattr(app, "macro_executor", None)
    vars_ctx = getattr(executor, "macro_vars", None)
    if not callable(vars_ctx):
        return False
    try:
        with vars_ctx() as macro_vars:
            return bool(macro_vars.get("prompt_cancelled", False))
    except Exception:
        return False


def _run_stream_tool_change_worker(app, tool_name: str, line_index: int | None) -> None:
    _ = line_index
    try:
        saved_timeouts = _disable_macro_timeouts_for_tool_change(app)
    except Exception as exc:
        _log_suppressed("Failed preparing tool-change workflow timeout state", exc)
        app.grbl.complete_stream_tool_change(False, "Tool-change workflow setup failed.")
        return
    started = False
    succeeded = False
    try:
        try:
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["tool_change_required_tool_name"] = str(tool_name or "")
                macro_vars["tool_change_context"] = "stream"
                macro_ns = macro_vars.get("macro")
                state_ns = getattr(macro_ns, "state", None)
                if state_ns is not None:
                    setattr(state_ns, "TOOL_CHANGE_CONTEXT", "stream")
                    setattr(state_ns, "REQUIRED_TOOL_NAME", str(tool_name or ""))
        except Exception as exc:
            _log_suppressed("Failed storing required tool name in macro vars", exc)

        started = bool(
            app._call_on_ui_thread(
                app.macro_executor.run_macro,
                4,
                True,
                timeout=None,
            )
        )
        if started:
            succeeded = _wait_for_macro_finish(app)
    except Exception as exc:
        _log_suppressed("Tool-change workflow failed", exc)
        started = False
        succeeded = False
    finally:
        try:
            _restore_macro_timeouts_for_tool_change(app, saved_timeouts)
        except Exception as exc:
            _log_suppressed("Failed restoring tool-change workflow timeout state", exc)
    if started and succeeded:
        app.grbl.complete_stream_tool_change(True)
        return
    reason = "Tool-change macro failed."
    if _all_stop_cancel_requested(app):
        reason = "Canceled by ALL STOP."
    elif _tool_change_macro_prompt_cancelled(app):
        reason = "Tool change canceled by user."
    app.grbl.complete_stream_tool_change(False, reason)


def handle_stream_tool_change(app, tool_name: str, *, line_index: int | None = None) -> None:
    existing = getattr(app, "_stream_tool_change_thread", None)
    if isinstance(existing, threading.Thread) and existing.is_alive():
        return
    worker = threading.Thread(
        target=_run_stream_tool_change_worker,
        args=(app, str(tool_name or ""), line_index),
        daemon=True,
        name="tool-change-workflow",
    )
    app._stream_tool_change_thread = worker
    worker.start()


__all__ = ["handle_stream_tool_change"]
