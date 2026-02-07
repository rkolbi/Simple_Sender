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

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Sequence

from simple_sender.ui.widgets import (
    ToolTip,
    apply_tooltip,
    attach_log_gcode,
    attach_numeric_keypad,
    set_kb_id,
    set_tab_tooltip,
)
from simple_sender.utils.constants import (
    GRBL_NON_NUMERIC_SETTINGS,
    GRBL_SETTING_KEYS,
    GRBL_SETTING_LIMITS,
    GRBL_SETTINGS_WRITE_DELAY,
)

logger = logging.getLogger(__name__)

class GRBLSettingsController:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.settings_tree: ttk.Treeview | None = None
        self.settings_raw_text: tk.Text | None = None
        self.settings_tip: ToolTip | None = None
        self.btn_refresh: ttk.Button | None = None
        self.btn_save: ttk.Button | None = None
        self._settings_capture = False
        self._settings_data: dict[str, tuple[str, int | None]] = {}
        self._settings_values: dict[str, str] = {}
        self._settings_edited: dict[str, str] = {}
        self._settings_edit_entry: ttk.Entry | None = None
        self._settings_baseline: dict[str, str] = {}
        self._settings_items: dict[str, str] = {}
        self._settings_raw_lines: list[str] = []
        self._settings_entry_meta: dict[ttk.Entry, tuple[str | None, str | None]] = {}
        self._settings_saving = False
        self._settings_prev_state: dict[str, Any] = {}

    def build_tabs(self, notebook: ttk.Notebook) -> None:
        rtab = ttk.Frame(notebook, padding=6)
        notebook.add(rtab, text="Raw $$")
        set_tab_tooltip(notebook, rtab, "View the raw $$ settings dump from GRBL.")
        self.settings_raw_text = tk.Text(rtab, wrap="word", height=12, state="disabled")
        rsb = ttk.Scrollbar(rtab, orient="vertical", command=self.settings_raw_text.yview)
        self.settings_raw_text.configure(yscrollcommand=rsb.set)
        self.settings_raw_text.grid(row=0, column=0, sticky="nsew")
        rsb.grid(row=0, column=1, sticky="ns")
        rtab.grid_rowconfigure(0, weight=1)
        rtab.grid_columnconfigure(0, weight=1)

        stab = ttk.Frame(notebook, padding=6)
        notebook.add(stab, text="GRBL Settings")
        set_tab_tooltip(notebook, stab, "Edit GRBL configuration values and save changes.")
        sbar = ttk.Frame(stab)
        sbar.pack(fill="x", pady=(0, 6))
        self.btn_refresh = ttk.Button(
            sbar,
            text="Refresh $$",
            command=self.app._request_settings_dump,
        )
        set_kb_id(self.btn_refresh, "grbl_settings_refresh")
        self.btn_refresh.pack(side="left")
        apply_tooltip(self.btn_refresh, "Request $$ settings from GRBL.")
        attach_log_gcode(self.btn_refresh, "$$")
        self.app._manual_controls.append(self.btn_refresh)
        self.btn_save = ttk.Button(
            sbar,
            text="Save Changes",
            command=self.save_changes,
        )
        set_kb_id(self.btn_save, "grbl_settings_save")
        self.btn_save.pack(side="left", padx=(8, 0))
        apply_tooltip(self.btn_save, "Send edited settings to GRBL.")
        self.app._manual_controls.append(self.btn_save)

        self.settings_tree = ttk.Treeview(
            stab,
            columns=("setting", "name", "value", "units", "desc"),
            show="headings",
            height=12,
        )
        self.settings_tree.heading("setting", text="Setting")
        self.settings_tree.heading("name", text="Name")
        self.settings_tree.heading("value", text="Value")
        self.settings_tree.heading("units", text="Units")
        self.settings_tree.heading("desc", text="Description")
        self.settings_tree.column("setting", width=80, anchor="w")
        self.settings_tree.column("name", width=200, anchor="w")
        self.settings_tree.column("value", width=120, anchor="w")
        self.settings_tree.column("units", width=100, anchor="w")
        self.settings_tree.column("desc", width=420, anchor="w")
        self.settings_tree.pack(fill="both", expand=True)
        self.settings_tree.bind("<Double-1>", self._edit_setting_value)
        self.settings_tree.bind("<Motion>", self._settings_tooltip_motion)
        self.settings_tree.bind("<Leave>", self._settings_tooltip_hide)
        self.settings_tip = ToolTip(self.settings_tree, "")
        self.settings_tree.tag_configure("edited", background="#fff5c2")

    def start_capture(self, header: str = "Requesting $$...") -> None:
        self._settings_capture = True
        self._settings_data = {}
        self._settings_edited = {}
        self._settings_raw_lines = []
        self._render_settings_raw(header)

    def handle_line(self, line: str) -> None:
        if not self._settings_capture:
            s = line.strip()
            if s.startswith("$") and "=" in s:
                self.start_capture("Captured $$ output")
            else:
                return
        s = line.strip()
        if s.startswith("<") and s.endswith(">"):
            return
        low = s.lower()
        if low != "ok" and not low.startswith("error"):
            self._settings_raw_lines.append(s)
        if s.startswith("$") and "=" in s:
            key, value = s.split("=", 1)
            try:
                idx = int(key[1:])
            except Exception:
                idx = None
            self._settings_data[key] = (value.strip(), idx)
            return
        if low == "ok":
            self._settings_capture = False
            self._render_settings()
            self._update_rapid_rates()
            self._update_accel_rates()
            if self.app._last_gcode_lines:
                self.app._update_gcode_stats(self.app._last_gcode_lines)
            self._render_settings_raw()
        elif low.startswith("error"):
            self._settings_capture = False
            self.app.status.config(text=f"Settings error: {s}")
            self._render_settings_raw()

    def save_changes(self) -> None:
        if self._settings_saving:
            messagebox.showinfo("Busy", "Settings save already in progress.")
            return
        self._commit_pending_setting_edit()
        if not self.app.grbl.is_connected():
            messagebox.showwarning("Not connected", "Connect to GRBL first.")
            return
        if self.app.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before saving settings.")
            return
        if not self._settings_edited:
            messagebox.showinfo("No changes", "No settings have been edited.")
            return
        if not messagebox.askyesno("Confirm save", "Send edited settings to GRBL?"):
            return
        changes: list[tuple[str, str]] = []
        for key, value in self._settings_edited.items():
            val = "" if value is None else str(value).strip()
            if val == "":
                continue
            changes.append((key, val))
        if not changes:
            messagebox.showinfo("No changes", "No non-empty settings to send.")
            return
        self._settings_saving = True
        self._set_settings_edit_enabled(False)

        def worker() -> None:
            sent = 0
            try:
                for key, val in changes:
                    self.app._send_manual(f"{key}={val}", "settings")
                    sent += 1
                    time.sleep(GRBL_SETTINGS_WRITE_DELAY)
            except Exception as exc:
                self.app.ui_q.put(("log", f"[settings] Save failed: {exc}"))
                try:
                    def finish_failed() -> None:
                        self._finish_settings_save_failed(str(exc))

                    self.app.after(0, finish_failed)
                except Exception:
                    pass
                return
            self.app.ui_q.put(("log", f"[settings] Sent {sent} change(s)."))
            try:
                def finish_save(sent_count: int = sent) -> None:
                    self._finish_settings_save(changes, sent_count)

                self.app.after(0, finish_save)
            except Exception as exc:
                logger.exception("Failed to schedule settings refresh: %s", exc)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_settings_save(self, changes: Sequence[tuple[str, str]], sent_count: int) -> None:
        self._settings_edited = {}
        self._settings_saving = False
        self._set_settings_edit_enabled(True)
        self._mark_settings_saved(changes, sent_count, refresh=True)

    def _finish_settings_save_failed(self, message: str | None = None) -> None:
        self._settings_saving = False
        self._set_settings_edit_enabled(True)
        if message:
            try:
                self.app.status.config(text=f"Settings save failed: {message}")
            except Exception:
                pass

    def _set_settings_edit_enabled(self, enabled: bool) -> None:
        if not enabled:
            if self.settings_tree and "tree" not in self._settings_prev_state:
                self._settings_prev_state["tree"] = self.settings_tree.state()
                self.settings_tree.state(["disabled"])
            if self.btn_save and "save" not in self._settings_prev_state:
                self._settings_prev_state["save"] = self.btn_save.cget("state")
                self.btn_save.config(state="disabled")
            if self.btn_refresh and "refresh" not in self._settings_prev_state:
                self._settings_prev_state["refresh"] = self.btn_refresh.cget("state")
                self.btn_refresh.config(state="disabled")
            return
        if self.settings_tree and "tree" in self._settings_prev_state:
            self.settings_tree.state(self._settings_prev_state.pop("tree"))
        if self.btn_save and "save" in self._settings_prev_state:
            self.btn_save.config(state=self._settings_prev_state.pop("save"))
        if self.btn_refresh and "refresh" in self._settings_prev_state:
            self.btn_refresh.config(state=self._settings_prev_state.pop("refresh"))

    def set_streaming_lock(self, locked: bool) -> None:
        if locked:
            self._set_settings_edit_enabled(False)
            return
        if not self._settings_saving:
            self._set_settings_edit_enabled(True)

    def _mark_settings_saved(
        self, changes: Sequence[tuple[str, str]], sent_count: int, refresh: bool = False
    ) -> None:
        if refresh:
            try:
                self.app.status.config(
                    text=f"Settings: sent {sent_count} change(s); refreshing $$ for confirmation"
                )
            except Exception as exc:
                logger.exception("Failed to update settings status: %s", exc)
            try:
                self.app._request_settings_dump()
            except Exception as exc:
                logger.exception("Failed to request settings dump: %s", exc)
            return
        for key, _ in changes:
            if key in self._settings_values:
                self._settings_baseline[key] = self._settings_values[key]
                self._update_setting_row_tags(key)
        try:
            self.app.status.config(text=f"Settings: sent {sent_count} change(s)")
        except Exception as exc:
            logger.exception("Failed to update settings status: %s", exc)

    def _render_settings(self) -> None:
        if not self.settings_tree:
            return
        self._settings_items = {}
        for item in self.settings_tree.get_children():
            self.settings_tree.delete(item)
        items: list[tuple[int, str, str, str, str, str]] = []
        self._settings_values = {}
        for key, (value, idx) in self._settings_data.items():
            self._settings_values[key] = value
            info = self.app._grbl_setting_info.get(key, {})
            name = info.get("name", "")
            units = info.get("units", "")
            desc = info.get("desc", "")
            items.append((idx if idx is not None else 9999, key, name, value, units, desc))
        for idx in self.app._grbl_setting_keys:
            key = f"${idx}"
            if key not in self._settings_values:
                self._settings_values[key] = ""
                info = self.app._grbl_setting_info.get(key, {})
                name = info.get("name", "")
                units = info.get("units", "")
                desc = info.get("desc", "")
                items.append((idx, key, name, "", units, desc))
        for _, key, name, value, units, desc in sorted(items):
            item_id = self.settings_tree.insert("", "end", values=(key, name, value, units, desc))
            self._settings_items[key] = item_id
        self._settings_baseline = dict(self._settings_values)
        for key in self._settings_items:
            self._update_setting_row_tags(key)
        self.app.status.config(text=f"Settings: {len(items)} values")

    def _update_rapid_rates(self) -> None:
        try:
            rx = float(self._settings_data.get("$110", ("", None))[0])
            ry = float(self._settings_data.get("$111", ("", None))[0])
            rz = float(self._settings_data.get("$112", ("", None))[0])
            if rx > 0 and ry > 0 and rz > 0:
                self.app._rapid_rates = (rx, ry, rz)
                self.app._rapid_rates_source = "grbl"
                return
        except Exception:
            pass
        self.app._rapid_rates = None
        self.app._rapid_rates_source = None

    def _update_accel_rates(self) -> None:
        try:
            ax = float(self._settings_data.get("$120", ("", None))[0])
            ay = float(self._settings_data.get("$121", ("", None))[0])
            az = float(self._settings_data.get("$122", ("", None))[0])
            if ax > 0 and ay > 0 and az > 0:
                self.app._accel_rates = (ax, ay, az)
                return
        except Exception:
            pass
        self.app._accel_rates = None

    def _render_settings_raw(self, header: str | None = None) -> None:
        if not self.settings_raw_text:
            return
        lines = []
        if header:
            lines.append(header)
        if self._settings_raw_lines:
            lines.extend(self._settings_raw_lines)
        self.settings_raw_text.config(state="normal")
        self.settings_raw_text.delete("1.0", "end")
        self.settings_raw_text.insert("end", "\n".join(lines).strip() + "\n")
        self.settings_raw_text.config(state="disabled")

    def _edit_setting_value(self, event: tk.Event) -> None:
        if self._settings_saving:
            return
        if not self.settings_tree:
            return
        item = self.settings_tree.identify_row(event.y)
        col = self.settings_tree.identify_column(event.x)
        if not item or col != "#3":
            return
        bbox = self.settings_tree.bbox(item, column=col)
        if not bbox:
            return
        x, y, w, h = bbox
        values = self.settings_tree.item(item, "values")
        if not values:
            return
        key = str(values[0])
        current = str(values[2])
        self._commit_pending_setting_edit()
        entry = ttk.Entry(self.settings_tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, current)
        entry.focus_set()
        try:
            idx = int(key[1:]) if key.startswith("$") else None
        except Exception:
            idx = None
        if idx is not None and idx not in GRBL_NON_NUMERIC_SETTINGS:
            attach_numeric_keypad(entry, allow_decimal=True)
        self._settings_entry_meta[entry] = (key, item)
        self._settings_edit_entry = entry

        def commit(_event: tk.Event | None = None) -> None:
            self._commit_pending_setting_edit()

        def cancel(_event: tk.Event | None = None) -> None:
            self._cancel_pending_setting_edit()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _commit_pending_setting_edit(self) -> None:
        if self._settings_saving:
            self._cancel_pending_setting_edit()
            return
        entry = self._settings_edit_entry
        if entry is None:
            return
        key, item = self._settings_entry_meta.pop(entry, (None, None))
        tree = self.settings_tree
        try:
            if key and item and tree:
                new_val = entry.get().strip()
                if new_val:
                    try:
                        idx = int(key[1:]) if key.startswith("$") else None
                    except Exception:
                        idx = None
                    if idx is not None and idx not in GRBL_NON_NUMERIC_SETTINGS:
                        try:
                            val_num = float(new_val)
                        except Exception:
                            messagebox.showwarning("Invalid value", f"Setting {key} must be numeric.")
                            return
                        limits = GRBL_SETTING_LIMITS.get(idx)
                        if limits:
                            lo, hi = limits
                            if lo is not None and val_num < lo:
                                messagebox.showwarning("Out of range", f"Setting {key} must be >= {lo}.")
                                return
                            if hi is not None and val_num > hi:
                                messagebox.showwarning("Out of range", f"Setting {key} must be <= {hi}.")
                                return
                tree.set(item, "value", new_val)
                self._settings_values[key] = new_val
                baseline = self._settings_baseline.get(key, "")
                if new_val == baseline and key in self._settings_edited:
                    self._settings_edited.pop(key, None)
                else:
                    self._settings_edited[key] = new_val
                self._update_setting_row_tags(key)
        finally:
            self._settings_entry_meta.pop(entry, None)
            try:
                entry.destroy()
            except Exception:
                pass
            self._settings_edit_entry = None

    def _cancel_pending_setting_edit(self) -> None:
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        self._settings_entry_meta.pop(entry, None)
        try:
            entry.destroy()
        except Exception:
            pass
        self._settings_edit_entry = None

    def _update_setting_row_tags(self, key: str) -> None:
        if not self.settings_tree:
            return
        item = self._settings_items.get(key)
        if not item:
            return
        current = self._settings_values.get(key, "")
        baseline = self._settings_baseline.get(key, "")
        tags = list(self.settings_tree.item(item, "tags"))
        if current != baseline:
            if "edited" not in tags:
                tags.append("edited")
        else:
            tags = [t for t in tags if t != "edited"]
        self.settings_tree.item(item, tags=tuple(tags))

    def _settings_tooltip_motion(self, event: tk.Event) -> None:
        if not self.settings_tree or not self.settings_tip:
            return
        item = self.settings_tree.identify_row(event.y)
        if not item:
            self._settings_tooltip_hide()
            return
        values = self.settings_tree.item(item, "values")
        if not values:
            self._settings_tooltip_hide()
            return
        key = values[0]
        try:
            idx = int(key[1:])
        except Exception:
            idx = None
        info = self.app._grbl_setting_info.get(key, {})
        desc = info.get("desc", "")
        units = info.get("units", "")
        tooltip = info.get("tooltip", "")
        baseline_val = self._settings_baseline.get(key, "")
        current_val = self._settings_values.get(key, "")
        limits = None
        try:
            limits = GRBL_SETTING_LIMITS.get(int(key[1:]), None)
        except Exception:
            limits = None
        allow_text = False
        try:
            allow_text = int(key[1:]) in GRBL_NON_NUMERIC_SETTINGS
        except Exception:
            allow_text = False
        value_line = (
            f"Pending: {current_val} (last saved: {baseline_val})"
            if current_val != baseline_val
            else f"Value: {baseline_val}"
        )
        parts = []
        if tooltip:
            parts.append(tooltip)
        elif desc:
            parts.append(desc)
        if units:
            parts.append(f"Units: {units}")
        if allow_text:
            parts.append("Allows text values")
        if limits:
            lo, hi = limits
            if lo is not None and hi is not None:
                parts.append(f"Allowed: {lo} .. {hi}")
            elif lo is not None:
                parts.append(f"Allowed: >= {lo}")
            elif hi is not None:
                parts.append(f"Allowed: <= {hi}")
        parts.append(value_line)
        parts.append("Typical: machine-specific")
        self.settings_tip.set_text("\n".join([p for p in parts if p]))
        self.settings_tip._schedule_show()

    def _settings_tooltip_hide(self, _event: Any | None = None) -> None:
        if self.settings_tip:
            self.settings_tip._hide()

