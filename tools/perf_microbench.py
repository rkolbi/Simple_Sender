#!/usr/bin/env python3
"""Micro-benchmarks for recent Pi-focused performance optimizations."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from simple_sender.grbl_worker_streaming import _stream_patterns
from simple_sender.streaming_controller import StreamingController
from simple_sender.utils.perf_monitor import AppPerformanceMonitor


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000.0:.2f} ms"


def _fmt_us(seconds: float) -> str:
    return f"{seconds * 1_000_000.0:.3f} us"


def bench_gcode_viewer_scheduler(
    *,
    total_lines: int = 120_000,
    chunk_size: int = 500,
    max_chunks_per_tick: int = 8,
    progress_interval_s: float = 0.08,
) -> None:
    def _format_chunk(start_idx: int, end_idx: int) -> int:
        base = start_idx + 1
        lines_out = [f"{base + i:5d}  G1 X{base + i}" for i in range(end_idx - start_idx)]
        return len("\n".join(lines_out)) + 1

    # Legacy: exactly one chunk per callback/tick and one progress callback per chunk.
    old_callbacks = 0
    old_progress_calls = 0
    old_sink = 0
    old_idx = 0
    old_start = time.perf_counter()
    while old_idx < total_lines:
        old_callbacks += 1
        end = min(old_idx + chunk_size, total_lines)
        old_sink += _format_chunk(old_idx, end)
        old_idx = end
        old_progress_calls += 1
    old_elapsed = time.perf_counter() - old_start

    # Optimized: multiple chunks per callback with throttled progress callbacks.
    new_callbacks = 0
    new_progress_calls = 0
    new_sink = 0
    new_idx = 0
    last_progress_ts = 0.0
    new_start = time.perf_counter()
    while new_idx < total_lines:
        new_callbacks += 1
        chunks_this_tick = 0
        tick_start = time.perf_counter()
        while new_idx < total_lines and chunks_this_tick < max_chunks_per_tick:
            end = min(new_idx + chunk_size, total_lines)
            new_sink += _format_chunk(new_idx, end)
            new_idx = end
            chunks_this_tick += 1
            now = time.perf_counter()
            if (now - last_progress_ts) >= progress_interval_s:
                new_progress_calls += 1
                last_progress_ts = now
            if (now - tick_start) >= 0.008:
                break
    new_progress_calls += 1  # Final force-progress emit.
    new_elapsed = time.perf_counter() - new_start

    assert old_sink == new_sink
    print("G-code viewer chunk scheduler:")
    print(f"  before callbacks: {old_callbacks}")
    print(f"  after callbacks:  {new_callbacks}")
    print(f"  callback reduction: {((old_callbacks - new_callbacks) / old_callbacks) * 100.0:.1f}%")
    print(f"  before progress callbacks: {old_progress_calls}")
    print(f"  after progress callbacks:  {new_progress_calls}")
    print(
        f"  progress callback reduction: "
        f"{((old_progress_calls - new_progress_calls) / old_progress_calls) * 100.0:.1f}%"
    )
    print(f"  before runtime: {_fmt_ms(old_elapsed)}")
    print(f"  after runtime:  {_fmt_ms(new_elapsed)}")


@dataclass
class _FakeConsole:
    insert_calls: int = 0
    bytes_written: int = 0

    def insert(self, _where: str, text: str, *_tags: tuple[str, ...]) -> None:
        self.insert_calls += 1
        self.bytes_written += len(text)


@dataclass
class _FakeVar:
    value: bool = True

    def get(self) -> bool:
        return self.value


@dataclass
class _FakeApp:
    performance_mode: _FakeVar
    console_positions_enabled: _FakeVar
    _stream_state: str = "idle"
    _ui_throttle_ms: int = 50

    def after(self, _delay: int, cb):
        cb()
        return None


def bench_console_insert_batching(*, entries_count: int = 20_000) -> None:
    tags = ("console_tx", "console_tx", "console_status", "console_status", None, None)
    entries = [(f"line {idx}", tags[idx % len(tags)]) for idx in range(entries_count)]

    old_console = _FakeConsole()
    old_start = time.perf_counter()
    for line, tag in entries:
        if tag:
            old_console.insert("end", line + "\n", (tag,))
        else:
            old_console.insert("end", line + "\n")
    old_elapsed = time.perf_counter() - old_start

    fake_app = _FakeApp(performance_mode=_FakeVar(True), console_positions_enabled=_FakeVar(True))
    controller = StreamingController(fake_app)
    new_console = _FakeConsole()
    controller.console = new_console
    new_start = time.perf_counter()
    controller._insert_entries_unlocked(entries)
    new_elapsed = time.perf_counter() - new_start

    print("Console insert batching:")
    print(f"  before insert calls: {old_console.insert_calls}")
    print(f"  after insert calls:  {new_console.insert_calls}")
    print(
        f"  insert-call reduction: "
        f"{((old_console.insert_calls - new_console.insert_calls) / old_console.insert_calls) * 100.0:.1f}%"
    )
    print(f"  before runtime: {_fmt_ms(old_elapsed)}")
    print(f"  after runtime:  {_fmt_ms(new_elapsed)}")


def bench_stream_pattern_cache(*, iterations: int = 600_000) -> None:
    def _legacy_stream_patterns():
        from simple_sender import grbl_worker as grbl_worker_mod

        return (
            grbl_worker_mod._PAUSE_MCODE_MAP,
            grbl_worker_mod._PAUSE_MCODE_PAT,
            grbl_worker_mod._SANITIZE_TOKEN_PAT,
            grbl_worker_mod._DRY_RUN_M_CODES,
        )

    _stream_patterns.cache_clear()
    _stream_patterns()  # Warmup
    _legacy_stream_patterns()

    legacy_start = time.perf_counter()
    for _ in range(iterations):
        _legacy_stream_patterns()
    legacy_elapsed = time.perf_counter() - legacy_start

    cached_start = time.perf_counter()
    for _ in range(iterations):
        _stream_patterns()
    cached_elapsed = time.perf_counter() - cached_start

    print("Streaming pattern lookup cache:")
    print(f"  before avg call: {_fmt_us(legacy_elapsed / iterations)}")
    print(f"  after avg call:  {_fmt_us(cached_elapsed / iterations)}")
    print(
        f"  speedup: {legacy_elapsed / cached_elapsed:.2f}x "
        f"({((legacy_elapsed - cached_elapsed) / legacy_elapsed) * 100.0:.1f}% faster)"
    )


def bench_manual_wait_loop(*, duration_s: float = 2.0) -> None:
    evt = threading.Event()

    def _legacy_wait() -> tuple[int, float]:
        loops = 0
        cpu_start = time.process_time()
        end = time.perf_counter() + duration_s
        while time.perf_counter() < end:
            loops += 1
            time.sleep(0.01)
        return loops, time.process_time() - cpu_start

    def _optimized_wait() -> tuple[int, float]:
        loops = 0
        cpu_start = time.process_time()
        end = time.perf_counter() + duration_s
        while time.perf_counter() < end:
            loops += 1
            evt.wait(0.05)
            evt.clear()
        return loops, time.process_time() - cpu_start

    old_loops, old_cpu = _legacy_wait()
    new_loops, new_cpu = _optimized_wait()
    print("Manual completion wait loop:")
    print(f"  before wakeups in {duration_s:.1f}s: {old_loops}")
    print(f"  after wakeups in {duration_s:.1f}s:  {new_loops}")
    print(f"  before process CPU: {_fmt_ms(old_cpu)}")
    print(f"  after process CPU:  {_fmt_ms(new_cpu)}")


@dataclass
class _PerfApp:
    connected: bool = True
    _stream_state: str = "idle"
    _ui_queue_drain_ticks: int = 0
    _ui_queue_drain_events: int = 0
    _ui_queue_drain_max_ms: float = 3.0
    _ui_queue_drain_stall_count: int = 0
    _ui_queue_drain_stall_budget_ms: float = 16.0


def demo_perf_monitor() -> None:
    app = _PerfApp()
    monitor = AppPerformanceMonitor(
        app,
        startup_started_at=time.perf_counter() - 0.45,
        sample_interval_s=0.25,
        steady_state_after_s=3.0,
        idle_snapshot_after_s=2.0,
        leak_watch=False,
    )
    monitor.mark_app_ready()

    # Idle sampling window.
    app._stream_state = "idle"
    time.sleep(1.2)

    # Simulated streaming workload window.
    app._stream_state = "running"
    end = time.perf_counter() + 1.5
    spin = 0
    while time.perf_counter() < end:
        spin += 1
        if (spin % 50000) == 0:
            time.sleep(0.001)

    # Back to idle to settle and hit steady-state RSS marker.
    app._stream_state = "idle"
    time.sleep(8.5)
    report = monitor.emit_exit_report()
    print("Perf monitor sample report:")
    print(report)


def main() -> None:
    print("=== Pi Optimization Microbench ===")
    bench_gcode_viewer_scheduler()
    print("")
    bench_console_insert_batching()
    print("")
    bench_stream_pattern_cache()
    print("")
    bench_manual_wait_loop()
    print("")
    demo_perf_monitor()


if __name__ == "__main__":
    main()
