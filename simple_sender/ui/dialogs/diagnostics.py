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

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
import zipfile
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Any, cast

from simple_sender import __version__ as SIMPLE_SENDER_PACKAGE_VERSION
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.kasa_actions import format_kasa_status_line, kasa_status_snapshot
from simple_sender.ui.macro_files import discover_macro_assets
from simple_sender.ui.pi_profile import PI_PROFILE_STATUS_POLL_INTERVAL
from simple_sender.ui.stream_completion import deferred_completion_wait_snapshot
from simple_sender.ui.tk_vars import safe_set_var_attr
from simple_sender.utils.constants import (
    GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT,
    GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER,
)
from simple_sender.utils.logging_config import get_log_dir
from simple_sender.utils.task_timing import record_task_timing
from .popup_utils import center_window
from .diagnostics_bundle import (
    collect_diagnostics_bundle_payload as _collect_diagnostics_bundle_payload_impl,
    write_diagnostics_bundle_archive as _write_diagnostics_bundle_archive_impl,
)
from .diagnostics_bundle_exporter import (
    build_recent_serial_window_from_log as _build_recent_serial_window_from_log_impl,
    collect_streaming_bundle_artifacts as _collect_streaming_bundle_artifacts_impl,
    write_bounded_log_tail_chunked as _write_bounded_log_tail_chunked_impl,
)
from .diagnostics_runtime_reporting import (
    format_runtime_metrics as _format_runtime_metrics_impl,
)
from .diagnostics_runtime_metrics import (
    build_runtime_metrics as _build_runtime_metrics_impl,
)
from .diagnostics_report_text import (
    build_performance_report_text as _build_performance_report_text_impl,
)
from .diagnostics_performance_actions import (
    apply_performance_test_preset as _apply_performance_test_preset_impl,
    save_performance_report_to_logs as _save_performance_report_to_logs_impl,
)
from .diagnostics_session_text import (
    build_session_diagnostics_lines as _build_session_diagnostics_lines_impl,
)
from .diagnostics_runtime_display import (
    open_runtime_telemetry as _open_runtime_telemetry_impl,
)
from .diagnostics_checklists import (
    open_release_checklist as _open_release_checklist_impl,
    open_run_checklist as _open_run_checklist_impl,
)
from .diagnostics_report_export import (
    export_session_diagnostics as _export_session_diagnostics_impl,
)
from .diagnostics_bundle_export import (
    export_diagnostics_bundle as _export_diagnostics_bundle_impl,
)
from .diagnostics_metadata import (
    bounded_ssmeta as _bounded_ssmeta_impl,
    build_system_info_text as _build_system_info_text_impl,
    collect_build_info as _collect_build_info_impl,
    effective_line_cache_cap_lines as _effective_line_cache_cap_lines_impl,
    headless_live_state_line_estimate as _headless_live_state_line_estimate_impl,
    json_dump as _json_dump_impl,
    pi_profile_enabled as _pi_profile_enabled_impl,
    resolved_settings_path as _resolved_settings_path_impl,
)
from .diagnostics_preflight import (
    evaluate_run_preflight as _evaluate_run_preflight_impl,
    format_validation_summary as _format_validation_summary_impl,
    get_bounds as _get_bounds_impl,
    get_travel_limits as _get_travel_limits_impl,
    run_preflight_check as _run_preflight_check_impl,
)

CHECKLIST_ITEMS = [
    "Connect/disconnect: port list refreshes, status shows connected, $G and $$ populate settings.",
    "Units: modal units match controller; $13 reporting indicator updates; unit toggle locked while streaming.",
    "Load G-code: file name, size, estimates, and bounds render in the correct units.",
    "Streaming: start/pause/resume/stop behaves correctly; buffer fill and progress update smoothly.",
    "Completion: popup shows run stats; progress bar resets after acknowledgment.",
    "Overrides: feed/spindle sliders send real-time commands and update the UI.",
    "Jogging: on-screen jog works; jog cancel halts motion; joystick hold stops on release.",
    "Safety: joystick safety hold gates actions; blocked actions emit status/log text.",
    "Alarms: alarm/lock messages display; unlock and recovery actions behave as expected.",
]
RUN_CHECKLIST_ITEMS = [
    "Confirm emergency stop and limit switches are functional.",
    "Home the machine and verify travel direction/limits.",
    "Set WCS zero and confirm units (G20/G21) match expectations.",
    "Verify tool, clamp clearance, and safe Z height.",
    "Dry-run in air if the job is new or the setup changed.",
]
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
RUNTIME_TELEMETRY_REFRESH_MS = 1000
PERF_TEST_STATUS_POLL_INTERVAL = max(1.0, float(PI_PROFILE_STATUS_POLL_INTERVAL))
DIAG_BUNDLE_LOG_MAX_FILES = 12
DIAG_BUNDLE_LOG_TAIL_MAX_BYTES = 512_000
DIAG_BUNDLE_IO_CHUNK_BYTES = 64 * 1024
DIAGNOSTICS_SCHEMA_REV = "2026-03-07-telemetry-r2"


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _set_var_value(app: Any, attr_name: str, value: Any) -> None:
    safe_set_var_attr(app, attr_name, value, log_suppressed=_log_suppressed)


def _json_dump(obj: Any) -> str:
    return cast(str, _json_dump_impl(obj, log_suppressed=_log_suppressed))


def _collect_build_info(app: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _collect_build_info_impl(
            app,
            diagnostics_schema_rev=DIAGNOSTICS_SCHEMA_REV,
            package_version=str(SIMPLE_SENDER_PACKAGE_VERSION or "").strip(),
            log_suppressed=_log_suppressed,
        ),
    )


def _build_system_info_text(app: Any, *, build_info: dict[str, Any] | None = None) -> str:
    return cast(
        str,
        _build_system_info_text_impl(
            app,
            collect_build_info=_collect_build_info,
            build_info=build_info,
        ),
    )


def _resolved_settings_path(app: Any) -> Path | None:
    return cast(Path | None, _resolved_settings_path_impl(app))


def _pi_profile_enabled(app: Any) -> bool:
    return cast(bool, _pi_profile_enabled_impl(app))


def _effective_line_cache_cap_lines(app: Any) -> tuple[int, str]:
    return cast(
        tuple[int, str],
        _effective_line_cache_cap_lines_impl(
            app,
            default_cap=int(GCODE_FULL_LINE_CACHE_MAX_LINES_DEFAULT),
            low_power_cap=int(GCODE_FULL_LINE_CACHE_MAX_LINES_LOW_POWER),
            pi_profile_enabled=_pi_profile_enabled,
        ),
    )


def _headless_live_state_line_estimate(gview: Any) -> int:
    return cast(int, _headless_live_state_line_estimate_impl(gview))


def _bounded_ssmeta(ssmeta: Any) -> dict[str, str]:
    return cast(dict[str, str], _bounded_ssmeta_impl(ssmeta))


def _format_validation_summary(report: Any) -> list[str]:
    return cast(list[str], _format_validation_summary_impl(report))


def _get_bounds(app: Any):
    return _get_bounds_impl(app)


def _get_travel_limits(app: Any) -> dict[str, float]:
    return cast(dict[str, float], _get_travel_limits_impl(app))


def _runtime_metrics(app: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _build_runtime_metrics_impl(
            app,
            collect_build_info=_collect_build_info,
            kasa_status_snapshot=kasa_status_snapshot,
            format_kasa_status_line=format_kasa_status_line,
            log_suppressed=_log_suppressed,
            headless_live_state_line_estimate=_headless_live_state_line_estimate,
            effective_line_cache_cap_lines=_effective_line_cache_cap_lines,
            deferred_completion_wait_snapshot=deferred_completion_wait_snapshot,
            bounded_ssmeta=_bounded_ssmeta,
        ),
    )


def _format_runtime_metrics(
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int = 20,
) -> list[str]:
    return cast(
        list[str],
        _format_runtime_metrics_impl(
            metrics,
            include_samples=include_samples,
            sample_limit=sample_limit,
        ),
    )


def open_runtime_telemetry(app) -> None:
    _open_runtime_telemetry_impl(
        app,
        runtime_metrics=_runtime_metrics,
        format_runtime_metrics=_format_runtime_metrics,
        log_suppressed=_log_suppressed,
        record_task_timing=record_task_timing,
        center_window=center_window,
        refresh_ms=RUNTIME_TELEMETRY_REFRESH_MS,
    )


def open_release_checklist(app: Any) -> None:
    _open_release_checklist_impl(
        app,
        fallback_items=CHECKLIST_ITEMS,
        log_suppressed=_log_suppressed,
    )


def open_run_checklist(app: Any) -> None:
    _open_run_checklist_impl(
        app,
        fallback_items=RUN_CHECKLIST_ITEMS,
        log_suppressed=_log_suppressed,
    )


def evaluate_run_preflight(app: Any) -> tuple[list[str], list[str]]:
    return cast(
        tuple[list[str], list[str]],
        _evaluate_run_preflight_impl(
            app,
            get_bounds=_get_bounds,
            get_travel_limits=_get_travel_limits,
        ),
    )


def run_preflight_check(app) -> None:
    _run_preflight_check_impl(
        app,
        evaluate_run_preflight=evaluate_run_preflight,
        format_validation_summary=_format_validation_summary,
        showinfo=_messagebox_info,
        showwarning=_messagebox_warning,
    )


def _build_performance_report_text(app: Any) -> str:
    return cast(
        str,
        _build_performance_report_text_impl(
            app,
            runtime_metrics=_runtime_metrics,
            format_runtime_metrics=_format_runtime_metrics,
            log_suppressed=_log_suppressed,
        ),
    )


def _messagebox_info(title: str, message: str) -> None:
    messagebox.showinfo(title, message)


def _messagebox_warning(title: str, message: str) -> None:
    messagebox.showwarning(title, message)


def _messagebox_error(title: str, message: str) -> None:
    messagebox.showerror(title, message)


def apply_performance_test_preset(app) -> None:
    _apply_performance_test_preset_impl(
        app,
        set_var_value=_set_var_value,
        log_suppressed=_log_suppressed,
        showinfo=_messagebox_info,
        showerror=_messagebox_error,
        perf_test_status_poll_interval=PERF_TEST_STATUS_POLL_INTERVAL,
    )


def save_performance_report_to_logs(app) -> None:
    _save_performance_report_to_logs_impl(
        app,
        get_log_dir=get_log_dir,
        build_performance_report_text=_build_performance_report_text,
        log_suppressed=_log_suppressed,
        showinfo=_messagebox_info,
        showerror=_messagebox_error,
    )


def _build_session_diagnostics_lines(app: Any) -> list[str]:
    return cast(
        list[str],
        _build_session_diagnostics_lines_impl(
            app,
            format_kasa_status_line=format_kasa_status_line,
            log_suppressed=_log_suppressed,
            effective_line_cache_cap_lines=_effective_line_cache_cap_lines,
            headless_live_state_line_estimate=_headless_live_state_line_estimate,
            format_validation_summary=_format_validation_summary,
            runtime_metrics=_runtime_metrics,
            format_runtime_metrics=_format_runtime_metrics,
        ),
    )

def _write_bounded_log_tail_chunked(
    archive: zipfile.ZipFile,
    *,
    arcname: str,
    path: Path,
    max_bytes: int,
    chunk_bytes: int = DIAG_BUNDLE_IO_CHUNK_BYTES,
) -> None:
    _write_bounded_log_tail_chunked_impl(
        archive,
        arcname=arcname,
        path=path,
        max_bytes=max_bytes,
        chunk_bytes=chunk_bytes,
        log_suppressed=_log_suppressed,
    )


def _build_recent_serial_window_from_log(
    serial_log_path: Path,
    *,
    window_minutes: int = 10,
    max_lines: int = 8000,
    min_lines: int = 1200,
) -> str:
    return cast(
        str,
        _build_recent_serial_window_from_log_impl(
            serial_log_path,
            window_minutes=window_minutes,
            max_lines=max_lines,
            min_lines=min_lines,
            log_suppressed=_log_suppressed,
        ),
    )


def _collect_streaming_bundle_artifacts(
    runtime_metrics: dict[str, Any],
    log_files: list[Path],
) -> dict[str, str]:
    return cast(
        dict[str, str],
        _collect_streaming_bundle_artifacts_impl(
            runtime_metrics,
            log_files,
            json_dump=_json_dump,
            log_suppressed=_log_suppressed,
        ),
    )


def _collect_diagnostics_bundle_payload(app: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _collect_diagnostics_bundle_payload_impl(
            app,
            collect_build_info=_collect_build_info,
            build_session_diagnostics_lines=_build_session_diagnostics_lines,
            build_performance_report_text=_build_performance_report_text,
            runtime_metrics=_runtime_metrics,
            json_dump=_json_dump,
            build_system_info_text=_build_system_info_text,
            resolved_settings_path=_resolved_settings_path,
            discover_macro_assets=discover_macro_assets,
            get_log_dir=get_log_dir,
            collect_streaming_bundle_artifacts=_collect_streaming_bundle_artifacts,
            log_suppressed=_log_suppressed,
            bundle_log_max_files=DIAG_BUNDLE_LOG_MAX_FILES,
        ),
    )


def _write_diagnostics_bundle_archive(out_path: Path, payload: dict[str, Any]) -> None:
    _write_diagnostics_bundle_archive_impl(
        out_path,
        payload,
        json_dump=_json_dump,
        write_bounded_log_tail_chunked=_write_bounded_log_tail_chunked,
        log_suppressed=_log_suppressed,
        bundle_log_tail_max_bytes=DIAG_BUNDLE_LOG_TAIL_MAX_BYTES,
    )


def export_diagnostics_bundle(app) -> None:
    _export_diagnostics_bundle_impl(
        app,
        run_file_dialog=run_file_dialog,
        asksaveasfilename=filedialog.asksaveasfilename,
        collect_diagnostics_bundle_payload=_collect_diagnostics_bundle_payload,
        write_diagnostics_bundle_archive=_write_diagnostics_bundle_archive,
        log_suppressed=_log_suppressed,
        showinfo=_messagebox_info,
        showerror=_messagebox_error,
        thread_cls=threading.Thread,
    )


def export_session_diagnostics(app) -> None:
    _export_session_diagnostics_impl(
        app,
        run_file_dialog=run_file_dialog,
        asksaveasfilename=filedialog.asksaveasfilename,
        build_session_diagnostics_lines=_build_session_diagnostics_lines,
        log_suppressed=_log_suppressed,
        showinfo=_messagebox_info,
        showerror=_messagebox_error,
        thread_cls=threading.Thread,
    )


