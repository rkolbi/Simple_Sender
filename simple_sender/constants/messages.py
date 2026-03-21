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

"""Centralized user-facing status and dialog messages."""


class DialogTitles:
    """Dialog titles shown to operators."""

    BUSY = "Busy"


class BusyMessages:
    """Warnings shown when streaming blocks an operation."""

    STOP_STREAM_BEFORE_CLEARING_GCODE = "Stop the stream before clearing the G-code file."
    STOP_STREAM_BEFORE_DISCONNECTING = "Stop the stream before disconnecting."
    STOP_STREAM_BEFORE_LOADING_NEW_GCODE = "Stop the stream before loading a new G-code file."
    STOP_STREAM_BEFORE_RESUMING = "Stop the stream before resuming."
    STOP_STREAM_BEFORE_RESUMING_FROM_LINE = "Stop the stream before resuming from a line."


class MachineStateMessages:
    """Machine-state label text."""

    DISCONNECTED = "DISCONNECTED"

    @staticmethod
    def connected(port: str) -> str:
        return f"CONNECTED ({port})"


class StatusMessages:
    """Primary status-bar text."""

    DISCONNECTED = "Disconnected"
    GCODE_CLEARED = "G-code cleared"
    STREAMING = "Streaming..."

    @staticmethod
    def connected(port: str) -> str:
        return f"Connected: {port}"

    @staticmethod
    def connected_waiting_for_grbl(port: str) -> str:
        return f"Connected: {port} (waiting for Grbl)"

    @staticmethod
    def streaming_job(name: str) -> str:
        return f"Streaming: {name}"
