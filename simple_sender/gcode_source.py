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
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import threading
from typing import Iterator

from simple_sender.gcode_parser import clean_gcode_line


class FileGcodeSource:
    """Lazy G-code line source backed by a file and precomputed offsets."""

    def __init__(self, path: str, offsets: list[int], encoding: str = "utf-8"):
        self.path = path
        self._offsets = list(offsets)
        self._encoding = encoding
        self._lock = threading.Lock()
        self._file = None

    def __len__(self) -> int:
        return len(self._offsets)

    def __iter__(self) -> Iterator[str]:
        for idx in range(len(self._offsets)):
            yield self._read_line_at(idx)

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            start, stop, step = idx.indices(len(self._offsets))
            if step == 1:
                return [self._read_line_at(i) for i in range(start, stop)]
            return [self._read_line_at(i) for i in range(start, stop, step)]
        if idx < 0:
            idx += len(self._offsets)
        if idx < 0 or idx >= len(self._offsets):
            raise IndexError("G-code index out of range")
        return self._read_line_at(idx)

    def close(self) -> None:
        with self._lock:
            if self._file and not self._file.closed:
                try:
                    self._file.close()
                except Exception:
                    pass
            self._file = None

    def _open(self):
        if self._file is None or self._file.closed:
            self._file = open(
                self.path,
                "r",
                encoding=self._encoding,
                errors="replace",
                newline="",
            )
        return self._file

    def _read_line_at(self, idx: int) -> str:
        with self._lock:
            f = self._open()
            f.seek(self._offsets[idx])
            raw = f.readline()
        return clean_gcode_line(raw)
