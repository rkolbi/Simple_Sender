"""Read-only operator summary; command admission remains with existing controls."""

from __future__ import annotations

import math
import time
from pathlib import Path
from tkinter import ttk
from typing import Any

from simple_sender.types import NormalSessionPhase
from simple_sender.ui.job_setup_state import has_valid_job_setup_state


def _readiness(app: Any) -> str:
    if getattr(app, "_disconnecting", False):
        return "Disconnecting — wait for the connection to close."
    if getattr(app, "_connecting", False):
        return "Connecting — waiting for the controller."
    if not getattr(app, "connected", False):
        return "Disconnected — select a port and Connect."
    worker = getattr(app, "grbl", None)
    try:
        if worker.recovery_required():
            return "Recovery required — follow the Recovery dialog before further machine operation."
        if getattr(app, "_alarm_locked", False):
            return "Alarm — review the alarm and use the existing recovery controls."
        if not getattr(app, "_grbl_ready", False) or not getattr(app, "_status_seen", False):
            return "Connected — waiting for controller communication and status."
        normal = worker.normal_session_state()
        if normal.required:
            if normal.homing_started:
                return "Homing in progress — waiting for the controller to finish and confirm position."
            if normal.phase is NormalSessionPhase.POSITION_REQUIRED:
                return "Homing required — use Home to establish machine position."
            return "Machine initialization incomplete — follow the readiness dialog."
        if getattr(app, "_stream_done_pending_idle", False) or worker.execution_pending():
            return "Finishing job — waiting for controller confirmation and completion safeguards."
        if getattr(app, "_gcode_loading", False):
            return "Loading job — wait for file preparation to finish."
        if getattr(app, "_pending_gcode_source_transaction", None) is not None:
            return "Preparing job — waiting for the loaded file to be installed."
        if getattr(app, "_gcode_restore_failed", False):
            return "Job restore failed — load the intended file again."
        state = str(getattr(app, "_stream_state", "") or "").lower()
        suspension = {
            "pause_requested": "Pause requested — waiting for controller Hold confirmation.",
            "resume_requested": "Resume requested — waiting for controller confirmation.",
            "external_hold": "Controller Hold — review the machine before explicitly requesting Resume.",
            "door_suspended": "Safety Door suspension — follow the controller status before Resume.",
        }
        if state in suspension:
            return suspension[state]
        if state == "paused":
            return "Job paused — follow any active tool-change or hold instructions before Resume."
        if state == "running":
            return "Job running — monitor the machine; use Pause or Stop when needed."
        if not getattr(getattr(app, "gview", None), "lines_count", 0):
            return "No job loaded — use Read Job to select a file."
        ready, missing = worker.job_start_eligibility(
            getattr(app, "_worker_gcode_source_identity", None)
        )
        if not ready:
            reasons = {
                "displayed source does not match worker source": "loaded file confirmation is pending",
                "G-code source is not committed": "loaded file confirmation is pending",
                "no committed G-code job": "load the intended job again",
                "machine position": "machine position has not been established",
            }
            reason = str(missing[0]) if missing else "controller readiness is unavailable"
            return "Run unavailable — " + reasons.get(reason, reason) + "."
        if str(app.btn_run.cget("state")) == "disabled":
            return "Run unavailable — finish the active machine operation or follow the current dialog."
        if not has_valid_job_setup_state(app):
            return "Setup warning — no current tool reference. Review Job Setup before Run."
        return "Run available — verify the tool, work zero and clearance before starting."
    except Exception:
        # An unavailable observation must never turn into a positive readiness claim.
        return "Readiness unavailable — check the controller status and current dialog."


def _seconds(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total // 60) % 60:02d}:{total % 60:02d}"


def summary_text(app: Any, *, now: float | None = None) -> tuple[str, str, str]:
    """Read current state only, without scanning files or retaining session snapshots."""
    loading = bool(getattr(app, "_gcode_loading", False)) or (
        getattr(app, "_pending_gcode_source_transaction", None) is not None
    )
    loaded = bool(getattr(getattr(app, "gview", None), "lines_count", 0))
    path = str(getattr(app, "_last_gcode_path", "") or "")
    # Bound display length and keep filenames from injecting extra layout rows.
    name = " ".join(Path(path).name.split())
    if len(name) > 100:
        name = name[:97] + "..."
    title = "Job: preparing file…" if loading else (
        f"Job: {name or 'Loaded file'}" if loaded else "Job: none loaded"
    )
    readiness = _readiness(app)
    timing = ""
    active = readiness.startswith(("Job running", "Job paused"))
    if active and not loading:
        start = _seconds(getattr(app, "_stream_start_ts", None))
        paused = _seconds(getattr(app, "_stream_pause_total", 0))
        paused_at = _seconds(getattr(app, "_stream_paused_at", None))
        current = time.time() if now is None else now
        if start is not None and paused is not None:
            end = paused_at if paused_at is not None else current
            timing = "Run time (excludes pauses): " + _duration(max(0, end - start - paused))
        remaining = _seconds(getattr(app, "_live_estimate_display_min", None))
        if remaining is None:
            remaining = _seconds(getattr(app, "_live_estimate_min", None))
        estimate = _duration(remaining * 60) if remaining is not None else "unavailable"
        timing += ("  |  " if timing else "") + "Estimated remaining: " + estimate
    if getattr(app, "_screen_lock_active", False):
        readiness = "Screen locked — unlock for controls. " + readiness
    return title, readiness, timing


def build_job_summary(app: Any, parent: Any) -> None:
    frame = ttk.Frame(parent, padding=(8, 6))
    frame.pack(fill="x")
    labels = tuple(
        ttk.Label(frame, text="", width=1, anchor="w", justify="left")
        for _ in range(3)
    )
    for label in labels:
        label.pack(fill="x")
    labels[0].configure(font="TkHeadingFont")
    app._job_summary_labels = labels
    app.job_summary_frame = frame

    def resize(event: Any) -> None:
        width = max(120, event.width - 16)
        for label in labels:
            label.configure(wraplength=width)

    frame.bind("<Configure>", resize)
    refresh_job_summary(app, force=True)


def refresh_job_summary(app: Any, *, force: bool = False) -> None:
    labels = getattr(app, "_job_summary_labels", None)
    if not labels or getattr(app, "_closing", False):
        return
    now = time.monotonic()
    if not force and now - getattr(app, "_job_summary_updated_at", 0.0) < 1.0:
        return
    app._job_summary_updated_at = now
    for label, text in zip(labels, summary_text(app)):
        if str(label.cget("text")) != text:
            label.configure(text=text)
