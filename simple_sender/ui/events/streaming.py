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
import time
import logging
import tkinter as tk
from typing import Callable

from simple_sender.constants.messages import StatusMessages
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.stream_completion import (
    begin_deferred_completion_wait,
    end_deferred_completion_wait,
    should_defer_done_until_idle,
)
from .stream_state_ui import (
    apply_stream_busy_state,
    restore_controls_after_stream,
    manual_controls_allowed,
)

logger = logging.getLogger(__name__)
_LOADED_RECONCILE_BUDGET_MS = 15.0
_LOADED_RECONCILE_WARN_MS = 50.0


def _log_stream_ui_issue(context: str, exc: BaseException) -> None:
    logger.debug("%s: %s", context, exc)


def _set_streaming_lock_safe(
    app,
    locked: bool,
    *,
    defer_toolbar_refresh: bool = False,
) -> None:
    set_streaming_lock = getattr(app, "_set_streaming_lock", None)
    if not callable(set_streaming_lock):
        return
    set_streaming_lock(bool(locked), defer_toolbar_refresh=defer_toolbar_refresh)


def _schedule_stream_ui_callback(
    app,
    *,
    callback_attr: str,
    callback: Callable[[], None],
    context: str,
) -> None:
    pending_after_id = getattr(app, callback_attr, None)
    if pending_after_id is not None:
        cancel_fn = getattr(app, "after_cancel", None)
        if callable(cancel_fn):
            try:
                cancel_fn(pending_after_id)
            except Exception as exc:
                _log_stream_ui_issue(
                    f"Failed canceling deferred stream UI callback: {callback_attr}",
                    exc,
                )
        setattr(app, callback_attr, None)

    def _run() -> None:
        setattr(app, callback_attr, None)
        try:
            callback()
        except Exception as exc:
            _log_stream_ui_issue(context, exc)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            setattr(app, callback_attr, after_fn(0, _run))
            return
        except Exception as exc:
            _log_stream_ui_issue(
                f"Failed scheduling deferred stream UI callback: {callback_attr}",
                exc,
            )
    _run()


def _stop_job_accessories_for_state(app, state: str) -> None:
    try:
        if hasattr(app, "_stop_job_accessories"):
            app._stop_job_accessories(f"job_{state}")
    except Exception as exc:
        _log_stream_ui_issue(
            "Failed stopping Kasa job accessories on stream-state transition", exc
        )


def _refresh_stream_busy_ui(app) -> None:
    if hasattr(app, "_update_quick_button_visibility"):
        try:
            app._update_quick_button_visibility()
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed refreshing quick-button visibility after stream-state transition",
                exc,
            )
    if hasattr(app, "_refresh_toolbar_action_focus"):
        try:
            app._refresh_toolbar_action_focus()
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed refreshing toolbar action focus after stream-state transition",
                exc,
            )


def _refresh_stream_busy_quick_buttons(app) -> None:
    if not hasattr(app, "_update_quick_button_visibility"):
        return
    try:
        app._update_quick_button_visibility()
    except Exception as exc:
        _log_stream_ui_issue(
            "Failed refreshing quick-button visibility after stream-state transition",
            exc,
        )


def _refresh_stream_busy_toolbar_focus(app) -> None:
    if not hasattr(app, "_refresh_toolbar_action_focus"):
        return
    try:
        app._refresh_toolbar_action_focus()
    except Exception as exc:
        _log_stream_ui_issue(
            "Failed refreshing toolbar action focus after stream-state transition", exc
        )


def _stream_busy_from_state(state: str | None, done_pending_idle: bool) -> bool:
    if bool(done_pending_idle):
        return True
    normalized = str(state or "").strip().lower()
    return normalized in ("running", "paused")


def _set_stream_progress_ui(
    app,
    *,
    pct: float | None,
    visible: bool,
    acked_offset: int | None = None,
    file_size_bytes: int | None = None,
) -> None:
    if acked_offset is not None:
        try:
            app._stream_acked_byte_offset = max(0, int(acked_offset))
        except Exception:
            app._stream_acked_byte_offset = 0
    if file_size_bytes is not None:
        try:
            app._stream_progress_file_size_bytes = max(0, int(file_size_bytes))
        except Exception:
            app._stream_progress_file_size_bytes = 0
    if pct is None or not bool(visible):
        app._stream_progress_pct = 0.0
        if hasattr(app, "progress_text"):
            try:
                app.progress_text.set("")
            except Exception:
                pass
        set_visible = getattr(app, "_set_stream_progress_visible", None)
        if callable(set_visible):
            try:
                set_visible(False)
            except Exception:
                pass
        return
    try:
        pct_f = max(0.0, min(100.0, float(pct)))
    except Exception:
        pct_f = 0.0
    app._stream_progress_pct = pct_f
    if hasattr(app, "progress_text"):
        try:
            app.progress_text.set(f"{pct_f:.1f}%")
        except Exception:
            pass
    set_visible = getattr(app, "_set_stream_progress_visible", None)
    if callable(set_visible):
        try:
            set_visible(True)
        except Exception:
            pass


def _loaded_state_signature(
    app, loaded_total: int | None
) -> tuple[str, str, int, str, str]:
    path = str(getattr(app, "_last_gcode_path", "") or "")
    gcode_hash = str(getattr(app, "_gcode_hash", "") or "")
    storage_mode = str(getattr(app, "_gcode_storage_mode", "") or "")
    stats_mode = str(getattr(app, "_gcode_stats_compute_mode", "") or "")
    total = (
        int(loaded_total)
        if loaded_total is not None
        else int(getattr(app, "_gcode_total_lines", 0) or 0)
    )
    return (path, gcode_hash, total, storage_mode, stats_mode)


def _loaded_state_transition_is_noop(
    prev_state: str | None,
    *,
    prior_signature: tuple[str, str, int, str, str] | None,
    next_signature: tuple[str, str, int, str, str],
    force_apply: bool,
) -> bool:
    return (
        str(prev_state or "").strip().lower() == "loaded"
        and prior_signature == next_signature
        and not force_apply
    )


def _prepare_loaded_state_transition(
    app,
    *,
    previous_state: str | None,
    loaded_total: int | None,
) -> tuple[bool, tuple[str, str, int, str, str], tuple[str, str, int, str, str] | None]:
    force_apply = bool(getattr(app, "_stream_loaded_force_apply", False))
    next_signature = _loaded_state_signature(app, loaded_total)
    prior_signature = getattr(app, "_stream_loaded_signature", None)
    return force_apply, next_signature, prior_signature


def _cancel_loaded_reconcile(app) -> None:
    after_id = getattr(app, "_stream_loaded_reconcile_after_id", None)
    if after_id is None:
        return
    cancel_fn = getattr(app, "after_cancel", None)
    if callable(cancel_fn):
        try:
            cancel_fn(after_id)
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed canceling loaded-state reconcile callback", exc
            )
    app._stream_loaded_reconcile_after_id = None


def _schedule_loaded_reconcile(
    app,
    *,
    loaded_total: int | None,
    prev_stream_busy: bool,
    force_apply: bool,
    signature: tuple[str, str, int, str, str],
) -> None:
    _cancel_loaded_reconcile(app)
    prior_reconcile_signature = getattr(app, "_stream_loaded_reconcile_signature", None)
    generation = int(getattr(app, "_stream_loaded_reconcile_generation", 0) or 0) + 1
    app._stream_loaded_reconcile_generation = generation
    app._stream_loaded_reconcile_signature = signature
    started_at = time.perf_counter()
    slice_count = 0
    max_slice_ms = 0.0
    slowest_phase = "none"
    slowest_phase_ms = 0.0
    section_timings_ms: dict[str, float] = {}

    def _record_section_timing(section_name: str, elapsed_ms: float) -> None:
        section_timings_ms[str(section_name)] = float(elapsed_ms)
        if elapsed_ms > _LOADED_RECONCILE_BUDGET_MS:
            logger.info(
                "[ui] stream_state loaded reconcile section exceeded %.1fms: section=%s %.2fms",
                _LOADED_RECONCILE_BUDGET_MS,
                str(section_name),
                elapsed_ms,
            )

    def _format_section_summary(prefix: str) -> str:
        rows = [
            (name, ms)
            for name, ms in section_timings_ms.items()
            if str(name).startswith(prefix)
        ]
        if not rows:
            return "<none>"
        rows.sort(key=lambda item: item[1], reverse=True)
        return ", ".join(
            f"{name.split('.', 1)[-1]}={ms:.2f}ms" for name, ms in rows[:8]
        )

    def _finalize_metrics(*, skipped: bool, reason: str) -> None:
        total_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        app._stream_loaded_reconcile_last_metrics = {
            "job": signature[0],
            "hash": signature[1],
            "generation": int(generation),
            "slices": int(slice_count),
            "total_ms": float(total_ms),
            "max_slice_ms": float(max_slice_ms),
            "slowest_phase": str(slowest_phase),
            "slowest_phase_ms": float(slowest_phase_ms),
            "skipped": bool(skipped),
            "reason": str(reason),
        }
        logger.info(
            "[ui] stream_state loaded reconcile metrics: job=%s hash=%s gen=%d slices=%d total=%.2fms "
            "max_slice=%.2fms slowest=%s(%.2fms) skipped=%s reason=%s",
            signature[0] or "<none>",
            signature[1] or "<none>",
            int(generation),
            int(slice_count),
            total_ms,
            max_slice_ms,
            slowest_phase,
            slowest_phase_ms,
            bool(skipped),
            reason,
        )
        logger.info(
            "[ui] stream_state loaded reconcile detail: gen=%d phase_b={%s} phase_c={%s}",
            int(generation),
            _format_section_summary("phase_b."),
            _format_section_summary("phase_c."),
        )

    def _phase_a_reset_progress() -> None:
        app._stream_done_pending_idle = False
        try:
            app.progress_pct.set(0)
            _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        except Exception as exc:
            _log_stream_ui_issue("Failed setting loaded progress during phase A", exc)

    def _phase_a_macro_state() -> None:
        try:
            with app.macro_executor.macro_vars() as macro_vars:
                macro_vars["running"] = False
                macro_vars["paused"] = False
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed updating macro running flags during loaded phase A", exc
            )

    def _phase_a_disable_pause_resume() -> None:
        try:
            app.btn_pause.config(state="disabled")
            app.btn_resume.config(state="disabled")
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed disabling pause/resume during loaded phase A", exc
            )

    def _phase_b_reset_runtime() -> None:
        app._stream_start_ts = None
        app._stream_pause_total = 0.0
        app._stream_paused_at = None
        app._live_estimate_min = None
        app._live_estimate_observed_total_min = None
        app._live_estimate_display_min = None
        app._live_estimate_display_ts = 0.0
        app._live_estimate_total_min = None

    def _phase_b_refresh_stats() -> None:
        try:
            app._refresh_gcode_stats_display()
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed refreshing G-code stats during loaded phase B", exc
            )

    def _phase_b_reset_throughput() -> None:
        try:
            app.throughput_var.set("TX: 0 B/s")
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed resetting throughput display during loaded phase B", exc
            )

    def _phase_b_set_run_resume() -> None:
        try:
            if loaded_total is None:
                ready = job_controls_ready(app)
            else:
                ready = job_controls_ready(app, bool(loaded_total))
            set_run_resume_from(app, bool(ready))
            app._job_controls_last_ready = bool(ready)
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed updating run/resume controls during loaded phase B", exc
            )

    def _phase_b_set_manual_controls() -> None:
        try:
            app._set_manual_controls_enabled(manual_controls_allowed(app))
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed updating manual controls during loaded phase B", exc
            )

    def _phase_b_clear_stream_lock() -> None:
        subsection_timings_ms: dict[str, float] = {}

        def _measure_subsection(name: str, fn) -> None:
            sub_started_at = time.perf_counter()
            fn()
            subsection_elapsed_ms = max(
                0.0, (time.perf_counter() - sub_started_at) * 1000.0
            )
            subsection_timings_ms[str(name)] = subsection_elapsed_ms
            _record_section_timing(
                f"phase_b.clear_stream_lock.{name}", subsection_elapsed_ms
            )

        try:
            _measure_subsection(
                "controls",
                lambda: _set_streaming_lock_safe(
                    app, False, defer_toolbar_refresh=True
                ),
            )
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed clearing streaming lock during loaded phase B", exc
            )
        if subsection_timings_ms:
            top_rows = sorted(
                subsection_timings_ms.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            top_elapsed_ms = top_rows[0][1]
            if top_elapsed_ms > _LOADED_RECONCILE_BUDGET_MS:
                logger.info(
                    "[ui] stream_state loaded reconcile clear_stream_lock detail: %s",
                    ", ".join(f"{name}={ms:.2f}ms" for name, ms in top_rows[:8]),
                )

    def _phase_c_clear_force_apply() -> None:
        app._stream_loaded_force_apply = False

    def _phase_c_apply_stream_busy_state() -> None:
        apply_stream_busy_state(app, False, log_hook=_log_stream_ui_issue)

    def _phase_c_refresh_busy_quick_buttons() -> None:
        _refresh_stream_busy_quick_buttons(app)

    def _phase_c_refresh_busy_toolbar_focus() -> None:
        _refresh_stream_busy_toolbar_focus(app)

    def _phase_c_update_joystick_polling() -> None:
        if hasattr(app, "_update_joystick_polling_state") and prev_stream_busy:
            try:
                app._update_joystick_polling_state()
            except Exception as exc:
                _log_stream_ui_issue(
                    "Failed updating joystick polling state during loaded phase C", exc
                )

    def _phase_c_apply_status_poll_profile() -> None:
        if prev_stream_busy:
            try:
                app._apply_status_poll_profile()
            except Exception as exc:
                _log_stream_ui_issue(
                    "Failed applying status poll profile during loaded phase C", exc
                )

    phases: list[tuple[str, Callable[[], None]]] = [
        ("phase_a.reset_progress", _phase_a_reset_progress),
        ("phase_a.macro_state", _phase_a_macro_state),
        ("phase_a.disable_pause_resume", _phase_a_disable_pause_resume),
        ("phase_b.reset_runtime", _phase_b_reset_runtime),
        ("phase_b.refresh_stats", _phase_b_refresh_stats),
        ("phase_b.reset_throughput", _phase_b_reset_throughput),
        ("phase_b.set_run_resume", _phase_b_set_run_resume),
        ("phase_b.set_manual_controls", _phase_b_set_manual_controls),
        ("phase_b.clear_stream_lock", _phase_b_clear_stream_lock),
        ("phase_c.clear_force_apply", _phase_c_clear_force_apply),
        ("phase_c.apply_stream_busy_state", _phase_c_apply_stream_busy_state),
        ("phase_c.refresh_busy_quick_buttons", _phase_c_refresh_busy_quick_buttons),
        ("phase_c.refresh_busy_toolbar_focus", _phase_c_refresh_busy_toolbar_focus),
        ("phase_c.update_joystick_polling", _phase_c_update_joystick_polling),
        ("phase_c.apply_status_poll_profile", _phase_c_apply_status_poll_profile),
    ]

    def _run_phase(phase_index: int) -> None:
        nonlocal slice_count, max_slice_ms, slowest_phase, slowest_phase_ms
        app._stream_loaded_reconcile_after_id = None
        if int(getattr(app, "_stream_loaded_reconcile_generation", 0) or 0) != int(
            generation
        ):
            _finalize_metrics(skipped=True, reason="coalesced_by_newer_reconcile")
            return
        if phase_index >= len(phases):
            _finalize_metrics(skipped=False, reason="ok")
            return
        phase_name, phase_fn = phases[phase_index]
        phase_started_at = time.perf_counter()
        try:
            phase_fn()
        except Exception as exc:
            _log_stream_ui_issue(f"Loaded reconcile phase failed: {phase_name}", exc)
            _finalize_metrics(skipped=True, reason=f"phase_failed:{phase_name}")
            return
        phase_elapsed_ms = max(0.0, (time.perf_counter() - phase_started_at) * 1000.0)
        _record_section_timing(str(phase_name), phase_elapsed_ms)
        slice_count += 1
        if phase_elapsed_ms > max_slice_ms:
            max_slice_ms = phase_elapsed_ms
        if phase_elapsed_ms > slowest_phase_ms:
            slowest_phase_ms = phase_elapsed_ms
            slowest_phase = str(phase_name)
        if phase_elapsed_ms > _LOADED_RECONCILE_BUDGET_MS:
            level_log = (
                logger.warning
                if phase_elapsed_ms >= _LOADED_RECONCILE_WARN_MS
                else logger.info
            )
            level_log(
                "[ui] stream_state loaded reconcile slice exceeded %.1fms budget: phase=%s %.2fms",
                _LOADED_RECONCILE_BUDGET_MS,
                str(phase_name),
                phase_elapsed_ms,
            )
        after_fn = getattr(app, "after", None)
        if callable(after_fn):
            try:
                app._stream_loaded_reconcile_after_id = after_fn(
                    0,
                    lambda idx=phase_index + 1: _run_phase(idx),
                )
                return
            except Exception as exc:
                _log_stream_ui_issue(
                    "Failed scheduling next loaded-state reconcile phase", exc
                )
        _run_phase(phase_index + 1)

    # If already loaded with same signature and no forced apply, skip reconcile work.
    if (
        not force_apply
        and str(getattr(app, "_stream_state", "") or "").strip().lower() == "loaded"
        and prior_reconcile_signature == signature
    ):
        logger.info(
            "[ui] stream_state loaded reconcile skip: job=%s hash=%s reason=signature_match",
            signature[0] or "<none>",
            signature[1] or "<none>",
        )
        _finalize_metrics(skipped=True, reason="signature_match")
        return

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._stream_loaded_reconcile_after_id = after_fn(0, lambda: _run_phase(0))
            return
        except Exception as exc:
            _log_stream_ui_issue("Failed scheduling loaded-state reconcile", exc)
    _run_phase(0)


def handle_stream_state_event(app, evt):
    st = evt[1]
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        try:
            perf_monitor.note_stream_state(str(st))
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed forwarding stream-state transition to performance monitor", exc
            )
    prev = app._stream_state
    prev_done_pending_idle = bool(getattr(app, "_stream_done_pending_idle", False))
    prev_stream_busy = _stream_busy_from_state(prev, prev_done_pending_idle)
    loaded_total = evt[2] if len(evt) > 2 else None
    if st == "loaded":
        force_apply, next_loaded_sig, prior_loaded_sig = _prepare_loaded_state_transition(
            app,
            previous_state=prev,
            loaded_total=loaded_total,
        )
        if _loaded_state_transition_is_noop(
            prev,
            prior_signature=prior_loaded_sig,
            next_signature=next_loaded_sig,
            force_apply=force_apply,
        ):
            logger.info(
                "[ui] stream_state idempotent skip: %s->%s job=%s hash=%s reason=already_loaded_no_changes",
                str(prev or "").strip().lower() or "none",
                "loaded",
                next_loaded_sig[0] or "<none>",
                next_loaded_sig[1] or "<none>",
            )
            return
        app._stream_loaded_signature = next_loaded_sig
    elif str(prev or "").strip().lower() == "loaded":
        app._stream_loaded_signature = None
    now = time.time()
    app._stream_state = st
    load_settling = bool(getattr(app, "_gcode_load_settling", False))
    if st == "loaded" and load_settling:
        end_deferred_completion_wait(app, now_ts=now)
        app._stream_done_pending_idle = False
        app._stream_loaded_force_apply = True
        try:
            app.progress_pct.set(0)
            _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed setting progress during load-settling stream-state update", exc
            )
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        try:
            app.btn_pause.config(state="disabled")
            app.btn_resume.config(state="disabled")
        except Exception as exc:
            _log_stream_ui_issue(
                "Failed disabling pause/resume during load-settling stream-state update",
                exc,
            )
        logger.info(
            "[ui] stream_state loaded deferred while load settling: job=%s hash=%s",
            str(next_loaded_sig[0] or "<none>"),
            str(next_loaded_sig[1] or "<none>"),
        )
        return
    if st == "loaded":
        end_deferred_completion_wait(app, now_ts=now)
        _schedule_loaded_reconcile(
            app,
            loaded_total=loaded_total,
            prev_stream_busy=prev_stream_busy,
            force_apply=force_apply,
            signature=next_loaded_sig,
        )
        return
    if st == "running":
        if prev == "paused":
            if app._stream_paused_at is not None:
                app._stream_pause_total += max(0.0, now - app._stream_paused_at)
                app._stream_paused_at = None
        elif prev != "running":
            app._stream_start_ts = now
            app._stream_pause_total = 0.0
            app._stream_paused_at = None
            app._live_estimate_min = None
            app._live_estimate_total_min = None
            app._live_estimate_observed_total_min = None
            app._live_estimate_display_min = None
            app._live_estimate_display_ts = 0.0
            _schedule_stream_ui_callback(
                app,
                callback_attr="_stream_state_stats_refresh_after_id",
                callback=lambda: app._refresh_gcode_stats_display(),
                context="Failed refreshing G-code stats after running-state transition",
            )
            app.throughput_var.set("TX: 0 B/s")
        if prev != "paused":
            try:
                last_acked_idx = int(getattr(app, "_last_acked_index", -1) or -1)
            except Exception:
                last_acked_idx = -1
            if last_acked_idx < 0:
                try:
                    stream_file_size = max(
                        int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
                        int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
                    )
                except Exception:
                    stream_file_size = 0
                try:
                    app.progress_pct.set(0)
                    _set_stream_progress_ui(
                        app,
                        pct=0.0,
                        visible=bool(stream_file_size > 0),
                        acked_offset=0,
                        file_size_bytes=int(stream_file_size),
                    )
                except Exception as exc:
                    _log_stream_ui_issue(
                        "Failed resetting running-state byte progress at fresh stream start",
                        exc,
                    )
        try:
            status_text = app.status.cget("text")
        except (AttributeError, tk.TclError):
            status_text = ""
        if status_text.startswith(("Stream error", "Paused", "Resuming")):
            try:
                name = ""
                path = getattr(app, "_last_gcode_path", None)
                if path:
                    name = os.path.basename(path)
                if not name:
                    name = getattr(app.grbl, "_gcode_name", "") or ""
                label = (
                    StatusMessages.streaming_job(name)
                    if name
                    else StatusMessages.STREAMING
                )
                app.status.config(text=label)
            except (AttributeError, tk.TclError, TypeError) as exc:
                logger.debug("Failed updating streaming status label: %s", exc)
    elif st == "paused":
        if app._stream_paused_at is None:
            app._stream_paused_at = now
    elif st in ("done", "stopped", "error", "alarm", "loaded"):
        app._stream_start_ts = None
        app._stream_pause_total = 0.0
        app._stream_paused_at = None
        app._live_estimate_min = None
        app._live_estimate_observed_total_min = None
        app._live_estimate_display_min = None
        app._live_estimate_display_ts = 0.0
        if st in ("error", "alarm", "loaded"):
            app._live_estimate_total_min = None
        _schedule_stream_ui_callback(
            app,
            callback_attr="_stream_state_stats_refresh_after_id",
            callback=lambda: app._refresh_gcode_stats_display(),
            context="Failed refreshing G-code stats after stream-state transition",
        )
        app.throughput_var.set("TX: 0 B/s")

    if st == "loaded":
        app._stream_done_pending_idle = False
        total = loaded_total
        app.progress_pct.set(0)
        _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        restore_controls_after_stream(
            app,
            loaded_total=total,
            job_ready_hook=job_controls_ready,
            set_run_resume_hook=set_run_resume_from,
        )
    elif st == "running":
        app._stream_done_pending_idle = False
        end_deferred_completion_wait(app, now_ts=now)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = True
            macro_vars["paused"] = False
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="normal")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app._set_manual_controls_enabled(False)
        _set_streaming_lock_safe(app, True, defer_toolbar_refresh=True)
    elif st == "paused":
        app._stream_done_pending_idle = False
        end_deferred_completion_wait(app, now_ts=now)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = True
            macro_vars["paused"] = True
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="normal")
        app.btn_resume_from.config(state="disabled")
        app._set_manual_controls_enabled(False)
        _set_streaming_lock_safe(app, True, defer_toolbar_refresh=True)
    elif st in ("done", "stopped"):
        _stop_job_accessories_for_state(app, st)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        if st == "done":
            defer_done = should_defer_done_until_idle(app, now_ts=now)
            app._stream_done_pending_idle = bool(defer_done)
            if defer_done:
                begin_deferred_completion_wait(app, now_ts=now)
            else:
                end_deferred_completion_wait(app, now_ts=now)
            app.progress_pct.set(99 if defer_done else 100)
            _set_stream_progress_ui(
                app,
                pct=(99.0 if defer_done else 100.0),
                visible=True,
                acked_offset=max(
                    int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
                    int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
                ),
                file_size_bytes=max(
                    int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
                    int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
                ),
            )
        else:
            app._stream_done_pending_idle = False
            end_deferred_completion_wait(app, now_ts=now)
            app.progress_pct.set(0)
            _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        if st == "done" and app._stream_done_pending_idle:
            app._set_manual_controls_enabled(False)
            _set_streaming_lock_safe(app, True, defer_toolbar_refresh=True)
        else:
            restore_controls_after_stream(
                app,
                job_ready_hook=job_controls_ready,
                set_run_resume_hook=set_run_resume_from,
            )
    elif st == "error":
        _stop_job_accessories_for_state(app, st)
        app._stream_done_pending_idle = False
        end_deferred_completion_wait(app, now_ts=now)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        restore_controls_after_stream(
            app,
            job_ready_hook=job_controls_ready,
            set_run_resume_hook=set_run_resume_from,
        )
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.status.config(text=f"Stream error: {evt[2]}")
    elif st == "alarm":
        _stop_job_accessories_for_state(app, st)
        app._stream_done_pending_idle = False
        end_deferred_completion_wait(app, now_ts=now)
        with app.macro_executor.macro_vars() as macro_vars:
            macro_vars["running"] = False
            macro_vars["paused"] = False
        app.progress_pct.set(0)
        _set_stream_progress_ui(app, pct=0.0, visible=False, acked_offset=0)
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app._set_alarm_lock(True, evt[2] if len(evt) > 2 else None)
        _set_streaming_lock_safe(app, False)
    stream_busy = st in ("running", "paused") or bool(
        getattr(app, "_stream_done_pending_idle", False)
    )
    apply_stream_busy_state(app, stream_busy, log_hook=_log_stream_ui_issue)

    def _apply_post_stream_state_ui_updates() -> None:
        _refresh_stream_busy_ui(app)
        if hasattr(app, "_update_joystick_polling_state") and (
            stream_busy != prev_stream_busy
        ):
            try:
                app._update_joystick_polling_state()
            except Exception as exc:
                _log_stream_ui_issue(
                    "Failed updating joystick polling state after stream-state transition",
                    exc,
                )
        if stream_busy != prev_stream_busy or st in ("alarm", "error"):
            try:
                app._apply_status_poll_profile()
            except Exception as exc:
                _log_stream_ui_issue(
                    "Failed applying status poll profile after stream-state transition",
                    exc,
                )

    _schedule_stream_ui_callback(
        app,
        callback_attr="_stream_state_post_apply_after_id",
        callback=_apply_post_stream_state_ui_updates,
        context="Failed applying deferred stream-state UI updates",
    )


def handle_stream_interrupted(app, evt):
    was_streaming = bool(evt[1]) if len(evt) > 1 else False
    if not was_streaming:
        return
    if getattr(app, "_user_disconnect", False):
        return
    app._resume_after_disconnect = True
    app._resume_from_index = max(0, app._last_acked_index + 1)
    app._resume_job_name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
