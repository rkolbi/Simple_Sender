#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Passive, bounded 2D G-code path preview geometry.

Path preview is observational only. No parser result, warning, LOD output, or
rendering state from this module may participate in streaming admission,
machine control, probing, recovery, or job-completion decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from bisect import bisect_right
import re
from types import MappingProxyType
from typing import Iterable, Mapping


GEOMETRY_POINT_BUDGET = 12000
CANVAS_ITEM_BUDGET = 1024
ARC_MAX_SEGMENTS = 72
ARC_MIN_SEGMENTS = 4
ARC_TOLERANCE_MM = 0.35


class PreviewWarningCode(str, Enum):
    UNSUPPORTED_PLANE = "UNSUPPORTED_PLANE"
    UNSUPPORTED_COORDINATE_CHANGE = "UNSUPPORTED_COORDINATE_CHANGE"
    UNSUPPORTED_ARC_CENTER_MODE = "UNSUPPORTED_ARC_CENTER_MODE"
    MALFORMED_WORD = "MALFORMED_WORD"
    MALFORMED_ARC = "MALFORMED_ARC"
    NONFINITE_VALUE = "NONFINITE_VALUE"
    LOD_LIMITED = "LOD_LIMITED"
    SOURCE_SAMPLED = "SOURCE_SAMPLED"


@dataclass(frozen=True)
class PreviewWarning:
    code: PreviewWarningCode
    line_index: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class PreviewSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: str
    source_index: int
    z1: float = 0.0
    z2: float = 0.0


@dataclass(frozen=True)
class PreviewBatch:
    kind: str
    points: tuple[tuple[float, float], ...]
    source_start: int
    source_end: int


@dataclass(frozen=True)
class PreviewToolSection:
    section_id: int
    label: str
    tool_number: int | None
    source_start: int
    source_end: int
    confidence: str = "inferred"


@dataclass(frozen=True)
class PreviewBounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float


@dataclass(frozen=True)
class PreviewGeometry:
    segments: tuple[PreviewSegment, ...]
    bounds: PreviewBounds | None
    warnings: tuple[PreviewWarning, ...] = ()
    motion_count: int = 0
    units: str = "mm"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class PreviewLod:
    batches: tuple[PreviewBatch, ...]
    bounds: PreviewBounds | None
    warnings: tuple[PreviewWarning, ...] = ()
    original_motion_count: int = 0
    rendered_point_count: int = 0
    limited: bool = False
    source_complete: bool = True
    source_line_count: int | None = None
    preview_line_count: int = 0
    tool_sections: tuple[PreviewToolSection, ...] = ()


_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)", re.I)
_PAREN_COMMENT_RE = re.compile(r"\([^)]*\)")
_GEOMETRY_CODES = {
    "G53",
    "G54",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "G92",
    "G10",
    "G28",
    "G30",
}


def _strip_comments(raw: str) -> str:
    text = _PAREN_COMMENT_RE.sub("", str(raw or ""))
    return text.split(";", 1)[0].strip()


def _code_letter_number(letter: str, value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{letter}{int(round(value))}"
    return f"{letter}{value:g}"


def _words(text: str, line_index: int, warnings: list[PreviewWarning]) -> list[tuple[str, float]]:
    matches = list(_WORD_RE.finditer(text))
    if not matches and text.strip():
        return []
    parsed: list[tuple[str, float]] = []
    consumed = [False] * len(text)
    for match in matches:
        for idx in range(match.start(), match.end()):
            consumed[idx] = True
        letter = match.group(1).upper()
        try:
            value = float(match.group(2))
        except ValueError:
            warnings.append(PreviewWarning(PreviewWarningCode.MALFORMED_WORD, line_index, match.group(0)))
            continue
        if not math.isfinite(value):
            warnings.append(PreviewWarning(PreviewWarningCode.NONFINITE_VALUE, line_index, match.group(0)))
            continue
        parsed.append((letter, value))
    leftovers = "".join(ch for idx, ch in enumerate(text) if not consumed[idx]).strip()
    if leftovers:
        warnings.append(PreviewWarning(PreviewWarningCode.MALFORMED_WORD, line_index, leftovers[:40]))
    return parsed


def _bounds_from_segments(segments: list[PreviewSegment]) -> PreviewBounds | None:
    if not segments:
        return None
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for seg in segments:
        xs.extend((seg.x1, seg.x2))
        ys.extend((seg.y1, seg.y2))
        zs.extend((seg.z1, seg.z2))
    return PreviewBounds(min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _arc_center_from_r(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius_word: float,
    clockwise: bool,
) -> tuple[float, float] | None:
    dx = x1 - x0
    dy = y1 - y0
    chord = math.hypot(dx, dy)
    radius = abs(radius_word)
    if chord <= 1e-12 or radius <= 0.0 or chord > 2.0 * radius + 1e-9:
        return None
    mx = (x0 + x1) / 2.0
    my = (y0 + y1) / 2.0
    h_sq = max(0.0, radius * radius - (chord / 2.0) ** 2)
    h = math.sqrt(h_sq)
    ux = -dy / chord
    uy = dx / chord
    candidates = ((mx + ux * h, my + uy * h), (mx - ux * h, my - uy * h))
    want_large = radius_word < 0.0
    for cx, cy in candidates:
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
        span = _arc_span(a0, a1, clockwise)
        is_large = abs(span) > math.pi
        if is_large == want_large:
            return cx, cy
    return candidates[0]


def _arc_span(a0: float, a1: float, clockwise: bool) -> float:
    if clockwise:
        while a1 >= a0:
            a1 -= math.tau
    else:
        while a1 <= a0:
            a1 += math.tau
    return a1 - a0


def _arc_segments(
    *,
    x0: float,
    y0: float,
    z0: float,
    x1: float,
    y1: float,
    z1: float,
    cx: float,
    cy: float,
    clockwise: bool,
    source_index: int,
) -> list[PreviewSegment]:
    radius = math.hypot(x0 - cx, y0 - cy)
    if radius <= 1e-12:
        return []
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if abs(x0 - x1) <= 1e-9 and abs(y0 - y1) <= 1e-9:
        span = -math.tau if clockwise else math.tau
    else:
        span = _arc_span(a0, a1, clockwise)
    chord_step = max(ARC_TOLERANCE_MM, 0.05)
    by_length = max(ARC_MIN_SEGMENTS, int(math.ceil(abs(span) * radius / chord_step)))
    by_angle = max(ARC_MIN_SEGMENTS, int(math.ceil(abs(span) / math.radians(12.0))))
    count = min(ARC_MAX_SEGMENTS, max(ARC_MIN_SEGMENTS, by_length, by_angle))
    out: list[PreviewSegment] = []
    px = x0
    py = y0
    pz = z0
    for step in range(1, count + 1):
        t = step / count
        angle = a0 + span * t
        nx = cx + math.cos(angle) * radius
        ny = cy + math.sin(angle) * radius
        nz = z0 + (z1 - z0) * t
        out.append(PreviewSegment(px, py, nx, ny, "feed", source_index, pz, nz))
        px, py, pz = nx, ny, nz
    return out


def parse_preview_geometry(lines: Iterable[str], *, keep_running=None) -> PreviewGeometry:
    warnings: list[PreviewWarning] = []
    segments: list[PreviewSegment] = []
    x = y = z = 0.0
    motion = "G0"
    absolute = True
    units_scale = 1.0
    units = "mm"
    plane = "G17"
    arc_centers_absolute = False
    motion_count = 0
    pending_tool: int | None = None
    tool_events: list[tuple[int, int | None, str | None]] = []
    last_line_index = 0

    for sequence_index, raw in enumerate(lines):
        if isinstance(raw, tuple) and len(raw) == 2:
            line_index = int(raw[0])
            raw_line = str(raw[1])
        else:
            line_index = sequence_index
            raw_line = str(raw)
        last_line_index = line_index
        if callable(keep_running) and not keep_running():
            break
        raw_text = raw_line.strip()
        if raw_text.upper().startswith("TC:"):
            tool_name = raw_text[3:].strip() or "Unnamed tool"
            tool_events.append((line_index, None, tool_name))
            continue
        text = _strip_comments(raw_line).upper()
        if not text:
            continue
        words = _words(text, line_index, warnings)
        if not words:
            continue
        axes: dict[str, float] = {}
        i_word = j_word = r_word = None
        line_motion = None
        geometry_warning_codes: set[str] = set()
        for letter, value in words:
            if letter == "G":
                code = _code_letter_number("G", value)
                if code in {"G0", "G1", "G2", "G3"}:
                    line_motion = code
                elif code == "G90":
                    absolute = True
                elif code == "G91":
                    absolute = False
                elif code == "G20":
                    units_scale = 25.4
                    units = "inch"
                elif code == "G21":
                    units_scale = 1.0
                    units = "mm"
                elif code == "G17":
                    plane = "G17"
                elif code in {"G18", "G19"}:
                    plane = code
                    warnings.append(PreviewWarning(PreviewWarningCode.UNSUPPORTED_PLANE, line_index, code))
                elif code == "G90.1":
                    arc_centers_absolute = True
                elif code == "G91.1":
                    arc_centers_absolute = False
                elif code in _GEOMETRY_CODES:
                    geometry_warning_codes.add(code)
            elif letter in {"X", "Y", "Z"}:
                axes[letter] = value * units_scale
            elif letter == "I":
                i_word = value * units_scale
            elif letter == "J":
                j_word = value * units_scale
            elif letter == "R":
                r_word = value * units_scale
            elif letter == "T" and value.is_integer():
                pending_tool = int(value)
        if any(letter == "M" and int(value) in {6} for letter, value in words):
            tool_events.append((line_index, pending_tool, None))
            pending_tool = None
        for code in sorted(geometry_warning_codes):
            warnings.append(
                PreviewWarning(PreviewWarningCode.UNSUPPORTED_COORDINATE_CHANGE, line_index, code)
            )
        if line_motion is not None:
            motion = line_motion
        if not axes and motion not in {"G2", "G3"}:
            continue
        nx = axes["X"] if "X" in axes and absolute else x + axes.get("X", 0.0)
        ny = axes["Y"] if "Y" in axes and absolute else y + axes.get("Y", 0.0)
        nz = axes["Z"] if "Z" in axes and absolute else z + axes.get("Z", 0.0)
        if motion in {"G0", "G1"}:
            if (nx, ny, nz) != (x, y, z):
                kind = "rapid" if motion == "G0" else "feed"
                segments.append(PreviewSegment(x, y, nx, ny, kind, line_index, z, nz))
                motion_count += 1
            x, y, z = nx, ny, nz
            continue
        if motion in {"G2", "G3"}:
            if plane != "G17":
                x, y, z = nx, ny, nz
                continue
            clockwise = motion == "G2"
            center = None
            if r_word is not None:
                center = _arc_center_from_r(x, y, nx, ny, r_word, clockwise)
            elif i_word is not None or j_word is not None:
                if arc_centers_absolute:
                    center = (float(i_word or 0.0), float(j_word or 0.0))
                else:
                    center = (x + float(i_word or 0.0), y + float(j_word or 0.0))
            else:
                warnings.append(PreviewWarning(PreviewWarningCode.MALFORMED_ARC, line_index, "missing I/J/R"))
            if center is None:
                warnings.append(PreviewWarning(PreviewWarningCode.MALFORMED_ARC, line_index, "invalid arc"))
            else:
                arc_out = _arc_segments(
                    x0=x,
                    y0=y,
                    z0=z,
                    x1=nx,
                    y1=ny,
                    z1=nz,
                    cx=center[0],
                    cy=center[1],
                    clockwise=clockwise,
                    source_index=line_index,
                )
                segments.extend(arc_out)
                motion_count += 1
            x, y, z = nx, ny, nz
    sections: list[PreviewToolSection] = []
    for index, (start, tool, tool_name) in enumerate(tool_events):
        end = tool_events[index + 1][0] - 1 if index + 1 < len(tool_events) else max(start, last_line_index)
        if tool_name:
            label = f"Tool: {tool_name}"
        else:
            label = f"Tool {tool}" if tool is not None else "Tool change (unknown tool)"
        sections.append(PreviewToolSection(index, label, tool, start, end))
    return PreviewGeometry(
        segments=tuple(segments),
        bounds=_bounds_from_segments(segments),
        warnings=tuple(warnings),
        motion_count=motion_count,
        units=units,
        metadata={"passive": True, "tool_sections": tuple(sections)},
    )


def build_lod(geometry: PreviewGeometry, *, point_budget: int = GEOMETRY_POINT_BUDGET) -> PreviewLod:
    budget = max(16, int(point_budget))
    segments = geometry.segments
    original_points = len(segments) * 2
    tool_sections_meta = tuple(
        section for section in geometry.metadata.get("tool_sections", ())
        if isinstance(section, PreviewToolSection)
    )
    section_starts = tuple(section.source_start for section in tool_sections_meta)

    def section_for_source(source_index: int) -> PreviewToolSection | None:
        position = bisect_right(section_starts, source_index) - 1
        if position < 0:
            return None
        section = tool_sections_meta[position]
        return section if source_index <= section.source_end else None
    if tool_sections_meta:
        nonempty = [
            section for section in tool_sections_meta
            if any(section.source_start <= seg.source_index <= section.source_end for seg in segments)
        ]
        per_section_budget = max(2, budget // max(1, len(nonempty)))
        selected: list[PreviewSegment] = []
        for section in nonempty:
            section_segments = [
                seg for seg in segments
                if section.source_start <= seg.source_index <= section.source_end
            ]
            stride = max(1, int(math.ceil(len(section_segments) / max(1, per_section_budget // 2))))
            selected.extend(section_segments[::stride])
            if section_segments[-1] not in selected:
                selected.append(section_segments[-1])
        stride = 1
    else:
        stride = max(1, int(math.ceil(max(1, len(segments)) / max(1, budget // 2))))
        selected = list(segments[::stride])
    batches: list[PreviewBatch] = []
    current_kind = ""
    current_section_id: int | None = None
    points: list[tuple[float, float]] = []
    source_start = source_end = 0
    for seg in selected:
        section = section_for_source(seg.source_index)
        segment_section_id = section.section_id if section is not None else None
        section_changed = bool(points) and segment_section_id != current_section_id
        if seg.kind != current_kind or not points or section_changed:
            if len(points) >= 2:
                batches.append(
                    PreviewBatch(current_kind, tuple(points), source_start, source_end)
                )
            current_kind = seg.kind
            current_section_id = segment_section_id
            points = [(seg.x1, seg.y1), (seg.x2, seg.y2)]
            source_start = source_end = seg.source_index
            continue
        last = points[-1]
        if abs(last[0] - seg.x1) > 1e-9 or abs(last[1] - seg.y1) > 1e-9:
            points.append((seg.x1, seg.y1))
        points.append((seg.x2, seg.y2))
        source_end = seg.source_index
    if len(points) >= 2:
        batches.append(PreviewBatch(current_kind, tuple(points), source_start, source_end))
    limited = stride > 1 or sum(len(batch.points) for batch in batches) > budget
    warnings = list(geometry.warnings)
    source_complete = bool(geometry.metadata.get("source_complete", True))
    source_line_count_raw = geometry.metadata.get("source_line_count")
    source_line_count = int(source_line_count_raw) if isinstance(source_line_count_raw, int) else None
    preview_line_count = int(geometry.metadata.get("preview_line_count", 0) or 0)
    tool_sections = tuple(
        section for section in geometry.metadata.get("tool_sections", ())
        if isinstance(section, PreviewToolSection)
    )
    if not source_complete:
        warnings.append(PreviewWarning(PreviewWarningCode.SOURCE_SAMPLED, None, "preview input is sampled"))
    if limited:
        warnings.append(
            PreviewWarning(
                PreviewWarningCode.LOD_LIMITED,
                None,
                f"displayed={sum(len(batch.points) for batch in batches)} original={original_points}",
            )
        )
    if tool_sections:
        section_budget = max(1, CANVAS_ITEM_BUDGET // max(1, len(tool_sections)))
        rendered_batches: list[PreviewBatch] = []
        for section in tool_sections:
            section_batches = [
                batch for batch in batches
                if not (
                    batch.source_end < section.source_start
                    or batch.source_start > section.source_end
                )
            ]
            rendered_batches.extend(section_batches[:section_budget])
        rendered_batches = rendered_batches[:CANVAS_ITEM_BUDGET]
    else:
        rendered_batches = batches[:CANVAS_ITEM_BUDGET]

    return PreviewLod(
        batches=tuple(rendered_batches),
        bounds=geometry.bounds,
        warnings=tuple(warnings),
        original_motion_count=geometry.motion_count,
        rendered_point_count=sum(len(batch.points) for batch in rendered_batches),
        limited=limited or len(batches) > CANVAS_ITEM_BUDGET,
        source_complete=source_complete,
        source_line_count=source_line_count,
        preview_line_count=preview_line_count,
        tool_sections=tool_sections,
    )
