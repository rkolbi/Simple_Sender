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
import os
import time
from typing import Callable
import tkinter as tk
from collections import deque

from simple_sender.utils.constants import MAX_CONSOLE_LINES
from simple_sender.utils.constants import CONSOLE_PENDING_BATCH_MAX
from simple_sender.utils.constants import CONSOLE_MAX_BUFFER_BYTES
from simple_sender.types import AppProtocol, GcodeViewLike
from simple_sender.ui.file_info_tab import ssmeta_toolpaths, ssmeta_tools
from simple_sender.ui.loaded_job_metadata_state import get_loaded_job_metadata_state
from simple_sender.ui.stream_completion import should_defer_completion

logger = logging.getLogger(__name__)
_JOB_TELEMETRY_HEARTBEAT_INTERVAL_MS = 30 * 60 * 1000
_JOB_TELEMETRY_MILESTONE_STEP_PCT = 10
_JOB_TELEMETRY_MAX_MILESTONE_PCT = 90

AfterId = str | int
ConsoleEntry = tuple[str, str | None]
ConsoleEntryLike = ConsoleEntry | str

class StreamingController:
    """Manage streaming-related UI updates and console logging."""

    def __init__(self, app: AppProtocol, *, timestamp: Callable[[], str] | None = None) -> None:
        self.app = app
        self._timestamp = timestamp or (lambda: time.strftime("%H:%M:%S"))
        self.console: tk.Text | None = None
        self.gview: GcodeViewLike | None = None
        self.progress_pct: tk.IntVar | None = None
        self.progress_text: tk.StringVar | None = None
        self.buffer_fill: tk.StringVar | None = None
        self.buffer_fill_pct: tk.IntVar | None = None
        self.throughput_var: tk.StringVar | None = None
        self._console_lines: deque[ConsoleEntry] = deque(maxlen=MAX_CONSOLE_LINES)
        self._console_bytes = 0
        self._console_filter: str | None = None
        self._pending_console_entries: list[ConsoleEntry] = []
        self._pending_console_trim: int = 0
        self._console_after_id: AfterId | None = None
        self._console_render_pending: bool = False
        self._pending_marks_after_id: AfterId | None = None
        self._pending_sent_index: int | None = None
        self._pending_acked_index: int | None = None
        self._pending_progress: tuple[int, int] | None = None
        self._pending_progress_bytes: tuple[int, int] | None = None
        self._pending_buffer: tuple[int, int, int] | None = None
        self._progress_after_id: AfterId | None = None
        self._buffer_after_id: AfterId | None = None
        self._last_progress_pct: int | None = None
        self._last_progress_text: str | None = None
        self._last_buffer_fill_text: str | None = None
        self._last_buffer_fill_pct: int | None = None
        self._last_throughput_text: str | None = None
        self._pending_log_rx_lines: list[str] = []
        self._log_rx_after_id: AfterId | None = None
        self._log_rx_flush_interval_ms = max(
            100, int(getattr(self.app, "_log_rx_flush_interval_ms", 125) or 125)
        )
        self._manual_motion_hidden_status_log_interval_s = max(
            0.25,
            float(
                getattr(
                    self.app,
                    "_manual_motion_hidden_status_log_interval_s",
                    0.75,
                )
                or 0.75
            ),
        )
        self._manual_motion_hidden_status_log_last_ts = 0.0
        self._job_telemetry_after_id: AfterId | None = None
        self._job_telemetry_token = 0
        self._job_lifecycle_run_type: str | None = None
        self._job_next_milestone_pct: int | None = None
        self._job_last_authoritative_progress_pct: float | None = None
        self._job_telemetry_scheduling = False

    def _full_progress_allowed(self) -> bool:
        stream_state = str(getattr(self.app, "_stream_state", "") or "").strip().lower()
        done_pending_idle = bool(getattr(self.app, "_stream_done_pending_idle", False))
        return stream_state == "done" and not done_pending_idle

    @staticmethod
    def _floor_percent_1dp(pct_f: float) -> float:
        # Floor one-decimal display to prevent rounding 99.9x -> 100.0 before completion.
        bounded = max(0.0, min(100.0, float(pct_f)))
        return float(int(bounded * 10.0)) / 10.0

    def _manual_motion_active(self) -> bool:
        if bool(getattr(self.app, "_stream_done_pending_idle", False)):
            return False
        stream_state = str(getattr(self.app, "_stream_state", "") or "").strip().lower()
        if stream_state in {"running", "paused"}:
            return False
        if bool(getattr(self.app, "_active_joystick_hold_binding", None)):
            return True
        state_text = str(getattr(self.app, "_machine_state_text", "") or "").strip().lower()
        return state_text.startswith(("jog", "hold"))

    def _manual_motion_console_throttle_active(self) -> bool:
        return bool(self._manual_motion_active())

    def _console_flush_interval_ms(self) -> int:
        base = max(1, int(getattr(self.app, "_ui_throttle_ms", 100) or 100))
        if self._manual_motion_console_throttle_active():
            manual_interval = int(getattr(self.app, "_manual_motion_console_flush_ms", 300) or 300)
            manual_interval = max(250, manual_interval)
            return max(base, manual_interval)
        return base

    def attach_widgets(
        self,
        console: tk.Text,
        gview: GcodeViewLike,
        progress_pct: tk.IntVar,
        buffer_fill: tk.StringVar,
        buffer_fill_pct: tk.IntVar,
        throughput_var: tk.StringVar,
        progress_text: tk.StringVar | None = None,
    ) -> None:
        """Attach UI widgets used for streaming updates."""
        self.console = console
        self.gview = gview
        self.progress_pct = progress_pct
        self.progress_text = progress_text or getattr(self.app, "progress_text", None)
        self.buffer_fill = buffer_fill
        self.buffer_fill_pct = buffer_fill_pct
        self.throughput_var = throughput_var

    def _console_tag_for_line(self, s: str) -> str | None:
        u = s.upper()
        if "ALARM" in u:
            return "console_alarm"
        if "ERROR" in u:
            return "console_error"
        stripped = s.strip()
        if stripped.upper() in ("<< OK", "OK"):
            return "console_ok"
        if stripped.startswith(">>"):
            return "console_tx"
        if stripped.startswith("<<") and "<" in stripped and ">" in stripped:
            return "console_status"
        return None

    def _is_position_line(self, s: str) -> bool:
        u = s.upper()
        return ("WPOS:" in u) or ("MPOS:" in u)

    def _is_status_line(self, s: str) -> bool:
        stripped = s.strip()
        return (
            stripped.startswith("<< <")
            or (stripped.startswith("<") and stripped.endswith(">"))
            or ("<" in stripped and ">" in stripped)
        )

    def _should_skip_console_entry_for_toggles(self, entry: ConsoleEntryLike) -> bool:
        if isinstance(entry, tuple):
            s, _ = entry
        else:
            s = str(entry)
        enabled = bool(self.app.console_positions_enabled.get())
        if not enabled and self._is_position_line(s):
            return True
        upper = s.upper()
        if not enabled and self._is_status_line(s):
            if ("ALARM" not in upper) and ("ERROR" not in upper):
                return True
        return False

    def _console_filter_match(self, entry: ConsoleEntryLike, for_save: bool = False) -> bool:
        if isinstance(entry, tuple):
            s, _ = entry
        else:
            s = str(entry)
        upper = s.upper()
        if self._console_filter == "alarms" and "ALARM" not in upper:
            return False
        if self._console_filter == "errors" and "ERROR" not in upper:
            return False
        if for_save and self._is_position_line(s):
            return False
        is_pos = self._is_position_line(s)
        enabled = bool(self.app.console_positions_enabled.get())
        is_status = self._is_status_line(s)
        if (not for_save) and not enabled:
            if is_pos:
                return False
            if is_status and ("ALARM" not in upper) and ("ERROR" not in upper):
                return False
        return True

    def _should_suppress_rx_log(self, raw: str) -> bool:
        if not bool(self.app.performance_mode.get()):
            return False
        if self.app._stream_state != "running":
            return False
        upper = raw.upper()
        if ("ALARM" in upper) or ("ERROR" in upper):
            return False
        if "[MSG" in upper or "RESET TO CONTINUE" in upper:
            return False
        return True

    def log(self, s: str, tag: str | None = None) -> None:
        """Record a console entry and render it if needed."""
        if tag is None:
            tag = self._console_tag_for_line(s)
        entry: ConsoleEntry = (s, tag)
        if self._should_skip_console_entry_for_toggles(entry):
            return
        entry_bytes = len(s.encode("utf-8", errors="ignore")) + 1
        dropped = 1 if len(self._console_lines) >= MAX_CONSOLE_LINES else 0
        if dropped and self._console_lines:
            self._console_bytes = max(0, self._console_bytes - self._entry_bytes(self._console_lines[0]))
        self._console_lines.append(entry)
        self._console_bytes += entry_bytes
        byte_trimmed = self._trim_console_bytes_if_needed()
        if dropped:
            if self._console_filter is not None:
                if bool(self.app.performance_mode.get()):
                    self._queue_console_render()
                    return
                self._render_console()
                return
            if bool(self.app.performance_mode.get()) or self._manual_motion_console_throttle_active():
                self._pending_console_trim += dropped
            else:
                self._trim_console_widget(dropped)
        if byte_trimmed > 0:
            if self._console_filter is not None:
                if bool(self.app.performance_mode.get()):
                    self._queue_console_render()
                    return
                self._render_console()
                return
            if bool(self.app.performance_mode.get()) or self._manual_motion_console_throttle_active():
                self._pending_console_trim += byte_trimmed
            else:
                self._trim_console_widget(byte_trimmed)
        if not self._console_filter_match(entry):
            return
        if bool(self.app.performance_mode.get()) or self._manual_motion_console_throttle_active():
            self._pending_console_entries.append(entry)
            pending_limit = int(CONSOLE_PENDING_BATCH_MAX)
            if self._manual_motion_console_throttle_active():
                pending_limit = max(pending_limit, 2000)
            if len(self._pending_console_entries) > pending_limit:
                # Bound pending memory growth during heavy logging bursts.
                drop = len(self._pending_console_entries) - pending_limit
                if drop > 0:
                    self._pending_console_entries = self._pending_console_entries[drop:]
                    self._pending_console_trim += drop
            self._schedule_console_flush()
            return
        self._append_to_console(entry)

    def _append_to_console(self, entry: ConsoleEntry) -> None:
        if not self.console:
            return
        line, tag = entry
        self.console.config(state="normal")
        if tag:
            self.console.insert("end", line + "\n", (tag,))
        else:
            self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _queue_console_render(self) -> None:
        self._console_render_pending = True
        self._schedule_console_flush()

    def _schedule_console_flush(self) -> None:
        if self._console_after_id is not None:
            return
        self._console_after_id = self.app.after(
            self._console_flush_interval_ms(),
            self._flush_console_updates,
        )

    def _schedule_log_rx_flush(self) -> None:
        if self._log_rx_after_id is not None:
            return
        self._log_rx_after_id = self.app.after(
            int(self._log_rx_flush_interval_ms),
            self._flush_log_rx_updates,
        )

    def _flush_log_rx_updates(self) -> None:
        self._log_rx_after_id = None
        pending = self._pending_log_rx_lines
        self._pending_log_rx_lines = []
        if not pending:
            return
        for raw in pending:
            self.log(f"<< {raw}", self._console_tag_for_line(raw))

    @staticmethod
    def _is_critical_rx_line(raw: str) -> bool:
        upper = str(raw or "").upper()
        return (
            "ALARM" in upper
            or "ERROR" in upper
            or upper.startswith("GRBL")
            or "[MSG" in upper
        )

    def _flush_console_updates(self) -> None:
        self._console_after_id = None
        active_tab = str(getattr(self.app, "_active_tab_label", "") or "").strip().lower()
        if self._manual_motion_console_throttle_active() and active_tab not in {"console"}:
            if self._pending_console_entries or self._pending_console_trim > 0 or self._console_render_pending:
                self._schedule_console_flush()
            return
        if self._console_render_pending:
            self._console_render_pending = False
            self._pending_console_entries = []
            self._pending_console_trim = 0
            self._render_console()
            return
        if (not self._pending_console_entries) and (self._pending_console_trim <= 0):
            return
        if not self.console:
            return
        self.console.config(state="normal")
        if self._pending_console_trim > 0:
            self._trim_console_widget_unlocked(self._pending_console_trim)
            self._pending_console_trim = 0
        pending_entries = self._pending_console_entries
        self._pending_console_entries = []
        if self._manual_motion_console_throttle_active() and len(pending_entries) > 200:
            self._insert_entries_unlocked(pending_entries[:200])
            self._pending_console_entries = pending_entries[200:]
        else:
            self._insert_entries_unlocked(pending_entries)
        self.console.see("end")
        self.console.config(state="disabled")
        if self._pending_console_entries or self._pending_console_trim > 0:
            self._schedule_console_flush()

    def _render_console(self) -> None:
        if not self.console:
            return
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        filtered_entries: list[ConsoleEntry] = [
            (line, tag)
            for line, tag in self._console_lines
            if self._console_filter_match((line, tag))
        ]
        self._insert_entries_unlocked(filtered_entries)
        self.console.see("end")
        self.console.config(state="disabled")

    def _insert_entries_unlocked(self, entries: list[ConsoleEntry]) -> None:
        """Insert console entries in tag-runs to reduce Tk insert call volume."""
        console = self.console
        if console is None or not entries:
            return
        run_tag: str | None = None
        run_lines: list[str] = []

        def flush_run() -> None:
            nonlocal run_tag, run_lines
            if not run_lines:
                run_tag = None
                return
            text = "\n".join(run_lines) + "\n"
            if run_tag:
                console.insert("end", text, (run_tag,))
            else:
                console.insert("end", text)
            run_tag = None
            run_lines = []

        for line, tag in entries:
            if run_lines and tag != run_tag:
                flush_run()
            run_tag = tag
            run_lines.append(line)
        flush_run()

    def _trim_console_widget(self, count: int) -> None:
        if count <= 0 or not self.console:
            return
        self.console.config(state="normal")
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def _trim_console_widget_unlocked(self, count: int) -> None:
        if count <= 0 or not self.console:
            return
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")

    def set_console_filter(self, mode: str | None) -> None:
        """Update the console filter and re-render."""
        self._console_filter = mode
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        self._render_console()

    def clear_console(self) -> None:
        """Clear all console content and pending entries."""
        self._console_lines.clear()
        self._console_bytes = 0
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        if self.console:
            self.console.config(state="normal")
            self.console.delete("1.0", "end")
            self.console.config(state="disabled")

    def get_console_lines(self) -> list[ConsoleEntry]:
        """Return a copy of the raw console lines."""
        return list(self._console_lines)

    def matches_filter(self, entry: ConsoleEntryLike, for_save: bool = False) -> bool:
        """Check whether a console entry should be shown for the current filter."""
        return self._console_filter_match(entry, for_save=for_save)

    def is_position_line(self, s: str) -> bool:
        """Return True if the line looks like a position report."""
        return self._is_position_line(s)

    def flush_console(self) -> None:
        """Flush any pending console updates."""
        self._flush_console_updates()

    def render_console(self) -> None:
        """Force a full console re-render."""
        self._render_console()

    def bind_button_logging(self) -> None:
        """Attach GUI bindings for button logging."""
        self.app.bind_class("TButton", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Button", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Canvas", "<Button-1>", self._on_button_press, add="+")

    def _on_button_press(self, event: tk.Event) -> None:
        w = event.widget
        if not hasattr(w, "winfo_name"):
            return
        if isinstance(w, tk.Canvas) and not getattr(w, "_log_button", False):
            return
        try:
            if w.cget("state") == "disabled":
                return
        except Exception as exc:
            logger.debug("Failed to read widget state for logging: %s", exc)
        if not bool(self.app.gui_logging_enabled.get()):
            return
        label = ""
        try:
            label = w.cget("text")
        except Exception as exc:
            logger.debug("Failed to read widget text for logging: %s", exc)
        if not label:
            label = w.winfo_name()
        tip = ""
        try:
            tip = getattr(w, "_tooltip_text", "")
        except Exception:
            tip = ""
        gcode = ""
        try:
            getter = getattr(w, "_log_gcode_get", None)
            if callable(getter):
                gcode = getter()
            elif isinstance(getter, str):
                gcode = getter
        except Exception:
            gcode = ""
        ts = self._timestamp()
        if tip and gcode:
            self.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self.log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self.log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self.log(f"[{ts}] Button: {label}")

    def _schedule_gcode_mark_flush(self) -> None:
        if self._pending_marks_after_id is not None:
            return
        self._pending_marks_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_gcode_marks)

    def _flush_gcode_marks(self) -> None:
        self._pending_marks_after_id = None
        sent_idx = self._pending_sent_index
        acked_idx = self._pending_acked_index
        self._pending_sent_index = None
        self._pending_acked_index = None
        if sent_idx is not None:
            self.app._last_sent_index = sent_idx
        if acked_idx is not None:
            self.app._last_acked_index = acked_idx
            if sent_idx is None and acked_idx > self.app._last_sent_index:
                self.app._last_sent_index = acked_idx

    def _schedule_progress_flush(self) -> None:
        if self._progress_after_id is not None:
            return
        self._progress_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_progress)

    def _line_progress_display_pct(
        self,
        done_total: tuple[int, int] | None,
        *,
        force_done_clamp: bool,
    ) -> float | None:
        done = 0
        total = 0
        if done_total is not None:
            done, total = done_total
        else:
            try:
                total = int(getattr(self.app, "_gcode_executable_lines", 0) or 0)
            except Exception:
                total = 0
            if total <= 0:
                try:
                    total = int(getattr(self.app, "_gcode_total_lines", 0) or 0)
                except Exception:
                    total = 0
            try:
                done = int(getattr(self.app, "_last_acked_index", -1) or -1) + 1
            except Exception:
                done = 0
        total = max(0, int(total))
        if total <= 0:
            return None
        done = max(0, min(total, int(done)))
        pct_f = max(0.0, min(100.0, (float(done) / float(total)) * 100.0))
        if force_done_clamp and total > 0:
            pct_f = 100.0
        return pct_f

    @staticmethod
    def _byte_progress_display_pct(
        acked_offset: int,
        file_size_bytes: int,
        *,
        force_done_clamp: bool,
    ) -> float | None:
        acked_offset = max(0, int(acked_offset))
        file_size_bytes = max(0, int(file_size_bytes))
        if file_size_bytes <= 0:
            return None
        acked_offset = min(acked_offset, file_size_bytes)
        if force_done_clamp:
            acked_offset = int(file_size_bytes)
        return max(
            0.0,
            min(100.0, (float(acked_offset) / float(file_size_bytes)) * 100.0),
        )

    def _flush_progress(self) -> None:
        self._progress_after_id = None
        if (not self._pending_progress) and (not self._pending_progress_bytes):
            return
        done_total = self._pending_progress
        self._pending_progress = None
        byte_progress = self._pending_progress_bytes
        self._pending_progress_bytes = None
        stream_state = str(getattr(self.app, "_stream_state", "") or "").strip().lower()
        done_pending_idle = bool(getattr(self.app, "_stream_done_pending_idle", False))
        force_done_clamp = stream_state == "done" and not done_pending_idle

        has_file_size = False
        if byte_progress is not None:
            acked_offset, file_size_bytes = byte_progress
            acked_offset = max(0, int(acked_offset))
            file_size_bytes = max(0, int(file_size_bytes))
            setattr(self.app, "_stream_acked_byte_offset", int(acked_offset))
            setattr(self.app, "_stream_progress_file_size_bytes", int(file_size_bytes))
            if file_size_bytes > 0:
                has_file_size = True
                acked_offset = min(acked_offset, file_size_bytes)
                if force_done_clamp:
                    acked_offset = int(file_size_bytes)
                setattr(self.app, "_stream_acked_byte_offset", int(acked_offset))
                pct_f = self._byte_progress_display_pct(
                    acked_offset,
                    file_size_bytes,
                    force_done_clamp=force_done_clamp,
                )
                if pct_f is not None:
                    setattr(self.app, "_stream_progress_pct", float(pct_f))

        if done_total is not None:
            done, total = done_total
            defer_completion = should_defer_completion(
                self.app, done, total, now_ts=time.time()
            )
            if done and total and not defer_completion:
                self.app._update_live_estimate(done, total)
            if not defer_completion:
                self.app._maybe_notify_job_completion(done, total)

        prefer_line_progress = bool(
            getattr(self.app, "_gcode_executable_lines_known", False)
        ) or not has_file_size
        display_pct = None
        visible = False
        if prefer_line_progress:
            display_pct = self._line_progress_display_pct(
                done_total,
                force_done_clamp=force_done_clamp,
            )
            visible = display_pct is not None
        if display_pct is None and byte_progress is not None:
            acked_offset, file_size_bytes = byte_progress
            display_pct = self._byte_progress_display_pct(
                acked_offset,
                file_size_bytes,
                force_done_clamp=force_done_clamp,
            )
            visible = display_pct is not None
        elif display_pct is None and has_file_size:
            display_pct = self._byte_progress_display_pct(
                int(getattr(self.app, "_stream_acked_byte_offset", 0) or 0),
                int(getattr(self.app, "_stream_progress_file_size_bytes", 0) or 0),
                force_done_clamp=force_done_clamp,
            )
            visible = display_pct is not None
        if display_pct is None and not prefer_line_progress:
            display_pct = self._line_progress_display_pct(
                done_total,
                force_done_clamp=force_done_clamp,
            )
            visible = display_pct is not None
        setattr(self.app, "_stream_progress_pct", float(display_pct or 0.0))
        self._set_progress_display(display_pct, visible=visible)
        self._maybe_log_job_progress_milestones(
            done_total=done_total,
            byte_progress=byte_progress,
        )

    def _set_progress_display(self, pct_f: float | None, *, visible: bool) -> None:
        set_visible = getattr(self.app, "_set_stream_progress_visible", None)
        if callable(set_visible):
            try:
                set_visible(bool(visible))
            except Exception as exc:
                logger.debug("Failed toggling stream progress visibility: %s", exc, exc_info=exc)
        if not visible or pct_f is None:
            if self.progress_pct and self._last_progress_pct != 0:
                self.progress_pct.set(0)
                self._last_progress_pct = 0
            if self.progress_text and self._last_progress_text != "":
                self.progress_text.set("")
                self._last_progress_text = ""
            return
        pct_f = max(0.0, min(100.0, float(pct_f)))
        if not self._full_progress_allowed():
            pct_f = min(pct_f, 99.9)
        pct_f_display = self._floor_percent_1dp(pct_f)
        pct_i = int(pct_f_display)
        text = f"{pct_f_display:.1f}%"
        if self.progress_pct and self._last_progress_pct != pct_i:
            self.progress_pct.set(pct_i)
            self._last_progress_pct = pct_i
        if self.progress_text and self._last_progress_text != text:
            self.progress_text.set(text)
            self._last_progress_text = text

    def _schedule_buffer_flush(self) -> None:
        if self._buffer_after_id is not None:
            return
        self._buffer_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_buffer_fill)

    def _flush_buffer_fill(self) -> None:
        self._buffer_after_id = None
        if not self._pending_buffer:
            return
        pct, used, window = self._pending_buffer
        self._pending_buffer = None
        text = f"Buffer: {pct}% ({used}/{window})"
        if self.buffer_fill:
            if self._last_buffer_fill_text != text:
                self.buffer_fill.set(text)
                self._last_buffer_fill_text = text
        if self.buffer_fill_pct:
            if self._last_buffer_fill_pct != pct:
                self.buffer_fill_pct.set(pct)
                self._last_buffer_fill_pct = pct

    def clear_pending_ui_updates(self) -> None:
        """Cancel pending UI updates when switching modes/closing."""
        for attr in (
            "_pending_marks_after_id",
            "_progress_after_id",
            "_buffer_after_id",
            "_console_after_id",
            "_log_rx_after_id",
        ):
            val = getattr(self, attr, None)
            if val is None:
                continue
            try:
                self.app.after_cancel(val)
            except Exception as exc:
                logger.debug("Failed canceling pending UI callback: %s", exc, exc_info=exc)
            setattr(self, attr, None)
        self._pending_marks_after_id = None
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_progress_bytes = None
        self._pending_buffer = None
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        self._pending_log_rx_lines = []
        self._last_buffer_fill_text = None
        self._last_buffer_fill_pct = None
        self._last_throughput_text = None
        self._last_progress_pct = None
        self._last_progress_text = None

    @staticmethod
    def _entry_bytes(entry: ConsoleEntry) -> int:
        line, _tag = entry
        return len(line.encode("utf-8", errors="ignore")) + 1

    def _trim_console_bytes_if_needed(self) -> int:
        budget = int(CONSOLE_MAX_BUFFER_BYTES)
        if budget <= 0:
            return 0
        removed = 0
        while self._console_lines and self._console_bytes > budget:
            oldest = self._console_lines.popleft()
            self._console_bytes = max(0, self._console_bytes - self._entry_bytes(oldest))
            removed += 1
        return removed

    def handle_log_rx(self, raw: str) -> None:
        """Log a line received from GRBL."""
        if self._should_suppress_rx_log(raw):
            return
        if (
            (not self._is_critical_rx_line(raw))
            and self._is_status_line(raw)
            and self._manual_motion_console_throttle_active()
            and str(getattr(self.app, "_active_tab_label", "") or "").strip().lower() not in {"console"}
        ):
            now = time.monotonic()
            if (
                now - float(self._manual_motion_hidden_status_log_last_ts)
                < float(self._manual_motion_hidden_status_log_interval_s)
            ):
                return
            self._manual_motion_hidden_status_log_last_ts = now
        self._pending_log_rx_lines.append(str(raw))
        if self._is_critical_rx_line(raw):
            self._flush_log_rx_updates()
            return
        self._schedule_log_rx_flush()

    def handle_log_tx(self, message: str) -> None:
        """Log a line sent to GRBL."""
        self.log(f">> {message}")

    def handle_log(self, message: str) -> None:
        """Log an informational message."""
        self.log(message)

    @staticmethod
    def _bool_var(value) -> bool:
        getter = getattr(value, "get", None)
        if callable(getter):
            try:
                return bool(getter())
            except Exception:
                return False
        return bool(value)

    @staticmethod
    def _format_bytes_compact(size_bytes: int) -> str:
        value = max(0, int(size_bytes or 0))
        units = ("B", "KB", "MB", "GB")
        size = float(value)
        unit = units[0]
        for candidate in units[1:]:
            if size < 1024.0:
                break
            size /= 1024.0
            unit = candidate
        if unit == "B":
            return f"{int(size)} {unit}"
        return f"{size:.1f} {unit}"

    @staticmethod
    def _format_elapsed_compact(total_seconds: float) -> str:
        seconds = max(0, int(round(float(total_seconds or 0.0))))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _job_name(self) -> str:
        state = get_loaded_job_metadata_state(self.app)
        path = str(state.last_gcode_path or "").strip()
        if path:
            return os.path.basename(path)
        return str(getattr(getattr(self.app, "grbl", None), "_gcode_name", "") or "").strip()

    def _job_metadata_lists(self) -> tuple[list[str], list[str]]:
        state = get_loaded_job_metadata_state(self.app)
        ssmeta = state.ssmeta
        if not bool(state.ssmeta_present) or not isinstance(ssmeta, dict):
            return ([], [])
        ssmeta_map = dict(ssmeta)
        return (ssmeta_toolpaths(ssmeta_map), ssmeta_tools(ssmeta_map))

    def _runtime_metrics_snapshot(self) -> dict:
        metrics_getter = getattr(getattr(self.app, "grbl", None), "get_runtime_metrics", None)
        if not callable(metrics_getter):
            return {}
        try:
            metrics = metrics_getter()
        except Exception as exc:
            logger.debug("Failed collecting runtime metrics for job lifecycle logging: %s", exc)
            return {}
        return dict(metrics) if isinstance(metrics, dict) else {}

    def _performance_metrics_snapshot(self) -> dict:
        perf_monitor = getattr(self.app, "_perf_monitor", None)
        snapshot_getter = getattr(perf_monitor, "runtime_snapshot", None)
        if not callable(snapshot_getter):
            return {}
        try:
            snapshot = snapshot_getter()
        except Exception as exc:
            logger.debug("Failed collecting perf metrics for job telemetry logging: %s", exc)
            return {}
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def _resolved_acked_index(self, metrics: dict) -> int:
        current = int(metrics.get("live_gcode_current_acked_index", -1) or -1)
        legacy = int(getattr(self.app, "_last_acked_index", -1) or -1)
        return max(current, legacy)

    def _job_line_progress_known(self) -> bool:
        state = get_loaded_job_metadata_state(self.app)
        executable_total = int(state.executable_lines or 0)
        if executable_total > 0:
            return bool(state.executable_lines_known)
        total_lines = int(state.total_lines or 0)
        if total_lines > 0:
            return bool(state.total_lines_known)
        return False

    def _job_progress_pct_from_start_index(self, start_index: int) -> float | None:
        state = get_loaded_job_metadata_state(self.app)
        total = int(
            state.executable_lines
            or state.total_lines
            or 0
        )
        if total <= 0 or int(start_index) <= 0:
            return None
        done = max(0, min(total, int(start_index)))
        return max(0.0, min(100.0, (float(done) / float(total)) * 100.0))

    def _resolve_job_progress_pct(
        self,
        *,
        metrics: dict | None = None,
        done_total: tuple[int, int] | None = None,
        byte_progress: tuple[int, int] | None = None,
        start_index: int | None = None,
    ) -> float | None:
        runtime_metrics = metrics or {}
        line_pct = self._line_progress_display_pct(
            done_total,
            force_done_clamp=False,
        )
        line_known = self._job_line_progress_known()
        if byte_progress is not None:
            acked_offset, file_size_bytes = byte_progress
            byte_pct = self._byte_progress_display_pct(
                acked_offset,
                file_size_bytes,
                force_done_clamp=False,
            )
        else:
            metadata = get_loaded_job_metadata_state(self.app)
            file_size = int(
                runtime_metrics.get("stream_file_size_bytes", 0)
                or getattr(self.app, "_stream_progress_file_size_bytes", 0)
                or metadata.file_size_bytes
                or 0
            )
            acked_bytes = int(
                runtime_metrics.get(
                    "acked_byte_offset",
                    getattr(self.app, "_stream_acked_byte_offset", 0),
                )
                or 0
            )
            byte_pct = self._byte_progress_display_pct(
                acked_bytes,
                file_size,
                force_done_clamp=False,
            )
        progress_pct = None
        if line_pct is not None and line_known:
            progress_pct = line_pct
        elif byte_pct is not None:
            progress_pct = byte_pct
        elif line_pct is not None:
            progress_pct = line_pct
        else:
            progress_metric = runtime_metrics.get(
                "stream_progress_pct",
                getattr(self.app, "_stream_progress_pct", None),
            )
            if progress_metric is not None:
                try:
                    progress_pct = float(progress_metric)
                except Exception:
                    progress_pct = None
        resume_pct = (
            self._job_progress_pct_from_start_index(int(start_index))
            if start_index is not None
            else None
        )
        if resume_pct is not None and (progress_pct is None or progress_pct < resume_pct):
            progress_pct = resume_pct
        if progress_pct is None:
            return None
        return max(0.0, min(100.0, float(progress_pct)))

    def _initialize_job_telemetry_tracking(self, *, progress_pct: float | None) -> None:
        self._job_last_authoritative_progress_pct = (
            None if progress_pct is None else max(0.0, min(100.0, float(progress_pct)))
        )
        if progress_pct is None:
            next_milestone = _JOB_TELEMETRY_MILESTONE_STEP_PCT
        else:
            bounded_pct = max(0.0, min(100.0, float(progress_pct)))
            next_milestone = (
                int(bounded_pct // _JOB_TELEMETRY_MILESTONE_STEP_PCT) + 1
            ) * _JOB_TELEMETRY_MILESTONE_STEP_PCT
        if next_milestone > _JOB_TELEMETRY_MAX_MILESTONE_PCT:
            self._job_next_milestone_pct = None
            return
        self._job_next_milestone_pct = int(next_milestone)

    def _emit_job_progress_milestones(
        self,
        *,
        progress_pct: float | None,
        metrics: dict | None = None,
        perf_metrics: dict | None = None,
    ) -> int:
        if progress_pct is None or self._job_next_milestone_pct is None:
            return 0
        bounded_pct = max(0.0, min(100.0, float(progress_pct)))
        logged = 0
        while (
            self._job_next_milestone_pct is not None
            and self._job_next_milestone_pct <= _JOB_TELEMETRY_MAX_MILESTONE_PCT
            and bounded_pct >= float(self._job_next_milestone_pct)
        ):
            milestone_pct = int(self._job_next_milestone_pct)
            self._log_job_telemetry_line(
                metrics=metrics,
                perf_metrics=perf_metrics,
                progress_pct=bounded_pct,
                milestone_pct=milestone_pct,
            )
            logged += 1
            next_milestone = milestone_pct + _JOB_TELEMETRY_MILESTONE_STEP_PCT
            self._job_next_milestone_pct = (
                next_milestone
                if next_milestone <= _JOB_TELEMETRY_MAX_MILESTONE_PCT
                else None
            )
        return logged

    def _maybe_log_job_progress_milestones(
        self,
        *,
        done_total: tuple[int, int] | None = None,
        byte_progress: tuple[int, int] | None = None,
    ) -> int:
        if self._job_lifecycle_run_type is None or not self._job_is_active_for_telemetry():
            return 0
        progress_pct = self._resolve_job_progress_pct(
            done_total=done_total,
            byte_progress=byte_progress,
        )
        if progress_pct is None:
            return 0
        if (
            self._job_last_authoritative_progress_pct is None
            or progress_pct > self._job_last_authoritative_progress_pct
        ):
            self._job_last_authoritative_progress_pct = progress_pct
        metrics = self._runtime_metrics_snapshot()
        perf_metrics = self._performance_metrics_snapshot()
        logged = self._emit_job_progress_milestones(
            progress_pct=progress_pct,
            metrics=metrics,
            perf_metrics=perf_metrics,
        )
        if logged > 0:
            self._schedule_job_telemetry(token=self._job_telemetry_token)
        return logged

    def _cancel_job_telemetry(self) -> None:
        after_id = self._job_telemetry_after_id
        self._job_telemetry_after_id = None
        if after_id is not None:
            try:
                self.app.after_cancel(after_id)
            except Exception as exc:
                logger.debug("Failed canceling job telemetry timer: %s", exc, exc_info=exc)

    def stop_job_lifecycle_logging(self) -> None:
        self._cancel_job_telemetry()
        self._job_lifecycle_run_type = None
        self._job_next_milestone_pct = None
        self._job_last_authoritative_progress_pct = None
        self._job_telemetry_token += 1

    def _schedule_job_telemetry(self, *, token: int) -> None:
        self._cancel_job_telemetry()

        def _run() -> None:
            if self._job_telemetry_scheduling:
                return
            self._job_telemetry_after_id = None
            if token != self._job_telemetry_token:
                return
            if not self._job_is_active_for_telemetry():
                return
            metrics = self._runtime_metrics_snapshot()
            perf_metrics = self._performance_metrics_snapshot()
            progress_pct = self._resolve_job_progress_pct(metrics=metrics)
            if self._job_last_authoritative_progress_pct is not None:
                if progress_pct is None:
                    progress_pct = self._job_last_authoritative_progress_pct
                else:
                    progress_pct = max(progress_pct, self._job_last_authoritative_progress_pct)
            if progress_pct is not None:
                self._job_last_authoritative_progress_pct = progress_pct
            logged = self._emit_job_progress_milestones(
                progress_pct=progress_pct,
                metrics=metrics,
                perf_metrics=perf_metrics,
            )
            if logged == 0:
                self._log_job_telemetry_line(
                    label="Heartbeat",
                    metrics=metrics,
                    perf_metrics=perf_metrics,
                    progress_pct=progress_pct,
                )
            self._schedule_job_telemetry(token=token)

        try:
            self._job_telemetry_scheduling = True
            self._job_telemetry_after_id = self.app.after(
                _JOB_TELEMETRY_HEARTBEAT_INTERVAL_MS,
                _run,
            )
        except Exception as exc:
            logger.debug("Failed scheduling job telemetry timer: %s", exc, exc_info=exc)
        finally:
            self._job_telemetry_scheduling = False

    def _job_is_active_for_telemetry(self) -> bool:
        stream_state = str(getattr(self.app, "_stream_state", "") or "").strip().lower()
        return stream_state in {"running", "paused"} or bool(
            getattr(self.app, "_stream_done_pending_idle", False)
        )

    def _log_job_telemetry_line(
        self,
        *,
        label: str = "Telemetry",
        metrics: dict | None = None,
        perf_metrics: dict | None = None,
        progress_pct: float | None = None,
        milestone_pct: int | None = None,
    ) -> None:
        metrics = metrics if metrics is not None else self._runtime_metrics_snapshot()
        perf_metrics = (
            perf_metrics if perf_metrics is not None else self._performance_metrics_snapshot()
        )
        parts: list[str] = []
        if milestone_pct is not None:
            parts.append(f"milestone={int(milestone_pct)}%")
        start_time = getattr(self.app, "_job_started_at", None)
        if start_time is not None:
            try:
                elapsed_s = max(0.0, time.time() - float(getattr(start_time, "timestamp", lambda: 0.0)()))
            except Exception:
                try:
                    elapsed_s = max(0.0, time.time() - float(start_time.timestamp()))
                except Exception:
                    elapsed_s = 0.0
            parts.append(f"elapsed={self._format_elapsed_compact(elapsed_s)}")
        if progress_pct is None:
            progress_metric = metrics.get("stream_progress_pct", None)
            try:
                if progress_metric is not None:
                    progress_pct = float(progress_metric)
                else:
                    progress_pct = float(getattr(self.app, "_stream_progress_pct", 0.0) or 0.0)
            except Exception:
                progress_pct = float(getattr(self.app, "_stream_progress_pct", 0.0) or 0.0)
        parts.append(f"progress={progress_pct:.1f}%")
        acked_idx = self._resolved_acked_index(metrics)
        total = int(getattr(self.app, "_gcode_executable_lines", 0) or getattr(self.app, "_gcode_total_lines", 0) or 0)
        if total > 0:
            parts.append(f"acked_line={max(0, acked_idx + 1):,}/{total:,}")
        elif acked_idx >= 0:
            parts.append(f"acked_line={acked_idx + 1:,}")
        acked_bytes_metric = metrics.get("acked_byte_offset", None)
        try:
            if acked_bytes_metric is not None:
                acked_bytes = int(acked_bytes_metric or 0)
            else:
                acked_bytes = int(getattr(self.app, "_stream_acked_byte_offset", 0) or 0)
        except Exception:
            acked_bytes = int(getattr(self.app, "_stream_acked_byte_offset", 0) or 0)
        file_size_metric = metrics.get("stream_file_size_bytes", None)
        try:
            if file_size_metric is not None:
                file_size = int(file_size_metric or 0)
            else:
                file_size = int(
                    getattr(self.app, "_stream_progress_file_size_bytes", 0)
                    or getattr(self.app, "_gcode_file_size_bytes", 0)
                    or 0
                )
        except Exception:
            file_size = int(
                getattr(self.app, "_stream_progress_file_size_bytes", 0)
                or getattr(self.app, "_gcode_file_size_bytes", 0)
                or 0
            )
        if file_size > 0:
            parts.append(f"bytes={acked_bytes:,}/{file_size:,}")
        queue_depth = metrics.get("queue_depth_last")
        if isinstance(queue_depth, dict):
            try:
                parts.append(f"queue={int(queue_depth.get('stream', 0) or 0)}")
            except Exception:
                pass
        else:
            try:
                parts.append(
                    f"queue={int(metrics.get('stream_outstanding_queue_depth', 0) or 0)}"
                )
            except Exception:
                pass
        try:
            parts.append(f"buf={int(metrics.get('stream_buf_used_bytes', 0) or 0)}B")
        except Exception:
            pass
        ack_avg = metrics.get("ack_latency_ms_avg", metrics.get("ok_latency_ms_avg"))
        try:
            if ack_avg is not None:
                parts.append(f"ack_avg_ms={float(ack_avg):.1f}")
        except Exception:
            pass
        tx_lps = metrics.get("tx_lines_per_sec")
        try:
            if tx_lps is not None:
                parts.append(f"tx_lps={float(tx_lps):.2f}")
        except Exception:
            pass
        cpu_avg = perf_metrics.get("stream_cpu_avg", None)
        try:
            if cpu_avg is not None:
                parts.append(f"cpu={float(cpu_avg):.1f}%")
        except Exception:
            pass
        rss_current = perf_metrics.get("rss_current_bytes", None)
        try:
            if rss_current is not None:
                parts.append(f"rss={self._format_bytes_compact(int(rss_current or 0)).replace(' ', '')}")
        except Exception:
            pass
        self.handle_log(f"[{self._timestamp()}] [job] {label}: " + ", ".join(parts))

    def log_job_loaded(self) -> None:
        self.stop_job_lifecycle_logging()
        metadata = get_loaded_job_metadata_state(self.app)
        name = self._job_name() or "unavailable"
        file_size = int(metadata.file_size_bytes or 0)
        storage_mode = str(metadata.storage_mode or "").strip() or "unavailable"
        exec_count = int(metadata.executable_lines or 0)
        exec_known = bool(metadata.executable_lines_known)
        line_state = "known" if exec_known else "estimated"
        parts = [
            f"file={name}",
            f"size={self._format_bytes_compact(file_size)}",
            f"storage={storage_mode}",
            f"executable_lines={exec_count:,} ({line_state})" if exec_count > 0 else "executable_lines=unavailable",
            f"dry_run={self._bool_var(getattr(self.app, 'dry_run_sanitize_stream', False))}",
        ]
        self.handle_log(f"[{self._timestamp()}] [job] Loaded: " + ", ".join(parts))
        toolpaths, tools = self._job_metadata_lists()
        if tools:
            self.handle_log(f"[{self._timestamp()}] [job] Tools: " + " | ".join(tools))
        if toolpaths:
            self.handle_log(f"[{self._timestamp()}] [job] Toolpaths: " + " | ".join(toolpaths))

    def log_job_started(self, *, run_type: str, start_index: int = 0) -> None:
        self.stop_job_lifecycle_logging()
        normalized_run_type = str(run_type or "").strip().lower() or "normal"
        self._job_lifecycle_run_type = normalized_run_type
        parts = [f"file={self._job_name() or 'unavailable'}", f"run_type={normalized_run_type}"]
        if normalized_run_type == "resume" or int(start_index) > 0:
            parts.append(f"start_line={int(start_index) + 1:,}")
            parts.append(f"start_index={int(start_index)}")
        self.handle_log(f"[{self._timestamp()}] [job] Start: " + ", ".join(parts))
        metrics = self._runtime_metrics_snapshot()
        if normalized_run_type == "resume" or int(start_index) > 0:
            initial_progress_pct = self._resolve_job_progress_pct(
                metrics=metrics,
                start_index=int(start_index),
            )
        else:
            initial_progress_pct = 0.0
        self._initialize_job_telemetry_tracking(progress_pct=initial_progress_pct)
        self._log_job_telemetry_line(metrics=metrics)
        self._job_telemetry_token += 1
        self._schedule_job_telemetry(token=self._job_telemetry_token)

    def log_job_completed(
        self,
        *,
        elapsed_str: str,
        start_text: str,
        finish_text: str,
    ) -> None:
        prior_run_type = self._job_lifecycle_run_type
        self.stop_job_lifecycle_logging()
        metadata = get_loaded_job_metadata_state(self.app)
        metrics = self._runtime_metrics_snapshot()
        progress_pct = float(getattr(self.app, "_stream_progress_pct", metrics.get("stream_progress_pct", 0.0)) or 0.0)
        acked_idx = self._resolved_acked_index(metrics)
        total = int(metadata.executable_lines or metadata.total_lines or 0)
        acked_line_text = (
            f"{max(0, acked_idx + 1):,}/{total:,}"
            if total > 0
            else (f"{acked_idx + 1:,}" if acked_idx >= 0 else "unavailable")
        )
        acked_bytes = int(
            getattr(self.app, "_stream_acked_byte_offset", metrics.get("acked_byte_offset", 0)) or 0
        )
        file_size = int(
            getattr(self.app, "_stream_progress_file_size_bytes", 0)
            or metadata.file_size_bytes
            or metrics.get("stream_file_size_bytes", 0)
            or 0
        )
        run_type = prior_run_type or (
            "dry run" if self._bool_var(getattr(self.app, "dry_run_sanitize_stream", False)) else "normal"
        )
        parts = [
            f"file={self._job_name() or 'unavailable'}",
            f"run_type={run_type}",
            f"started={start_text}",
            f"finished={finish_text}",
            f"elapsed={elapsed_str}",
            f"progress={progress_pct:.1f}%",
            f"acked_line={acked_line_text}",
        ]
        if file_size > 0:
            parts.append(f"bytes={acked_bytes:,}/{file_size:,}")
        parts.append(f"dry_run={self._bool_var(getattr(self.app, 'dry_run_sanitize_stream', False))}")
        self.handle_log(f"[{self._timestamp()}] [job] Completed: " + ", ".join(parts))

    def handle_buffer_fill(self, pct: int, used: int, window: int) -> None:
        """Queue buffer utilization updates."""
        self._pending_buffer = (pct, used, window)
        self._schedule_buffer_flush()

    def handle_throughput(self, bps: float) -> None:
        """Update throughput display."""
        if self.throughput_var:
            text = self.app._format_throughput(float(bps))
            if self._last_throughput_text != text:
                self.throughput_var.set(text)
                self._last_throughput_text = text

    def handle_gcode_sent(self, idx: int) -> None:
        """Queue sent-line marker updates."""
        if self._pending_sent_index is None or idx > self._pending_sent_index:
            self._pending_sent_index = idx
        self._schedule_gcode_mark_flush()

    def handle_gcode_acked(self, idx: int) -> None:
        """Queue acknowledged-line marker updates."""
        if idx > getattr(self.app, "_last_acked_index", -1):
            self.app._last_acked_index = int(idx)
        if self._pending_acked_index is None or idx > self._pending_acked_index:
            self._pending_acked_index = idx
        self._schedule_gcode_mark_flush()

    def handle_progress(self, done: int, total: int) -> None:
        """Queue progress updates and estimate refreshes."""
        self._pending_progress = (done, total)
        self._schedule_progress_flush()

    def handle_progress_bytes(self, acked_offset: int, file_size_bytes: int) -> None:
        """Queue byte-based progress updates."""
        self._pending_progress_bytes = (int(acked_offset), int(file_size_bytes))
        self._schedule_progress_flush()
