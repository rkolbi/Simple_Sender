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

"""Lightweight runtime performance profiling hooks for low-power deployments."""

from __future__ import annotations

import os
import time
import threading
import tracemalloc
import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Sequence

from simple_sender.utils.task_timing import snapshot_task_timings

logger = logging.getLogger(__name__)

_HIDDEN_IDLE_TASK_SOURCE_MAP: dict[str, str] = {
    "app_settings.lazy_build_slice": "app_settings_refresh",
    "app_settings.sticky_header": "app_settings_refresh",
    "diagnostics.runtime_telemetry_refresh": "diagnostics_refresh",
    "joystick.discovery": "joystick_discovery",
    "joystick.live_status": "joystick_live_status",
    "joystick.poll": "joystick_poll",
    "tooltip.notebook_poll": "tooltip_poll",
}


def _env_flag(name: str) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value <= 0:
        return float(default)
    return float(value)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(default)
    if value <= 0:
        return int(default)
    return int(value)


def _safe_bool_setting(settings: dict[str, Any] | None, key: str, default: bool = False) -> bool:
    if not isinstance(settings, dict):
        return bool(default)
    try:
        return bool(settings.get(key, default))
    except Exception:
        return bool(default)


def _safe_str_setting(settings: dict[str, Any] | None, key: str) -> str:
    if not isinstance(settings, dict):
        return ""
    try:
        return str(settings.get(key, "") or "").strip()
    except Exception:
        return ""


def _rss_bytes() -> int | None:
    # Linux /proc fast-path.
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except Exception:
        pass

    # Windows fallback.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("Psapi.dll")
            kernel32 = ctypes.WinDLL("Kernel32.dll")
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            get_process_memory_info.restype = wintypes.BOOL

            process = kernel32.GetCurrentProcess()
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if bool(get_process_memory_info(process, ctypes.byref(counters), counters.cb)):
                return int(counters.WorkingSetSize)
        except Exception:
            pass
    return None


def _sample_stats(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(float(v) for v in values)
    avg = sum(ordered) / float(len(ordered))
    idx = int(round((len(ordered) - 1) * 0.95))
    idx = max(0, min(len(ordered) - 1, idx))
    p95 = ordered[idx]
    return avg, p95


class _RollingCpuStats:
    """Bounded rolling CPU stats with O(1) update and histogram p95."""

    def __init__(
        self,
        *,
        maxlen: int,
        bin_width: float = 0.25,
        max_value: float = 200.0,
    ) -> None:
        self._samples: deque[float] = deque(maxlen=maxlen)
        self._sum = 0.0
        self._bin_width = max(0.05, float(bin_width))
        self._max_value = max(self._bin_width, float(max_value))
        self._overflow_idx = int(math.ceil(self._max_value / self._bin_width))
        self._bins: list[int] = [0] * (self._overflow_idx + 1)

    @property
    def samples(self) -> deque[float]:
        return self._samples

    def add(self, value: float) -> None:
        try:
            val = max(0.0, float(value))
        except (TypeError, ValueError):
            return
        maxlen = self._samples.maxlen
        if maxlen is not None and len(self._samples) >= maxlen:
            old = self._samples.popleft()
            self._sum -= old
            self._update_bin(old, delta=-1)
        self._samples.append(val)
        self._sum += val
        self._update_bin(val, delta=1)

    def summary(self) -> tuple[float | None, float | None]:
        n = len(self._samples)
        if n <= 0:
            return None, None
        avg = self._sum / float(n)
        target_rank = int(round((n - 1) * 0.95)) + 1
        cumulative = 0
        p95 = 0.0
        for idx, count in enumerate(self._bins):
            if count <= 0:
                continue
            cumulative += count
            if cumulative >= target_rank:
                p95 = self._bin_value(idx)
                break
        return avg, p95

    def _update_bin(self, value: float, *, delta: int) -> None:
        idx = self._bin_index(value)
        self._bins[idx] = max(0, int(self._bins[idx]) + int(delta))

    def _bin_index(self, value: float) -> int:
        if value >= self._max_value:
            return self._overflow_idx
        idx = int(value / self._bin_width)
        if idx < 0:
            return 0
        if idx > self._overflow_idx:
            return self._overflow_idx
        return idx

    def _bin_value(self, idx: int) -> float:
        if idx >= self._overflow_idx:
            return float(self._max_value)
        lower = float(idx) * self._bin_width
        upper = lower + self._bin_width
        return (lower + upper) / 2.0


def _format_mb(value_bytes: float | int | None) -> str:
    if value_bytes is None:
        return "n/a"
    return f"{(float(value_bytes) / (1024.0 * 1024.0)):.2f} MB"


@dataclass(slots=True)
class _SnapshotEntry:
    label: str
    ts: float
    snapshot: tracemalloc.Snapshot


class _PhaseSampleStats:
    """Per-phase CPU and RSS summaries with bounded CPU history."""

    def __init__(self, *, maxlen: int) -> None:
        self._cpu = _RollingCpuStats(maxlen=maxlen)
        self._rss_start: int | None = None
        self._rss_current: int | None = None
        self._rss_peak: int | None = None

    def add(self, cpu_pct: float, rss_bytes: int | None) -> None:
        self._cpu.add(cpu_pct)
        if rss_bytes is None:
            return
        rss = int(rss_bytes)
        if self._rss_start is None:
            self._rss_start = rss
            self._rss_peak = rss
        self._rss_current = rss
        if self._rss_peak is None or rss > self._rss_peak:
            self._rss_peak = rss

    def snapshot(self) -> dict[str, float | int | None]:
        cpu_avg, cpu_p95 = self._cpu.summary()
        return {
            "samples": len(self._cpu.samples),
            "cpu_avg": cpu_avg,
            "cpu_p95": cpu_p95,
            "rss_start_bytes": self._rss_start,
            "rss_current_bytes": self._rss_current,
            "rss_peak_bytes": self._rss_peak,
        }


class AppPerformanceMonitor:
    """Low-overhead sampler for process CPU/RSS and optional tracemalloc milestones."""

    def __init__(
        self,
        app: Any,
        *,
        startup_started_at: float | None = None,
        log_path: str = "",
        leak_watch: bool = False,
        sample_interval_s: float = 1.0,
        cpu_sample_window_s: float = 3600.0,
        steady_state_after_s: float = 1800.0,
        idle_snapshot_after_s: float = 600.0,
        leak_snapshot_max: int = 16,
    ) -> None:
        self._app = app
        self._startup_started_at = float(startup_started_at or time.perf_counter())
        self._created_at = time.perf_counter()
        self._app_ready_at: float | None = None
        self._startup_time_s: float | None = None
        self._log_path = str(log_path or "").strip()
        self._sample_interval_s = max(0.2, float(sample_interval_s))
        self._cpu_sample_window_s = max(60.0, float(cpu_sample_window_s))
        self._cpu_sample_maxlen = max(
            60,
            int(round(self._cpu_sample_window_s / self._sample_interval_s)),
        )
        self._steady_state_after_s = max(10.0, float(steady_state_after_s))
        self._idle_snapshot_after_s = max(10.0, float(idle_snapshot_after_s))
        self._leak_snapshot_max = max(2, int(leak_snapshot_max))
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="perf-sampler",
            daemon=True,
        )
        self._lock = threading.Lock()
        self._reported = False
        self._rss_start = _rss_bytes()
        self._rss_current = self._rss_start
        self._rss_peak = self._rss_start or 0
        self._rss_steady_state: int | None = None
        self._idle_cpu_stats = _RollingCpuStats(maxlen=self._cpu_sample_maxlen)
        self._quiet_idle_cpu_stats = _RollingCpuStats(maxlen=self._cpu_sample_maxlen)
        self._stream_cpu_stats = _RollingCpuStats(maxlen=self._cpu_sample_maxlen)
        self._phase_stats: dict[str, _PhaseSampleStats] = {
            "idle_connected": _PhaseSampleStats(maxlen=self._cpu_sample_maxlen),
            "streaming": _PhaseSampleStats(maxlen=self._cpu_sample_maxlen),
        }
        self._sample_trace: deque[dict[str, float | int | str | None]] = deque(
            maxlen=self._cpu_sample_maxlen
        )
        self._hidden_idle_contributors: dict[str, dict[str, float | int]] = {}
        self._hidden_idle_task_prev: dict[str, tuple[int, float]] = {}
        self._seed_hidden_idle_task_prev()
        self._cpu_prev_wall = time.perf_counter()
        self._cpu_prev_proc = time.process_time()
        self._stream_seen = False
        self._leak_watch = bool(leak_watch)
        self._leak_trace_frames = max(1, _env_int("SIMPLE_SENDER_LEAK_TRACE_FRAMES", 1))
        self._idle_snapshot_taken = False
        self._snapshots: deque[_SnapshotEntry] = deque(maxlen=self._leak_snapshot_max)
        if self._leak_watch:
            try:
                tracemalloc.start(self._leak_trace_frames)
            except Exception as exc:
                logger.debug("Failed enabling tracemalloc leak watch: %s", exc, exc_info=exc)
                self._leak_watch = False
        self._thread.start()

    def mark_app_ready(self) -> None:
        if self._app_ready_at is not None:
            return
        now = time.perf_counter()
        self._app_ready_at = now
        self._startup_time_s = max(0.0, now - self._startup_started_at)
        self._take_snapshot("startup")

    def note_file_loaded(self) -> None:
        self._take_snapshot("after_file_load")

    def note_stream_state(self, state: str | None) -> None:
        normalized = str(state or "").strip().lower()
        if normalized == "running":
            self._stream_seen = True
            return
        if normalized in {"done", "stopped", "error", "alarm"} and self._stream_seen:
            self._stream_seen = False
            self._take_snapshot("after_stream")

    def emit_exit_report(self) -> str:
        with self._lock:
            if self._reported:
                return ""
            self._reported = True
        self._stop_evt.set()
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass
        self._sample_once()
        report = self._build_report()
        if report:
            logger.info("\n%s", report)
            if self._log_path:
                try:
                    with open(self._log_path, "a", encoding="utf-8", newline="\n") as handle:
                        handle.write(report)
                        handle.write("\n")
                except Exception as exc:
                    logger.debug("Failed writing performance report to %s: %s", self._log_path, exc, exc_info=exc)
        if self._leak_watch:
            try:
                tracemalloc.stop()
            except Exception:
                pass
        return report

    def build_report_snapshot(self) -> str:
        """Build a point-in-time report without stopping the monitor."""
        self._sample_once()
        return self._build_report()

    def _take_snapshot(self, label: str) -> None:
        if not self._leak_watch:
            return
        try:
            snapshot = tracemalloc.take_snapshot()
        except Exception as exc:
            logger.debug("Failed taking tracemalloc snapshot (%s): %s", label, exc, exc_info=exc)
            return
        self._snapshots.append(_SnapshotEntry(label=label, ts=time.perf_counter(), snapshot=snapshot))

    def _connected_and_idle(self) -> bool:
        try:
            connected = bool(getattr(self._app, "connected", False))
            stream_state = str(getattr(self._app, "_stream_state", "") or "").strip().lower()
            if not connected or stream_state in {"running", "paused"}:
                return False
            if bool(getattr(self._app, "_gcode_loading", False)):
                return False
            if bool(getattr(self._app, "_stream_done_pending_idle", False)):
                return False
            if bool(getattr(self._app, "_homing_in_progress", False)):
                return False
            if bool(getattr(self._app, "_gcode_parsing_active", False)):
                return False
            if getattr(self._app, "_stats_pending_request", None):
                return False
            machine_state = str(getattr(self._app, "_machine_state_text", "") or "").strip().lower()
            if machine_state and (not machine_state.startswith("idle")):
                return False
            return True
        except Exception:
            return False

    def _connected_and_quiet_idle(self) -> bool:
        try:
            if not self._connected_and_idle():
                return False
            if not bool(getattr(self._app, "_grbl_ready", False)):
                return False
            if bool(getattr(self._app, "_app_settings_tab_active", False)):
                return False
            if bool(getattr(self._app, "_status_seen", False)) is False:
                return False
            status_last_non_idle = float(getattr(self._app, "_status_last_non_idle_ts", 0.0) or 0.0)
            if status_last_non_idle <= 0.0:
                return False
            quiet_elapsed_s = max(0.0, time.monotonic() - status_last_non_idle)
            if quiet_elapsed_s < 20.0:
                return False
            ui_q = getattr(self._app, "ui_q", None)
            if ui_q is not None and hasattr(ui_q, "qsize"):
                try:
                    if int(ui_q.qsize()) > 0:
                        return False
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _connected_and_streaming(self) -> bool:
        try:
            connected = bool(getattr(self._app, "connected", False))
            stream_state = str(getattr(self._app, "_stream_state", "") or "").strip().lower()
            return connected and stream_state in {"running", "paused"}
        except Exception:
            return False

    def _phase_metrics_snapshot(self) -> dict[str, dict[str, float | int | None]]:
        phase_metrics: dict[str, dict[str, float | int | None]] = {}
        for phase_name, stats in self._phase_stats.items():
            phase_metrics[phase_name] = stats.snapshot()
        return phase_metrics

    def _seed_hidden_idle_task_prev(self) -> None:
        task_metrics = getattr(self._app, "_task_timing_metrics", None)
        if not isinstance(task_metrics, dict):
            return
        for task_name in _HIDDEN_IDLE_TASK_SOURCE_MAP.keys():
            raw_entry = task_metrics.get(task_name)
            if not isinstance(raw_entry, dict):
                continue
            try:
                count = max(0, int(raw_entry.get("count", 0) or 0))
                total_ms = max(0.0, float(raw_entry.get("total_ms", 0.0) or 0.0))
            except Exception:
                continue
            self._hidden_idle_task_prev[str(task_name)] = (count, total_ms)

    def _record_hidden_idle_contributor(
        self,
        source: str,
        *,
        active_sample: bool = False,
        cpu_ms: float = 0.0,
        task_count: int = 0,
        task_ms: float = 0.0,
    ) -> None:
        name = str(source or "").strip().lower()
        if not name:
            return
        raw_entry = self._hidden_idle_contributors.get(name)
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        if active_sample:
            entry["active_samples"] = int(entry.get("active_samples", 0) or 0) + 1
        if cpu_ms > 0.0:
            entry["active_cpu_ms"] = max(
                0.0, float(entry.get("active_cpu_ms", 0.0) or 0.0)
            ) + float(cpu_ms)
        if task_count > 0:
            entry["task_count"] = int(entry.get("task_count", 0) or 0) + int(task_count)
        if task_ms > 0.0:
            entry["task_ms"] = max(0.0, float(entry.get("task_ms", 0.0) or 0.0)) + float(
                task_ms
            )
        self._hidden_idle_contributors[name] = entry

    def _collect_hidden_idle_contributors(self, *, sample_cpu_ms: float) -> None:
        app = self._app
        app_settings_active = bool(getattr(app, "_app_settings_tab_active", False))
        if app_settings_active:
            self._record_hidden_idle_contributor(
                "app_settings_tab",
                active_sample=True,
                cpu_ms=sample_cpu_ms,
            )
        runtime_telemetry_win = getattr(app, "_runtime_telemetry_window", None)
        if runtime_telemetry_win is not None:
            self._record_hidden_idle_contributor(
                "diagnostics_window",
                active_sample=True,
                cpu_ms=sample_cpu_ms,
            )
        joystick_poll_id = getattr(app, "_joystick_poll_id", None)
        if joystick_poll_id is not None:
            self._record_hidden_idle_contributor(
                "joystick_timer",
                active_sample=True,
                cpu_ms=sample_cpu_ms,
            )
        try:
            notebook = getattr(app, "notebook", None)
            tooltip_handler = (
                getattr(notebook, "_tab_tooltip_handler", None) if notebook is not None else None
            )
            if tooltip_handler is not None and getattr(tooltip_handler, "_poll_after_id", None) is not None:
                self._record_hidden_idle_contributor(
                    "tooltip_timer",
                    active_sample=True,
                    cpu_ms=sample_cpu_ms,
                )
        except Exception:
            pass

        task_metrics = getattr(app, "_task_timing_metrics", None)
        if not isinstance(task_metrics, dict):
            return
        for task_name, source_name in _HIDDEN_IDLE_TASK_SOURCE_MAP.items():
            raw_entry = task_metrics.get(task_name)
            if not isinstance(raw_entry, dict):
                continue
            try:
                count = max(0, int(raw_entry.get("count", 0) or 0))
                total_ms = max(0.0, float(raw_entry.get("total_ms", 0.0) or 0.0))
            except Exception:
                continue
            prev_count, prev_total = self._hidden_idle_task_prev.get(task_name, (0, 0.0))
            delta_count = max(0, count - int(prev_count))
            delta_ms = max(0.0, total_ms - float(prev_total))
            self._hidden_idle_task_prev[task_name] = (count, total_ms)
            if delta_count <= 0 and delta_ms <= 0.0:
                continue
            self._record_hidden_idle_contributor(
                source_name,
                task_count=delta_count,
                task_ms=delta_ms,
            )

    def _hidden_idle_contributors_snapshot(self, *, limit: int = 5) -> list[dict[str, float | int | str]]:
        rows: list[dict[str, float | int | str]] = []
        for source, raw_entry in self._hidden_idle_contributors.items():
            if not isinstance(raw_entry, dict):
                continue
            rows.append(
                {
                    "source": str(source),
                    "active_samples": int(raw_entry.get("active_samples", 0) or 0),
                    "active_cpu_ms": max(0.0, float(raw_entry.get("active_cpu_ms", 0.0) or 0.0)),
                    "task_count": int(raw_entry.get("task_count", 0) or 0),
                    "task_ms": max(0.0, float(raw_entry.get("task_ms", 0.0) or 0.0)),
                }
            )
        rows.sort(
            key=lambda item: (
                float(item.get("task_ms", 0.0) or 0.0),
                float(item.get("active_cpu_ms", 0.0) or 0.0),
                int(item.get("task_count", 0) or 0),
                int(item.get("active_samples", 0) or 0),
            ),
            reverse=True,
        )
        return rows[: max(1, int(limit))]

    def _sample_loop(self) -> None:
        while not self._stop_evt.wait(self._sample_interval_s):
            self._sample_once()

    def _sample_once(self) -> None:
        now_wall = time.perf_counter()
        now_proc = time.process_time()
        delta_wall = max(1e-9, now_wall - self._cpu_prev_wall)
        delta_proc = max(0.0, now_proc - self._cpu_prev_proc)
        cpu_pct = (delta_proc / delta_wall) * 100.0
        sample_cpu_ms = max(0.0, float(delta_proc) * 1000.0)
        self._cpu_prev_wall = now_wall
        self._cpu_prev_proc = now_proc

        rss = _rss_bytes()
        with self._lock:
            if rss is not None:
                self._rss_current = rss
                if rss > self._rss_peak:
                    self._rss_peak = rss
            phase = "other"
            connected_idle = self._connected_and_idle()
            if connected_idle:
                self._idle_cpu_stats.add(cpu_pct)
                self._phase_stats["idle_connected"].add(cpu_pct, rss)
                phase = "idle_connected"
                self._collect_hidden_idle_contributors(sample_cpu_ms=sample_cpu_ms)
            if self._connected_and_quiet_idle():
                self._quiet_idle_cpu_stats.add(cpu_pct)
            if self._connected_and_streaming():
                self._stream_cpu_stats.add(cpu_pct)
                self._phase_stats["streaming"].add(cpu_pct, rss)
                phase = "streaming"
            self._sample_trace.append(
                {
                    "ts": float(time.time()),
                    "cpu_pct": float(cpu_pct),
                    "rss_bytes": int(rss) if rss is not None else None,
                    "phase": phase,
                }
            )

            ready_at = self._app_ready_at or self._created_at
            elapsed_since_ready = max(0.0, now_wall - ready_at)
            if self._rss_steady_state is None and rss is not None and elapsed_since_ready >= self._steady_state_after_s:
                self._rss_steady_state = rss
            if (
                self._leak_watch
                and not self._idle_snapshot_taken
                and elapsed_since_ready >= self._idle_snapshot_after_s
                and self._connected_and_idle()
            ):
                self._idle_snapshot_taken = True
                self._take_snapshot("idle_10m")

    def _build_budget_lines(
        self,
        idle_cpu_avg: float | None,
        quiet_idle_cpu_avg: float | None,
        stream_cpu_p95: float | None,
    ) -> list[str]:
        lines: list[str] = []
        idle_target = 3.0
        if idle_cpu_avg is None:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: n/a (no idle samples)")
        elif idle_cpu_avg <= idle_target:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: PASS ({idle_cpu_avg:.2f}%)")
        else:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: FAIL ({idle_cpu_avg:.2f}%)")
        if quiet_idle_cpu_avg is None:
            lines.append(f"- Quiet idle CPU <= {idle_target:.1f}%: n/a (no quiet-idle samples)")
        elif quiet_idle_cpu_avg <= idle_target:
            lines.append(
                f"- Quiet idle CPU <= {idle_target:.1f}%: PASS ({quiet_idle_cpu_avg:.2f}%)"
            )
        else:
            lines.append(
                f"- Quiet idle CPU <= {idle_target:.1f}%: FAIL ({quiet_idle_cpu_avg:.2f}%)"
            )

        _ticks, _events, max_drain_ms, drain_stalls = self._ui_drain_metrics()
        stall_target_ms = float(getattr(self._app, "_ui_queue_drain_stall_budget_ms", 16.0) or 16.0)
        if max_drain_ms <= stall_target_ms and drain_stalls <= 0:
            lines.append(
                f"- UI stall budget <= {stall_target_ms:.1f} ms: PASS "
                f"(max {max_drain_ms:.2f} ms, stalls {drain_stalls})"
            )
        else:
            lines.append(
                f"- UI stall budget <= {stall_target_ms:.1f} ms: FAIL "
                f"(max {max_drain_ms:.2f} ms, stalls {drain_stalls})"
            )

        if self._leak_watch:
            lines.append("- Steady-state RSS growth <= 64 MB: n/a (leak watch enabled)")
        elif self._rss_start is not None and self._rss_steady_state is not None:
            growth_mb = (self._rss_steady_state - self._rss_start) / (1024.0 * 1024.0)
            if growth_mb <= 64.0:
                lines.append(f"- Steady-state RSS growth <= 64 MB: PASS ({growth_mb:.2f} MB)")
            else:
                lines.append(f"- Steady-state RSS growth <= 64 MB: FAIL ({growth_mb:.2f} MB)")
        else:
            lines.append("- Steady-state RSS growth <= 64 MB: n/a (insufficient runtime)")

        if stream_cpu_p95 is None:
            lines.append("- Streaming CPU samples: n/a")
        else:
            lines.append(f"- Streaming CPU p95: {stream_cpu_p95:.2f}%")
        return lines

    def _format_leak_watch(self) -> list[str]:
        if not self._leak_watch or len(self._snapshots) < 2:
            return []
        snapshots = list(self._snapshots)
        lines = ["Leak Watch (tracemalloc deltas):"]
        for prev, cur in zip(snapshots[:-1], snapshots[1:]):
            lines.append(f"- {prev.label} -> {cur.label}:")
            try:
                stats = cur.snapshot.compare_to(prev.snapshot, "lineno")
            except Exception as exc:
                lines.append(f"  compare failed: {exc}")
                continue
            shown = 0
            for stat in stats:
                size_diff = int(getattr(stat, "size_diff", 0) or 0)
                if size_diff <= 0:
                    continue
                lines.append(
                    f"  {stat.traceback[0]} | +{size_diff / 1024.0:.1f} KiB | +{int(getattr(stat, 'count_diff', 0) or 0)}"
                )
                shown += 1
                if shown >= 10:
                    break
            if shown == 0:
                lines.append("  no positive growth")
        return lines

    def _build_report(self) -> str:
        with self._lock:
            uptime_s = max(0.0, time.perf_counter() - self._created_at)
            idle_cpu_avg, idle_cpu_p95 = self._idle_cpu_stats.summary()
            quiet_idle_cpu_avg, quiet_idle_cpu_p95 = self._quiet_idle_cpu_stats.summary()
            quiet_idle_sample_count = len(self._quiet_idle_cpu_stats.samples)
            stream_cpu_avg, stream_cpu_p95 = self._stream_cpu_stats.summary()
            phase_metrics = self._phase_metrics_snapshot()
            hidden_idle_contributors = self._hidden_idle_contributors_snapshot(limit=8)
            ui_ticks, ui_events, ui_max_ms, ui_stalls = self._ui_drain_metrics()
            rss_start = self._rss_start
            rss_current = self._rss_current
            rss_peak = self._rss_peak
            rss_steady_state = self._rss_steady_state
            startup_time_s = self._startup_time_s
            leak_watch = self._leak_watch
            leak_trace_frames = self._leak_trace_frames
        lines: list[str] = []
        lines.append("=== Simple Sender Performance Report ===")
        lines.append(f"Uptime: {uptime_s:.2f}s")
        lines.append(
            "Startup time: "
            + (f"{startup_time_s:.3f}s" if startup_time_s is not None else "n/a")
        )
        lines.append(
            "CPU sample window: "
            f"last {self._cpu_sample_maxlen} sample(s) "
            f"(~{self._cpu_sample_window_s:.0f}s @ {self._sample_interval_s:.2f}s)"
        )
        if idle_cpu_avg is None or idle_cpu_p95 is None:
            lines.append("Idle CPU avg/p95: n/a")
        else:
            lines.append(f"Idle CPU avg/p95: {idle_cpu_avg:.2f}% / {idle_cpu_p95:.2f}%")
        if quiet_idle_cpu_avg is None or quiet_idle_cpu_p95 is None:
            lines.append("Quiet idle CPU avg/p95: n/a")
        else:
            lines.append(
                "Quiet idle CPU avg/p95: "
                f"{quiet_idle_cpu_avg:.2f}% / {quiet_idle_cpu_p95:.2f}% "
                f"(samples={quiet_idle_sample_count})"
            )
        if stream_cpu_avg is None or stream_cpu_p95 is None:
            lines.append("Streaming CPU avg/p95: n/a")
        else:
            lines.append(f"Streaming CPU avg/p95: {stream_cpu_avg:.2f}% / {stream_cpu_p95:.2f}%")
        if phase_metrics:
            lines.append("Phase CPU/RSS:")
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
                    f"- {label}: cpu avg/p95={cpu_text}, samples={samples}, "
                    f"rss start/current/peak={_format_mb(phase.get('rss_start_bytes'))} / "
                    f"{_format_mb(phase.get('rss_current_bytes'))} / "
                    f"{_format_mb(phase.get('rss_peak_bytes'))}"
                )
        if hidden_idle_contributors:
            lines.append("Hidden-idle contributors (top):")
            for entry in hidden_idle_contributors[:5]:
                lines.append(
                    "- "
                    f"{str(entry.get('source', '') or 'unknown')}: "
                    f"samples={int(entry.get('active_samples', 0) or 0)}, "
                    f"cpu_ms={float(entry.get('active_cpu_ms', 0.0) or 0.0):.2f}, "
                    f"events={int(entry.get('task_count', 0) or 0)}, "
                    f"task_ms={float(entry.get('task_ms', 0.0) or 0.0):.2f}"
                )
        lines.append(f"RSS start: {_format_mb(rss_start)}")
        lines.append(f"RSS current: {_format_mb(rss_current)}")
        lines.append(f"RSS peak: {_format_mb(rss_peak)}")
        lines.append(f"RSS steady-state ({self._steady_state_after_s:.0f}s): {_format_mb(rss_steady_state)}")
        if leak_watch:
            lines.append(
                f"Leak watch trace frames: {leak_trace_frames} "
                "(RSS/CPU include tracemalloc overhead)"
            )
        lines.append(
            "UI queue drain: "
            f"ticks={ui_ticks}, "
            f"events={ui_events}, "
            f"max_ms={ui_max_ms:.2f}, "
            f"stalls={ui_stalls}"
        )
        slow_event_ms = float(getattr(self._app, "_ui_queue_drain_runtime_slowest_event_ms", 0.0) or 0.0)
        slow_event_kind = str(getattr(self._app, "_ui_queue_drain_runtime_slowest_event_kind", "") or "").strip()
        if slow_event_ms > 0.0:
            lines.append(
                "UI queue slowest runtime event: "
                f"{slow_event_kind or 'unknown'} ({slow_event_ms:.2f} ms)"
            )
        task_timings = snapshot_task_timings(self._app)
        if task_timings:
            lines.append("Background I/O timings:")
            for task_name in sorted(task_timings.keys()):
                timing_entry = task_timings[task_name]
                lines.append(
                    "- "
                    f"{task_name}: "
                    f"count={int(timing_entry.get('count', 0) or 0)}, "
                    f"ok={int(timing_entry.get('ok_count', 0) or 0)}, "
                    f"err={int(timing_entry.get('err_count', 0) or 0)}, "
                    f"avg={float(timing_entry.get('avg_ms', 0.0) or 0.0):.2f} ms, "
                    f"max={float(timing_entry.get('max_ms', 0.0) or 0.0):.2f} ms"
                )
        lines.append("Budgets:")
        lines.extend(self._build_budget_lines(idle_cpu_avg, quiet_idle_cpu_avg, stream_cpu_p95))
        leak_lines = self._format_leak_watch()
        if leak_lines:
            lines.append("")
            lines.extend(leak_lines)
        return "\n".join(lines)

    def runtime_snapshot(self) -> dict[str, Any]:
        """Return a best-effort live snapshot for diagnostics windows/exports."""
        with self._lock:
            idle_cpu_avg, idle_cpu_p95 = self._idle_cpu_stats.summary()
            quiet_idle_cpu_avg, quiet_idle_cpu_p95 = self._quiet_idle_cpu_stats.summary()
            stream_cpu_avg, stream_cpu_p95 = self._stream_cpu_stats.summary()
            phase_metrics = self._phase_metrics_snapshot()
            snapshot: dict[str, Any] = {
                "available": True,
                "uptime_s": max(0.0, time.perf_counter() - self._created_at),
                "startup_time_s": self._startup_time_s,
                "idle_cpu_avg": idle_cpu_avg,
                "idle_cpu_p95": idle_cpu_p95,
                "quiet_idle_cpu_avg": quiet_idle_cpu_avg,
                "quiet_idle_cpu_p95": quiet_idle_cpu_p95,
                "quiet_idle_cpu_samples": len(self._quiet_idle_cpu_stats.samples),
                "stream_cpu_avg": stream_cpu_avg,
                "stream_cpu_p95": stream_cpu_p95,
                "rss_start_bytes": self._rss_start,
                "rss_current_bytes": self._rss_current,
                "rss_peak_bytes": self._rss_peak,
                "rss_steady_state_bytes": self._rss_steady_state,
                "steady_state_after_s": self._steady_state_after_s,
                "phase_metrics": phase_metrics,
                "hidden_idle_contributors": self._hidden_idle_contributors_snapshot(limit=16),
                "sample_trace": list(self._sample_trace),
            }
        try:
            snapshot["ui_queue_drain_ticks"] = int(
                getattr(self._app, "_ui_queue_drain_ticks", 0) or 0
            )
            snapshot["ui_queue_drain_events"] = int(
                getattr(self._app, "_ui_queue_drain_events", 0) or 0
            )
            snapshot["ui_queue_drain_max_ms"] = float(
                getattr(self._app, "_ui_queue_drain_max_ms", 0.0) or 0.0
            )
            snapshot["ui_queue_drain_stall_count"] = int(
                getattr(self._app, "_ui_queue_drain_stall_count", 0) or 0
            )
            snapshot["ui_queue_drain_stall_budget_ms"] = float(
                getattr(self._app, "_ui_queue_drain_stall_budget_ms", 16.0) or 16.0
            )
            rt_ticks, rt_events, rt_max_ms, rt_stalls = self._ui_drain_metrics()
            snapshot["ui_queue_drain_runtime_ticks"] = rt_ticks
            snapshot["ui_queue_drain_runtime_events"] = rt_events
            snapshot["ui_queue_drain_runtime_max_ms"] = rt_max_ms
            snapshot["ui_queue_drain_runtime_stall_count"] = rt_stalls
            snapshot["ui_queue_drain_runtime_slowest_event_ms"] = float(
                getattr(self._app, "_ui_queue_drain_runtime_slowest_event_ms", 0.0) or 0.0
            )
            snapshot["ui_queue_drain_runtime_slowest_event_kind"] = str(
                getattr(self._app, "_ui_queue_drain_runtime_slowest_event_kind", "") or ""
            )
            snapshot["background_task_timings"] = snapshot_task_timings(self._app)
            outliers = getattr(self._app, "_ui_queue_drain_outliers", None)
            outlier_entries: list[dict[str, Any]] = []
            if isinstance(outliers, deque):
                for item in list(outliers):
                    if isinstance(item, dict):
                        outlier_entries.append(dict(item))
            elif isinstance(outliers, list):
                for item in outliers:
                    if isinstance(item, dict):
                        outlier_entries.append(dict(item))
            snapshot["ui_queue_drain_outliers"] = outlier_entries
            snapshot["ui_queue_drain_outlier_total"] = int(
                getattr(self._app, "_ui_queue_drain_outlier_total", 0) or 0
            )
            runtime_stalls = getattr(self._app, "_ui_queue_drain_runtime_stalls", None)
            runtime_stall_entries: list[dict[str, Any]] = []
            if isinstance(runtime_stalls, deque):
                for item in list(runtime_stalls):
                    if isinstance(item, dict):
                        runtime_stall_entries.append(dict(item))
            elif isinstance(runtime_stalls, list):
                for item in runtime_stalls:
                    if isinstance(item, dict):
                        runtime_stall_entries.append(dict(item))
            snapshot["ui_queue_drain_runtime_stalls"] = runtime_stall_entries
            snapshot["ui_queue_drain_runtime_stall_total"] = int(
                getattr(self._app, "_ui_queue_drain_runtime_stall_total", 0) or 0
            )
        except Exception:
            pass
        return snapshot

    def _ui_drain_metrics(self) -> tuple[int, int, float, int]:
        runtime_ticks = int(getattr(self._app, "_ui_queue_drain_runtime_ticks", 0) or 0)
        if runtime_ticks > 0:
            return (
                runtime_ticks,
                int(getattr(self._app, "_ui_queue_drain_runtime_events", 0) or 0),
                float(getattr(self._app, "_ui_queue_drain_runtime_max_ms", 0.0) or 0.0),
                int(getattr(self._app, "_ui_queue_drain_runtime_stall_count", 0) or 0),
            )
        return (
            int(getattr(self._app, "_ui_queue_drain_ticks", 0) or 0),
            int(getattr(self._app, "_ui_queue_drain_events", 0) or 0),
            float(getattr(self._app, "_ui_queue_drain_max_ms", 0.0) or 0.0),
            int(getattr(self._app, "_ui_queue_drain_stall_count", 0) or 0),
        )


def create_app_performance_monitor(
    app: Any,
    *,
    startup_started_at: float | None = None,
) -> AppPerformanceMonitor | None:
    settings = getattr(app, "settings", None)
    enabled = _env_flag("SIMPLE_SENDER_PERF_PROFILE") or _safe_bool_setting(
        settings,
        "performance_profile_enabled",
        default=True,
    )
    if not enabled:
        return None
    leak_watch = _env_flag("SIMPLE_SENDER_LEAK_WATCH") or _safe_bool_setting(
        settings,
        "performance_leak_watch_enabled",
        default=False,
    )
    log_path = str(os.getenv("SIMPLE_SENDER_PERF_LOG_PATH", "") or "").strip()
    if not log_path:
        log_path = _safe_str_setting(settings, "performance_profile_log_path")
    monitor = AppPerformanceMonitor(
        app,
        startup_started_at=startup_started_at,
        log_path=log_path,
        leak_watch=leak_watch,
        sample_interval_s=_env_float("SIMPLE_SENDER_PERF_SAMPLE_INTERVAL_S", 1.0),
        cpu_sample_window_s=_env_float("SIMPLE_SENDER_PERF_CPU_WINDOW_SEC", 3600.0),
        steady_state_after_s=_env_float("SIMPLE_SENDER_PERF_STEADY_STATE_SEC", 1800.0),
        idle_snapshot_after_s=_env_float("SIMPLE_SENDER_PERF_IDLE_SNAPSHOT_SEC", 600.0),
        leak_snapshot_max=_env_int("SIMPLE_SENDER_LEAK_SNAPSHOT_MAX", 16),
    )
    return monitor
