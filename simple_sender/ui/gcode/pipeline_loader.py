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
from collections import deque
import math
import re
from typing import Any, cast

from simple_sender.constants.messages import BusyMessages, DialogTitles
from simple_sender.utils.task_timing import record_task_timing
from .pipeline_loader_prepare import (
    _quick_file_fingerprint,
    _resolve_fast_prepare_scan_limit_lines,
    _resolve_index_mode_policy,
    _sample_tail_lines_from_file,
    _trim_sampled_lines,
)
from .pipeline_loader_ssmeta import (
    _parse_ssmeta_line,
    _read_ssmeta_header,
    _ssmeta_bounds_box,
    _ssmeta_units_value,
)

__all__ = ["_parse_ssmeta_line"]


def _format_mb(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{value / (1024 * 1024):.1f} MB"


def _emit_progress(app, token_id: int, done: int, total: int, label: str) -> None:
    app.ui_q.put(("gcode_load_progress", token_id, done, total, label))


class _GcodeLoadCancelled(Exception):
    """Raised when a newer load token supersedes the active worker."""


class _SystemCommandError(Exception):
    def __init__(self, line_no: int, text: str) -> None:
        super().__init__(text)
        self.line_no = int(line_no)
        self.text = str(text)


class _UnsendableLineError(Exception):
    def __init__(self, line_no: int, text: str, reason: str) -> None:
        super().__init__(reason)
        self.line_no = int(line_no)
        self.text = str(text)
        self.reason = str(reason)


@dataclass(slots=True)
class _JobSnapshotData:
    path: str
    sha256: str
    byte_size: int
    raw_line_count: int
    cleaned_line_count: int
    mtime_ns: int


@dataclass(slots=True)
class _FastPrepareData:
    sample_lines: list[str]
    sampled_head_lines: int
    sampled_tail_lines: int
    sampled_interval_lines: int
    sampled_max_lines: int
    sampled_line_count: int
    file_size_bytes: int
    file_line_count: int
    file_line_count_known: bool
    cleaned_lines_estimate: int
    cleaned_lines_known: bool
    executable_lines_estimate: int
    motion_lines_estimate: int
    sampled_executable_lines: int
    sampled_motion_lines: int
    raw_lines_scanned: int
    bytes_scanned: int
    lines_hash_quick: str
    quick_scan_ms: float
    bounds_box: dict[str, float] | None
    bounds_confidence: str
    dimensions_confidence_reasons: dict[str, bool]
    estimated_job_time_sec: int | None
    estimate_confidence: str
    estimate_confidence_reasons: dict[str, bool]
    estimate_inputs_snapshot: dict[str, Any]
    autolevel_prereq_snapshot: dict[str, Any]
    ssmeta_present: bool
    ssmeta: dict[str, str]
    dimensions_source: str
    units_source: str
    ssmeta_scan_reduced: bool


def _check_load_token(app, token: int) -> None:
    if token != app._gcode_load_token:
        raise _GcodeLoadCancelled()


def _remove_temp_path(deps, path: str | None) -> None:
    if not path:
        return
    try:
        deps.os.remove(path)
    except OSError:
        return


def _source_stat_identity(stat_result: Any) -> tuple[int, int, int, int]:
    return (
        int(getattr(stat_result, "st_dev", 0) or 0),
        int(getattr(stat_result, "st_ino", 0) or 0),
        int(getattr(stat_result, "st_size", 0) or 0),
        int(getattr(stat_result, "st_mtime_ns", 0) or 0),
    )


def _create_job_snapshot(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _JobSnapshotData:
    """Create the bounded, canonical file that is later admitted to the worker."""
    started_at = deps.time.perf_counter()
    success = False
    snapshot_path: str | None = None
    try:
        _check_load_token(app, token)
        temp_dir = str(deps.get_preferred_temp_dir() or "").strip() or None
        buffer_size = max(1, int(getattr(deps, "TEMP_FILE_BUFFER_SIZE", 64 * 1024)))
        hasher = deps.hashlib.sha256()
        raw_line_count = 0
        cleaned_line_count = 0
        byte_size = 0
        progress_last_ts = 0.0
        progress_last_pct = -1
        source_identity_before: tuple[int, int, int, int] | None = None
        source_identity_after: tuple[int, int, int, int] | None = None

        with deps.tempfile.NamedTemporaryFile(
            mode="wb",
            buffering=buffer_size,
            delete=False,
            prefix="simple_sender_job_",
            suffix=".gcode",
            dir=temp_dir,
        ) as snapshot_file:
            snapshot_path = str(snapshot_file.name)
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as source:
                try:
                    source_identity_before = _source_stat_identity(
                        deps.os.fstat(source.fileno())
                    )
                except (AttributeError, OSError):
                    source_identity_before = None
                while True:
                    if (raw_line_count & 0x1FF) == 0:
                        _check_load_token(app, token)
                    raw = source.readline()
                    if not raw:
                        break
                    raw_line_count += 1
                    cleaned = cast(str, deps.clean_gcode_line(raw))
                    if cleaned:
                        if cleaned.startswith("$"):
                            raise _SystemCommandError(raw_line_count, cleaned)
                        try:
                            payload = cleaned.encode("ascii") + b"\n"
                        except UnicodeEncodeError as exc:
                            raise _UnsendableLineError(
                                raw_line_count,
                                cleaned,
                                "Non-ASCII characters are not supported by GRBL job streaming.",
                            ) from exc
                        if len(payload) > int(deps.MAX_LINE_LENGTH):
                            raise _UnsendableLineError(
                                raw_line_count,
                                cleaned,
                                f"Line is {len(payload)} bytes including newline; GRBL limit is {deps.MAX_LINE_LENGTH} bytes.",
                            )
                        snapshot_file.write(payload)
                        hasher.update(payload)
                        byte_size += len(payload)
                        cleaned_line_count += 1
                    if file_size and (raw_line_count & 0x7F) == 0:
                        now = deps.time.perf_counter()
                        if now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL:
                            try:
                                pct = min(100, int(source.tell() * 100 / file_size))
                            except (OSError, ValueError):
                                pct = progress_last_pct
                            if pct != progress_last_pct:
                                _emit_progress(
                                    app,
                                    token,
                                    pct,
                                    100,
                                    f"Securing job snapshot: {deps.os.path.basename(path)}",
                                )
                                progress_last_pct = pct
                            progress_last_ts = now
                try:
                    source_identity_after = _source_stat_identity(
                        deps.os.fstat(source.fileno())
                    )
                except (AttributeError, OSError):
                    source_identity_after = None
            snapshot_file.flush()
            try:
                deps.os.fsync(snapshot_file.fileno())
            except (AttributeError, OSError):
                pass

        _check_load_token(app, token)
        if (
            source_identity_before is not None
            and source_identity_after is not None
            and source_identity_before != source_identity_after
        ):
            raise RuntimeError(
                "The selected G-code file changed while it was being prepared. Reload the job after the file is stable."
            )
        snapshot_stat = deps.os.stat(snapshot_path)
        if int(getattr(snapshot_stat, "st_size", -1)) != byte_size:
            raise RuntimeError("Prepared job snapshot size verification failed.")
        _emit_progress(
            app,
            token,
            100,
            100,
            f"Secured job snapshot: {deps.os.path.basename(path)}",
        )
        success = True
        return _JobSnapshotData(
            path=snapshot_path,
            sha256=hasher.hexdigest(),
            byte_size=int(byte_size),
            raw_line_count=int(raw_line_count),
            cleaned_line_count=int(cleaned_line_count),
            mtime_ns=int(getattr(snapshot_stat, "st_mtime_ns", 0) or 0),
        )
    except Exception:
        _remove_temp_path(deps, snapshot_path)
        raise
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.snapshot", elapsed_ms, success=success)


def _validate_job_snapshot(
    app,
    *,
    deps,
    token: int,
    display_path: str,
    snapshot: _JobSnapshotData,
) -> Any:
    """Run the full bounded validator before the snapshot can be admitted."""
    started_at = deps.time.perf_counter()
    success = False
    try:
        _check_load_token(app, token)
        line_count = 0
        validated_hasher = deps.hashlib.sha256()

        def iter_snapshot_lines():
            nonlocal line_count
            with open(
                snapshot.path,
                "rb",
                buffering=max(
                    1, int(getattr(deps, "TEMP_FILE_BUFFER_SIZE", 64 * 1024))
                ),
            ) as handle:
                while True:
                    if (line_count & 0x1FF) == 0:
                        _check_load_token(app, token)
                    raw = handle.readline()
                    if not raw:
                        break
                    validated_hasher.update(raw)
                    line_count += 1
                    if (line_count & 0x7F) == 0:
                        _emit_progress(
                            app,
                            token,
                            min(
                                100,
                                int(
                                    line_count
                                    * 100
                                    / max(1, snapshot.cleaned_line_count)
                                ),
                            ),
                            100,
                            f"Validating job: {deps.os.path.basename(display_path)}",
                        )
                    yield raw.decode("ascii", errors="strict").rstrip("\r\n")

        report = deps.validate_gcode_lines(iter_snapshot_lines())
        _check_load_token(app, token)
        if validated_hasher.hexdigest() != snapshot.sha256:
            raise RuntimeError("Prepared job snapshot digest verification failed.")
        if int(getattr(report, "total_lines", -1)) != snapshot.cleaned_line_count:
            raise RuntimeError("Prepared job snapshot line-count verification failed.")
        report.snapshot_sha256 = snapshot.sha256
        _emit_progress(
            app,
            token,
            100,
            100,
            f"Validated job: {deps.os.path.basename(display_path)}",
        )
        success = True
        return report
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.validate_snapshot", elapsed_ms, success=success)


def _validation_blocking_summary(report: Any) -> str | None:
    sections: list[str] = []
    for attr, label in (
        ("long_line_count", "overlong lines"),
        ("unsupported_axes", "unsupported axes"),
        ("unsupported_words", "unknown word letters"),
        ("unsupported_g_codes", "unsupported G-codes"),
        ("unsupported_m_codes", "unsupported M-codes"),
        ("grbl_warnings", "GRBL-incompatible commands"),
        ("malformed_line_count", "malformed or unsupported syntax lines"),
    ):
        value = getattr(report, attr, None)
        if not value:
            continue
        if isinstance(value, int):
            detail = str(value)
        else:
            try:
                detail = ", ".join(f"{key} ({count})" for key, count in value.items())
            except Exception:
                detail = str(value)
        sections.append(f"{label}: {detail}")
    if not sections:
        return None
    return (
        "Complete job validation found commands that cannot be sent reliably to GRBL:\n"
        + "\n".join(f"- {section}" for section in sections)
        + "\n\nThe job was not loaded. Correct the post-processor output and reload it."
    )


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


def _is_ultra_large_file_with_threshold(
    *, file_size: int | None, threshold_bytes: int
) -> bool:
    if file_size is None:
        return False
    return int(threshold_bytes) > 0 and int(file_size) >= int(threshold_bytes)


_MOTION_GCODE_PAT = re.compile(r"(?<![0-9.])G(?:0|1|2|3)(?![0-9.])")
_MOTION_AXIS_PAT = re.compile(r"[XYZ][-+]?(?:\d+(?:\.\d*)?|\.\d+)?", re.IGNORECASE)
_G20_PAT = re.compile(r"(?<![0-9.])G20(?![0-9.])", re.IGNORECASE)
_G21_PAT = re.compile(r"(?<![0-9.])G21(?![0-9.])", re.IGNORECASE)
_G90_PAT = re.compile(r"(?<![0-9.])G90(?![0-9.])", re.IGNORECASE)
_G91_PAT = re.compile(r"(?<![0-9.])G91(?![0-9.])", re.IGNORECASE)
_G0_PAT = re.compile(r"(?<![0-9.])G0(?![0-9.])", re.IGNORECASE)
_G1_PAT = re.compile(r"(?<![0-9.])G1(?![0-9.])", re.IGNORECASE)
_G2_PAT = re.compile(r"(?<![0-9.])G2(?![0-9.])", re.IGNORECASE)
_G3_PAT = re.compile(r"(?<![0-9.])G3(?![0-9.])", re.IGNORECASE)
_WORD_PAT = re.compile(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_AXIS_WORDS = ("X", "Y", "Z")


def _is_motion_line(line: str) -> bool:
    text = str(line or "").strip().upper()
    if not text:
        return False
    if _MOTION_GCODE_PAT.search(text):
        return True
    return bool(_MOTION_AXIS_PAT.search(text))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_motion_setting(settings_data: Any, key: str) -> float | None:
    if not isinstance(settings_data, dict):
        return None
    raw_entry = settings_data.get(key)
    if isinstance(raw_entry, tuple):
        raw = raw_entry[0] if raw_entry else None
    else:
        raw = raw_entry
    return _safe_float(raw)


def _capture_motion_settings_snapshot(app: Any) -> dict[str, float | None]:
    motion_settings: dict[str, float | None] = {}
    settings_controller = getattr(app, "settings_controller", None)
    settings_data = (
        getattr(settings_controller, "_settings_data", None)
        if settings_controller is not None
        else None
    )
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        motion_settings[key] = _extract_motion_setting(settings_data, key)
    return motion_settings


def _has_complete_motion_settings(motion_settings: dict[str, float | None]) -> bool:
    for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
        if _safe_float(motion_settings.get(key)) is None:
            return False
    return True


def _resolve_quick_rate_tuple(
    app: Any, motion_settings: dict[str, float | None]
) -> tuple[tuple[float, float, float], str]:
    if _has_complete_motion_settings(motion_settings):
        return (
            (
                float(motion_settings.get("$110") or 0.0),
                float(motion_settings.get("$111") or 0.0),
                float(motion_settings.get("$112") or 0.0),
            ),
            "grbl",
        )
    try:
        rate_x_var = getattr(app, "estimate_rate_x_var", None)
        rate_y_var = getattr(app, "estimate_rate_y_var", None)
        rate_z_var = getattr(app, "estimate_rate_z_var", None)
        if rate_x_var is None or rate_y_var is None or rate_z_var is None:
            raise ValueError("missing estimate rate variables")
        rx = float(rate_x_var.get())
        ry = float(rate_y_var.get())
        rz = float(rate_z_var.get())
        if rx > 0.0 and ry > 0.0 and rz > 0.0:
            units = str(
                getattr(getattr(app, "unit_mode", None), "get", lambda: "mm")() or "mm"
            ).lower()
            scale = 25.4 if units.startswith("in") else 1.0
            return ((rx * scale, ry * scale, rz * scale), "estimate")
    except Exception:
        pass
    try:
        fallback_var = getattr(app, "fallback_rapid_rate", None)
        if fallback_var is None:
            raise ValueError("missing fallback rapid rate")
        fallback = float(fallback_var.get())
        if fallback > 0.0:
            return ((fallback, fallback, fallback), "fallback")
    except Exception:
        pass
    # Keep a deterministic default for rough quick-scan estimates.
    return ((2000.0, 2000.0, 500.0), "default")


def _quick_scan_bounds_and_estimate(
    app: Any,
    deps: Any,
    *,
    sampled_lines: list[str],
    sampled_executable_lines: int,
    sampled_motion_lines: int,
    executable_total_estimate: int,
    motion_total_estimate: int,
    cleaned_lines_known: bool,
    source_path: str,
    source_hash: str,
    source_total_lines: int,
) -> tuple[
    dict[str, float] | None,
    str,
    dict[str, bool],
    int | None,
    str,
    dict[str, bool],
    dict[str, Any],
    dict[str, Any],
]:
    if not sampled_lines:
        now_ts = float(deps.time.time())
        empty_snapshot = {
            "captured_at_ts": now_ts,
            "capture_stage": "quick_scan",
            "gcode_hash": str(source_hash or ""),
            "stats_mode": "quick_scan_empty",
            "stats_sample_scale": 1.0,
            "stats_sample_line_count": 0,
            "stats_sample_total_lines": int(max(0, source_total_lines)),
            "stats_sample_executable_lines": 0,
            "stats_sample_motion_lines": 0,
            "stats_executable_total_lines": int(max(0, executable_total_estimate)),
            "stats_motion_total_lines": int(max(0, motion_total_estimate)),
            "rate_source": "n/a",
            "estimate_confidence": "provisional",
            "grbl_motion_settings": _capture_motion_settings_snapshot(app),
            "modal_context": {
                "units": None,
                "distance_mode": None,
                "feed_mode": None,
                "sampled_lines": 0,
                "sample_limit": 0,
            },
        }
        autolevel_snapshot = {
            "stage": "quick_scan",
            "prepared_at_ts": now_ts,
            "source_path": str(source_path or ""),
            "source_exists": bool(source_path and deps.os.path.isfile(source_path)),
            "source_hash": str(source_hash or ""),
            "source_total_lines": int(max(0, source_total_lines)),
            "bounds_ready": False,
            "bounds": None,
            "bounds_confidence": "rough",
            "xy_width_mm": 0.0,
            "xy_height_mm": 0.0,
            "z_min_mm": 0.0,
            "z_max_mm": 0.0,
            "probe_grid_applicable": False,
        }
        return (
            None,
            "rough",
            {
                "sampled_scan": True,
                "scan_incomplete": True,
                "no_motion_lines_found": True,
            },
            None,
            "provisional",
            {
                "missing_grbl_settings": True,
                "sampled_scan": True,
                "scan_incomplete": True,
                "no_motion_lines_found": True,
            },
            empty_snapshot,
            autolevel_snapshot,
        )

    motion_settings = _capture_motion_settings_snapshot(app)
    rapid_rates, rate_source = _resolve_quick_rate_tuple(app, motion_settings)
    default_feed = max(50.0, min(rapid_rates))

    units_scale = 1.0
    distance_mode = "absolute"
    feed_mode = "units_per_minute"
    motion_mode = 1
    feed_mm_min = float(default_feed)

    x = 0.0
    y = 0.0
    z = 0.0
    min_x = math.inf
    max_x = -math.inf
    min_y = math.inf
    max_y = -math.inf
    min_z = math.inf
    max_z = -math.inf
    have_bounds = False
    sample_exec_count = 0
    sample_motion_count = 0
    sample_motion_distance_mm = 0.0
    sample_rapid_distance_mm = 0.0
    sample_motion_time_min = 0.0
    sample_rapid_time_min = 0.0
    has_arc_move = False

    for raw_line in sampled_lines:
        line = str(raw_line or "").strip().upper()
        if not line:
            continue
        sample_exec_count += 1
        if _G20_PAT.search(line):
            units_scale = 25.4
        elif _G21_PAT.search(line):
            units_scale = 1.0
        if _G90_PAT.search(line):
            distance_mode = "absolute"
        elif _G91_PAT.search(line):
            distance_mode = "relative"
        if _G0_PAT.search(line):
            motion_mode = 0
        elif _G1_PAT.search(line):
            motion_mode = 1
        elif _G2_PAT.search(line):
            motion_mode = 2
            has_arc_move = True
        elif _G3_PAT.search(line):
            motion_mode = 3
            has_arc_move = True

        words: dict[str, float] = {}
        for word, value in _WORD_PAT.findall(line):
            val = _safe_float(value)
            if val is None:
                continue
            words[str(word).upper()] = float(val)
        if "F" in words and words["F"] > 0.0:
            feed_mm_min = max(1.0, float(words["F"]) * units_scale)

        prev_x, prev_y, prev_z = x, y, z
        moved = False
        for axis_name in _AXIS_WORDS:
            if axis_name not in words:
                continue
            moved = True
            val_mm = float(words[axis_name]) * units_scale
            if distance_mode == "relative":
                if axis_name == "X":
                    x += val_mm
                elif axis_name == "Y":
                    y += val_mm
                else:
                    z += val_mm
            else:
                if axis_name == "X":
                    x = val_mm
                elif axis_name == "Y":
                    y = val_mm
                else:
                    z = val_mm
        if not moved:
            continue

        have_bounds = True
        min_x = min(min_x, prev_x, x)
        max_x = max(max_x, prev_x, x)
        min_y = min(min_y, prev_y, y)
        max_y = max(max_y, prev_y, y)
        min_z = min(min_z, prev_z, z)
        max_z = max(max_z, prev_z, z)

        dx = x - prev_x
        dy = y - prev_y
        dz = z - prev_z
        dist_mm = math.sqrt((dx * dx) + (dy * dy) + (dz * dz))
        if dist_mm <= 0.0:
            continue
        sample_motion_count += 1
        if motion_mode == 0:
            axis_rates = []
            if abs(dx) > 1e-9:
                axis_rates.append(float(rapid_rates[0]))
            if abs(dy) > 1e-9:
                axis_rates.append(float(rapid_rates[1]))
            if abs(dz) > 1e-9:
                axis_rates.append(float(rapid_rates[2]))
            rapid_rate = min(axis_rates) if axis_rates else float(min(rapid_rates))
            rapid_rate = max(1.0, rapid_rate)
            sample_rapid_distance_mm += dist_mm
            sample_rapid_time_min += dist_mm / rapid_rate
        else:
            effective_feed = max(1.0, float(feed_mm_min))
            sample_motion_distance_mm += dist_mm
            sample_motion_time_min += dist_mm / effective_feed

    bounds_box: dict[str, float] | None = None
    if have_bounds:
        bounds_box = {
            "min_x": float(min_x),
            "max_x": float(max_x),
            "min_y": float(min_y),
            "max_y": float(max_y),
            "min_z": float(min_z),
            "max_z": float(max_z),
            "width": float(max(0.0, max_x - min_x)),
            "height": float(max(0.0, max_y - min_y)),
        }
    bounds_confidence = (
        "confident"
        if (cleaned_lines_known and have_bounds and not has_arc_move)
        else "rough"
    )
    dimensions_confidence_reasons = {
        "sampled_scan": not bool(cleaned_lines_known),
        "scan_incomplete": not bool(cleaned_lines_known),
        "no_motion_lines_found": not bool(have_bounds),
        "arc_endpoint_bounds": bool(has_arc_move),
    }

    sample_scale = 1.0
    min_exec = max(
        1,
        int(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MIN_EXECUTABLE_LINES", 2000) or 2000),
    )
    min_motion = max(
        1, int(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MIN_MOTION_LINES", 500) or 500)
    )
    max_scale = max(
        1.0, float(getattr(deps, "GCODE_ESTIMATE_SAMPLE_MAX_SCALE", 64.0) or 64.0)
    )
    if sample_motion_count >= min_motion and motion_total_estimate > 0:
        sample_scale = float(motion_total_estimate) / float(max(1, sample_motion_count))
    elif sample_exec_count >= min_exec and executable_total_estimate > 0:
        sample_scale = float(executable_total_estimate) / float(
            max(1, sample_exec_count)
        )
        sample_scale = min(sample_scale, max_scale * 0.5)
    elif sample_exec_count >= 250 and executable_total_estimate > 0:
        sample_scale = min(
            8.0, float(executable_total_estimate) / float(max(1, sample_exec_count))
        )
    sample_scale = max(1.0, min(float(sample_scale), max_scale))

    total_time_min = (sample_motion_time_min + sample_rapid_time_min) * sample_scale
    estimated_job_time_sec = (
        int(round(max(0.0, total_time_min) * 60.0)) if total_time_min > 0.0 else None
    )
    has_motion_settings = _has_complete_motion_settings(motion_settings)
    estimate_confidence = (
        "confident"
        if (
            has_motion_settings
            and bool(cleaned_lines_known)
            and sample_motion_count > 0
        )
        else "provisional"
    )
    estimate_confidence_reasons = {
        "missing_grbl_settings": not has_motion_settings,
        "sampled_scan": not bool(cleaned_lines_known),
        "scan_incomplete": not bool(cleaned_lines_known),
        "no_motion_lines_found": sample_motion_count <= 0,
    }

    now_ts = float(deps.time.time())
    modal_context = {
        "units": "inch" if units_scale > 1.0 else "mm",
        "distance_mode": str(distance_mode),
        "feed_mode": str(feed_mode),
        "sampled_lines": int(sample_exec_count),
        "sample_limit": int(len(sampled_lines)),
    }
    estimate_snapshot: dict[str, Any] = {
        "captured_at_ts": now_ts,
        "capture_stage": "quick_scan",
        "gcode_hash": str(source_hash or ""),
        "stats_mode": "quick_scan",
        "stats_sample_scale": float(sample_scale),
        "stats_sample_line_count": int(len(sampled_lines)),
        "stats_sample_total_lines": int(max(0, source_total_lines)),
        "stats_sample_executable_lines": int(max(0, sampled_executable_lines)),
        "stats_sample_motion_lines": int(max(0, sampled_motion_lines)),
        "stats_executable_total_lines": int(max(0, executable_total_estimate)),
        "stats_motion_total_lines": int(max(0, motion_total_estimate)),
        "sample_motion_count_quick": int(max(0, sample_motion_count)),
        "rate_source": str(rate_source),
        "estimate_confidence": str(estimate_confidence),
        "estimated_job_time_sec": int(estimated_job_time_sec)
        if estimated_job_time_sec is not None
        else None,
        "sample_motion_distance_mm": float(max(0.0, sample_motion_distance_mm)),
        "sample_rapid_distance_mm": float(max(0.0, sample_rapid_distance_mm)),
        "sample_motion_time_min": float(max(0.0, sample_motion_time_min)),
        "sample_rapid_time_min": float(max(0.0, sample_rapid_time_min)),
        "grbl_motion_settings": motion_settings,
        "rapid_rates_mm_min": (
            float(rapid_rates[0]),
            float(rapid_rates[1]),
            float(rapid_rates[2]),
        ),
        "modal_context": modal_context,
    }
    autolevel_prereq_snapshot = {
        "stage": "quick_scan",
        "prepared_at_ts": now_ts,
        "source_path": str(source_path or ""),
        "source_exists": bool(source_path and deps.os.path.isfile(source_path)),
        "source_hash": str(source_hash or ""),
        "source_total_lines": int(max(0, source_total_lines)),
        "bounds_ready": bool(bounds_box is not None),
        "bounds_confidence": str(bounds_confidence),
        "bounds": (
            (
                float(bounds_box["min_x"]),
                float(bounds_box["max_x"]),
                float(bounds_box["min_y"]),
                float(bounds_box["max_y"]),
                float(bounds_box["min_z"]),
                float(bounds_box["max_z"]),
            )
            if bounds_box is not None
            else None
        ),
        "xy_width_mm": float(bounds_box["width"]) if bounds_box is not None else 0.0,
        "xy_height_mm": float(bounds_box["height"]) if bounds_box is not None else 0.0,
        "z_min_mm": float(bounds_box["min_z"]) if bounds_box is not None else 0.0,
        "z_max_mm": float(bounds_box["max_z"]) if bounds_box is not None else 0.0,
        "probe_grid_applicable": bool(
            bounds_box is not None
            and float(bounds_box["width"]) > 0.0
            and float(bounds_box["height"]) > 0.0
        ),
    }
    return (
        bounds_box,
        bounds_confidence,
        dimensions_confidence_reasons,
        estimated_job_time_sec,
        estimate_confidence,
        estimate_confidence_reasons,
        estimate_snapshot,
        autolevel_prereq_snapshot,
    )


def _prepare_stream_source_fast(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _FastPrepareData:
    started_at = deps.time.perf_counter()
    success = False
    try:
        _check_load_token(app, token)
        sample_lines: list[str] = []
        sampled_head: list[str] = []
        sampled_periodic: list[str] = []
        sampled_tail_limit = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_TAIL_LINES", 0) or 0)
        )
        sampled_tail: deque[str] = deque(
            maxlen=sampled_tail_limit if sampled_tail_limit > 0 else None
        )
        sampled_head_limit = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_HEAD_LINES", 0) or 0)
        )
        sampled_interval = max(
            1, int(getattr(deps, "GCODE_PREP_SAMPLE_INTERVAL_LINES", 1) or 1)
        )
        sampled_max_lines = max(
            0, int(getattr(deps, "GCODE_PREP_SAMPLE_MAX_LINES", 0) or 0)
        )
        sample_limit = max(
            0, int(getattr(deps, "GCODE_STREAMING_SAMPLE_LINES", 0) or 0)
        )
        line_scan_limit = _resolve_fast_prepare_scan_limit_lines(
            app, deps, file_size=file_size
        )
        ssmeta_present, ssmeta = _read_ssmeta_header(
            path,
            max_lines=max(
                1,
                int(getattr(deps, "GCODE_SSMETA_HEADER_MAX_LINES", 500) or 500),
            ),
            max_bytes=max(
                1024,
                int(getattr(deps, "GCODE_SSMETA_HEADER_MAX_BYTES", 64 * 1024) or (64 * 1024)),
            ),
        )
        ssmeta_bounds = _ssmeta_bounds_box(ssmeta)
        ssmeta_units = _ssmeta_units_value(ssmeta)
        ssmeta_scan_reduced = False
        if ssmeta_bounds is not None and ssmeta_units is not None:
            reduced_limit = max(
                1_000,
                int(
                    getattr(deps, "GCODE_SSMETA_FAST_SCAN_MAX_LINES", 4_000) or 4_000
                ),
            )
            if line_scan_limit > reduced_limit:
                line_scan_limit = reduced_limit
                ssmeta_scan_reduced = True

        progress_label = f"Preparing {deps.os.path.basename(path)}"
        progress_last_ts = 0.0
        progress_last_pct = -1
        raw_lines_scanned = 0
        cleaned_lines_scanned = 0
        motion_lines_scanned = 0
        bytes_scanned = 0
        sampled_line_index = 0
        reached_eof = False
        sample_hasher = deps.hashlib.sha256()

        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            while raw_lines_scanned < line_scan_limit:
                if (raw_lines_scanned & 0x1FF) == 0:
                    _check_load_token(app, token)
                raw_line = handle.readline()
                if not raw_line:
                    reached_eof = True
                    break
                raw_lines_scanned += 1
                try:
                    bytes_scanned = int(handle.tell())
                except Exception:
                    bytes_scanned = max(0, bytes_scanned)
                if file_size and (raw_lines_scanned & 0x7F) == 0:
                    now = deps.time.perf_counter()
                    if now - progress_last_ts >= deps.GCODE_LOAD_PROGRESS_INTERVAL:
                        pct = min(100, int(bytes_scanned * 100 / file_size))
                        if pct != progress_last_pct:
                            _emit_progress(app, token, pct, 100, progress_label)
                            progress_last_pct = pct
                        progress_last_ts = now
                cleaned = cast(str, deps.clean_gcode_line(raw_line))
                if not cleaned:
                    continue
                cleaned_lines_scanned += 1
                if _is_motion_line(cleaned):
                    motion_lines_scanned += 1
                sampled_line_index += 1
                if sample_limit > 0 and len(sample_lines) < sample_limit:
                    sample_lines.append(cleaned)
                if sampled_head_limit > 0 and sampled_line_index <= sampled_head_limit:
                    sampled_head.append(cleaned)
                elif (
                    sampled_interval > 0
                    and (sampled_line_index % sampled_interval) == 0
                ):
                    sampled_periodic.append(cleaned)
                if sampled_tail_limit > 0:
                    sampled_tail.append(cleaned)
                if len(sampled_head) < 256:
                    sample_hasher.update(cleaned.encode("utf-8"))
                    sample_hasher.update(b"\n")

        if file_size:
            _emit_progress(
                app,
                token,
                min(progress_last_pct if progress_last_pct >= 0 else 0, 95),
                100,
                progress_label,
            )
        tail_lines = list(sampled_tail)
        if not reached_eof and sampled_tail_limit > 0:
            tail_lines = _sample_tail_lines_from_file(
                path,
                deps=deps,
                limit=sampled_tail_limit,
                file_size=file_size,
            )
        sampled_lines = _trim_sampled_lines(
            head_lines=sampled_head,
            periodic_lines=sampled_periodic,
            tail_lines=tail_lines,
            sampled_max_lines=sampled_max_lines,
        )
        _emit_progress(
            app, token, 95, 100, f"Computing bounds: {deps.os.path.basename(path)}"
        )
        sampled_executable_lines = int(len(sampled_lines))
        sampled_motion_lines = 0
        for sampled_line in sampled_lines:
            if _is_motion_line(sampled_line):
                sampled_motion_lines += 1
        cleaned_lines_known = bool(reached_eof)
        file_line_count_known = bool(reached_eof)
        file_line_count = int(raw_lines_scanned)
        if (
            not file_line_count_known
            and file_size
            and bytes_scanned > 0
            and raw_lines_scanned > 0
        ):
            try:
                file_line_count = max(
                    file_line_count,
                    int(
                        (float(raw_lines_scanned) / float(bytes_scanned))
                        * float(file_size)
                    ),
                )
            except Exception:
                file_line_count = int(raw_lines_scanned)
        cleaned_lines_estimate = int(cleaned_lines_scanned)
        executable_lines_estimate = int(cleaned_lines_scanned)
        motion_lines_estimate = int(motion_lines_scanned)
        if not cleaned_lines_known:
            if file_size and bytes_scanned > 0 and cleaned_lines_scanned > 0:
                density = float(cleaned_lines_scanned) / float(max(1, bytes_scanned))
                estimated = int(float(file_size) * density)
                cleaned_lines_estimate = max(cleaned_lines_estimate, estimated)
                executable_lines_estimate = max(executable_lines_estimate, estimated)
            else:
                cleaned_lines_estimate = max(cleaned_lines_estimate, raw_lines_scanned)
                executable_lines_estimate = max(
                    executable_lines_estimate, raw_lines_scanned
                )
            if cleaned_lines_scanned > 0 and motion_lines_scanned > 0:
                motion_ratio = float(motion_lines_scanned) / float(
                    max(1, cleaned_lines_scanned)
                )
                motion_lines_estimate = max(
                    motion_lines_estimate,
                    int(max(1.0, float(executable_lines_estimate) * motion_ratio)),
                )
        cleaned_lines_estimate = max(cleaned_lines_estimate, len(sample_lines))
        executable_lines_estimate = max(executable_lines_estimate, len(sample_lines))
        motion_lines_estimate = max(motion_lines_estimate, sampled_motion_lines)
        quick_scan_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        (
            bounds_box,
            bounds_confidence,
            dimensions_confidence_reasons,
            estimated_job_time_sec,
            estimate_confidence,
            estimate_confidence_reasons,
            estimate_inputs_snapshot,
            autolevel_prereq_snapshot,
        ) = _quick_scan_bounds_and_estimate(
            app,
            deps,
            sampled_lines=sampled_lines,
            sampled_executable_lines=sampled_executable_lines,
            sampled_motion_lines=sampled_motion_lines,
            executable_total_estimate=executable_lines_estimate,
            motion_total_estimate=motion_lines_estimate,
            cleaned_lines_known=cleaned_lines_known,
            source_path=path,
            source_hash="",
            source_total_lines=cleaned_lines_estimate,
        )
        dimensions_source = "scan"
        units_source = "scan"
        if ssmeta_units:
            modal_context = estimate_inputs_snapshot.get("modal_context", None)
            if isinstance(modal_context, dict):
                modal_context["units"] = ssmeta_units
            estimate_inputs_snapshot["units_source"] = "ssmeta"
            units_source = "ssmeta"
        if ssmeta_bounds is not None:
            bounds_box = dict(ssmeta_bounds)
            bounds_confidence = "confident"
            dimensions_confidence_reasons = {
                "sampled_scan": False,
                "scan_incomplete": False,
                "no_motion_lines_found": False,
                "ssmeta_override": True,
            }
            dimensions_source = "ssmeta"
            estimate_inputs_snapshot["dimensions_source"] = "ssmeta"
            autolevel_prereq_snapshot["bounds_ready"] = True
            autolevel_prereq_snapshot["bounds_confidence"] = "confident"
            autolevel_prereq_snapshot["bounds"] = (
                float(bounds_box["min_x"]),
                float(bounds_box["max_x"]),
                float(bounds_box["min_y"]),
                float(bounds_box["max_y"]),
                float(bounds_box["min_z"]),
                float(bounds_box["max_z"]),
            )
            autolevel_prereq_snapshot["xy_width_mm"] = float(bounds_box["width"])
            autolevel_prereq_snapshot["xy_height_mm"] = float(bounds_box["height"])
            autolevel_prereq_snapshot["z_min_mm"] = float(bounds_box["min_z"])
            autolevel_prereq_snapshot["z_max_mm"] = float(bounds_box["max_z"])
            autolevel_prereq_snapshot["probe_grid_applicable"] = bool(
                float(bounds_box["width"]) > 0.0 and float(bounds_box["height"]) > 0.0
            )
        estimate_inputs_snapshot["ssmeta_present"] = bool(ssmeta_present)
        estimate_inputs_snapshot["ssmeta_fields"] = sorted(ssmeta.keys())
        _emit_progress(
            app, token, 97, 100, f"Computing estimate: {deps.os.path.basename(path)}"
        )
        _emit_progress(
            app,
            token,
            99,
            100,
            f"Preparing auto-level data: {deps.os.path.basename(path)}",
        )
        if cleaned_lines_known:
            _emit_progress(app, token, 100, 100, progress_label)
        else:
            _emit_progress(app, token, 100, 100, f"{progress_label} (sampled)")
        quick_fingerprint = _quick_file_fingerprint(deps, path, file_size=file_size)
        lines_hash_quick = f"{quick_fingerprint}:{sample_hasher.hexdigest()[:16]}"
        estimate_inputs_snapshot["gcode_hash"] = lines_hash_quick
        autolevel_prereq_snapshot["source_hash"] = lines_hash_quick
        success = True
        result = _FastPrepareData(
            sample_lines=list(sample_lines),
            sampled_head_lines=sampled_head_limit,
            sampled_tail_lines=sampled_tail_limit,
            sampled_interval_lines=sampled_interval,
            sampled_max_lines=sampled_max_lines,
            sampled_line_count=int(len(sampled_lines)),
            file_size_bytes=int(file_size or 0),
            file_line_count=int(file_line_count),
            file_line_count_known=bool(file_line_count_known),
            cleaned_lines_estimate=cleaned_lines_estimate,
            cleaned_lines_known=cleaned_lines_known,
            executable_lines_estimate=executable_lines_estimate,
            motion_lines_estimate=motion_lines_estimate,
            sampled_executable_lines=sampled_executable_lines,
            sampled_motion_lines=sampled_motion_lines,
            raw_lines_scanned=raw_lines_scanned,
            bytes_scanned=bytes_scanned,
            lines_hash_quick=lines_hash_quick,
            quick_scan_ms=float(quick_scan_ms),
            bounds_box=bounds_box,
            bounds_confidence=str(bounds_confidence),
            dimensions_confidence_reasons=dict(dimensions_confidence_reasons),
            estimated_job_time_sec=estimated_job_time_sec,
            estimate_confidence=str(estimate_confidence),
            estimate_confidence_reasons=dict(estimate_confidence_reasons),
            estimate_inputs_snapshot=estimate_inputs_snapshot,
            autolevel_prereq_snapshot=autolevel_prereq_snapshot,
            ssmeta_present=bool(ssmeta_present),
            ssmeta=dict(ssmeta),
            dimensions_source=str(dimensions_source),
            units_source=str(units_source),
            ssmeta_scan_reduced=bool(ssmeta_scan_reduced),
        )
        # Drop scan-temporary buffers before returning to keep worker RSS steady.
        sampled_head.clear()
        sampled_periodic.clear()
        sampled_tail.clear()
        tail_lines.clear()
        sampled_lines.clear()
        return result
    finally:
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.prepare_fast", elapsed_ms, success=success)


def quick_scan_gcode(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
) -> _FastPrepareData:
    """Unified low-memory quick scan used by every load before stream-ready."""
    return _prepare_stream_source_fast(
        app,
        path,
        token,
        deps,
        file_size=file_size,
    )


def _stream_from_disk(
    app,
    path: str,
    token: int,
    deps,
    *,
    file_size: int | None,
    log_message: str | None = None,
) -> None:
    started_at = deps.time.perf_counter()
    success = False
    prepare_data: _FastPrepareData | None = None
    snapshot: _JobSnapshotData | None = None
    try:
        _check_load_token(app, token)
        if log_message:
            app.ui_q.put(("log", log_message))
        snapshot = _create_job_snapshot(
            app,
            path,
            token,
            deps,
            file_size=file_size,
        )
        _check_load_token(app, token)
        sample_only = True
        cache_profile = "disabled"
        setattr(app, "_gcode_full_line_cache_profile", cache_profile)
        setattr(app, "_gcode_full_line_cache_cap_lines", 0)
        setattr(app, "_gcode_full_line_cache_cap_hit", False)
        setattr(
            app,
            "_gcode_sample_line_cap",
            int(getattr(deps, "GCODE_STREAMING_SAMPLE_LINES", 0) or 0),
        )
        prepare_data = quick_scan_gcode(
            app,
            snapshot.path,
            token,
            deps,
            file_size=snapshot.byte_size,
        )
        _check_load_token(app, token)
        report = _validate_job_snapshot(
            app,
            deps=deps,
            token=token,
            display_path=path,
            snapshot=snapshot,
        )
        blocking_summary = _validation_blocking_summary(report)
        if blocking_summary:
            _remove_temp_path(deps, snapshot.path)
            snapshot = None
            app.ui_q.put(("gcode_load_error", token, path, blocking_summary))
            return
        app._gcode_quick_scan_ms = float(
            getattr(prepare_data, "quick_scan_ms", 0.0) or 0.0
        )
        app._gcode_post_popup_background_tasks = "none"
        app.ui_q.put(
            (
                "log",
                "[gcode] Immutable job snapshot and complete bounded validation finished before stream admission; no preparation work remains on the Run path.",
            )
        )
        cleaned_lines_estimate = int(snapshot.cleaned_line_count)
        prepare_data.cleaned_lines_estimate = cleaned_lines_estimate
        prepare_data.cleaned_lines_known = True
        prepare_data.executable_lines_estimate = cleaned_lines_estimate
        prepare_data.file_size_bytes = int(file_size or snapshot.byte_size)
        prepare_data.file_line_count = int(snapshot.raw_line_count)
        prepare_data.file_line_count_known = True
        prepare_data.estimate_inputs_snapshot["gcode_hash"] = snapshot.sha256
        prepare_data.autolevel_prereq_snapshot["source_hash"] = snapshot.sha256
        prepare_data.autolevel_prereq_snapshot["source_path"] = snapshot.path
        prepare_data.autolevel_prereq_snapshot["source_total_lines"] = cleaned_lines_estimate
        index_mode_requested = _resolve_index_mode_policy(
            deps,
            file_size=file_size,
            cleaned_lines_estimate=cleaned_lines_estimate,
        )
        lines_for_app = list(prepare_data.sample_lines)
        source = deps.FileGcodeSource(
            snapshot.path,
            offsets=None,
            already_clean=True,
            total_lines=cleaned_lines_estimate,
            line_count_known=True,
            snapshot_sha256=snapshot.sha256,
            snapshot_size_bytes=snapshot.byte_size,
            snapshot_mtime_ns=snapshot.mtime_ns,
            validation_complete=True,
            validated_line_count=cleaned_lines_estimate,
        )
        setattr(source, "_cleanup_path", snapshot.path)
        setattr(source, "_prepare_sampled_lines", None)
        setattr(
            source, "_prepare_sample_head_lines", int(prepare_data.sampled_head_lines)
        )
        setattr(
            source, "_prepare_sample_tail_lines", int(prepare_data.sampled_tail_lines)
        )
        setattr(
            source,
            "_prepare_sample_interval_lines",
            int(prepare_data.sampled_interval_lines),
        )
        setattr(
            source, "_prepare_sample_max_lines", int(prepare_data.sampled_max_lines)
        )
        setattr(
            source, "_prepare_sample_line_count", int(prepare_data.sampled_line_count)
        )
        setattr(
            source,
            "_prepare_file_size_bytes",
            int(getattr(prepare_data, "file_size_bytes", 0) or 0),
        )
        setattr(
            source,
            "_prepare_file_line_count",
            int(getattr(prepare_data, "file_line_count", 0) or 0),
        )
        setattr(
            source,
            "_prepare_file_line_count_known",
            bool(getattr(prepare_data, "file_line_count_known", False)),
        )
        setattr(
            source,
            "_prepare_executable_total_lines",
            int(prepare_data.executable_lines_estimate),
        )
        setattr(
            source,
            "_prepare_executable_total_lines_known",
            bool(getattr(prepare_data, "cleaned_lines_known", False)),
        )
        setattr(
            source,
            "_prepare_motion_total_lines",
            int(prepare_data.motion_lines_estimate),
        )
        setattr(
            source,
            "_prepare_motion_total_lines_known",
            bool(getattr(prepare_data, "cleaned_lines_known", False)),
        )
        setattr(
            source,
            "_prepare_sampled_executable_lines",
            int(prepare_data.sampled_executable_lines),
        )
        setattr(
            source,
            "_prepare_sampled_motion_lines",
            int(prepare_data.sampled_motion_lines),
        )
        setattr(source, "_offset_index_enabled", False)
        setattr(source, "_index_mode_requested", str(index_mode_requested))
        setattr(source, "_load_mode", "validated_snapshot_ready")
        setattr(source, "_quick_hash", snapshot.sha256)
        setattr(source, "_strict_validation_requested", True)
        setattr(
            source,
            "_quick_scan_ms",
            float(getattr(prepare_data, "quick_scan_ms", 0.0) or 0.0),
        )
        setattr(source, "_quick_bounds_box", prepare_data.bounds_box)
        setattr(
            source,
            "_quick_bounds_confidence",
            str(getattr(prepare_data, "bounds_confidence", "rough") or "rough"),
        )
        setattr(
            source,
            "_quick_dimensions_confidence",
            str(getattr(prepare_data, "bounds_confidence", "rough") or "rough"),
        )
        setattr(
            source,
            "_quick_dimensions_confidence_reasons",
            dict(getattr(prepare_data, "dimensions_confidence_reasons", {}) or {}),
        )
        setattr(
            source, "_quick_estimated_job_time_sec", prepare_data.estimated_job_time_sec
        )
        setattr(
            source,
            "_quick_estimate_confidence",
            str(
                getattr(prepare_data, "estimate_confidence", "provisional")
                or "provisional"
            ),
        )
        setattr(
            source,
            "_quick_estimate_confidence_reasons",
            dict(getattr(prepare_data, "estimate_confidence_reasons", {}) or {}),
        )
        setattr(
            source,
            "_quick_estimate_inputs_snapshot",
            dict(getattr(prepare_data, "estimate_inputs_snapshot", {}) or {}),
        )
        setattr(
            source,
            "_quick_autolevel_prereq_snapshot",
            dict(getattr(prepare_data, "autolevel_prereq_snapshot", {}) or {}),
        )
        setattr(source, "_quick_ssmeta_present", bool(prepare_data.ssmeta_present))
        setattr(source, "_quick_ssmeta", dict(getattr(prepare_data, "ssmeta", {}) or {}))
        setattr(
            source,
            "_quick_dimensions_source",
            str(getattr(prepare_data, "dimensions_source", "scan") or "scan"),
        )
        setattr(
            source,
            "_quick_units_source",
            str(getattr(prepare_data, "units_source", "scan") or "scan"),
        )
        setattr(
            source,
            "_quick_ssmeta_scan_reduced",
            bool(getattr(prepare_data, "ssmeta_scan_reduced", False)),
        )
        app.ui_q.put(
            (
                "log",
                "[gcode] Quick scan complete; file-backed stream-ready: "
                f"mode=validated_snapshot_ready, "
                f"index={index_mode_requested}, "
                f"line_count=known({cleaned_lines_estimate:,}), "
                f"sha256={snapshot.sha256[:16]}....",
            )
        )
        app.ui_q.put(
            (
                "log",
                f"[gcode] Quick Scan summary: bounds_conf={prepare_data.bounds_confidence}, "
                f"estimate_conf={prepare_data.estimate_confidence}, "
                f"estimate_sec={prepare_data.estimated_job_time_sec if prepare_data.estimated_job_time_sec is not None else 'n/a'}, "
                f"quick_scan_ms={float(getattr(prepare_data, 'quick_scan_ms', 0.0) or 0.0):.2f}, "
                f"ssmeta={'present' if prepare_data.ssmeta_present else 'not_found'}, "
                f"dimensions_source={prepare_data.dimensions_source}, "
                f"units_source={prepare_data.units_source}.",
            )
        )
        app.ui_q.put(
            (
                "gcode_loaded_stream",
                token,
                path,
                source,
                lines_for_app,
                snapshot.sha256,
                cleaned_lines_estimate,
                report,
                sample_only,
            )
        )
        snapshot = None
        record_task_timing(
            app,
            "gcode.load.time_to_stream_ready",
            max(0.0, (deps.time.perf_counter() - started_at) * 1000.0),
            success=True,
        )
        success = True
    except _GcodeLoadCancelled:
        _remove_temp_path(deps, getattr(snapshot, "path", None))
        return
    except _SystemCommandError as exc:
        _remove_temp_path(deps, getattr(snapshot, "path", None))
        app.ui_q.put(
            ("gcode_load_invalid_command", token, path, exc.line_no, exc.text)
        )
        return
    except _UnsendableLineError as exc:
        _remove_temp_path(deps, getattr(snapshot, "path", None))
        app.ui_q.put(
            (
                "gcode_load_error",
                token,
                path,
                f"G-code line {exc.line_no} cannot be streamed safely: {exc.reason}\n"
                f"Line: {exc.text[:160]}",
            )
        )
        return
    except Exception:
        _remove_temp_path(deps, getattr(snapshot, "path", None))
        raise
    finally:
        # Release scan objects as soon as possible; streaming source retains only
        # compact metadata and bounded sample lines passed through the UI event.
        prepare_data = None
        elapsed_ms = max(0.0, (deps.time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "gcode.load.stream_total", elapsed_ms, success=success)


def load_gcode_from_path(app, path: str, module):
    deps = module
    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
        deps.messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_LOADING_NEW_GCODE,
        )
        return None
    if not deps.os.path.isfile(path):
        deps.messagebox.showerror("Open G-code", "File not found.")
        return None
    app.settings["last_gcode_dir"] = deps.os.path.dirname(path)
    app._gcode_load_token += 1
    token = app._gcode_load_token
    if getattr(app, "_gcode_load_result_event", None) is None:
        try:
            app._gcode_load_result_event = deps.threading.Event()
        except Exception:
            app._gcode_load_result_event = None
    app._gcode_load_last_result_token = -1
    app._gcode_load_last_result_success = None
    app._gcode_load_last_result_error = ""
    app._gcode_load_last_result_path = ""
    app._gcode_loading = True
    try:
        app._gcode_load_started_at = deps.time.perf_counter()
    except Exception:
        app._gcode_load_started_at = None
    deps.disable_job_controls(app)
    app.gcode_stats_var.set("Preparing job...")
    app._gcode_status_last_text = "Preparing job..."
    app.status.config(text=f"Preparing job: {deps.os.path.basename(path)}")
    app._set_gcode_loading_indeterminate(f"reading {deps.os.path.basename(path)}")
    app.gview.clear()

    file_size = None
    ultra_large_mode = False
    ultra_large_threshold_bytes = _resolve_ultra_large_threshold_bytes(app, deps)
    try:
        file_size = deps.os.path.getsize(path)
        ultra_large_mode = _is_ultra_large_file_with_threshold(
            file_size=file_size,
            threshold_bytes=ultra_large_threshold_bytes,
        )
    except OSError:
        ultra_large_mode = False
    def worker():
        try:
            log_message = "[gcode] Using file-backed streaming load mode (bounded sample/sample retention)."
            if ultra_large_mode:
                ultra_threshold_text = _format_mb(ultra_large_threshold_bytes)
                size_text = _format_mb(file_size)
                app.ui_q.put(
                    (
                        "log",
                        f"[gcode] Ultra-large mode active ({size_text} >= {ultra_threshold_text}); "
                        "using sampled metadata and conservative file-backed cache choices; complete validation remains required.",
                    )
                )
            _stream_from_disk(
                app,
                path,
                token,
                deps,
                file_size=file_size,
                log_message=log_message,
            )
        except _GcodeLoadCancelled:
            return
        except Exception as exc:
            app.ui_q.put(("gcode_load_error", token, path, str(exc)))

    deps.threading.Thread(target=worker, daemon=True).start()
    return token
