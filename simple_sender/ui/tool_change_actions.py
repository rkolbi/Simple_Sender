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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import time

from simple_sender.ui.threading_utils import UI_CALL_DISPATCHED, UI_CALL_HANDOFF_FAILED

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _all_stop_cancel_requested(app) -> bool:
    macro_executor = getattr(app, "macro_executor", None)
    cancel_event = getattr(macro_executor, "_alarm_event", None)
    if cancel_event is None or not hasattr(cancel_event, "is_set"):
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return False


def _snapshot_macro_timeout_state(app) -> bool:
    return bool(getattr(app, "_tool_change_unlimited_time_active", False))


def _apply_macro_timeout_state(
    app,
    *,
    no_timeout_override: bool,
) -> None:
    try:
        app._tool_change_unlimited_time_active = bool(no_timeout_override)
    except Exception as exc:
        _log_suppressed("Failed restoring tool-change no-timeout override", exc)


def _disable_macro_timeouts_for_tool_change(
    app,
) -> bool:
    saved_raw = app._call_on_ui_thread(_snapshot_macro_timeout_state, app)
    saved = bool(saved_raw) if isinstance(saved_raw, bool) else bool(
        getattr(app, "_tool_change_unlimited_time_active", False)
    )
    try:
        app._tool_change_unlimited_time_active = True
    except Exception as exc:
        _log_suppressed("Failed enabling tool-change no-timeout override", exc)
    return saved


def _restore_macro_timeouts_for_tool_change(
    app,
    saved: bool,
) -> None:
    app._call_on_ui_thread(
        _apply_macro_timeout_state,
        app,
        no_timeout_override=bool(saved),
    )


def _tool_change_workflow_timeout_s(app) -> float:
    if bool(getattr(app, "_tool_change_unlimited_time_active", False)) or bool(
        getattr(app, "_operator_assisted_workflow_unlimited_time_active", False)
    ):
        return 0.0
    try:
        timeout_s = float(getattr(app, "_tool_change_workflow_timeout_s", 1800.0) or 0.0)
    except Exception:
        timeout_s = 1800.0
    return max(30.0, timeout_s)


def _wait_for_workflow_finish(app, *, timeout_s: float) -> str:
    executor = getattr(app, "macro_executor", None)
    lock = getattr(executor, "_macro_lock", None)
    if lock is None or not hasattr(lock, "locked"):
        return "failed"
    started_at = time.monotonic()
    while True:
        try:
            if not bool(lock.locked()):
                break
        except Exception:
            return "failed"
        if _all_stop_cancel_requested(app):
            return "cancelled"
        if bool(getattr(app, "_closing", False)):
            return "closed"
        if timeout_s > 0.0 and (time.monotonic() - started_at) >= timeout_s:
            return "timed_out"
        time.sleep(0.1)
    return "success" if bool(getattr(executor, "_last_macro_run_success", False)) else "failed"


def _tool_change_workflow_prompt_cancelled(app) -> bool:
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
    timed_out = False
    handoff_failed = False
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

        started_result = app._call_on_ui_thread(
            app.macro_executor.run_builtin_workflow,
            "tool_change",
            True,
            timeout=None,
            return_on_handoff=True,
        )
        if started_result is UI_CALL_HANDOFF_FAILED:
            started = False
            handoff_failed = True
        else:
            started = started_result is UI_CALL_DISPATCHED or bool(started_result)
        if started:
            workflow_timeout_s = _tool_change_workflow_timeout_s(app)
            wait_status = _wait_for_workflow_finish(app, timeout_s=workflow_timeout_s)
            succeeded = wait_status == "success"
            timed_out = wait_status == "timed_out"
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
    reason = "Tool-change workflow failed."
    if _all_stop_cancel_requested(app):
        reason = "Canceled by ALL STOP."
    elif handoff_failed:
        reason = "Tool-change workflow failed: UI handoff timed out."
    elif timed_out:
        cancel_macro = getattr(app.macro_executor, "cancel_macro", None)
        if callable(cancel_macro):
            try:
                cancel_macro("Tool-change workflow timed out.")
            except Exception as exc:
                _log_suppressed("Failed canceling timed-out tool-change workflow", exc)
        reason = "Tool-change workflow timed out."
    elif _tool_change_workflow_prompt_cancelled(app):
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

