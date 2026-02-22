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

"""Shared stream UI-state helpers used by streaming/status event handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from

LogHook = Callable[[str, BaseException], None]
JobReadyHook = Callable[..., bool]
SetRunResumeHook = Callable[[Any, bool], None]


def manual_controls_allowed(app: Any) -> bool:
    return bool(
        app.connected and app._grbl_ready and app._status_seen and not app._alarm_locked
    )


def restore_controls_after_stream(
    app: Any,
    *,
    loaded_total: int | None = None,
    job_ready_hook: JobReadyHook = job_controls_ready,
    set_run_resume_hook: SetRunResumeHook = set_run_resume_from,
) -> None:
    if loaded_total is None:
        ready = job_ready_hook(app)
    else:
        ready = job_ready_hook(app, bool(loaded_total))
    set_run_resume_hook(app, ready)
    app._set_manual_controls_enabled(manual_controls_allowed(app))
    app._set_streaming_lock(False)


def apply_stream_busy_state(
    app: Any,
    stream_busy: bool,
    *,
    log_hook: LogHook | None = None,
) -> None:
    try:
        app.settings_controller.set_streaming_lock(bool(stream_busy))
    except (AttributeError, TypeError) as exc:
        if log_hook is not None:
            log_hook("Failed updating settings streaming lock", exc)
    try:
        app.toolpath_panel.set_streaming(bool(stream_busy))
    except (AttributeError, TypeError) as exc:
        if log_hook is not None:
            log_hook("Failed updating toolpath streaming mode", exc)
    if stream_busy:
        return
    _flush_deferred_when_idle(app, log_hook=log_hook)


def _flush_deferred_when_idle(app: Any, *, log_hook: LogHook | None = None) -> None:
    if (
        getattr(app, "_pending_settings_refresh", False)
        and app._grbl_ready
        and not app._alarm_locked
        and not app.grbl.is_streaming()
    ):
        app._pending_settings_refresh = False
        try:
            app._request_settings_dump()
        except (AttributeError, TypeError, RuntimeError) as exc:
            if log_hook is not None:
                log_hook("Failed requesting deferred settings dump", exc)
    if (
        app._toolpath_reparse_deferred
        and app._last_gcode_lines
        and not getattr(app, "_gcode_streaming_mode", False)
    ):
        app._toolpath_reparse_deferred = False
        try:
            app.toolpath_panel.reparse_lines(app._last_gcode_lines, lines_hash=app._gcode_hash)
        except (AttributeError, TypeError, RuntimeError) as exc:
            if log_hook is not None:
                log_hook("Failed reparsing deferred toolpath lines", exc)
