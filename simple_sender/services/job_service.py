"""Bounded job-start service helpers."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class JobStartOutcome(Enum):
    """Explicit outcomes for the bounded job-start workflow."""

    CANCELED = "canceled"
    SETUP_CONFIRMATION_REQUIRED = "setup_confirmation_required"
    START_FAILED = "start_failed"
    STARTED = "started"


class DryRunStartDecision(Enum):
    """Operator-selected Dry Run behavior at job start."""

    CONTINUE_DRY_RUN = "continue_dry_run"
    SWITCH_TO_NORMAL_RUN = "switch_to_normal_run"
    CANCEL = "cancel"


@dataclass(frozen=True)
class JobStartResult:
    """Result returned by the bounded job-start service."""

    outcome: JobStartOutcome
    detail: str | None = None

    @property
    def started(self) -> bool:
        return self.outcome is JobStartOutcome.STARTED


class JobStopOutcome(Enum):
    """Explicit outcomes for the bounded job-stop workflow."""

    NOTHING_ACTIVE = "nothing_active"
    STOP_FAILED = "stop_failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class JobStopResult:
    """Result returned by the bounded job-stop service."""

    outcome: JobStopOutcome
    detail: str | None = None

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
        confirm_dry_run_start: Callable[[Any], DryRunStartDecision] | None = None,
        set_dry_run_enabled: Callable[[Any, bool], None] | None = None,
        now_factory: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._has_valid_job_setup_state = has_valid_job_setup_state
        self._invalidate_job_setup_state = invalidate_job_setup_state
        self._log_suppressed = log_suppressed
        self._confirm_dry_run_start = (
            confirm_dry_run_start
            if confirm_dry_run_start is not None
            else self._default_confirm_dry_run_start
        )
        self._set_dry_run_enabled = (
            set_dry_run_enabled
            if set_dry_run_enabled is not None
            else self._default_set_dry_run_enabled
        )
        self._now_factory = now_factory

    def start_job(
        self,
        app: Any,
        *,
        allow_start_without_setup: bool = False,
    ) -> JobStartResult:
        if not allow_start_without_setup and not self._has_valid_job_setup_state(app):
            return JobStartResult(JobStartOutcome.SETUP_CONFIRMATION_REQUIRED)
        dry_run_guard_result = self._resolve_dry_run_start_guard(app)
        if dry_run_guard_result is not None:
            return dry_run_guard_result
        self._reset_accessory_router_state(app)
        self._apply_stream_start_settings(app)
        app.grbl.start_stream()
        started = self._stream_started(app)
        if not started:
            return JobStartResult(
                JobStartOutcome.START_FAILED,
                detail=self._build_start_failure_detail(app),
            )
        self._reset_stream_progress_state(app)
        self._apply_post_start_bookkeeping(app)
        return JobStartResult(JobStartOutcome.STARTED)

    def stop_job(self, app: Any) -> JobStopResult:
        if not self._job_has_real_active_or_finishing_state(app):
            return JobStopResult(
                JobStopOutcome.NOTHING_ACTIVE,
                detail="No active job was running.",
            )
        stop_result = app.grbl.stop_stream()
        if stop_result is False:
            return JobStopResult(
                JobStopOutcome.STOP_FAILED,
                detail="GRBL did not accept the Stop Job request.",
            )
        if self._job_has_real_active_or_finishing_state(app):
            return JobStopResult(
                JobStopOutcome.STOP_FAILED,
                detail="GRBL did not leave its active job state after Stop Job.",
            )
        self._stop_job_accessories(app)
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

    def _resolve_dry_run_start_guard(self, app: Any) -> JobStartResult | None:
        if not self._is_dry_run_enabled(app):
            return None
        try:
            decision = self._confirm_dry_run_start(app)
        except Exception as exc:
            self._log_suppressed("Failed confirming Dry Run start mode", exc)
            return JobStartResult(
                JobStartOutcome.CANCELED,
                detail="Dry Run confirmation could not be completed.",
            )
        if decision is DryRunStartDecision.CONTINUE_DRY_RUN:
            return None
        if decision is DryRunStartDecision.SWITCH_TO_NORMAL_RUN:
            try:
                self._set_dry_run_enabled(app, False)
            except Exception as exc:
                self._log_suppressed("Failed switching Dry Run setting before Run", exc)
                return JobStartResult(
                    JobStartOutcome.CANCELED,
                    detail="Dry Run could not be switched off before Run.",
                )
            return None
        return JobStartResult(
            JobStartOutcome.CANCELED,
            detail="Run canceled before job start.",
        )

    @staticmethod
    def _is_dry_run_enabled(app: Any) -> bool:
        dry_run_var = getattr(app, "dry_run_sanitize_stream", None)
        getter = getattr(dry_run_var, "get", None)
        if callable(getter):
            return bool(getter())
        return bool(dry_run_var)

    @staticmethod
    def _default_confirm_dry_run_start(_app: Any) -> DryRunStartDecision:
        return DryRunStartDecision.CONTINUE_DRY_RUN

    @staticmethod
    def _default_set_dry_run_enabled(app: Any, enabled: bool) -> None:
        dry_run_enabled = bool(enabled)
        dry_run_var = getattr(app, "dry_run_sanitize_stream", None)
        setter = getattr(dry_run_var, "set", None)
        if callable(setter):
            setter(dry_run_enabled)
        settings = getattr(app, "settings", None)
        if isinstance(settings, dict):
            settings["dry_run_sanitize_stream"] = dry_run_enabled
        grbl = getattr(app, "grbl", None)
        runtime_setter = getattr(grbl, "set_dry_run_sanitize", None)
        if callable(runtime_setter):
            runtime_setter(dry_run_enabled)

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

    def _build_start_failure_detail(self, app: Any) -> str:
        try:
            if not bool(app.grbl.is_connected()):
                return "GRBL disconnected before the job could start."
        except Exception as exc:
            self._log_suppressed("Failed checking GRBL connection state after Run", exc)
        if not bool(getattr(app, "_grbl_ready", True)):
            return "GRBL is no longer ready to stream."
        return "GRBL did not enter streaming state after the Run command."

    def _apply_post_start_bookkeeping(self, app: Any) -> None:
        app._reset_gcode_view_for_run()
        app._job_started_at = self._now_factory()
        app._job_completion_notified = False
        app._job_completion_finalize_pending = False
        streaming_controller = getattr(app, "streaming_controller", None)
        if streaming_controller is not None:
            log_job_started = getattr(streaming_controller, "log_job_started", None)
            if callable(log_job_started):
                try:
                    run_type = (
                        "dry run"
                        if self._is_dry_run_enabled(app)
                        else "normal"
                    )
                    log_job_started(run_type=run_type, start_index=0)
                except Exception as exc:
                    self._log_suppressed("Failed logging job start lifecycle entry", exc)
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

    def _job_has_real_active_or_finishing_state(self, app: Any) -> bool:
        try:
            if bool(app.grbl.is_streaming()):
                return True
        except Exception as exc:
            self._log_suppressed("Failed checking GRBL streaming state for Stop Job", exc)
        if bool(getattr(app, "_stream_done_pending_idle", False)):
            return True
        return False
