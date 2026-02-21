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

import itertools
import os
import tempfile
from collections.abc import Callable, Iterable
from typing import Any, cast

from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import (
    LevelFileResult,
    LevelResult,
    level_gcode_file,
    level_gcode_lines,
    write_gcode_lines,
)
from simple_sender.gcode_parser import clean_gcode_line, split_gcode_lines, split_gcode_lines_stream
from simple_sender.gcode_parser_split import GcodeSplitResult, GcodeSplitStreamResult
from simple_sender.utils.constants import MAX_LINE_LENGTH

from .calculations import _format_overlong_error, _log_split_result


def _level_from_source_lines(
    *,
    source_lines: list[str],
    target_path: str,
    height_map: HeightMap,
    arc_step_rad: float,
    interpolation: str,
    header_lines: list[str] | None,
    log_fn: Callable[[str], None] | None,
    level_gcode_lines_fn: Callable[..., LevelResult],
    write_gcode_lines_fn: Callable[..., LevelFileResult],
    split_gcode_lines_fn: Callable[..., GcodeSplitResult],
) -> LevelFileResult:
    level_result = level_gcode_lines_fn(
        source_lines,
        height_map,
        arc_step_rad=arc_step_rad,
        interpolation=interpolation,
    )
    if level_result.error:
        return LevelFileResult(None, 0, level_result.error, False)
    split_result = split_gcode_lines_fn(level_result.lines, MAX_LINE_LENGTH)
    if split_result.failed_index is not None:
        error = _format_overlong_error(
            level_result.lines,
            fallback_index=split_result.failed_index,
            fallback_len=split_result.failed_len,
            line_offset=len(header_lines or []),
        )
        return LevelFileResult(None, 0, error, False)
    _log_split_result(log_fn, split_result)
    return write_gcode_lines_fn(
        target_path,
        split_result.lines,
        header_lines=header_lines,
    )


def _level_and_write_auto_level_output(
    *,
    source_path: str,
    source_lines: list[str] | None,
    target_path: str,
    height_map: HeightMap,
    arc_step_rad: float,
    interpolation: str,
    header_lines: list[str] | None,
    streaming_mode: bool,
    log_fn: Callable[[str], None] | None = None,
    level_gcode_lines_fn: Callable[..., LevelResult] = level_gcode_lines,
    level_gcode_file_fn: Callable[..., LevelFileResult] = level_gcode_file,
    write_gcode_lines_fn: Callable[..., LevelFileResult] = write_gcode_lines,
    split_gcode_lines_fn: Callable[..., GcodeSplitResult] = split_gcode_lines,
    split_gcode_lines_stream_fn: Callable[..., GcodeSplitStreamResult] = split_gcode_lines_stream,
    clean_gcode_line_fn: Callable[..., str] = clean_gcode_line,
    tempfile_module: Any = tempfile,
) -> LevelFileResult:
    use_lines = (
        not streaming_mode and isinstance(source_lines, list) and bool(source_lines)
    )
    if use_lines:
        return _level_from_source_lines(
            source_lines=cast(list[str], source_lines),
            target_path=target_path,
            height_map=height_map,
            arc_step_rad=arc_step_rad,
            interpolation=interpolation,
            header_lines=header_lines,
            log_fn=log_fn,
            level_gcode_lines_fn=level_gcode_lines_fn,
            write_gcode_lines_fn=write_gcode_lines_fn,
            split_gcode_lines_fn=split_gcode_lines_fn,
        )

    result = level_gcode_file_fn(
        source_path,
        target_path,
        height_map,
        arc_step_rad=arc_step_rad,
        interpolation=interpolation,
        header_lines=header_lines,
    )
    if result.error:
        return result
    expected_headers = [h.rstrip("\r\n") for h in header_lines or []]
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as infile:
            header_buffer: list[str] = []
            matched_headers = True
            for header in expected_headers:
                raw_header = infile.readline()
                if not raw_header:
                    matched_headers = False
                    break
                raw_header_text = raw_header.rstrip("\r\n")
                header_buffer.append(raw_header_text)
                if raw_header_text != header:
                    matched_headers = False
                    break
            if matched_headers and expected_headers:
                header_count = len(header_buffer)
                raw_iter: Iterable[str] = infile
            else:
                header_count = 0
                raw_iter = itertools.chain(header_buffer, infile)
            stream_result = split_gcode_lines_stream_fn(
                raw_iter,
                max_len=MAX_LINE_LENGTH,
                clean_line=clean_gcode_line_fn,
                preserve_raw=True,
            )
    except Exception as exc:
        try:
            os.remove(target_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))

    if stream_result.failed_index is not None:
        try:
            os.remove(target_path)
        except OSError:
            pass
        error = _format_overlong_error(
            [],
            fallback_index=stream_result.failed_index,
            fallback_len=stream_result.failed_len,
            fallback_count=stream_result.too_long,
            line_offset=header_count,
        )
        return LevelFileResult(None, 0, error, False)
    if stream_result.modified_count == 0:
        return result

    header_lines_to_write: list[str] = []
    header_count = 0
    temp_path = None
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as infile:
            header_buffer = []
            matched_headers = True
            for header in expected_headers:
                raw_header = infile.readline()
                if not raw_header:
                    matched_headers = False
                    break
                raw_header_text = raw_header.rstrip("\r\n")
                header_buffer.append(raw_header_text)
                if raw_header_text != header:
                    matched_headers = False
                    break
            if matched_headers and expected_headers:
                header_lines_to_write = header_buffer
                header_count = len(header_lines_to_write)
                rewrite_iter: Iterable[str] = infile
            else:
                rewrite_iter = itertools.chain(header_buffer, infile)

            dir_name = os.path.dirname(target_path) or "."
            base_name = os.path.splitext(os.path.basename(target_path))[0]
            ext = os.path.splitext(target_path)[1] or ".gcode"
            temp_file = tempfile_module.NamedTemporaryFile(
                prefix=f"{base_name}_split_",
                suffix=ext,
                delete=False,
                dir=dir_name,
            )
            temp_path = temp_file.name
            temp_file.close()
            with open(temp_path, "w", encoding="utf-8", newline="") as outfile:
                def write_line(line: str) -> None:
                    outfile.write(line.rstrip("\n"))
                    outfile.write("\n")

                for header_line in header_lines_to_write:
                    write_line(header_line)
                stream_result = split_gcode_lines_stream_fn(
                    rewrite_iter,
                    max_len=MAX_LINE_LENGTH,
                    clean_line=clean_gcode_line_fn,
                    preserve_raw=True,
                    write_line=write_line,
                )
    except Exception as exc:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        try:
            os.remove(target_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))

    if stream_result.failed_index is not None:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        try:
            os.remove(target_path)
        except OSError:
            pass
        error = _format_overlong_error(
            [],
            fallback_index=stream_result.failed_index,
            fallback_len=stream_result.failed_len,
            fallback_count=stream_result.too_long,
            line_offset=header_count,
        )
        return LevelFileResult(None, 0, error, False)
    _log_split_result(log_fn, stream_result)
    try:
        if temp_path:
            os.replace(temp_path, target_path)
    except Exception as exc:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        try:
            os.remove(target_path)
        except OSError:
            pass
        return LevelFileResult(None, 0, str(exc), isinstance(exc, OSError))
    return LevelFileResult(
        target_path,
        stream_result.lines_written + header_count,
        None,
        False,
    )


def _apply_auto_level_to_path(
    *,
    source_path: str,
    source_lines: list[str] | None,
    output_path: str,
    temp_path_fn: Callable[[str], str],
    height_map: HeightMap,
    arc_step_rad: float,
    interpolation: str,
    header_lines: list[str] | None,
    streaming_mode: bool,
    log_fn: Callable[[str], None] | None = None,
    level_gcode_lines_fn: Callable[..., LevelResult] = level_gcode_lines,
    level_gcode_file_fn: Callable[..., LevelFileResult] = level_gcode_file,
    write_gcode_lines_fn: Callable[..., LevelFileResult] = write_gcode_lines,
    split_gcode_lines_fn: Callable[..., GcodeSplitResult] = split_gcode_lines,
    split_gcode_lines_stream_fn: Callable[..., GcodeSplitStreamResult] = split_gcode_lines_stream,
    clean_gcode_line_fn: Callable[..., str] = clean_gcode_line,
    tempfile_module: Any = tempfile,
) -> tuple[LevelFileResult, bool, str | None]:
    result = _level_and_write_auto_level_output(
        source_path=source_path,
        source_lines=source_lines,
        target_path=output_path,
        height_map=height_map,
        arc_step_rad=arc_step_rad,
        interpolation=interpolation,
        header_lines=header_lines,
        streaming_mode=streaming_mode,
        log_fn=log_fn,
        level_gcode_lines_fn=level_gcode_lines_fn,
        level_gcode_file_fn=level_gcode_file_fn,
        write_gcode_lines_fn=write_gcode_lines_fn,
        split_gcode_lines_fn=split_gcode_lines_fn,
        split_gcode_lines_stream_fn=split_gcode_lines_stream_fn,
        clean_gcode_line_fn=clean_gcode_line_fn,
        tempfile_module=tempfile_module,
    )
    fallback_warning = None
    is_temp = False
    if result.error and result.io_error:
        temp_path = temp_path_fn(source_path)
        fallback = _level_and_write_auto_level_output(
            source_path=source_path,
            source_lines=source_lines,
            target_path=temp_path,
            height_map=height_map,
            arc_step_rad=arc_step_rad,
            interpolation=interpolation,
            header_lines=header_lines,
            streaming_mode=streaming_mode,
            log_fn=log_fn,
            level_gcode_lines_fn=level_gcode_lines_fn,
            level_gcode_file_fn=level_gcode_file_fn,
            write_gcode_lines_fn=write_gcode_lines_fn,
            split_gcode_lines_fn=split_gcode_lines_fn,
            split_gcode_lines_stream_fn=split_gcode_lines_stream_fn,
            clean_gcode_line_fn=clean_gcode_line_fn,
            tempfile_module=tempfile_module,
        )
        if fallback.error is None:
            result = fallback
            is_temp = True
            fallback_warning = (
                "Could not write the leveled file next to the source file. "
                "Saved a temporary leveled file instead. Use Save Leveled to keep a copy."
            )
        else:
            result = fallback
    return result, is_temp, fallback_warning
