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
# SPDX-License-Identifier: GPL-3.0-or-later

import queue
import threading


def call_on_ui_thread(app, func, *args, timeout: float | None = 5.0, **kwargs):
    if threading.current_thread() is threading.main_thread():
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            app._log_exception("UI action failed", exc)
            return None
    result_q: queue.Queue = queue.Queue()
    app.ui_q.put(("ui_call", func, args, kwargs, result_q))
    try:
        if timeout is None:
            while True:
                try:
                    ok, value = result_q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if app._closing:
                        app.ui_q.put(("log", "[ui] Action canceled (closing)."))
                        return None
        else:
            ok, value = result_q.get(timeout=timeout)
    except queue.Empty:
        app.ui_q.put(("log", "[ui] Action timed out."))
        return None
    if ok:
        return value
    app.ui_q.put(("log", f"[ui] Action failed: {value}"))
    return None


def post_ui_thread(app, func, *args, **kwargs):
    app.ui_q.put(("ui_post", func, args, kwargs))
