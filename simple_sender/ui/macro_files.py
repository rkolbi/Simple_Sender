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
import tempfile
from pathlib import Path
from typing import Any

from simple_sender.utils.constants import MACRO_EXTS, MACRO_PREFIXES
from simple_sender.utils.macro_headers import MacroFormatError, parse_macro_header

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


def _macro_color_validator_from_app(app: Any):
    checker = getattr(app, "winfo_rgb", None)
    if not callable(checker):
        return None

    def _validate(color: str) -> bool:
        try:
            checker(color)
            return True
        except Exception:
            return False

    return _validate


def read_macro_slot(
    app: Any, index: int
) -> tuple[str, str, str, str, str, str | None, str]:
    path = None
    try:
        path = app.macro_executor.macro_path(int(index))
    except Exception:
        path = None
    if not path:
        return "", "", "", "", "", None, ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception:
        return "", "", "", "", "", path, "Failed reading macro file."
    color_validator = _macro_color_validator_from_app(app)
    try:
        name, tip, color, text_color, body_start = parse_macro_header(
            lines,
            color_validator=color_validator,
        )
    except MacroFormatError as exc:
        name = str(lines[0]).strip() if lines else ""
        tip = str(lines[1]).strip() if len(lines) > 1 else ""
        if len(lines) >= 5:
            color = str(lines[2]).strip()
            text_color = str(lines[3]).strip()
            body_lines = lines[4:]
        else:
            color = ""
            text_color = ""
            body_lines = lines[2:] if len(lines) > 2 else []
        body = "\n".join(body_lines)
        return name, tip, color, text_color, body, path, str(exc)
    body = "\n".join(lines[body_start:]) if len(lines) > body_start else ""
    return name, tip, color or "", text_color or "", body, path, ""


def write_macro_slot(
    macro_dir: str,
    index: int,
    *,
    name: str,
    tip: str,
    color: str = "",
    text_color: str = "",
    body: str,
) -> str:
    os.makedirs(macro_dir, exist_ok=True)
    path = macro_slot_path(macro_dir, index)
    path_obj = Path(path)
    temp_path: Path | None = None
    lines = [
        str(name).strip(),
        str(tip).strip(),
        str(color).strip(),
        str(text_color).strip(),
    ]
    body_text = str(body).replace("\r\n", "\n").replace("\r", "\n")
    if not any(line.strip() for line in body_text.split("\n")):
        raise ValueError("Macro body cannot be empty.")
    if body_text:
        lines.extend(body_text.split("\n"))
    text = "\n".join(lines).rstrip("\n") + "\n"
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=macro_dir,
            prefix=f"{path_obj.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        assert temp_path is not None
        temp_path.replace(path_obj)
        if os.name != "nt":
            try:
                dir_fd = os.open(macro_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                pass
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return path


def remove_macro_slot(macro_dir: str, index: int) -> bool:
    canonical = macro_slot_path(macro_dir, index)
    candidates = [canonical]
    for prefix in MACRO_PREFIXES:
        for ext in MACRO_EXTS:
            candidates.append(os.path.join(macro_dir, f"{prefix}{int(index)}{ext}"))
    seen: set[str] = set()
    removed_any = False
    failures: list[tuple[str, BaseException]] = []
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(candidate):
            try:
                os.remove(candidate)
                removed_any = True
            except Exception as exc:
                failures.append((candidate, exc))
    if failures:
        if removed_any:
            failed_paths = ", ".join(path for path, _exc in failures)
            raise OSError(f"Partially removed macro slot; failed deleting: {failed_paths}")
        path, exc = failures[0]
        raise OSError(f"Failed deleting macro slot file {path}: {exc}") from exc
    return removed_any


def discover_macro_assets(app: Any) -> list[tuple[str, str]]:
    assets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for macro_dir in get_macro_search_dirs(app):
        if not macro_dir or not os.path.isdir(macro_dir):
            continue
        patterns = [
            os.path.join(macro_dir, "Macro-*"),
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
