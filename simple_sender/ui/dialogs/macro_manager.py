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

import os
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any

from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.macro_files import (
    get_writable_macro_dir,
    read_macro_slot,
    remove_macro_slot,
    write_macro_slot,
)


class _MacroManagerDialog:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.window = tk.Toplevel(app)
        self.window.title("Macro Manager")
        self.window.transient(app)
        self.window.resizable(True, True)
        self.window.minsize(900, 520)
        self._macro_dir = get_writable_macro_dir(app)
        self._selected_slot = 1
        self._slot_data: dict[int, tuple[str, str, str, str | None]] = {}

        root = ttk.Frame(self.window, padding=10)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(1, weight=1)

        ttk.Label(
            root,
            text=(
                f"Editing directory: {self._macro_dir}"
                if self._macro_dir
                else "No writable macro directory found."
            ),
            wraplength=860,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        ttk.Label(left, text="Macro slots").grid(row=0, column=0, sticky="w")
        self.slot_list = tk.Listbox(left, height=12, exportselection=False, width=28)
        self.slot_list.grid(row=1, column=0, sticky="nsw")
        self.slot_list.bind("<<ListboxSelect>>", self._on_slot_select)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_columnconfigure(1, weight=1)
        right.grid_rowconfigure(2, weight=1)

        ttk.Label(right, text="Name").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(right, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(right, text="Tooltip").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.tip_var = tk.StringVar()
        self.tip_entry = ttk.Entry(right, textvariable=self.tip_var)
        self.tip_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(right, text="Body").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.body_text = tk.Text(right, wrap="word", height=16)
        self.body_text.grid(row=2, column=1, sticky="nsew", pady=4)
        body_scroll = ttk.Scrollbar(right, orient="vertical", command=self.body_text.yview)
        body_scroll.grid(row=2, column=2, sticky="ns", pady=4)
        self.body_text.configure(yscrollcommand=body_scroll.set)

        actions = ttk.Frame(root)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.grid_columnconfigure(8, weight=1)

        self.btn_reload = ttk.Button(actions, text="Reload", command=self.refresh)
        self.btn_reload.grid(row=0, column=0, padx=(0, 6))
        self.btn_save = ttk.Button(actions, text="Save Slot", command=self.save_current)
        self.btn_save.grid(row=0, column=1, padx=(0, 6))
        self.btn_blank = ttk.Button(actions, text="New Blank", command=self.new_blank)
        self.btn_blank.grid(row=0, column=2, padx=(0, 6))
        self.btn_delete = ttk.Button(actions, text="Delete Slot", command=self.delete_current)
        self.btn_delete.grid(row=0, column=3, padx=(0, 6))
        self.btn_up = ttk.Button(actions, text="Move Up", command=lambda: self.move_current(-1))
        self.btn_up.grid(row=0, column=4, padx=(0, 6))
        self.btn_down = ttk.Button(actions, text="Move Down", command=lambda: self.move_current(1))
        self.btn_down.grid(row=0, column=5, padx=(0, 6))
        ttk.Label(actions, text="Duplicate to").grid(row=0, column=6, padx=(0, 6))
        self.duplicate_target = tk.StringVar(value="2")
        self.duplicate_combo = ttk.Combobox(
            actions,
            state="readonly",
            width=6,
            textvariable=self.duplicate_target,
            values=["1", "2", "3", "4", "5", "6", "7", "8"],
        )
        self.duplicate_combo.grid(row=0, column=7, padx=(0, 6))
        self.btn_duplicate = ttk.Button(actions, text="Duplicate", command=self.duplicate_current)
        self.btn_duplicate.grid(row=0, column=8, sticky="w")
        ttk.Button(actions, text="Close", command=self.close).grid(row=0, column=9, padx=(10, 0))

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        center_window(self.window, app)
        self.refresh()
        self.slot_list.selection_set(0)
        self.slot_list.event_generate("<<ListboxSelect>>")

    def _set_editing_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        try:
            self.name_entry.configure(state=state)
            self.tip_entry.configure(state=state)
            self.body_text.configure(state="normal" if enabled else "disabled")
            self.btn_save.configure(state=state)
            self.btn_blank.configure(state=state)
            self.btn_delete.configure(state=state)
            self.btn_up.configure(state=state)
            self.btn_down.configure(state=state)
            self.btn_duplicate.configure(state=state)
            self.duplicate_combo.configure(state="readonly" if enabled else "disabled")
        except Exception:
            pass

    def refresh(self) -> None:
        self.slot_list.delete(0, "end")
        self._slot_data = {}
        for slot in range(1, 9):
            name, tip, body, path = read_macro_slot(self.app, slot)
            self._slot_data[slot] = (name, tip, body, path)
            label = name or "(empty)"
            self.slot_list.insert("end", f"Macro-{slot}: {label}")
        can_edit = self._macro_dir is not None
        self._set_editing_enabled(can_edit)

    def _current_editor_values(self) -> tuple[str, str, str]:
        name = self.name_var.get().strip()
        tip = self.tip_var.get().strip()
        body = self.body_text.get("1.0", "end-1c")
        return name, tip, body

    def _load_slot(self, slot: int) -> None:
        self._selected_slot = int(slot)
        name, tip, body, _path = self._slot_data.get(slot, ("", "", "", None))
        self.name_var.set(name)
        self.tip_var.set(tip)
        self.body_text.delete("1.0", "end")
        if body:
            self.body_text.insert("1.0", body)
        duplicate_target = int(self.duplicate_target.get() or "0")
        if self._selected_slot == duplicate_target:
            next_target = 1 if self._selected_slot == 8 else self._selected_slot + 1
            self.duplicate_target.set(str(next_target))

    def _on_slot_select(self, _event=None) -> None:
        selection = self.slot_list.curselection()
        if not selection:
            return
        slot = int(selection[0]) + 1
        self._load_slot(slot)

    def _refresh_macro_buttons(self) -> None:
        try:
            panel = getattr(self.app, "macro_panel", None)
            if panel is not None and hasattr(panel, "refresh"):
                panel.refresh()
        except Exception:
            pass

    def _write_slot(self, slot: int, name: str, tip: str, body: str) -> None:
        if not self._macro_dir:
            raise RuntimeError("No writable macro directory found.")
        write_macro_slot(self._macro_dir, slot, name=name, tip=tip, body=body)

    def save_current(self) -> None:
        if not self._macro_dir:
            messagebox.showwarning("Macro Manager", "No writable macro directory found.")
            return
        name, tip, body = self._current_editor_values()
        if not name:
            messagebox.showwarning("Macro Manager", "Macro name cannot be empty.")
            return
        try:
            self._write_slot(self._selected_slot, name, tip, body)
        except Exception as exc:
            messagebox.showerror("Macro Manager", f"Failed to save macro:\n{exc}")
            return
        self.refresh()
        self.slot_list.selection_clear(0, "end")
        self.slot_list.selection_set(self._selected_slot - 1)
        self._load_slot(self._selected_slot)
        self._refresh_macro_buttons()

    def new_blank(self) -> None:
        self.name_var.set(f"Macro {self._selected_slot}")
        self.tip_var.set("")
        self.body_text.delete("1.0", "end")

    def delete_current(self) -> None:
        if not self._macro_dir:
            messagebox.showwarning("Macro Manager", "No writable macro directory found.")
            return
        if not messagebox.askyesno(
            "Macro Manager",
            f"Delete Macro-{self._selected_slot} from the editable macro directory?",
        ):
            return
        try:
            remove_macro_slot(self._macro_dir, self._selected_slot)
        except Exception as exc:
            messagebox.showerror("Macro Manager", f"Failed to delete macro:\n{exc}")
            return
        self.refresh()
        self.slot_list.selection_clear(0, "end")
        self.slot_list.selection_set(self._selected_slot - 1)
        self._load_slot(self._selected_slot)
        self._refresh_macro_buttons()

    def duplicate_current(self) -> None:
        if not self._macro_dir:
            messagebox.showwarning("Macro Manager", "No writable macro directory found.")
            return
        target = int(self.duplicate_target.get())
        source = int(self._selected_slot)
        if target == source:
            messagebox.showwarning("Macro Manager", "Pick a different target slot.")
            return
        name, tip, body = self._current_editor_values()
        if not name:
            messagebox.showwarning("Macro Manager", "Macro name cannot be empty.")
            return
        try:
            self._write_slot(target, name, tip, body)
        except Exception as exc:
            messagebox.showerror("Macro Manager", f"Failed to duplicate macro:\n{exc}")
            return
        self.refresh()
        self._refresh_macro_buttons()
        messagebox.showinfo("Macro Manager", f"Copied Macro-{source} to Macro-{target}.")

    def move_current(self, delta: int) -> None:
        if not self._macro_dir:
            messagebox.showwarning("Macro Manager", "No writable macro directory found.")
            return
        source = int(self._selected_slot)
        target = source + int(delta)
        if target < 1 or target > 8:
            return
        src_name, src_tip, src_body = self._current_editor_values()
        if not src_name:
            messagebox.showwarning("Macro Manager", "Macro name cannot be empty.")
            return
        tgt_name, tgt_tip, tgt_body, tgt_path = self._slot_data.get(target, ("", "", "", None))
        try:
            self._write_slot(target, src_name, src_tip, src_body)
            if tgt_name or tgt_tip or tgt_body or tgt_path:
                self._write_slot(source, tgt_name, tgt_tip, tgt_body)
            else:
                remove_macro_slot(self._macro_dir, source)
        except Exception as exc:
            messagebox.showerror("Macro Manager", f"Failed to reorder macro:\n{exc}")
            return
        self.refresh()
        self.slot_list.selection_clear(0, "end")
        self.slot_list.selection_set(target - 1)
        self._load_slot(target)
        self._refresh_macro_buttons()

    def close(self) -> None:
        try:
            setattr(self.app, "_macro_manager_window", None)
        except Exception:
            pass
        self.window.destroy()


def show_macro_manager(app: Any) -> None:
    existing = getattr(app, "_macro_manager_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass
    dialog = _MacroManagerDialog(app)
    try:
        app._macro_manager_window = dialog.window
    except Exception:
        pass
