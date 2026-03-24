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

"""Structured logging setup for Simple Sender."""

from __future__ import annotations

import logging
import logging.handlers
import os
import tempfile
from pathlib import Path

from .config import get_settings_path
from .temp_paths import get_preferred_temp_dir

APP_LOGGER_NAME = "simple_sender"
LOG_DIRNAME = "logs"


def _handler_exists(logger: logging.Logger, name: str) -> bool:
    for handler in logger.handlers:
        if handler.get_name() == name:
            return True
    return False


def _probe_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    if not path.exists() or not path.is_dir():
        return False
    try:
        fd, probe_path = tempfile.mkstemp(
            dir=str(path),
            prefix=".simple_sender_write_probe_",
            suffix=".tmp",
        )
    except Exception:
        return False
    try:
        os.close(fd)
        Path(probe_path).unlink(missing_ok=True)
    except Exception:
        return False
    return True


def _resolve_usable_log_dir(path: Path) -> Path | None:
    return path if _probe_writable_directory(path) else None


def get_log_dir() -> Path | None:
    """Resolve a usable directory for log files."""
    base_dir = Path(get_settings_path()).parent
    log_dir = _resolve_usable_log_dir(base_dir / LOG_DIRNAME)
    if log_dir is not None:
        return log_dir
    fallback = Path(get_preferred_temp_dir()) / LOG_DIRNAME
    return _resolve_usable_log_dir(fallback)


def _add_rotating_file_handler(
    logger: logging.Logger,
    *,
    log_dir: Path | None,
    handler_name: str,
    filename: str,
    level: int,
    formatter: logging.Formatter,
    max_bytes: int,
    backup_count: int,
    warn_logger: logging.Logger,
) -> None:
    if log_dir is None or _handler_exists(logger, handler_name):
        return
    try:
        handler = logging.handlers.RotatingFileHandler(
            log_dir / filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except Exception as exc:
        warn_logger.warning(
            "File logging disabled for %s: %s",
            filename,
            exc,
        )
        return
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.set_name(handler_name)
    logger.addHandler(handler)


def setup_logging() -> logging.Logger:
    """Initialize application logging with rotating file handlers."""
    log_dir = get_log_dir()

    root = logging.getLogger(APP_LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    if not _handler_exists(root, "simple_sender_console"):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )
        console.set_name("simple_sender_console")
        root.addHandler(console)

    if log_dir is None:
        root.warning("No writable log directory available; continuing with console-only logging.")
    _add_rotating_file_handler(
        root,
        log_dir=log_dir,
        handler_name="simple_sender_app_file",
        filename="simple_sender.log",
        level=logging.DEBUG,
        formatter=logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
        max_bytes=10_000_000,
        backup_count=5,
        warn_logger=root,
    )
    _add_rotating_file_handler(
        root,
        log_dir=log_dir,
        handler_name="simple_sender_error_file",
        filename="errors.log",
        level=logging.WARNING,
        formatter=logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d\n%(message)s\n"
        ),
        max_bytes=2_000_000,
        backup_count=5,
        warn_logger=root,
    )

    serial_logger = logging.getLogger(f"{APP_LOGGER_NAME}.serial")
    serial_logger.setLevel(logging.DEBUG)
    _add_rotating_file_handler(
        serial_logger,
        log_dir=log_dir,
        handler_name="simple_sender_serial_file",
        filename="serial.log",
        level=logging.DEBUG,
        formatter=logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ),
        max_bytes=5_000_000,
        backup_count=3,
        warn_logger=root,
    )

    ui_logger = logging.getLogger(f"{APP_LOGGER_NAME}.ui")
    ui_logger.setLevel(logging.DEBUG)
    _add_rotating_file_handler(
        ui_logger,
        log_dir=log_dir,
        handler_name="simple_sender_ui_file",
        filename="ui.log",
        level=logging.DEBUG,
        formatter=logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
        max_bytes=5_000_000,
        backup_count=3,
        warn_logger=root,
    )

    return root
