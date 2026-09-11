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

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import re
from typing import Iterable

from simple_sender.gcode_parser import WORD_PAT
from simple_sender.utils.constants import MAX_LINE_LENGTH

DETAIL_LINE_LIMIT = 200
DETAIL_LINE_TEXT_LIMIT = 160
DISTINCT_CODE_LIMIT = 100
ADDITIONAL_CODE_KEY = "<additional distinct codes>"


SUPPORTED_G_CODES = {
    0.0,
    1.0,
    2.0,
    3.0,
    4.0,
    10.0,
    17.0,
    18.0,
    19.0,
    20.0,
    21.0,
    28.0,
    28.1,
    30.0,
    30.1,
    38.2,
    38.3,
    38.4,
    38.5,
    40.0,
    43.1,
    49.0,
    53.0,
    54.0,
    55.0,
    56.0,
    57.0,
    58.0,
    59.0,
    59.1,
    59.2,
    59.3,
    61.0,
    80.0,
    90.0,
    91.0,
    91.1,
    92.0,
    92.1,
    92.2,
    92.3,
    93.0,
    94.0,
    90.1,
}
SUPPORTED_M_CODES = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    30,
}
KNOWN_WORD_LETTERS = {
    "G",
    "M",
    "X",
    "Y",
    "Z",
    "I",
    "J",
    "K",
    "R",
    "F",
    "S",
    "T",
    "P",
    "L",
    "N",
    "Q",
}
UNSUPPORTED_AXES = {"A", "B", "C", "U", "V", "W"}
MODAL_HAZARDS = {
    91.0: "G91 (incremental distance mode)",
    93.0: "G93 (inverse time feed mode)",
    92.0: "G92 offsets",
    92.1: "G92 offsets",
    92.2: "G92 offsets",
    92.3: "G92 offsets",
}
GRBL_WARN_G_CODES = {
    90.1: "G90.1 (arc center absolute) is not supported by GRBL 1.1h",
}


@dataclass
class GcodeValidationLineIssue:
    """Line-level validation issues for reporting."""
    line_no: int
    line: str
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GcodePlacementAnalysis:
    """Bounded placement facts collected during complete command validation."""

    programmed_bounds_mm: tuple[
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
    ] | None = None
    motion_line_count: int = 0
    hazards: tuple[str, ...] = ()
    work_coordinate_systems: tuple[str, ...] = ()
    requires_initial_units: bool = False
    requires_initial_distance_mode: bool = False
    requires_initial_motion_mode: bool = False


@dataclass
class GcodeValidationReport:
    """Aggregate validation report for a full G-code file."""
    total_lines: int
    long_line_count: int
    long_lines: list[tuple[int, int]]
    unsupported_axes: Counter[str]
    unsupported_words: Counter[str]
    unsupported_g_codes: Counter[str]
    unsupported_m_codes: Counter[str]
    grbl_warnings: Counter[str]
    modal_hazards: set[str]
    line_issue_count: int
    line_issues: list[GcodeValidationLineIssue]
    line_issues_truncated: bool
    malformed_line_count: int = 0
    placement: GcodePlacementAnalysis = field(default_factory=GcodePlacementAnalysis)
    snapshot_sha256: str = ""


@dataclass(slots=True)
class _PlacementAccumulator:
    unit_scale: float | None = None
    distance_mode: str | None = None
    motion_mode: float | None = None
    min_values: list[float] = field(
        default_factory=lambda: [math.inf, math.inf, math.inf]
    )
    max_values: list[float] = field(
        default_factory=lambda: [-math.inf, -math.inf, -math.inf]
    )
    motion_line_count: int = 0
    hazards: set[str] = field(default_factory=set)
    work_coordinate_systems: set[str] = field(default_factory=set)
    requires_initial_units: bool = False
    requires_initial_distance_mode: bool = False
    requires_initial_motion_mode: bool = False

    def finish(self) -> GcodePlacementAnalysis:
        bounds = None
        if self.motion_line_count > 0:
            axis_bounds: list[float | None] = []
            for idx in range(3):
                if math.isfinite(self.min_values[idx]) and math.isfinite(
                    self.max_values[idx]
                ):
                    axis_bounds.extend((self.min_values[idx], self.max_values[idx]))
                else:
                    axis_bounds.extend((None, None))
            bounds = (
                axis_bounds[0],
                axis_bounds[1],
                axis_bounds[2],
                axis_bounds[3],
                axis_bounds[4],
                axis_bounds[5],
            )
        return GcodePlacementAnalysis(
            programmed_bounds_mm=bounds,
            motion_line_count=self.motion_line_count,
            hazards=tuple(sorted(self.hazards)),
            work_coordinate_systems=tuple(sorted(self.work_coordinate_systems)),
            requires_initial_units=self.requires_initial_units,
            requires_initial_distance_mode=self.requires_initial_distance_mode,
            requires_initial_motion_mode=self.requires_initial_motion_mode,
        )


def _format_code(letter: str, code: float) -> str:
    if abs(code - round(code)) < 1e-9:
        return f"{letter}{int(round(code))}"
    return f"{letter}{code:g}"


_PLACEMENT_WCS_CODES = {
    54.0,
    55.0,
    56.0,
    57.0,
    58.0,
    59.0,
    59.1,
    59.2,
    59.3,
}
_PLACEMENT_PROBE_CODES = {38.2, 38.3, 38.4, 38.5}
_PLACEMENT_OFFSET_CODES = {92.0, 92.1, 92.2, 92.3}
_PLACEMENT_STORED_POSITION_CODES = {28.0, 28.1, 30.0, 30.1}
_PLACEMENT_TLO_CODES = {43.0, 43.1, 49.0}


def _collect_placement_facts(
    accumulator: _PlacementAccumulator,
    *,
    line: str,
    words: list[tuple[str, str]],
) -> None:
    parsed_words: list[tuple[str, float]] = []
    for letter, raw_value in words:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            accumulator.hazards.add("non-finite numeric command")
            continue
        parsed_words.append((str(letter).upper(), value))

    g_codes = [round(value, 3) for letter, value in parsed_words if letter == "G"]
    m_codes = [value for letter, value in parsed_words if letter == "M"]
    g_code_set = set(g_codes)

    if line.startswith("TC:") or any(abs(code - 6.0) <= 1e-9 for code in m_codes):
        accumulator.hazards.add("tool-change workflow motion")
    if 53.0 in g_code_set:
        accumulator.hazards.add("G53 machine-coordinate motion")
    if 10.0 in g_code_set:
        accumulator.hazards.add("G10 work-offset changes")
    if g_code_set.intersection(_PLACEMENT_OFFSET_CODES):
        accumulator.hazards.add("G92 coordinate-offset changes")
    if g_code_set.intersection(_PLACEMENT_STORED_POSITION_CODES):
        accumulator.hazards.add("G28/G30 stored-position motion")
    if g_code_set.intersection(_PLACEMENT_TLO_CODES):
        accumulator.hazards.add("tool-length offset changes")
    if g_code_set.intersection(_PLACEMENT_PROBE_CODES):
        accumulator.hazards.add("probe motion")
    if g_code_set.intersection({2.0, 3.0}):
        accumulator.hazards.add("arc motion")
    if 91.0 in g_code_set:
        accumulator.hazards.add("incremental G91 motion")

    selected_wcs = g_code_set.intersection(_PLACEMENT_WCS_CODES)
    accumulator.work_coordinate_systems.update(
        _format_code("G", code) for code in selected_wcs
    )
    if len(accumulator.work_coordinate_systems) > 1:
        accumulator.hazards.add("multiple work coordinate systems")

    units_in_block = g_code_set.intersection({20.0, 21.0})
    distances_in_block = g_code_set.intersection({90.0, 91.0})
    motions_in_block = g_code_set.intersection({0.0, 1.0, 2.0, 3.0})
    if len(units_in_block) > 1 or len(distances_in_block) > 1 or len(motions_in_block) > 1:
        accumulator.hazards.add("conflicting modal words in one line")

    if len(units_in_block) == 1:
        accumulator.unit_scale = 25.4 if 20.0 in units_in_block else 1.0
    if len(distances_in_block) == 1:
        accumulator.distance_mode = "G91" if 91.0 in distances_in_block else "G90"
    if len(motions_in_block) == 1:
        accumulator.motion_mode = next(iter(motions_in_block))

    axis_values: dict[str, float] = {}
    duplicate_axis = False
    for letter, value in parsed_words:
        if letter not in {"X", "Y", "Z"}:
            continue
        if letter in axis_values:
            duplicate_axis = True
        axis_values[letter] = value
    if duplicate_axis:
        accumulator.hazards.add("duplicate axis words in one line")
    if not axis_values:
        return
    if 4.0 in g_code_set:
        accumulator.hazards.add("axis words on a G4 dwell command")
        return

    parameter_only = bool(
        g_code_set.intersection(
            {10.0, 28.1, 30.1, 43.0, 43.1, 49.0, 92.0, 92.1, 92.2, 92.3}
        )
        and not motions_in_block
        and not g_code_set.intersection(_PLACEMENT_PROBE_CODES)
    )
    if parameter_only:
        return

    effective_motion: float | None = None
    if motions_in_block:
        effective_motion = next(iter(motions_in_block))
    elif g_code_set.intersection(_PLACEMENT_PROBE_CODES):
        effective_motion = next(iter(g_code_set.intersection(_PLACEMENT_PROBE_CODES)))
    else:
        effective_motion = accumulator.motion_mode
        if effective_motion is None:
            accumulator.requires_initial_motion_mode = True
            effective_motion = 1.0

    accumulator.motion_line_count += 1
    if effective_motion in {2.0, 3.0}:
        accumulator.hazards.add("arc motion")
    if effective_motion in _PLACEMENT_PROBE_CODES:
        accumulator.hazards.add("probe motion")

    if accumulator.unit_scale is None:
        accumulator.requires_initial_units = True
        unit_scale = 1.0
    else:
        unit_scale = accumulator.unit_scale
    if accumulator.distance_mode is None:
        accumulator.requires_initial_distance_mode = True
        distance_mode = "G90"
    else:
        distance_mode = accumulator.distance_mode
    if distance_mode != "G90":
        return

    for axis_index, axis in enumerate(("X", "Y", "Z")):
        target_value = axis_values.get(axis)
        if target_value is None:
            continue
        value_mm = float(target_value) * float(unit_scale)
        accumulator.min_values[axis_index] = min(
            accumulator.min_values[axis_index], value_mm
        )
        accumulator.max_values[axis_index] = max(
            accumulator.max_values[axis_index], value_mm
        )


def validate_gcode_lines(
    lines: Iterable[str],
    *,
    word_pattern: re.Pattern[str] = WORD_PAT,
    supported_g_codes: Iterable[float] = SUPPORTED_G_CODES,
    supported_m_codes: Iterable[int] = SUPPORTED_M_CODES,
) -> GcodeValidationReport:
    """Validate G-code lines against GRBL 1.1h constraints."""
    supported_g_codes = set(supported_g_codes)
    supported_m_codes = set(supported_m_codes)
    long_lines: list[tuple[int, int]] = []
    long_line_count = 0
    unsupported_axes: Counter[str] = Counter()
    unsupported_words: Counter[str] = Counter()
    unsupported_g_codes: Counter[str] = Counter()
    unsupported_m_codes: Counter[str] = Counter()
    grbl_warnings: Counter[str] = Counter()
    modal_hazards: set[str] = set()
    line_issues: list[GcodeValidationLineIssue] = []
    line_issue_count = 0
    line_issues_truncated = False
    malformed_line_count = 0
    placement_accumulator = _PlacementAccumulator()
    total = 0

    def add_issue(target: list[str], seen: set[str], text: str) -> None:
        if text in seen:
            return
        target.append(text)
        seen.add(text)

    def count_code(counter: Counter[str], key: str) -> None:
        if key in counter or len(counter) < DISTINCT_CODE_LIMIT:
            counter[key] += 1
            return
        counter[ADDITIONAL_CODE_KEY] += 1

    def store_line_issue(line_no: int, text: str, issues: list[str]) -> None:
        nonlocal line_issue_count, line_issues_truncated
        line_issue_count += 1
        if len(line_issues) < DETAIL_LINE_LIMIT:
            line_issues.append(
                GcodeValidationLineIssue(
                    line_no,
                    text[: DETAIL_LINE_TEXT_LIMIT + 1],
                    tuple(issues),
                )
            )
        else:
            line_issues_truncated = True

    for idx, raw in enumerate(lines, start=1):
        total += 1
        line = raw.strip()
        if not line:
            continue
        line_issues_for_line: list[str] = []
        line_issue_seen: set[str] = set()
        line_len = len(line.encode("utf-8")) + 1
        if line_len > MAX_LINE_LENGTH:
            long_line_count += 1
            if len(long_lines) < 5:
                long_lines.append((idx, line_len))
            add_issue(
                line_issues_for_line,
                line_issue_seen,
                f"Long line ({line_len} bytes)",
            )
        words = word_pattern.findall(line.upper())
        if word_pattern is WORD_PAT:
            _collect_placement_facts(
                placement_accumulator,
                line=line.upper(),
                words=words,
            )
        sender_directive = bool(
            line == "VACUUM_ON"
            or line == "VACUUM_OFF"
            or line.startswith("TC:")
        )
        if word_pattern is WORD_PAT and not sender_directive:
            residual = WORD_PAT.sub("", line.upper())
            if any(not ch.isspace() for ch in residual):
                malformed_line_count += 1
                add_issue(
                    line_issues_for_line,
                    line_issue_seen,
                    "Malformed or unsupported syntax",
                )
        if not words:
            if line_issues_for_line:
                store_line_issue(idx, line, line_issues_for_line)
            continue
        for letter, val in words:
            if letter in UNSUPPORTED_AXES:
                unsupported_axes[letter] += 1
                add_issue(line_issues_for_line, line_issue_seen, f"Unsupported axis {letter}")
            if letter not in KNOWN_WORD_LETTERS:
                unsupported_words[letter] += 1
                if letter not in UNSUPPORTED_AXES:
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unknown word letter {letter}",
                    )
            if letter == "G":
                try:
                    code = round(float(val), 3)
                except Exception:
                    continue
                if code in MODAL_HAZARDS:
                    modal_hazards.add(MODAL_HAZARDS[code])
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Modal hazard: {MODAL_HAZARDS[code]}",
                    )
                if code in GRBL_WARN_G_CODES:
                    grbl_warnings[GRBL_WARN_G_CODES[code]] += 1
                if code not in supported_g_codes:
                    code_label = _format_code("G", code)
                    count_code(unsupported_g_codes, code_label)
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unsupported G-code {code_label}",
                    )
            elif letter == "M":
                try:
                    code = float(val)
                except Exception:
                    continue
                if abs(code - round(code)) > 1e-6:
                    code_label = f"M{val}"
                    count_code(unsupported_m_codes, code_label)
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unsupported M-code {code_label}",
                    )
                    continue
                code_int = int(round(code))
                if code_int not in supported_m_codes:
                    code_label = f"M{code_int}"
                    count_code(unsupported_m_codes, code_label)
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unsupported M-code {code_label}",
                    )
        if line_issues_for_line:
            store_line_issue(idx, line, line_issues_for_line)

    return GcodeValidationReport(
        total_lines=total,
        long_line_count=long_line_count,
        long_lines=long_lines,
        unsupported_axes=unsupported_axes,
        unsupported_words=unsupported_words,
        unsupported_g_codes=unsupported_g_codes,
        unsupported_m_codes=unsupported_m_codes,
        grbl_warnings=grbl_warnings,
        modal_hazards=modal_hazards,
        line_issue_count=line_issue_count,
        line_issues=line_issues,
        line_issues_truncated=line_issues_truncated,
        malformed_line_count=malformed_line_count,
        placement=placement_accumulator.finish(),
    )


def _format_counter(counter: Counter[str], limit: int = 5) -> str:
    items = counter.most_common(limit)
    return ", ".join(f"{key} ({count})" for key, count in items)


def format_validation_report(report: GcodeValidationReport | None) -> str:
    """Summarize validation results for brief display."""
    if report is None:
        return "G-code validation: unavailable."
    issues: list[str] = []
    if report.long_lines:
        first_idx, first_len = report.long_lines[0]
        issues.append(
            f"Long lines (> {MAX_LINE_LENGTH} bytes): {report.long_line_count} "
            f"(first at line {first_idx}, {first_len} bytes)."
        )
    if report.unsupported_axes:
        issues.append(f"Unsupported axes: {_format_counter(report.unsupported_axes)}.")
    if report.unsupported_g_codes:
        issues.append(
            "Unsupported G-codes (not in GRBL 1.1h list): "
            f"{_format_counter(report.unsupported_g_codes)}."
        )
    if report.unsupported_m_codes:
        issues.append(
            "Unsupported M-codes (not in GRBL 1.1h list): "
            f"{_format_counter(report.unsupported_m_codes)}."
        )
    if report.grbl_warnings:
        issues.append(f"GRBL warnings: {_format_counter(report.grbl_warnings)}.")
    if report.modal_hazards:
        hazards = ", ".join(sorted(report.modal_hazards))
        issues.append(f"Modal hazards: {hazards}.")
    if report.unsupported_words:
        issues.append(
            f"Unknown word letters: {_format_counter(report.unsupported_words)}."
        )
    if report.malformed_line_count:
        issues.append(f"Malformed or unsupported syntax: {report.malformed_line_count} line(s).")
    if not issues:
        issues.append("No issues detected.")
    return "G-code validation (GRBL 1.1h):\n- " + "\n- ".join(issues)


def _trim_detail_line(text: str, limit: int = DETAIL_LINE_TEXT_LIMIT) -> str:
    if limit <= 3:
        return text[:limit]
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_validation_details(report: GcodeValidationReport | None) -> str:
    """Return a detailed, line-by-line validation report."""
    if report is None:
        return "G-code validation details: unavailable."
    if report.line_issue_count <= 0 and not report.grbl_warnings:
        return "G-code validation details (GRBL 1.1h):\nNo issues detected."
    lines: list[str] = ["G-code validation details (GRBL 1.1h):"]
    if report.grbl_warnings:
        lines.append(f"GRBL warnings: {_format_counter(report.grbl_warnings)}.")
        if report.line_issue_count <= 0:
            lines.append("No other issues detected.")
            return "\n".join(lines)
    summary = f"Issues on {report.line_issue_count} line(s)."
    if report.line_issues_truncated:
        summary += f" Showing first {len(report.line_issues)} line(s)."
    lines.append(summary)
    for entry in report.line_issues:
        issues = "; ".join(entry.issues)
        lines.append(f"Line {entry.line_no}: {issues}")
        lines.append(f"  {_trim_detail_line(entry.line)}")
    if report.line_issues_truncated and report.line_issues:
        lines.append("... additional issue lines omitted.")
    return "\n".join(lines)
