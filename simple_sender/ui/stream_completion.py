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

import time

from simple_sender.utils.constants import STATUS_POLL_DEFAULT


def status_poll_interval_s(app) -> float:
    try:
        interval = float(app.status_poll_interval.get())
    except Exception:
        interval = STATUS_POLL_DEFAULT
    if interval <= 0:
        interval = STATUS_POLL_DEFAULT
    return interval


def idle_status_recent(app, now_ts: float) -> bool:
    state_text = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
    if not state_text.startswith("idle"):
        return False
    try:
        last_status_ts = float(getattr(app, "_last_status_ts", 0.0) or 0.0)
    except Exception:
        return True
    if last_status_ts <= 0:
        return True
    # Allow several poll intervals before treating the last idle report as stale.
    max_age_s = max(1.0, status_poll_interval_s(app) * 4.0)
    return (now_ts - last_status_ts) <= max_age_s


def should_defer_done_until_idle(app, now_ts: float | None = None) -> bool:
    if now_ts is None:
        now_ts = time.time()
    state_text = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
    if state_text.startswith("idle"):
        return not idle_status_recent(app, now_ts)
    if state_text:
        return True
    return bool(getattr(app, "connected", False) and getattr(app, "_status_seen", False))


def should_defer_completion(app, done: int, total: int, now_ts: float | None = None) -> bool:
    if total <= 0 or done < total:
        return False
    return should_defer_done_until_idle(app, now_ts=now_ts)
