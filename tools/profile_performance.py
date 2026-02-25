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
import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simple_sender.gcode_parser import (
    clean_gcode_line,
    parse_gcode_lines,
    split_gcode_lines,
    split_gcode_lines_stream,
)
from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.utils.constants import GCODE_STREAMING_PREVIEW_LINES, MAX_LINE_LENGTH


def _generate_gcode(path: Path, lines: int, arc_every: int) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("G21\n")
        for i in range(lines):
            x = (i % 1000) * 0.01
            y = (i % 800) * 0.01
            if arc_every and i > 0 and i % arc_every == 0:
                handle.write(f"G2 X{x:.3f} Y{y:.3f} I0.5 J0.0\n")
            else:
                handle.write(f"G1 X{x:.3f} Y{y:.3f} F1000\n")


def _streaming_scan(path: Path) -> dict:
    offsets: list[int] = []
    preview_lines: list[str] = []
    total_lines = 0
    cleaned_lines = 0
    too_long = 0
    first_idx = None
    first_len = None
    invalid = False
    hasher = hashlib.sha256()
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        while True:
            pos = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            total_lines += 1
            cleaned = clean_gcode_line(raw)
            if not cleaned:
                continue
            cleaned_lines += 1
            encoded = cleaned.encode("utf-8")
            line_len = len(encoded) + 1
            if line_len > MAX_LINE_LENGTH:
                too_long += 1
                if first_idx is None:
                    first_idx = total_lines - 1
                    first_len = line_len
                invalid = True
                continue
            if invalid:
                continue
            offsets.append(pos)
            if len(preview_lines) < GCODE_STREAMING_PREVIEW_LINES:
                preview_lines.append(cleaned)
            hasher.update(encoded)
            hasher.update(b"\n")
    return {
        "offsets": offsets,
        "preview_lines": preview_lines,
        "hash": hasher.hexdigest() if offsets else None,
        "total_lines": total_lines,
        "cleaned_lines": cleaned_lines,
        "too_long": too_long,
        "first_idx": first_idx,
        "first_len": first_len,
    }


def _streaming_validate(path: Path) -> dict:
    def iter_cleaned_lines():
        with path.open("r", encoding="utf-8", errors="replace") as reader:
            for raw in reader:
                cleaned = clean_gcode_line(raw)
                if cleaned:
                    yield cleaned

    report = validate_gcode_lines(iter_cleaned_lines())
    return {"report": report}


def _timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed


def _format_seconds(seconds: float) -> str:
    return f"{seconds * 1000:.2f} ms"


def _profile_streaming(path: Path, validate: bool, repeat: int) -> None:
    totals: dict[str, list[float]] = {"scan": [], "validate": []}
    for _ in range(repeat):
        _, scan_time = _timed(_streaming_scan, path)
        totals["scan"].append(scan_time)
        if validate:
            _, validate_time = _timed(_streaming_validate, path)
            totals["validate"].append(validate_time)
    print(f"Mode: streaming | File: {path}")
    print(f"scan: {_format_seconds(sum(totals['scan']) / repeat)}")
    if validate:
        print(f"validate: {_format_seconds(sum(totals['validate']) / repeat)}")


def _profile_full(path: Path, parse: bool, repeat: int) -> None:
    totals: dict[str, list[float]] = {"read_clean": [], "split": [], "validate": [], "parse": []}
    for _ in range(repeat):
        lines, read_time = _timed(_load_clean_lines, path)
        totals["read_clean"].append(read_time)
        split_result, split_time = _timed(split_gcode_lines, lines, MAX_LINE_LENGTH)
        if split_result.failed_index is not None:
            raise RuntimeError(
                f"Line too long at index {split_result.failed_index} (len={split_result.failed_len})."
            )
        lines = split_result.lines
        totals["split"].append(split_time)
        _, validate_time = _timed(validate_gcode_lines, lines)
        totals["validate"].append(validate_time)
        if parse:
            _, parse_time = _timed(parse_gcode_lines, lines)
            totals["parse"].append(parse_time)
    print(f"Mode: full | File: {path}")
    print(f"read_clean: {_format_seconds(sum(totals['read_clean']) / repeat)}")
    print(f"split: {_format_seconds(sum(totals['split']) / repeat)}")
    print(f"validate: {_format_seconds(sum(totals['validate']) / repeat)}")
    if parse:
        print(f"parse: {_format_seconds(sum(totals['parse']) / repeat)}")


def _profile_split(path: Path, repeat: int) -> None:
    totals: dict[str, list[float]] = {"read_clean": [], "split": []}
    for _ in range(repeat):
        lines, read_time = _timed(_load_clean_lines, path)
        totals["read_clean"].append(read_time)
        split_result, split_time = _timed(split_gcode_lines, lines, MAX_LINE_LENGTH)
        if split_result.failed_index is not None:
            raise RuntimeError(
                f"Line too long at index {split_result.failed_index} (len={split_result.failed_len})."
            )
        totals["split"].append(split_time)
    print(f"Mode: split | File: {path}")
    print(f"read_clean: {_format_seconds(sum(totals['read_clean']) / repeat)}")
    print(f"split: {_format_seconds(sum(totals['split']) / repeat)}")


def _profile_split_stream(path: Path, repeat: int, preserve_raw: bool) -> None:
    totals: list[float] = []
    for _ in range(repeat):
        def _run():
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return split_gcode_lines_stream(
                    handle,
                    max_len=MAX_LINE_LENGTH,
                    clean_line=clean_gcode_line,
                    preserve_raw=preserve_raw,
                )

        result, split_time = _timed(_run)
        if result.failed_index is not None:
            raise RuntimeError(
                f"Line too long at index {result.failed_index} (len={result.failed_len})."
            )
        totals.append(split_time)
    print(f"Mode: split-stream | File: {path}")
    print(f"preserve_raw: {preserve_raw}")
    print(f"split_stream: {_format_seconds(sum(totals) / repeat)}")


def _load_clean_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            cleaned = clean_gcode_line(raw)
            if cleaned:
                lines.append(cleaned)
    return lines


def _parse_sizes(text: str) -> list[int]:
    sizes = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            continue
        sizes.append(int(raw))
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile large G-code file handling.")
    parser.add_argument("--path", help="Path to a G-code file to profile.")
    parser.add_argument(
        "--mode",
        choices=("streaming", "full", "split", "split-stream"),
        default="streaming",
        help="Profile streaming scan, full load pipeline, or split passes.",
    )
    parser.add_argument(
        "--sizes",
        default="1000,10000,100000",
        help="Comma-separated line counts for generated files.",
    )
    parser.add_argument(
        "--arc-every",
        type=int,
        default=0,
        help="Insert a G2 arc every N lines (0 disables).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each profile run and average results.",
    )
    parser.add_argument(
        "--validate-streaming",
        action="store_true",
        help="Run streaming validation pass (streaming mode only).",
    )
    parser.add_argument(
        "--no-parse",
        action="store_true",
        help="Skip parse step (full mode only).",
    )
    parser.add_argument(
        "--preserve-raw",
        action="store_true",
        help="Preserve raw comment segments (split-stream mode only).",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep generated files instead of cleaning up.",
    )
    args = parser.parse_args()

    paths: list[Path] = []
    temp_dir: Path | None = None
    if args.path:
        paths.append(Path(args.path))
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="simple_sender_perf_"))
        for size in _parse_sizes(args.sizes):
            out_path = temp_dir / f"gcode_{size}.nc"
            _generate_gcode(out_path, size, args.arc_every)
            paths.append(out_path)

    for path in paths:
        if args.mode == "streaming":
            _profile_streaming(path, args.validate_streaming, args.repeat)
        elif args.mode == "full":
            _profile_full(path, not args.no_parse, args.repeat)
        elif args.mode == "split":
            _profile_split(path, args.repeat)
        else:
            _profile_split_stream(path, args.repeat, args.preserve_raw)
        print("")

    if temp_dir and not args.keep_files:
        for path in temp_dir.iterdir():
            try:
                path.unlink()
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    main()
