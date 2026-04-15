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
from simple_sender.utils.log_suppressed import log_suppressed_exception
import re
import threading
import time
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, cast

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.theme_helpers import (
    bind_scrollbar_theme,
    bind_text_display_theme,
    notebook_page_style_name,
    text_display_theme_options,
)
from simple_sender.utils.atomic_files import atomic_replace_path
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
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _viewer_dialog_parent(viewer) -> tk.Misc | None:
    getter = getattr(viewer, "winfo_toplevel", None)
    if callable(getter):
        try:
            parent = getter()
            if parent is not None and bool(getattr(parent, "winfo_exists", lambda: True)()):
                return cast(tk.Misc, parent)
        except Exception:
            pass
    app = getattr(viewer, "app", None)
    parent = getattr(app, "_logs_window", None)
    if parent is None:
        return None
    try:
        if not bool(parent.winfo_exists()):
            return None
    except Exception:
        return None
    return cast(tk.Misc, parent)


def _showinfo(title: str, message: str, *, parent: tk.Misc | None = None) -> str:
    if parent is None:
        return messagebox.showinfo(title, message)
    return messagebox.showinfo(title, message, parent=parent)


def _showwarning(title: str, message: str, *, parent: tk.Misc | None = None) -> str:
    if parent is None:
        return messagebox.showwarning(title, message)
    return messagebox.showwarning(title, message, parent=parent)


def _showerror(title: str, message: str, *, parent: tk.Misc | None = None) -> str:
    if parent is None:
        return messagebox.showerror(title, message)
    return messagebox.showerror(title, message, parent=parent)


def _askyesno(title: str, message: str, *, parent: tk.Misc | None = None) -> bool:
    if parent is None:
        return bool(messagebox.askyesno(title, message))
    return bool(messagebox.askyesno(title, message, parent=parent))


def _resolve_log_files(log_dir: Path | None, source: str) -> list[Path]:
    """Return matching log files for a source, ordered oldest to newest."""

    if log_dir is None:
        return []
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
    """Read the most recent decoded lines from a log file."""

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
        sample = "; ".join(failures[:3])
        if len(failures) > 3:
            sample = f"{sample}; ..."
        raise RuntimeError(f"Failed clearing one or more log files ({sample})")

    return truncated, deleted


def _format_export_outcome(
    out_path: Path,
    *,
    total_files: int,
    written_files: int,
    failed_files: list[tuple[str, str]],
) -> tuple[str, str]:
    failed_count = len(failed_files)
    if failed_count <= 0:
        return (
            "info",
            f"Saved {written_files}/{total_files} log files to:\n{out_path}",
        )
    sample = "; ".join(f"{name}: {reason}" for name, reason in failed_files[:3])
    if failed_count > 3:
        sample = f"{sample}; ..."
    if written_files > 0:
        return (
            "warning",
            (
                f"Partial export saved {written_files}/{total_files} log files to:\n{out_path}\n\n"
                f"Failed files ({failed_count}): {sample}"
            ),
        )
    return (
        "error",
        f"Export failed; no log files were written.\nFailed files ({failed_count}): {sample}",
    )


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
        super().__init__(parent, padding=12, style=notebook_page_style_name())
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
        themed_options = cast(dict[str, Any], text_display_theme_options(self.app))
        if themed_options:
            self.text.configure(cnf=themed_options)
        self.text.pack(fill="both", expand=True, side="left")
        bind_text_display_theme(self.app, self.text)

        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        bind_scrollbar_theme(self.app, y_scroll)
        y_scroll.pack(side="right", fill="y")
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        bind_scrollbar_theme(self.app, x_scroll)
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

    def _post_ui(self, callback, *, on_drop=None) -> bool:
        if self._closing:
            if callable(on_drop):
                try:
                    on_drop()
                except Exception as exc:
                    _log_suppressed("Failed running Log Viewer drop-path cleanup", exc)
            return False
        if threading.current_thread() is threading.main_thread():
            try:
                callback()
            except Exception as exc:
                _log_suppressed("Failed running Log Viewer callback on UI thread", exc)
            return True
        post_ui = getattr(self.app, "_post_ui_thread", None)
        if callable(post_ui):
            try:
                post_ui(callback)
                return True
            except Exception as exc:
                _log_suppressed("Failed posting Log Viewer callback via _post_ui_thread", exc)
        ui_q = getattr(self.app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("ui_post", callback, (), {}))
                return True
            except Exception as exc:
                _log_suppressed("Failed posting Log Viewer callback via ui_q", exc)
        _log_suppressed("Dropped Log Viewer callback: no safe UI-post path", RuntimeError("no ui_post path"))
        if callable(on_drop):
            try:
                on_drop()
            except Exception as exc:
                _log_suppressed("Failed running Log Viewer drop-path cleanup", exc)
        return False

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
            self._post_ui(
                lambda: self._complete_refresh(request, lines, error, elapsed_ms),
                on_drop=lambda: setattr(self, "_refresh_inflight", False),
            )

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
            source, level = request
            _log_suppressed(
                f"Log Viewer refresh failed for source={source} level={level}",
                error,
            )
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

    def _complete_export(
        self,
        out_path: Path,
        *,
        total_files: int,
        written_files: int,
        failed_files: list[tuple[str, str]],
        error: Exception | None,
        elapsed_ms: float,
    ) -> None:
        self._set_export_inflight(False)
        if self._closing:
            return
        record_task_timing(self.app, "log_viewer.export", elapsed_ms, success=(error is None))
        if error is not None:
            _showerror(
                "Export Logs",
                f"Failed to export logs:\n{error}",
                parent=_viewer_dialog_parent(self),
            )
            return
        level, message = _format_export_outcome(
            out_path,
            total_files=max(0, int(total_files)),
            written_files=max(0, int(written_files)),
            failed_files=list(failed_files),
        )
        if level == "info":
            _showinfo("Export Logs", message, parent=_viewer_dialog_parent(self))
        elif level == "warning":
            _showwarning("Export Logs", message, parent=_viewer_dialog_parent(self))
        else:
            _showerror("Export Logs", message, parent=_viewer_dialog_parent(self))

    def export_logs(self) -> None:
        if self._export_inflight or self._clear_inflight:
            _showinfo(
                "Export Logs",
                "Log operation already in progress.",
                parent=_viewer_dialog_parent(self),
            )
            return
        log_dir = get_log_dir()
        log_files = _resolve_log_files(log_dir, "All")
        if not log_files:
            _showinfo(
                "Export Logs",
                "No log files found.",
                parent=_viewer_dialog_parent(self),
            )
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
            parent=_viewer_dialog_parent(self),
        )
        if not path:
            return
        out_path = Path(path)
        self._set_export_inflight(True)
        started_at = time.perf_counter()

        def _worker() -> None:
            error: Exception | None = None
            written_files = 0
            failed_files: list[tuple[str, str]] = []
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=str(out_path.parent),
                    prefix=f"{out_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for log_path in log_files:
                        try:
                            archive.write(log_path, arcname=log_path.name)
                            written_files += 1
                        except Exception as exc:
                            failed_files.append((log_path.name, str(exc)))
                            _log_suppressed("Failed adding log file to export archive", exc)
                assert temp_path is not None
                atomic_replace_path(temp_path, out_path)
            except Exception as exc:
                error = exc
                _log_suppressed(f"Failed exporting logs archive to {out_path}", exc)
            finally:
                if temp_path is not None and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            self._post_ui(
                lambda: self._complete_export(
                    out_path,
                    total_files=len(log_files),
                    written_files=written_files,
                    failed_files=failed_files,
                    error=error,
                    elapsed_ms=elapsed_ms,
                ),
                on_drop=lambda: self._set_export_inflight(False),
            )

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
            _showerror(
                "Export Logs",
                f"Failed to export logs:\n{exc}",
                parent=_viewer_dialog_parent(self),
            )

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
            _showerror(
                "Clear Logs",
                f"Failed to clear logs:\n{error}",
                parent=_viewer_dialog_parent(self),
            )
            return
        self.refresh()
        _showinfo(
            "Clear Logs",
            f"Cleared active logs: {truncated}\nRemoved rotated logs: {deleted}",
            parent=_viewer_dialog_parent(self),
        )

    def clear_logs(self) -> None:
        if self._export_inflight or self._clear_inflight:
            _showinfo(
                "Clear Logs",
                "Log operation already in progress.",
                parent=_viewer_dialog_parent(self),
            )
            return
        log_dir = get_log_dir()
        log_files = _resolve_log_files(log_dir, "All")
        if not log_files:
            _showinfo(
                "Clear Logs",
                "No log files found.",
                parent=_viewer_dialog_parent(self),
            )
            return
        confirmed = _askyesno(
            "Clear Logs",
            "Clear all current logs and remove rotated log files?",
            parent=_viewer_dialog_parent(self),
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
            self._post_ui(
                lambda: self._complete_clear(truncated, deleted, error, elapsed_ms),
                on_drop=lambda: self._set_clear_inflight(False),
            )

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
            _showerror(
                "Clear Logs",
                f"Failed to clear logs:\n{exc}",
                parent=_viewer_dialog_parent(self),
            )

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

