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

import glob
import os
from typing import Any

from simple_sender.utils.constants import MACRO_EXTS, MACRO_PREFIXES

CHECKLIST_PREFIX = "checklist-"
CHECKLIST_EXT = ".chk"


def get_macro_search_dirs(app: Any) -> tuple[str, ...]:
    executor = getattr(app, "macro_executor", None)
    if executor is None:
        return ()
    dirs = getattr(executor, "macro_search_dirs", None)
    if dirs is None:
        dirs = getattr(executor, "_macro_search_dirs", ())
    return tuple(dirs or ())


def get_writable_macro_dir(app: Any) -> str | None:
    for macro_dir in get_macro_search_dirs(app):
        if not macro_dir:
            continue
        if not os.path.isdir(macro_dir):
            continue
        if os.access(macro_dir, os.W_OK):
            return macro_dir
    return None


def macro_slot_filename(index: int) -> str:
    return f"Macro-{int(index)}"


def macro_slot_path(macro_dir: str, index: int) -> str:
    return os.path.join(macro_dir, macro_slot_filename(index))


def read_macro_slot(app: Any, index: int) -> tuple[str, str, str, str | None]:
    path = None
    try:
        path = app.macro_executor.macro_path(int(index))
    except Exception:
        path = None
    if not path:
        return "", "", "", None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception:
        return "", "", "", path
    name = lines[0].strip() if lines else ""
    tip = lines[1].strip() if len(lines) > 1 else ""
    body = "\n".join(lines[2:]) if len(lines) > 2 else ""
    return name, tip, body, path


def write_macro_slot(
    macro_dir: str,
    index: int,
    *,
    name: str,
    tip: str,
    body: str,
) -> str:
    os.makedirs(macro_dir, exist_ok=True)
    path = macro_slot_path(macro_dir, index)
    lines = [str(name).strip(), str(tip).strip()]
    body_text = str(body).replace("\r\n", "\n").replace("\r", "\n")
    if body_text:
        lines.extend(body_text.split("\n"))
    text = "\n".join(lines).rstrip("\n") + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def remove_macro_slot(macro_dir: str, index: int) -> None:
    canonical = macro_slot_path(macro_dir, index)
    candidates = [canonical]
    for prefix in MACRO_PREFIXES:
        for ext in MACRO_EXTS:
            candidates.append(os.path.join(macro_dir, f"{prefix}{int(index)}{ext}"))
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(candidate):
            try:
                os.remove(candidate)
            except Exception:
                continue


def discover_macro_assets(app: Any) -> list[tuple[str, str]]:
    assets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for macro_dir in get_macro_search_dirs(app):
        if not macro_dir or not os.path.isdir(macro_dir):
            continue
        patterns = [
            os.path.join(macro_dir, "Macro-*"),
            os.path.join(macro_dir, "Maccro-*"),
            os.path.join(macro_dir, f"{CHECKLIST_PREFIX}*{CHECKLIST_EXT}"),
        ]
        for pattern in patterns:
            try:
                matches = sorted(glob.glob(pattern))
            except Exception:
                continue
            for path in matches:
                if not os.path.isfile(path):
                    continue
                basename = os.path.basename(path)
                key = basename.lower()
                if key in seen:
                    continue
                seen.add(key)
                assets.append((path, basename))
    return assets
