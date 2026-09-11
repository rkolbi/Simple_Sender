"""Bounded preflight validation service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Any, Callable

_TRAVEL_COMPARE_EPSILON = 1e-6
_PLACEMENT_COMPARE_EPSILON_MM = 0.002
_TRAVEL_LIMITS_UNAVAILABLE_WARNING = (
    "Machine travel settings ($130/$131/$132) are unavailable, so Simple Sender "
    "cannot compare job span to machine travel."
)
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
    PLACEMENT_UNVERIFIABLE = "placement_unverifiable"


class PlacementOutcome(Enum):
    """Whether exact job placement can be proven inside the machine envelope."""

    VERIFIED_SAFE = "verified_safe"
    VERIFIED_VIOLATION = "verified_violation"
    UNVERIFIABLE = "unverifiable"


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
class PlacementViolation:
    """A projected work-coordinate range outside one GRBL machine axis."""

    axis: str
    setting_key: str
    machine_min_mm: float
    machine_max_mm: float
    envelope_min_mm: float
    envelope_max_mm: float
    message: str


@dataclass(frozen=True, slots=True)
class PlacementResult:
    """Exact placement outcome kept distinct from the span comparison."""

    outcome: PlacementOutcome
    reasons: tuple[str, ...] = ()
    work_bounds: BoundsInfo | None = None
    machine_bounds: BoundsInfo | None = None
    violations: tuple[PlacementViolation, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Structured preflight validation result."""

    outcome: PreflightOutcome
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    bounds: BoundsInfo | None = None
    travel_limits: TravelLimits = TravelLimits()
    violations: tuple[TravelViolation, ...] = ()
    placement: PlacementResult = PlacementResult(PlacementOutcome.UNVERIFIABLE)
    has_job: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures


class PreflightService:
    """Evaluate loaded-job readiness, travel span, and exact placement."""

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
        """Return the readiness and envelope result for the loaded job."""
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
        placement = PlacementResult(PlacementOutcome.UNVERIFIABLE)

        if bounds is None:
            failures.append("Job bounds are unavailable (wait for parsing to complete).")
        else:
            violations = self._build_travel_violations(bounds, travel_limits)
            if violations:
                failures.extend(violation.message for violation in violations)
            elif not travel_limits.available:
                warnings.append(_TRAVEL_LIMITS_UNAVAILABLE_WARNING)

        placement = self.evaluate_placement(app, travel_limits=travel_limits)
        if placement.outcome is PlacementOutcome.VERIFIED_VIOLATION:
            failures.extend(violation.message for violation in placement.violations)
        elif placement.outcome is PlacementOutcome.UNVERIFIABLE:
            reason_text = "; ".join(placement.reasons) or "required evidence is unavailable"
            warnings.append(
                "Job placement inside the machine envelope is unverifiable: "
                f"{reason_text}."
            )

        outcome = self._resolve_outcome(
            has_job=has_job,
            failures=failures,
            warnings=warnings,
            violations=violations,
            placement=placement,
        )
        return PreflightResult(
            outcome=outcome,
            failures=tuple(failures),
            warnings=tuple(warnings),
            bounds=bounds,
            travel_limits=travel_limits,
            violations=violations,
            placement=placement,
            has_job=has_job,
        )

    def evaluate_placement(
        self,
        app: Any,
        *,
        travel_limits: TravelLimits | None = None,
    ) -> PlacementResult:
        """Project validated work-coordinate targets into standard GRBL MPos."""
        reasons: list[str] = []

        def add_reason(reason: str) -> None:
            if reason not in reasons:
                reasons.append(reason)

        limits = travel_limits if travel_limits is not None else self.get_travel_limits(app)
        limit_values = (limits.x, limits.y, limits.z)
        if not all(
            value is not None and math.isfinite(float(value)) and float(value) > 0.0
            for value in limit_values
        ):
            add_reason("complete positive $130/$131/$132 travel settings are unavailable")

        report = getattr(app, "_gcode_validation_report", None)
        analysis = getattr(report, "placement", None) if report is not None else None
        if analysis is None:
            add_reason("complete placement analysis is unavailable for this job")
        report_digest = str(getattr(report, "snapshot_sha256", "") or "")
        source = getattr(app, "_gcode_source", None)
        source_digest = str(getattr(source, "snapshot_sha256", "") or "")
        app_digest = str(getattr(app, "_gcode_hash", "") or "")
        if not report_digest or not source_digest:
            add_reason("placement analysis is not bound to a validated job snapshot")
        elif report_digest != source_digest or (app_digest and app_digest != report_digest):
            add_reason("placement analysis does not match the loaded job snapshot")

        hazards = tuple(getattr(analysis, "hazards", ()) or ())
        for hazard in hazards:
            add_reason(str(hazard))

        trust_requirements = (
            ("_machine_coordinates_trusted", "machine position is not trusted"),
            ("_modal_state_trusted", "modal state is not trusted"),
            ("_work_offsets_trusted", "work-coordinate offset is not trusted"),
            ("_g92_trusted", "G92 state is not trusted"),
            ("_tool_length_offset_trusted", "tool-length offset is not trusted"),
        )
        for attr, reason in trust_requirements:
            if not bool(getattr(app, attr, False)):
                add_reason(reason)

        machine_position = self._coerce_triplet(getattr(app, "_mpos_raw", None))
        work_coordinate_offset = self._coerce_triplet(getattr(app, "_wco_raw", None))
        if machine_position is None:
            add_reason("current machine position is unavailable")
        if work_coordinate_offset is None:
            add_reason("current work-coordinate offset is unavailable")
        self._check_position_freshness(app, add_reason)

        machine_snapshot = getattr(app, "_recovery_snapshot", None)
        active_wcs = str(getattr(machine_snapshot, "active_wcs", "") or "").upper()
        modal_units = str(getattr(machine_snapshot, "modal_units", "") or "").upper()
        distance_mode = str(
            getattr(machine_snapshot, "distance_mode", "") or ""
        ).upper()
        motion_mode = str(getattr(machine_snapshot, "motion_mode", "") or "").upper()
        if machine_snapshot is None:
            add_reason("current automatic machine-state snapshot is unavailable")
        else:
            self._check_snapshot_provenance(app, machine_snapshot, add_reason)

        referenced_wcs = tuple(
            str(value).upper()
            for value in (getattr(analysis, "work_coordinate_systems", ()) or ())
        )
        if referenced_wcs and (not active_wcs or referenced_wcs != (active_wcs,)):
            add_reason(
                "job work-coordinate selection does not match the current active WCS"
            )
        if bool(getattr(analysis, "requires_initial_units", False)) and modal_units != "G21":
            add_reason("motion before G20/G21 cannot be resolved as millimetres")
        if (
            bool(getattr(analysis, "requires_initial_distance_mode", False))
            and distance_mode != "G90"
        ):
            add_reason("motion before G90/G91 does not start in absolute mode")
        if (
            bool(getattr(analysis, "requires_initial_motion_mode", False))
            and motion_mode not in {"G0", "G1"}
        ):
            add_reason("axis motion before G0/G1 depends on an unsafe initial motion mode")

        programmed_bounds = self._coerce_programmed_bounds(
            getattr(analysis, "programmed_bounds_mm", None)
        )
        if programmed_bounds is None or int(
            getattr(analysis, "motion_line_count", 0) or 0
        ) <= 0:
            add_reason("no exact linear-motion bounds are available")

        if reasons:
            return PlacementResult(
                PlacementOutcome.UNVERIFIABLE,
                reasons=tuple(reasons),
            )

        assert machine_position is not None
        assert work_coordinate_offset is not None
        assert programmed_bounds is not None
        assert limits.x is not None and limits.y is not None and limits.z is not None
        verified_limits = (float(limits.x), float(limits.y), float(limits.z))
        current_work_position = tuple(
            machine_position[idx] - work_coordinate_offset[idx] for idx in range(3)
        )
        work_ranges: list[tuple[float, float]] = []
        for axis_index in range(3):
            programmed_min = programmed_bounds[axis_index * 2]
            programmed_max = programmed_bounds[(axis_index * 2) + 1]
            current_value = current_work_position[axis_index]
            values = [current_value]
            if programmed_min is not None:
                values.append(programmed_min)
            if programmed_max is not None:
                values.append(programmed_max)
            work_ranges.append((min(values), max(values)))

        work_bounds = BoundsInfo(
            work_ranges[0][0],
            work_ranges[0][1],
            work_ranges[1][0],
            work_ranges[1][1],
            work_ranges[2][0],
            work_ranges[2][1],
        )
        machine_ranges = tuple(
            (
                work_ranges[idx][0] + work_coordinate_offset[idx],
                work_ranges[idx][1] + work_coordinate_offset[idx],
            )
            for idx in range(3)
        )
        machine_bounds = BoundsInfo(
            machine_ranges[0][0],
            machine_ranges[0][1],
            machine_ranges[1][0],
            machine_ranges[1][1],
            machine_ranges[2][0],
            machine_ranges[2][1],
        )

        violations: list[PlacementViolation] = []
        for axis_index, (axis, setting_key) in enumerate(_AXIS_SETTINGS):
            limit_mm = verified_limits[axis_index]
            machine_min, machine_max = machine_ranges[axis_index]
            envelope_min = -limit_mm
            envelope_max = 0.0
            if (
                machine_min < envelope_min - _PLACEMENT_COMPARE_EPSILON_MM
                or machine_max > envelope_max + _PLACEMENT_COMPARE_EPSILON_MM
            ):
                violations.append(
                    PlacementViolation(
                        axis=axis,
                        setting_key=setting_key,
                        machine_min_mm=machine_min,
                        machine_max_mm=machine_max,
                        envelope_min_mm=envelope_min,
                        envelope_max_mm=envelope_max,
                        message=(
                            f"{axis.upper()} placement projects to machine range "
                            f"{machine_min:.3f}..{machine_max:.3f} mm outside "
                            f"{envelope_min:.3f}..{envelope_max:.3f} mm "
                            f"({setting_key}={limit_mm:.3f} mm)."
                        ),
                    )
                )
        if violations:
            return PlacementResult(
                PlacementOutcome.VERIFIED_VIOLATION,
                work_bounds=work_bounds,
                machine_bounds=machine_bounds,
                violations=tuple(violations),
            )
        return PlacementResult(
            PlacementOutcome.VERIFIED_SAFE,
            work_bounds=work_bounds,
            machine_bounds=machine_bounds,
        )

    @staticmethod
    def _coerce_triplet(raw: Any) -> tuple[float, float, float] | None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            return None
        try:
            values = (float(raw[0]), float(raw[1]), float(raw[2]))
        except (TypeError, ValueError):
            return None
        return values if all(math.isfinite(value) for value in values) else None

    @staticmethod
    def _coerce_programmed_bounds(
        raw: Any,
    ) -> tuple[
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
    ] | None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 6:
            return None
        values: list[float | None] = []
        for value in raw:
            if value is None:
                values.append(None)
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(parsed):
                return None
            values.append(parsed)
        return (
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
        )

    @staticmethod
    def _check_snapshot_provenance(app: Any, snapshot: Any, add_reason) -> None:
        worker = getattr(app, "grbl", None)
        generation_getter = getattr(worker, "connection_generation", None)
        recovery_getter = getattr(worker, "recovery_epoch", None)
        try:
            if callable(generation_getter) and int(snapshot.connection_generation) != int(
                generation_getter()
            ):
                add_reason("machine-state snapshot belongs to an older connection")
            if callable(recovery_getter) and int(snapshot.recovery_epoch) != int(
                recovery_getter()
            ):
                add_reason("machine-state snapshot belongs to an older recovery epoch")
        except Exception:
            add_reason("machine-state snapshot provenance cannot be verified")

    @staticmethod
    def _check_position_freshness(app: Any, add_reason) -> None:
        state = str(getattr(app, "_machine_state_text", "") or "").lower()
        if not state.startswith("idle"):
            add_reason("the latest automatic status does not show the machine as Idle")
        try:
            installed_at = float(
                getattr(app, "_status_last_installed_coordinate_ts", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            installed_at = 0.0
        if installed_at <= 0.0:
            add_reason("fresh automatically reported coordinates are unavailable")
            return
        try:
            poll_var = getattr(app, "status_poll_interval", None)
            getter = getattr(poll_var, "get", None)
            poll_seconds = float(getter()) if callable(getter) else 0.25
        except (TypeError, ValueError):
            poll_seconds = 0.25
        max_age_seconds = max(1.0, poll_seconds * 4.0)
        if time.monotonic() - installed_at > max_age_seconds:
            add_reason("the latest automatically reported coordinates are stale")

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
        placement: PlacementResult,
    ) -> PreflightOutcome:
        if not has_job:
            return PreflightOutcome.NO_JOB
        if failures:
            non_violation_failure_count = len(failures) - len(violations)
            if violations and non_violation_failure_count <= 0:
                return PreflightOutcome.OUT_OF_BOUNDS
            return PreflightOutcome.BLOCKED
        if warnings:
            if placement.outcome is PlacementOutcome.UNVERIFIABLE:
                return PreflightOutcome.PLACEMENT_UNVERIFIABLE
            return PreflightOutcome.LIMITS_UNAVAILABLE
        return PreflightOutcome.VALID


__all__ = [
    "BoundsInfo",
    "PlacementOutcome",
    "PlacementResult",
    "PlacementViolation",
    "PreflightOutcome",
    "PreflightResult",
    "PreflightService",
    "TravelLimits",
    "TravelViolation",
]
