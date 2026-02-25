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
import os
import string
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import messagebox, ttk

from simple_sender.ui.dialogs.popup_utils import center_window

GCODE_FILE_EXTENSIONS = (".nc", ".gcode", ".tap", ".txt")
USE_SYSTEM_FILE_PICKER = "__use_system_file_picker__"
_DRIVES_VIEW_SENTINEL = "__drives_view__"

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


@dataclass(frozen=True)
class BrowserEntry:
    name: str
    path: str
    is_dir: bool
    kind: str
    size_label: str = ""


def _coerce_scale(value: object, default: float = 1.5) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if parsed <= 0:
        return default
    return max(1.0, min(2.5, parsed))


def _safe_ui_scale(app) -> float:
    try:
        return _coerce_scale(app.ui_scale.get(), 1.5)
    except Exception:
        return 1.5


def _normalize_start_dir(path: str) -> str:
    text = str(path or "").strip()
    if text:
        try:
            expanded = os.path.abspath(os.path.expanduser(text))
            if os.path.isdir(expanded):
                return expanded
        except Exception:
            pass
    for fallback in (os.path.expanduser("~"), os.getcwd()):
        try:
            resolved = os.path.abspath(str(fallback))
            if os.path.isdir(resolved):
                return resolved
        except Exception:
            continue
    return ""


def _windows_drive_roots() -> list[str]:
    if os.name != "nt":
        return []
    roots: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if os.path.isdir(root):
                roots.append(root)
        except Exception:
            continue
    return roots


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _is_allowed_file(path: str, allowed_exts: set[str]) -> bool:
    if not allowed_exts:
        return True
    try:
        ext = os.path.splitext(path)[1].lower()
    except Exception:
        return False
    return ext in allowed_exts


def _list_entries(current_dir: str, allowed_exts: set[str]) -> list[BrowserEntry]:
    if current_dir == _DRIVES_VIEW_SENTINEL:
        drives = _windows_drive_roots()
        return [
            BrowserEntry(name=drive, path=drive, is_dir=True, kind="Drive")
            for drive in drives
        ]

    entries: list[BrowserEntry] = []
    with os.scandir(current_dir) as iterator:
        for item in iterator:
            try:
                if item.is_dir(follow_symlinks=False):
                    entries.append(
                        BrowserEntry(
                            name=item.name,
                            path=item.path,
                            is_dir=True,
                            kind="Folder",
                        )
                    )
                    continue
                if not item.is_file(follow_symlinks=False):
                    continue
                if not _is_allowed_file(item.path, allowed_exts):
                    continue
                size = ""
                try:
                    size = _format_size(int(item.stat(follow_symlinks=False).st_size))
                except Exception:
                    size = ""
                entries.append(
                    BrowserEntry(
                        name=item.name,
                        path=item.path,
                        is_dir=False,
                        kind="File",
                        size_label=size,
                    )
                )
            except PermissionError:
                continue
            except OSError:
                continue
    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.lower()))
    return entries


class _TouchFileBrowserDialog:
    def __init__(self, app, *, start_dir: str, allowed_exts: set[str], title: str) -> None:
        self.app = app
        self.allowed_exts = allowed_exts
        self.current_dir = start_dir or _normalize_start_dir("")
        self.result = ""
        self._item_entry: dict[str, BrowserEntry] = {}
        self._building = False

        self.window = tk.Toplevel(app)
        self.window.title(title)
        self.window.transient(app)
        self.window.grab_set()
        self.window.resizable(True, True)
        self.window.minsize(760, 520)
        self.window.geometry("980x680")
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

        self.path_var = tk.StringVar(value="")
        self.selection_var = tk.StringVar(value="Select a file to load.")

        self._build_ui()
        self._refresh_entries()

    def show(self) -> str:
        try:
            center_window(self.window, self.app)
        except Exception as exc:
            _log_suppressed("Failed centering touch file browser", exc)
        try:
            self.window.focus_set()
        except Exception as exc:
            _log_suppressed("Failed focusing touch file browser", exc)
        self.window.wait_window()
        return self.result

    def _build_ui(self) -> None:
        scale = _safe_ui_scale(self.app)
        base_font = tkfont.nametofont("TkDefaultFont")
        body_font = tkfont.Font(
            family=base_font.cget("family"),
            size=max(9, int(round(abs(int(base_font.cget("size"))) * 1.05))),
            weight=base_font.cget("weight"),
        )
        heading_font = tkfont.Font(
            family=base_font.cget("family"),
            size=max(10, int(round(abs(int(base_font.cget("size"))) * 1.1))),
            weight="bold",
        )

        style = ttk.Style()
        row_height = max(34, int(round(30 * scale)))
        style.configure("SimpleSender.TouchBrowser.Treeview", rowheight=row_height, font=body_font)
        style.configure(
            "SimpleSender.TouchBrowser.Treeview.Heading",
            font=heading_font,
        )
        style.configure(
            "SimpleSender.TouchBrowser.TButton",
            padding=(12, max(8, int(round(8 * scale)))),
            font=body_font,
        )

        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(2, weight=1)

        ttk.Label(
            outer,
            text="Touch File Browser",
            font=heading_font,
        ).grid(row=0, column=0, sticky="w")

        path_wrap = ttk.Frame(outer)
        path_wrap.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        path_wrap.grid_columnconfigure(0, weight=1)
        ttk.Label(path_wrap, text="Path:", font=heading_font).grid(row=0, column=0, sticky="w")
        ttk.Label(
            path_wrap,
            textvariable=self.path_var,
            wraplength=920,
            justify="left",
            font=body_font,
        ).grid(row=1, column=0, sticky="ew", pady=(2, 0))

        controls = ttk.Frame(outer)
        controls.grid(row=2, column=0, sticky="nsew")
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_rowconfigure(1, weight=1)

        nav = ttk.Frame(controls)
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for col in range(6):
            nav.grid_columnconfigure(col, weight=0)
        nav.grid_columnconfigure(5, weight=1)
        ttk.Button(
            nav,
            text="Home",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._go_home,
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Button(
            nav,
            text="Up",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._go_up,
        ).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ttk.Button(
            nav,
            text="Refresh",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._refresh_entries,
        ).grid(row=0, column=2, sticky="w", padx=(0, 6))
        if os.name == "nt":
            ttk.Button(
                nav,
                text="Drives",
                style="SimpleSender.TouchBrowser.TButton",
                command=self._show_drives,
            ).grid(row=0, column=3, sticky="w", padx=(0, 6))
            fallback_col = 4
        else:
            fallback_col = 3
        ttk.Button(
            nav,
            text="Use System Picker",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._use_system_picker,
        ).grid(row=0, column=fallback_col, sticky="w")

        list_frame = ttk.Frame(controls)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            list_frame,
            columns=("kind", "size"),
            show="tree headings",
            selectmode="browse",
            style="SimpleSender.TouchBrowser.Treeview",
        )
        self.tree.heading("#0", text="Name")
        self.tree.heading("kind", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=560, stretch=True)
        self.tree.column("kind", width=120, anchor="center", stretch=False)
        self.tree.column("size", width=120, anchor="e", stretch=False)

        y_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._activate_selected)
        self.tree.bind("<Return>", self._activate_selected)

        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        actions.grid_columnconfigure(0, weight=1)
        ttk.Label(
            actions,
            textvariable=self.selection_var,
            wraplength=760,
            justify="left",
            font=body_font,
        ).grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(actions)
        btns.grid(row=0, column=1, sticky="e")
        ttk.Button(
            btns,
            text="Cancel",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._cancel,
        ).pack(side="right")
        self.btn_select = ttk.Button(
            btns,
            text="Select File",
            style="SimpleSender.TouchBrowser.TButton",
            command=self._confirm_selection,
            state="disabled",
        )
        self.btn_select.pack(side="right", padx=(0, 8))

    def _show_drives(self) -> None:
        self.current_dir = _DRIVES_VIEW_SENTINEL
        self._refresh_entries()

    def _go_home(self) -> None:
        self.current_dir = _normalize_start_dir("")
        self._refresh_entries()

    def _go_up(self) -> None:
        if self.current_dir == _DRIVES_VIEW_SENTINEL:
            return
        try:
            parent = os.path.dirname(self.current_dir.rstrip("\\/"))
        except Exception:
            parent = ""
        if not parent:
            if os.name == "nt":
                self.current_dir = _DRIVES_VIEW_SENTINEL
            else:
                self.current_dir = "/"
        else:
            self.current_dir = parent
        self._refresh_entries()

    def _safe_set_path_label(self) -> None:
        if self.current_dir == _DRIVES_VIEW_SENTINEL:
            self.path_var.set("Computer drives")
            return
        self.path_var.set(self.current_dir or _normalize_start_dir(""))

    def _refresh_entries(self) -> None:
        self._building = True
        self._safe_set_path_label()
        self.selection_var.set("Select a file to load.")
        self.btn_select.config(state="disabled", text="Select File")
        self._item_entry.clear()
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        try:
            entries = _list_entries(self.current_dir, self.allowed_exts)
        except Exception as exc:
            _log_suppressed("Failed listing touch file browser directory", exc)
            try:
                messagebox.showwarning(
                    "Open G-code",
                    f"Cannot access folder:\n{self.current_dir}\n\n{exc}",
                    parent=self.window,
                )
            except Exception as msg_exc:
                _log_suppressed("Failed showing folder access warning in touch browser", msg_exc)
            fallback = _normalize_start_dir("")
            self.current_dir = fallback
            self._safe_set_path_label()
            try:
                entries = _list_entries(self.current_dir, self.allowed_exts)
            except Exception as retry_exc:
                _log_suppressed("Failed listing touch browser fallback directory", retry_exc)
                entries = []

        for idx, entry in enumerate(entries):
            item_id = f"entry_{idx}"
            name_prefix = "[DIR] " if entry.is_dir else ""
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                text=f"{name_prefix}{entry.name}",
                values=(entry.kind, entry.size_label),
            )
            self._item_entry[item_id] = entry
        self._building = False

    def _selected_entry(self) -> BrowserEntry | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return self._item_entry.get(selected[0])

    def _on_select(self, _event=None) -> None:
        if self._building:
            return
        entry = self._selected_entry()
        if entry is None:
            self.selection_var.set("Select a file to load.")
            self.btn_select.config(state="disabled", text="Select File")
            return
        if entry.is_dir:
            self.selection_var.set(f"Folder selected: {entry.path}")
            self.btn_select.config(state="normal", text="Open Folder")
            return
        self.selection_var.set(f"File selected: {entry.path}")
        self.btn_select.config(state="normal", text="Select File")

    def _activate_selected(self, _event=None) -> None:
        self._confirm_selection()

    def _confirm_selection(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.is_dir:
            self.current_dir = entry.path
            self._refresh_entries()
            return
        self.result = entry.path
        self._close()

    def _use_system_picker(self) -> None:
        self.result = USE_SYSTEM_FILE_PICKER
        self._close()

    def _cancel(self) -> None:
        self.result = ""
        self._close()

    def _close(self) -> None:
        try:
            self.window.destroy()
        except Exception as exc:
            _log_suppressed("Failed closing touch file browser", exc)


def browse_for_gcode_path(app, *, initial_dir: str = "") -> str:
    allowed_exts = {ext.lower() for ext in GCODE_FILE_EXTENSIONS}
    start_dir = _normalize_start_dir(initial_dir)
    dialog = _TouchFileBrowserDialog(
        app,
        start_dir=start_dir,
        allowed_exts=allowed_exts,
        title="Open G-code",
    )
    return dialog.show()

