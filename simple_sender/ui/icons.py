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


def icon_label(icon: str, label: str) -> str:
    """Render a button label with a leading icon."""
    return f"{icon} {label}"


def stacked_icon_label(icon: str, *lines: str) -> str:
    """Render a toolbar label with a leading icon row and stacked caption lines."""
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    if not cleaned:
        return str(icon)
    return "\n".join([str(icon)] + cleaned)

ICON_REFRESH = "⟳"
ICON_CONNECT = "⚡"
ICON_JOB_READ = "⏺"
ICON_JOB_CLEAR = "⏏"
ICON_RUN = "▶"
ICON_PAUSE = "⏸"
ICON_RESUME = "⏵"
ICON_STOP = "⏹"
ICON_RESUME_FROM = "⤴"
ICON_UNLOCK = "🔓"
ICON_RECOVER = "🛠"
ICON_HOME = "⌂"
ICON_HOLD = "⏸"
ICON_UNITS = "↔"
ICON_AUTO_LEVEL = "⤓"
