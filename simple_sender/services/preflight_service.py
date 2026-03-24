"""Bounded preflight validation service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

_TRAVEL_COMPARE_EPSILON = 1e-6
_AXIS_SETTINGS: tuple[tuple[str, str], ...] = (
    ("x", "$130"),
    ("y", "$131"),
    ("z", "$132"),
)


class PreflightOutcome(Enum):
    """Primary outcome for a job preflight evaluation."""

    VALID = "valid"
    NO_JOB = "no_job"
    BLOCKED = "blocked"
    OUT_OF_BOUNDS = "out_of_bounds"
    LIMITS_UNAVAILABLE = "limits_unavailable"


@dataclass(frozen=True, slots=True)
class BoundsInfo:
    """Normalized six-value job bounds."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    @property
    def span_x(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def span_y(self) -> float:
        return max(0.0, self.max_y - self.min_y)

    @property
    def span_z(self) -> float:
        return max(0.0, self.max_z - self.min_z)

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.min_x,
            self.max_x,
            self.min_y,
            self.max_y,
            self.min_z,
            self.max_z,
        )


@dataclass(frozen=True, slots=True)
class TravelLimits:
    """Normalized machine travel settings."""

    x: float | None = None
    y: float | None = None
    z: float | None = None

    @property
    def available(self) -> bool:
        return self.x is not None or self.y is not None or self.z is not None

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.x is not None:
            out["x"] = self.x
        if self.y is not None:
            out["y"] = self.y
        if self.z is not None:
            out["z"] = self.z
        return out


@dataclass(frozen=True, slots=True)
class TravelViolation:
    """Structured machine-travel overrun details."""

    axis: str
    setting_key: str
    span_mm: float
    limit_mm: float
    message: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Structured preflight validation result."""

    outcome: PreflightOutcome
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    bounds: BoundsInfo | None = None
    travel_limits: TravelLimits = TravelLimits()
    violations: tuple[TravelViolation, ...] = ()
    has_job: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures


class PreflightService:
    """Evaluate loaded-job readiness without UI coupling."""

    def __init__(
        self,
        *,
        get_bounds: Callable[[Any], Any] | None = None,
        get_travel_limits: Callable[[Any], Any] | None = None,
    ) -> None:
        self._get_bounds = get_bounds
        self._get_travel_limits = get_travel_limits

    def get_bounds(self, app: Any) -> BoundsInfo | None:
        raw = (
            self._get_bounds(app)
            if self._get_bounds is not None
            else self._default_get_bounds(app)
        )
        return self._coerce_bounds(raw)

    def get_travel_limits(self, app: Any) -> TravelLimits:
        raw = (
            self._get_travel_limits(app)
            if self._get_travel_limits is not None
            else self._default_get_travel_limits(app)
        )
        return self._coerce_travel_limits(raw)

    def validate_job(self, app: Any) -> PreflightResult:
        failures: list[str] = []
        warnings: list[str] = []
        has_job = self._has_loaded_job(app)
        if not has_job:
            failures.append("No G-code job is loaded.")
            return PreflightResult(
                outcome=PreflightOutcome.NO_JOB,
                failures=tuple(failures),
                warnings=tuple(warnings),
                has_job=False,
            )

        failures.extend(self._state_failures(app))

        bounds = self.get_bounds(app)
        travel_limits = self.get_travel_limits(app)
        violations: tuple[TravelViolation, ...] = ()

        if bounds is None:
            failures.append("Job bounds are unavailable (wait for parsing to complete).")
        else:
            violations = self._build_travel_violations(bounds, travel_limits)
            if violations:
                failures.extend(violation.message for violation in violations)
            elif not travel_limits.available:
                warnings.append("Machine travel settings ($130/$131/$132) are unavailable.")

        outcome = self._resolve_outcome(
            has_job=has_job,
            failures=failures,
            warnings=warnings,
            violations=violations,
        )
        return PreflightResult(
            outcome=outcome,
            failures=tuple(failures),
            warnings=tuple(warnings),
            bounds=bounds,
            travel_limits=travel_limits,
            violations=violations,
            has_job=has_job,
        )

    @staticmethod
    def _default_get_bounds(app: Any) -> tuple[float, float, float, float, float, float] | None:
        parse_result = getattr(app, "_last_parse_result", None)
        bounds = getattr(parse_result, "bounds", None) if parse_result else None
        if not bounds:
            quick_bounds = getattr(app, "_gcode_bounds_box", None)
            if isinstance(quick_bounds, dict):
                try:
                    bounds = (
                        float(quick_bounds.get("min_x", 0.0) or 0.0),
                        float(quick_bounds.get("max_x", 0.0) or 0.0),
                        float(quick_bounds.get("min_y", 0.0) or 0.0),
                        float(quick_bounds.get("max_y", 0.0) or 0.0),
                        float(quick_bounds.get("min_z", 0.0) or 0.0),
                        float(quick_bounds.get("max_z", 0.0) or 0.0),
                    )
                except Exception:
                    bounds = None
        if not bounds or len(bounds) < 6:
            return None
        return bounds

    @staticmethod
    def _default_get_travel_limits(app: Any) -> dict[str, float]:
        data = (
            getattr(getattr(app, "settings_controller", None), "_settings_data", {}) or {}
        )
        out: dict[str, float] = {}
        for setting_key, axis in (("$130", "x"), ("$131", "y"), ("$132", "z")):
            raw = data.get(setting_key)
            if not raw:
                continue
            try:
                out[axis] = float(raw[0])
            except Exception:
                continue
        return out

    @staticmethod
    def _coerce_bounds(raw: Any) -> BoundsInfo | None:
        if isinstance(raw, BoundsInfo):
            return raw
        if not isinstance(raw, (list, tuple)) or len(raw) < 6:
            return None
        try:
            return BoundsInfo(
                float(raw[0]),
                float(raw[1]),
                float(raw[2]),
                float(raw[3]),
                float(raw[4]),
                float(raw[5]),
            )
        except Exception:
            return None

    @staticmethod
    def _coerce_travel_limits(raw: Any) -> TravelLimits:
        if isinstance(raw, TravelLimits):
            return raw
        if not isinstance(raw, dict):
            return TravelLimits()

        def _read(axis: str) -> float | None:
            value = raw.get(axis)
            if value is None:
                return None
            try:
                return float(value)
            except Exception:
                return None

        return TravelLimits(
            x=_read("x"),
            y=_read("y"),
            z=_read("z"),
        )

    @staticmethod
    def _has_loaded_job(app: Any) -> bool:
        path = getattr(app, "_last_gcode_path", None)
        has_job = bool(path)
        if not has_job:
            try:
                has_job = bool(getattr(app.gview, "lines_count", 0))
            except Exception:
                has_job = False
        return has_job

    @staticmethod
    def _state_failures(app: Any) -> list[str]:
        failures: list[str] = []
        if bool(getattr(app, "_alarm_locked", False)):
            failures.append("Controller is in Alarm state. Clear alarm before running.")
        if bool(getattr(app, "_homing_in_progress", False)):
            failures.append("Homing is currently active. Wait until homing finishes.")
        if not bool(getattr(app, "_grbl_ready", False)):
            failures.append("GRBL is not ready yet. Wait for startup/status sync.")
        if not bool(getattr(app, "_status_seen", False)):
            failures.append("No live status has been received yet.")
        return failures

    @staticmethod
    def _build_travel_violations(
        bounds: BoundsInfo,
        travel_limits: TravelLimits,
    ) -> tuple[TravelViolation, ...]:
        if not travel_limits.available:
            return ()

        spans = {
            "x": bounds.span_x,
            "y": bounds.span_y,
            "z": bounds.span_z,
        }
        limits = {
            "x": travel_limits.x,
            "y": travel_limits.y,
            "z": travel_limits.z,
        }
        violations: list[TravelViolation] = []
        for axis, setting_key in _AXIS_SETTINGS:
            limit_mm = limits.get(axis)
            if limit_mm is None:
                continue
            span_mm = spans[axis]
            if span_mm > limit_mm + _TRAVEL_COMPARE_EPSILON:
                violations.append(
                    TravelViolation(
                        axis=axis,
                        setting_key=setting_key,
                        span_mm=span_mm,
                        limit_mm=limit_mm,
                        message=(
                            f"{axis.upper()} span {span_mm:.3f} mm exceeds machine travel "
                            f"{setting_key}={limit_mm:.3f} mm."
                        ),
                    )
                )
        return tuple(violations)

    @staticmethod
    def _resolve_outcome(
        *,
        has_job: bool,
        failures: list[str],
        warnings: list[str],
        violations: tuple[TravelViolation, ...],
    ) -> PreflightOutcome:
        if not has_job:
            return PreflightOutcome.NO_JOB
        if failures:
            non_violation_failure_count = len(failures) - len(violations)
            if violations and non_violation_failure_count <= 0:
                return PreflightOutcome.OUT_OF_BOUNDS
            return PreflightOutcome.BLOCKED
        if warnings:
            return PreflightOutcome.LIMITS_UNAVAILABLE
        return PreflightOutcome.VALID


__all__ = [
    "BoundsInfo",
    "PreflightOutcome",
    "PreflightResult",
    "PreflightService",
    "TravelLimits",
    "TravelViolation",
]
