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

"""Platform detection helpers."""

from __future__ import annotations

import os
import platform
import sys


def detect_raspberry_pi() -> bool:
    """Return True when running on a Raspberry Pi Linux host."""
    if not sys.platform.startswith("linux"):
        return False

    model_paths = (
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    )
    for path in model_paths:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    if "raspberry pi" in handle.read().lower():
                        return True
        except Exception:
            continue

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as handle:
            if "raspberry pi" in handle.read().lower():
                return True
    except Exception:
        pass

    machine = platform.machine().lower()
    return machine in ("armv6l", "armv7l", "aarch64", "arm64")

