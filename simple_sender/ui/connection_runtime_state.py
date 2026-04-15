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

from collections import deque
from dataclasses import dataclass, field

CONNECTION_TIMELINE_LIMIT = 200


@dataclass(slots=True)
class ConnectionRuntimeState:
    connected: bool = False
    connecting: bool = False
    disconnecting: bool = False
    ready: bool = False
    status_seen: bool = False
    alarm_locked: bool = False
    connected_port: str | None = None
    status_connect_settling_until_ts: float = 0.0
    ui_sync_failed: bool = False
    ui_sync_failure_phase: str = ""
    timeline: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=CONNECTION_TIMELINE_LIMIT)
    )


def _safe_bool_attr(app: object, name: str, fallback: bool = False) -> bool:
    try:
        return bool(getattr(app, name, fallback))
    except Exception:
        return bool(fallback)


def _safe_float_attr(app: object, name: str, fallback: float = 0.0) -> float:
    try:
        return float(getattr(app, name, fallback) or 0.0)
    except Exception:
        return float(fallback)


def _safe_port_attr(app: object, name: str) -> str | None:
    try:
        value = getattr(app, name, None)
    except Exception:
        value = None
    if value is None:
        return None
    text = str(value or "").strip()
    return text or None


def _coerce_timeline(value: object) -> deque[dict[str, object]]:
    if isinstance(value, deque):
        if value.maxlen == CONNECTION_TIMELINE_LIMIT:
            return value
        return deque(value, maxlen=CONNECTION_TIMELINE_LIMIT)
    if isinstance(value, list):
        return deque(value, maxlen=CONNECTION_TIMELINE_LIMIT)
    return deque(maxlen=CONNECTION_TIMELINE_LIMIT)


def sync_connection_runtime_state_from_app(
    app: object,
    state: ConnectionRuntimeState,
) -> ConnectionRuntimeState:
    state.connected = _safe_bool_attr(app, "connected", state.connected)
    state.connecting = _safe_bool_attr(app, "_connecting", state.connecting)
    state.disconnecting = _safe_bool_attr(app, "_disconnecting", state.disconnecting)
    state.ready = _safe_bool_attr(app, "_grbl_ready", state.ready)
    state.status_seen = _safe_bool_attr(app, "_status_seen", state.status_seen)
    state.alarm_locked = _safe_bool_attr(app, "_alarm_locked", state.alarm_locked)
    state.connected_port = _safe_port_attr(app, "_connected_port")
    state.status_connect_settling_until_ts = _safe_float_attr(
        app,
        "_status_connect_settling_until_ts",
        state.status_connect_settling_until_ts,
    )
    state.ui_sync_failed = _safe_bool_attr(
        app,
        "_connection_ui_sync_failed",
        state.ui_sync_failed,
    )
    try:
        phase = getattr(app, "_connection_ui_sync_failure_phase", state.ui_sync_failure_phase)
    except Exception:
        phase = state.ui_sync_failure_phase
    state.ui_sync_failure_phase = str(phase or "").strip()
    state.timeline = _coerce_timeline(getattr(app, "_connection_timeline", state.timeline))
    return state


def sync_connection_runtime_state_to_app(
    app: object,
    state: ConnectionRuntimeState,
) -> ConnectionRuntimeState:
    setattr(app, "_connection_runtime_state", state)
    setattr(app, "connected", bool(state.connected))
    setattr(app, "_connecting", bool(state.connecting))
    setattr(app, "_disconnecting", bool(state.disconnecting))
    setattr(app, "_grbl_ready", bool(state.ready))
    setattr(app, "_status_seen", bool(state.status_seen))
    setattr(app, "_alarm_locked", bool(state.alarm_locked))
    setattr(app, "_connected_port", state.connected_port)
    setattr(
        app,
        "_status_connect_settling_until_ts",
        float(state.status_connect_settling_until_ts),
    )
    setattr(app, "_connection_ui_sync_failed", bool(state.ui_sync_failed))
    setattr(
        app,
        "_connection_ui_sync_failure_phase",
        str(state.ui_sync_failure_phase or "").strip(),
    )
    setattr(app, "_connection_timeline", state.timeline)
    return state


def get_connection_runtime_state(app: object) -> ConnectionRuntimeState:
    state = getattr(app, "_connection_runtime_state", None)
    if not isinstance(state, ConnectionRuntimeState):
        state = ConnectionRuntimeState()
    sync_connection_runtime_state_from_app(app, state)
    sync_connection_runtime_state_to_app(app, state)
    return state


def append_connection_timeline_event(
    app: object,
    event: str,
    details: str = "",
    *,
    now_ts: float,
) -> None:
    state = get_connection_runtime_state(app)
    state.timeline.append(
        {
            "ts": float(now_ts),
            "event": str(event or "").strip() or "unknown",
            "details": str(details or "").strip(),
        }
    )
    sync_connection_runtime_state_to_app(app, state)
