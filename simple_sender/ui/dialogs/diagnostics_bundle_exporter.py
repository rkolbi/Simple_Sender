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

"""Diagnostics bundle export helper functions."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import zipfile


def find_named_log(log_files: list[Path], filename: str) -> Path | None:
    target = str(filename or "").strip().lower()
    if not target:
        return None
    for path in log_files:
        try:
            if path.name.strip().lower() == target:
                return path
        except Exception:
            continue
    return None


def write_bounded_log_tail_chunked(
    archive: zipfile.ZipFile,
    *,
    arcname: str,
    path: Path,
    max_bytes: int,
    log_suppressed: Callable[[str, BaseException], None],
    chunk_bytes: int = 64 * 1024,
) -> None:
    cap = max(1, int(max_bytes))
    chunk_size = max(4096, int(chunk_bytes))
    try:
        size = int(path.stat().st_size)
    except Exception:
        with archive.open(arcname, "w") as out_file:
            out_file.write(b"")
        return
    if size <= 0:
        with archive.open(arcname, "w") as out_file:
            out_file.write(b"")
        return
    start = max(0, size - cap)
    header_written = start > 0
    header_bytes = b""
    if header_written:
        header_text = (
            f"# BOUNDED TAIL EXPORT (last {cap:,} bytes)\n"
            f"# Source: {path}\n"
            f"# Total file size: {size:,} bytes\n\n"
        )
        header_bytes = header_text.encode("utf-8", errors="replace")
    with archive.open(arcname, "w") as out_file:
        if header_bytes:
            out_file.write(header_bytes)
        try:
            with open(path, "rb") as in_file:
                in_file.seek(start)
                skip_partial_line = start > 0
                while True:
                    chunk = in_file.read(chunk_size)
                    if not chunk:
                        break
                    if skip_partial_line:
                        newline_idx = chunk.find(b"\n")
                        if newline_idx < 0:
                            continue
                        chunk = chunk[newline_idx + 1 :]
                        skip_partial_line = False
                    if chunk:
                        out_file.write(chunk)
        except Exception as exc:
            log_suppressed(
                "Failed reading/writing bounded log tail during diagnostics bundle export",
                exc,
            )


def parse_serial_log_timestamp(line: str) -> datetime | None:
    text = str(line or "")
    if len(text) < 23:
        return None
    stamp = text[:23]
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        return None


def build_recent_serial_window_from_log(
    serial_log_path: Path,
    *,
    log_suppressed: Callable[[str, BaseException], None],
    window_minutes: int = 10,
    max_lines: int = 8000,
    min_lines: int = 1200,
) -> str:
    lines_tail: deque[str] = deque(maxlen=max_lines)
    try:
        with open(serial_log_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                lines_tail.append(raw.rstrip("\r\n"))
    except Exception as exc:
        log_suppressed(
            "Failed reading serial.log for recent streaming window export", exc
        )
        return ""
    if not lines_tail:
        return ""
    tail_lines = list(lines_tail)
    newest_ts = None
    for line in reversed(tail_lines):
        ts = parse_serial_log_timestamp(line)
        if ts is not None:
            newest_ts = ts
            break
    if newest_ts is None:
        selected = tail_lines[-min(min_lines, len(tail_lines)) :]
    else:
        cutoff = newest_ts.timestamp() - max(60, int(window_minutes) * 60)
        selected = []
        for line in tail_lines:
            ts = parse_serial_log_timestamp(line)
            if ts is None:
                continue
            if ts.timestamp() >= cutoff:
                selected.append(line)
        if len(selected) < min_lines:
            selected = tail_lines[-min(min_lines, len(tail_lines)) :]
    if not selected:
        return ""
    header = [
        "# Simple Sender serial activity window",
        f"# Source: {serial_log_path}",
        f"# Exported: {datetime.now().isoformat(timespec='seconds')}",
        f"# Lines: {len(selected)}",
        "",
    ]
    return "\n".join(header + selected) + "\n"


def collect_streaming_bundle_artifacts(
    runtime_metrics: dict[str, Any],
    log_files: list[Path],
    *,
    json_dump: Callable[[Any], str],
    log_suppressed: Callable[[str, BaseException], None],
) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    serial_tail = runtime_metrics.get("serial_activity_tail")
    if isinstance(serial_tail, list) and serial_tail:
        artifacts["streaming/serial_activity_tail.json"] = (
            json_dump(serial_tail) + "\n"
        )
        window_lines = ["# Simple Sender serial activity tail", ""]
        for entry in serial_tail:
            if not isinstance(entry, dict):
                continue
            stamp = str(entry.get("timestamp", "") or "")
            direction = str(entry.get("dir", "") or "")
            line = str(entry.get("line", "") or "")
            window_lines.append(f"{stamp} [{direction}] {line}")
        artifacts["streaming/serial_activity_tail.log"] = "\n".join(window_lines) + "\n"
    serial_log = find_named_log(log_files, "serial.log")
    if serial_log is not None:
        window_text = build_recent_serial_window_from_log(
            serial_log,
            log_suppressed=log_suppressed,
        )
        if window_text:
            artifacts["streaming/serial_recent_window.log"] = window_text
    perf_sample_trace = runtime_metrics.get("perf_sample_trace")
    if isinstance(perf_sample_trace, list) and perf_sample_trace:
        artifacts["streaming/perf_sample_trace.json"] = (
            json_dump(perf_sample_trace) + "\n"
        )
    jog_dro_trace = runtime_metrics.get("jog_dro_trace_tail")
    if isinstance(jog_dro_trace, list) and jog_dro_trace:
        artifacts["streaming/jog_dro_trace_tail.json"] = (
            json_dump(jog_dro_trace) + "\n"
        )
        trace_lines = ["# Jog DRO trace tail", ""]
        for row in jog_dro_trace:
            if not isinstance(row, dict):
                continue
            ts = str(row.get("ts", "") or "")
            kind = str(row.get("kind", "") or "")
            actual = row.get("actual_mpos")
            est = row.get("est_mpos")
            delta = row.get("delta")
            trace_lines.append(
                f"{ts} [{kind}] actual={actual} est={est} delta={delta}"
            )
        artifacts["streaming/jog_dro_trace_tail.log"] = "\n".join(trace_lines) + "\n"
    jog_dro_stats = runtime_metrics.get("jog_dro_interp_stats")
    if isinstance(jog_dro_stats, dict) and jog_dro_stats:
        artifacts["streaming/jog_dro_interp_stats.json"] = (
            json_dump(jog_dro_stats) + "\n"
        )
    return artifacts
