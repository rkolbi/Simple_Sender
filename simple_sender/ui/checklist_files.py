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

CHECKLIST_PREFIX = "checklist-"
CHECKLIST_EXT = ".chk"


def _macro_search_dirs(app) -> tuple[str, ...]:
    executor = getattr(app, "macro_executor", None)
    if executor is None:
        return ()
    dirs = getattr(executor, "macro_search_dirs", None)
    if dirs is None:
        dirs = getattr(executor, "_macro_search_dirs", ())
    return tuple(dirs or ())


def discover_checklist_files(app) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for macro_dir in _macro_search_dirs(app):
        pattern = os.path.join(macro_dir, f"{CHECKLIST_PREFIX}*{CHECKLIST_EXT}")
        try:
            candidates = sorted(glob.glob(pattern))
        except Exception:
            continue
        for path in candidates:
            base = os.path.basename(path).lower()
            if base in seen:
                continue
            seen.add(base)
            paths.append(path)
    return paths


def find_named_checklist(app, name: str) -> str | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    filename = f"{CHECKLIST_PREFIX}{key}{CHECKLIST_EXT}"
    for macro_dir in _macro_search_dirs(app):
        path = os.path.join(macro_dir, filename)
        if os.path.isfile(path):
            return path
    return None


def format_checklist_title(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    lower = base.lower()
    if lower.startswith(CHECKLIST_PREFIX):
        base = base[len(CHECKLIST_PREFIX):]
    title = base.replace("_", " ").replace("-", " ").strip()
    return title.title() if title else "Checklist"


def load_checklist_items(path: str) -> list[str] | None:
    items: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                items.append(text)
    except Exception:
        return None
    return items
