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

import argparse
import logging
import os
import pathlib
import shutil

DEFAULT_SKIP_DIRS = (
    ".git",
    ".venv",
    "venv",
    "env",
    "ref",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)

logger = logging.getLogger(__name__)


def _remove_tree(path: pathlib.Path) -> None:
    try:
        shutil.rmtree(path)
    except Exception as exc:
        logger.debug("Failed removing directory tree %s: %s", path, exc, exc_info=exc)


def _remove_file(path: pathlib.Path) -> None:
    try:
        path.unlink()
    except Exception as exc:
        logger.debug("Failed removing file %s: %s", path, exc, exc_info=exc)


def clean_release(root: pathlib.Path, skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS) -> None:
    skip_set = {name.strip() for name in skip_dirs if name.strip()}
    for current, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [name for name in dirnames if name not in skip_set]
        current_path = pathlib.Path(current)
        for dirname in list(dirnames):
            if dirname != "__pycache__":
                continue
            _remove_tree(current_path / dirname)
            dirnames.remove(dirname)
        for filename in filenames:
            if filename.endswith(".pyc"):
                _remove_file(current_path / filename)
    coverage = root / ".coverage"
    if coverage.exists():
        _remove_file(coverage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean release artifacts.")
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory to clean (default: current directory).",
    )
    parser.add_argument(
        "--skip-dir",
        action="append",
        default=list(DEFAULT_SKIP_DIRS),
        help=(
            "Directory name to skip while cleaning (can be used more than once). "
            f"Defaults: {', '.join(DEFAULT_SKIP_DIRS)}"
        ),
    )
    args = parser.parse_args()
    root = pathlib.Path(args.root).resolve()
    clean_release(root, tuple(args.skip_dir))


if __name__ == "__main__":
    main()
