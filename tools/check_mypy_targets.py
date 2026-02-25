#!/usr/bin/env python3
"""Validate the mypy target manifest and its documented count."""

from __future__ import annotations

import argparse
import configparser
from pathlib import Path
import re
import sys

README_COUNT_PATTERN = re.compile(r"mypy against (\d+) source files", re.IGNORECASE)


def _parse_mypy_files(config_path: Path) -> list[str]:
    parser = configparser.ConfigParser()
    read = parser.read(config_path, encoding="utf-8")
    if not read:
        raise FileNotFoundError(f"mypy config not found: {config_path}")
    if "mypy" not in parser or "files" not in parser["mypy"]:
        raise KeyError("missing [mypy] files entry")
    raw = parser["mypy"]["files"]
    entries = [part.strip() for line in raw.splitlines() for part in line.split(",") if part.strip()]
    return entries


def _resolve_entry_path(base_dir: Path, entry: str) -> Path:
    path = Path(entry)
    if path.is_absolute():
        return path
    return base_dir / path


def _extract_readme_count(readme_path: Path) -> int:
    text = readme_path.read_text(encoding="utf-8")
    match = README_COUNT_PATTERN.search(text)
    if not match:
        raise ValueError(
            "README count note not found; expected text matching "
            "'mypy against <N> source files'"
        )
    return int(match.group(1))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="mypy.ini", help="Path to mypy config file")
    ap.add_argument("--readme", default="README.md", help="Path to README for count note verification")
    ap.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Optional fixed expected count. If omitted, only README sync is enforced.",
    )
    args = ap.parse_args(argv[1:])

    config_path = Path(args.config)
    readme_path = Path(args.readme)

    try:
        entries = _parse_mypy_files(config_path)
    except (FileNotFoundError, KeyError) as exc:
        print(f"FAIL mypy target manifest: {exc}", file=sys.stderr)
        return 2

    count = len(entries)
    print(f"mypy target count: {count}")

    unique_count = len(set(entries))
    if unique_count != count:
        print(
            f"FAIL mypy target manifest: duplicate entries found "
            f"({count - unique_count} duplicates)",
            file=sys.stderr,
        )
        return 1

    missing = [entry for entry in entries if not _resolve_entry_path(config_path.parent, entry).exists()]
    if missing:
        print("FAIL mypy target manifest: missing files referenced by mypy.ini", file=sys.stderr)
        for path in missing[:20]:
            print(f"  - {path}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more", file=sys.stderr)
        return 1

    if args.expected_count is not None and count != args.expected_count:
        print(
            f"FAIL mypy target count: expected {args.expected_count}, got {count}",
            file=sys.stderr,
        )
        return 1

    try:
        readme_count = _extract_readme_count(readme_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL README mypy count note: {exc}", file=sys.stderr)
        return 2

    print(f"README mypy count note: {readme_count}")
    if readme_count != count:
        print(
            f"FAIL README mypy count note: README says {readme_count}, "
            f"but mypy.ini has {count}",
            file=sys.stderr,
        )
        return 1

    print("PASS mypy target manifest and README count note are in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
