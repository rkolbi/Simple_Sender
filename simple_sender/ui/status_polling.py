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

from simple_sender.utils.constants import (
    STATUS_POLL_DEFAULT,
    STATUS_POLL_INTERVAL_MIN,
    STATUS_QUERY_FAILURE_LIMIT_DEFAULT,
    STATUS_QUERY_FAILURE_LIMIT_MAX,
    STATUS_QUERY_FAILURE_LIMIT_MIN,
    WATCHDOG_HOMING_TIMEOUT,
)

logger = logging.getLogger(__name__)


def on_status_interval_change(app, _event=None):
    try:
        val = float(app.status_poll_interval.get())
    except Exception:
        val = app.settings.get("status_poll_interval", STATUS_POLL_DEFAULT)
    if val <= 0:
        val = STATUS_POLL_DEFAULT
    if val < STATUS_POLL_INTERVAL_MIN:
        val = STATUS_POLL_INTERVAL_MIN
    app.status_poll_interval.set(val)
    app._apply_status_poll_profile()


def on_status_failure_limit_change(app, _event=None):
    try:
        limit = int(app.status_query_failure_limit.get())
    except Exception:
        limit = app.settings.get("status_query_failure_limit", STATUS_QUERY_FAILURE_LIMIT_DEFAULT)
    if limit < STATUS_QUERY_FAILURE_LIMIT_MIN:
        limit = STATUS_QUERY_FAILURE_LIMIT_MIN
    if limit > STATUS_QUERY_FAILURE_LIMIT_MAX:
        limit = STATUS_QUERY_FAILURE_LIMIT_MAX
    app.status_query_failure_limit.set(limit)
    try:
        app.grbl.set_status_query_failure_limit(limit)
    except Exception as exc:
        logger.exception("Failed to set status failure limit: %s", exc)


def on_homing_watchdog_change(app, _event=None):
    enabled = bool(app.homing_watchdog_enabled.get())
    try:
        timeout = float(app.homing_watchdog_timeout.get())
    except Exception:
        timeout = app.settings.get("homing_watchdog_timeout", WATCHDOG_HOMING_TIMEOUT)
    if timeout <= 0:
        timeout = app.settings.get("homing_watchdog_timeout", WATCHDOG_HOMING_TIMEOUT)
    if timeout <= 0:
        timeout = WATCHDOG_HOMING_TIMEOUT
    app.homing_watchdog_timeout.set(timeout)
    try:
        app.grbl.set_homing_watchdog_settings(enabled, timeout)
    except Exception as exc:
        logger.exception("Failed to set homing watchdog settings: %s", exc)
    entry = getattr(app, "homing_watchdog_timeout_entry", None)
    if entry is not None:
        entry.config(state="normal" if enabled else "disabled")
