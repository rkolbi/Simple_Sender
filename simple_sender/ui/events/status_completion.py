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

"""Deferred stream-completion helpers used by status handling."""

from collections.abc import Callable


def deferred_completion_target_total(app) -> int:
    try:
        executable_total = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    except Exception:
        executable_total = 0
    if executable_total > 0:
        return executable_total
    try:
        return int(getattr(app, "_gcode_total_lines", 0) or 0)
    except Exception:
        return 0


def deferred_completion_bytes_done(app) -> bool:
    try:
        file_size = max(
            int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
            int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
        )
    except Exception:
        file_size = 0
    if file_size <= 0:
        return False
    try:
        acked = int(getattr(app, "_stream_acked_byte_offset", 0) or 0)
    except Exception:
        acked = 0
    return max(0, acked) >= file_size


def sync_deferred_stream_completion(
    app,
    state: str,
    *,
    time_module,
    deferred_completion_target_total: Callable[[object], int],
    deferred_completion_bytes_done: Callable[[object], bool],
    begin_deferred_completion_wait: Callable[..., object],
    end_deferred_completion_wait: Callable[..., object],
    set_stream_progress_ui: Callable[..., object],
    log_suppressed: Callable[[str, BaseException], None],
    restore_controls_after_stream: Callable[..., object],
    apply_stream_busy_state: Callable[..., object],
    job_controls_ready,
    set_run_resume_from,
) -> None:
    if not bool(getattr(app, "_stream_done_pending_idle", False)):
        return
    now_ts = time_module.time()
    total = deferred_completion_target_total(app)
    done = max(0, int(getattr(app, "_last_acked_index", -1)) + 1)
    complete_by_lines = total > 0 and done >= total
    complete_by_bytes = deferred_completion_bytes_done(app)
    if not complete_by_lines and not complete_by_bytes:
        begin_deferred_completion_wait(app, now_ts=now_ts)
        return
    if str(state or "").lower().startswith("idle"):
        end_deferred_completion_wait(app, now_ts=now_ts)
        app._stream_done_pending_idle = False
        app._stream_state = "done"
        notify_total = total if total > 0 else done
        if complete_by_bytes and done > 0 and done < notify_total:
            notify_total = done
        try:
            final_file_size = max(
                int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
                int(getattr(app, "_stream_progress_file_size_bytes", 0) or 0),
            )
        except Exception:
            final_file_size = 0
        try:
            set_stream_progress_ui(
                app,
                pct=100.0,
                visible=True,
                acked_offset=final_file_size,
                file_size_bytes=final_file_size,
            )
        except Exception as exc:
            log_suppressed("Failed finalizing deferred progress display at stream completion", exc)

        def _finalize_completion_ui() -> None:
            app._deferred_stream_finalize_pending = False
            try:
                app._maybe_notify_job_completion(done, notify_total)
            except Exception as exc:
                log_suppressed("Failed notifying deferred stream completion", exc)
            try:
                app.btn_pause.config(state="disabled")
                app.btn_resume.config(state="disabled")
            except Exception as exc:
                log_suppressed("Failed finalizing pause/resume controls after deferred completion", exc)
            try:
                restore_controls_after_stream(
                    app,
                    job_ready_hook=job_controls_ready,
                    set_run_resume_hook=set_run_resume_from,
                )
            except Exception as exc:
                log_suppressed("Failed finalizing manual controls after deferred completion", exc)
            apply_stream_busy_state(app, False, log_hook=log_suppressed)
            try:
                if hasattr(app, "_update_joystick_polling_state"):
                    app._update_joystick_polling_state()
            except Exception as exc:
                log_suppressed(
                    "Failed refreshing joystick polling after deferred completion",
                    exc,
                )
            try:
                app._apply_status_poll_profile()
            except Exception as exc:
                log_suppressed("Failed applying status poll profile after deferred completion", exc)

        if bool(getattr(app, "_deferred_stream_finalize_pending", False)):
            return
        after = getattr(app, "after", None)
        if callable(after):
            try:
                app._deferred_stream_finalize_pending = True
                after(0, _finalize_completion_ui)
                return
            except Exception as exc:
                app._deferred_stream_finalize_pending = False
                log_suppressed("Failed scheduling deferred stream-completion finalize callback", exc)
        _finalize_completion_ui()
        return
    begin_deferred_completion_wait(app, now_ts=now_ts)
    try:
        if int(app.progress_pct.get()) >= 100:
            app.progress_pct.set(99)
    except (AttributeError, TypeError, ValueError) as exc:
        log_suppressed("Failed clamping deferred completion progress", exc)
