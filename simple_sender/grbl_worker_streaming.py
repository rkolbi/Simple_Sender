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
import queue
import re
import threading
import time
from collections import deque
from typing import Sequence, cast

from simple_sender.types import GrblWorkerState

from .utils.constants import EVENT_QUEUE_TIMEOUT, MAX_LINE_LENGTH, RX_BUFFER_SAFETY
from .utils.exceptions import SerialWriteError
logger = logging.getLogger(__name__)


def _stream_patterns():
    from . import grbl_worker as grbl_worker_mod

    return (
        grbl_worker_mod._PAUSE_MCODE_MAP,
        grbl_worker_mod._PAUSE_MCODE_PAT,
        grbl_worker_mod._SANITIZE_TOKEN_PAT,
        grbl_worker_mod._DRY_RUN_M_CODES,
    )


def _annotate_stream_error(raw_error: str) -> str:
    from . import grbl_worker as grbl_worker_mod

    return cast(str, grbl_worker_mod.annotate_grbl_error(raw_error))


class GrblWorkerStreamingMixin(GrblWorkerState):
    def is_streaming(self) -> bool:
        """Check if currently streaming G-code.
        
        Returns:
            True if streaming is active
        """
        return self._streaming

    def set_dry_run_sanitize(self, enabled: bool) -> None:
        """Enable or disable dry-run sanitization for streamed G-code."""
        self._dry_run_sanitize = bool(enabled)
    
    # ========================================================================
    # COMMAND EXECUTION
    # ========================================================================
    
    def load_gcode(self, lines: Sequence[str], *, name: str | None = None) -> None:
        """Load G-code for streaming.
        
        Args:
            lines: List of G-code lines (already cleaned)
            name: Optional job name for error reporting
        """
        self._gcode = lines
        self._gcode_name = name
        self._streaming = False
        self._paused = False
        self._send_index = 0
        self._ack_index = -1
        self._reset_stream_buffer()
        self.ui_q.put(("stream_state", "loaded", len(lines)))
        logger.info(f"Loaded {len(lines)} lines of G-code")
    
    def start_stream(self) -> None:
        """Start streaming loaded G-code from beginning."""
        if not self.is_connected():
            logger.warning("Cannot start stream - not connected")
            return
        
        if not self._gcode:
            logger.warning("Cannot start stream - no G-code loaded")
            return
        
        self._clear_outgoing()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = True
            self._paused = False
        self._abort_writes.clear()
        self._reset_stream_buffer()
        self._emit_buffer_fill()
        if self._dry_run_sanitize:
            self.ui_q.put(("log", "[dry run] Spindle/coolant/tool changes removed while streaming."))
        self.ui_q.put(("stream_state", "running", None))
        logger.info("Started G-code streaming")
    
    def start_stream_from(
        self,
        start_index: int,
        preamble: Sequence[str] | None = None
    ) -> None:
        """Resume streaming from specific line.
        
        Args:
            start_index: Zero-based index to resume from
            preamble: Optional setup commands to send first (e.g., G90, G21)
        """
        if not self.is_connected():
            logger.warning("Cannot resume stream - not connected")
            return
        
        if not self._gcode:
            logger.warning("Cannot resume stream - no G-code loaded")
            return
        
        start_index = max(0, min(start_index, len(self._gcode) - 1))
        
        self._clear_outgoing()
        with self._stream_lock:
            self._stream_token += 1
            self._streaming = True
            self._paused = False
        self._abort_writes.clear()
        self._reset_stream_buffer()
        
        with self._stream_lock:
            self._send_index = start_index
            self._ack_index = start_index - 1
            
            if preamble:
                cleaned = [ln.strip() for ln in preamble if ln and ln.strip()]
                self._resume_preamble = deque(cleaned)
        
        self._emit_buffer_fill()
        if self._dry_run_sanitize:
            self.ui_q.put(("log", "[dry run] Spindle/coolant/tool changes removed while streaming."))
        self.ui_q.put(("progress", start_index, len(self._gcode)))
        self.ui_q.put(("stream_state", "running", None))
        logger.info(f"Resumed streaming from line {start_index}")
    
    def pause_stream(self) -> None:
        """Pause active stream (feed hold)."""
        self._pause_stream()
    
    def resume_stream(self) -> None:
        """Resume paused stream (cycle start)."""
        if self._streaming:
            try:
                self.resume()
            except SerialWriteError as exc:
                logger.error(f"Resume failed: {exc}")
                self.ui_q.put(("log", f"[resume failed] {exc}"))
                return
            self._paused = False
            self.ui_q.put(("stream_state", "running", None))
            logger.info("Stream resumed")

    def _pause_stream(self, reason: str | None = None) -> None:
        if not self._streaming:
            return
        if not self._paused:
            try:
                self.hold()
            except SerialWriteError as exc:
                logger.error(f"Pause failed: {exc}")
                self.ui_q.put(("log", f"[pause failed] {exc}"))
        self._paused = True
        self.ui_q.put(("stream_state", "paused", None))
        if reason:
            self.ui_q.put(("stream_pause_reason", reason))
            logger.info(f"Stream paused ({reason})")
        else:
            logger.info("Stream paused")
    
    def stop_stream(self) -> None:
        """Stop active stream and reset."""
        self.reset(emit_state=False)
        self.ui_q.put(("stream_state", "stopped", None))
        logger.info("Stream stopped")
    
    # ========================================================================
    # STATUS MANAGEMENT
    # ========================================================================
    
    def _sanitize_stream_line(self, line: str) -> str:
        if not self._dry_run_sanitize or not line:
            return line
        _, _, sanitize_pat, dry_run_codes = _stream_patterns()

        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            letter = token[0].upper()
            if letter == "S":
                return ""
            if letter == "T":
                return ""
            if letter == "M":
                try:
                    value = float(token[1:])
                except Exception:
                    return token
                if abs(value - round(value)) < 1e-9 and int(round(value)) in dry_run_codes:
                    return ""
            return token

        return cast(str, sanitize_pat.sub(repl, line))

    def _build_line_payload(self, line: str) -> bytes | None:
        try:
            return self._encode_line_payload(line)
        except UnicodeEncodeError:
            return None

    def _pause_reason_for_line(self, line: str) -> str | None:
        if not line:
            return None
        pause_map, pause_pat, _, _ = _stream_patterns()
        match = pause_pat.search(line.upper())
        if not match:
            return None
        return cast(str | None, pause_map.get(match.group(1)))

    def _maybe_pause_after_ack(self, idx: int | None) -> None:
        if idx is None:
            return
        if self._pause_after_idx is None or idx != self._pause_after_idx:
            return
        reason = self._pause_after_reason or "M0/M1/M6"
        self._pause_after_idx = None
        self._pause_after_reason = None
        self._pause_stream(reason=reason)
        self.ui_q.put(("log", f"[stream] Paused on {reason} at line {idx + 1}"))

    def _format_stream_error(
        self,
        raw_error: str,
        idx: int | None,
        line_text: str | None,
    ) -> str:
        parts = [_annotate_stream_error(raw_error)]
        if idx is not None:
            if self._gcode_name:
                parts.append(f"{self._gcode_name} line {idx + 1}")
            else:
                parts.append(f"line {idx + 1}")
        if line_text:
            parts.append(line_text)
        return " | ".join(parts)
    
    def _tx_loop(self, stop_evt: threading.Event) -> None:
        """Transmit thread - handles streaming and command queue.
        
        Args:
            stop_evt: Event to signal thread shutdown
        """
        logger.debug("TX thread started")
        
        try:
            while not stop_evt.is_set():
                if not self.is_connected():
                    time.sleep(0.05)
                    continue
                
                # Handle streaming
                if self._streaming and not self._paused:
                    self._process_stream_queue()

                # Handle manual commands with buffer pacing
                self._process_manual_queue()

                if stop_evt.wait(EVENT_QUEUE_TIMEOUT):
                    break
        
        except Exception as e:
            logger.error(f"TX thread error: {e}", exc_info=True)
            self._emit_exception("TX thread error", e)
            self._signal_disconnect(f"TX thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("TX thread stopped")
    
    def _process_stream_queue(self) -> None:
        """Process streaming queue - fill GRBL buffer."""
        while True:
            if not self._streaming or self._paused or self._abort_writes.is_set():
                break
            
            with self._stream_lock:
                if not self._streaming or self._paused or self._abort_writes.is_set():
                    break
                
                stream_token = self._stream_token

                if (
                    self._pause_after_idx is not None
                    and self._send_index > self._pause_after_idx
                ):
                    break

                # Get next item to send
                item = self._stream_pending_item
                if item is None:
                    if self._resume_preamble:
                        line = self._resume_preamble[0]
                        item = (line, False, None)
                    else:
                        if self._send_index >= len(self._gcode):
                            break
                        line = self._gcode[self._send_index].strip()
                        item = (line, True, self._send_index)
                
                line, is_gcode, idx = item
                line = self._sanitize_stream_line(line)
                item = (line, is_gcode, idx)
                if is_gcode and idx is not None and self._pause_after_idx is None:
                    reason = self._pause_reason_for_line(line)
                    if reason:
                        self._pause_after_idx = idx
                        self._pause_after_reason = reason
                payload = self._build_line_payload(line)
                if payload is None:
                    msg = self._format_stream_error(
                        "Non-ASCII characters in line",
                        idx,
                        line,
                    )
                    self._pause_stream(reason="invalid characters")
                    self.ui_q.put(("stream_error", msg, idx, line, self._gcode_name))
                    self.ui_q.put(("log", f"[stream error] {msg}"))
                    break
                line_len = len(payload)
                if line_len > MAX_LINE_LENGTH:
                    msg = self._format_stream_error(
                        f"Line too long ({line_len} > {MAX_LINE_LENGTH})",
                        idx,
                        line,
                    )
                    self._pause_stream(reason="line too long")
                    self.ui_q.put(("stream_error", msg, idx, line, self._gcode_name))
                    self.ui_q.put(("log", f"[stream error] {msg}"))
                    break
                
                # Check if it fits in buffer
                usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
                can_fit = (self._stream_buf_used + line_len) <= usable
                
                if not can_fit and self._stream_buf_used > 0:
                    # Wait for buffer space
                    self._stream_pending_item = item
                    break
                
                # Send the line
                self._stream_pending_item = None
                if is_gcode:
                    idx = self._send_index
                    self._send_index += 1
                
                self._stream_buf_used += line_len
                self._stream_line_queue.append((line_len, is_gcode, idx, line))
            
            # Write outside lock
            if (
                self._abort_writes.is_set()
                or stream_token != self._stream_token
                or not self._streaming
                or self._paused
            ):
                with self._stream_lock:
                    if is_gcode and self._send_index > 0:
                        self._send_index -= 1
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    self._stream_pending_item = None
                self._emit_buffer_fill()
                break

            if not self._write_line(line, payload):
                # Write failed - rollback and stop streaming
                with self._stream_lock:
                    if is_gcode and self._send_index > 0:
                        self._send_index -= 1
                    
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    
                    self._stream_pending_item = item
                
                self._emit_buffer_fill()
                if not self.is_connected():
                    break
                if not (self._abort_writes.is_set() or stream_token != self._stream_token):
                    self._streaming = False
                    self._paused = False
                    self.ui_q.put(("stream_state", "error", "Write failed"))
                break
            
            # Update state
            if not is_gcode and self._resume_preamble:
                self._resume_preamble.popleft()
            
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()
            
            if is_gcode:
                self.ui_q.put(("gcode_sent", idx, line))
        
        # Check if streaming is complete
        with self._stream_lock:
            send_index = self._send_index
            ack_index = self._ack_index
            pending = bool(
                self._stream_line_queue or
                self._stream_pending_item or
                self._resume_preamble
            )
        
        if (self._streaming and
            not pending and
            send_index >= len(self._gcode) and
            ack_index >= len(self._gcode) - 1):
            self._streaming = False
            self.ui_q.put(("stream_state", "done", None))
            logger.info("Streaming complete")

    def _process_manual_queue(self) -> None:
        """Process immediate command queue with buffer pacing."""
        if self._purge_jog_queue.is_set():
            self._purge_jog_queue.clear()
            pending: list[str] = []
            try:
                while True:
                    pending.append(self._outgoing_q.get_nowait())
            except queue.Empty:
                pass
            kept = []
            for cmd in pending:
                if isinstance(cmd, str) and cmd.lstrip().upper().startswith("$J="):
                    continue
                kept.append(cmd)
            for cmd in kept:
                self._outgoing_q.put(cmd)
            if self._manual_pending_item is not None:
                line, _, _ = self._manual_pending_item
                if isinstance(line, str) and line.lstrip().upper().startswith("$J="):
                    self._manual_pending_item = None
        while True:
            if self._streaming or self._paused:
                return
            if not self.is_connected():
                return
            if self._abort_writes.is_set() and not self._alarm_active:
                return
            payload: bytes | None = None
            line_len: int
            if self._manual_pending_item is not None:
                line, payload, line_len = self._manual_pending_item
            else:
                try:
                    line = self._outgoing_q.get_nowait()
                except queue.Empty:
                    return

                if self._alarm_active:
                    cmd_upper = line.strip().upper()
                    if not (cmd_upper.startswith("$X") or cmd_upper.startswith("$H")):
                        self._clear_outgoing()
                        continue

                line = line.strip()
                if not line:
                    continue
                payload = self._build_line_payload(line)
                if payload is None:
                    self.ui_q.put((
                        "log",
                        f"[manual] Non-ASCII characters in line: {line}",
                    ))
                    self._manual_pending_item = None
                    continue
                line_len = len(payload)

            allowed_alarm_cmd = False
            if self._alarm_active:
                cmd_upper = line.strip().upper()
                allowed_alarm_cmd = cmd_upper.startswith("$X") or cmd_upper.startswith("$H")
                if not allowed_alarm_cmd:
                    self._clear_outgoing()
                    continue

            if line_len > MAX_LINE_LENGTH:
                self.ui_q.put((
                    "log",
                    f"[manual] Line too long ({line_len} > {MAX_LINE_LENGTH}): {line}",
                ))
                self._manual_pending_item = None
                continue

            drop_for_buffer = False
            usable = None
            with self._stream_lock:
                usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
                if line_len > usable and self._stream_buf_used <= 0:
                    self._manual_pending_item = None
                    drop_for_buffer = True
                else:
                    can_fit = (self._stream_buf_used + line_len) <= usable

                    if not can_fit and self._stream_buf_used > 0:
                        self._manual_pending_item = (line, payload, line_len)
                        break

                    self._manual_pending_item = None
                    self._stream_buf_used += line_len
                    self._stream_line_queue.append((line_len, False, None, line))

            if drop_for_buffer:
                self.ui_q.put((
                    "log",
                    f"[manual] Line too long for buffer ({line_len} > {usable}): {line}",
                ))
                continue

            if self._abort_writes.is_set() and not allowed_alarm_cmd:
                with self._stream_lock:
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    self._manual_pending_item = None
                self._emit_buffer_fill()
                break

            is_settings_dump = line.strip().upper() == "$$"
            if is_settings_dump:
                self._settings_dump_active = True
                self._settings_dump_seen = False
            if not self._write_line(line, payload, allow_abort=allowed_alarm_cmd):
                if is_settings_dump:
                    self._settings_dump_active = False
                    self._settings_dump_seen = False
                with self._stream_lock:
                    if self._stream_line_queue:
                        try:
                            last_len, _, _, _ = self._stream_line_queue.pop()
                        except Exception:
                            last_len = line_len
                        self._stream_buf_used = max(0, self._stream_buf_used - last_len)
                    else:
                        self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                    if self.is_connected():
                        self._manual_pending_item = (line, payload, line_len)
                    else:
                        self._manual_pending_item = None
                self._emit_buffer_fill()
                break

            self.ui_q.put(("log_tx", line))
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()

    def wait_for_manual_completion(self, timeout_s: float = 30.0) -> bool:
        """Block until manual/immediate commands finish."""
        start = time.time()
        while True:
            with self._stream_lock:
                pending = bool(self._stream_line_queue) or self._manual_pending_item is not None or bool(self._resume_preamble)
            if not pending:
                return True
            if timeout_s and (time.time() - start) > timeout_s:
                return False
            time.sleep(0.01)
    
