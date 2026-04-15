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

from simple_sender.ui.job_controls import (
    disable_job_controls,
    job_controls_ready,
    set_run_resume_from,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def format_alarm_message(message: str | None) -> str:
    if not message:
        return "ALARM"
    text = str(message).strip()
    if text.lower().startswith("alarm"):
        return text
    if "reset to continue" in text.lower():
        return "ALARM: Reset to continue"
    if text.startswith("[MSG:"):
        return f"ALARM: {text}"
    return f"ALARM: {text}"


def is_hard_limit_alarm_message(message: str | None) -> bool:
    text = format_alarm_message(message).upper()
    return text.startswith("ALARM:1") or "HARD LIMIT" in text


def _alarm_recovery_kind(message: str | None) -> str:
    text = format_alarm_message(message).upper()
    if text.startswith("ALARM:1") or "HARD LIMIT" in text:
        return "hard_limit"
    if text.startswith("ALARM:2") or "SOFT LIMIT" in text:
        return "soft_limit"
    if text.startswith(("ALARM:4", "ALARM:5")) or "PROBE" in text:
        return "probe"
    if text.startswith(("ALARM:6", "ALARM:7", "ALARM:8", "ALARM:9")) or "HOMING" in text:
        return "homing"
    return "generic"


def alarm_recovery_guidance_text(message: str | None) -> str:
    kind = _alarm_recovery_kind(message)
    if kind == "hard_limit":
        return (
            "Hard-limit alarm recovery: confirm the limit condition is really clear before retrying "
            "Unlock ($X). If the controller still sees a limit input, unlock may be refused or the "
            "alarm may return immediately. Check the controller evidence below, then re-home ($H) "
            "after recovery if machine position is no longer trustworthy. If motion feels unsafe, "
            "use Reset (Ctrl-X)."
        )
    if kind == "soft_limit":
        return (
            "Soft-limit alarm recovery: confirm the requested move stays inside the configured "
            "machine travel before retrying Unlock ($X). If limits or machine position look wrong, "
            "re-home ($H) before running again. Use Reset (Ctrl-X) if motion feels unsafe."
        )
    if kind == "probe":
        return (
            "Probe alarm recovery: verify the probe or tool-setter wiring/contact state before "
            "retrying Unlock ($X). Make sure the expected probe input is no longer triggered, then "
            "rerun the probing step only when it is safe. Use Reset (Ctrl-X) if motion feels unsafe."
        )
    if kind == "homing":
        return (
            "Homing alarm recovery: resolve the homing or limit problem first, then re-home ($H) "
            "before jogging or running. Unlock ($X) may be refused until the controller sees a safe "
            "state. Use Reset (Ctrl-X) if motion feels unsafe."
        )
    return (
        "Suggested steps: Unlock ($X) to clear the alarm, then Home ($H) if required. Check the "
        "controller evidence below if unlock is refused or the alarm returns. If motion feels unsafe, "
        "use Reset (Ctrl-X)."
    )


def alarm_recovery_evidence_lines(
    last_status: str | None,
    pins: str | None,
) -> list[str]:
    lines: list[str] = []
    status_text = str(last_status or "").strip()
    if status_text:
        lines.append(f"Last status: {status_text}")
    pins_text = str(pins or "").strip()
    if pins_text:
        lines.append(f"Pins: {pins_text}")
    return lines


def alarm_recovery_log_lines(
    message: str | None,
    *,
    last_status: str | None = None,
    pins: str | None = None,
) -> list[str]:
    kind = _alarm_recovery_kind(message)
    if kind == "hard_limit":
        lines = [
            "[ALARM] Recovery: hard-limit alarm. Confirm the limit condition is really clear, then retry Unlock ($X).",
            "[ALARM] Recovery: if the controller still sees a limit input, unlock may be refused or the alarm may return. Re-home ($H) after recovery if position is no longer trustworthy. Use Reset (Ctrl-X) if motion feels unsafe.",
        ]
    elif kind == "soft_limit":
        lines = [
            "[ALARM] Recovery: soft-limit alarm. Confirm the requested move stays inside configured machine travel, then retry Unlock ($X).",
            "[ALARM] Recovery: if limits or machine position look wrong, re-home ($H) before running again. Use Reset (Ctrl-X) if motion feels unsafe.",
        ]
    elif kind == "probe":
        lines = [
            "[ALARM] Recovery: probe alarm. Verify the probe or tool-setter wiring/contact state before retrying Unlock ($X).",
            "[ALARM] Recovery: make sure the expected probe input is no longer triggered, then rerun the probing step only when it is safe. Use Reset (Ctrl-X) if motion feels unsafe.",
        ]
    elif kind == "homing":
        lines = [
            "[ALARM] Recovery: homing alarm. Resolve the homing or limit problem first, then re-home ($H) before jogging or running.",
            "[ALARM] Recovery: Unlock ($X) may be refused until the controller sees a safe state. Use Reset (Ctrl-X) if motion feels unsafe.",
        ]
    else:
        lines = [
            "[ALARM] Recovery: Unlock ($X) to clear the alarm, then Home ($H) if required. Use Reset (Ctrl-X) if motion feels unsafe.",
        ]
    evidence = alarm_recovery_evidence_lines(last_status, pins)
    if evidence:
        lines.append("[ALARM] Controller evidence: " + " | ".join(evidence))
    return lines


def _emit_alarm_recovery_log(app, message: str | None) -> None:
    ui_q = getattr(app, "ui_q", None)
    if ui_q is None or not hasattr(ui_q, "put"):
        return
    last_status = str(getattr(app, "_last_status_raw", "") or "").strip()
    pins_value = getattr(app, "_last_status_pins", None)
    pins = str(pins_value or "").strip()
    key = (format_alarm_message(message), last_status, pins)
    if key == getattr(app, "_alarm_recovery_log_key", None):
        return
    try:
        for line in alarm_recovery_log_lines(
            message,
            last_status=last_status,
            pins=pins,
        ):
            ui_q.put(("log", line))
        app._alarm_recovery_log_key = key
    except Exception as exc:
        _log_suppressed("Failed logging alarm recovery guidance", exc)


def mark_alarm_clear_requested(app) -> None:
    try:
        app._alarm_clear_requested = True
    except Exception as exc:
        _log_suppressed("Failed marking alarm-clear request", exc)


def set_alarm_lock(app, locked: bool, message: str | None = None):
    if locked:
        app._alarm_locked = True
        app._alarm_latched = True
        app._alarm_clear_requested = False
        if message:
            app._alarm_message = message
        disable_job_controls(app)
        try:
            app.btn_alarm_recover.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling Alarm Recover button when alarm lock engages", exc)
        app._set_manual_controls_enabled(True)
        try:
            app.status.config(text=format_alarm_message(message or app._alarm_message))
        except Exception as exc:
            _log_suppressed("Failed updating status text when alarm lock engages", exc)
        app._machine_state_text = "Alarm"
        app.machine_state.set("Alarm")
        app._start_state_flash("#ff5252")
        _emit_alarm_recovery_log(app, message or app._alarm_message)
        return

    if not app._alarm_locked and not bool(getattr(app, "_alarm_latched", False)):
        return
    app._alarm_locked = False
    app._alarm_latched = False
    app._alarm_clear_requested = False
    app._alarm_message = ""
    app._alarm_recovery_log_key = None
    app.macro_executor.clear_alarm_notification()
    try:
        app.btn_alarm_recover.config(state="disabled")
    except Exception as exc:
        _log_suppressed("Failed disabling Alarm Recover button when alarm lock clears", exc)
    if (
        app.connected
        and app._grbl_ready
        and app._status_seen
        and app._stream_state not in ("running", "paused")
        and not bool(getattr(app, "_stream_done_pending_idle", False))
    ):
        app._set_manual_controls_enabled(True)
        if job_controls_ready(app):
            set_run_resume_from(app, True)
    status_text = ""
    try:
        status_text = app.status.cget("text")
    except Exception as exc:
        _log_suppressed("Failed reading status text while clearing alarm lock", exc)
    if app.connected and status_text.startswith("ALARM"):
        app.status.config(text=f"Connected: {app._connected_port}")
    if app._pending_settings_refresh and app._grbl_ready:
        app._pending_settings_refresh = False
        app._request_settings_dump()
    app.machine_state.set(app._machine_state_text)
    app._update_state_highlight(app._machine_state_text)
    app._apply_status_poll_profile()

