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
from typing import IO, Protocol, Sequence, cast

from simple_sender.utils.task_timing import record_task_timing


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


class _GcodeLoadCancelled(Exception):
    """Raised when a newer load token supersedes the active worker."""


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
    offsets: Sequence[int]
    preview_lines: list[str]
    all_lines: list[str] | None
    line_capture_limit_hit: bool
    lines_hash: str | None
    split_result: _SplitStreamResultLike
    total_lines_raw: int
    cleaned_input_lines: int


def _should_run_full_validation(
    app,
    deps,
    *,
    token: int,
    path: str,
    line_count: int,
    validation_enabled: bool,
) -> bool:
    _check_load_token(app, token)
    threshold = int(deps.STREAMING_VALIDATION_PROMPT_LINES)
    if line_count <= threshold:
        return validation_enabled
    if not validation_enabled:
        app.ui_q.put(("log", "[gcode] Full validation skipped for large file (fast-load mode)."))
        return False

    result_q = deps.queue.Queue(maxsize=1)
    app.ui_q.put((
        "streaming_validation_prompt",
        token,
        deps.os.path.basename(path),
        line_count,
        threshold,
        result_q,
    ))
    try:
        allow = bool(result_q.get(timeout=deps.STREAMING_VALIDATION_PROMPT_TIMEOUT))
    except deps.queue.Empty:
        allow = False
    _check_load_token(app, token)
    if not allow:
        app.ui_q.put(("log", "[gcode] Full validation skipped for large file by user request."))
    return allow


def _close_temp_file(temp_file: IO[str] | None) -> None:
    if temp_file is None:
        return
    try:
        temp_file.close()
    except (OSError, ValueError):
        return


def _remove_temp_path(deps, temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        deps.os.remove(temp_path)
    except OSError:
        return


def _format_adjusted_lines_message(modified_count: int, split_count: int, max_line_length: int) -> str:
    base = (
        f"[gcode] Adjusted {modified_count} line(s) to fit "
        f"{max_line_length}-byte limit"
    )
    if split_count:
        return f"{base} (split {split_count})."
    return f"{base}."


def _check_load_token(app, token: int) -> None:
    if token != app._gcode_load_token:
        raise _GcodeLoadCancelled()


def _new_offset_index(deps):
    try:
        return deps.array.array("Q")
    except Exception:
        return []


def _resolve_preferred_temp_dir(deps) -> str:
    temp_dir_getter = getattr(deps, "get_preferred_temp_dir", None)
    if callable(temp_dir_getter):
        try:
            candidate = str(temp_dir_getter() or "").strip()
            if candidate:
                return candidate
        except Exception:
            pass
    tempfile_mod = getattr(deps, "tempfile", None)
    if tempfile_mod is not None:
        gettempdir = getattr(tempfile_mod, "gettempdir", None)
        if callable(gettempdir):
            try:
                return str(gettempdir())
            except Exception:
                pass
    return "."


def _resolve_ultra_large_threshold_bytes(app, deps) -> int:
    default_bytes = 0
    try:
        default_bytes = int(getattr(deps, "GCODE_ULTRA_LARGE_SIZE_THRESHOLD", 0) or 0)
    except Exception:
        default_bytes = 0
    if app is None:
        return max(0, default_bytes)
    var = getattr(app, "ultra_large_size_threshold_mb", None)
    if var is None:
        return max(0, default_bytes)
    try:
        threshold_mb = int(var.get())
    except Exception:
        try:
            threshold_mb = int(var)
        except Exception:
            return max(0, default_bytes)
    if threshold_mb <= 0:
        return 0
    return int(threshold_mb) * 1024 * 1024


def _is_ultra_large_file_with_threshold(*, file_size: int | None, threshold_bytes: int) -> bool:
    if file_size is None:
        return False
    return int(threshold_bytes) > 0 and int(file_size) >= int(threshold_bytes)


def _ultra_large_disk_headroom(deps, file_size: int) -> tuple[bool, int | None, int | None, str | None]:
    try:
        multiplier = int(getattr(deps, "GCODE_ULTRA_LARGE_REQUIRED_FREE_MULTIPLIER", 3) or 3)
    except Exception:
        multiplier = 3
    multiplier = max(1, multiplier)
    try:
        margin = int(getattr(deps, "GCODE_ULTRA_LARGE_REQUIRED_FREE_MARGIN_BYTES", 0) or 0)
    except Exception:
        margin = 0
    margin = max(0, margin)
    required_bytes = int(file_size) * multiplier + margin
    temp_dir = _resolve_preferred_temp_dir(deps)
    shutil_mod = getattr(deps, "shutil", None)
    disk_usage = getattr(shutil_mod, "disk_usage", None) if shutil_mod is not None else None
    if not callable(disk_usage):
        return True, required_bytes, None, temp_dir
    try:
        usage = disk_usage(temp_dir)
        free_value = getattr(usage, "free", None)
        if free_value is None:
            free_value = usage[2]
        free_bytes = int(free_value)
    except Exception:
        return True, required_bytes, None, temp_dir
    return free_bytes >= required_bytes, required_bytes, free_bytes, temp_dir


def _split_stream_to_temp_file(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    capture_full_lines: bool,
    full_lines_limit: int | None,
) -> _StreamTempData:
    started_at = deps.time.perf_counter()
    success = False
    temp_path: str | None = None
    try:
        _check_load_token(app, token)
        offsets = _new_offset_index(deps)
        preview_lines: list[str] = []
        all_lines: list[str] | None = [] if capture_full_lines else None
        line_capture_limit_hit = False
        total_lines_raw = 0
        cleaned_input_lines = 0
        current_line_no = 0
        hasher = deps.hashlib.sha256()
        progress_label = f"Scanning {deps.os.path.basename(path)}"
        progress_last_ts = 0.0
        progress_last_pct = -1
        temp_file: IO[str] | None = None
        split_result: _SplitStreamResultLike | None = None

        def write_output(line: str) -> None:
            nonlocal all_lines, line_capture_limit_hit
            _check_load_token(app, token)
            assert temp_file is not None
            offsets.append(temp_file.tell())
            temp_file.write(line)
            temp_file.write("\n")
            if len(preview_lines) < deps.GCODE_STREAMING_PREVIEW_LINES:
                preview_lines.append(line)
            if all_lines is not None:
                if full_lines_limit is not None and len(all_lines) >= full_lines_limit:
                    all_lines = None
                    line_capture_limit_hit = True
                else:
                    all_lines.append(line)
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
            temp_dir = None
            temp_dir_getter = getattr(deps, "get_preferred_temp_dir", None)
            if callable(temp_dir_getter):
                try:
                    temp_dir = str(temp_dir_getter())
                except Exception:
                    temp_dir = None
            try:
                temp_buffer_size = int(getattr(deps, "TEMP_FILE_BUFFER_SIZE", 0) or 0)
            except Exception:
                temp_buffer_size = 0
            temp_kwargs = {
                "mode": "w",
                "encoding": "utf-8",
                "newline": "",
                "delete": False,
                "prefix": "simple_sender_stream_",
                "suffix": ".gcode",
            }
            if temp_dir:
                temp_kwargs["dir"] = temp_dir
            if temp_buffer_size > 0:
                temp_kwargs["buffering"] = temp_buffer_size
            temp_file = deps.tempfile.NamedTemporaryFile(
                **temp_kwargs,
            )
            temp_path = temp_file.name
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:

                def iter_raw_lines():
                    nonlocal total_lines_raw, current_line_no, progress_last_ts, progress_last_pct
                    while True:
                        if (total_lines_raw & 0x1FF) == 0:
                            _check_load_token(app, token)
                        ln = f.readline()
                        if not ln:
                            break
                        total_lines_raw += 1
                        current_line_no = total_lines_raw
                        if file_size and (total_lines_raw & 0x7F) == 0:
                            now = deps.time.perf_counter()
                            if now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL:
                                pos = f.tell()
                                pct = min(100, int(pos * 100 / file_size))
                                if pct != progress_last_pct:
                                    _emit_progress(app, token, pct, 100, progress_label)
                                    progress_last_pct = pct
                                progress_last_ts = now
                        yield ln

                _check_load_token(app, token)

                split_result = deps.split_gcode_lines_stream(
                    iter_raw_lines(),
                    max_len=deps.MAX_LINE_LENGTH,
                    clean_line=clean_and_track,
                    write_line=write_output,
                )
                _check_load_token(app, token)
            if file_size:
                _emit_progress(app, token, 100, 100, progress_label)
        finally:
            _close_temp_file(temp_file)

        assert temp_path is not None
        assert split_result is not None
        success = True
        return _StreamTempData(
            temp_path=temp_path,
            offsets=offsets,
            preview_lines=preview_lines,
            all_lines=all_lines,
            line_capture_limit_hit=line_capture_limit_hit,
            lines_hash=hasher.hexdigest() if offsets else None,
            split_result=split_result,
            total_lines_raw=total_lines_raw,
            cleaned_input_lines=cleaned_input_lines,
        )
    except Exception:
        _remove_temp_path(deps, temp_path)
        raise
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.split_stream", elapsed_ms, success=success)


def _validate_streaming_output(
    app,
    *,
    deps,
    token: int,
    path: str,
    temp_path: str,
    output_lines: int,
) -> object | None:
    started_at = deps.time.perf_counter()
    success = False
    _check_load_token(app, token)

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
                if (line_no & 0x1FF) == 0:
                    _check_load_token(app, token)
                raw_line = rf.readline()
                if not raw_line:
                    break
                line_no += 1
                yield raw_line.rstrip("\r\n")
                if (line_no & 0x7F) == 0:
                    now = deps.time.perf_counter()
                    if now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL:
                        if temp_size:
                            pos = rf.tell()
                            pct = min(100, int(pos * 100 / temp_size))
                        elif output_lines:
                            pct = min(100, int(line_no * 100 / output_lines))
                        else:
                            pct = 100
                        if pct != progress_last_pct:
                            _emit_progress(app, token, pct, 100, progress_label)
                            progress_last_pct = pct
                        progress_last_ts = now
        _emit_progress(app, token, 100, 100, progress_label)

    try:
        result = cast(object, deps.validate_gcode_lines(iter_lines_with_progress()))
        success = True
        return result
    except Exception as exc:
        app.ui_q.put(("log", f"[gcode] Streaming validation failed: {exc}"))
        return None
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.validate_stream", elapsed_ms, success=success)


def _stream_from_disk(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    validate_streaming: bool,
    preview_only: bool,
    streaming_line_threshold: int | None,
    log_message: str | None = None,
) -> None:
    started_at = deps.time.perf_counter()
    success = False
    temp_data = None
    try:
        _check_load_token(app, token)
        if log_message:
            app.ui_q.put(("log", log_message))
        validate_streaming_enabled = bool(validate_streaming)
        temp_data = _split_stream_to_temp_file(
            app,
            path,
            token,
            deps,
            file_size=file_size,
            capture_full_lines=not preview_only,
            full_lines_limit=(
                streaming_line_threshold
                if (not preview_only and streaming_line_threshold is not None)
                else None
            ),
        )

        split_result = temp_data.split_result
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
            msg = _format_adjusted_lines_message(
                split_result.modified_count,
                split_result.split_count,
                deps.MAX_LINE_LENGTH,
            )
            app.ui_q.put(("log", msg))
        output_lines = split_result.lines_written
        if not preview_only and temp_data.line_capture_limit_hit:
            preview_only = True
            threshold_text = (
                f"{streaming_line_threshold:,}"
                if streaming_line_threshold is not None
                else "?"
            )
            app.ui_q.put((
                "log",
                f"[gcode] Large file detected ({output_lines:,} cleaned lines > {threshold_text}); "
                "using preview-only mode.",
            ))
        if not preview_only and temp_data.all_lines is None:
            preview_only = True
        report = None
        should_validate = False
        if output_lines:
            should_validate = _should_run_full_validation(
                app,
                deps,
                token=token,
                path=path,
                line_count=output_lines,
                validation_enabled=validate_streaming_enabled,
            )
        if should_validate:
            report = _validate_streaming_output(
                app,
                deps=deps,
                token=token,
                path=path,
                temp_path=temp_data.temp_path,
                output_lines=output_lines,
            )
        elif not validate_streaming_enabled:
            app.ui_q.put((
                "log",
                "[gcode] Streaming validation disabled (App Settings > Diagnostics).",
            ))
        _check_load_token(app, token)
        lines_for_app = temp_data.preview_lines if preview_only else (temp_data.all_lines or [])
        source = deps.FileGcodeSource(
            temp_data.temp_path,
            temp_data.offsets,
            already_clean=True,
        )
        setattr(source, "_cleanup_path", temp_data.temp_path)
        app.ui_q.put((
            "gcode_loaded_stream",
            token,
            path,
            source,
            lines_for_app,
            temp_data.lines_hash,
            output_lines,
            report,
            preview_only,
        ))
        success = True
    except _GcodeLoadCancelled:
        _remove_temp_path(deps, getattr(temp_data, "temp_path", None))
        return
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
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.stream_total", elapsed_ms, success=success)


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
    try:
        app._gcode_load_started_at = deps.time.perf_counter()
    except Exception:
        app._gcode_load_started_at = None
    deps.disable_job_controls(app)
    app.gcode_stats_var.set("Preparing job...")
    app.status.config(text=f"Preparing job: {deps.os.path.basename(path)}")
    app._set_gcode_loading_indeterminate(f"reading {deps.os.path.basename(path)}")
    app.gview.set_lines_chunked([])

    file_size = None
    preview_only = False
    ultra_large_mode = False
    ultra_large_threshold_bytes = _resolve_ultra_large_threshold_bytes(app, deps)
    try:
        file_size = deps.os.path.getsize(path)
        preview_only = file_size >= deps.GCODE_STREAMING_SIZE_THRESHOLD
        ultra_large_mode = _is_ultra_large_file_with_threshold(
            file_size=file_size,
            threshold_bytes=ultra_large_threshold_bytes,
        )
    except OSError:
        preview_only = False
        ultra_large_mode = False
    try:
        validate_streaming = bool(app.validate_streaming_gcode.get())
    except (AttributeError, TypeError, ValueError):
        validate_streaming = False
    validate_streaming_requested = bool(validate_streaming)
    if ultra_large_mode:
        preview_only = True
        if file_size is not None:
            ok, required_bytes, free_bytes, temp_dir = _ultra_large_disk_headroom(deps, int(file_size))
            if not ok:
                required_text = _format_mb(required_bytes)
                free_text = _format_mb(free_bytes)
                app._gcode_loading = False
                app._finish_gcode_loading()
                deps.messagebox.showerror(
                    "Open G-code",
                    "Not enough free disk space for this ultra-large file load.\n"
                    f"Required: {required_text} free in temp workspace.\n"
                    f"Available: {free_text}\n"
                    f"Temp dir: {temp_dir}",
                )
                app.gcode_stats_var.set("No file loaded")
                app.status.config(text="G-code load failed (insufficient disk space)")
                return
        # Ultra-large mode always uses fast-load behavior to keep Pi-class systems stable.
        validate_streaming = False

    try:
        raw_line_threshold = int(app.streaming_line_threshold.get())
    except (AttributeError, TypeError, ValueError):
        raw_line_threshold = deps.GCODE_STREAMING_LINE_THRESHOLD
    streaming_line_threshold = raw_line_threshold if raw_line_threshold > 0 else None

    def worker():
        try:
            log_message = None
            if preview_only:
                size_text = _format_mb(file_size)
                threshold_text = _format_mb(deps.GCODE_STREAMING_SIZE_THRESHOLD)
                log_message = (
                    f"[gcode] Large file detected ({size_text} >= {threshold_text}); "
                    "using preview-only mode."
                )
            if ultra_large_mode:
                ultra_threshold_text = _format_mb(ultra_large_threshold_bytes)
                size_text = _format_mb(file_size)
                app.ui_q.put((
                    "log",
                    f"[gcode] Ultra-large mode active ({size_text} >= {ultra_threshold_text}); "
                    "forcing preview-only mode and fast-load safeguards.",
                ))
                if validate_streaming_requested:
                    app.ui_q.put(("log", "[gcode] Full validation disabled automatically for ultra-large file load."))
                app.ui_q.put(("log", "[gcode] Recommendation: run a load-only dry-run before cutting."))
            _stream_from_disk(
                app,
                path,
                token,
                deps,
                file_size=file_size,
                validate_streaming=validate_streaming,
                preview_only=preview_only,
                streaming_line_threshold=streaming_line_threshold,
                log_message=log_message,
            )
        except _GcodeLoadCancelled:
            return
        except Exception as exc:
            app.ui_q.put(("gcode_load_error", token, path, str(exc)))

    deps.threading.Thread(target=worker, daemon=True).start()
