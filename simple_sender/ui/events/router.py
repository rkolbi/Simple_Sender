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

import os
import queue
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import time
from typing import Any, cast
from tkinter import messagebox, TclError

from .status import (
    _parse_modal_units,
    _parse_report_units_setting,
    handle_status_event,
)
from . import streaming as _event_router_streaming
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.ui.job_setup_state import invalidate_job_setup_state
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.dialogs.error_dialogs_ui import show_grbl_code_popup
from simple_sender.utils.task_timing import record_task_timing
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error
from simple_sender.types import (
    AlarmEvent,
    ConnectionEvent,
    GcodeAckedEvent,
    GcodeSentEvent,
    ProgressBytesEvent,
    ProgressEvent,
    ReadyEvent,
    SettingsDumpDoneEvent,
    StatusEvent,
    StreamErrorEvent,
    StreamInterruptedEvent,
    StreamPauseReasonEvent,
    StreamStateEvent,
    UiEvent,
)

logger = logging.getLogger(__name__)
_GCODE_LOADED_STREAM_APPLY_BUDGET_MS = 15.0
_GCODE_LOADED_STREAM_APPLY_WARN_MS = 50.0
_MANUAL_ERROR_HANDLER_BUDGET_MS = 15.0
_MANUAL_ERROR_COALESCE_WINDOW_S = 0.75
_STREAM_ERROR_LINE_TEXT_MAX_CHARS = 240
_STREAM_ERROR_33_HINT = (
    "Likely arc precision/tolerance issue (error:33). "
    "Increase VCarve inch-post X/Y and I/J precision (1.4-1.5) or output arcs as segments."
)


_JOG_LIMIT_ERROR_HINT = (
    "Jog blocked by travel limits (error:15). "
    "Move away from axis limits or verify homing and $130-$132."
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc)


def _cleanup_streaming_source(source: Any, *, context: str) -> None:
    cleanup_path = getattr(source, "_cleanup_path", None) if source is not None else None
    if source is not None:
        try:
            source.close()
        except (OSError, RuntimeError, ValueError) as exc:
            _log_suppressed(f"{context}: failed closing streaming source", exc)
    if cleanup_path:
        try:
            os.remove(cleanup_path)
        except OSError as exc:
            _log_suppressed(f"{context}: failed removing streamed temp file", exc)


def _signal_gcode_load_result(
    app: Any,
    *,
    token: int,
    success: bool,
    path: str | None = None,
    error: str | None = None,
) -> None:
    app._gcode_load_last_result_token = int(token)
    app._gcode_load_last_result_success = bool(success)
    app._gcode_load_last_result_error = str(error or "")
    app._gcode_load_last_result_path = str(path or "")
    evt = getattr(app, "_gcode_load_result_event", None)
    if evt is not None and hasattr(evt, "set"):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed("Failed signaling G-code load result event", exc)


def _loaded_stream_signature(
    *,
    path: str,
    lines_hash: str | None,
    total_lines: int | None,
    sample_only: bool,
) -> tuple[str, str, int, bool]:
    safe_total = 0
    if total_lines is not None:
        try:
            safe_total = max(0, int(total_lines))
        except Exception:
            safe_total = 0
    return (
        str(path or ""),
        str(lines_hash or ""),
        safe_total,
        bool(sample_only),
    )


def _loaded_stream_is_noop(app: Any, signature: tuple[str, str, int, bool]) -> bool:
    if str(getattr(app, "_stream_state", "") or "").strip().lower() != "loaded":
        return False
    current = _loaded_stream_signature(
        path=str(getattr(app, "_last_gcode_path", "") or ""),
        lines_hash=str(getattr(app, "_gcode_hash", "") or ""),
        total_lines=getattr(app, "_gcode_total_lines", 0),
        sample_only=bool(getattr(app, "_gcode_streaming_mode", False)),
    )
    return current == signature


def _queue_loaded_stream_apply(
    app: Any,
    *,
    token: int,
    signature: tuple[str, str, int, bool],
    path: str,
    source: Any,
    sample_lines: list[str],
    lines_hash: str | None,
    total_lines: int | None,
    report: Any,
    sample_only: bool,
) -> None:
    generation = int(getattr(app, "_gcode_loaded_stream_apply_generation", 0) or 0) + 1
    app._gcode_loaded_stream_apply_generation = generation
    pending = getattr(app, "_gcode_loaded_stream_pending", None)
    if pending and isinstance(pending, tuple) and len(pending) >= 4:
        stale_source = pending[4]
        if stale_source is not source:
            logger.info(
                "[ui] gcode_loaded_stream coalesced: job=%s hash=%s reason=pending_apply_replaced",
                signature[0] or "<none>",
                signature[1] or "<none>",
            )
            _cleanup_streaming_source(stale_source, context="Coalesced gcode_loaded_stream apply")
    app._gcode_loaded_stream_pending = (
        generation,
        token,
        signature,
        path,
        source,
        sample_lines,
        lines_hash,
        total_lines,
        report,
        sample_only,
    )
    _schedule_loaded_stream_apply(app)


def _cancel_load_settling_clear_timer(app: Any) -> None:
    after_id = getattr(app, "_gcode_load_settling_after_id", None)
    if after_id is None:
        return
    cancel_fn = getattr(app, "after_cancel", None)
    if callable(cancel_fn):
        try:
            cancel_fn(after_id)
        except Exception as exc:
            _log_suppressed("Failed canceling load-settling clear timer", exc)
    app._gcode_load_settling_after_id = None


def _set_load_settling(app: Any, enabled: bool, *, generation: int | None = None) -> None:
    if enabled:
        _cancel_load_settling_clear_timer(app)
        app._gcode_load_settling = True
        app._gcode_load_settling_generation = int(generation or 0)
        try:
            app._gcode_load_settling_started_at = time.monotonic()
        except Exception:
            app._gcode_load_settling_started_at = 0.0
        return
    _cancel_load_settling_clear_timer(app)
    app._gcode_load_settling = False
    app._gcode_load_settling_generation = 0
    app._status_settling_last_state = ""
    app._status_settling_last_apply_ts = 0.0
    app._status_settling_drop_count = 0


def _schedule_load_settling_clear(app: Any, *, generation: int) -> None:
    _cancel_load_settling_clear_timer(app)
    try:
        delay_ms = int(getattr(app, "_gcode_load_settling_tail_ms", 1200))
    except Exception:
        delay_ms = 1200
    delay_ms = max(0, delay_ms)

    def _clear_when_idle() -> None:
        app._gcode_load_settling_after_id = None
        if int(getattr(app, "_gcode_load_settling_generation", 0) or 0) != int(generation):
            return
        if getattr(app, "_gcode_loaded_stream_apply_after_id", None) is not None:
            _schedule_load_settling_clear(app, generation=generation)
            return
        if getattr(app, "_gcode_loaded_stream_pending", None):
            _schedule_load_settling_clear(app, generation=generation)
            return
        started_at = float(getattr(app, "_gcode_load_settling_started_at", 0.0) or 0.0)
        duration_ms = 0.0
        if started_at > 0.0:
            try:
                duration_ms = max(0.0, (time.monotonic() - started_at) * 1000.0)
            except Exception:
                duration_ms = 0.0
        pending = 0
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None and hasattr(ui_q, "qsize"):
            try:
                pending = max(0, int(ui_q.qsize()))
            except Exception:
                pending = 0
        logger.info(
            "[ui] load_settling OFF gen=%d duration_ms=%.2f tab=%s pending=%d",
            int(generation),
            duration_ms,
            str(getattr(app, "_active_tab_label", "") or "unknown"),
            int(pending),
        )
        _set_load_settling(app, False)
        if bool(getattr(app, "_stream_loaded_force_apply", False)):
            try:
                app.ui_q.put(("stream_state", "loaded", int(getattr(app, "_gcode_total_lines", 0) or 0)))
            except Exception as exc:
                _log_suppressed("Failed queueing loaded stream-state reconciliation after load settling", exc)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._gcode_load_settling_after_id = after_fn(delay_ms, _clear_when_idle)
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling load-settling clear callback", exc)
    _clear_when_idle()


def _schedule_loaded_stream_apply(app: Any) -> None:
    if getattr(app, "_gcode_loaded_stream_apply_after_id", None) is not None:
        return

    def _run_pending() -> None:
        app._gcode_loaded_stream_apply_after_id = None
        payload = getattr(app, "_gcode_loaded_stream_pending", None)
        app._gcode_loaded_stream_pending = None
        if not payload:
            return
        (
            generation,
            token,
            signature,
            path,
            source,
            sample_lines,
            lines_hash,
            total_lines,
            report,
            sample_only,
        ) = payload
        _set_load_settling(app, True, generation=generation)
        started_at = time.perf_counter()
        slice_count = 0
        max_slice_ms = 0.0
        slowest_phase = "none"
        slowest_phase_ms = 0.0
        source_consumed = False

        def _finalize_metrics(*, aborted: bool, reason: str) -> None:
            total_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            app._gcode_loaded_stream_apply_last_metrics = {
                "job": signature[0],
                "hash": signature[1],
                "generation": int(generation),
                "slices": int(slice_count),
                "total_ms": float(total_ms),
                "max_slice_ms": float(max_slice_ms),
                "slowest_phase": str(slowest_phase),
                "slowest_phase_ms": float(slowest_phase_ms),
                "aborted": bool(aborted),
                "reason": str(reason),
            }
            logger.info(
                "[ui] gcode_loaded_stream apply metrics: job=%s hash=%s gen=%d slices=%d total=%.2fms "
                "max_slice=%.2fms slowest=%s(%.2fms) aborted=%s reason=%s",
                signature[0] or "<none>",
                signature[1] or "<none>",
                int(generation),
                int(slice_count),
                total_ms,
                max_slice_ms,
                slowest_phase,
                slowest_phase_ms,
                bool(aborted),
                reason,
            )

        def _phase_prepare() -> None:
            app._gcode_validation_report = report

        def _phase_apply() -> None:
            nonlocal source_consumed
            apply_fn = getattr(app, "_apply_loaded_gcode", None)
            if not callable(apply_fn):
                raise RuntimeError("Missing _apply_loaded_gcode handler")
            apply_fn(
                path,
                sample_lines,
                lines_hash=lines_hash,
                validated=True,
                streaming_source=source,
                total_lines=total_lines,
                sample_only=sample_only,
                defer_viewer_stage_apply=True,
            )
            source_consumed = True

        def _phase_finalize() -> None:
            app._gcode_loaded_stream_last_signature = signature
            _signal_gcode_load_result(app, token=token, success=True, path=path)
            perf_monitor = getattr(app, "_perf_monitor", None)
            if perf_monitor is not None:
                try:
                    perf_monitor.note_file_loaded()
                except Exception as exc:
                    _log_suppressed("Failed forwarding streamed-file milestone to performance monitor", exc)
            _schedule_load_settling_clear(app, generation=generation)

        phases: list[tuple[str, Any]] = [
            ("prepare", _phase_prepare),
            ("apply", _phase_apply),
            ("finalize", _phase_finalize),
        ]

        def _run_phase(phase_index: int) -> None:
            nonlocal slice_count, max_slice_ms, slowest_phase, slowest_phase_ms, source_consumed
            app._gcode_loaded_stream_apply_after_id = None
            current_generation = int(getattr(app, "_gcode_loaded_stream_apply_generation", 0) or 0)
            if int(generation) != current_generation:
                if not source_consumed:
                    _cleanup_streaming_source(source, context="Canceled gcode_loaded_stream apply")
                _finalize_metrics(aborted=True, reason="coalesced_by_newer_load")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            if phase_index >= len(phases):
                _finalize_metrics(aborted=False, reason="ok")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            phase_name, phase_fn = phases[phase_index]
            phase_started_at = time.perf_counter()
            try:
                phase_fn()
            except Exception as exc:
                _log_suppressed(f"Deferred gcode_loaded_stream {phase_name} phase failed", exc)
                if not source_consumed:
                    _cleanup_streaming_source(
                        source,
                        context=f"Deferred gcode_loaded_stream {phase_name} failure",
                    )
                _fail_gcode_load(
                    app,
                    token=token,
                    path=path,
                    err=str(exc),
                    dialog_prefix="Failed to load file",
                )
                _set_load_settling(app, False)
                _finalize_metrics(aborted=True, reason=f"phase_failed:{phase_name}")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            phase_elapsed_ms = max(0.0, (time.perf_counter() - phase_started_at) * 1000.0)
            slice_count += 1
            if phase_elapsed_ms > max_slice_ms:
                max_slice_ms = phase_elapsed_ms
            if phase_elapsed_ms > slowest_phase_ms:
                slowest_phase_ms = phase_elapsed_ms
                slowest_phase = str(phase_name)
            if phase_elapsed_ms > _GCODE_LOADED_STREAM_APPLY_BUDGET_MS:
                level_log = logger.warning if phase_elapsed_ms >= _GCODE_LOADED_STREAM_APPLY_WARN_MS else logger.info
                level_log(
                    "[ui] gcode_loaded_stream slice exceeded %.1fms budget: phase=%s %.2fms",
                    _GCODE_LOADED_STREAM_APPLY_BUDGET_MS,
                    str(phase_name),
                    phase_elapsed_ms,
                )
            after_fn = getattr(app, "after", None)
            if callable(after_fn):
                try:
                    app._gcode_loaded_stream_apply_after_id = after_fn(
                        0,
                        lambda idx=phase_index + 1: _run_phase(idx),
                    )
                    return
                except Exception as exc:
                    _log_suppressed("Failed scheduling next gcode_loaded_stream apply slice", exc)
            _run_phase(phase_index + 1)

        _run_phase(0)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._gcode_loaded_stream_apply_after_id = after_fn(0, _run_pending)
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling deferred gcode_loaded_stream apply", exc)
    _run_pending()


def _is_error_15(message: str | None) -> bool:
    if not message:
        return False
    return "error:15" in str(message).lower()


def _is_jog_source(source: str | None) -> bool:
    if not source:
        return False
    normalized = str(source).strip().lower()
    return normalized in {"joystick", "jog", "jog_hold", "jog_button"}


def _truncate_stream_error_line_text(text: str, *, max_chars: int = _STREAM_ERROR_LINE_TEXT_MAX_CHARS) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= int(max_chars):
        return cleaned
    if int(max_chars) <= 3:
        return cleaned[: max(0, int(max_chars))]
    return cleaned[: int(max_chars) - 3] + "..."


def _stream_error_hint(message: str, line_text: str) -> str:
    haystack = f"{message} {line_text}".lower()
    if "error:33" in haystack:
        return _STREAM_ERROR_33_HINT
    return ""


def _safe_status_update(app: Any, text: str, *, context: str) -> None:
    try:
        app.status.config(text=text)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed(context, exc)


def _clear_homing_watchdog(app: Any, context: str) -> None:
    if not getattr(app, "_homing_in_progress", False):
        return
    app._homing_in_progress = False
    app._homing_state_seen = False
    try:
        app.grbl.clear_watchdog_ignore("homing")
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed(context, exc)


def _handle_log_rx_event(app: Any, raw: str) -> None:
    try:
        if str(raw).lstrip().upper().startswith("GRBL"):
            invalidate_job_setup_state(app)
    except Exception as exc:
        _log_suppressed("Failed invalidating job setup on GRBL reset banner", exc)
    _parse_modal_units(app, raw)
    _parse_report_units_setting(app, raw)
    probe_controller = getattr(app, "probe_controller", None)
    if probe_controller is not None:
        probe_controller.handle_rx_line(raw)
    settings_controller = getattr(app, "settings_controller", None)
    if settings_controller is not None:
        should_route_settings = bool(getattr(settings_controller, "_settings_capture", False))
        if not should_route_settings:
            stripped = str(raw).lstrip()
            should_route_settings = stripped.startswith("$") and ("=" in stripped)
        if should_route_settings:
            settings_controller.handle_line(raw)
    app.streaming_controller.handle_log_rx(raw)


def _handle_manual_error_event(app: Any, msg: str, source: str | None) -> None:
    started = time.perf_counter()
    try:
        stop_predict = getattr(app, "_stop_manual_jog_prediction", None)
        if callable(stop_predict):
            stop_predict(reason="manual_error")
    except Exception as exc:
        _log_suppressed("Failed stopping jog DRO interpolation after manual error", exc)
    raw_msg = str(msg)
    annotated = annotate_grbl_error(raw_msg)
    label = str(source).strip() if source else ""
    if label.lower() == "macro":
        macro_executor = getattr(app, "macro_executor", None)
        notify_manual_error = getattr(macro_executor, "notify_manual_error", None)
        if callable(notify_manual_error):
            try:
                notify_manual_error(annotated)
            except Exception as exc:
                _log_suppressed("Failed notifying macro executor of manual error", exc)
    show_jog_limit_hint = _is_jog_source(label) and (_is_error_15(raw_msg) or _is_error_15(annotated))
    key = (label.lower(), str(annotated), bool(show_jog_limit_hint))
    now = time.monotonic()
    last_key = getattr(app, "_manual_error_last_key", None)
    last_ts = float(getattr(app, "_manual_error_last_ts", 0.0) or 0.0)
    if key == last_key and (now - last_ts) <= _MANUAL_ERROR_COALESCE_WINDOW_S:
        app._manual_error_repeat_count = int(
            getattr(app, "_manual_error_repeat_count", 0) or 0
        ) + 1
        app._manual_error_last_ts = now
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        record_task_timing(app, "ui.manual_error", elapsed_ms, success=True)
        logger.info(
            "[ui] manual_error coalesced: source=%s repeats=%d window_s=%.2f",
            key[0] or "manual",
            int(getattr(app, "_manual_error_repeat_count", 0) or 0),
            float(_MANUAL_ERROR_COALESCE_WINDOW_S),
        )
        return
    app._manual_error_last_key = key
    app._manual_error_last_ts = now
    app._manual_error_repeat_count = 0
    _clear_homing_watchdog(app, "Failed clearing homing watchdog ignore after manual error")
    prefix = f"GRBL error ({label})" if label else "GRBL error"
    status_text = (
        _JOG_LIMIT_ERROR_HINT
        if show_jog_limit_hint
        else f"{prefix}: {annotated}"
    )
    src_tag = f" ({label})" if label else ""
    log_text = f"[ERROR{src_tag}] {annotated}"
    app._manual_error_ui_payload = (
        status_text,
        bool(show_jog_limit_hint and label.lower().startswith("joystick")),
        log_text,
    )
    if getattr(app, "_manual_error_ui_after_id", None) is not None:
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        record_task_timing(app, "ui.manual_error", elapsed_ms, success=True)
        return

    def _apply_manual_error_ui() -> None:
        apply_started = time.perf_counter()
        app._manual_error_ui_after_id = None
        payload = getattr(app, "_manual_error_ui_payload", None)
        if not payload or len(payload) != 3:
            return
        status_value = str(payload[0] or "")
        set_joystick_hint = bool(payload[1])
        log_value = str(payload[2] or "")
        status_ms = 0.0
        joy_ms = 0.0
        log_ms = 0.0
        section_started = time.perf_counter()
        _safe_status_update(
            app,
            status_value,
            context="Failed to update status for manual error",
        )
        status_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        if set_joystick_hint:
            section_started = time.perf_counter()
            try:
                if hasattr(app, "joystick_event_status"):
                    app.joystick_event_status.set(_JOG_LIMIT_ERROR_HINT)
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to update joystick status hint", exc)
            joy_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        section_started = time.perf_counter()
        try:
            app.streaming_controller.handle_log(log_value)
        except (AttributeError, RuntimeError, TclError) as exc:
            _log_suppressed("Failed to log manual error to console", exc)
        log_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        total_ms = max(0.0, (time.perf_counter() - apply_started) * 1000.0)
        record_task_timing(app, "ui.manual_error", total_ms, success=True)
        logger.info(
            "[ui] manual_error timing: total=%.2fms status=%.2fms joystick=%.2fms log=%.2fms",
            total_ms,
            status_ms,
            joy_ms,
            log_ms,
        )
        if total_ms > _MANUAL_ERROR_HANDLER_BUDGET_MS:
            logger.warning(
                "[ui] manual_error exceeded %.1fms budget: total=%.2fms",
                _MANUAL_ERROR_HANDLER_BUDGET_MS,
                total_ms,
            )

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._manual_error_ui_after_id = after_fn(0, _apply_manual_error_ui)
        except Exception as exc:
            _log_suppressed("Failed scheduling manual_error deferred UI update", exc)
            _apply_manual_error_ui()
    else:
        _apply_manual_error_ui()
    elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
    record_task_timing(app, "ui.manual_error.enqueue", elapsed_ms, success=True)


def _handle_manual_queue_drop_event(app: Any, dropped: int, total: int) -> None:
    try:
        app._manual_queue_drop_total = max(0, int(total))
    except Exception:
        app._manual_queue_drop_total = max(0, int(dropped))
    _safe_status_update(
        app,
        f"Manual queue full: dropped {app._manual_queue_drop_total} command(s).",
        context="Failed to update manual queue drop status",
    )


def _handle_alarm_event(app: Any, message: str) -> None:
    msg = annotate_grbl_alarm(str(message))
    _clear_homing_watchdog(app, "Failed clearing homing watchdog ignore after alarm")
    app._set_alarm_lock(True, msg)
    app.macro_executor.notify_alarm(msg)
    app._apply_status_poll_profile()
    try:
        show_grbl_code_popup(app, msg)
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed showing GRBL alarm popup", exc)


def _handle_stream_error_event(
    app: Any,
    msg: Any,
    err_idx: Any,
    err_line: Any,
    gcode_name: Any,
) -> None:
    message = str(msg or "").strip() or "Unknown stream error"
    parsed_idx: int | None = None
    if err_idx is not None:
        try:
            parsed_idx = int(cast(int | str, err_idx))
        except (TypeError, ValueError):
            parsed_idx = None
    line_number: int | None = None
    if parsed_idx is not None and parsed_idx >= 0:
        app._last_error_index = parsed_idx
        line_number = parsed_idx + 1
    line_text = _truncate_stream_error_line_text(str(err_line or ""))
    file_name = str(gcode_name or "").strip()
    if not file_name:
        last_path = str(getattr(app, "_last_gcode_path", "") or "").strip()
        file_name = os.path.basename(last_path) if last_path else ""
    hint = _stream_error_hint(message, line_text)
    app._last_stream_error_message = message
    app._last_stream_error_file_name = file_name
    app._last_stream_error_line_index = int(parsed_idx) if parsed_idx is not None else -1
    app._last_stream_error_line_number = int(line_number) if line_number is not None else 0
    app._last_stream_error_line_text = line_text
    app._last_stream_error_hint = hint

    line_desc = ""
    if line_number is not None:
        if file_name:
            line_desc = f"{file_name} line {line_number}"
        else:
            line_desc = f"line {line_number}"
    status_msg = f"Stream error: {message}"
    if line_desc:
        status_msg = f"{status_msg} | {line_desc}"
    if line_text:
        status_msg = f"{status_msg} -> {line_text}"
    if hint:
        status_msg = f"{status_msg} | {hint}"
    _safe_status_update(
        app,
        status_msg,
        context="Failed to update stream error status",
    )
    try:
        if line_desc:
            app.streaming_controller.handle_log(
                f"[stream error] {line_desc}{' -> ' + line_text if line_text else ''}"
            )
        if hint:
            app.streaming_controller.handle_log(f"[stream error hint] {hint}")
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed routing stream error details to console log", exc)
    try:
        show_grbl_code_popup(app, status_msg)
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed showing stream error popup", exc)


def _handle_stream_pause_reason_event(app: Any, reason: Any) -> None:
    if not reason:
        return
    _safe_status_update(
        app,
        f"Paused ({reason})",
        context="Failed to update pause reason status",
    )


def _handle_unknown_event(app: Any, evt: Any) -> None:
    message = f"Unhandled UI event: {evt!r}"
    try:
        if hasattr(app, "_log_exception"):
            app._log_exception("Unhandled UI event", ValueError(message))
        else:
            _log_suppressed("Unhandled UI event (no app logger)", ValueError(message))
    except Exception as exc:
        _log_suppressed("Failed logging unhandled UI event", exc)
    try:
        if hasattr(app, "streaming_controller"):
            app.streaming_controller.handle_log(f"[ui] {message}")
    except Exception as exc:
        _log_suppressed("Failed writing unhandled UI event to console", exc)


def set_streaming_lock(app: Any, locked: bool, *, defer_toolbar_refresh: bool = False):
    locked = bool(locked)
    prior_locked = getattr(app, "_streaming_lock_state", None)
    if prior_locked is not None and bool(prior_locked) == locked:
        return
    app._streaming_lock_state = locked
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update connect button state", exc)
    try:
        app.btn_refresh.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update refresh button state", exc)
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update port combo state", exc)
    try:
        app.btn_unit_toggle.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update unit toggle state", exc)
    if not defer_toolbar_refresh and hasattr(app, "_refresh_toolbar_action_focus"):
        try:
            app._refresh_toolbar_action_focus()
        except (AttributeError, RuntimeError, TclError, TypeError, ValueError) as exc:
            _log_suppressed("Failed refreshing toolbar action focus after streaming lock change", exc)


def handle_event(app: Any, evt: UiEvent):
    match evt:
        case ConnectionEvent(connected=connected, port=port):
            handle_connection_event(app, bool(connected), cast(str | None, port))
            return
        case ReadyEvent(is_ready=is_ready):
            handle_ready_event(app, bool(is_ready))
            return
        case AlarmEvent(message=msg):
            _handle_alarm_event(app, cast(str, msg))
            return
        case StatusEvent(line=line):
            handle_status_event(app, cast(str, line))
            return
        case SettingsDumpDoneEvent():
            try:
                app.settings_controller.handle_line("ok")
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to process settings dump completion", exc)
            return
        case GcodeSentEvent(idx=idx, line=_line):
            app.streaming_controller.handle_gcode_sent(int(cast(int, idx)))
            return
        case GcodeAckedEvent(idx=idx):
            app.streaming_controller.handle_gcode_acked(int(cast(int, idx)))
            return
        case ProgressEvent(done=done, total=total):
            app.streaming_controller.handle_progress(
                int(cast(int, done)),
                int(cast(int, total)),
            )
            return
        case ProgressBytesEvent(acked_offset=acked_offset, file_size_bytes=file_size_bytes):
            app.streaming_controller.handle_progress_bytes(
                int(cast(int, acked_offset)),
                int(cast(int, file_size_bytes)),
            )
            return
        case StreamStateEvent():
            handle_stream_state_event(app, evt)
            return
        case StreamInterruptedEvent():
            handle_stream_interrupted(app, evt)
            return
        case StreamErrorEvent(
            message=msg,
            err_idx=err_idx,
            err_line=err_line,
            gcode_name=name,
        ):
            _handle_stream_error_event(app, msg, err_idx, err_line, name)
            return
        case StreamPauseReasonEvent(reason=reason):
            _handle_stream_pause_reason_event(app, reason)
            return
        case ("conn", connected, port):
            handle_connection_event(app, cast(bool, connected), cast(str | None, port))
            return
        case ("ui_call", func, args, kwargs, result_q):
            handle_ui_call(app, func, args, kwargs, result_q)
            return
        case ("ui_call", func, args, kwargs, result_q, cancel_token):
            handle_ui_call(
                app,
                func,
                args,
                kwargs,
                result_q,
                cancel_token=cancel_token,
            )
            return
        case ("ui_call", func, args, kwargs, result_q, cancel_token, start_q):
            handle_ui_call(
                app,
                func,
                args,
                kwargs,
                result_q,
                cancel_token=cancel_token,
                start_q=start_q,
            )
            return
        case ("ui_post", func, args, kwargs):
            handle_ui_post(app, func, args, kwargs)
            return
        case ("macro_prompt", title, message, choices, cancel_label, result_q):
            handle_macro_prompt(app, title, message, choices, cancel_label, result_q)
            return
        case ("gcode_load_progress", token, done, total, label):
            handle_gcode_load_progress(app, token, done, total, label)
            return
        case ("gcode_loaded", *_):
            handle_gcode_loaded(app, evt)
            return
        case ("gcode_loaded_stream", *_):
            handle_gcode_loaded_stream(app, evt)
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines, cleaned_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                cast(int | None, cleaned_lines),
            )
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                None,
            )
            return
        case ("gcode_load_invalid_command", token, path, line_no, line_text):
            handle_gcode_load_invalid_command(
                app,
                cast(int, token),
                cast(str, path),
                cast(int | None, line_no),
                cast(str | None, line_text),
            )
            return
        case ("gcode_load_error", token, path, err):
            handle_gcode_load_error(app, token, path, err)
            return
        case ("log", message):
            app.streaming_controller.handle_log(message)
            return
        case ("manual_queue_drop", dropped, total):
            _handle_manual_queue_drop_event(
                app,
                cast(int, dropped),
                cast(int, total),
            )
            return
        case ("log_tx", message):
            app.streaming_controller.handle_log_tx(message)
            try:
                if hasattr(app, "_handle_outgoing_gcode_line"):
                    app._handle_outgoing_gcode_line(cast(str, message), "manual")
            except Exception as exc:
                _log_suppressed("Failed processing outbound manual line for Kasa routing", exc)
            return
        case ("log_rx", raw):
            _handle_log_rx_event(app, cast(str, raw))
            return
        case ("settings_dump_done",):
            try:
                app.settings_controller.handle_line("ok")
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to process settings dump completion", exc)
            return
        case ("manual_error", msg, source):
            _handle_manual_error_event(
                app,
                cast(str, msg),
                cast(str | None, source),
            )
            return
        case ("ready", is_ready):
            handle_ready_event(app, is_ready)
            return
        case ("alarm", msg):
            _handle_alarm_event(app, cast(str, msg))
            return
        case ("status", line):
            handle_status_event(app, cast(str, line))
            return
        case ("buffer_fill", pct, used, window):
            app.streaming_controller.handle_buffer_fill(pct, used, window)
            return
        case ("throughput", bps):
            app.streaming_controller.handle_throughput(float(cast(float, bps)))
            return
        case ("stream_state", *_):
            handle_stream_state_event(app, evt)
            return
        case ("stream_interrupted", *_):
            handle_stream_interrupted(app, evt)
            return
        case ("stream_error", msg, err_idx, err_line, name):
            _handle_stream_error_event(app, msg, err_idx, err_line, name)
            return
        case ("stream_pause_reason", reason):
            _handle_stream_pause_reason_event(app, reason)
            return
        case ("stream_vacuum_directive", is_on):
            try:
                if hasattr(app, "_handle_stream_vacuum_directive"):
                    app._handle_stream_vacuum_directive(bool(cast(bool, is_on)))
            except Exception as exc:
                _log_suppressed("Failed handling streamed vacuum directive", exc)
            return
        case ("stream_tool_change", line_idx, tool_name):
            try:
                if hasattr(app, "_handle_stream_tool_change"):
                    app._handle_stream_tool_change(
                        cast(str, tool_name),
                        line_index=cast(int | None, line_idx),
                    )
            except Exception as exc:
                _log_suppressed("Failed handling streamed tool-change directive", exc)
            return
        case ("spindle_state", is_on, _idx):
            try:
                if hasattr(app, "_handle_stream_spindle_state"):
                    app._handle_stream_spindle_state(bool(cast(bool, is_on)))
            except Exception as exc:
                _log_suppressed("Failed processing streamed spindle-state event for Kasa routing", exc)
            return
        case ("gcode_sent", idx, _line):
            app.streaming_controller.handle_gcode_sent(idx)
            return
        case ("gcode_acked", idx):
            app.streaming_controller.handle_gcode_acked(idx)
            return
        case ("progress", done, total):
            app.streaming_controller.handle_progress(done, total)
            return
        case ("progress_bytes", acked_offset, file_size_bytes):
            app.streaming_controller.handle_progress_bytes(
                int(cast(int, acked_offset)),
                int(cast(int, file_size_bytes)),
            )
            return
        case _:
            _handle_unknown_event(app, evt)
            return


def handle_stream_state_event(app, evt):
    _event_router_streaming.job_controls_ready = job_controls_ready
    _event_router_streaming.set_run_resume_from = set_run_resume_from
    return _event_router_streaming.handle_stream_state_event(app, evt)


def handle_stream_interrupted(app, evt):
    return _event_router_streaming.handle_stream_interrupted(app, evt)


def handle_ui_call(app, func, args, kwargs, result_q, *, cancel_token=None, start_q=None):
    if cancel_token is not None and cancel_token.is_set():
        return
    if start_q is not None:
        try:
            start_q.put_nowait(True)
        except queue.Full as exc:
            _log_suppressed("UI call start queue full while reporting handoff", exc)
    try:
        value = func(*args, **kwargs)
    except Exception as exc:
        app._log_exception("UI action failed", exc)
        if cancel_token is not None and cancel_token.is_set():
            return
        try:
            result_q.put_nowait((False, exc))
        except queue.Full as queue_exc:
            _log_suppressed("UI call result queue full while reporting failure", queue_exc)
        return
    if cancel_token is not None and cancel_token.is_set():
        return
    try:
        result_q.put_nowait((True, value))
    except queue.Full as exc:
        _log_suppressed("UI call result queue full while reporting success", exc)


def handle_ui_post(app, func, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception as exc:
        app._log_exception("UI action failed", exc)


def handle_macro_prompt(app, title, message, choices, cancel_label, result_q):
    try:
        app._show_macro_prompt(title, message, choices, cancel_label, result_q)
    except (AttributeError, RuntimeError, TclError, TypeError) as exc:
        try:
            app.streaming_controller.log(f"[macro] Prompt failed: {exc}")
        except (AttributeError, RuntimeError, TclError) as log_exc:
            _log_suppressed("Failed to log macro prompt failure to UI console", log_exc)
        try:
            result_q.put_nowait(cancel_label)
        except queue.Full as exc:
            _log_suppressed("Macro prompt result queue was full while reporting failure", exc)


def handle_gcode_load_progress(app, token, done, total, label):
    if token != app._gcode_load_token:
        return
    if not getattr(app, "_gcode_loading", False):
        return
    try:
        app._set_gcode_loading_progress(done, total, label)
    except (AttributeError, RuntimeError, TclError, TypeError, ValueError) as exc:
        _log_suppressed("Failed to update G-code loading progress", exc)


def handle_gcode_loaded(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        return
    path = evt[2]
    lines = evt[3]
    lines_hash = evt[4] if len(evt) > 4 else None
    validated = bool(evt[5]) if len(evt) > 5 else False
    report = evt[6] if len(evt) > 6 else None
    app._gcode_validation_report = report
    try:
        app._apply_loaded_gcode(path, lines, lines_hash=lines_hash, validated=validated)
    except Exception as exc:
        _signal_gcode_load_result(app, token=token, success=False, path=path, error=str(exc))
        raise
    _signal_gcode_load_result(app, token=token, success=True, path=path)
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        try:
            perf_monitor.note_file_loaded()
        except Exception as exc:
            _log_suppressed("Failed forwarding file-loaded milestone to performance monitor", exc)


def handle_gcode_loaded_stream(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        source = evt[3] if len(evt) > 3 else None
        _cleanup_streaming_source(source, context="Stale gcode_loaded_stream token")
        return
    path = evt[2]
    source = evt[3]
    sample_lines = evt[4] if len(evt) > 4 else []
    lines_hash = evt[5] if len(evt) > 5 else None
    total_lines = evt[6] if len(evt) > 6 else None
    report = evt[7] if len(evt) > 7 else None
    sample_only = bool(evt[8]) if len(evt) > 8 else True
    signature = _loaded_stream_signature(
        path=path,
        lines_hash=lines_hash,
        total_lines=total_lines,
        sample_only=sample_only,
    )
    if _loaded_stream_is_noop(app, signature):
        logger.info(
            "[ui] gcode_loaded_stream idempotent skip: %s->loaded job=%s hash=%s reason=already_loaded_no_changes",
            str(getattr(app, "_stream_state", "") or "").strip().lower() or "none",
            signature[0] or "<none>",
            signature[1] or "<none>",
        )
        _signal_gcode_load_result(app, token=token, success=True, path=path)
        _cleanup_streaming_source(source, context="Idempotent gcode_loaded_stream skip")
        return
    _queue_loaded_stream_apply(
        app,
        token=token,
        signature=signature,
        path=path,
        source=source,
        sample_lines=sample_lines,
        lines_hash=lines_hash,
        total_lines=total_lines,
        report=report,
        sample_only=sample_only,
    )


def handle_gcode_load_invalid(
    app,
    token,
    _path,
    too_long: int,
    first_idx: int | None,
    first_len: int | None,
    total_lines: int | None = None,
    cleaned_lines: int | None = None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    msg = f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit."
    if first_idx is not None and first_len is not None:
        msg += f"\nFirst at line {first_idx + 1} ({first_len} bytes including newline)."
    if total_lines is not None or cleaned_lines is not None:
        orig = f"{total_lines}" if total_lines is not None else "?"
        cleaned = f"{cleaned_lines}" if cleaned_lines is not None else "?"
        msg += f"\nFile lines: {orig} (non-empty: {cleaned})."
    _signal_gcode_load_result(app, token=token, success=False, error=msg)
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def handle_gcode_load_invalid_command(
    app,
    token,
    _path,
    line_no: int | None,
    line_text: str | None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    text = (line_text or "").strip() or "$"
    msg = "GRBL system commands ($...) are not allowed inside G-code jobs."
    if line_no is not None:
        msg += f"\nFirst at line {line_no}: {text}"
    _signal_gcode_load_result(app, token=token, success=False, error=msg)
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def _fail_gcode_load(
    app,
    *,
    token,
    path: str | None,
    err,
    dialog_prefix: str,
) -> None:
    if token != app._gcode_load_token:
        return
    _signal_gcode_load_result(
        app,
        token=token,
        success=False,
        path=path,
        error=str(err),
    )
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    messagebox.showerror("Open G-code", f"{dialog_prefix}:\n{err}")
    app.status.config(text="G-code load failed")


def handle_gcode_load_error(app, token, path, err):
    _fail_gcode_load(
        app,
        token=token,
        path=path,
        err=err,
        dialog_prefix="Failed to read file",
    )


def _clear_autolevel_restore(app) -> None:
    restore = getattr(app, "_auto_level_restore", None)
    if not isinstance(restore, dict):
        return
    app._auto_level_restore = None
    leveled_path = restore.get("leveled_path")
    if restore.get("leveled_temp") and leveled_path:
        try:
            os.remove(leveled_path)
        except OSError as exc:
            _log_suppressed("Failed removing autolevel temp restore file", exc)
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None

