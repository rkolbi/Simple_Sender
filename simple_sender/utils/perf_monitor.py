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

logger = logging.getLogger(__name__)


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
        if len(self._samples) >= self._samples.maxlen:
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


def _format_mb(value_bytes: int | None) -> str:
    if value_bytes is None:
        return "n/a"
    return f"{(float(value_bytes) / (1024.0 * 1024.0)):.2f} MB"


@dataclass(slots=True)
class _SnapshotEntry:
    label: str
    ts: float
    snapshot: tracemalloc.Snapshot


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
        self._stream_cpu_stats = _RollingCpuStats(maxlen=self._cpu_sample_maxlen)
        # Backward-compatible attributes used by diagnostics/tests.
        self._idle_cpu_samples = self._idle_cpu_stats.samples
        self._stream_cpu_samples = self._stream_cpu_stats.samples
        self._cpu_prev_wall = time.perf_counter()
        self._cpu_prev_proc = time.process_time()
        self._stream_seen = False
        self._leak_watch = bool(leak_watch)
        self._idle_snapshot_taken = False
        self._snapshots: deque[_SnapshotEntry] = deque(maxlen=self._leak_snapshot_max)
        if self._leak_watch:
            try:
                tracemalloc.start(10)
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
            print(report)
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
            return connected and stream_state not in {"running", "paused"}
        except Exception:
            return False

    def _connected_and_streaming(self) -> bool:
        try:
            connected = bool(getattr(self._app, "connected", False))
            stream_state = str(getattr(self._app, "_stream_state", "") or "").strip().lower()
            return connected and stream_state in {"running", "paused"}
        except Exception:
            return False

    def _sample_loop(self) -> None:
        while not self._stop_evt.wait(self._sample_interval_s):
            self._sample_once()

    def _sample_once(self) -> None:
        now_wall = time.perf_counter()
        now_proc = time.process_time()
        delta_wall = max(1e-9, now_wall - self._cpu_prev_wall)
        delta_proc = max(0.0, now_proc - self._cpu_prev_proc)
        cpu_pct = (delta_proc / delta_wall) * 100.0
        self._cpu_prev_wall = now_wall
        self._cpu_prev_proc = now_proc

        rss = _rss_bytes()
        with self._lock:
            if rss is not None:
                self._rss_current = rss
                if rss > self._rss_peak:
                    self._rss_peak = rss
            if self._connected_and_idle():
                self._idle_cpu_stats.add(cpu_pct)
            if self._connected_and_streaming():
                self._stream_cpu_stats.add(cpu_pct)

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

    def _build_budget_lines(self, idle_cpu_avg: float | None, stream_cpu_p95: float | None) -> list[str]:
        lines: list[str] = []
        idle_target = 3.0
        if idle_cpu_avg is None:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: n/a (no idle samples)")
        elif idle_cpu_avg <= idle_target:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: PASS ({idle_cpu_avg:.2f}%)")
        else:
            lines.append(f"- Idle CPU <= {idle_target:.1f}%: FAIL ({idle_cpu_avg:.2f}%)")

        max_drain_ms = float(getattr(self._app, "_ui_queue_drain_max_ms", 0.0) or 0.0)
        drain_stalls = int(getattr(self._app, "_ui_queue_drain_stall_count", 0) or 0)
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

        if self._rss_start is not None and self._rss_steady_state is not None:
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
        uptime_s = max(0.0, time.perf_counter() - self._created_at)
        idle_cpu_avg, idle_cpu_p95 = self._idle_cpu_stats.summary()
        stream_cpu_avg, stream_cpu_p95 = self._stream_cpu_stats.summary()
        lines: list[str] = []
        lines.append("=== Simple Sender Performance Report ===")
        lines.append(f"Uptime: {uptime_s:.2f}s")
        lines.append(
            "Startup time: "
            + (f"{self._startup_time_s:.3f}s" if self._startup_time_s is not None else "n/a")
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
        if stream_cpu_avg is None or stream_cpu_p95 is None:
            lines.append("Streaming CPU avg/p95: n/a")
        else:
            lines.append(f"Streaming CPU avg/p95: {stream_cpu_avg:.2f}% / {stream_cpu_p95:.2f}%")
        lines.append(f"RSS start: {_format_mb(self._rss_start)}")
        lines.append(f"RSS current: {_format_mb(self._rss_current)}")
        lines.append(f"RSS peak: {_format_mb(self._rss_peak)}")
        lines.append(f"RSS steady-state ({self._steady_state_after_s:.0f}s): {_format_mb(self._rss_steady_state)}")
        lines.append(
            "UI queue drain: "
            f"ticks={int(getattr(self._app, '_ui_queue_drain_ticks', 0) or 0)}, "
            f"events={int(getattr(self._app, '_ui_queue_drain_events', 0) or 0)}, "
            f"max_ms={float(getattr(self._app, '_ui_queue_drain_max_ms', 0.0) or 0.0):.2f}, "
            f"stalls={int(getattr(self._app, '_ui_queue_drain_stall_count', 0) or 0)}"
        )
        lines.append("Budgets:")
        lines.extend(self._build_budget_lines(idle_cpu_avg, stream_cpu_p95))
        leak_lines = self._format_leak_watch()
        if leak_lines:
            lines.append("")
            lines.extend(leak_lines)
        return "\n".join(lines)

    def runtime_snapshot(self) -> dict[str, Any]:
        """Return a best-effort live snapshot for diagnostics windows/exports."""
        with self._lock:
            idle_cpu_avg, idle_cpu_p95 = self._idle_cpu_stats.summary()
            stream_cpu_avg, stream_cpu_p95 = self._stream_cpu_stats.summary()
            snapshot = {
                "available": True,
                "uptime_s": max(0.0, time.perf_counter() - self._created_at),
                "startup_time_s": self._startup_time_s,
                "idle_cpu_avg": idle_cpu_avg,
                "idle_cpu_p95": idle_cpu_p95,
                "stream_cpu_avg": stream_cpu_avg,
                "stream_cpu_p95": stream_cpu_p95,
                "rss_start_bytes": self._rss_start,
                "rss_current_bytes": self._rss_current,
                "rss_peak_bytes": self._rss_peak,
                "rss_steady_state_bytes": self._rss_steady_state,
                "steady_state_after_s": self._steady_state_after_s,
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
        except Exception:
            pass
        return snapshot


def create_app_performance_monitor(
    app: Any,
    *,
    startup_started_at: float | None = None,
) -> AppPerformanceMonitor | None:
    settings = getattr(app, "settings", None)
    enabled = _env_flag("SIMPLE_SENDER_PERF_PROFILE") or _safe_bool_setting(
        settings,
        "performance_profile_enabled",
        default=False,
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
