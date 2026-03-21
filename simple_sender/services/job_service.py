"""Bounded job-start service helpers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class JobStartOutcome(Enum):
    """Explicit outcomes for the bounded job-start workflow."""

    SETUP_CONFIRMATION_REQUIRED = "setup_confirmation_required"
    START_FAILED = "start_failed"
    STARTED = "started"


@dataclass(frozen=True)
class JobStartResult:
    """Result returned by the bounded job-start service."""

    outcome: JobStartOutcome

    @property
    def started(self) -> bool:
        return self.outcome is JobStartOutcome.STARTED


class JobStopOutcome(Enum):
    """Explicit outcomes for the bounded job-stop workflow."""

    STOPPED = "stopped"


@dataclass(frozen=True)
class JobStopResult:
    """Result returned by the bounded job-stop service."""

    outcome: JobStopOutcome

    @property
    def stopped(self) -> bool:
        return self.outcome is JobStopOutcome.STOPPED


class JobService:
    """Bounded business logic for starting and stopping a job."""

    def __init__(
        self,
        *,
        has_valid_job_setup_state: Callable[[Any], bool],
        invalidate_job_setup_state: Callable[[Any], None],
        log_suppressed: Callable[[str, BaseException], None],
        now_factory: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._has_valid_job_setup_state = has_valid_job_setup_state
        self._invalidate_job_setup_state = invalidate_job_setup_state
        self._log_suppressed = log_suppressed
        self._now_factory = now_factory

    def start_job(
        self,
        app: Any,
        *,
        allow_start_without_setup: bool = False,
    ) -> JobStartResult:
        if not allow_start_without_setup and not self._has_valid_job_setup_state(app):
            return JobStartResult(JobStartOutcome.SETUP_CONFIRMATION_REQUIRED)
        self._reset_accessory_router_state(app)
        self._apply_stream_start_settings(app)
        self._reset_stream_progress_state(app)
        app.grbl.start_stream()
        started = self._stream_started(app)
        if not started:
            return JobStartResult(JobStartOutcome.START_FAILED)
        self._apply_post_start_bookkeeping(app)
        return JobStartResult(JobStartOutcome.STARTED)

    def stop_job(self, app: Any) -> JobStopResult:
        self._stop_job_accessories(app)
        app.grbl.stop_stream()
        self._invalidate_job_setup_state(app)
        return JobStopResult(JobStopOutcome.STOPPED)

    def _reset_accessory_router_state(self, app: Any) -> None:
        try:
            app._kasa_last_stream_line_index = -1
        except Exception as exc:
            self._log_suppressed("Failed resetting Kasa stream-line index before run", exc)
        accessory_router = getattr(app, "accessory_router", None)
        if accessory_router is None:
            return
        reset_debounce = getattr(accessory_router, "reset_debounce", None)
        if not callable(reset_debounce):
            return
        try:
            reset_debounce()
        except Exception as exc:
            self._log_suppressed("Failed resetting Kasa debounce state before run", exc)

    def _apply_stream_start_settings(self, app: Any) -> None:
        app.grbl.set_dry_run_sanitize(bool(app.dry_run_sanitize_stream.get()))
        app._reset_gcode_view_for_run()

    def _reset_stream_progress_state(self, app: Any) -> None:
        try:
            app._stream_acked_byte_offset = 0
            app._stream_progress_pct = 0.0
            app._stream_progress_file_size_bytes = max(
                0,
                int(getattr(app, "_gcode_file_size_bytes", 0) or 0),
            )
        except Exception as exc:
            self._log_suppressed("Failed resetting stream byte-progress state before Run", exc)
        try:
            app.progress_pct.set(0)
        except Exception as exc:
            self._log_suppressed("Failed resetting progress bar before Run", exc)
        try:
            if hasattr(app, "progress_text"):
                app.progress_text.set("")
        except Exception as exc:
            self._log_suppressed("Failed resetting progress label before Run", exc)

    def _stream_started(self, app: Any) -> bool:
        started = False
        try:
            started = bool(app.grbl.is_streaming())
        except Exception as exc:
            self._log_suppressed("Failed checking GRBL streaming state after Run", exc)
        return started

    def _apply_post_start_bookkeeping(self, app: Any) -> None:
        app._job_started_at = self._now_factory()
        app._job_completion_notified = False
        try:
            if hasattr(app, "_start_job_accessories"):
                app._start_job_accessories("job_run")
        except Exception as exc:
            self._log_suppressed("Failed starting Kasa job accessories on Run", exc)

    def _stop_job_accessories(self, app: Any) -> None:
        try:
            if hasattr(app, "_stop_job_accessories"):
                app._stop_job_accessories("job_stop")
        except Exception as exc:
            self._log_suppressed("Failed stopping Kasa job accessories on Stop/Reset", exc)
