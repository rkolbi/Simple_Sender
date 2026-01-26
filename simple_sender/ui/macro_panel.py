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

import tkinter as tk
from tkinter import ttk, messagebox

from simple_sender.ui.widgets import apply_tooltip, set_kb_id
from simple_sender.ui.popup_utils import center_window

class MacroPanel:
    def __init__(self, app):
        self.app = app
        self._left_frame: ttk.Frame | None = None
        self._right_frame: ttk.Frame | None = None
        self._macro_buttons: list[tk.Widget] = []

    def attach_frames(self, left: ttk.Frame, right: ttk.Frame):
        self._left_frame = left
        self._right_frame = right
        self._load_macro_buttons()

    def _macro_path(self, index: int) -> str | None:
        return self.app.macro_executor.macro_path(index)

    def _read_macro_header(self, path: str, index: int) -> tuple[str, str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                name = f.readline().strip()
                tip = f.readline().strip()
            if not name:
                name = f"Macro {index}"
            return name, tip
        except Exception:
            return f"Macro {index}", ""

    def _show_macro_preview(self, name: str, lines: list[str]) -> None:
        body = "".join(lines[2:]) if len(lines) > 2 else ""
        dlg = tk.Toplevel(self.app)
        dlg.title(f"Macro Preview - {name}")
        dlg.transient(self.app)
        dlg.grab_set()
        dlg.resizable(True, True)
        frame = ttk.Frame(dlg, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=name, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 6))
        text = tk.Text(frame, wrap="word", height=14, width=80, state="normal")
        text.insert("end", body)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="left", padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        center_window(dlg, self.app)
        dlg.wait_window()

    def _preview_macro(self, index: int):
        path = self._macro_path(index)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Macro error", str(e))
            return
        name = lines[0].strip() if lines else f"Macro {index}"
        self._show_macro_preview(name, lines)

    def _run_macro(self, index: int):
        self.app.macro_executor.run_macro(index)

    def _load_macro_buttons(self):
        if not self._left_frame or not self._right_frame:
            return
        if self._macro_buttons:
            self.app._manual_controls = [w for w in self.app._manual_controls if w not in self._macro_buttons]
        self._macro_buttons = []
        for w in self._left_frame.winfo_children():
            w.destroy()
        for w in self._right_frame.winfo_children():
            w.destroy()
        self._right_frame.grid_columnconfigure(0, weight=1, uniform="macro_buttons", minsize=140)
        self._right_frame.grid_columnconfigure(1, weight=1, uniform="macro_buttons", minsize=140)
        self._right_frame.grid_rowconfigure(0, weight=0)

        for idx in (1, 2, 3):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(self._left_frame, text=name, command=lambda i=idx: self._run_macro(i))
            set_kb_id(btn, f"macro_{idx}")
            btn.pack(fill="x", pady=(0, 4))
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self.app._manual_controls.append(btn)
            self._macro_buttons.append(btn)

        col = 0
        row = 0
        for idx in (4, 5, 6, 7):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(self._right_frame, text=name, command=lambda i=idx: self._run_macro(i))
            set_kb_id(btn, f"macro_{idx}")
            if col == 0:
                padx = (0, 8)
            else:
                padx = (20, 0)
            btn.grid(row=row, column=col, padx=padx, pady=2, sticky="ew")
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self.app._manual_controls.append(btn)
            self._macro_buttons.append(btn)
            col += 1
            if col > 1:
                col = 0
                row += 1
        self.app._refresh_keyboard_table()


