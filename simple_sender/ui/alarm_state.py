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

from simple_sender.ui.job_controls import (
    disable_job_controls,
    job_controls_ready,
    set_run_resume_from,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def format_alarm_message(message: str | None) -> str:
    if not message:
        return "ALARM"
    text = str(message).strip()
    if text.lower().startswith("alarm"):
        return text
    if "reset to continue" in text.lower():
        return "ALARM: Reset to continue"
    if text.startswith("[MSG:"):
        return f"ALARM: {text}"
    return f"ALARM: {text}"


def mark_alarm_clear_requested(app) -> None:
    try:
        app._alarm_clear_requested = True
    except Exception as exc:
        _log_suppressed("Failed marking alarm-clear request", exc)


def set_alarm_lock(app, locked: bool, message: str | None = None):
    if locked:
        app._alarm_locked = True
        app._alarm_latched = True
        app._alarm_clear_requested = False
        if message:
            app._alarm_message = message
        disable_job_controls(app)
        try:
            app.btn_alarm_recover.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling Alarm Recover button when alarm lock engages", exc)
        app._set_manual_controls_enabled(True)
        try:
            app.status.config(text=format_alarm_message(message or app._alarm_message))
        except Exception as exc:
            _log_suppressed("Failed updating status text when alarm lock engages", exc)
        app._machine_state_text = "Alarm"
        app.machine_state.set("Alarm")
        app._start_state_flash("#ff5252")
        return

    if not app._alarm_locked and not bool(getattr(app, "_alarm_latched", False)):
        return
    app._alarm_locked = False
    app._alarm_latched = False
    app._alarm_clear_requested = False
    app._alarm_message = ""
    app.macro_executor.clear_alarm_notification()
    try:
        app.btn_alarm_recover.config(state="disabled")
    except Exception as exc:
        _log_suppressed("Failed disabling Alarm Recover button when alarm lock clears", exc)
    if (
        app.connected
        and app._grbl_ready
        and app._status_seen
        and app._stream_state not in ("running", "paused")
        and not bool(getattr(app, "_stream_done_pending_idle", False))
    ):
        app._set_manual_controls_enabled(True)
        if job_controls_ready(app):
            set_run_resume_from(app, True)
    status_text = ""
    try:
        status_text = app.status.cget("text")
    except Exception as exc:
        _log_suppressed("Failed reading status text while clearing alarm lock", exc)
    if app.connected and status_text.startswith("ALARM"):
        app.status.config(text=f"Connected: {app._connected_port}")
    if app._pending_settings_refresh and app._grbl_ready:
        app._pending_settings_refresh = False
        app._request_settings_dump()
    app.machine_state.set(app._machine_state_text)
    app._update_state_highlight(app._machine_state_text)
    app._apply_status_poll_profile()
