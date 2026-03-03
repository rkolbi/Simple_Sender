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

from __future__ import annotations

import logging
import re
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.utils.task_timing import record_task_timing
from simple_sender.utils.logging_config import get_log_dir

LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
LEVEL_PATTERN = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")

LOG_SOURCES = {
    "Application": ("simple_sender.log",),
    "Serial": ("serial.log",),
    "UI": ("ui.log",),
    "Errors": ("errors.log",),
    "All": ("simple_sender.log", "serial.log", "ui.log", "errors.log"),
}

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _resolve_log_files(log_dir: Path, source: str) -> list[Path]:
    bases = LOG_SOURCES.get(source, LOG_SOURCES["Application"])
    files: list[Path] = []
    for base in bases:
        for path in log_dir.glob(f"{base}*"):
            if path.is_file():
                files.append(path)

    def _sort_key(path: Path):
        try:
            return (path.stat().st_mtime, path.name)
        except Exception:
            return (0, path.name)

    files.sort(key=_sort_key)
    return files


def _read_tail_lines(path: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    try:
        file_size = int(path.stat().st_size)
    except Exception:
        return []
    if file_size <= 0:
        return []
    window = min(file_size, max(16 * 1024, int(limit) * 256))
    while True:
        start = max(0, file_size - window)
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                raw = handle.read(file_size - start)
        except Exception:
            return []
        if start > 0:
            newline_idx = raw.find(b"\n")
            if newline_idx >= 0:
                raw = raw[newline_idx + 1 :]
            else:
                raw = b""
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) >= limit or start <= 0:
            return lines[-limit:]
        if window >= file_size:
            return lines[-limit:]
        window = min(file_size, window * 2)


def _read_log_lines(paths: list[Path], limit: int = 1000) -> list[str]:
    capped_limit = max(1, int(limit))
    remaining = capped_limit
    chunks: list[list[str]] = []
    for path in reversed(paths):
        if remaining <= 0:
            break
        tail = _read_tail_lines(path, remaining)
        if not tail:
            continue
        chunks.append(tail)
        remaining -= len(tail)
    lines: list[str] = []
    for chunk in reversed(chunks):
        lines.extend(chunk)
    if len(lines) > capped_limit:
        return lines[-capped_limit:]
    return lines


def _filter_lines(lines: list[str], min_level: str) -> list[str]:
    min_value = LEVEL_ORDER.get(min_level, LEVEL_ORDER["INFO"])
    filtered: list[str] = []
    current_level = "INFO"
    for line in lines:
        match = LEVEL_PATTERN.search(line)
        if match:
            current_level = match.group(1)
        level_value = LEVEL_ORDER.get(current_level, LEVEL_ORDER["INFO"])
        if level_value >= min_value:
            filtered.append(line)
    return filtered


def _active_log_basenames() -> set[str]:
    names: set[str] = set()
    for values in LOG_SOURCES.values():
        for value in values:
            if value.endswith(".log"):
                names.add(value)
    return names


def _clear_log_files(paths: list[Path]) -> tuple[int, int]:
    active_basenames = _active_log_basenames()
    truncated = 0
    deleted = 0
    failures: list[str] = []

    for path in paths:
        try:
            if not path.is_file():
                continue
        except Exception:
            continue

        is_active = path.name in active_basenames
        if is_active:
            try:
                path.write_text("", encoding="utf-8")
                truncated += 1
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
            continue

        try:
            path.unlink()
            deleted += 1
            continue
        except Exception:
            pass

        try:
            path.write_text("", encoding="utf-8")
            truncated += 1
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")

    if failures:
        preview = "; ".join(failures[:3])
        if len(failures) > 3:
            preview = f"{preview}; ..."
        raise RuntimeError(f"Failed clearing one or more log files ({preview})")

    return truncated, deleted


class LogViewer(ttk.Frame):
    def __init__(
        self,
        parent,
        app,
        *,
        include_close: bool = False,
        close_callback=None,
        line_limit: int = 1000,
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self._include_close = include_close
        self._close_callback = close_callback
        self._line_limit = line_limit
        self._closing = False
        self._refresh_inflight = False
        self._refresh_pending: tuple[str, str] | None = None
        self._export_inflight = False
        self._clear_inflight = False
        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(toolbar, text="Source:").pack(side="left")
        self.source_var = tk.StringVar(value="Application")
        self.source_combo = ttk.Combobox(
            toolbar,
            textvariable=self.source_var,
            values=list(LOG_SOURCES.keys()),
            state="readonly",
            width=14,
        )
        self.source_combo.pack(side="left", padx=(6, 12))

        ttk.Label(toolbar, text="Level:").pack(side="left")
        self.level_var = tk.StringVar(value="INFO")
        self.level_combo = ttk.Combobox(
            toolbar,
            textvariable=self.level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            state="readonly",
            width=10,
        )
        self.level_combo.pack(side="left", padx=(6, 12))

        font = getattr(self.app, "console_font", None)
        if font:
            self.text = tk.Text(self, wrap=tk.NONE, height=28, font=font)
        else:
            self.text = tk.Text(self, wrap=tk.NONE, height=28)
        self.text.pack(fill="both", expand=True, side="left")

        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        y_scroll.pack(side="right", fill="y")
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        x_scroll.pack(fill="x")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")
        self.export_button = ttk.Button(actions, text="Export Logs...", command=self.export_logs)
        self.export_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(actions, text="Clear Logs", command=self.clear_logs)
        self.clear_button.pack(side="left", padx=(8, 0))
        if self._include_close:
            ttk.Button(actions, text="Close", command=self._close).pack(side="right")

        self.source_combo.bind("<<ComboboxSelected>>", self.refresh)
        self.level_combo.bind("<<ComboboxSelected>>", self.refresh)

    def _render(self, lines: list[str]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if lines:
            self.text.insert("end", "\n".join(lines))
        else:
            self.text.insert("end", "No log entries found.")
        self.text.configure(state="disabled")

    def _post_ui(self, callback) -> None:
        if self._closing:
            return
        after = getattr(self.app, "after", None)
        if callable(after):
            try:
                after(0, callback)
                return
            except Exception as exc:
                _log_suppressed("Failed posting Log Viewer callback to UI thread", exc)
        try:
            callback()
        except Exception as exc:
            _log_suppressed("Failed running Log Viewer callback", exc)

    def _start_refresh_worker(self) -> None:
        request = self._refresh_pending
        if request is None or self._closing:
            return
        self._refresh_pending = None
        source, level = request
        self._refresh_inflight = True
        started_at = time.perf_counter()

        def _worker() -> None:
            lines: list[str] = []
            error: Exception | None = None
            try:
                log_dir = get_log_dir()
                paths = _resolve_log_files(log_dir, source)
                if paths:
                    raw_lines = _read_log_lines(paths, limit=self._line_limit)
                    lines = _filter_lines(raw_lines, level)
            except Exception as exc:
                error = exc
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            self._post_ui(lambda: self._complete_refresh(request, lines, error, elapsed_ms))

        try:
            worker = threading.Thread(
                target=_worker,
                name="log-viewer-refresh",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._refresh_inflight = False
            _log_suppressed("Failed starting Log Viewer refresh thread", exc)
            self._render([])

    def _complete_refresh(
        self,
        request: tuple[str, str],
        lines: list[str],
        error: Exception | None,
        elapsed_ms: float,
    ) -> None:
        self._refresh_inflight = False
        if self._closing:
            return
        pending = self._refresh_pending
        stale = pending is not None and pending != request
        if error is not None:
            _log_suppressed("Log Viewer refresh failed", error)
            if not stale:
                self._render(["Failed to load logs."])
        elif not stale:
            self._render(lines)
        record_task_timing(self.app, "log_viewer.refresh", elapsed_ms, success=(error is None))
        if self._refresh_pending is not None:
            self._start_refresh_worker()

    def refresh(self, *_args) -> None:
        source = str(self.source_var.get() or "Application")
        level = str(self.level_var.get() or "INFO")
        self._refresh_pending = (source, level)
        if self._refresh_inflight:
            return
        self._start_refresh_worker()

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        try:
            self.export_button.configure(state=state)
            self.clear_button.configure(state=state)
        except Exception:
            return

    def _set_export_inflight(self, value: bool) -> None:
        self._export_inflight = bool(value)
        self._set_action_buttons_enabled(not (self._export_inflight or self._clear_inflight))

    def _set_clear_inflight(self, value: bool) -> None:
        self._clear_inflight = bool(value)
        self._set_action_buttons_enabled(not (self._export_inflight or self._clear_inflight))

    def _complete_export(self, out_path: Path, error: Exception | None, elapsed_ms: float) -> None:
        self._set_export_inflight(False)
        if self._closing:
            return
        record_task_timing(self.app, "log_viewer.export", elapsed_ms, success=(error is None))
        if error is None:
            messagebox.showinfo("Export Logs", f"Saved to:\n{out_path}")
            return
        messagebox.showerror("Export Logs", f"Failed to export logs:\n{error}")

    def export_logs(self) -> None:
        if self._export_inflight or self._clear_inflight:
            messagebox.showinfo("Export Logs", "Log operation already in progress.")
            return
        log_dir = get_log_dir()
        log_files = _resolve_log_files(log_dir, "All")
        if not log_files:
            messagebox.showinfo("Export Logs", "No log files found.")
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"simple_sender_logs_{timestamp}.zip"
        initial_dir = Path.home() / "Desktop"
        if not initial_dir.exists():
            initial_dir = Path.home()
        path = run_file_dialog(
            self.app,
            filedialog.asksaveasfilename,
            title="Export Logs",
            defaultextension=".zip",
            initialdir=str(initial_dir),
            initialfile=default_name,
            filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
        )
        if not path:
            return
        out_path = Path(path)
        self._set_export_inflight(True)
        started_at = time.perf_counter()

        def _worker() -> None:
            error: Exception | None = None
            try:
                with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for log_path in log_files:
                        try:
                            archive.write(log_path, arcname=log_path.name)
                        except Exception as exc:
                            _log_suppressed("Failed adding log file to export archive", exc)
            except Exception as exc:
                error = exc
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            self._post_ui(lambda: self._complete_export(out_path, error, elapsed_ms))

        try:
            worker = threading.Thread(
                target=_worker,
                name="log-viewer-export",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._set_export_inflight(False)
            _log_suppressed("Failed starting log export thread", exc)
            messagebox.showerror("Export Logs", f"Failed to export logs:\n{exc}")

    def _complete_clear(
        self,
        truncated: int,
        deleted: int,
        error: Exception | None,
        elapsed_ms: float,
    ) -> None:
        self._set_clear_inflight(False)
        if self._closing:
            return
        record_task_timing(self.app, "log_viewer.clear", elapsed_ms, success=(error is None))
        if error is not None:
            messagebox.showerror("Clear Logs", f"Failed to clear logs:\n{error}")
            return
        self.refresh()
        messagebox.showinfo(
            "Clear Logs",
            f"Cleared active logs: {truncated}\nRemoved rotated logs: {deleted}",
        )

    def clear_logs(self) -> None:
        if self._export_inflight or self._clear_inflight:
            messagebox.showinfo("Clear Logs", "Log operation already in progress.")
            return
        log_dir = get_log_dir()
        log_files = _resolve_log_files(log_dir, "All")
        if not log_files:
            messagebox.showinfo("Clear Logs", "No log files found.")
            return
        confirmed = messagebox.askyesno(
            "Clear Logs",
            "Clear all current logs and remove rotated log files?",
        )
        if not confirmed:
            return

        self._set_clear_inflight(True)
        started_at = time.perf_counter()

        def _worker() -> None:
            truncated = 0
            deleted = 0
            error: Exception | None = None
            try:
                truncated, deleted = _clear_log_files(log_files)
            except Exception as exc:
                error = exc
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            self._post_ui(lambda: self._complete_clear(truncated, deleted, error, elapsed_ms))

        try:
            worker = threading.Thread(
                target=_worker,
                name="log-viewer-clear",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._set_clear_inflight(False)
            _log_suppressed("Failed starting log clear thread", exc)
            messagebox.showerror("Clear Logs", f"Failed to clear logs:\n{exc}")

    def _on_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._closing = True

    def _close(self) -> None:
        if callable(self._close_callback):
            self._close_callback()
            return
        try:
            self.winfo_toplevel().destroy()
        except Exception:
            return
