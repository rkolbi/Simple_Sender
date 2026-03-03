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

import logging
import json
import os
import platform
import sys
import threading
import zipfile
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any, cast

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.macro_files import discover_macro_assets
from simple_sender.ui.pi_profile import PI_PROFILE_STATUS_POLL_INTERVAL
from simple_sender.utils.logging_config import get_log_dir
from .popup_utils import center_window

CHECKLIST_ITEMS = [
    "Connect/disconnect: port list refreshes, status shows connected, $G and $$ populate settings.",
    "Units: modal units match controller; $13 reporting indicator updates; unit toggle locked while streaming.",
    "Load G-code: file name, size, estimates, and bounds render in the correct units.",
    "Streaming: start/pause/resume/stop behaves correctly; buffer fill and progress update smoothly.",
    "Completion: popup shows run stats; progress bar resets after acknowledgment.",
    "Overrides: feed/spindle sliders send real-time commands and update the UI.",
    "Jogging: on-screen jog works; jog cancel halts motion; joystick hold stops on release.",
    "Safety: joystick safety hold gates actions; blocked actions emit status/log text.",
    "Alarms: alarm/lock messages display; unlock and recovery actions behave as expected.",
]
RUN_CHECKLIST_ITEMS = [
    "Confirm emergency stop and limit switches are functional.",
    "Home the machine and verify travel direction/limits.",
    "Set WCS zero and confirm units (G20/G21) match expectations.",
    "Verify tool, clamp clearance, and safe Z height.",
    "Dry-run in air if the job is new or the setup changed.",
]
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
PREFLIGHT_DECISION_HISTORY_LIMIT = 100
RUNTIME_TELEMETRY_REFRESH_MS = 1000
PERF_TEST_STATUS_POLL_INTERVAL = max(1.0, float(PI_PROFILE_STATUS_POLL_INTERVAL))


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _set_var_value(app: Any, attr_name: str, value: Any) -> None:
    var = getattr(app, attr_name, None)
    setter = getattr(var, "set", None)
    if not callable(setter):
        return
    try:
        setter(value)
    except Exception as exc:
        _log_suppressed(f"Failed setting {attr_name}", exc)


def _json_dump(obj: Any) -> str:
    def _default(value: Any):
        return str(value)

    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=_default)
    except Exception as exc:
        _log_suppressed("Failed serializing diagnostics JSON payload", exc)
        return "{}"


def _build_system_info_text(app: Any) -> str:
    lines: list[str] = []
    lines.append("Simple Sender system snapshot")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    version_text = str(version_getter() if callable(version_getter) else "").strip()
    if version_text:
        lines.append(f"App version: {version_text}")
    lines.append(f"Python: {sys.version.splitlines()[0] if sys.version else 'n/a'}")
    lines.append(f"Executable: {sys.executable}")
    lines.append(f"Platform: {platform.platform()}")
    lines.append(f"Machine: {platform.machine()}")
    lines.append(f"Processor: {platform.processor()}")
    lines.append(f"PID: {os.getpid()}")
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if cwd:
        lines.append(f"CWD: {cwd}")
    env = os.environ
    for key in ("USER", "LOGNAME", "HOME", "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
        value = str(env.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def _resolved_settings_path(app: Any) -> Path | None:
    raw_path = str(getattr(app, "settings_path", "") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    return path


def _safe_job_hash(app: Any) -> str:
    raw_hash = (
        getattr(app, "_gcode_hash", None)
        or getattr(app, "_last_parse_hash", None)
        or ""
    )
    return str(raw_hash).strip()


def _format_mb(value_bytes: Any) -> str:
    try:
        if value_bytes is None:
            return "n/a"
        return f"{(float(value_bytes) / (1024.0 * 1024.0)):.2f} MB"
    except (TypeError, ValueError):
        return "n/a"


def _runtime_metrics(app: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    grbl = getattr(app, "grbl", None)
    getter = getattr(grbl, "get_runtime_metrics", None) if grbl is not None else None
    if callable(getter):
        try:
            raw = getter()
        except Exception as exc:
            _log_suppressed("Failed collecting runtime telemetry metrics", exc)
            raw = {}
        if isinstance(raw, dict):
            metrics.update(cast(dict[str, Any], raw))
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is None:
        metrics["perf_available"] = False
        return metrics
    snapshot_getter = getattr(perf_monitor, "runtime_snapshot", None)
    if not callable(snapshot_getter):
        metrics["perf_available"] = False
        return metrics
    try:
        snapshot = snapshot_getter()
    except Exception as exc:
        _log_suppressed("Failed collecting performance-monitor runtime snapshot", exc)
        metrics["perf_available"] = False
        return metrics
    if not isinstance(snapshot, dict):
        metrics["perf_available"] = False
        return metrics
    metrics["perf_available"] = bool(snapshot.get("available", True))
    for key, value in snapshot.items():
        metrics[f"perf_{key}"] = value
    raw_status_perf = getattr(app, "_status_perf_metrics", None)
    if isinstance(raw_status_perf, dict) and raw_status_perf:
        status_perf: dict[str, dict[str, float | int]] = {}
        for raw_name, raw_entry in raw_status_perf.items():
            if not isinstance(raw_entry, dict):
                continue
            name = str(raw_name or "").strip()
            if not name:
                continue
            count = int(raw_entry.get("count", 0) or 0)
            total_ms = float(raw_entry.get("total_ms", 0.0) or 0.0)
            max_ms = float(raw_entry.get("max_ms", 0.0) or 0.0)
            avg_ms = (total_ms / count) if count > 0 else 0.0
            status_perf[name] = {
                "count": count,
                "avg_ms": avg_ms,
                "max_ms": max_ms,
            }
        if status_perf:
            metrics["status_perf_metrics"] = status_perf
    viewer_mode = str(getattr(app, "_gcode_viewer_mode", "") or "").strip()
    if viewer_mode:
        metrics["viewer_mode"] = viewer_mode
    policy_lines = getattr(app, "_gcode_viewer_policy_line_count", None)
    preview_lines = getattr(app, "_gcode_viewer_preview_line_count", None)
    threshold = getattr(app, "_gcode_viewer_virtualization_threshold", None)
    window = getattr(app, "_gcode_viewer_virtual_window", None)
    chunk_size = getattr(app, "_gcode_viewer_chunk_size", None)
    preview_only = getattr(app, "_gcode_viewer_preview_only", None)
    if policy_lines is not None:
        metrics["viewer_policy_line_count"] = int(policy_lines)
    if preview_lines is not None:
        metrics["viewer_preview_line_count"] = int(preview_lines)
    if threshold is not None:
        metrics["viewer_virtualization_threshold"] = int(threshold)
    if window is not None:
        metrics["viewer_virtual_window_size"] = int(window)
    if chunk_size is not None:
        metrics["viewer_chunk_size"] = int(chunk_size)
    if preview_only is not None:
        metrics["viewer_preview_only"] = bool(preview_only)
    gview = getattr(app, "gview", None)
    if gview is not None:
        for attr_name, metric_name in (
            ("_insert_hidden_delay_ms", "viewer_hidden_insert_delay_ms"),
            ("_insert_hidden_max_chunks_per_tick", "viewer_hidden_max_chunks_per_tick"),
            ("_insert_hidden_chunk_size", "viewer_hidden_chunk_size"),
        ):
            raw_value = getattr(gview, attr_name, None)
            if raw_value is None:
                continue
            try:
                metrics[metric_name] = int(raw_value)
            except (TypeError, ValueError):
                continue
    return metrics


def _format_runtime_metrics(
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int = 20,
) -> list[str]:
    if not metrics:
        return []
    lines: list[str] = []
    has_worker_metrics = any(
        key in metrics
        for key in (
            "tx_lines_per_sec",
            "tx_loop_cycles",
            "queue_depth_last",
        )
    )
    if has_worker_metrics:
        tx_lines_per_sec = float(metrics.get("tx_lines_per_sec", 0.0) or 0.0)
        ok_last = float(metrics.get("ok_latency_ms_last", 0.0) or 0.0)
        ok_avg = float(metrics.get("ok_latency_ms_avg", 0.0) or 0.0)
        ok_samples = int(metrics.get("ok_latency_samples", 0) or 0)
        lines.append(f"- TX lines/sec: {tx_lines_per_sec:.2f}")
        lines.append(
            f"- ACK latency ms: last={ok_last:.2f}, avg={ok_avg:.2f}, samples={ok_samples}"
        )
        tx_loop_cycles = int(metrics.get("tx_loop_cycles", 0) or 0)
        tx_loop_idle_cycles = int(metrics.get("tx_loop_idle_cycles", 0) or 0)
        tx_loop_active_cycles = int(metrics.get("tx_loop_active_cycles", 0) or 0)
        tx_loop_idle_wait_total_s = float(metrics.get("tx_loop_idle_wait_total_s", 0.0) or 0.0)
        tx_loop_idle_ratio = float(metrics.get("tx_loop_idle_ratio", 0.0) or 0.0)
        lines.append(
            "- TX loop: "
            f"cycles={tx_loop_cycles}, "
            f"active={tx_loop_active_cycles}, "
            f"idle={tx_loop_idle_cycles}, "
            f"idle_ratio={tx_loop_idle_ratio:.3f}, "
            f"idle_wait_s={tx_loop_idle_wait_total_s:.2f}"
        )
        queue_last = metrics.get("queue_depth_last", {})
        if isinstance(queue_last, dict):
            lines.append(
                "- Queue depth last: "
                f"stream={int(queue_last.get('stream', 0) or 0)}, "
                f"manual={int(queue_last.get('manual', 0) or 0)}, "
                f"ui={int(queue_last.get('ui', 0) or 0)} "
                f"@ {str(queue_last.get('timestamp', '') or 'n/a')}"
            )
        if include_samples:
            queue_samples = metrics.get("queue_depth_samples", [])
            if isinstance(queue_samples, list) and queue_samples:
                lines.append("- Queue depth samples:")
                for sample in queue_samples[-sample_limit:]:
                    if not isinstance(sample, dict):
                        continue
                    lines.append(
                        "  "
                        f"{str(sample.get('timestamp', '') or 'n/a')} "
                        f"stream={int(sample.get('stream', 0) or 0)} "
                        f"manual={int(sample.get('manual', 0) or 0)} "
                        f"ui={int(sample.get('ui', 0) or 0)}"
                    )
    else:
        lines.append("- Worker runtime telemetry unavailable.")

    perf_available = bool(metrics.get("perf_available", False))
    if not perf_available:
        lines.append("- Perf monitor disabled (enable runtime performance profiling for CPU/RSS telemetry).")
        return lines

    idle_cpu_avg = metrics.get("perf_idle_cpu_avg")
    idle_cpu_p95 = metrics.get("perf_idle_cpu_p95")
    quiet_idle_cpu_avg = metrics.get("perf_quiet_idle_cpu_avg")
    quiet_idle_cpu_p95 = metrics.get("perf_quiet_idle_cpu_p95")
    quiet_idle_samples = metrics.get("perf_quiet_idle_cpu_samples")
    stream_cpu_avg = metrics.get("perf_stream_cpu_avg")
    stream_cpu_p95 = metrics.get("perf_stream_cpu_p95")
    if idle_cpu_avg is None or idle_cpu_p95 is None:
        lines.append("- Idle CPU avg/p95: n/a")
    else:
        lines.append(f"- Idle CPU avg/p95: {float(idle_cpu_avg):.2f}% / {float(idle_cpu_p95):.2f}%")
    if quiet_idle_cpu_avg is None or quiet_idle_cpu_p95 is None:
        lines.append("- Quiet idle CPU avg/p95: n/a")
    else:
        sample_suffix = ""
        try:
            sample_count = int(quiet_idle_samples or 0)
        except (TypeError, ValueError):
            sample_count = 0
        if sample_count > 0:
            sample_suffix = f" (samples={sample_count})"
        lines.append(
            "- Quiet idle CPU avg/p95: "
            f"{float(quiet_idle_cpu_avg):.2f}% / {float(quiet_idle_cpu_p95):.2f}%"
            f"{sample_suffix}"
        )
    if stream_cpu_avg is None or stream_cpu_p95 is None:
        lines.append("- Streaming CPU avg/p95: n/a")
    else:
        lines.append(
            f"- Streaming CPU avg/p95: {float(stream_cpu_avg):.2f}% / {float(stream_cpu_p95):.2f}%"
        )
    lines.append(f"- RSS start/current/peak: {_format_mb(metrics.get('perf_rss_start_bytes'))} / "
                 f"{_format_mb(metrics.get('perf_rss_current_bytes'))} / "
                 f"{_format_mb(metrics.get('perf_rss_peak_bytes'))}")
    steady_after = metrics.get("perf_steady_state_after_s")
    steady_label = f"{int(float(steady_after))}s" if steady_after is not None else "steady-state"
    lines.append(
        f"- RSS {steady_label}: {_format_mb(metrics.get('perf_rss_steady_state_bytes'))}"
    )
    uptime_s = metrics.get("perf_uptime_s")
    startup_s = metrics.get("perf_startup_time_s")
    if uptime_s is not None:
        lines.append(f"- Perf monitor uptime: {float(uptime_s):.1f}s")
    if startup_s is not None:
        lines.append(f"- Startup time: {float(startup_s):.3f}s")
    max_drain_ms = metrics.get("perf_ui_queue_drain_runtime_max_ms")
    stall_count = metrics.get("perf_ui_queue_drain_runtime_stall_count")
    if max_drain_ms is None:
        max_drain_ms = metrics.get("perf_ui_queue_drain_max_ms")
    if stall_count is None:
        stall_count = metrics.get("perf_ui_queue_drain_stall_count")
    stall_budget = metrics.get("perf_ui_queue_drain_stall_budget_ms")
    if max_drain_ms is not None and stall_count is not None:
        budget_text = f"{float(stall_budget):.1f}" if stall_budget is not None else "n/a"
        lines.append(
            "- UI queue drain max/stalls: "
            f"{float(max_drain_ms):.2f} ms / {int(stall_count)} (budget {budget_text} ms)"
        )
    slow_event_ms = metrics.get("perf_ui_queue_drain_runtime_slowest_event_ms")
    if slow_event_ms is not None:
        try:
            slow_ms = float(slow_event_ms)
        except (TypeError, ValueError):
            slow_ms = 0.0
        if slow_ms > 0.0:
            slow_kind = str(metrics.get("perf_ui_queue_drain_runtime_slowest_event_kind", "") or "")
            lines.append(
                "- UI queue slowest runtime event: "
                f"{slow_kind or 'unknown'} ({slow_ms:.2f} ms)"
            )
    status_perf = metrics.get("status_perf_metrics")
    if isinstance(status_perf, dict) and status_perf:
        lines.append("- Status handler timings:")
        for name in sorted(status_perf.keys()):
            entry = status_perf.get(name)
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{name}: "
                f"count={int(entry.get('count', 0) or 0)}, "
                f"avg={float(entry.get('avg_ms', 0.0) or 0.0):.3f} ms, "
                f"max={float(entry.get('max_ms', 0.0) or 0.0):.3f} ms"
            )
    task_timings = metrics.get("perf_background_task_timings")
    if isinstance(task_timings, dict) and task_timings:
        lines.append("- Background I/O timings:")
        for task_name in sorted(task_timings.keys()):
            entry = task_timings.get(task_name)
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{task_name}: "
                f"count={int(entry.get('count', 0) or 0)}, "
                f"ok={int(entry.get('ok_count', 0) or 0)}, "
                f"err={int(entry.get('err_count', 0) or 0)}, "
                f"avg={float(entry.get('avg_ms', 0.0) or 0.0):.2f} ms, "
                f"max={float(entry.get('max_ms', 0.0) or 0.0):.2f} ms"
            )
    viewer_mode = str(metrics.get("viewer_mode", "") or "").strip()
    if viewer_mode:
        details: list[str] = [f"mode={viewer_mode}"]
        policy_lines = metrics.get("viewer_policy_line_count")
        preview_lines = metrics.get("viewer_preview_line_count")
        threshold = metrics.get("viewer_virtualization_threshold")
        preview_only = metrics.get("viewer_preview_only")
        window_size = metrics.get("viewer_virtual_window_size")
        chunk_size = metrics.get("viewer_chunk_size")
        if policy_lines is not None:
            details.append(f"policy_lines={int(policy_lines):,}")
        if preview_lines is not None:
            details.append(f"preview_lines={int(preview_lines):,}")
        if threshold is not None:
            details.append(f"threshold={int(threshold):,}")
        if preview_only is not None:
            details.append(f"preview_only={bool(preview_only)}")
        if window_size is not None:
            details.append(f"window={int(window_size):,}")
        if chunk_size is not None:
            details.append(f"chunk={int(chunk_size):,}")
        lines.append("- G-code viewer load policy: " + ", ".join(details))
    hidden_delay = metrics.get("viewer_hidden_insert_delay_ms")
    hidden_chunks = metrics.get("viewer_hidden_max_chunks_per_tick")
    hidden_chunk_size = metrics.get("viewer_hidden_chunk_size")
    if hidden_delay is not None or hidden_chunks is not None or hidden_chunk_size is not None:
        details: list[str] = []
        if hidden_delay is not None:
            details.append(f"delay={int(hidden_delay)} ms")
        if hidden_chunks is not None:
            details.append(f"max_chunks={int(hidden_chunks)}")
        if hidden_chunk_size is not None:
            details.append(f"chunk_size={int(hidden_chunk_size)}")
        if details:
            lines.append("- G-code viewer hidden-tab throttle: " + ", ".join(details))
    return lines


def open_runtime_telemetry(app) -> None:
    existing = getattr(app, "_runtime_telemetry_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing runtime telemetry window", exc)
    win = tk.Toplevel(app)
    app._runtime_telemetry_window = win
    app._runtime_telemetry_after_id = None
    win.title("Runtime telemetry")
    win.minsize(600, 360)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    ttk.Label(container, text="Runtime telemetry", font=("TkDefaultFont", 12, "bold")).pack(
        anchor="w"
    )
    ttk.Label(
        container,
        text="Live worker/queue counters. Refreshes every second while this window is open.",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    text = tk.Text(container, wrap="none", height=14, font=("TkFixedFont", 10))
    text.pack(fill="both", expand=True)
    text.configure(state="disabled")
    last_rendered = {"text": None}
    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))

    def _render() -> None:
        metrics = _runtime_metrics(app)
        if metrics:
            lines = _format_runtime_metrics(metrics, include_samples=True, sample_limit=12)
            body = "\n".join(lines) if lines else "Runtime telemetry unavailable."
        else:
            body = "Runtime telemetry unavailable."
        if body == last_rendered["text"]:
            return
        last_rendered["text"] = body
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", body)
        text.configure(state="disabled")

    def _schedule_refresh() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            app._runtime_telemetry_after_id = win.after(
                RUNTIME_TELEMETRY_REFRESH_MS,
                _on_refresh_timer,
            )
        except Exception as exc:
            _log_suppressed("Failed scheduling runtime telemetry refresh", exc)

    def _on_refresh_timer() -> None:
        if getattr(app, "_runtime_telemetry_window", None) is not win:
            return
        _render()
        _schedule_refresh()

    def _on_close() -> None:
        after_id = getattr(app, "_runtime_telemetry_after_id", None)
        if after_id is not None:
            try:
                win.after_cancel(after_id)
            except Exception:
                pass
        app._runtime_telemetry_after_id = None
        app._runtime_telemetry_window = None
        win.destroy()

    ttk.Button(btn_row, text="Refresh now", command=_render).pack(side="left")
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)
    _render()
    _schedule_refresh()
    center_window(win, app)


def _record_preflight_decision(
    app: Any,
    *,
    decision: str,
    failures: list[str],
    warnings: list[str],
) -> None:
    job_path = str(getattr(app, "_last_gcode_path", "") or "")
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "decision": str(decision),
        "job_path": job_path,
        "job_name": os.path.basename(job_path) if job_path else "",
        "job_hash": _safe_job_hash(app),
        "stream_state": str(getattr(app, "_stream_state", "") or ""),
        "streaming_mode": bool(getattr(app, "_gcode_streaming_mode", False)),
        "failure_count": int(len(failures)),
        "warning_count": int(len(warnings)),
        "failures": list(failures),
        "warnings": list(warnings),
    }
    try:
        history = getattr(app, "_preflight_run_decisions", None)
        if not isinstance(history, list):
            history = []
            setattr(app, "_preflight_run_decisions", history)
        history.append(record)
        overflow = len(history) - PREFLIGHT_DECISION_HISTORY_LIMIT
        if overflow > 0:
            del history[:overflow]
    except Exception as exc:
        _log_suppressed("Failed recording preflight run decision", exc)


def _resolve_checklist_items(app, name: str, fallback: list[str]) -> list[str]:
    path = find_named_checklist(app, name)
    if not path:
        return fallback
    items = load_checklist_items(path)
    if items is None:
        return fallback
    return cast(list[str], items)


def _resolve_checklist_items_any(app, names: list[str], fallback: list[str]) -> list[str]:
    for name in names:
        path = find_named_checklist(app, name)
        if not path:
            continue
        items = load_checklist_items(path)
        if items is not None:
            return cast(list[str], items)
    return fallback


def open_release_checklist(app):
    existing = getattr(app, "_release_checklist_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing release checklist window", exc)
    win = tk.Toplevel(app)
    app._release_checklist_window = win
    win.title("Release checklist")
    win.minsize(560, 380)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(container, text="Release checklist", font=("TkDefaultFont", 12, "bold"))
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this quick pass before release to confirm the critical GRBL workflows.",
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    items = _resolve_checklist_items(app, "release", CHECKLIST_ITEMS)
    text = tk.Text(container, wrap="word", height=12)
    text.pack(fill="both", expand=True)
    if items:
        text.insert("end", "\n".join(f"- {item}" for item in items))
    else:
        text.insert("end", "Checklist file is empty.")
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close():
        app._release_checklist_window = None
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)


def open_run_checklist(app):
    existing = getattr(app, "_run_checklist_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            _log_suppressed("Failed restoring existing run checklist window", exc)
    win = tk.Toplevel(app)
    app._run_checklist_window = win
    win.title("Start Job checklist")
    win.minsize(520, 320)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    title = ttk.Label(container, text="Start Job checklist", font=("TkDefaultFont", 12, "bold"))
    title.pack(anchor="w")
    ttk.Label(
        container,
        text="Use this checklist before starting a job to reduce surprises.",
        wraplength=480,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    items = _resolve_checklist_items_any(app, ["start-job", "run"], RUN_CHECKLIST_ITEMS)
    text = tk.Text(container, wrap="word", height=10)
    text.pack(fill="both", expand=True)
    if items:
        text.insert("end", "\n".join(f"- {item}" for item in items))
    else:
        text.insert("end", "Checklist file is empty.")
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close():
        app._run_checklist_window = None
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)


def _format_validation_summary(report) -> list[str]:
    if report is None:
        return []
    summary = []
    if getattr(report, "long_line_count", 0):
        summary.append(f"Overlong lines: {report.long_line_count}")
    unsupported_axes = getattr(report, "unsupported_axes", {})
    if unsupported_axes:
        axes = ", ".join(f"{k} x{v}" for k, v in unsupported_axes.items())
        summary.append(f"Unsupported axes: {axes}")
    unsupported_g = getattr(report, "unsupported_g_codes", {})
    if unsupported_g:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_g.items())
        summary.append(f"Unsupported G-codes: {codes}")
    unsupported_m = getattr(report, "unsupported_m_codes", {})
    if unsupported_m:
        codes = ", ".join(f"{k} x{v}" for k, v in unsupported_m.items())
        summary.append(f"Unsupported M-codes: {codes}")
    grbl_warnings = getattr(report, "grbl_warnings", {})
    if grbl_warnings:
        warnings = ", ".join(f"{k} x{v}" for k, v in grbl_warnings.items())
        summary.append(f"GRBL warnings: {warnings}")
    unsupported_words = getattr(report, "unsupported_words", {})
    if unsupported_words:
        words = ", ".join(f"{k} x{v}" for k, v in unsupported_words.items())
        summary.append(f"Unknown words: {words}")
    hazards = sorted(getattr(report, "modal_hazards", set()))
    if hazards:
        summary.append(f"Modal hazards: {', '.join(hazards)}")
    if getattr(report, "line_issue_count", 0):
        summary.append(f"Line issues: {report.line_issue_count}")
    return summary


def _get_bounds(app: Any):
    parse_result = getattr(app, "_last_parse_result", None)
    bounds = getattr(parse_result, "bounds", None) if parse_result else None
    if not bounds:
        top_view = getattr(getattr(app, "toolpath_panel", None), "top_view", None)
        bounds = getattr(top_view, "bounds", None) if top_view else None
    if not bounds or len(bounds) < 6:
        return None
    return bounds


def _get_travel_limits(app: Any) -> dict[str, float]:
    data = getattr(getattr(app, "settings_controller", None), "_settings_data", {}) or {}
    out: dict[str, float] = {}
    for key, axis in (("$130", "x"), ("$131", "y"), ("$132", "z")):
        raw = data.get(key)
        if not raw:
            continue
        try:
            out[axis] = float(raw[0])
        except Exception:
            continue
    return out


def _is_incremental_hazard_only(report: Any) -> bool:
    if report is None:
        return False
    if int(getattr(report, "line_issue_count", 0)) <= 0:
        return False
    if int(getattr(report, "long_line_count", 0)) > 0:
        return False
    if getattr(report, "unsupported_axes", None):
        return False
    if getattr(report, "unsupported_words", None):
        return False
    if getattr(report, "unsupported_g_codes", None):
        return False
    if getattr(report, "unsupported_m_codes", None):
        return False
    if getattr(report, "grbl_warnings", None):
        return False
    hazards = set(getattr(report, "modal_hazards", set()) or set())
    expected = {"G91 (incremental distance mode)"}
    if hazards != expected:
        return False
    line_issues = list(getattr(report, "line_issues", []) or [])
    if not line_issues:
        return False
    for issue in line_issues:
        line_text = str(getattr(issue, "line", "")).strip().upper()
        if line_text != "G91":
            return False
        issue_texts = tuple(str(text) for text in getattr(issue, "issues", tuple()))
        if issue_texts != ("Modal hazard: G91 (incremental distance mode)",):
            return False
    return True


def evaluate_run_preflight(app: Any) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    path = getattr(app, "_last_gcode_path", None)
    has_job = bool(path)
    if not has_job:
        try:
            has_job = bool(getattr(app.gview, "lines_count", 0))
        except Exception:
            has_job = False
    if not has_job:
        failures.append("No G-code job is loaded.")
        return failures, warnings

    if bool(getattr(app, "_alarm_locked", False)):
        failures.append("Controller is in Alarm state. Clear alarm before running.")
    if bool(getattr(app, "_homing_in_progress", False)):
        failures.append("Homing is currently active. Wait until homing finishes.")
    if not bool(getattr(app, "_grbl_ready", False)):
        failures.append("GRBL is not ready yet. Wait for startup/status sync.")
    if not bool(getattr(app, "_status_seen", False)):
        failures.append("No live status has been received yet.")

    bounds = _get_bounds(app)
    if not bounds:
        failures.append("Toolpath bounds are unavailable (wait for parsing / top view).")
    else:
        minx, maxx, miny, maxy, minz, maxz = bounds
        span_x = max(0.0, float(maxx) - float(minx))
        span_y = max(0.0, float(maxy) - float(miny))
        span_z = max(0.0, float(maxz) - float(minz))
        travel = _get_travel_limits(app)
        if travel:
            if "x" in travel and span_x > travel["x"] + 1e-6:
                failures.append(
                    f"X span {span_x:.3f} mm exceeds machine travel $130={travel['x']:.3f} mm."
                )
            if "y" in travel and span_y > travel["y"] + 1e-6:
                failures.append(
                    f"Y span {span_y:.3f} mm exceeds machine travel $131={travel['y']:.3f} mm."
                )
            if "z" in travel and span_z > travel["z"] + 1e-6:
                failures.append(
                    f"Z span {span_z:.3f} mm exceeds machine travel $132={travel['z']:.3f} mm."
                )
        else:
            warnings.append("Machine travel settings ($130/$131/$132) are unavailable.")

    streaming_mode = bool(getattr(app, "_gcode_streaming_mode", False))
    validate_streaming = False
    try:
        validate_streaming = bool(app.validate_streaming_gcode.get())
    except Exception:
        validate_streaming = False
    if streaming_mode and not validate_streaming:
        failures.append("Streaming validation is disabled for this large file.")

    report = getattr(app, "_gcode_validation_report", None)
    if report is None:
        if streaming_mode:
            if validate_streaming:
                failures.append("Validation report is unavailable (streaming validation failed/skipped).")
        else:
            failures.append("Validation report is unavailable.")
    else:
        if _is_incremental_hazard_only(report):
            warnings.append(
                "Validation note: incremental mode (G91) is used; confirm this is intentional."
            )
        elif getattr(report, "line_issue_count", 0) > 0:
            failures.append(
                f"Validation found issues on {int(getattr(report, 'line_issue_count', 0))} line(s)."
            )
        if getattr(report, "grbl_warnings", None):
            failures.append("Validation reported GRBL incompatibility warnings.")
    return failures, warnings


def run_preflight_gate(app: Any) -> bool:
    failures, warnings = evaluate_run_preflight(app)
    if failures:
        message = "Run blocked by preflight safety gate:\n" + "\n".join(
            f"- {item}" for item in failures
        )
        if warnings:
            message += "\n\nWarnings:\n" + "\n".join(f"- {item}" for item in warnings)
        proceed = bool(messagebox.askyesno(
            "Preflight gate",
            message
            + "\n\nContinue anyway?\n"
            + "Choose Yes to override the preflight gate and start the job.",
        ))
        if proceed:
            _record_preflight_decision(
                app,
                decision="override",
                failures=failures,
                warnings=warnings,
            )
            try:
                app.ui_q.put(("log", "[preflight] Override accepted; starting despite gate failures."))
                for item in failures:
                    app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
                for item in warnings:
                    app.ui_q.put(("log", f"[preflight] warning: {item}"))
            except Exception as exc:
                _log_suppressed("Failed writing preflight override details to UI log queue", exc)
            try:
                app.status.config(text="Preflight overridden: starting job")
            except Exception as exc:
                _log_suppressed("Failed updating status text after preflight override", exc)
            return True
        _record_preflight_decision(
            app,
            decision="blocked",
            failures=failures,
            warnings=warnings,
        )
        try:
            app.ui_q.put(("log", "[preflight] Override declined; run canceled."))
            for item in failures:
                app.ui_q.put(("log", f"[preflight] blocked-check: {item}"))
            for item in warnings:
                app.ui_q.put(("log", f"[preflight] warning: {item}"))
        except Exception as exc:
            _log_suppressed("Failed writing preflight blocked decision details to UI log queue", exc)
        try:
            app.status.config(text="Run blocked: preflight gate failed")
        except Exception as exc:
            _log_suppressed("Failed updating status text when preflight blocks run", exc)
        return False
    if warnings:
        try:
            app.ui_q.put(("log", "[preflight] " + "; ".join(warnings)))
        except Exception as exc:
            _log_suppressed("Failed writing preflight warnings to UI log queue", exc)
    return True


def run_preflight_check(app) -> None:
    path = getattr(app, "_last_gcode_path", None)
    if not path:
        messagebox.showinfo("Preflight check", "Load a G-code file first.")
        return
    failures, warnings = evaluate_run_preflight(app)
    report = getattr(app, "_gcode_validation_report", None)
    issues: list[str] = []
    if failures:
        issues.extend(failures)
    if warnings:
        issues.extend(warnings)
    if report is not None:
        issues.extend(_format_validation_summary(report))
    if issues:
        title = "Preflight check"
        prefix = "Review before running:\n"
        if failures:
            title = "Preflight check (fail)"
            prefix = "Blocking issues found:\n"
        messagebox.showwarning(
            title,
            prefix + "\n".join(f"- {item}" for item in issues),
        )
        return
    messagebox.showinfo("Preflight check", "No issues detected.")


def _build_performance_report_text(app: Any) -> str:
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        build_report = getattr(perf_monitor, "build_report_snapshot", None)
        if callable(build_report):
            try:
                report = str(build_report() or "").strip()
                if report:
                    return report
            except Exception as exc:
                _log_suppressed("Failed building performance monitor report snapshot", exc)

    lines: list[str] = []
    lines.append("=== Simple Sender Performance Snapshot ===")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    metrics = _runtime_metrics(app)
    if metrics:
        lines.extend(_format_runtime_metrics(metrics, include_samples=False))
    else:
        lines.append("Runtime telemetry unavailable.")
    return "\n".join(lines)


def apply_performance_test_preset(app) -> None:
    try:
        app._status_perf_metrics_enabled = True
    except Exception as exc:
        _log_suppressed("Failed enabling status perf-metric capture in diagnostics preset", exc)
    _set_var_value(app, "performance_profile_enabled", True)
    _set_var_value(app, "performance_leak_watch_enabled", False)
    _set_var_value(app, "performance_mode", True)
    _set_var_value(app, "gui_logging_enabled", False)
    _set_var_value(app, "status_poll_interval", PERF_TEST_STATUS_POLL_INTERVAL)

    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        settings["performance_profile_enabled"] = True
        settings["performance_leak_watch_enabled"] = False
        settings["performance_mode"] = True
        settings["gui_logging_enabled"] = False
        settings["status_poll_interval"] = PERF_TEST_STATUS_POLL_INTERVAL

    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed("Failed saving settings for diagnostics perf-test preset", exc)
            messagebox.showerror("Diagnostics preset", f"Failed to save settings:\n{exc}")
            return
    try:
        app.ui_q.put(("log", "[diagnostics] Performance test preset applied (restart required)."))
    except Exception as exc:
        _log_suppressed("Failed queueing diagnostics preset status log", exc)
    messagebox.showinfo(
        "Diagnostics preset",
        (
            "Performance test preset applied.\n\n"
            "- Runtime performance profiling: ON\n"
            "- Leak-watch snapshots: OFF\n"
            "- Performance mode: ON\n"
            "- GUI logging: OFF\n"
            f"- Status poll interval: {PERF_TEST_STATUS_POLL_INTERVAL:.2f}s\n\n"
            "Restart the app before the next run for clean benchmark numbers."
        ),
    )


def save_performance_report_to_logs(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"simple_sender_performance_report_{timestamp}.txt"
    path = get_log_dir() / filename
    report = _build_performance_report_text(app)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_suppressed("Failed creating logs directory for performance report", exc)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write(report)
            outfile.write("\n")
        messagebox.showinfo("Save performance report", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Save performance report", f"Failed to write report:\n{exc}")


def _build_session_diagnostics_lines(app: Any) -> list[str]:
    lines: list[str] = []
    lines.append("Simple Sender diagnostics")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    version_text = ""
    try:
        version_text = app.version_var.get()
    except Exception:
        version_text = ""
    if version_text:
        lines.append(f"Version: {version_text}")
    lines.append(f"Connected: {getattr(app, 'connected', False)}")
    lines.append(f"Port: {getattr(app, '_connected_port', '')}")
    lines.append(f"Streaming: {getattr(app, '_stream_state', '')}")
    lines.append(f"G-code path: {getattr(app, '_last_gcode_path', '')}")
    lines.append(f"G-code streaming mode: {getattr(app, '_gcode_streaming_mode', False)}")
    lines.append(f"G-code total lines: {getattr(app, '_gcode_total_lines', 0)}")
    viewer_mode = str(getattr(app, "_gcode_viewer_mode", "") or "").strip()
    if viewer_mode:
        lines.append(f"G-code viewer mode: {viewer_mode}")
        lines.append(
            "G-code viewer policy lines: "
            f"{int(getattr(app, '_gcode_viewer_policy_line_count', 0) or 0)}"
        )
        lines.append(
            "G-code viewer preview lines: "
            f"{int(getattr(app, '_gcode_viewer_preview_line_count', 0) or 0)}"
        )
        lines.append(
            "G-code viewer virtualization threshold: "
            f"{int(getattr(app, '_gcode_viewer_virtualization_threshold', 0) or 0)}"
        )
        lines.append(
            "G-code viewer virtual window: "
            f"{int(getattr(app, '_gcode_viewer_virtual_window', 0) or 0)}"
        )
        chunk_size = getattr(app, "_gcode_viewer_chunk_size", None)
        lines.append(
            "G-code viewer chunk size: "
            + ("n/a" if chunk_size is None else str(int(chunk_size)))
        )
        lines.append(
            "G-code viewer preview_only: "
            f"{bool(getattr(app, '_gcode_viewer_preview_only', False))}"
        )
    lines.append("")
    report = getattr(app, "_gcode_validation_report", None)
    report_summary = _format_validation_summary(report)
    if report_summary:
        lines.append("Validation summary:")
        lines.extend(f"- {item}" for item in report_summary)
        lines.append("")
    metrics = _runtime_metrics(app)
    if metrics:
        lines.append("Runtime telemetry:")
        lines.extend(_format_runtime_metrics(metrics, include_samples=True, sample_limit=20))
        lines.append("")
    decisions = getattr(app, "_preflight_run_decisions", None)
    if isinstance(decisions, list) and decisions:
        lines.append("Preflight run decisions:")
        for entry in decisions[-50:]:
            if not isinstance(entry, dict):
                continue
            stamp = str(entry.get("timestamp", "") or "")
            decision = str(entry.get("decision", "") or "")
            job_name = str(entry.get("job_name", "") or "")
            job_hash = str(entry.get("job_hash", "") or "")
            failure_count = int(entry.get("failure_count", 0) or 0)
            warning_count = int(entry.get("warning_count", 0) or 0)
            summary = (
                f"- {stamp} | decision={decision or 'unknown'}"
                f" | job={job_name or '<unknown>'}"
                f" | hash={job_hash or 'n/a'}"
                f" | failures={failure_count}"
                f" | warnings={warning_count}"
            )
            lines.append(summary)
            failures = entry.get("failures", [])
            if isinstance(failures, list):
                for item in failures:
                    lines.append(f"  FAIL: {item}")
            warnings = entry.get("warnings", [])
            if isinstance(warnings, list):
                for item in warnings:
                    lines.append(f"  WARN: {item}")
        lines.append("")
    last_status = getattr(app, "_last_status_raw", "")
    if last_status:
        lines.append("Last status:")
        lines.append(last_status.strip())
        lines.append("")
    history = getattr(app, "_status_history", [])
    history_entries = list(history) if history else []
    if history_entries:
        lines.append("Recent status history:")
        for ts, raw in history_entries[-50:]:
            stamp = datetime.fromtimestamp(ts).isoformat(timespec="seconds")
            lines.append(f"{stamp} {raw.strip()}")
        lines.append("")
    connection_history = getattr(app, "_connection_timeline", [])
    connection_entries = list(connection_history) if connection_history else []
    if connection_entries:
        lines.append("Connection timeline:")
        for entry in connection_entries[-80:]:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts")
            try:
                stamp = datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
            except Exception:
                stamp = str(ts or "n/a")
            event = str(entry.get("event", "") or "unknown")
            details = str(entry.get("details", "") or "")
            if details:
                lines.append(f"{stamp} {event} | {details}")
            else:
                lines.append(f"{stamp} {event}")
        lines.append("")
    console_lines = []
    try:
        console_lines = app.streaming_controller.get_console_lines()
    except Exception:
        console_lines = []
    if console_lines:
        lines.append("Recent console log:")
        for entry, tag in console_lines[-200:]:
            tag_text = f"[{tag}] " if tag else ""
            lines.append(f"{tag_text}{entry}")
        lines.append("")
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        lines.append("Settings:")
        lines.append(json.dumps(settings, indent=2, sort_keys=True))
        lines.append("")
    return lines


def _collect_diagnostics_bundle_payload(app: Any) -> dict[str, Any]:
    session_text = "\n".join(_build_session_diagnostics_lines(app)) + "\n"
    perf_text = _build_performance_report_text(app).strip() + "\n"
    runtime_metrics_json = _json_dump(_runtime_metrics(app)) + "\n"
    connection_timeline_json = _json_dump(list(getattr(app, "_connection_timeline", []) or [])) + "\n"
    system_info_text = _build_system_info_text(app)
    settings_snapshot_json = _json_dump(getattr(app, "settings", {}) or {}) + "\n"
    settings_path = _resolved_settings_path(app)
    macro_assets = discover_macro_assets(app)
    log_dir = get_log_dir()
    try:
        log_candidates = list(log_dir.iterdir())
    except Exception as exc:
        _log_suppressed("Failed enumerating log files for diagnostics bundle", exc)
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
        key=lambda p: p.name,
    )
    bundle_manifest: dict[str, Any] = {
        "kind": "simple_sender_diagnostics_bundle",
        "created": datetime.now().isoformat(timespec="seconds"),
        "version": str(getattr(getattr(app, "version_var", None), "get", lambda: "")() or ""),
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
        "bundle_manifest": bundle_manifest,
    }


def _write_diagnostics_bundle_archive(out_path: Path, payload: dict[str, Any]) -> None:
    bundle_manifest = dict(payload.get("bundle_manifest", {}) or {})
    files_section = dict(bundle_manifest.get("files", {}) or {})
    bundle_manifest["files"] = files_section
    settings_path = payload.get("settings_path")
    macro_assets = list(payload.get("macro_assets", []) or [])
    log_files = list(payload.get("log_files", []) or [])
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("session_diagnostics.txt", str(payload.get("session_text", "")))
        archive.writestr("performance_report.txt", str(payload.get("perf_text", "")))
        archive.writestr("runtime_metrics.json", str(payload.get("runtime_metrics_json", "")))
        archive.writestr("connection_timeline.json", str(payload.get("connection_timeline_json", "")))
        archive.writestr("system_info.txt", str(payload.get("system_info_text", "")))
        archive.writestr(
            "settings/runtime_settings_snapshot.json",
            str(payload.get("settings_snapshot_json", "")),
        )
        if isinstance(settings_path, Path):
            try:
                archive.write(settings_path, arcname="settings/settings.json")
            except Exception as exc:
                _log_suppressed("Failed adding settings file to diagnostics bundle", exc)
        macro_added = 0
        for source, name in macro_assets:
            try:
                archive.write(source, arcname=f"macros/{os.path.basename(name)}")
                macro_added += 1
            except Exception as exc:
                _log_suppressed("Failed adding macro/checklist asset to diagnostics bundle", exc)
        log_added = 0
        for log_path in log_files:
            try:
                archive.write(log_path, arcname=f"logs/{log_path.name}")
                log_added += 1
            except Exception as exc:
                _log_suppressed("Failed adding log file to diagnostics bundle", exc)
        files_section["log_count"] = log_added
        files_section["macro_asset_count"] = macro_added
        archive.writestr("manifest.json", _json_dump(bundle_manifest))


def export_diagnostics_bundle(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_bundle_{timestamp}.zip"
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Export diagnostics bundle",
        defaultextension=".zip",
        initialfile=default_name,
        filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
    )
    if not path:
        return
    out_path = Path(path)
    if out_path.parent:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            _log_suppressed("Failed creating export directory for diagnostics bundle", exc)

    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed("Failed saving settings before diagnostics-bundle export", exc)

    payload = _collect_diagnostics_bundle_payload(app)
    use_background_export = bool(getattr(app, "_diagnostics_bundle_async_export", True))
    if use_background_export and callable(getattr(app, "after", None)):
        if bool(getattr(app, "_diagnostics_bundle_export_inflight", False)):
            messagebox.showinfo(
                "Export diagnostics bundle",
                "A diagnostics bundle export is already running.",
            )
            return
        app._diagnostics_bundle_export_inflight = True
        try:
            app.ui_q.put(("log", "[diagnostics] Exporting diagnostics bundle..."))
        except Exception:
            pass

        def _complete_export(error: Exception | None = None) -> None:
            app._diagnostics_bundle_export_inflight = False
            if error is None:
                messagebox.showinfo("Export diagnostics bundle", f"Saved to:\n{out_path}")
                return
            messagebox.showerror("Export diagnostics bundle", f"Failed to create bundle:\n{error}")

        after = getattr(app, "after")

        def _export_worker() -> None:
            error: Exception | None = None
            try:
                _write_diagnostics_bundle_archive(out_path, payload)
            except Exception as exc:
                error = exc
            try:
                after(0, lambda: _complete_export(error))
            except Exception as exc:
                _log_suppressed("Failed posting diagnostics bundle completion callback", exc)
                _complete_export(error)

        try:
            worker = threading.Thread(
                target=_export_worker,
                name="diagnostics-bundle-export",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            app._diagnostics_bundle_export_inflight = False
            _log_suppressed("Failed starting diagnostics bundle export thread", exc)

    try:
        _write_diagnostics_bundle_archive(out_path, payload)
        messagebox.showinfo("Export diagnostics bundle", f"Saved to:\n{out_path}")
    except Exception as exc:
        messagebox.showerror("Export diagnostics bundle", f"Failed to create bundle:\n{exc}")


def export_session_diagnostics(app) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_{timestamp}.txt"
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Export diagnostics",
        defaultextension=".txt",
        initialfile=default_name,
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
    )
    if not path:
        return
    lines = _build_session_diagnostics_lines(app)
    dir_name = os.path.dirname(path)
    if dir_name:
        try:
            os.makedirs(dir_name, exist_ok=True)
        except Exception as exc:
            _log_suppressed("Failed creating export directory for diagnostics report", exc)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as outfile:
            outfile.write("\n".join(lines))
        messagebox.showinfo("Export diagnostics", f"Saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Export diagnostics", f"Failed to write diagnostics:\n{exc}")
