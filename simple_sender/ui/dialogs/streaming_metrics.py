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
from datetime import datetime
import logging
import threading
from simple_sender.utils.log_suppressed import log_suppressed_exception
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

from simple_sender.builtin_workflow_runtime import park_safe_machine_z_command_sequence
from simple_sender.macro_state import macro_wait_for_idle
from simple_sender.ui.dialogs.runtime_modal_policy import (
    RuntimeModalRecoveryController,
)
from simple_sender.ui.events.stream_state_ui import apply_stream_busy_state, restore_controls_after_stream
from simple_sender.ui.job_controls import job_controls_ready, set_run_resume_from
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window
from simple_sender.utils.constants import MACRO_LINE_TIMEOUT

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_JOB_COMPLETION_DIALOG_ID = "simple_sender.ui.dialogs.streaming_metrics._show_job_completion_dialog"


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


@dataclass(frozen=True)
class _JobCompletionContext:
    start_time: datetime
    finish_time: datetime
    elapsed_str: str
    start_text: str
    finish_text: str
    dry_run: bool
    eof_verified: bool
    eof_total_lines: int
    eof_total_lines_known: bool
    eof_last_acked_index: int
    eof_shortfall_lines: int
    eof_warning: str


@dataclass(frozen=True)
class _JobCompletionSafetyResult:
    safe: bool
    title: str
    header_text: str
    body_prefix: str = ""
    detail_lines: tuple[str, ...] = ()
    force_dialog: bool = False


def format_throughput(bps: float) -> str:
    if bps <= 0:
        return "TX: 0 B/s"
    if bps < 1024:
        return f"TX: {bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"TX: {bps / 1024.0:.1f} KB/s"
    return f"TX: {bps / (1024.0 * 1024.0):.2f} MB/s"


def maybe_notify_job_completion(app, done: int, total: int) -> None:
    state_text = str(getattr(app, "_machine_state_text", "") or "").lower()
    motion_active = bool(state_text) and not state_text.startswith("idle")
    if (
        app._job_started_at is None
        or app._job_completion_notified
        or bool(getattr(app, "_job_completion_finalize_pending", False))
        or total <= 0
        or done < total
        or motion_active
    ):
        return
    context = _build_job_completion_context(app)
    if context.dry_run:
        _finalize_job_completion_notification(
            app,
            context,
            _JobCompletionSafetyResult(
                safe=True,
                title="Dry run completed",
                header_text="Dry run completed",
                body_prefix="Dry run completed.\n\nNo spindle-off or safe-Z motion was commanded.",
            ),
        )
        return
    if not _controller_connected(app):
        _finalize_job_completion_notification(
            app,
            context,
            _JobCompletionSafetyResult(
                safe=False,
                title="Job finished with warning",
                header_text="Job finished with warning",
                body_prefix=(
                    "The job reached the end of the stream, but Simple Sender could not enforce "
                    "spindle-off or Park safe-Z because the controller is disconnected."
                ),
                detail_lines=("Reconnect the controller and verify spindle state / Z position manually.",),
                force_dialog=True,
            ),
        )
        return
    if bool(getattr(app, "_alarm_locked", False)):
        _finalize_job_completion_notification(
            app,
            context,
            _JobCompletionSafetyResult(
                safe=False,
                title="Job finished with warning",
                header_text="Job finished with warning",
                body_prefix=(
                    "The job reached the end of the stream, but Simple Sender could not enforce "
                    "spindle-off or Park safe-Z because the controller is in alarm."
                ),
                detail_lines=("Clear the alarm and verify spindle state / Z position manually.",),
                force_dialog=True,
            ),
        )
        return
    _begin_job_completion_cleanup_pending(app)
    _start_job_completion_cleanup(app, context)


def _build_job_completion_context(app) -> _JobCompletionContext:
    start_time = app._job_started_at
    finish_time = datetime.now()
    elapsed = finish_time - start_time
    elapsed_str = str(elapsed).split(".")[0]
    eof_verified = bool(getattr(app, "_stream_completion_verified_eof", False))
    eof_total_lines = int(getattr(app, "_stream_completion_total_lines", 0) or 0)
    eof_total_lines_known = bool(
        getattr(app, "_stream_completion_total_lines_known", False)
    )
    raw_eof_last_acked_index = getattr(app, "_stream_completion_last_acked_index", -1)
    try:
        eof_last_acked_index = int(raw_eof_last_acked_index)
    except Exception:
        eof_last_acked_index = -1
    eof_shortfall_lines = int(
        getattr(app, "_stream_completion_shortfall_lines", 0) or 0
    )
    eof_warning = str(getattr(app, "_stream_completion_warning", "") or "").strip()
    return _JobCompletionContext(
        start_time=start_time,
        finish_time=finish_time,
        elapsed_str=elapsed_str,
        start_text=start_time.strftime("%Y-%m-%d %H:%M:%S"),
        finish_text=finish_time.strftime("%Y-%m-%d %H:%M:%S"),
        dry_run=_is_dry_run_enabled(app),
        eof_verified=eof_verified,
        eof_total_lines=eof_total_lines,
        eof_total_lines_known=eof_total_lines_known,
        eof_last_acked_index=eof_last_acked_index,
        eof_shortfall_lines=eof_shortfall_lines,
        eof_warning=eof_warning,
    )


def _is_dry_run_enabled(app) -> bool:
    dry_run_var = getattr(app, "dry_run_sanitize_stream", False)
    getter = getattr(dry_run_var, "get", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:
            return False
    return bool(dry_run_var)


def _controller_connected(app) -> bool:
    grbl = getattr(app, "grbl", None)
    checker = getattr(grbl, "is_connected", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception as exc:
            _log_suppressed("Failed checking GRBL connection state during job completion", exc)
            return False
    return bool(getattr(app, "connected", False))


def _command_timeout_s(app) -> float:
    raw = getattr(app, "macro_line_timeout_sec", MACRO_LINE_TIMEOUT)
    getter = getattr(raw, "get", None)
    if callable(getter):
        try:
            raw = getter()
        except Exception:
            raw = MACRO_LINE_TIMEOUT
    try:
        timeout_s = float(raw)
    except Exception:
        timeout_s = MACRO_LINE_TIMEOUT
    if timeout_s <= 0:
        timeout_s = MACRO_LINE_TIMEOUT
    return max(1.0, timeout_s)


def _job_completion_log(app, message: str) -> None:
    try:
        controller = getattr(app, "streaming_controller", None)
        if controller is None:
            logger.info(message)
            return
        logger_fn = getattr(controller, "handle_log", None)
        if callable(logger_fn):
            logger_fn(message)
            return
        log_fn = getattr(controller, "log", None)
        if callable(log_fn):
            log_fn(message)
            return
        logger.info(message)
    except Exception as exc:
        _log_suppressed("Failed logging job-completion message", exc)


def _begin_job_completion_cleanup_pending(app) -> None:
    app._job_completion_finalize_pending = True
    try:
        app._set_manual_controls_enabled(False)
    except Exception as exc:
        _log_suppressed("Failed disabling manual controls during job-completion cleanup", exc)
    try:
        app.btn_run.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
    except Exception as exc:
        _log_suppressed("Failed disabling run controls during job-completion cleanup", exc)
    set_streaming_lock = getattr(app, "_set_streaming_lock", None)
    if callable(set_streaming_lock):
        try:
            set_streaming_lock(True, defer_toolbar_refresh=True)
        except Exception as exc:
            _log_suppressed("Failed enabling stream lock during job-completion cleanup", exc)
    apply_stream_busy_state(app, True, log_hook=_log_suppressed)
    try:
        if hasattr(app, "_update_joystick_polling_state"):
            app._update_joystick_polling_state()
    except Exception as exc:
        _log_suppressed("Failed refreshing joystick polling during job-completion cleanup", exc)
    try:
        app._apply_status_poll_profile()
    except Exception as exc:
        _log_suppressed("Failed applying status poll profile during job-completion cleanup", exc)


def _end_job_completion_cleanup_pending(app) -> None:
    app._job_completion_finalize_pending = False
    try:
        restore_controls_after_stream(
            app,
            job_ready_hook=job_controls_ready,
            set_run_resume_hook=set_run_resume_from,
        )
    except Exception as exc:
        _log_suppressed("Failed restoring controls after job-completion cleanup", exc)
    apply_stream_busy_state(app, False, log_hook=_log_suppressed)
    try:
        if hasattr(app, "_update_joystick_polling_state"):
            app._update_joystick_polling_state()
    except Exception as exc:
        _log_suppressed("Failed refreshing joystick polling after job-completion cleanup", exc)
    try:
        app._apply_status_poll_profile()
    except Exception as exc:
        _log_suppressed("Failed applying status poll profile after job-completion cleanup", exc)


def _start_job_completion_cleanup(app, context: _JobCompletionContext) -> None:
    def worker() -> None:
        result = _run_job_completion_cleanup(app)
        poster = getattr(app, "_post_ui_thread", None)
        if callable(poster):
            try:
                poster(_finalize_job_completion_notification, app, context, result)
                return
            except Exception as exc:
                _log_suppressed("Failed posting job-completion finalize callback to UI thread", exc)
        _finalize_job_completion_notification(app, context, result)

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception as exc:
        _log_suppressed("Failed starting job-completion cleanup worker", exc)
        _finalize_job_completion_notification(
            app,
            context,
            _JobCompletionSafetyResult(
                safe=False,
                title="Job finished with warning",
                header_text="Job finished with warning",
                body_prefix="Post-job safety cleanup could not be started.",
                detail_lines=(str(exc),),
                force_dialog=True,
            ),
        )


def _stop_job_accessories_after_completion(app, result: _JobCompletionSafetyResult) -> None:
    stopper = getattr(app, "_stop_job_accessories", None)
    if not callable(stopper):
        return
    source = "job_done" if bool(result.safe) else "job_done_warning"
    try:
        stopper(source)
        if result.safe:
            _job_completion_log(
                app,
                "[job] Job accessories OFF requested after post-job cleanup completed.",
            )
        else:
            _job_completion_log(
                app,
                "[job] Job accessories OFF requested after post-job cleanup warning; verify accessory state manually.",
            )
    except Exception as exc:
        _log_suppressed("Failed stopping job accessories after job-completion cleanup", exc)
        _job_completion_log(
            app,
            f"[job] Job accessory shutdown failed after completion cleanup: {exc}",
        )


def _run_job_completion_cleanup(app) -> _JobCompletionSafetyResult:
    _job_completion_log(app, "[job] Enforcing post-job safety cleanup: spindle off + Park safe-Z.")
    spindle_ok, spindle_error = _send_job_completion_command(app, "M5")
    if spindle_ok:
        _job_completion_log(app, "[job] Post-job spindle-off command accepted.")
    else:
        _job_completion_log(
            app,
            f"[job] Post-job spindle-off command failed: {spindle_error}",
        )
    motion_block_reason = _motion_block_reason(app)
    safe_z_ok = False
    safe_z_error = ""
    safe_z_sequence = park_safe_machine_z_command_sequence()
    if motion_block_reason is not None:
        safe_z_error = motion_block_reason
        _job_completion_log(
            app,
            f"[job] Post-job Park safe-Z raise skipped: {motion_block_reason}",
        )
    else:
        for index, command in enumerate(safe_z_sequence):
            wait_for_idle_after = index == (len(safe_z_sequence) - 1)
            ok, error = _send_job_completion_command(
                app,
                command,
                wait_for_idle_after=wait_for_idle_after,
            )
            if not ok:
                safe_z_error = error or f"Failed sending {command}."
                _job_completion_log(
                    app,
                    f"[job] Post-job Park safe-Z step failed: command={command!r} detail={safe_z_error}",
                )
                break
        else:
            safe_z_ok = True
            _job_completion_log(
                app,
                "[job] Post-job Park safe-Z raise completed using shared Park sequence: "
                + " -> ".join(safe_z_sequence),
            )
    if spindle_ok and safe_z_ok:
        return _JobCompletionSafetyResult(
            safe=True,
            title="Job completed",
            header_text="Job completed",
        )
    detail_lines = []
    if spindle_ok:
        detail_lines.append("Spindle off: completed.")
    else:
        detail_lines.append(f"Spindle off: failed. {spindle_error}")
    if safe_z_ok:
        detail_lines.append("Park safe-Z raise: completed.")
    else:
        detail_lines.append(f"Park safe-Z raise: failed. {safe_z_error}")
    return _JobCompletionSafetyResult(
        safe=False,
        title="Job finished with warning",
        header_text="Job finished with warning",
        body_prefix=(
            "The job reached the end of the stream, but the post-job safety actions did not fully complete."
        ),
        detail_lines=tuple(detail_lines),
        force_dialog=True,
    )


def _motion_block_reason(app) -> str | None:
    if not _controller_connected(app):
        return "controller disconnected before the safe-Z raise could run."
    if bool(getattr(app, "_alarm_locked", False)):
        return "controller is in alarm, so motion was not attempted."
    if not bool(getattr(app, "_machine_coordinates_trusted", False)):
        return "machine coordinates are not trusted; home the machine before automatic G53 safe-Z."
    return None


def _send_job_completion_command(
    app,
    command: str,
    *,
    wait_for_idle_after: bool = False,
) -> tuple[bool, str | None]:
    grbl = getattr(app, "grbl", None)
    if grbl is None:
        return False, "GRBL worker is unavailable."
    timeout_s = _command_timeout_s(app)
    send_tracked = getattr(grbl, "send_immediate_tracked", None)
    tracker = None
    if callable(send_tracked):
        try:
            tracker = send_tracked(command, source="job_completion")
        except Exception as exc:
            return False, f"Failed sending {command}: {exc}"
        if tracker is None:
            return False, f"Controller rejected {command}."
        try:
            if not tracker.wait(timeout_s):
                return False, f"Timed out waiting for controller acknowledgement: {command}"
        except Exception as exc:
            return False, f"Failed waiting for {command}: {exc}"
        if not bool(getattr(tracker, "success", False)):
            detail = str(getattr(tracker, "error", "") or "").strip()
            return False, detail or f"Controller rejected {command}."
    else:
        send_immediate = getattr(grbl, "send_immediate", None)
        if not callable(send_immediate):
            return False, "GRBL immediate-command API is unavailable."
        try:
            accepted = bool(send_immediate(command, source="job_completion"))
        except TypeError:
            accepted = bool(send_immediate(command))
        except Exception as exc:
            return False, f"Failed sending {command}: {exc}"
        if not accepted:
            return False, f"Controller rejected {command}."
    if wait_for_idle_after:
        return _wait_for_job_completion_idle(app, command=command, timeout_s=timeout_s)
    return True, None


def _wait_for_job_completion_idle(
    app,
    *,
    command: str,
    timeout_s: float,
) -> tuple[bool, str | None]:
    grbl = getattr(app, "grbl", None)
    if grbl is None:
        return False, "GRBL worker is unavailable."
    try:
        macro_wait_for_idle(
            app=app,
            grbl=grbl,
            ui_q=getattr(app, "ui_q", None),
            timeout_s=timeout_s,
        )
    except Exception as exc:
        return False, f"Failed waiting for idle after {command}: {exc}"
    state_text = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
    if state_text.startswith("idle"):
        return True, None
    return False, f"Controller did not report idle after {command} (state={state_text or 'unknown'})."


def _finalize_job_completion_notification(
    app,
    context: _JobCompletionContext,
    result: _JobCompletionSafetyResult,
) -> None:
    if bool(getattr(app, "_job_completion_finalize_pending", False)):
        _end_job_completion_cleanup_pending(app)
    else:
        app._job_completion_finalize_pending = False
    app._job_completion_notified = True
    app._job_started_at = None
    summary = (
        f"Job completed in {context.elapsed_str} "
        f"(started {context.start_text}, finished {context.finish_text})."
    )
    try:
        log_job_completed = getattr(app.streaming_controller, "log_job_completed", None)
        if callable(log_job_completed):
            log_job_completed(
                elapsed_str=context.elapsed_str,
                start_text=context.start_text,
                finish_text=context.finish_text,
            )
        else:
            _job_completion_log(app, f"[job] {summary}")
    except Exception as exc:
        _log_suppressed("Failed logging job completion summary", exc)
    try:
        _job_completion_log(
            app,
            "[job] EOF verification: "
            f"verified={context.eof_verified}, "
            f"total_lines={context.eof_total_lines}, "
            f"total_known={context.eof_total_lines_known}, "
            f"last_acked_index={context.eof_last_acked_index}, "
            f"shortfall_lines={context.eof_shortfall_lines}",
        )
    except Exception as exc:
        _log_suppressed("Failed logging EOF verification summary", exc)
    result = _merge_eof_verification_result(context, result)
    _stop_job_accessories_after_completion(app, result)
    message = _build_job_completion_message(context, result)
    popup_var = getattr(app, "job_completion_popup", False)
    popup_getter = getattr(popup_var, "get", None)
    popup_enabled = bool(popup_getter()) if callable(popup_getter) else bool(popup_var)
    if result.force_dialog or popup_enabled:
        _show_job_completion_dialog(
            app,
            message,
            title=result.title,
            header_text=result.header_text,
        )
    if bool(getattr(result, "safe", False)):
        _job_completion_log(app, f"[job] {summary}")
    else:
        _job_completion_log(app, f"[job] {summary} Post-job safety cleanup reported a warning.")
    beep_var = getattr(app, "job_completion_beep", False)
    beep_getter = getattr(beep_var, "get", None)
    beep_enabled = bool(beep_getter()) if callable(beep_getter) else bool(beep_var)
    if beep_enabled:
        try:
            app.bell()
        except Exception as exc:
            _log_suppressed("Failed playing job completion bell", exc)


def _build_job_completion_message(
    context: _JobCompletionContext,
    result: _JobCompletionSafetyResult,
) -> str:
    sections = []
    prefix = str(result.body_prefix or "").strip()
    if prefix:
        sections.append(prefix)
    sections.append(
        f"Started: {context.start_text}\n"
        f"Finished: {context.finish_text}\n"
        f"Elapsed: {context.elapsed_str}"
    )
    eof_lines = [
        "EOF verification: "
        + ("verified" if context.eof_verified else "NOT verified"),
        "Executable lines at completion: "
        + (
            f"{context.eof_total_lines}"
            if context.eof_total_lines > 0
            else "unknown"
        )
        + (
            " (known)"
            if context.eof_total_lines_known
            else " (estimated/unknown)"
        ),
        f"Final acknowledged line: {max(0, context.eof_last_acked_index + 1)}",
    ]
    if context.eof_shortfall_lines > 0:
        eof_lines.append(
            f"Executable-line shortfall: {context.eof_shortfall_lines}"
        )
    if context.eof_warning:
        eof_lines.append(context.eof_warning)
    sections.append("\n".join(eof_lines))
    if result.detail_lines:
        sections.append("\n".join(result.detail_lines))
    return "\n\n".join(str(section) for section in sections if str(section).strip())


def _merge_eof_verification_result(
    context: _JobCompletionContext,
    result: _JobCompletionSafetyResult,
) -> _JobCompletionSafetyResult:
    if context.eof_verified:
        return result
    detail_lines = list(result.detail_lines)
    if context.eof_warning:
        detail_lines.insert(0, context.eof_warning)
    else:
        detail_lines.insert(
            0,
            "True EOF was not verified before completion was reported.",
        )
    if result.safe:
        body_prefix = (
            "The machine reached the completion path, but Simple Sender could not "
            "prove that the true executable end-of-file was reached."
        )
    else:
        body_prefix = str(result.body_prefix or "").strip()
        eof_prefix = (
            "Simple Sender could not prove that the true executable end-of-file "
            "was reached."
        )
        body_prefix = (
            f"{eof_prefix}\n\n{body_prefix}" if body_prefix else eof_prefix
        )
    return _JobCompletionSafetyResult(
        safe=False,
        title="Job finished with warning",
        header_text="Job finished with warning",
        body_prefix=body_prefix,
        detail_lines=tuple(detail_lines),
        force_dialog=True,
    )


def _set_completion_progress_display(app) -> None:
    try:
        setattr(app, "_stream_progress_pct", 100.0)
    except Exception:
        pass
    try:
        progress_pct = getattr(app, "progress_pct", None)
        if progress_pct is not None:
            progress_pct.set(100)
    except Exception as exc:
        _log_suppressed("Failed setting completion progress bar to 100%", exc)
    try:
        progress_text = getattr(app, "progress_text", None)
        if progress_text is not None:
            progress_text.set("100.0%")
    except Exception as exc:
        _log_suppressed("Failed setting completion progress label to 100.0%", exc)
    set_visible = getattr(app, "_set_stream_progress_visible", None)
    if callable(set_visible):
        try:
            set_visible(True)
        except Exception as exc:
            _log_suppressed("Failed ensuring completion progress display is visible", exc)


def _start_completion_flash(app) -> None:
    if getattr(app, "_completion_flash_active", False):
        return
    _set_completion_progress_display(app)
    app._completion_flash_active = True
    app._completion_flash_on = False

    def tick():
        if not getattr(app, "_completion_flash_active", False):
            return
        app.progress_pct.set(0 if app._completion_flash_on else 100)
        app._completion_flash_on = not app._completion_flash_on
        app._completion_flash_id = app.after(350, tick)

    app._completion_flash_id = app.after(0, tick)


def _stop_completion_flash(app) -> None:
    app._completion_flash_active = False
    flash_id = getattr(app, "_completion_flash_id", None)
    if flash_id is not None:
        try:
            app.after_cancel(flash_id)
        except Exception as exc:
            _log_suppressed("Failed canceling completion flash timer", exc)
    app._completion_flash_id = None
    app._completion_flash_on = False
    _set_completion_progress_display(app)


def _show_job_completion_dialog(
    app,
    message: str,
    *,
    title: str = "Job completed",
    header_text: str = "Job completed",
) -> None:
    if getattr(app, "_completion_dialog", None) is not None:
        return
    _start_completion_flash(app)
    dialog = tk.Toplevel(app)
    app._completion_dialog = dialog
    dialog.title(title)
    dialog.transient(app)
    dialog.resizable(False, False)
    dialog.configure(padx=24, pady=16)
    apply_toplevel_theme(dialog, app)

    base_font = tkfont.nametofont("TkDefaultFont")
    title_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 4,
        weight="bold",
    )
    body_font = tkfont.Font(
        family=base_font.cget("family"),
        size=int(base_font.cget("size")) + 2,
    )

    header = ttk.Frame(dialog)
    header.pack(fill="x")
    ttk.Label(header, text=header_text, font=title_font).pack(side="left")
    btn = ttk.Button(header, text="OK")
    btn.pack(side="right", padx=(0, 6))
    ttk.Label(dialog, text=message, font=body_font, justify="left", wraplength=520).pack(
        anchor="w", pady=(12, 6)
    )
    recovery = RuntimeModalRecoveryController(
        app,
        dialog,
        dialog_id=_JOB_COMPLETION_DIALOG_ID,
        action_buttons=(btn,),
    )
    recovery.attach_lock_toggle(
        header,
        pack_kwargs={"side": "right"},
    )

    def close():
        if getattr(app, "_completion_dialog", None) is None:
            return
        recovery.cleanup()
        app._completion_dialog = None
        _stop_completion_flash(app)
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed destroying job completion dialog", exc)
    btn.configure(command=close)
    dialog.protocol("WM_DELETE_WINDOW", close)
    try:
        dialog.grab_set()
    except Exception as exc:
        _log_suppressed("Failed setting job completion dialog grab", exc)
    center_window(dialog, app)
