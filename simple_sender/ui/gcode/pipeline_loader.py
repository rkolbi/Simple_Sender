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


from dataclasses import dataclass
from typing import IO, Protocol, cast


def _format_mb(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{value / (1024 * 1024):.1f} MB"


def _emit_progress(app, token_id: int, done: int, total: int, label: str) -> None:
    app.ui_q.put(("gcode_load_progress", token_id, done, total, label))


class _SystemCommandError(Exception):
    def __init__(self, line_no: int, text: str) -> None:
        super().__init__()
        self.line_no = line_no
        self.text = text


class _SplitStreamResultLike(Protocol):
    lines_written: int
    split_count: int
    modified_count: int
    failed_index: int | None
    failed_len: int | None
    too_long: int


@dataclass(slots=True)
class _StreamTempData:
    temp_path: str
    offsets: list[int]
    preview_lines: list[str]
    lines_hash: str | None
    split_result: _SplitStreamResultLike
    total_lines_raw: int
    cleaned_input_lines: int


def _close_temp_file(temp_file: IO[str] | None) -> None:
    if temp_file is None:
        return
    try:
        temp_file.close()
    except Exception:
        pass


def _remove_temp_path(deps, temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        deps.os.remove(temp_path)
    except OSError:
        pass


def _split_stream_to_temp_file(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _StreamTempData:
    offsets: list[int] = []
    preview_lines: list[str] = []
    total_lines_raw = 0
    cleaned_input_lines = 0
    current_line_no = 0
    hasher = deps.hashlib.sha256()
    progress_label = f"Scanning {deps.os.path.basename(path)}"
    progress_last_ts = 0.0
    progress_last_pct = -1
    temp_path: str | None = None
    temp_file: IO[str] | None = None
    split_result: _SplitStreamResultLike | None = None

    def write_output(line: str) -> None:
        assert temp_file is not None
        offsets.append(temp_file.tell())
        temp_file.write(line)
        temp_file.write("\n")
        if len(preview_lines) < deps.GCODE_STREAMING_PREVIEW_LINES:
            preview_lines.append(line)
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")

    def clean_and_track(raw_text: str) -> str:
        nonlocal cleaned_input_lines
        cleaned = cast(str, deps.clean_gcode_line(raw_text))
        if cleaned:
            cleaned_input_lines += 1
            if cleaned.startswith("$"):
                raise _SystemCommandError(current_line_no, cleaned)
        return cleaned

    try:
        try:
            temp_file = deps.tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                delete=False,
                prefix="simple_sender_stream_",
                suffix=".gcode",
            )
            temp_path = temp_file.name
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                def iter_raw_lines():
                    nonlocal total_lines_raw, current_line_no, progress_last_ts, progress_last_pct
                    while True:
                        pos = f.tell()
                        ln = f.readline()
                        if not ln:
                            break
                        total_lines_raw += 1
                        current_line_no = total_lines_raw
                        if file_size:
                            now = deps.time.perf_counter()
                            pct = min(100, int(pos * 100 / file_size))
                            if (
                                pct != progress_last_pct
                                and now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL
                            ):
                                _emit_progress(app, token, pct, 100, progress_label)
                                progress_last_ts = now
                                progress_last_pct = pct
                        yield ln

                split_result = deps.split_gcode_lines_stream(
                    iter_raw_lines(),
                    max_len=deps.MAX_LINE_LENGTH,
                    clean_line=clean_and_track,
                    write_line=write_output,
                )
            if file_size:
                _emit_progress(app, token, 100, 100, progress_label)
        finally:
            _close_temp_file(temp_file)
    except Exception:
        _remove_temp_path(deps, temp_path)
        raise

    assert temp_path is not None
    assert split_result is not None
    return _StreamTempData(
        temp_path=temp_path,
        offsets=offsets,
        preview_lines=preview_lines,
        lines_hash=hasher.hexdigest() if offsets else None,
        split_result=split_result,
        total_lines_raw=total_lines_raw,
        cleaned_input_lines=cleaned_input_lines,
    )


def _validate_streaming_output(
    app,
    *,
    deps,
    token: int,
    path: str,
    temp_path: str,
    output_lines: int,
) -> object | None:
    validate_prompt_threshold = deps.STREAMING_VALIDATION_PROMPT_LINES
    if output_lines > validate_prompt_threshold:
        result_q = deps.queue.Queue(maxsize=1)
        app.ui_q.put((
            "streaming_validation_prompt",
            token,
            deps.os.path.basename(path),
            output_lines,
            validate_prompt_threshold,
            result_q,
        ))
        try:
            allow = bool(result_q.get(timeout=deps.STREAMING_VALIDATION_PROMPT_TIMEOUT))
        except deps.queue.Empty:
            allow = False
        if not allow:
            app.ui_q.put(("log", "[gcode] Streaming validation skipped by user request."))
            return None

    progress_label = f"Validating {deps.os.path.basename(path)}"
    progress_last_ts = 0.0
    progress_last_pct = -1
    temp_size = None
    try:
        temp_size = deps.os.path.getsize(temp_path)
    except OSError:
        temp_size = None

    def iter_lines_with_progress():
        nonlocal progress_last_ts, progress_last_pct
        with open(temp_path, "r", encoding="utf-8", errors="replace") as rf:
            line_no = 0
            while True:
                pos = rf.tell()
                raw_line = rf.readline()
                if not raw_line:
                    break
                line_no += 1
                yield raw_line.rstrip("\r\n")
                now = deps.time.perf_counter()
                if temp_size:
                    pct = min(100, int(pos * 100 / temp_size))
                elif output_lines:
                    pct = min(100, int(line_no * 100 / output_lines))
                else:
                    pct = 100
                if (
                    pct != progress_last_pct
                    and now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL
                ):
                    _emit_progress(app, token, pct, 100, progress_label)
                    progress_last_ts = now
                    progress_last_pct = pct
        _emit_progress(app, token, 100, 100, progress_label)

    try:
        return cast(object, deps.validate_gcode_lines(iter_lines_with_progress()))
    except Exception as exc:
        app.ui_q.put(("log", f"[gcode] Streaming validation failed: {exc}"))
        return None


def _stream_from_disk(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    validate_streaming: bool,
    log_message: str | None = None,
) -> None:
    if log_message:
        app.ui_q.put(("log", log_message))
    validate_streaming_enabled = bool(validate_streaming)
    temp_data = None
    try:
        temp_data = _split_stream_to_temp_file(
            app,
            path,
            token,
            deps,
            file_size=file_size,
        )
    except _SystemCommandError as exc:
        _remove_temp_path(deps, getattr(temp_data, "temp_path", None))
        app.ui_q.put((
            "gcode_load_invalid_command",
            token,
            path,
            exc.line_no,
            exc.text,
        ))
        return
    except Exception:
        _remove_temp_path(deps, getattr(temp_data, "temp_path", None))
        raise

    if temp_data is None:
        return
    split_result = temp_data.split_result
    if split_result is None:
        return
    if split_result.failed_index is not None:
        _remove_temp_path(deps, temp_data.temp_path)
        too_long = split_result.too_long if split_result.too_long else 1
        app.ui_q.put((
            "gcode_load_invalid",
            token,
            path,
            too_long,
            split_result.failed_index,
            split_result.failed_len,
            temp_data.total_lines_raw,
            temp_data.cleaned_input_lines,
        ))
        return
    if split_result.modified_count:
        if split_result.split_count:
            msg = (
                f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
                f"{deps.MAX_LINE_LENGTH}-byte limit (split {split_result.split_count})."
            )
        else:
            msg = (
                f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
                f"{deps.MAX_LINE_LENGTH}-byte limit."
            )
        app.ui_q.put(("log", msg))
    output_lines = split_result.lines_written
    report = None
    if validate_streaming_enabled and output_lines:
        report = _validate_streaming_output(
            app,
            deps=deps,
            token=token,
            path=path,
            temp_path=temp_data.temp_path,
            output_lines=output_lines,
        )
    elif not validate_streaming_enabled and not validate_streaming:
        app.ui_q.put((
            "log",
            "[gcode] Streaming validation disabled (App Settings > Diagnostics).",
        ))
    if token != app._gcode_load_token:
        _remove_temp_path(deps, temp_data.temp_path)
        return
    source = deps.FileGcodeSource(temp_data.temp_path, temp_data.offsets)
    setattr(source, "_cleanup_path", temp_data.temp_path)
    app.ui_q.put((
        "gcode_loaded_stream",
        token,
        path,
        source,
        temp_data.preview_lines,
        temp_data.lines_hash,
        output_lines,
        report,
    ))


def _load_non_streaming_or_fallback_stream(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    streaming_line_threshold: int | None,
    validate_streaming: bool,
) -> None:
    force_streaming = False
    line_threshold_hit = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = []
        line_map = []
        total_lines = 0
        cleaned_lines = 0
        system_cmd_line = None
        system_cmd_text = None
        for line_no, ln in enumerate(f, start=1):
            total_lines = line_no
            cleaned = deps.clean_gcode_line(ln)
            if not cleaned:
                continue
            if cleaned.startswith("$"):
                system_cmd_line = line_no
                system_cmd_text = cleaned
                break
            cleaned_lines += 1
            if streaming_line_threshold and cleaned_lines > streaming_line_threshold:
                force_streaming = True
                line_threshold_hit = cleaned_lines
                break
            lines.append(cleaned)
            line_map.append(line_no)
    if force_streaming:
        threshold_text = (
            f"{streaming_line_threshold:,}"
            if streaming_line_threshold is not None
            else "?"
        )
        hit_text = f"{line_threshold_hit:,}" if line_threshold_hit is not None else "?"
        _stream_from_disk(
            app,
            path,
            token,
            deps,
            file_size=file_size,
            validate_streaming=validate_streaming,
            log_message=(
                f"[gcode] Large file detected ({hit_text} cleaned lines >= {threshold_text}); "
                "using streaming mode."
            ),
        )
        return
    if system_cmd_line is not None:
        app.ui_q.put((
            "gcode_load_invalid_command",
            token,
            path,
            system_cmd_line,
            system_cmd_text,
        ))
        return
    result = deps.split_gcode_lines(lines, deps.MAX_LINE_LENGTH)
    if result.failed_index is not None:
        too_long, first_idx, first_len = deps._find_overlong_lines(
            lines,
            fallback_index=result.failed_index,
            fallback_len=result.failed_len,
        )
        if first_idx is not None and 0 <= first_idx < len(line_map):
            first_idx = line_map[first_idx] - 1
        app.ui_q.put((
            "gcode_load_invalid",
            token,
            path,
            too_long,
            first_idx,
            first_len,
            total_lines,
            len(lines),
        ))
        return
    if result.modified_count:
        if result.split_count:
            msg = (
                f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                f"{deps.MAX_LINE_LENGTH}-byte limit (split {result.split_count})."
            )
        else:
            msg = (
                f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                f"{deps.MAX_LINE_LENGTH}-byte limit."
            )
        app.ui_q.put(("log", msg))
    lines = result.lines
    report = deps.validate_gcode_lines(lines)
    lines_hash = deps.hash_lines(lines)
    app.ui_q.put(("gcode_loaded", token, path, lines, lines_hash, True, report))


def load_gcode_from_path(app, path: str, module):
    deps = module
    if app.grbl.is_streaming():
        deps.messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
        return
    if not deps.os.path.isfile(path):
        deps.messagebox.showerror("Open G-code", "File not found.")
        return
    app.settings["last_gcode_dir"] = deps.os.path.dirname(path)
    app._gcode_load_token += 1
    token = app._gcode_load_token
    app._gcode_loading = True
    deps.disable_job_controls(app)
    app.gcode_stats_var.set("Loading...")
    app.status.config(text=f"Loading: {deps.os.path.basename(path)}")
    app._set_gcode_loading_indeterminate(f"Reading {deps.os.path.basename(path)}")
    app.gview.set_lines_chunked([])

    use_streaming = False
    file_size = None
    try:
        file_size = deps.os.path.getsize(path)
        use_streaming = file_size >= deps.GCODE_STREAMING_SIZE_THRESHOLD
    except OSError:
        use_streaming = False
    try:
        validate_streaming = bool(app.validate_streaming_gcode.get())
    except Exception:
        validate_streaming = False

    try:
        raw_line_threshold = int(app.streaming_line_threshold.get())
    except Exception:
        raw_line_threshold = deps.GCODE_STREAMING_LINE_THRESHOLD
    streaming_line_threshold = raw_line_threshold if raw_line_threshold > 0 else None

    def worker():
        try:
            if use_streaming:
                size_text = _format_mb(file_size)
                threshold_text = _format_mb(deps.GCODE_STREAMING_SIZE_THRESHOLD)
                _stream_from_disk(
                    app,
                    path,
                    token,
                    deps,
                    file_size=file_size,
                    validate_streaming=validate_streaming,
                    log_message=(
                        f"[gcode] Large file detected ({size_text} >= {threshold_text}); "
                        "using streaming mode."
                    ),
                )
                return
            _load_non_streaming_or_fallback_stream(
                app,
                path,
                token,
                deps,
                file_size=file_size,
                streaming_line_threshold=streaming_line_threshold,
                validate_streaming=validate_streaming,
            )
        except Exception as exc:
            app.ui_q.put(("gcode_load_error", token, path, str(exc)))

    deps.threading.Thread(target=worker, daemon=True).start()
