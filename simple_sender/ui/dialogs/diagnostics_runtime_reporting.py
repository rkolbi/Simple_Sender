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

"""Runtime metrics formatting helpers for diagnostics reporting."""

from __future__ import annotations

from typing import Any


def format_mb(value_bytes: Any) -> str:
    try:
        if value_bytes is None:
            return "n/a"
        return f"{(float(value_bytes) / (1024.0 * 1024.0)):.2f} MB"
    except (TypeError, ValueError):
        return "n/a"


def format_runtime_metrics(
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int = 20,
) -> list[str]:
    if not metrics:
        return []
    lines: list[str] = []
    _append_worker_runtime_metrics(
        lines,
        metrics,
        include_samples=include_samples,
        sample_limit=sample_limit,
    )
    perf_available = bool(metrics.get("perf_available", False))
    if not perf_available:
        lines.append(
            "- Perf monitor disabled (enable runtime performance profiling for CPU/RSS telemetry)."
        )
        return lines
    _append_perf_monitor_summary(lines, metrics)
    _append_stream_and_motion_metrics(lines, metrics)
    _append_wait_and_prepare_metrics(lines, metrics)
    _append_estimator_and_auto_level_metrics(lines, metrics)
    _append_storage_snapshot_metrics(lines, metrics)
    return lines


def _resolve_headless_live_ack_summary(metrics: dict[str, Any]) -> tuple[int, int]:
    """Choose the most truthful ack summary for headless/file-backed diagnostics."""
    legacy_idx = int(metrics.get("live_gcode_last_acked_index", -1) or -1)
    legacy_offset = int(metrics.get("live_gcode_last_acked_byte_offset", 0) or 0)
    current_idx = int(metrics.get("live_gcode_current_acked_index", -1) or -1)
    acked_offset = int(metrics.get("acked_byte_offset", legacy_offset) or 0)
    storage_mode = str(metrics.get("gcode_storage_mode", "") or "").strip().lower()
    stream_file_size_bytes = int(metrics.get("stream_file_size_bytes", 0) or 0)

    resolved_idx = legacy_idx
    if current_idx >= 0:
        resolved_idx = current_idx

    resolved_offset = legacy_offset
    if storage_mode == "file_backed_streaming" and (
        current_idx >= 0 or acked_offset > 0 or stream_file_size_bytes > 0
    ):
        resolved_offset = max(0, acked_offset)

    return int(resolved_idx), int(resolved_offset)


def _append_worker_runtime_metrics(
    lines: list[str],
    metrics: dict[str, Any],
    *,
    include_samples: bool,
    sample_limit: int,
) -> None:
    kasa_status_line = str(metrics.get("kasa_status_line", "") or "").strip()
    if kasa_status_line:
        lines.append(f"- Kasa status: {kasa_status_line}")
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
        ack_last = float(
            metrics.get(
                "ack_latency_ms_last",
                metrics.get("ok_latency_ms_last", 0.0),
            )
            or 0.0
        )
        ack_avg = float(
            metrics.get(
                "ack_latency_ms_avg",
                metrics.get("ok_latency_ms_avg", 0.0),
            )
            or 0.0
        )
        ack_samples = int(
            metrics.get(
                "ack_latency_samples",
                metrics.get("ok_latency_samples", 0),
            )
            or 0
        )
        lines.append(f"- TX lines/sec: {tx_lines_per_sec:.2f}")
        lines.append(
            f"- ACK latency ms: last={ack_last:.2f}, avg={ack_avg:.2f}, samples={ack_samples}"
        )
        configured_poll = float(
            metrics.get("status_poll_interval_configured_s", 0.0) or 0.0
        )
        effective_poll = float(
            metrics.get(
                "status_poll_interval_effective_s",
                metrics.get("status_poll_interval_s", 0.0),
            )
            or 0.0
        )
        if configured_poll > 0.0 and effective_poll > 0.0:
            lines.append(
                "- Status poll interval s: "
                f"configured={configured_poll:.3f}, effective={effective_poll:.3f}"
            )
        elif effective_poll > 0.0:
            lines.append(f"- Status poll interval s: {effective_poll:.3f}")
        tx_loop_cycles = int(metrics.get("tx_loop_cycles", 0) or 0)
        tx_loop_idle_cycles = int(metrics.get("tx_loop_idle_cycles", 0) or 0)
        tx_loop_active_cycles = int(metrics.get("tx_loop_active_cycles", 0) or 0)
        tx_loop_idle_wait_total_s = float(
            metrics.get("tx_loop_idle_wait_total_s", 0.0) or 0.0
        )
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


def _append_perf_monitor_summary(lines: list[str], metrics: dict[str, Any]) -> None:
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
        lines.append(
            f"- Idle CPU avg/p95: {float(idle_cpu_avg):.2f}% / {float(idle_cpu_p95):.2f}%"
        )
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
    lines.append(
        f"- RSS start/current/peak: {format_mb(metrics.get('perf_rss_start_bytes'))} / "
        f"{format_mb(metrics.get('perf_rss_current_bytes'))} / "
        f"{format_mb(metrics.get('perf_rss_peak_bytes'))}"
    )
    steady_after = metrics.get("perf_steady_state_after_s")
    steady_label = (
        f"{int(float(steady_after))}s" if steady_after is not None else "steady-state"
    )
    lines.append(
        f"- RSS {steady_label}: {format_mb(metrics.get('perf_rss_steady_state_bytes'))}"
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
        budget_text = (
            f"{float(stall_budget):.1f}" if stall_budget is not None else "n/a"
        )
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
            slow_kind = str(
                metrics.get("perf_ui_queue_drain_runtime_slowest_event_kind", "") or ""
            )
            lines.append(
                "- UI queue slowest runtime event: "
                f"{slow_kind or 'unknown'} ({slow_ms:.2f} ms)"
            )
    phase_metrics = metrics.get("perf_phase_metrics")
    if isinstance(phase_metrics, dict) and phase_metrics:
        lines.append("- Phase CPU/RSS:")
        for phase_name in ("idle_connected", "streaming"):
            phase = phase_metrics.get(phase_name)
            if not isinstance(phase, dict):
                continue
            label = phase_name.replace("_", " ")
            samples = int(phase.get("samples", 0) or 0)
            cpu_avg = phase.get("cpu_avg")
            cpu_p95 = phase.get("cpu_p95")
            if cpu_avg is None or cpu_p95 is None:
                cpu_text = "n/a"
            else:
                cpu_text = f"{float(cpu_avg):.2f}% / {float(cpu_p95):.2f}%"
            lines.append(
                "  "
                f"{label}: "
                f"cpu avg/p95={cpu_text}, "
                f"samples={samples}, "
                f"rss start/current/peak="
                f"{format_mb(phase.get('rss_start_bytes'))} / "
                f"{format_mb(phase.get('rss_current_bytes'))} / "
                f"{format_mb(phase.get('rss_peak_bytes'))}"
            )
    phase_sampling_note = str(metrics.get("perf_phase_sampling_note", "") or "").strip()
    if phase_sampling_note:
        lines.append(f"- Phase sampling: {phase_sampling_note}")
    hidden_contributors = metrics.get("perf_hidden_idle_contributors")
    if isinstance(hidden_contributors, list) and hidden_contributors:
        lines.append("- Hidden idle contributors (top):")
        for entry in hidden_contributors[:5]:
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  "
                f"{str(entry.get('source', '') or 'unknown')}: "
                f"samples={int(entry.get('active_samples', 0) or 0)}, "
                f"cpu_ms={float(entry.get('active_cpu_ms', 0.0) or 0.0):.2f}, "
                f"events={int(entry.get('task_count', 0) or 0)}, "
                f"task_ms={float(entry.get('task_ms', 0.0) or 0.0):.2f}"
            )
    outlier_total = metrics.get("perf_ui_queue_drain_outlier_total")
    outlier_entries = metrics.get("perf_ui_queue_drain_outliers")
    runtime_stall_total = metrics.get("perf_ui_queue_drain_runtime_stall_total")
    runtime_stall_entries = metrics.get("perf_ui_queue_drain_runtime_stalls")
    if isinstance(outlier_total, (int, float)) or (
        isinstance(outlier_entries, list) and outlier_entries
    ):
        lines.append(f"- UI queue outlier ticks captured: {int(outlier_total or 0)}")
    if isinstance(runtime_stall_total, (int, float)) or (
        isinstance(runtime_stall_entries, list) and runtime_stall_entries
    ):
        lines.append(
            f"- UI queue runtime stalls captured: {int(runtime_stall_total or 0)}"
        )
    if isinstance(runtime_stall_entries, list) and runtime_stall_entries:
        lines.append("- UI queue runtime stall details (recent):")
        for entry in runtime_stall_entries[-5:]:
            if not isinstance(entry, dict):
                continue
            tick_ms = float(entry.get("tick_ms", 0.0) or 0.0)
            events = int(entry.get("events_drained", 0) or 0)
            pending_after = int(entry.get("pending_after_tick", 0) or 0)
            slow_kind = str(entry.get("slowest_event_kind", "") or "unknown")
            slow_ms = float(entry.get("slowest_event_ms", 0.0) or 0.0)
            budget_ms = float(entry.get("stall_budget_ms", 0.0) or 0.0)
            lines.append(
                "  "
                f"tick={tick_ms:.2f} ms, budget={budget_ms:.2f} ms, "
                f"events={events}, pending={pending_after}, "
                f"slowest={slow_kind} ({slow_ms:.2f} ms)"
            )
    if isinstance(outlier_entries, list) and outlier_entries:
        lines.append("- UI queue outlier details (recent):")
        for entry in outlier_entries[-5:]:
            if not isinstance(entry, dict):
                continue
            tick_ms = float(entry.get("tick_ms", 0.0) or 0.0)
            tab = str(entry.get("active_tab", "") or "unknown")
            events = int(entry.get("events_drained", 0) or 0)
            pending_after = int(entry.get("pending_after_tick", 0) or 0)
            slow_kind = str(entry.get("slowest_event_kind", "") or "unknown")
            slow_ms = float(entry.get("slowest_event_ms", 0.0) or 0.0)
            lines.append(
                "  "
                f"tick={tick_ms:.2f} ms, tab={tab}, events={events}, pending={pending_after}, "
                f"slowest={slow_kind} ({slow_ms:.2f} ms)"
            )
            top_kind_timing = entry.get("top_kind_timing")
            if isinstance(top_kind_timing, list) and top_kind_timing:
                top_parts: list[str] = []
                for item in top_kind_timing[:5]:
                    if not isinstance(item, dict):
                        continue
                    kind = str(item.get("kind", "") or "unknown")
                    total_ms = float(item.get("ms", 0.0) or 0.0)
                    count = int(item.get("count", 0) or 0)
                    top_parts.append(f"{kind}={total_ms:.2f}ms ({count})")
                if top_parts:
                    lines.append("    top kinds: " + ", ".join(top_parts))
            per_kind_counts = entry.get("per_kind_counts")
            if isinstance(per_kind_counts, dict) and per_kind_counts:
                count_parts = []
                for idx, (kind, count) in enumerate(per_kind_counts.items()):
                    if idx >= 8:
                        break
                    count_parts.append(f"{str(kind)}={int(count)}")
                if count_parts:
                    lines.append("    event counts: " + ", ".join(count_parts))
            special_ops = entry.get("special_ops")
            if isinstance(special_ops, list) and special_ops:
                lines.append(
                    "    special ops: "
                    + ", ".join(str(item) for item in special_ops if item)
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
        load_end = task_timings.get("gcode.load.end_to_end")
        if isinstance(load_end, dict):
            lines.append(
                "- Preparing Job timing (end-to-end): "
                f"count={int(load_end.get('count', 0) or 0)}, "
                f"avg={float(load_end.get('avg_ms', 0.0) or 0.0):.2f} ms, "
                f"max={float(load_end.get('max_ms', 0.0) or 0.0):.2f} ms"
            )
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


def _append_stream_and_motion_metrics(
    lines: list[str], metrics: dict[str, Any]
) -> None:
    storage_mode = str(metrics.get("gcode_storage_mode", "") or "").strip()
    load_mode = str(metrics.get("gcode_load_mode", "") or "").strip() or "n/a"
    index_mode = str(metrics.get("gcode_index_mode", "") or "").strip() or "n/a"
    line_count_known = bool(metrics.get("gcode_source_line_count_known", True))
    time_to_ready_ms = metrics.get("gcode_time_to_stream_ready_ms")
    time_to_popup_ms = metrics.get("gcode_time_to_popup_close_ms")
    if storage_mode:
        timing_parts: list[str] = []
        try:
            if time_to_ready_ms is not None:
                timing_parts.append(
                    f"time_to_stream_ready_ms={float(time_to_ready_ms):.2f}"
                )
        except (TypeError, ValueError):
            pass
        try:
            if time_to_popup_ms is not None:
                timing_parts.append(
                    f"time_to_popup_close_ms={float(time_to_popup_ms):.2f}"
                )
        except (TypeError, ValueError):
            pass
        detail = (
            f"{storage_mode}, load_mode={load_mode}, index_mode={index_mode}, "
            f"line_count_known={line_count_known}"
        )
        if timing_parts:
            detail += ", " + ", ".join(timing_parts)
        lines.append("- G-code storage mode: " + detail)
    quick_scan_ms = float(metrics.get("gcode_quick_scan_ms", 0.0) or 0.0)
    bounds_confidence = (
        str(metrics.get("gcode_bounds_confidence", "") or "").strip() or "rough"
    )
    dimensions_confidence = (
        str(metrics.get("dimensions_confidence", "") or "").strip() or "rough"
    )
    estimate_confidence = (
        str(metrics.get("estimate_confidence", "") or "").strip() or "rough"
    )
    estimated_job_time_sec = metrics.get("estimated_job_time_sec")
    file_size_bytes = int(metrics.get("gcode_file_size_bytes", 0) or 0)
    total_lines = int(metrics.get("gcode_total_lines", 0) or 0)
    total_lines_known = bool(metrics.get("gcode_total_lines_known", False))
    executable_lines = int(metrics.get("gcode_executable_lines", 0) or 0)
    executable_lines_known = bool(metrics.get("gcode_executable_lines_known", False))
    motion_lines = int(metrics.get("gcode_motion_lines", 0) or 0)
    motion_lines_known = bool(metrics.get("gcode_motion_lines_known", False))
    ssmeta_present = bool(metrics.get("ssmeta_present", False))
    dimensions_source = str(metrics.get("dimensions_source", "scan") or "scan")
    units_source = str(metrics.get("units_source", "scan") or "scan")
    ssmeta_scan_reduced = bool(metrics.get("ssmeta_scan_reduced", False))
    lines.append(
        "- Quick Assessment: "
        f"quick_scan_ms={quick_scan_ms:.2f}, "
        f"file_size_bytes={file_size_bytes:,}, "
        f"line_count={total_lines:,} ({'known' if total_lines_known else 'estimated'}), "
        f"estimated_job_time_sec={int(estimated_job_time_sec) if estimated_job_time_sec is not None else 'n/a'}, "
        f"estimate_confidence={estimate_confidence}, "
        f"dimensions_confidence={dimensions_confidence}, "
        f"bounds_confidence={bounds_confidence}"
    )
    lines.append(
        "- SSMETA: "
        f"{'present' if ssmeta_present else 'not_found'}, "
        f"dimensions_source={dimensions_source}, "
        f"units_source={units_source}, "
        f"scan_reduced={ssmeta_scan_reduced}"
    )
    lines.append(
        "- Line counters: "
        f"total={total_lines:,} ({'known' if total_lines_known else 'estimated'}), "
        f"executable={executable_lines:,} ({'known' if executable_lines_known else 'estimated'}), "
        f"motion={motion_lines:,} ({'known' if motion_lines_known else 'estimated'})"
    )
    stream_file_size_bytes = int(metrics.get("stream_file_size_bytes", 0) or 0)
    acked_byte_offset = int(metrics.get("acked_byte_offset", 0) or 0)
    stream_progress_pct = float(metrics.get("stream_progress_pct", 0.0) or 0.0)
    stream_done_pending_idle = bool(metrics.get("stream_done_pending_idle", False))
    stream_done_wait_active = bool(metrics.get("stream_done_wait_active", False))
    stream_done_wait_current_s = float(
        metrics.get("stream_done_wait_current_s", 0.0) or 0.0
    )
    stream_done_wait_last_s = float(
        metrics.get("stream_done_wait_last_s", 0.0) or 0.0
    )
    stream_done_wait_total_s = float(
        metrics.get("stream_done_wait_total_s", 0.0) or 0.0
    )
    stream_done_wait_count = int(metrics.get("stream_done_wait_count", 0) or 0)
    lines.append(
        "- Stream byte progress: "
        f"stream_progress_pct={stream_progress_pct:.1f}, "
        f"acked_byte_offset={acked_byte_offset:,}, "
        f"file_size_bytes={stream_file_size_bytes:,}"
    )
    if stream_done_wait_active or stream_done_wait_count > 0:
        lines.append(
            "- Deferred completion wait: "
            f"pending_idle={stream_done_pending_idle}, "
            f"active={stream_done_wait_active}, "
            f"count={stream_done_wait_count}, "
            f"current_s={stream_done_wait_current_s:.3f}, "
            f"last_s={stream_done_wait_last_s:.3f}, "
            f"total_s={stream_done_wait_total_s:.3f}"
        )
    live_past = int(metrics.get("live_gcode_past_count", 0) or 0)
    live_current = int(metrics.get("live_gcode_current_count", 0) or 0)
    live_next = int(metrics.get("live_gcode_next_count", 0) or 0)
    live_pending = int(metrics.get("live_gcode_pending_depth", 0) or 0)
    live_last_idx, live_last_offset = _resolve_headless_live_ack_summary(metrics)
    lines.append(
        "- Headless live G-code state: "
        f"past={live_past}, current={live_current}, next={live_next}, "
        f"pending_depth={live_pending}, last_acked_index={live_last_idx}, "
        f"last_acked_byte_offset={live_last_offset:,}"
    )
    jog_trace_count = int(metrics.get("jog_dro_trace_count", 0) or 0)
    jog_active = bool(metrics.get("jog_dro_interp_active", False))
    jog_stats = metrics.get("jog_dro_interp_stats")
    if isinstance(jog_stats, dict):
        sync_interval_avg_s = float(
            jog_stats.get("status_sync_interval_avg_s", 0.0) or 0.0
        )
        sync_interval_max_s = float(
            jog_stats.get("status_sync_interval_max_s", 0.0) or 0.0
        )
        horizon_avg_s = float(jog_stats.get("predict_horizon_avg_s", 0.0) or 0.0)
        horizon_max_s = float(jog_stats.get("predict_horizon_max_s", 0.0) or 0.0)
        lines.append(
            "- Jog DRO interpolation: "
            f"active={jog_active}, trace_samples={jog_trace_count}, "
            f"status_sync={int(jog_stats.get('status_sync_count', 0) or 0)}, "
            f"delta_abs_avg=({float(jog_stats.get('delta_abs_avg_x', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_avg_y', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_avg_z', 0.0) or 0.0):.4f}), "
            f"delta_abs_max=({float(jog_stats.get('delta_abs_max_x', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_max_y', 0.0) or 0.0):.4f},"
            f"{float(jog_stats.get('delta_abs_max_z', 0.0) or 0.0):.4f}), "
            f"sync_interval_avg_s={sync_interval_avg_s:.3f}, "
            f"sync_interval_max_s={sync_interval_max_s:.3f}, "
            f"horizon_avg_s={horizon_avg_s:.3f}, "
            f"horizon_max_s={horizon_max_s:.3f}"
        )
    elif jog_trace_count > 0 or jog_active:
        lines.append(
            "- Jog DRO interpolation: "
            f"active={jog_active}, trace_samples={jog_trace_count}"
        )
    query_interval_avg = float(
        metrics.get("manual_motion_status_query_interval_avg_ms", 0.0) or 0.0
    )
    query_interval_max = float(
        metrics.get("manual_motion_status_query_interval_max_ms", 0.0) or 0.0
    )
    query_count = int(metrics.get("manual_motion_status_query_count", 0) or 0)
    rx_interval_avg = float(
        metrics.get("manual_motion_status_rx_interval_avg_ms", 0.0) or 0.0
    )
    rx_interval_max = float(
        metrics.get("manual_motion_status_rx_interval_max_ms", 0.0) or 0.0
    )
    rx_count = int(metrics.get("manual_motion_status_rx_count", 0) or 0)
    if query_count > 0 or rx_count > 0:
        lines.append(
            "- Manual-motion status cadence: "
            f"tx_status_query_interval_ms(avg/max)={query_interval_avg:.2f}/{query_interval_max:.2f} "
            f"(samples={query_count}), "
            f"rx_status_interval_ms(avg/max)={rx_interval_avg:.2f}/{rx_interval_max:.2f} "
            f"(samples={rx_count})"
        )
    session_count = int(metrics.get("manual_motion_status_session_count", 0) or 0)
    if session_count > 0:
        session_active = bool(metrics.get("manual_motion_status_session_active", False))
        last_source = str(
            metrics.get("manual_motion_status_session_last_source", "") or ""
        )
        lines.append(
            "- Manual-motion sessions: "
            f"count={session_count}, active={session_active}, "
            f"last_source={last_source or 'n/a'}"
        )
    line_batch_avg = float(metrics.get("stream_line_batch_avg_lines", 0.0) or 0.0)
    line_batch_max = int(metrics.get("stream_line_batch_max_lines", 0) or 0)
    line_batch_total = int(metrics.get("stream_line_batch_count", 0) or 0)
    if line_batch_total > 0:
        lines.append(
            "- Stream line-batch stats: "
            f"count={line_batch_total}, avg_lines={line_batch_avg:.2f}, "
            f"max_lines={line_batch_max}"
        )
    stream_read_calls = int(metrics.get("stream_read_call_count", 0) or 0)
    if stream_read_calls > 0:
        stream_read_avg = float(metrics.get("stream_read_avg_bytes", 0.0) or 0.0)
        stream_read_max = int(metrics.get("stream_read_max_bytes", 0) or 0)
        lines.append(
            "- Stream read size stats: "
            f"count={stream_read_calls}, avg_bytes={stream_read_avg:.2f}, "
            f"max_bytes={stream_read_max}"
        )


def _append_wait_and_prepare_metrics(
    lines: list[str], metrics: dict[str, Any]
) -> None:
    status_wait_samples = int(metrics.get("status_wait_sample_count", 0) or 0)
    if status_wait_samples > 0:
        wait_avg_ms = float(metrics.get("status_wait_actual_avg_ms", 0.0) or 0.0)
        wait_requested_avg_ms = float(
            metrics.get("status_wait_requested_avg_ms", 0.0) or 0.0
        )
        wait_overshoot_max_lifetime_ms = float(
            metrics.get("status_wait_overshoot_max_ms", 0.0) or 0.0
        )
        wait_overshoot_recent_ms = float(
            metrics.get("status_wait_overshoot_max_recent_ms", 0.0)
            or wait_overshoot_max_lifetime_ms
        )
        wait_recent_window_s = float(
            metrics.get("status_wait_overshoot_recent_window_s", 60.0) or 60.0
        )
        wait_overshoot_alert_recent = bool(
            metrics.get("status_wait_overshoot_alert_recent", False)
        )
        wait_last_reason = str(metrics.get("status_wait_last_reason", "") or "")
        lines.append(
            "- Status loop wait telemetry: "
            f"requested_avg_ms={wait_requested_avg_ms:.2f}, "
            f"actual_avg_ms={wait_avg_ms:.2f}, "
            f"overshoot_max_recent_ms={wait_overshoot_recent_ms:.2f} "
            f"(window={wait_recent_window_s:.0f}s, lifetime={wait_overshoot_max_lifetime_ms:.2f}), "
            f"overshoot_alert_recent={wait_overshoot_alert_recent}, "
            f"last_reason={wait_last_reason or 'n/a'}, "
            f"samples={status_wait_samples}"
        )
    bounds_box = metrics.get("gcode_bounds_box")
    if isinstance(bounds_box, dict):
        try:
            lines.append(
                "- Bounds footprint box: "
                f"X[{float(bounds_box.get('min_x', 0.0)):.3f}..{float(bounds_box.get('max_x', 0.0)):.3f}] "
                f"Y[{float(bounds_box.get('min_y', 0.0)):.3f}..{float(bounds_box.get('max_y', 0.0)):.3f}] "
                f"Z[{float(bounds_box.get('min_z', 0.0)):.3f}..{float(bounds_box.get('max_z', 0.0)):.3f}] "
                f"size={float(bounds_box.get('width', 0.0)):.3f}x{float(bounds_box.get('height', 0.0)):.3f} mm"
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        "- Post-popup background tasks: "
        + str(metrics.get("post_popup_background_tasks", "none") or "none")
    )
    cap_lines = metrics.get("gcode_line_cache_cap_lines")
    cap_profile = str(metrics.get("gcode_line_cache_cap_profile", "") or "").strip()
    cap_hit = metrics.get("gcode_line_cache_cap_hit")
    sample_cap = metrics.get("gcode_sample_line_cap")
    if cap_lines is not None:
        detail = f"cap={int(cap_lines):,} lines"
        if cap_profile:
            detail += f" ({cap_profile})"
        if cap_hit is not None:
            detail += f", cap_hit={bool(cap_hit)}"
        if sample_cap is not None:
            detail += f", sample_cap={int(sample_cap):,}"
        lines.append("- G-code line-cache policy: " + detail)
    retained = metrics.get("gcode_retained_line_count")
    live_state_estimate = metrics.get("headless_live_state_line_estimate")
    retention_parts: list[str] = []
    if retained is not None:
        retention_parts.append(f"retained_lines={int(retained):,}")
    if live_state_estimate is not None:
        retention_parts.append(
            f"headless_live_state_lines_est={int(live_state_estimate):,}"
        )
    if retention_parts:
        lines.append("- G-code retained footprint: " + ", ".join(retention_parts))
    source_offsets = metrics.get("gcode_source_offset_count")
    source_offset_type = str(metrics.get("gcode_source_offset_type", "") or "").strip()
    offset_index_enabled = bool(metrics.get("gcode_offset_index_enabled", False))
    if source_offsets is not None:
        offset_details = f"offsets={int(source_offsets):,}"
        if source_offset_type:
            offset_details += f" ({source_offset_type})"
        offset_details += f", enabled={offset_index_enabled}"
        lines.append("- File-backed source index: " + offset_details)
    sample_lines = int(metrics.get("gcode_prepare_sample_line_count", 0) or 0)
    sample_head = int(metrics.get("gcode_prepare_sample_head_lines", 0) or 0)
    sample_tail = int(metrics.get("gcode_prepare_sample_tail_lines", 0) or 0)
    sample_interval = int(metrics.get("gcode_prepare_sample_interval_lines", 0) or 0)
    sample_max = int(metrics.get("gcode_prepare_sample_max_lines", 0) or 0)
    prep_exec_total = int(metrics.get("gcode_prepare_executable_total_lines", 0) or 0)
    prep_motion_total = int(metrics.get("gcode_prepare_motion_total_lines", 0) or 0)
    prep_sample_exec = int(
        metrics.get("gcode_prepare_sampled_executable_lines", 0) or 0
    )
    prep_sample_motion = int(metrics.get("gcode_prepare_sampled_motion_lines", 0) or 0)
    stats_mode = str(metrics.get("gcode_stats_compute_mode", "") or "").strip() or "n/a"
    stats_scale = float(metrics.get("gcode_stats_sample_scale", 1.0) or 1.0)
    stats_sample_lines = int(metrics.get("gcode_stats_sample_line_count", 0) or 0)
    stats_sample_total = int(metrics.get("gcode_stats_sample_total_lines", 0) or 0)
    stats_sample_executable = int(
        metrics.get("gcode_stats_sample_executable_lines", 0) or 0
    )
    stats_sample_motion = int(metrics.get("gcode_stats_sample_motion_lines", 0) or 0)
    stats_total_executable = int(
        metrics.get("gcode_stats_executable_total_lines", 0) or 0
    )
    stats_total_motion = int(metrics.get("gcode_stats_motion_total_lines", 0) or 0)
    stats_chunk_max_ms = float(metrics.get("gcode_stats_chunk_max_ms", 0.0) or 0.0)
    stats_chunk_max_section = str(
        metrics.get("gcode_stats_chunk_max_section", "") or "unknown"
    )
    stats_chunk_yields = int(metrics.get("gcode_stats_chunk_yield_count", 0) or 0)
    lines.append(
        "- Prepare sample policy: "
        f"sample_lines={sample_lines:,}, head={sample_head:,}, tail={sample_tail:,}, "
        f"interval={sample_interval:,}, max={sample_max:,}"
    )
    lines.append(
        "- Prepare sampled counts: "
        f"sample_exec={prep_sample_exec:,}, sample_motion={prep_sample_motion:,}, "
        f"total_exec={prep_exec_total:,}, total_motion={prep_motion_total:,}"
    )
    lines.append(
        "- Prepare sampled modes: "
        f"stats={stats_mode}, "
        f"stats_scale={stats_scale:.2f}, stats_sample={stats_sample_lines:,}/{stats_sample_total:,}"
    )
    lines.append(
        "- Estimate sample coverage: "
        f"sample_exec={stats_sample_executable:,}, sample_motion={stats_sample_motion:,}, "
        f"total_exec={stats_total_executable:,}, total_motion={stats_total_motion:,}"
    )
    lines.append(
        "- Stats cooperative chunking: "
        f"chunk_max_ms={stats_chunk_max_ms:.2f}, chunk_max_section={stats_chunk_max_section}, yields={stats_chunk_yields:,}"
    )


def _append_estimator_and_auto_level_metrics(
    lines: list[str], metrics: dict[str, Any]
) -> None:
    loaded_estimate_min = metrics.get("estimate_loaded_total_min")
    loaded_estimate_source = str(
        metrics.get("estimate_loaded_source", "") or ""
    ).strip()
    estimate_confidence = (
        str(metrics.get("estimate_confidence", "") or "").strip() or "rough"
    )
    observed_total_min = metrics.get("estimate_live_observed_total_min")
    try:
        loaded_estimate_min_f = (
            float(loaded_estimate_min) if loaded_estimate_min is not None else 0.0
        )
    except (TypeError, ValueError):
        loaded_estimate_min_f = 0.0
    estimate_parts: list[str] = []
    if loaded_estimate_min_f > 0.0:
        estimate_parts.append(f"baseline_total_min={loaded_estimate_min_f:.3f}")
    if loaded_estimate_source:
        estimate_parts.append(f"baseline_source={loaded_estimate_source}")
    try:
        observed_total_min_f = (
            float(observed_total_min) if observed_total_min is not None else None
        )
    except (TypeError, ValueError):
        observed_total_min_f = None
    if observed_total_min_f is not None and observed_total_min_f > 0.0:
        estimate_parts.append(f"observed_total_min={observed_total_min_f:.3f}")
    if estimate_parts:
        lines.append("- Estimator runtime totals: " + ", ".join(estimate_parts))
    lines.append(f"- Estimator confidence: {estimate_confidence}")
    rate_source = str(metrics.get("estimate_rate_source", "") or "").strip()
    rapid_rates = metrics.get("estimate_rapid_rates_mm_min")
    accel_rates = metrics.get("estimate_accel_rates_mm_s2")
    rate_parts: list[str] = []
    if rate_source:
        rate_parts.append(f"rapid_source={rate_source}")
    if isinstance(rapid_rates, list) and len(rapid_rates) == 3:
        try:
            rate_parts.append(
                "rapid_mm_min=("
                + ", ".join(f"{float(rapid_rates[idx]):.3f}" for idx in range(3))
                + ")"
            )
        except (TypeError, ValueError):
            pass
    if isinstance(accel_rates, list) and len(accel_rates) == 3:
        try:
            rate_parts.append(
                "accel_mm_s2=("
                + ", ".join(f"{float(accel_rates[idx]):.3f}" for idx in range(3))
                + ")"
            )
        except (TypeError, ValueError):
            pass
    if rate_parts:
        lines.append("- Estimator motion rates: " + ", ".join(rate_parts))
    estimate_inputs = metrics.get("estimate_inputs_snapshot")
    if isinstance(estimate_inputs, dict):
        capture_stage = (
            str(estimate_inputs.get("capture_stage", "") or "").strip() or "n/a"
        )
        capture_rate_source = (
            str(estimate_inputs.get("rate_source", "") or "").strip() or "n/a"
        )
        capture_stats_mode = (
            str(estimate_inputs.get("stats_mode", "") or "").strip() or "n/a"
        )
        capture_sample = int(estimate_inputs.get("stats_sample_line_count", 0) or 0)
        capture_total = int(estimate_inputs.get("stats_sample_total_lines", 0) or 0)
        capture_sample_exec = int(
            estimate_inputs.get("stats_sample_executable_lines", 0) or 0
        )
        capture_sample_motion = int(
            estimate_inputs.get("stats_sample_motion_lines", 0) or 0
        )
        capture_total_exec = int(
            estimate_inputs.get("stats_executable_total_lines", 0) or 0
        )
        capture_total_motion = int(
            estimate_inputs.get("stats_motion_total_lines", 0) or 0
        )
        capture_confidence = (
            str(estimate_inputs.get("estimate_confidence", "") or "").strip()
            or "provisional"
        )
        capture_scale = float(estimate_inputs.get("stats_sample_scale", 1.0) or 1.0)
        lines.append(
            "- Estimator captured inputs: "
            f"stage={capture_stage}, rate_source={capture_rate_source}, "
            f"stats_mode={capture_stats_mode}, sample={capture_sample:,}/{capture_total:,}, "
            f"sample_exec={capture_sample_exec:,}, sample_motion={capture_sample_motion:,}, "
            f"total_exec={capture_total_exec:,}, total_motion={capture_total_motion:,}, "
            f"scale={capture_scale:.2f}, confidence={capture_confidence}"
        )
        modal_ctx = estimate_inputs.get("modal_context")
        if isinstance(modal_ctx, dict):
            lines.append(
                "- Estimator modal context: "
                f"units={str(modal_ctx.get('units', '') or 'n/a')}, "
                f"distance={str(modal_ctx.get('distance_mode', '') or 'n/a')}, "
                f"feed={str(modal_ctx.get('feed_mode', '') or 'n/a')}, "
                f"sampled_lines={int(modal_ctx.get('sampled_lines', 0) or 0):,}"
            )
        motion_settings = estimate_inputs.get("grbl_motion_settings")
        if isinstance(motion_settings, dict):
            parts: list[str] = []
            for key in ("$110", "$111", "$112", "$120", "$121", "$122"):
                raw = motion_settings.get(key)
                if raw is None:
                    parts.append(f"{key}=n/a")
                else:
                    try:
                        parts.append(f"{key}={float(raw):.3f}")
                    except (TypeError, ValueError):
                        parts.append(f"{key}=n/a")
            lines.append("- Estimator GRBL settings: " + ", ".join(parts))
    al_source_path = str(metrics.get("auto_level_source_path", "") or "").strip()
    al_source_exists = bool(metrics.get("auto_level_source_exists", False))
    al_source_hash = str(metrics.get("auto_level_source_hash", "") or "").strip()
    al_source_total_lines = int(metrics.get("auto_level_source_total_lines", 0) or 0)
    source_parts = [
        f"exists={al_source_exists}",
        f"lines={al_source_total_lines:,}",
    ]
    if al_source_hash:
        source_parts.append(f"hash={al_source_hash}")
    if al_source_path:
        source_parts.append(f"path={al_source_path}")
    lines.append("- Auto-level source snapshot: " + ", ".join(source_parts))
    prereq_snapshot = metrics.get("auto_level_prereq_snapshot")
    if isinstance(prereq_snapshot, dict):
        stage = str(prereq_snapshot.get("stage", "") or "n/a")
        ready = bool(prereq_snapshot.get("bounds_ready", False))
        width_mm = float(prereq_snapshot.get("xy_width_mm", 0.0) or 0.0)
        height_mm = float(prereq_snapshot.get("xy_height_mm", 0.0) or 0.0)
        grid_ok = bool(prereq_snapshot.get("probe_grid_applicable", False))
        lines.append(
            "- Auto-level prereq snapshot: "
            f"stage={stage}, bounds_ready={ready}, "
            f"size_mm={width_mm:.3f}x{height_mm:.3f}, "
            f"grid_applicable={grid_ok}"
        )


def _append_storage_snapshot_metrics(
    lines: list[str], metrics: dict[str, Any]
) -> None:
    stream_queue_depth = metrics.get("stream_outstanding_queue_depth")
    stream_pending_item = metrics.get("stream_pending_item")
    stream_resume_depth = metrics.get("stream_resume_preamble_depth")
    stream_buf_used = metrics.get("stream_buf_used_bytes")
    stream_parts: list[str] = []
    if stream_queue_depth is not None:
        stream_parts.append(f"queued_lines={int(stream_queue_depth)}")
    if stream_pending_item is not None:
        stream_parts.append(f"pending_item={bool(stream_pending_item)}")
    if stream_resume_depth is not None:
        stream_parts.append(f"resume_preamble={int(stream_resume_depth)}")
    if stream_buf_used is not None:
        stream_parts.append(f"buf_used={int(stream_buf_used)}B")
    if stream_parts:
        lines.append("- Stream read-ahead footprint: " + ", ".join(stream_parts))
    serial_tail = metrics.get("serial_activity_tail")
    if isinstance(serial_tail, list):
        lines.append(f"- Serial activity tail entries: {len(serial_tail)}")
