#!/usr/bin/env python3
"""Helpers for CNC-critical tool measurement and compensation math."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Any, Callable, Sequence

DEFAULT_TOOL_CHANGE_REVIEW_THRESHOLD_MM = 25.0
DEFAULT_TOOL_PROBE_SAMPLE_COUNT = 5
DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM = 0.05
CURRENT_TOOL_REFERENCE_FORMAT = "sensor_wz_and_prb_mz_hp5_trimmed_v3"

_FINE_RETRACT_MM = 5.0
_FINE_SETTLE_S = 0.5
_FINE_PROBE_DISTANCE_MM = 6.0
_FINE_PROBE_FEED_MM_MIN = 175.0
_PROBE_REPORT_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class ToolProbeMeasurement:
    machine_z: float
    samples_machine_z: tuple[float, ...]
    trimmed_samples_machine_z: tuple[float, ...]
    spread_mm: float
    sample_count: int


@dataclass(frozen=True)
class ToolChangeCompensation:
    reference_work_z: float
    reference_machine_z: float
    current_machine_z: float
    delta_mm: float
    target_work_z: float


class ToolProbeSpreadExceededError(RuntimeError):
    def __init__(
        self,
        *,
        measurement_label: str,
        result: ToolProbeMeasurement,
        tolerance_mm: float,
    ) -> None:
        self.measurement_label = str(measurement_label)
        self.result = result
        self.spread_mm = float(result.spread_mm)
        self.tolerance_mm = float(tolerance_mm)
        samples_text = ", ".join(f"{value:0.4f}" for value in result.samples_machine_z)
        super().__init__(
            "Tool measurement was inconsistent: "
            f"spread {self.spread_mm:0.4f} mm exceeded tolerance {self.tolerance_mm:0.4f} mm "
            f"across samples [{samples_text}]. Clean/check the tool setter, tool, and wiring, then retry."
        )


def _format_probe_samples(samples_machine_z: Sequence[float]) -> str:
    return ", ".join(f"{float(value):0.4f}" for value in samples_machine_z)


def _emit_measurement_log(log: Callable[[str], Any] | None, message: str) -> None:
    if not callable(log):
        return
    try:
        log(str(message))
    except Exception:
        return


def _probe_report_sequence(probe_controller: Any) -> int:
    try:
        sequence_fn = getattr(probe_controller, "sequence", None)
        if callable(sequence_fn):
            return int(sequence_fn())
    except Exception:
        return -1
    return -1


def _clear_probe_reports(probe_controller: Any) -> None:
    clear_fn = getattr(probe_controller, "clear", None)
    if not callable(clear_fn):
        return
    try:
        clear_fn()
    except Exception:
        return


def _validate_probe_cycle_tuning() -> None:
    if _FINE_RETRACT_MM <= 0.0:
        raise RuntimeError(
            f"Invalid tool probe tuning: fine retract must be positive, got {_FINE_RETRACT_MM:0.3f} mm."
        )
    if _FINE_PROBE_DISTANCE_MM <= 0.0:
        raise RuntimeError(
            "Invalid tool probe tuning: fine probe distance must be positive, "
            f"got {_FINE_PROBE_DISTANCE_MM:0.3f} mm."
        )
    if _FINE_PROBE_DISTANCE_MM < _FINE_RETRACT_MM:
        raise RuntimeError(
            "Invalid tool probe tuning: fine probe distance "
            f"{_FINE_PROBE_DISTANCE_MM:0.3f} mm is shorter than fine retract "
            f"{_FINE_RETRACT_MM:0.3f} mm. Increase fine probe distance or reduce "
            "fine retract before running tool measurement."
        )


def _send_probe_and_wait_for_trip(
    *,
    macro_send: Callable[[str], Any],
    probe_controller: Any,
    command: str,
    timeout_s: float,
    cancel_event: Any = None,
    log: Callable[[str], Any] | None = None,
    phase_label: str,
) -> float:
    report_seq = _probe_report_sequence(probe_controller)
    macro_send(command)
    report = probe_controller.wait_for_report_change(
        report_seq,
        timeout_s,
        cancel_event=cancel_event,
    )
    if report is None:
        _emit_measurement_log(
            log,
            f"{phase_label}: timed out waiting for exact probe-trip report.",
        )
        raise RuntimeError(f"{phase_label} did not produce an exact probe-trip report.")
    try:
        return probe_machine_z(report)
    except RuntimeError as exc:
        _emit_measurement_log(log, f"{phase_label}: invalid probe-trip report.")
        raise RuntimeError(f"{phase_label} returned an invalid probe-trip report.") from exc


def probe_machine_z(report: Any) -> float:
    if report is None:
        raise RuntimeError("Probe report is missing.")
    try:
        ok = bool(getattr(report, "ok"))
    except Exception as exc:
        raise RuntimeError("Probe report is invalid.") from exc
    if not ok:
        raise RuntimeError("Probe report indicates an unsuccessful probe.")
    try:
        return float(getattr(report, "z"))
    except Exception as exc:
        raise RuntimeError("Probe report does not contain a valid machine-Z coordinate.") from exc


def trimmed_probe_average(samples_machine_z: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in samples_machine_z)
    if len(ordered) != DEFAULT_TOOL_PROBE_SAMPLE_COUNT:
        raise RuntimeError(
            f"Expected {DEFAULT_TOOL_PROBE_SAMPLE_COUNT} probe samples, got {len(ordered)}."
        )
    trimmed = ordered[1:-1]
    if len(trimmed) != 3:
        raise RuntimeError("Probe sample trimming did not produce the expected middle three samples.")
    return float(fmean(trimmed))


def probe_spread_mm(samples_machine_z: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in samples_machine_z)
    if not ordered:
        raise RuntimeError("Probe sample set is empty.")
    return float(ordered[-1] - ordered[0])


def build_measurement_result(samples_machine_z: Sequence[float]) -> ToolProbeMeasurement:
    ordered = tuple(sorted(round(float(value), 4) for value in samples_machine_z))
    if len(ordered) != DEFAULT_TOOL_PROBE_SAMPLE_COUNT:
        raise RuntimeError(
            f"Expected {DEFAULT_TOOL_PROBE_SAMPLE_COUNT} probe samples, got {len(ordered)}."
        )
    trimmed = ordered[1:-1]
    average_machine_z = round(float(fmean(trimmed)), 4)
    spread_mm = round(float(ordered[-1] - ordered[0]), 4)
    return ToolProbeMeasurement(
        machine_z=average_machine_z,
        samples_machine_z=ordered,
        trimmed_samples_machine_z=trimmed,
        spread_mm=spread_mm,
        sample_count=len(ordered),
    )


def tool_length_delta_mm(reference_machine_z: float, current_machine_z: float) -> float:
    return float(current_machine_z) - float(reference_machine_z)


def build_tool_change_compensation(
    *,
    reference_work_z: float,
    reference_machine_z: float,
    current_machine_z: float,
) -> ToolChangeCompensation:
    reference_work = float(reference_work_z)
    reference_machine = float(reference_machine_z)
    current_machine = float(current_machine_z)
    delta = tool_length_delta_mm(reference_machine, current_machine)
    return ToolChangeCompensation(
        reference_work_z=reference_work,
        reference_machine_z=reference_machine,
        current_machine_z=current_machine,
        delta_mm=float(delta),
        target_work_z=reference_work,
    )


def delta_exceeds_review_limit(delta_mm: float, limit_mm: float) -> bool:
    return abs(float(delta_mm)) > abs(float(limit_mm))


def collect_high_precision_tool_probe_measurement(
    *,
    macro_send: Callable[[str], Any],
    probe_controller: Any,
    cancel_event: Any = None,
    probe_distance_mm: float,
    rapid_feed_mm_min: float,
    fine_probe_feed_mm_min: float | None = None,
    sample_count: int = DEFAULT_TOOL_PROBE_SAMPLE_COUNT,
    spread_tolerance_mm: float = DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM,
    log: Callable[[str], Any] | None = None,
    measurement_label: str = "Tool measurement",
) -> ToolProbeMeasurement:
    if int(sample_count) != DEFAULT_TOOL_PROBE_SAMPLE_COUNT:
        raise RuntimeError(
            f"Tool probe measurement requires exactly {DEFAULT_TOOL_PROBE_SAMPLE_COUNT} samples."
        )
    _validate_probe_cycle_tuning()
    if probe_controller is None:
        raise RuntimeError("Probe controller is unavailable for tool measurement.")
    _clear_probe_reports(probe_controller)

    label = str(measurement_label or "Tool measurement").strip() or "Tool measurement"
    tolerance = abs(float(spread_tolerance_mm))
    fine_probe_feed = (
        float(_FINE_PROBE_FEED_MM_MIN)
        if fine_probe_feed_mm_min is None
        else float(fine_probe_feed_mm_min)
    )
    if fine_probe_feed <= 0.0:
        raise RuntimeError(
            f"Invalid tool probe tuning: fine probe feed must be positive, got {fine_probe_feed:0.3f} mm/min."
        )
    _emit_measurement_log(
        log,
        (
            f"{label} started: high-precision exact probe mode, "
            f"{DEFAULT_TOOL_PROBE_SAMPLE_COUNT} samples, trim min/max, average middle 3, "
            f"spread tolerance={tolerance:0.4f} mm."
        ),
    )

    samples: list[float] = []
    probe_contact_active = False
    result: ToolProbeMeasurement | None = None
    pending_error: Exception | None = None
    completion_state = "incomplete"
    macro_send("G91")
    try:
        _send_probe_and_wait_for_trip(
            macro_send=macro_send,
            probe_controller=probe_controller,
            command=f"G38.2 Z-{float(probe_distance_mm):0.3f} F{float(rapid_feed_mm_min):0.3f}",
            timeout_s=_PROBE_REPORT_TIMEOUT_S,
            cancel_event=cancel_event,
            log=log,
            phase_label=f"{label} coarse seek",
        )
        probe_contact_active = True
        for idx in range(DEFAULT_TOOL_PROBE_SAMPLE_COUNT):
            macro_send(f"G0 Z{_FINE_RETRACT_MM:0.3f}")
            probe_contact_active = False
            macro_send(f"G4 P{_FINE_SETTLE_S:0.2f}")
            sample_machine_z = _send_probe_and_wait_for_trip(
                macro_send=macro_send,
                probe_controller=probe_controller,
                command=(
                    f"G38.2 Z-{_FINE_PROBE_DISTANCE_MM:0.3f} "
                    f"F{fine_probe_feed:0.3f}"
                ),
                timeout_s=_PROBE_REPORT_TIMEOUT_S,
                cancel_event=cancel_event,
                log=log,
                phase_label=(
                    f"{label} exact sample {idx + 1}/{DEFAULT_TOOL_PROBE_SAMPLE_COUNT}"
                ),
            )
            probe_contact_active = True
            samples.append(sample_machine_z)
            _emit_measurement_log(
                log,
                (
                    f"{label} sample {idx + 1}/{DEFAULT_TOOL_PROBE_SAMPLE_COUNT}: "
                    f"exact probe-trip MZ={sample_machine_z:0.4f}"
                ),
            )
        result = build_measurement_result(samples)
        low_sample = float(result.samples_machine_z[0])
        high_sample = float(result.samples_machine_z[-1])
        _emit_measurement_log(
            log,
            f"{label} samples sorted: [{_format_probe_samples(result.samples_machine_z)}]",
        )
        _emit_measurement_log(
            log,
            f"{label} discarded low/high: low={low_sample:0.4f}, high={high_sample:0.4f}",
        )
        _emit_measurement_log(
            log,
            (
                f"{label} averaged truth (middle 3): MZ={result.machine_z:0.4f} "
                f"from [{_format_probe_samples(result.trimmed_samples_machine_z)}]"
            ),
        )
        _emit_measurement_log(
            log,
            f"{label} spread/tolerance: {result.spread_mm:0.4f} mm / {tolerance:0.4f} mm",
        )
        if result.spread_mm > tolerance:
            _emit_measurement_log(
                log,
                (
                    f"{label} result: rejected; spread {result.spread_mm:0.4f} mm exceeded "
                    f"tolerance {tolerance:0.4f} mm. This measurement round will not be committed."
                ),
            )
            completion_state = "rejected"
            raise ToolProbeSpreadExceededError(
                measurement_label=label,
                result=result,
                tolerance_mm=tolerance,
            )
        _emit_measurement_log(log, f"{label} result: accepted.")
        completion_state = "accepted"
    except Exception as exc:
        if completion_state != "rejected":
            completion_state = "failed"
        pending_error = exc
    finally:
        if probe_contact_active:
            if completion_state == "accepted":
                _emit_measurement_log(
                    log,
                    f"{label} completion: retracting off sensor after accepted measurement.",
                )
            elif completion_state == "rejected":
                _emit_measurement_log(
                    log,
                    f"{label} completion: retracting off sensor before aborting rejected measurement.",
                )
            else:
                _emit_measurement_log(
                    log,
                    f"{label} completion: retracting off sensor before aborting failed measurement.",
                )
            try:
                macro_send(f"G0 Z{_FINE_RETRACT_MM:0.3f}")
                probe_contact_active = False
            except Exception as cleanup_exc:
                _emit_measurement_log(
                    log,
                    f"{label} completion: failed to retract off sensor cleanly: {cleanup_exc}",
                )
                if pending_error is None:
                    pending_error = cleanup_exc
        try:
            macro_send("G90")
        except Exception as cleanup_exc:
            _emit_measurement_log(
                log,
                f"{label} completion: failed to restore absolute mode: {cleanup_exc}",
            )
            if pending_error is None:
                pending_error = cleanup_exc
    if pending_error is not None:
        raise pending_error
    assert result is not None
    return result
