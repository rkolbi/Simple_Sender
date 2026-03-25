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
from decimal import Decimal, InvalidOperation
from tkinter import ttk, messagebox
from typing import Any, Sequence

from simple_sender.ui.widgets_keypad import attach_numeric_keypad
from simple_sender.ui.widgets_tooltips import ToolTip, apply_tooltip, set_tab_tooltip
from simple_sender.ui.widgets_common import attach_log_gcode, set_kb_id
from simple_sender.utils.constants import (
    GRBL_NON_NUMERIC_SETTINGS,
    GRBL_SETTING_LIMITS,
    GRBL_SETTINGS_WRITE_DELAY,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_SETTINGS_VERIFY_RETRY_FAILURE_KEYS = (
    "Controller rejected $$ verification dump",
    "Failed requesting $$ verification dump",
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def parse_setting_line(line: str) -> tuple[str, str, int | None] | None:
    stripped = str(line or "").strip()
    if not (stripped.startswith("$") and "=" in stripped):
        return None
    key, value = stripped.split("=", 1)
    idx = parse_setting_index(key)
    return key, value.strip(), idx


def parse_setting_index(key: str) -> int | None:
    text = str(key or "").strip()
    if not text.startswith("$"):
        return None
    try:
        return int(text[1:])
    except (TypeError, ValueError):
        return None


def parse_setting_float(settings_data: dict[str, tuple[str, int | None]], key: str) -> float | None:
    raw = settings_data.get(key, ("", None))[0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_decimal_setting_value(value: str) -> Decimal | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _settings_values_match_for_verify(key: str, expected: str, actual: str) -> bool:
    expected_text = str(expected or "").strip()
    actual_text = str(actual or "").strip()
    if expected_text == actual_text:
        return True
    idx = parse_setting_index(key)
    if idx is None or idx in GRBL_NON_NUMERIC_SETTINGS:
        return False
    expected_num = _parse_decimal_setting_value(expected_text)
    actual_num = _parse_decimal_setting_value(actual_text)
    if expected_num is None or actual_num is None:
        return False
    return expected_num == actual_num


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
        self._settings_pending_confirmation: dict[str, str] = {}
        self._settings_verify_failed: dict[str, str] = {}

    def _post_ui(self, func, *args, **kwargs) -> None:
        poster = getattr(self.app, "_post_ui_thread", None)
        if callable(poster):
            try:
                poster(func, *args, **kwargs)
                return
            except Exception as exc:
                _log_suppressed("Failed posting settings callback via _post_ui_thread", exc)
        ui_q = getattr(self.app, "ui_q", None)
        if ui_q is not None:
            try:
                ui_q.put(("ui_post", func, args, kwargs))
            except Exception as exc:
                _log_suppressed("Failed posting settings callback via ui_q", exc)

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

        tree_frame = ttk.Frame(stab)
        tree_frame.pack(fill="both", expand=True)
        self.settings_tree = ttk.Treeview(
            tree_frame,
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
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.settings_tree.yview)
        self.settings_tree.configure(yscrollcommand=tree_scroll.set)
        self.settings_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.settings_tree.bind("<Double-1>", self._edit_setting_value)
        self.settings_tree.bind("<Motion>", self._settings_tooltip_motion)
        self.settings_tree.bind("<Leave>", self._settings_tooltip_hide)
        self.settings_tip = ToolTip(self.settings_tree, "")
        self.settings_tree.tag_configure("edited", background="#fff5c2")
        self.settings_tree.tag_configure("verify_failed", background="#ffd9b3")

    def start_capture(self, header: str = "Requesting $$...") -> None:
        self._settings_capture = True
        self._settings_data = {}
        self._settings_edited = {}
        self._settings_raw_lines = []
        self._render_settings_raw(header)

    def handle_line(self, line: str) -> None:
        if not self._settings_capture:
            s = line.strip()
            if parse_setting_line(s):
                self.start_capture("Captured $$ output")
            else:
                return
        s = line.strip()
        if s.startswith("<") and s.endswith(">"):
            return
        low = s.lower()
        if low != "ok" and not low.startswith("error"):
            self._settings_raw_lines.append(s)
        parsed = parse_setting_line(s)
        if parsed:
            key, value, idx = parsed
            self._settings_data[key] = (value, idx)
            return
        if low == "ok":
            self._settings_capture = False
            def _finalize_settings_capture() -> None:
                self._render_settings()
                self._update_rapid_rates()
                self._update_accel_rates()
                if self.app._last_gcode_lines:
                    self.app._update_gcode_stats(self.app._last_gcode_lines)
                self._render_settings_raw()

            after = getattr(self.app, "after", None)
            if callable(after):
                try:
                    after(0, _finalize_settings_capture)
                except Exception:
                    _finalize_settings_capture()
            else:
                _finalize_settings_capture()
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
        if self.app.grbl.is_streaming() or bool(
            getattr(self.app, "_stream_done_pending_idle", False)
        ):
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
        self._settings_pending_confirmation = {}
        self._settings_saving = True
        self._set_settings_edit_enabled(False)

        def worker() -> None:
            sent = 0
            try:
                for key, val in changes:
                    accepted = bool(self.app._send_manual(f"{key}={val}", "settings"))
                    if not accepted:
                        raise RuntimeError(f"Controller rejected {key}={val}")
                    sent += 1
                    time.sleep(GRBL_SETTINGS_WRITE_DELAY)
                wait_for_completion = getattr(self.app.grbl, "wait_for_manual_completion", None)
                if callable(wait_for_completion):
                    timeout_s = max(5.0, min(120.0, float(sent) * 2.0))
                    if not bool(wait_for_completion(timeout_s=timeout_s)):
                        raise TimeoutError("Timed out waiting for settings command queue to drain")
            except Exception as exc:
                message = str(exc)
                self.app.ui_q.put(("log", f"[settings] Save failed: {message}"))
                def finish_failed(error_message: str = message) -> None:
                    self._finish_settings_save_failed(error_message)

                self._post_ui(finish_failed)
                return
            self.app.ui_q.put(("log", f"[settings] Sent {sent} change(s)."))
            def finish_save(sent_count: int = sent) -> None:
                self._finish_settings_save(changes, sent_count)

            self._post_ui(finish_save)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_settings_save(self, changes: Sequence[tuple[str, str]], sent_count: int) -> None:
        self._settings_edited = {}
        self._settings_saving = False
        self._set_settings_edit_enabled(True)
        for key, _value in changes:
            self._settings_verify_failed.pop(str(key), None)
        self._settings_pending_confirmation = {
            str(key): str(value).strip()
            for key, value in changes
            if str(key).strip()
        }
        try:
            self.app.status.config(
                text=f"Settings: sent {sent_count} change(s); verifying with $$"
            )
        except Exception as exc:
            _log_suppressed("Failed updating status while verifying settings save", exc)
        try:
            accepted = self.app._request_settings_dump()
            if accepted is False:
                raise RuntimeError(_SETTINGS_VERIFY_RETRY_FAILURE_KEYS[0])
        except Exception as exc:
            logger.exception("Failed requesting settings verification dump: %s", exc)
            error_message = str(exc).strip() or _SETTINGS_VERIFY_RETRY_FAILURE_KEYS[1]
            if error_message == _SETTINGS_VERIFY_RETRY_FAILURE_KEYS[0]:
                self._finish_settings_save_failed(error_message)
            else:
                self._finish_settings_save_failed(_SETTINGS_VERIFY_RETRY_FAILURE_KEYS[1])

    def _finish_settings_save_failed(self, message: str | None = None) -> None:
        self._settings_saving = False
        self._set_settings_edit_enabled(True)
        if self._settings_pending_confirmation:
            self._restore_retryable_pending_settings(self._settings_pending_confirmation)
            self._settings_pending_confirmation = {}
        if message:
            try:
                self.app.status.config(text=f"Settings save failed: {message}")
            except Exception as exc:
                _log_suppressed("Failed updating status text for GRBL settings save failure", exc)

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
            prev_state = self._settings_prev_state.pop("tree")
            was_disabled = False
            if isinstance(prev_state, (tuple, list, set)):
                was_disabled = "disabled" in prev_state
            elif isinstance(prev_state, str):
                was_disabled = prev_state == "disabled"
            self.settings_tree.state(["disabled"] if was_disabled else ["!disabled"])
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

    def _verify_pending_settings_confirmation(self) -> None:
        pending = dict(self._settings_pending_confirmation)
        if not pending:
            return
        self._settings_pending_confirmation = {}
        mismatches: list[tuple[str, str, str]] = []
        for key, expected in pending.items():
            actual = str(self._settings_values.get(key, "")).strip()
            if not _settings_values_match_for_verify(key, expected, actual):
                mismatches.append((key, expected, actual))
        if mismatches:
            self._restore_retryable_pending_settings(
                {key: expected for key, expected, _actual in mismatches}
            )
            for key, expected, _actual in mismatches:
                self._settings_verify_failed[key] = expected
                self._settings_baseline[key] = expected
                self._update_setting_row_tags(key)
            summary = ", ".join(
                f"{key} expected {expected!r} got {actual!r}"
                for key, expected, actual in mismatches[:3]
            )
            if len(mismatches) > 3:
                summary = f"{summary}, ..."
            self.app.ui_q.put(
                (
                    "log",
                    f"[settings] Verification failed for {len(mismatches)} setting(s): {summary}",
                )
            )
            try:
                self.app.status.config(
                    text=f"Settings verification failed ({len(mismatches)} mismatch(es))"
                )
            except Exception as exc:
                _log_suppressed("Failed updating status after settings verification failure", exc)
            return
        for key, expected in pending.items():
            actual = str(self._settings_values.get(key, "")).strip()
            self._settings_verify_failed.pop(key, None)
            self._settings_edited.pop(key, None)
            self._settings_baseline[key] = actual if actual != "" else expected
            self._update_setting_row_tags(key)
        self.app.ui_q.put(
            ("log", f"[settings] Confirmed {len(pending)} change(s) from $$ response.")
        )
        try:
            self.app.status.config(text=f"Settings: confirmed {len(pending)} change(s)")
        except Exception as exc:
            _log_suppressed("Failed updating status after settings verification success", exc)

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
        for key, expected in self._settings_verify_failed.items():
            if key in self._settings_baseline:
                self._settings_baseline[key] = expected
        for key in self._settings_items:
            self._update_setting_row_tags(key)
        self.app.status.config(text=f"Settings: {len(items)} values")
        self._verify_pending_settings_confirmation()

    def _restore_retryable_pending_settings(self, pending: dict[str, str]) -> None:
        for key, value in pending.items():
            setting_key = str(key).strip()
            if not setting_key:
                continue
            attempted = str(value).strip()
            self._settings_edited[setting_key] = attempted
            if setting_key in self._settings_items:
                self._update_setting_row_tags(setting_key)

    def _update_rapid_rates(self) -> None:
        rx = parse_setting_float(self._settings_data, "$110")
        ry = parse_setting_float(self._settings_data, "$111")
        rz = parse_setting_float(self._settings_data, "$112")
        if rx is not None and ry is not None and rz is not None and rx > 0 and ry > 0 and rz > 0:
            self.app._rapid_rates = (rx, ry, rz)
            self.app._rapid_rates_source = "grbl"
            return
        self.app._rapid_rates = None
        self.app._rapid_rates_source = None

    def _update_accel_rates(self) -> None:
        ax = parse_setting_float(self._settings_data, "$120")
        ay = parse_setting_float(self._settings_data, "$121")
        az = parse_setting_float(self._settings_data, "$122")
        if ax is not None and ay is not None and az is not None and ax > 0 and ay > 0 and az > 0:
            self.app._accel_rates = (ax, ay, az)
            return
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
        idx = parse_setting_index(key)
        if idx is not None and idx not in GRBL_NON_NUMERIC_SETTINGS:
            attach_numeric_keypad(entry, allow_decimal=True)
        self._settings_entry_meta[entry] = (key, item)
        self._settings_edit_entry = entry

        def commit(_event: tk.Event | None = None) -> None:
            self._commit_pending_setting_edit()

        def commit_focus_out(_event: tk.Event | None = None) -> None:
            if self._focus_out_targets_numeric_keypad(entry):
                return
            self._commit_pending_setting_edit()

        def cancel(_event: tk.Event | None = None) -> None:
            self._cancel_pending_setting_edit()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit_focus_out)
        entry.bind("<Escape>", cancel)

    def _focus_out_targets_numeric_keypad(self, entry: ttk.Entry) -> bool:
        dlg = getattr(entry, "_numeric_keypad_dialog", None)
        if dlg is None:
            return False
        try:
            if not bool(dlg.winfo_exists()):
                return False
        except Exception:
            return False
        focus_widget: Any | None
        try:
            focus_widget = entry.focus_get()
        except Exception:
            focus_widget = None
        if focus_widget is None:
            return True
        widget: Any | None = focus_widget
        while widget is not None:
            if widget == dlg:
                return True
            widget = getattr(widget, "master", None)
        try:
            return bool(focus_widget.winfo_toplevel() == dlg)
        except Exception:
            return False

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
            if key is None or item is None or tree is None:
                return
            new_val = entry.get().strip()
            idx = parse_setting_index(key) if new_val else None
            if key in self._settings_verify_failed:
                self._settings_verify_failed.pop(key, None)
                self._settings_baseline[key] = self._settings_values.get(key, "")
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
            except Exception as exc:
                _log_suppressed("Failed destroying inline GRBL setting edit entry after commit", exc)
            self._settings_edit_entry = None

    def _cancel_pending_setting_edit(self) -> None:
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        self._settings_entry_meta.pop(entry, None)
        try:
            entry.destroy()
        except Exception as exc:
            _log_suppressed("Failed destroying inline GRBL setting edit entry on cancel", exc)
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
        if key in self._settings_verify_failed:
            if "verify_failed" not in tags:
                tags.append("verify_failed")
        else:
            tags = [t for t in tags if t != "verify_failed"]
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
        idx = parse_setting_index(str(key))
        info = self.app._grbl_setting_info.get(key, {})
        desc = info.get("desc", "")
        units = info.get("units", "")
        tooltip = info.get("tooltip", "")
        baseline_val = self._settings_baseline.get(key, "")
        current_val = self._settings_values.get(key, "")
        verify_failed_expected = self._settings_verify_failed.get(key)
        limits = GRBL_SETTING_LIMITS.get(idx, None) if idx is not None else None
        allow_text = bool(idx is not None and idx in GRBL_NON_NUMERIC_SETTINGS)
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
        if verify_failed_expected is not None:
            parts.append(f"Verification failed: controller did not keep attempted value {verify_failed_expected}")
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

