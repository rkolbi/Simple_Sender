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
import time
from typing import Any, cast
import tkinter as tk
from tkinter import messagebox, TclError, ttk

from .status import (
    _parse_modal_units,
    _parse_report_units_setting,
    handle_status_event,
)
from . import streaming as _event_router_streaming
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.dialogs.error_dialogs_ui import show_grbl_code_popup
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error
from simple_sender.types import UiEvent

logger = logging.getLogger(__name__)
_GCODE_LOADED_STREAM_APPLY_BUDGET_MS = 15.0
_GCODE_LOADED_STREAM_APPLY_WARN_MS = 50.0


_JOG_LIMIT_ERROR_HINT = (
    "Jog blocked by travel limits (error:15). "
    "Move away from axis limits or verify homing and $130-$132."
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc, exc_info=exc)


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
            try:
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
            except TypeError:
                # Backward-compatible fallback for tests/mocks and older signatures.
                apply_fn(
                    path,
                    sample_lines,
                    lines_hash=lines_hash,
                    validated=True,
                    streaming_source=source,
                    total_lines=total_lines,
                    sample_only=sample_only,
                )
            source_consumed = True

        def _phase_finalize() -> None:
            app._gcode_loaded_stream_last_signature = signature
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
    raw_msg = str(msg)
    annotated = annotate_grbl_error(raw_msg)
    label = str(source).strip() if source else ""
    prefix = f"GRBL error ({label})" if label else "GRBL error"
    show_jog_limit_hint = _is_jog_source(label) and (_is_error_15(raw_msg) or _is_error_15(annotated))
    _clear_homing_watchdog(app, "Failed clearing homing watchdog ignore after manual error")
    if show_jog_limit_hint:
        _safe_status_update(app, _JOG_LIMIT_ERROR_HINT, context="Failed to update status for jog limit hint")
    else:
        _safe_status_update(app, f"{prefix}: {annotated}", context="Failed to update status for manual error")
    if show_jog_limit_hint and label.lower().startswith("joystick"):
        try:
            if hasattr(app, "joystick_event_status"):
                app.joystick_event_status.set(_JOG_LIMIT_ERROR_HINT)
        except (AttributeError, RuntimeError, TclError) as exc:
            _log_suppressed("Failed to update joystick status hint", exc)
    try:
        src_tag = f" ({label})" if label else ""
        app.streaming_controller.handle_log(f"[ERROR{src_tag}] {annotated}")
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed to log manual error to console", exc)
    try:
        show_grbl_code_popup(app, annotated)
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed showing GRBL error popup", exc)


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


def _handle_stream_error_event(app: Any, msg: Any, err_idx: Any) -> None:
    parsed_idx: int | None = None
    if err_idx is not None:
        try:
            parsed_idx = int(cast(int | str, err_idx))
        except (TypeError, ValueError):
            parsed_idx = None
    if parsed_idx is not None and parsed_idx >= 0:
        app._last_error_index = parsed_idx
    _safe_status_update(app, f"Stream error: {msg}", context="Failed to update stream error status")
    try:
        show_grbl_code_popup(app, cast(str | None, msg))
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
        case ("ui_post", func, args, kwargs):
            handle_ui_post(app, func, args, kwargs)
            return
        case ("macro_prompt", title, message, choices, cancel_label, result_q):
            handle_macro_prompt(app, title, message, choices, cancel_label, result_q)
            return
        case ("gcode_load_progress", token, done, total, label):
            handle_gcode_load_progress(app, token, done, total, label)
            return
        case ("streaming_validation_prompt", token, name, cleaned_lines, threshold, result_q):
            handle_streaming_validation_prompt(
                app,
                cast(int, token),
                cast(str, name),
                cast(int, cleaned_lines),
                cast(int, threshold),
                result_q,
            )
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
        case ("stream_error", msg, err_idx, _err_line, _name):
            _handle_stream_error_event(app, msg, err_idx)
            return
        case ("stream_pause_reason", reason):
            _handle_stream_pause_reason_event(app, reason)
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
        case _:
            _handle_unknown_event(app, evt)
            return


def handle_stream_state_event(app, evt):
    _event_router_streaming.job_controls_ready = job_controls_ready
    _event_router_streaming.set_run_resume_from = set_run_resume_from
    return _event_router_streaming.handle_stream_state_event(app, evt)


def handle_stream_interrupted(app, evt):
    return _event_router_streaming.handle_stream_interrupted(app, evt)


def handle_ui_call(app, func, args, kwargs, result_q, *, cancel_token=None):
    if cancel_token is not None and cancel_token.is_set():
        return
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


def handle_streaming_validation_prompt(
    app,
    token,
    name: str,
    cleaned_lines: int,
    threshold: int,
    result_q,
):
    if token != app._gcode_load_token:
        try:
            result_q.put_nowait(False)
        except queue.Full as exc:
            _log_suppressed("Streaming validation prompt result queue was full for stale token", exc)
        return
    prompt_key = (
        str(name or "").strip().lower(),
        max(0, int(cleaned_lines or 0)),
        max(0, int(threshold or 0)),
    )
    remember_cache = getattr(app, "_streaming_validation_prompt_cache", None)
    if not isinstance(remember_cache, dict):
        remember_cache = {}
        setattr(app, "_streaming_validation_prompt_cache", remember_cache)
    cached_choice = remember_cache.get(prompt_key)
    if isinstance(cached_choice, bool):
        logger.info(
            "[ui] streaming_validation_prompt cached answer: token=%s file=%s lines=%d threshold=%d allow=%s",
            token,
            str(name or ""),
            int(cleaned_lines),
            int(threshold),
            bool(cached_choice),
        )
        try:
            result_q.put_nowait(bool(cached_choice))
        except queue.Full as exc:
            _log_suppressed("Streaming validation prompt result queue was full for cached answer", exc)
        return

    logger.info(
        "[ui] streaming_validation_prompt scheduled: token=%s file=%s lines=%d threshold=%d",
        token,
        str(name or ""),
        int(cleaned_lines),
        int(threshold),
    )

    def _show_prompt_async() -> None:
        if token != app._gcode_load_token:
            try:
                result_q.put_nowait(False)
            except queue.Full as exc:
                _log_suppressed(
                    "Streaming validation prompt result queue was full after token changed before show",
                    exc,
                )
            return
        try:
            existing_dlg = getattr(app, "_streaming_validation_prompt_dialog", None)
            if existing_dlg is not None:
                try:
                    existing_dlg.destroy()
                except Exception as exc:
                    _log_suppressed("Failed closing previous streaming validation prompt dialog", exc)
            dlg = tk.Toplevel(app)
            app._streaming_validation_prompt_dialog = dlg
            dlg.title("Validate large file?")
            dlg.transient(app)
            dlg.resizable(False, False)
            frame = ttk.Frame(dlg, padding=12)
            frame.pack(fill="both", expand=True)
            msg = (
                f"Validate G-code for '{name}'?\n\n"
                f"Detected {cleaned_lines:,} non-empty lines (prompt at {threshold:,}).\n"
                "Validation adds another full scan and can take a while on huge files."
            )
            lbl = ttk.Label(frame, text=msg, wraplength=460, justify="left")
            lbl.pack(fill="x", pady=(0, 10))
            remember_var = tk.BooleanVar(master=dlg, value=False)
            remember_cb = ttk.Checkbutton(
                frame,
                text="Remember my choice for this file/size condition",
                variable=remember_var,
            )
            remember_cb.pack(anchor="w", pady=(0, 10))
            btn_row = ttk.Frame(frame)
            btn_row.pack(fill="x")

            def _answer(allow: bool) -> None:
                remember_choice = bool(remember_var.get())
                if remember_choice:
                    remember_cache[prompt_key] = bool(allow)
                try:
                    result_q.put_nowait(bool(allow))
                except queue.Full as exc:
                    _log_suppressed("Streaming validation prompt result queue was full on answer", exc)
                logger.info(
                    "[ui] streaming_validation_prompt answered: token=%s file=%s allow=%s remember=%s",
                    token,
                    str(name or ""),
                    bool(allow),
                    remember_choice,
                )
                try:
                    if getattr(app, "_streaming_validation_prompt_dialog", None) is dlg:
                        app._streaming_validation_prompt_dialog = None
                    dlg.destroy()
                except Exception as exc:
                    _log_suppressed("Failed closing streaming validation prompt dialog", exc)

            ttk.Button(btn_row, text="Validate", command=lambda: _answer(True)).pack(side="left", padx=(0, 6))
            ttk.Button(btn_row, text="Skip", command=lambda: _answer(False)).pack(side="left")
            dlg.protocol("WM_DELETE_WINDOW", lambda: _answer(False))
            logger.info(
                "[ui] streaming_validation_prompt shown: token=%s file=%s lines=%d threshold=%d",
                token,
                str(name or ""),
                int(cleaned_lines),
                int(threshold),
            )
        except Exception as exc:
            _log_suppressed("Failed to show asynchronous streaming validation prompt", exc)
            logger.info(
                "[ui] streaming_validation_prompt fallback answer: token=%s file=%s allow=False reason=show_failed",
                token,
                str(name or ""),
            )
            try:
                result_q.put_nowait(False)
            except queue.Full as queue_exc:
                _log_suppressed("Streaming validation prompt result queue was full on fallback answer", queue_exc)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            after_fn(0, _show_prompt_async)
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling asynchronous streaming validation prompt", exc)
    _show_prompt_async()


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
    app._apply_loaded_gcode(path, lines, lines_hash=lines_hash, validated=validated)
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
        _cleanup_streaming_source(source, context="Idempotent gcode_loaded_stream skip")
        return
    generation = int(getattr(app, "_gcode_loaded_stream_apply_generation", 0) or 0) + 1
    app._gcode_loaded_stream_apply_generation = generation
    pending = getattr(app, "_gcode_loaded_stream_pending", None)
    if pending and isinstance(pending, tuple) and len(pending) >= 4:
        stale_source = pending[3]
        if stale_source is not source:
            logger.info(
                "[ui] gcode_loaded_stream coalesced: job=%s hash=%s reason=pending_apply_replaced",
                signature[0] or "<none>",
                signature[1] or "<none>",
            )
            _cleanup_streaming_source(stale_source, context="Coalesced gcode_loaded_stream apply")
    app._gcode_loaded_stream_pending = (
        generation,
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
    app.gcode_stats_var.set("No file loaded")
    msg = f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit."
    if first_idx is not None and first_len is not None:
        msg += f"\nFirst at line {first_idx + 1} ({first_len} bytes including newline)."
    if total_lines is not None or cleaned_lines is not None:
        orig = f"{total_lines}" if total_lines is not None else "?"
        cleaned = f"{cleaned_lines}" if cleaned_lines is not None else "?"
        msg += f"\nFile lines: {orig} (non-empty: {cleaned})."
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
    app.gcode_stats_var.set("No file loaded")
    text = (line_text or "").strip() or "$"
    msg = "GRBL system commands ($...) are not allowed inside G-code jobs."
    if line_no is not None:
        msg += f"\nFirst at line {line_no}: {text}"
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def handle_gcode_load_error(app, token, _path, err):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("No file loaded")
    messagebox.showerror("Open G-code", f"Failed to read file:\n{err}")
    app.status.config(text="G-code load failed")


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


