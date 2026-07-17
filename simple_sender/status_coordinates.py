"""Shared fail-closed parsing for GRBL status-coordinate evidence."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


# Standard GRBL status coordinates are reported to three decimal places.
# Three independently rounded values can disagree by up to 0.0015 in the
# controller's report unit, so 0.002 accepts rounding without accepting a
# materially different reported coordinate.
STATUS_COORDINATE_CONSISTENCY_TOLERANCE = 0.002

CoordinateTuple = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class StatusCoordinateEvidence:
    valid: bool
    mpos: CoordinateTuple | None = None
    wpos: CoordinateTuple | None = None
    wco: CoordinateTuple | None = None
    error: str = ""
    normalized_fields: tuple[str, ...] = ()
    axis_count: int = 3

    @property
    def signature(self) -> tuple[
        CoordinateTuple | None,
        CoordinateTuple | None,
        CoordinateTuple | None,
    ] | None:
        if not self.valid:
            return None
        if not any(value is not None for value in (self.mpos, self.wpos, self.wco)):
            return None
        return (self.mpos, self.wpos, self.wco)


def parse_exact_status_xyz(text: str) -> CoordinateTuple | None:
    """Return one finite XYZ tuple; unsupported axis counts fail closed."""
    try:
        values = tuple(float(value.strip()) for value in str(text).split(","))
    except (TypeError, ValueError, OverflowError):
        return None
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        return None
    return (values[0], values[1], values[2])


def parse_status_coordinate_fields(
    fields: Iterable[str],
    *,
    tolerance: float = STATUS_COORDINATE_CONSISTENCY_TOLERANCE,
) -> StatusCoordinateEvidence:
    """Parse one status frame without duplicate or last-value-wins ambiguity."""
    coordinates: dict[str, CoordinateTuple | None] = {
        "MPOS": None,
        "WPOS": None,
        "WCO": None,
    }
    seen: set[str] = set()
    normalized_fields: list[str] = []
    for part in fields:
        field_name, separator, field_value = str(part).partition(":")
        field_upper = field_name.strip().upper()
        if not separator or field_upper not in coordinates:
            continue
        if field_upper in seen:
            return StatusCoordinateEvidence(
                False,
                error=f"duplicate {field_upper} field",
                normalized_fields=tuple(normalized_fields),
            )
        seen.add(field_upper)
        normalized_fields.append(field_upper)
        value = parse_exact_status_xyz(field_value)
        if value is None:
            axis_count = len(str(field_value).split(",")) if field_value else 0
            return StatusCoordinateEvidence(
                False,
                error=f"{field_upper} must contain exactly three finite values",
                normalized_fields=tuple(normalized_fields),
                axis_count=axis_count,
            )
        coordinates[field_upper] = value

    mpos = coordinates["MPOS"]
    wpos = coordinates["WPOS"]
    wco = coordinates["WCO"]
    if mpos is not None and wpos is not None:
        if wco is None:
            return StatusCoordinateEvidence(
                False,
                error="simultaneous MPOS and WPOS require WCO",
                normalized_fields=tuple(normalized_fields),
            )
        if any(
            abs(float(mpos[axis]) - (float(wpos[axis]) + float(wco[axis])))
            > float(tolerance)
            for axis in range(3)
        ):
            return StatusCoordinateEvidence(
                False,
                error="MPOS is inconsistent with WPOS + WCO",
                normalized_fields=tuple(normalized_fields),
            )
    return StatusCoordinateEvidence(
        True,
        mpos=mpos,
        wpos=wpos,
        wco=wco,
        normalized_fields=tuple(normalized_fields),
        axis_count=3,
    )


__all__ = [
    "STATUS_COORDINATE_CONSISTENCY_TOLERANCE",
    "StatusCoordinateEvidence",
    "parse_exact_status_xyz",
    "parse_status_coordinate_fields",
]
