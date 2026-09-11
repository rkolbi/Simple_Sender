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

from array import array
import os
import threading
from typing import IO, Iterable, Iterator, cast, overload

from simple_sender.gcode_parser import clean_gcode_line


class FileGcodeSource:
    """Lazy G-code line source backed by a file and precomputed offsets."""

    def __init__(
        self,
        path: str,
        offsets: Iterable[int] | None,
        encoding: str = "utf-8",
        *,
        already_clean: bool = False,
        total_lines: int | None = None,
        line_count_known: bool | None = None,
        sparse_offsets: Iterable[int] | None = None,
        sparse_stride: int | None = None,
        snapshot_sha256: str | None = None,
        snapshot_size_bytes: int | None = None,
        snapshot_mtime_ns: int | None = None,
        validation_complete: bool = False,
        validated_line_count: int | None = None,
    ):
        self.path = path
        self._offsets: array[int] | None
        self._sparse_offsets: array[int] | None
        if offsets is not None and isinstance(offsets, array) and offsets.typecode in {"Q", "I", "L"}:
            self._offsets = cast(array[int], offsets)
        else:
            # Compact, contiguous offsets reduce memory for indexed jobs.
            self._offsets = array("Q", offsets) if offsets is not None else None
        if sparse_offsets is not None and isinstance(sparse_offsets, array) and sparse_offsets.typecode in {"Q", "I", "L"}:
            self._sparse_offsets = cast(array[int], sparse_offsets)
        else:
            self._sparse_offsets = array("Q", sparse_offsets) if sparse_offsets is not None else None
        self._sparse_stride = max(2, int(sparse_stride or 0)) if sparse_stride else 0
        self._encoding = encoding
        self._already_clean = bool(already_clean)
        self._lock = threading.Lock()
        self._file: IO[str] | None = None
        if total_lines is not None:
            self._line_count = int(total_lines)
        else:
            self._line_count = int(len(self._offsets or ()))
        if self._line_count < 0:
            self._line_count = 0
        if line_count_known is None:
            self._line_count_known = bool(total_lines is not None or self._offsets is not None)
        else:
            self._line_count_known = bool(line_count_known)
        # Sequential fallback cursor for non-indexed sources.
        self._cursor_index = -1
        self.snapshot_sha256 = str(snapshot_sha256 or "")
        self.snapshot_size_bytes = max(0, int(snapshot_size_bytes or 0))
        self.snapshot_mtime_ns = max(0, int(snapshot_mtime_ns or 0))
        self.validation_complete = bool(validation_complete)
        self.validated_line_count = max(0, int(validated_line_count or 0))

    def __len__(self) -> int:
        return self._line_count

    def __iter__(self) -> Iterator[str]:
        if self._offsets is None:
            emitted = 0
            with open(
                self.path,
                "r",
                encoding=self._encoding,
                errors="replace",
                newline="",
            ) as handle:
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    line = self._clean_line(raw)
                    if not line:
                        continue
                    emitted += 1
                    yield line
                    if self._line_count_known and emitted >= self._line_count:
                        break
            if not self._line_count_known:
                self.set_line_count(emitted, known=True)
            return
        for idx in range(self._line_count):
            yield self._read_line_at(idx)

    @overload
    def __getitem__(self, idx: int) -> str: ...

    @overload
    def __getitem__(self, idx: slice) -> list[str]: ...

    def __getitem__(self, idx: int | slice):
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._line_count)
            if step == 1:
                return [self._read_line_at(i) for i in range(start, stop)]
            return [self._read_line_at(i) for i in range(start, stop, step)]
        if idx < 0:
            idx += self._line_count
        if idx < 0:
            raise IndexError("G-code index out of range")
        if self._line_count_known and idx >= self._line_count:
            raise IndexError("G-code index out of range")
        return self._read_line_at(idx)

    def read_line_with_offsets(self, idx: int) -> tuple[str, int | None, int | None]:
        """Read a cleaned line plus its raw-file byte span.

        Returns:
            (line, start_offset, end_offset), where offsets are byte positions
            in the original file. Offsets may be None when unavailable.
        """
        if idx < 0:
            idx += self._line_count
        if idx < 0:
            raise IndexError("G-code index out of range")
        if self._line_count_known and idx >= self._line_count:
            raise IndexError("G-code index out of range")
        return self._read_line_with_offsets_at(idx)

    def close(self) -> None:
        with self._lock:
            if self._file and not self._file.closed:
                try:
                    self._file.close()
                except (OSError, ValueError):
                    pass
            self._file = None
            self._cursor_index = -1

    def clone(self) -> "FileGcodeSource":
        return FileGcodeSource(
            self.path,
            self._offsets,
            encoding=self._encoding,
            already_clean=self._already_clean,
            total_lines=self._line_count,
            line_count_known=self._line_count_known,
            sparse_offsets=self._sparse_offsets,
            sparse_stride=self._sparse_stride,
            snapshot_sha256=self.snapshot_sha256,
            snapshot_size_bytes=self.snapshot_size_bytes,
            snapshot_mtime_ns=self.snapshot_mtime_ns,
            validation_complete=self.validation_complete,
            validated_line_count=self.validated_line_count,
        )

    def snapshot_identity_matches(self) -> bool:
        """Return whether the owned snapshot still matches its admitted metadata."""
        if not self.snapshot_sha256:
            return True
        try:
            stat_result = os.stat(self.path)
        except OSError:
            return False
        return bool(
            int(stat_result.st_size) == self.snapshot_size_bytes
            and int(getattr(stat_result, "st_mtime_ns", 0) or 0)
            == self.snapshot_mtime_ns
            and self.validation_complete
            and self.validated_line_count == self._line_count
            and self._line_count_known
        )

    def line_count_known(self) -> bool:
        return bool(self._line_count_known)

    def set_line_count(self, total_lines: int, *, known: bool = True) -> None:
        with self._lock:
            self._line_count = max(0, int(total_lines))
            self._line_count_known = bool(known)

    def set_full_offsets(self, offsets: Iterable[int], *, total_lines: int) -> None:
        with self._lock:
            self._offsets = (
                cast(array[int], offsets) if isinstance(offsets, array) else array("Q", offsets)
            )
            self._line_count = max(0, int(total_lines))
            self._line_count_known = True
            self._cursor_index = -1
            self._sparse_offsets = None
            self._sparse_stride = 0

    def set_sparse_offsets(
        self,
        offsets: Iterable[int],
        *,
        stride: int,
        total_lines: int,
    ) -> None:
        with self._lock:
            self._sparse_offsets = (
                cast(array[int], offsets) if isinstance(offsets, array) else array("Q", offsets)
            )
            self._sparse_stride = max(2, int(stride))
            self._line_count = max(0, int(total_lines))
            self._line_count_known = True
            self._cursor_index = -1

    def index_mode(self) -> str:
        if self._offsets is not None:
            return "full"
        if self._sparse_offsets is not None and self._sparse_stride > 1:
            return "sparse"
        return "none"

    def _open(self) -> IO[str]:
        if self._file is None or self._file.closed:
            self._file = open(
                self.path,
                "r",
                encoding=self._encoding,
                errors="replace",
                newline="",
            )
        return self._file

    def _clean_line(self, raw: str) -> str:
        if self._already_clean:
            return cast(str, raw.rstrip("\r\n"))
        return cast(str, clean_gcode_line(raw))

    def _read_line_at(self, idx: int) -> str:
        line, _start, _end = self._read_line_with_offsets_at(idx)
        return line

    def _read_line_with_offsets_at(self, idx: int) -> tuple[str, int | None, int | None]:
        with self._lock:
            f = self._open()
            if self._offsets is not None:
                target_offset = self._offsets[idx]
                # Streaming sends lines in sequence, so skip seek when already at target.
                if f.tell() != target_offset:
                    f.seek(target_offset)
                raw = f.readline()
                start_offset = int(target_offset)
                end_offset = int(f.tell())
            else:
                base_idx = 0
                base_offset = 0
                if self._sparse_offsets is not None and self._sparse_stride > 1 and idx >= self._sparse_stride:
                    anchor_slot = min(
                        int(idx // self._sparse_stride),
                        max(0, len(self._sparse_offsets) - 1),
                    )
                    base_idx = int(anchor_slot * self._sparse_stride)
                    base_offset = int(self._sparse_offsets[anchor_slot])
                if idx <= self._cursor_index or self._cursor_index < base_idx:
                    f.seek(base_offset)
                    self._cursor_index = base_idx - 1
                line = ""
                line_start_offset: int | None = None
                line_end_offset: int | None = None
                while self._cursor_index < idx:
                    raw_start_offset = int(f.tell())
                    raw = f.readline()
                    raw_end_offset = int(f.tell())
                    if not raw:
                        # EOF before requested index, lock in true cleaned-line count.
                        self._line_count = max(0, self._cursor_index + 1)
                        self._line_count_known = True
                        raise IndexError("G-code index out of range")
                    cleaned = self._clean_line(raw)
                    if not cleaned:
                        continue
                    self._cursor_index += 1
                    if (
                        not self._line_count_known
                        and self._cursor_index + 1 > self._line_count
                    ):
                        self._line_count = self._cursor_index + 1
                    line = cleaned
                    line_start_offset = raw_start_offset
                    line_end_offset = raw_end_offset
                return line, line_start_offset, line_end_offset
        line = self._clean_line(raw)
        if not line:
            # Indexed sources should point to cleaned non-empty lines. Treat any
            # mismatch as an out-of-range read instead of recursing indefinitely.
            raise IndexError("G-code index out of range")
        return line, start_offset, end_offset
