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

import logging
import os
from tkinter import messagebox
from typing import Any

GCODE_FILE_EXTENSIONS = (".nc", ".gcode", ".tap", ".txt")

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_missing_dependency_notified = False

try:
    import tkfilebrowser as _tkfilebrowser
except Exception:
    _tkfilebrowser = None


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def tkfilebrowser_available() -> bool:
    return _tkfilebrowser is not None


def _normalize_start_dir(path: str) -> str:
    text = str(path or "").strip()
    if text:
        try:
            expanded = os.path.abspath(os.path.expanduser(text))
            if os.path.isdir(expanded):
                return expanded
        except Exception:
            pass
    for fallback in (os.path.expanduser("~"), os.getcwd()):
        try:
            resolved = os.path.abspath(str(fallback))
            if os.path.isdir(resolved):
                return resolved
        except Exception:
            continue
    return ""


def _coerce_path_result(result: Any) -> str:
    if isinstance(result, (tuple, list)):
        if not result:
            return ""
        return str(result[0] or "")
    return str(result or "")


def _normalize_default_extension(defaultextension: str) -> str:
    text = str(defaultextension or "").strip()
    if not text:
        return ""
    if text.startswith("."):
        return text
    return f".{text}"


def _apply_default_extension(path: str, defaultextension: str) -> str:
    normalized_ext = _normalize_default_extension(defaultextension)
    if (not path) or (not normalized_ext):
        return path
    root, ext = os.path.splitext(path)
    if ext:
        return path
    return f"{root}{normalized_ext}"


def _show_missing_dependency_message(parent=None) -> None:
    global _missing_dependency_notified
    if _missing_dependency_notified:
        return
    _missing_dependency_notified = True
    try:
        messagebox.showerror(
            "File browser unavailable",
            "tkfilebrowser is required for file open/save dialogs.\n\n"
            "Install it with: pip install tkfilebrowser",
            parent=parent,
        )
    except Exception as exc:
        _log_suppressed("Failed showing tkfilebrowser missing-dependency dialog", exc)


def ask_open_path(app, *args, **kwargs) -> str:
    del app
    del args
    if _tkfilebrowser is None:
        _show_missing_dependency_message(parent=kwargs.get("parent"))
        return ""
    call_kwargs = dict(kwargs)
    initialdir = _normalize_start_dir(str(call_kwargs.get("initialdir", "") or ""))
    if initialdir:
        call_kwargs["initialdir"] = initialdir
    try:
        return _coerce_path_result(_tkfilebrowser.askopenfilename(**call_kwargs))
    except Exception as exc:
        _log_suppressed("tkfilebrowser askopenfilename failed", exc)
        return ""


def ask_save_path(app, *args, **kwargs) -> str:
    del app
    del args
    if _tkfilebrowser is None:
        _show_missing_dependency_message(parent=kwargs.get("parent"))
        return ""
    call_kwargs = dict(kwargs)
    initialdir = _normalize_start_dir(str(call_kwargs.get("initialdir", "") or ""))
    if initialdir:
        call_kwargs["initialdir"] = initialdir
    try:
        chosen = _coerce_path_result(_tkfilebrowser.asksaveasfilename(**call_kwargs))
    except Exception as exc:
        _log_suppressed("tkfilebrowser asksaveasfilename failed", exc)
        return ""
    chosen = _apply_default_extension(chosen, str(call_kwargs.get("defaultextension", "") or ""))
    return chosen


def browse_for_gcode_path(app, *, initial_dir: str = "") -> str:
    allowed_patterns = " ".join(f"*{ext}" for ext in GCODE_FILE_EXTENSIONS)
    return ask_open_path(
        app,
        title="Open G-code",
        initialdir=initial_dir,
        filetypes=[("G-code", allowed_patterns), ("All files", "*.*")],
    )
