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
import gc
import hashlib
import sys
import tempfile
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simple_sender.gcode_parser import clean_gcode_line, parse_gcode_lines, split_gcode_lines  # noqa: E402
from simple_sender.gcode_validator import validate_gcode_lines  # noqa: E402
from simple_sender.utils.constants import GCODE_STREAMING_PREVIEW_LINES, MAX_LINE_LENGTH  # noqa: E402


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


def _format_mb(value: int) -> str:
    return f"{value / (1024 * 1024):.2f} MB"


def _print_memory(label: str) -> None:
    current, peak = tracemalloc.get_traced_memory()
    print(f"{label}: current={_format_mb(current)} peak={_format_mb(peak)}")


def _streaming_scan(path: Path) -> dict:
    offsets: list[int] = []
    preview_lines: list[str] = []
    total_lines = 0
    cleaned_lines = 0
    too_long = 0
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


def _load_clean_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            cleaned = clean_gcode_line(raw)
            if cleaned:
                lines.append(cleaned)
    return lines


def _profile_streaming(path: Path, validate: bool, run_gc: bool) -> dict:
    if run_gc:
        gc.collect()
    tracemalloc.reset_peak()
    data = _streaming_scan(path)
    _print_memory("scan")
    print(
        f"lines: total={data['total_lines']} cleaned={data['cleaned_lines']} "
        f"offsets={len(data['offsets'])}"
    )
    if validate:
        if run_gc:
            gc.collect()
        tracemalloc.reset_peak()
        report = _streaming_validate(path)
        data.update(report)
        _print_memory("validate")
    return data


def _profile_full(path: Path, parse: bool, run_gc: bool) -> dict:
    if run_gc:
        gc.collect()
    tracemalloc.reset_peak()
    lines = _load_clean_lines(path)
    _print_memory("read_clean")

    if run_gc:
        gc.collect()
    tracemalloc.reset_peak()
    split_result = split_gcode_lines(lines, MAX_LINE_LENGTH)
    if split_result.failed_index is not None:
        raise RuntimeError(
            f"Line too long at index {split_result.failed_index} (len={split_result.failed_len})."
        )
    lines = split_result.lines
    _print_memory("split")

    if run_gc:
        gc.collect()
    tracemalloc.reset_peak()
    report = validate_gcode_lines(lines)
    _print_memory("validate")

    parse_result = None
    if parse:
        if run_gc:
            gc.collect()
        tracemalloc.reset_peak()
        parse_result = parse_gcode_lines(lines)
        _print_memory("parse")

    return {
        "lines": lines,
        "split_result": split_result,
        "report": report,
        "parse_result": parse_result,
    }


def _parse_sizes(text: str) -> list[int]:
    sizes = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            continue
        sizes.append(int(raw))
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile memory usage for large G-code files.")
    parser.add_argument("--path", help="Path to a G-code file to profile.")
    parser.add_argument(
        "--mode",
        choices=("streaming", "full"),
        default="streaming",
        help="Profile streaming scan or full load pipeline.",
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
        "--gc",
        action="store_true",
        help="Run gc.collect() before each stage.",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep generated files instead of cleaning up.",
    )
    args = parser.parse_args()

    tracemalloc.start()

    paths: list[Path] = []
    temp_dir: Path | None = None
    if args.path:
        paths.append(Path(args.path))
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="simple_sender_mem_"))
        for size in _parse_sizes(args.sizes):
            out_path = temp_dir / f"gcode_{size}.nc"
            _generate_gcode(out_path, size, args.arc_every)
            paths.append(out_path)

    for path in paths:
        print(f"File: {path}")
        if args.mode == "streaming":
            _profile_streaming(path, args.validate_streaming, args.gc)
        else:
            _profile_full(path, not args.no_parse, args.gc)
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
