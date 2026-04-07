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
"""Shared macro-timeout policy helpers."""

from __future__ import annotations


def _read_bool(value: object, default: bool = False) -> bool:
    try:
        if hasattr(value, "get"):
            value = value.get()
        return bool(value)
    except Exception:
        return bool(default)


def macro_timeout_override_active(app) -> bool:
    return bool(
        _read_bool(getattr(app, "_tool_change_unlimited_time_active", False))
        or _read_bool(getattr(app, "_builtin_workflow_unlimited_time_active", False))
        or _read_bool(getattr(app, "_operator_assisted_workflow_unlimited_time_active", False))
    )


def macro_timeouts_disabled(app) -> bool:
    if macro_timeout_override_active(app):
        return True
    attr_value = getattr(app, "disable_macro_timeouts", None)
    if attr_value is not None:
        return _read_bool(attr_value)
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        return _read_bool(settings.get("disable_macro_timeouts", False))
    return False
