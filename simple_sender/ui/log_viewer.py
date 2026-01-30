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

import re
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
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


def _read_log_lines(paths: list[Path], limit: int = 1000) -> list[str]:
    lines: deque[str] = deque(maxlen=limit)
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    lines.append(line.rstrip("\n"))
        except Exception:
            continue
    return list(lines)


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
        self._build_ui()
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
        ttk.Button(actions, text="Export Logs...", command=self.export_logs).pack(side="left", padx=(8, 0))
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

    def refresh(self, *_args) -> None:
        log_dir = get_log_dir()
        paths = _resolve_log_files(log_dir, self.source_var.get())
        if not paths:
            self._render([])
            return
        lines = _read_log_lines(paths, limit=self._line_limit)
        filtered = _filter_lines(lines, self.level_var.get())
        self._render(filtered)

    def export_logs(self) -> None:
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
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for log_path in log_files:
                    try:
                        archive.write(log_path, arcname=log_path.name)
                    except Exception:
                        continue
            messagebox.showinfo("Export Logs", f"Saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Logs", f"Failed to export logs:\n{exc}")

    def _close(self) -> None:
        if callable(self._close_callback):
            self._close_callback()
            return
        try:
            self.winfo_toplevel().destroy()
        except Exception:
            pass
