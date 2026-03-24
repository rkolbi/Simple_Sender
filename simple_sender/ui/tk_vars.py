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

"""Shared helpers for interacting with Tk-style variable attributes."""

from __future__ import annotations

from typing import Any, Callable


def safe_set_var_attr(
    owner: Any,
    attr_name: str,
    value: Any,
    *,
    log_suppressed: Callable[[str, BaseException], None] | None = None,
    context: str | None = None,
) -> bool:
    """Set a Tk-style variable attribute when it exists and exposes ``set()``."""

    var = getattr(owner, attr_name, None)
    setter = getattr(var, "set", None)
    if not callable(setter):
        return False
    try:
        setter(value)
        return True
    except Exception as exc:
        if log_suppressed is not None:
            log_suppressed(context or f"Failed setting {attr_name}", exc)
        return False


def read_bool_var_attr(owner: Any, attr_name: str, *, default: bool = False) -> bool:
    """Read a bool-like value from a Tk-style variable attribute."""

    var = getattr(owner, attr_name, None)
    if var is None:
        return bool(default)
    getter = getattr(var, "get", None)
    if not callable(getter):
        return bool(default)
    try:
        return bool(getter())
    except Exception:
        return bool(default)


def read_bool_pref(
    owner: Any,
    *,
    attr_name: str,
    key: str,
    default: bool = False,
) -> bool:
    """Read a bool preference from a Tk variable first, then app settings."""

    var = getattr(owner, attr_name, None)
    if var is not None:
        getter = getattr(var, "get", None)
        if callable(getter):
            try:
                return bool(getter())
            except Exception:
                pass
        else:
            return bool(default)
    settings = getattr(owner, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get(key, default))
        except Exception:
            return bool(default)
    return bool(default)
