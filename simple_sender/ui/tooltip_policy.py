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

from typing import Callable, Any


def resolve_disabled_reason(widget: Any, resolve_owner: Callable[[Any, str], Any]) -> str | None:
    reason = getattr(widget, "_disabled_reason", None)
    if reason:
        return str(reason)
    app = resolve_owner(widget, "connected")
    if app is None:
        return None
    connected = bool(getattr(app, "connected", False))
    grbl_ready = bool(getattr(app, "_grbl_ready", False))
    status_seen = bool(getattr(app, "_status_seen", False))
    alarm_locked = bool(getattr(app, "_alarm_locked", False))
    connecting = bool(getattr(app, "_connecting", False))
    disconnecting = bool(getattr(app, "_disconnecting", False))
    done_pending_idle = bool(getattr(app, "_stream_done_pending_idle", False))
    stream_state = getattr(app, "_stream_state", None)
    loading = bool(getattr(app, "_gcode_loading", False))

    def basic_reason() -> str | None:
        if connecting:
            return "Connecting to controller."
        if disconnecting:
            return "Disconnecting from controller."
        if alarm_locked:
            return "Clear the alarm to enable."
        if not connected:
            return "Connect to enable."
        if not grbl_ready:
            return "Waiting for Grbl startup handshake."
        if not status_seen:
            return "Waiting for first status report."
        if done_pending_idle:
            return "Waiting for machine to return to Idle."
        if stream_state == "running":
            return "Disabled while job is running."
        if stream_state == "paused":
            return "Disabled while job is paused."
        if loading:
            return "Waiting for G-code to load."
        return None

    if widget in getattr(app, "_manual_controls", []):
        return basic_reason() or "Unavailable in current state."
    if widget is getattr(app, "btn_conn", None):
        if stream_state in ("running", "paused") or done_pending_idle:
            return "Stop the stream before disconnecting."
        return basic_reason()
    if widget is getattr(app, "btn_refresh", None):
        if stream_state in ("running", "paused") or done_pending_idle:
            return "Stop the stream before refreshing ports."
        return basic_reason()
    if widget is getattr(app, "port_combo", None):
        if stream_state in ("running", "paused") or done_pending_idle:
            return "Stop the stream before changing ports."
        return basic_reason()
    if widget is getattr(app, "btn_unit_toggle", None):
        if stream_state in ("running", "paused") or done_pending_idle:
            return "Unit toggle disabled while streaming."
        return basic_reason()
    if widget is getattr(app, "btn_open", None):
        if stream_state in ("running", "paused") or done_pending_idle:
            return "Stop the stream to load a new job."
        return basic_reason()
    if widget is getattr(app, "btn_run", None):
        if loading:
            return "Wait for G-code to finish loading."
        if not getattr(app, "gview", None) or not getattr(app.gview, "lines_count", 0):
            return "Load a job to enable."
        if stream_state in ("running", "paused"):
            return "Job already running."
        return basic_reason()
    if widget is getattr(app, "btn_pause", None):
        if stream_state == "paused":
            return "Job already paused."
        if stream_state not in ("running",):
            return "Start a job to enable."
        return basic_reason()
    if widget is getattr(app, "btn_resume", None):
        if stream_state != "paused":
            return "Pause a job to enable."
        return basic_reason()
    if widget is getattr(app, "btn_resume_from", None):
        if loading:
            return "Wait for G-code to finish loading."
        if not getattr(app, "gview", None) or not getattr(app.gview, "lines_count", 0):
            return "Load a job to enable."
        return basic_reason()
    if widget is getattr(app, "btn_stop", None):
        if stream_state not in ("running", "paused"):
            return "No active job to stop."
        return basic_reason()
    return basic_reason()
