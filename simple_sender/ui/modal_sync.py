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

"""Helpers for truthful modal-state sync requests."""

from __future__ import annotations

import time

_MODAL_SYNC_TIMEOUT_S = 3.0
_MODAL_SYNC_RETRY_DELAY_S = 1.0


def _report_modal_sync_message(
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


def modal_sync_allowed(app) -> bool:
    try:
        if not bool(getattr(app, "connected", False)):
            return False
        if not bool(getattr(app, "_grbl_ready", False)):
            return False
        if bool(getattr(app, "_alarm_locked", False)):
            return False
        if bool(getattr(app, "_stream_done_pending_idle", False)):
            return False
        stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
        if stream_state in {"running", "paused"}:
            return False
        grbl = getattr(app, "grbl", None)
        if grbl is not None and hasattr(grbl, "is_streaming"):
            try:
                if bool(grbl.is_streaming()):
                    return False
            except Exception:
                return False
        return True
    except Exception:
        return False


def _modal_sync_timeout_s(app) -> float:
    try:
        timeout_s = float(
            getattr(app, "_modal_sync_timeout_s", _MODAL_SYNC_TIMEOUT_S) or _MODAL_SYNC_TIMEOUT_S
        )
    except Exception:
        timeout_s = _MODAL_SYNC_TIMEOUT_S
    return max(0.1, timeout_s)


def _modal_sync_retry_delay_s(app) -> float:
    try:
        delay_s = float(
            getattr(app, "_modal_sync_retry_delay_s", _MODAL_SYNC_RETRY_DELAY_S)
            or _MODAL_SYNC_RETRY_DELAY_S
        )
    except Exception:
        delay_s = _MODAL_SYNC_RETRY_DELAY_S
    return max(0.1, delay_s)


def _modal_sync_now() -> float:
    try:
        return float(time.monotonic())
    except Exception:
        return 0.0


def _set_modal_sync_retry_after(app, delay_s: float) -> None:
    setattr(app, "_modal_sync_retry_after_ts", float(_modal_sync_now() + max(0.0, delay_s)))


def _modal_sync_retry_window_open(app) -> bool:
    retry_after_ts = float(getattr(app, "_modal_sync_retry_after_ts", 0.0) or 0.0)
    return retry_after_ts <= 0.0 or _modal_sync_now() >= retry_after_ts


def _expire_modal_sync_if_timed_out(
    app,
    *,
    timeout_status: str | None = None,
    timeout_log: str | None = None,
) -> bool:
    if not bool(getattr(app, "_modal_sync_inflight", False)):
        return False
    started_ts = float(getattr(app, "_modal_sync_inflight_started_ts", 0.0) or 0.0)
    if started_ts <= 0.0:
        return False
    if (_modal_sync_now() - started_ts) < _modal_sync_timeout_s(app):
        return False
    setattr(app, "_modal_sync_inflight", False)
    setattr(app, "_modal_sync_inflight_started_ts", 0.0)
    _set_modal_sync_retry_after(app, _modal_sync_retry_delay_s(app))
    _report_modal_sync_message(
        app,
        status_text=timeout_status,
        log_text=timeout_log,
    )
    return True


def modal_sync_retry_ready(
    app,
    *,
    timeout_status: str | None = None,
    timeout_log: str | None = None,
) -> bool:
    if not bool(getattr(app, "_pending_modal_sync", False)):
        return False
    _expire_modal_sync_if_timed_out(
        app,
        timeout_status=timeout_status,
        timeout_log=timeout_log,
    )
    if bool(getattr(app, "_modal_sync_inflight", False)):
        return False
    return _modal_sync_retry_window_open(app)


def request_modal_state_sync(
    app,
    *,
    source: str = "status",
    failure_status: str | None = None,
    failure_log: str | None = None,
    timeout_status: str | None = None,
    timeout_log: str | None = None,
) -> bool:
    setattr(app, "_pending_modal_sync", True)
    _expire_modal_sync_if_timed_out(
        app,
        timeout_status=timeout_status,
        timeout_log=timeout_log,
    )
    if not modal_sync_allowed(app):
        setattr(app, "_modal_sync_inflight", False)
        return False
    if bool(getattr(app, "_modal_sync_inflight", False)):
        return True
    if not _modal_sync_retry_window_open(app):
        return False
    accepted = True
    try:
        accepted = app._send_manual("$G", source)
    except Exception:
        accepted = False
    if accepted is False:
        setattr(app, "_modal_sync_inflight", False)
        setattr(app, "_modal_sync_inflight_started_ts", 0.0)
        _set_modal_sync_retry_after(app, _modal_sync_retry_delay_s(app))
        _report_modal_sync_message(
            app,
            status_text=failure_status,
            log_text=failure_log,
        )
        return False
    setattr(app, "_modal_sync_inflight", True)
    setattr(app, "_modal_sync_inflight_started_ts", _modal_sync_now())
    setattr(app, "_modal_sync_retry_after_ts", 0.0)
    return True


def clear_modal_sync_state(app) -> None:
    setattr(app, "_pending_modal_sync", False)
    setattr(app, "_modal_sync_inflight", False)
    setattr(app, "_modal_sync_inflight_started_ts", 0.0)
    setattr(app, "_modal_sync_retry_after_ts", 0.0)


__all__ = [
    "clear_modal_sync_state",
    "modal_sync_allowed",
    "modal_sync_retry_ready",
    "request_modal_state_sync",
]
