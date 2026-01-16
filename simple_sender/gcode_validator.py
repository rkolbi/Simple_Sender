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
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from simple_sender.gcode_parser import WORD_PAT
from simple_sender.utils.constants import MAX_LINE_LENGTH

DETAIL_LINE_LIMIT = 200
DETAIL_LINE_TEXT_LIMIT = 160


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
}
SUPPORTED_M_CODES = {
    0,
    1,
    2,
    3,
    4,
    5,
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


@dataclass
class GcodeValidationLineIssue:
    line_no: int
    line: str
    issues: tuple[str, ...]


@dataclass
class GcodeValidationReport:
    total_lines: int
    long_line_count: int
    long_lines: list[tuple[int, int]]
    unsupported_axes: Counter[str]
    unsupported_words: Counter[str]
    unsupported_g_codes: Counter[str]
    unsupported_m_codes: Counter[str]
    modal_hazards: set[str]
    line_issue_count: int
    line_issues: list[GcodeValidationLineIssue]
    line_issues_truncated: bool


def _format_code(letter: str, code: float) -> str:
    if abs(code - round(code)) < 1e-9:
        return f"{letter}{int(round(code))}"
    return f"{letter}{code:g}"


def validate_gcode_lines(lines: Iterable[str]) -> GcodeValidationReport:
    long_lines: list[tuple[int, int]] = []
    long_line_count = 0
    unsupported_axes: Counter[str] = Counter()
    unsupported_words: Counter[str] = Counter()
    unsupported_g_codes: Counter[str] = Counter()
    unsupported_m_codes: Counter[str] = Counter()
    modal_hazards: set[str] = set()
    line_issues: list[GcodeValidationLineIssue] = []
    line_issue_count = 0
    line_issues_truncated = False
    total = 0

    def add_issue(target: list[str], seen: set[str], text: str) -> None:
        if text in seen:
            return
        target.append(text)
        seen.add(text)

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
        words = WORD_PAT.findall(line.upper())
        if not words:
            if line_issues_for_line:
                line_issue_count += 1
                if len(line_issues) < DETAIL_LINE_LIMIT:
                    line_issues.append(
                        GcodeValidationLineIssue(idx, line, tuple(line_issues_for_line))
                    )
                else:
                    line_issues_truncated = True
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
                if code not in SUPPORTED_G_CODES:
                    code_label = _format_code("G", code)
                    unsupported_g_codes[code_label] += 1
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
                    unsupported_m_codes[code_label] += 1
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unsupported M-code {code_label}",
                    )
                    continue
                code_int = int(round(code))
                if code_int not in SUPPORTED_M_CODES:
                    code_label = f"M{code_int}"
                    unsupported_m_codes[code_label] += 1
                    add_issue(
                        line_issues_for_line,
                        line_issue_seen,
                        f"Unsupported M-code {code_label}",
                    )
        if line_issues_for_line:
            line_issue_count += 1
            if len(line_issues) < DETAIL_LINE_LIMIT:
                line_issues.append(
                    GcodeValidationLineIssue(idx, line, tuple(line_issues_for_line))
                )
            else:
                line_issues_truncated = True

    return GcodeValidationReport(
        total_lines=total,
        long_line_count=long_line_count,
        long_lines=long_lines,
        unsupported_axes=unsupported_axes,
        unsupported_words=unsupported_words,
        unsupported_g_codes=unsupported_g_codes,
        unsupported_m_codes=unsupported_m_codes,
        modal_hazards=modal_hazards,
        line_issue_count=line_issue_count,
        line_issues=line_issues,
        line_issues_truncated=line_issues_truncated,
    )


def _format_counter(counter: Counter[str], limit: int = 5) -> str:
    items = counter.most_common(limit)
    return ", ".join(f"{key} ({count})" for key, count in items)


def format_validation_report(report: GcodeValidationReport | None) -> str:
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
    if report.modal_hazards:
        hazards = ", ".join(sorted(report.modal_hazards))
        issues.append(f"Modal hazards: {hazards}.")
    if report.unsupported_words:
        issues.append(
            f"Unknown word letters: {_format_counter(report.unsupported_words)}."
        )
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
    if report is None:
        return "G-code validation details: unavailable."
    if report.line_issue_count <= 0:
        return "G-code validation details (GRBL 1.1h):\nNo issues detected."
    lines: list[str] = ["G-code validation details (GRBL 1.1h):"]
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
