import logging
import time
import tkinter as tk

from simple_sender.utils.constants import MAX_CONSOLE_LINES

logger = logging.getLogger(__name__)

class StreamingController:
    def __init__(self, app):
        self.app = app
        self.console: tk.Text | None = None
        self.gview = None
        self.progress_pct = None
        self.buffer_fill = None
        self.buffer_fill_pct = None
        self.throughput_var = None
        self._console_lines: list[tuple[str, str | None]] = []
        self._console_filter: str | None = None
        self._pending_console_entries: list[tuple[str, str | None]] = []
        self._pending_console_trim = 0
        self._console_after_id = None
        self._console_render_pending = False
        self._pending_marks_after_id = None
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_buffer = None
        self._progress_after_id = None
        self._buffer_after_id = None

    def attach_widgets(
        self,
        console: tk.Text,
        gview,
        progress_pct: tk.IntVar,
        buffer_fill: tk.StringVar,
        buffer_fill_pct: tk.IntVar,
        throughput_var: tk.StringVar,
    ):
        self.console = console
        self.gview = gview
        self.progress_pct = progress_pct
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

    def _should_skip_console_entry_for_toggles(self, entry) -> bool:
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

    def _console_filter_match(self, entry, for_save: bool = False) -> bool:
        if isinstance(entry, tuple):
            s, tag = entry
        else:
            s, tag = str(entry), None
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

    def log(self, s: str, tag: str | None = None):
        if tag is None:
            tag = self._console_tag_for_line(s)
        entry = (s, tag)
        if self._should_skip_console_entry_for_toggles(entry):
            return
        self._console_lines.append(entry)
        overflow = len(self._console_lines) - MAX_CONSOLE_LINES
        if overflow > 0:
            self._console_lines = self._console_lines[overflow:]
            if self._console_filter is not None:
                if bool(self.app.performance_mode.get()):
                    self._queue_console_render()
                    return
                self._render_console()
                return
            if bool(self.app.performance_mode.get()):
                self._pending_console_trim += overflow
            else:
                self._trim_console_widget(overflow)
        if not self._console_filter_match(entry):
            return
        if bool(self.app.performance_mode.get()):
            self._pending_console_entries.append(entry)
            self._schedule_console_flush()
            return
        self._append_to_console(entry)

    def _append_to_console(self, entry):
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

    def _queue_console_render(self):
        self._console_render_pending = True
        self._schedule_console_flush()

    def _schedule_console_flush(self):
        if self._console_after_id is not None:
            return
        self._console_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_console_updates)

    def _flush_console_updates(self):
        self._console_after_id = None
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
        for line, tag in self._pending_console_entries:
            if tag:
                self.console.insert("end", line + "\n", (tag,))
            else:
                self.console.insert("end", line + "\n")
        self._pending_console_entries = []
        self.console.see("end")
        self.console.config(state="disabled")

    def _render_console(self):
        if not self.console:
            return
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        for line, tag in self._console_lines:
            if self._console_filter_match((line, tag)):
                if tag:
                    self.console.insert("end", line + "\n", (tag,))
                else:
                    self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _trim_console_widget(self, count: int):
        if count <= 0 or not self.console:
            return
        self.console.config(state="normal")
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def _trim_console_widget_unlocked(self, count: int):
        if count <= 0 or not self.console:
            return
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")

    def set_console_filter(self, mode):
        self._console_filter = mode
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        self._render_console()

    def clear_console(self):
        self._console_lines = []
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False
        if self.console:
            self.console.config(state="normal")
            self.console.delete("1.0", "end")
            self.console.config(state="disabled")

    def get_console_lines(self) -> list[tuple[str, str | None]]:
        return list(self._console_lines)

    def matches_filter(self, entry, for_save: bool = False) -> bool:
        return self._console_filter_match(entry, for_save=for_save)

    def is_position_line(self, s: str) -> bool:
        return self._is_position_line(s)

    def flush_console(self):
        self._flush_console_updates()

    def render_console(self):
        self._render_console()

    def bind_button_logging(self):
        self.app.bind_class("TButton", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Button", "<Button-1>", self._on_button_press, add="+")
        self.app.bind_class("Canvas", "<Button-1>", self._on_button_press, add="+")

    def _on_button_press(self, event):
        w = event.widget
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
        ts = time.strftime("%H:%M:%S")
        if tip and gcode:
            self.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self.log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self.log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self.log(f"[{ts}] Button: {label}")

    def _schedule_gcode_mark_flush(self):
        if self._pending_marks_after_id is not None:
            return
        self._pending_marks_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_gcode_marks)

    def _flush_gcode_marks(self):
        self._pending_marks_after_id = None
        sent_idx = self._pending_sent_index
        acked_idx = self._pending_acked_index
        self._pending_sent_index = None
        self._pending_acked_index = None
        if sent_idx is not None:
            self.app.gview.mark_sent_upto(sent_idx)
            self.app._last_sent_index = sent_idx
        if acked_idx is not None:
            self.app.gview.mark_acked_upto(acked_idx)
            self.app._last_acked_index = acked_idx
            if sent_idx is None and acked_idx > self.app._last_sent_index:
                self.app._last_sent_index = acked_idx
        if sent_idx is not None or acked_idx is not None:
            self.app._update_current_highlight()

    def _schedule_progress_flush(self):
        if self._progress_after_id is not None:
            return
        self._progress_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_progress)

    def _flush_progress(self):
        self._progress_after_id = None
        if not self._pending_progress:
            return
        done, total = self._pending_progress
        self._pending_progress = None
        if self.progress_pct:
            self.progress_pct.set(int(round((done / total) * 100)) if total else 0)
        if done and total:
            self.app._update_live_estimate(done, total)
        self.app._maybe_notify_job_completion(done, total)

    def _schedule_buffer_flush(self):
        if self._buffer_after_id is not None:
            return
        self._buffer_after_id = self.app.after(self.app._ui_throttle_ms, self._flush_buffer_fill)

    def _flush_buffer_fill(self):
        self._buffer_after_id = None
        if not self._pending_buffer:
            return
        pct, used, window = self._pending_buffer
        self._pending_buffer = None
        if self.buffer_fill:
            self.buffer_fill.set(f"Buffer: {pct}% ({used}/{window})")
        if self.buffer_fill_pct:
            self.buffer_fill_pct.set(pct)

    def clear_pending_ui_updates(self):
        for attr in (
            "_pending_marks_after_id",
            "_progress_after_id",
            "_buffer_after_id",
            "_console_after_id",
        ):
            val = getattr(self, attr, None)
            if val is None:
                continue
            try:
                self.app.after_cancel(val)
            except Exception:
                pass
            setattr(self, attr, None)
        self._pending_marks_after_id = None
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_buffer = None
        self._pending_console_entries = []
        self._pending_console_trim = 0
        self._console_render_pending = False

    def handle_log_rx(self, raw: str):
        if self._should_suppress_rx_log(raw):
            return
        self.log(f"<< {raw}", self._console_tag_for_line(raw))

    def handle_log_tx(self, message: str):
        self.log(f">> {message}")

    def handle_log(self, message: str):
        self.log(message)

    def handle_buffer_fill(self, pct: int, used: int, window: int):
        self._pending_buffer = (pct, used, window)
        self._schedule_buffer_flush()

    def handle_throughput(self, bps: float):
        if self.throughput_var:
            self.throughput_var.set(self.app._format_throughput(float(bps)))

    def handle_gcode_sent(self, idx: int):
        if self._pending_sent_index is None or idx > self._pending_sent_index:
            self._pending_sent_index = idx
        self._schedule_gcode_mark_flush()

    def handle_gcode_acked(self, idx: int):
        if self._pending_acked_index is None or idx > self._pending_acked_index:
            self._pending_acked_index = idx
        self._schedule_gcode_mark_flush()

    def handle_progress(self, done: int, total: int):
        self._pending_progress = (done, total)
        self._schedule_progress_flush()


