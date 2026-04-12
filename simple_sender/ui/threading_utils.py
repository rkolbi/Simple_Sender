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

import queue
import threading

from simple_sender.utils.constants import (
    UI_THREAD_CALL_DEFAULT_TIMEOUT,
    UI_THREAD_CALL_POLL_INTERVAL,
)


class _UiCallMarker:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = str(name)

    def __repr__(self) -> str:
        return self.name


UI_CALL_DISPATCHED = _UiCallMarker("UI_CALL_DISPATCHED")
UI_CALL_HANDOFF_FAILED = _UiCallMarker("UI_CALL_HANDOFF_FAILED")


def _emit_ui_log(app, message: str) -> None:
    try:
        app.ui_q.put(("log", str(message)))
    except Exception:
        return


def call_on_ui_thread(
    app,
    func,
    *args,
    timeout: float | None = UI_THREAD_CALL_DEFAULT_TIMEOUT,
    return_on_handoff: bool = False,
    **kwargs,
):
    if threading.current_thread() is threading.main_thread():
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            app._log_exception("UI action failed", exc)
            return None
    result_q: queue.Queue = queue.Queue(maxsize=1)
    cancel_token = threading.Event()
    start_q: queue.Queue[bool] | None = None
    if timeout is None or bool(return_on_handoff):
        start_q = queue.Queue(maxsize=1)
    try:
        if start_q is None:
            app.ui_q.put(("ui_call", func, args, kwargs, result_q, cancel_token))
        else:
            app.ui_q.put(("ui_call", func, args, kwargs, result_q, cancel_token, start_q))
    except Exception:
        _emit_ui_log(app, "[ui] Action handoff failed.")
        return UI_CALL_HANDOFF_FAILED if start_q is not None else None
    if start_q is not None:
        try:
            start_q.get(timeout=UI_THREAD_CALL_DEFAULT_TIMEOUT)
        except queue.Empty:
            cancel_token.set()
            _emit_ui_log(app, "[ui] Action handoff timed out.")
            return UI_CALL_HANDOFF_FAILED
        if return_on_handoff:
            try:
                ok, value = result_q.get_nowait()
            except queue.Empty:
                return UI_CALL_DISPATCHED
            if ok:
                return value
            _emit_ui_log(app, f"[ui] Action failed: {value}")
            return None
    try:
        if timeout is None:
            while True:
                try:
                    ok, value = result_q.get(timeout=UI_THREAD_CALL_POLL_INTERVAL)
                    break
                except queue.Empty:
                    if app._closing:
                        cancel_token.set()
                        _emit_ui_log(app, "[ui] Action canceled (closing).")
                        return None
        else:
            ok, value = result_q.get(timeout=timeout)
    except queue.Empty:
        cancel_token.set()
        _emit_ui_log(app, "[ui] Action timed out.")
        return None
    if ok:
        return value
    _emit_ui_log(app, f"[ui] Action failed: {value}")
    return None


def post_ui_thread(app, func, *args, **kwargs):
    app.ui_q.put(("ui_post", func, args, kwargs))
