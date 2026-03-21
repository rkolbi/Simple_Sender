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

"""Diagnostics bundle payload and archive assembly helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import os
import zipfile


def collect_diagnostics_bundle_payload(
    app: Any,
    *,
    collect_build_info: Callable[[Any], dict[str, Any]],
    build_session_diagnostics_lines: Callable[[Any], list[str]],
    build_performance_report_text: Callable[[Any], str],
    runtime_metrics: Callable[[Any], dict[str, Any]],
    json_dump: Callable[[Any], str],
    build_system_info_text: Callable[..., str],
    resolved_settings_path: Callable[[Any], Path | None],
    discover_macro_assets: Callable[[Any], list[tuple[str, str]]],
    get_log_dir: Callable[[], Path],
    collect_streaming_bundle_artifacts: Callable[[dict[str, Any], list[Path]], dict[str, str]],
    log_suppressed: Callable[[str, BaseException], None],
    bundle_log_max_files: int,
) -> dict[str, Any]:
    build_info = collect_build_info(app)
    session_text = "\n".join(build_session_diagnostics_lines(app)) + "\n"
    perf_text = build_performance_report_text(app).strip() + "\n"
    runtime_snapshot = runtime_metrics(app)
    runtime_snapshot["build_info"] = dict(build_info)
    runtime_metrics_json = json_dump(runtime_snapshot) + "\n"
    connection_timeline_json = (
        json_dump(list(getattr(app, "_connection_timeline", []) or [])) + "\n"
    )
    system_info_text = build_system_info_text(app, build_info=build_info)
    settings_snapshot_json = json_dump(getattr(app, "settings", {}) or {}) + "\n"
    settings_path = resolved_settings_path(app)
    macro_assets = discover_macro_assets(app)
    log_dir = get_log_dir()
    try:
        log_candidates = list(log_dir.iterdir())
    except Exception as exc:
        log_suppressed("Failed enumerating log files for diagnostics bundle", exc)
        log_candidates = []
    log_files = sorted(
        (
            candidate
            for candidate in log_candidates
            if candidate.is_file()
            and (
                candidate.name.endswith(".log")
                or ".log." in candidate.name
                or candidate.name.startswith("simple_sender_performance_report_")
                or candidate.name.startswith("simple_sender_diagnostics_")
            )
        ),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )[: max(1, int(bundle_log_max_files))]
    streaming_artifacts = collect_streaming_bundle_artifacts(
        runtime_snapshot, log_files
    )
    bundle_manifest: dict[str, Any] = {
        "kind": "simple_sender_diagnostics_bundle",
        "created": datetime.now().isoformat(timespec="seconds"),
        "version": str(
            getattr(getattr(app, "version_var", None), "get", lambda: "")() or ""
        ),
        "build": dict(build_info),
        "files": {
            "session_diagnostics": True,
            "performance_report": True,
            "runtime_metrics": True,
            "connection_timeline": True,
            "system_info": True,
            "runtime_settings_snapshot": True,
            "settings_file": bool(settings_path is not None),
            "log_count": 0,
            "macro_asset_count": 0,
            "streaming_artifact_count": len(streaming_artifacts),
        },
    }
    return {
        "session_text": session_text,
        "perf_text": perf_text,
        "runtime_metrics_json": runtime_metrics_json,
        "connection_timeline_json": connection_timeline_json,
        "system_info_text": system_info_text,
        "settings_snapshot_json": settings_snapshot_json,
        "settings_path": settings_path,
        "macro_assets": macro_assets,
        "log_files": log_files,
        "streaming_artifacts": streaming_artifacts,
        "bundle_manifest": bundle_manifest,
    }


def write_diagnostics_bundle_archive(
    out_path: Path,
    payload: dict[str, Any],
    *,
    json_dump: Callable[[Any], str],
    write_bounded_log_tail_chunked: Callable[..., None],
    log_suppressed: Callable[[str, BaseException], None],
    bundle_log_tail_max_bytes: int,
) -> None:
    bundle_manifest = dict(payload.get("bundle_manifest", {}) or {})
    files_section = dict(bundle_manifest.get("files", {}) or {})
    bundle_manifest["files"] = files_section
    settings_path = payload.get("settings_path")
    macro_assets = list(payload.get("macro_assets", []) or [])
    log_files = list(payload.get("log_files", []) or [])
    streaming_artifacts = dict(payload.get("streaming_artifacts", {}) or {})
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "session_diagnostics.txt", str(payload.get("session_text", ""))
        )
        archive.writestr("performance_report.txt", str(payload.get("perf_text", "")))
        archive.writestr(
            "runtime_metrics.json", str(payload.get("runtime_metrics_json", ""))
        )
        archive.writestr(
            "connection_timeline.json", str(payload.get("connection_timeline_json", ""))
        )
        archive.writestr("system_info.txt", str(payload.get("system_info_text", "")))
        archive.writestr(
            "settings/runtime_settings_snapshot.json",
            str(payload.get("settings_snapshot_json", "")),
        )
        if isinstance(settings_path, Path):
            try:
                archive.write(settings_path, arcname="settings/settings.json")
            except Exception as exc:
                log_suppressed(
                    "Failed adding settings file to diagnostics bundle", exc
                )
        macro_added = 0
        for source, name in macro_assets:
            try:
                archive.write(source, arcname=f"macros/{os.path.basename(name)}")
                macro_added += 1
            except Exception as exc:
                log_suppressed(
                    "Failed adding macro/checklist asset to diagnostics bundle", exc
                )
        log_added = 0
        for log_path in log_files:
            try:
                write_bounded_log_tail_chunked(
                    archive,
                    arcname=f"logs/{log_path.name}",
                    path=log_path,
                    max_bytes=bundle_log_tail_max_bytes,
                )
                log_added += 1
            except Exception as exc:
                log_suppressed("Failed adding log file to diagnostics bundle", exc)
        streaming_added = 0
        for arcname, content in streaming_artifacts.items():
            try:
                archive.writestr(str(arcname), str(content or ""))
                streaming_added += 1
            except Exception as exc:
                log_suppressed(
                    "Failed adding streaming artifact to diagnostics bundle", exc
                )
        files_section["log_count"] = log_added
        files_section["macro_asset_count"] = macro_added
        files_section["streaming_artifact_count"] = streaming_added
        archive.writestr("manifest.json", json_dump(bundle_manifest))
