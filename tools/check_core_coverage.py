#!/usr/bin/env python3
"""Enforce minimum line coverage for critical runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
import sys
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class CoverageStats:
    covered: int = 0
    total: int = 0

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return (self.covered / self.total) * 100.0


CRITICAL_MODULES: dict[str, float] = {
    "grbl_worker.py": 85.0,
    "grbl_worker_commands.py": 90.0,
    "grbl_worker_connection.py": 95.0,
    "grbl_worker_status.py": 80.0,
    "grbl_worker_streaming.py": 90.0,
    "gcode_validator.py": 80.0,
}

CRITICAL_AGGREGATE_MIN = 88.0


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("./")
    if "/" in normalized:
        return normalized.rsplit("/", 1)[-1]
    return normalized


def _collect_module_stats(root: ET.Element) -> dict[str, CoverageStats]:
    by_module: dict[str, CoverageStats] = {}
    for cls in root.findall(".//class"):
        filename = _normalize_path(cls.attrib.get("filename", ""))
        if filename not in CRITICAL_MODULES:
            continue
        total = 0
        covered = 0
        for line in cls.findall("./lines/line"):
            total += 1
            if int(line.attrib.get("hits", "0")) > 0:
                covered += 1
        by_module[filename] = CoverageStats(covered=covered, total=total)
    return by_module


def main(argv: list[str]) -> int:
    report_path = argv[1] if len(argv) > 1 else "coverage.xml"
    try:
        tree = ET.parse(report_path)
    except FileNotFoundError:
        print(f"coverage report not found: {report_path}", file=sys.stderr)
        return 2
    except ET.ParseError as exc:
        print(f"failed to parse coverage report {report_path}: {exc}", file=sys.stderr)
        return 2

    root = tree.getroot()
    by_module = _collect_module_stats(root)
    failed = False

    missing = sorted(set(CRITICAL_MODULES) - set(by_module))
    if missing:
        failed = True
        print("missing coverage entries:")
        for name in missing:
            print(f"  - {name}")

    aggregate_covered = 0
    aggregate_total = 0
    for module_name, minimum in CRITICAL_MODULES.items():
        stats = by_module.get(module_name, CoverageStats())
        aggregate_covered += stats.covered
        aggregate_total += stats.total
        pct = stats.percent
        status = "PASS" if pct >= minimum else "FAIL"
        print(f"{status} {module_name}: {pct:.1f}% (min {minimum:.1f}%)")
        if pct < minimum:
            failed = True

    aggregate_pct = 0.0 if aggregate_total <= 0 else (aggregate_covered / aggregate_total) * 100.0
    aggregate_status = "PASS" if aggregate_pct >= CRITICAL_AGGREGATE_MIN else "FAIL"
    print(
        f"{aggregate_status} aggregate critical coverage: "
        f"{aggregate_pct:.1f}% (min {CRITICAL_AGGREGATE_MIN:.1f}%)"
    )
    if aggregate_pct < CRITICAL_AGGREGATE_MIN:
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
