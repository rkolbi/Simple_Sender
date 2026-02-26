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

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_FILE_DIALOG_ACTIVE_ATTR = "_file_dialog_active"


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _resolve_parent(app, kwargs):
    parent = kwargs.get("parent")
    if parent is not None:
        return parent
    if app is None:
        return None
    try:
        if bool(app.winfo_exists()):
            return app
    except Exception:
        return None
    return None


def _run_dialog_once(app, func, *args, **kwargs):
    call_kwargs = dict(kwargs)
    parent = _resolve_parent(app, call_kwargs)
    if parent is not None and call_kwargs.get("parent") is None:
        call_kwargs["parent"] = parent

    guard_active = False
    if app is not None:
        try:
            if bool(getattr(app, _FILE_DIALOG_ACTIVE_ATTR, False)):
                return ""
            setattr(app, _FILE_DIALOG_ACTIVE_ATTR, True)
            guard_active = True
        except Exception:
            guard_active = False
    try:
        return func(*args, **call_kwargs)
    finally:
        if guard_active:
            try:
                setattr(app, _FILE_DIALOG_ACTIVE_ATTR, False)
            except Exception as exc:
                _log_suppressed("Failed clearing file-dialog active guard flag", exc)


def run_file_dialog(app, func, *args, **kwargs):
    return _run_dialog_once(app, func, *args, **kwargs)
