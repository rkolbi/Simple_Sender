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

"""Small status-event reactions that do not need the full status router."""

from collections.abc import Callable


def schedule_request_settings_dump(app, *, log_suppressed: Callable[[str, BaseException], None]) -> None:
    if bool(getattr(app, "_settings_dump_deferred_pending", False)):
        return

    def _run() -> None:
        app._settings_dump_deferred_pending = False
        try:
            app._request_settings_dump()
        except Exception as exc:
            log_suppressed("Failed requesting deferred settings dump", exc)

    after = getattr(app, "after", None)
    if callable(after):
        try:
            app._settings_dump_deferred_pending = True
            after(0, _run)
            return
        except Exception as exc:
            app._settings_dump_deferred_pending = False
            log_suppressed("Failed scheduling deferred settings dump request", exc)
    _run()


def schedule_request_modal_state_sync(
    app,
    *,
    log_suppressed: Callable[[str, BaseException], None],
    request_modal_state_sync: Callable[..., object],
) -> None:
    if bool(getattr(app, "_modal_sync_deferred_pending", False)):
        return

    def _run() -> None:
        app._modal_sync_deferred_pending = False
        request_modal_state_sync(
            app,
            source="status",
            failure_status="Modal-state sync is pending retry.",
            failure_log="[status] $G modal sync was rejected; retry pending.",
            timeout_status="Modal-state sync timed out; retrying.",
            timeout_log="[status] $G modal sync timed out; retry pending.",
        )

    after = getattr(app, "after", None)
    if callable(after):
        try:
            app._modal_sync_deferred_pending = True
            after(0, _run)
            return
        except Exception as exc:
            app._modal_sync_deferred_pending = False
            log_suppressed("Failed scheduling deferred modal-state sync", exc)
    _run()


def maybe_restore_pending_g90(
    app,
    *,
    log_suppressed: Callable[[str, BaseException], None],
    stream_active_or_finishing: Callable[[object], bool],
) -> None:
    if not getattr(app, "_pending_force_g90", False):
        return
    snapshot = getattr(app, "_pending_force_g90_snapshot", None)
    snapshot_checker = getattr(app.grbl, "workflow_admission_snapshot_current", None)
    if snapshot is None or not callable(snapshot_checker):
        app._pending_force_g90 = False
        app._pending_force_g90_snapshot = None
        return
    try:
        snapshot_current = bool(snapshot_checker(snapshot))
    except Exception:
        snapshot_current = False
    if not snapshot_current:
        app._pending_force_g90 = False
        app._pending_force_g90_snapshot = None
        try:
            app.ui_q.put(("log", "[autolevel] Discarded pending G90 restore for a retired controller session."))
        except Exception as exc:
            log_suppressed("Failed to log retired pending G90 restore", exc)
        return
    if not app.grbl.is_connected():
        return
    if getattr(app, "_alarm_locked", False):
        return
    if app.grbl.is_streaming() or stream_active_or_finishing(app):
        return
    try:
        accepted = app.grbl.send_immediate(
            "G90",
            source="autolevel",
            expected_workflow_snapshot=snapshot,
        )
    except Exception as exc:
        log_suppressed("Failed to restore pending G90", exc)
        return
    if accepted is False:
        return
    app._pending_force_g90 = False
    app._pending_force_g90_snapshot = None
    try:
        app.ui_q.put(("log", "[autolevel] Requested pending G90 restore after alarm clear."))
    except Exception as exc:
        log_suppressed("Failed to queue G90 restore log message", exc)
