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
import tempfile
from pathlib import Path

from .config import get_settings_path

APP_LOGGER_NAME = "simple_sender"
LOG_DIRNAME = "logs"


def _handler_exists(logger: logging.Logger, name: str) -> bool:
    for handler in logger.handlers:
        if handler.get_name() == name:
            return True
    return False


def get_log_dir() -> Path:
    """Resolve the directory for log files (creates it if needed)."""
    base_dir = Path(get_settings_path()).parent
    log_dir = base_dir / LOG_DIRNAME
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    except Exception:
        fallback = Path(tempfile.gettempdir()) / "simple_sender_logs"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except Exception:
            return fallback


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

    if not _handler_exists(root, "simple_sender_app_file"):
        app_handler = logging.handlers.RotatingFileHandler(
            log_dir / "simple_sender.log",
            maxBytes=10_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        app_handler.setLevel(logging.DEBUG)
        app_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        app_handler.set_name("simple_sender_app_file")
        root.addHandler(app_handler)

    if not _handler_exists(root, "simple_sender_error_file"):
        error_handler = logging.handlers.RotatingFileHandler(
            log_dir / "errors.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d\n%(message)s\n")
        )
        error_handler.set_name("simple_sender_error_file")
        root.addHandler(error_handler)

    serial_logger = logging.getLogger(f"{APP_LOGGER_NAME}.serial")
    serial_logger.setLevel(logging.DEBUG)
    if not _handler_exists(serial_logger, "simple_sender_serial_file"):
        serial_handler = logging.handlers.RotatingFileHandler(
            log_dir / "serial.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        serial_handler.setLevel(logging.DEBUG)
        serial_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        serial_handler.set_name("simple_sender_serial_file")
        serial_logger.addHandler(serial_handler)

    ui_logger = logging.getLogger(f"{APP_LOGGER_NAME}.ui")
    ui_logger.setLevel(logging.DEBUG)
    if not _handler_exists(ui_logger, "simple_sender_ui_file"):
        ui_handler = logging.handlers.RotatingFileHandler(
            log_dir / "ui.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        ui_handler.setLevel(logging.DEBUG)
        ui_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        ui_handler.set_name("simple_sender_ui_file")
        ui_logger.addHandler(ui_handler)

    return root
