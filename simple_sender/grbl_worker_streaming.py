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
import queue
import re
import threading
import time
from collections import deque
from functools import lru_cache
from typing import Sequence, TYPE_CHECKING, cast

from simple_sender.types import (
    GcodeAckedEvent,
    GcodeSentEvent,
    GrblWorkerState,
    ManualCommandResultTracker,
    ManualPendingItem,
    ProgressBytesEvent,
    ProgressEvent,
    StreamCompletionEofEvent,
    StreamErrorEvent,
    StreamPendingItem,
    StreamPauseReasonEvent,
    StreamQueueItem,
    StreamStateEvent,
)
from simple_sender.kasa_accessory import SpindleCommandDetector

from .utils.constants import (
    EVENT_QUEUE_TIMEOUT,
    GCODE_IN_MEMORY_SEND_CACHE_THRESHOLD,
    MAX_LINE_LENGTH,
    RX_BUFFER_SAFETY,
)
from .utils.exceptions import SerialWriteError
from .gcode_source import FileGcodeSource
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
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


class _StreamSourceReadError(RuntimeError):
    """Raised when file-backed source reads fail during streaming."""

    def __init__(self, *, idx: int, source_path: str, error: Exception):
        self.idx = int(idx)
        self.source_path = str(source_path or "")
        self.error = error
        super().__init__(str(error) or "source read failed")


class GrblWorkerStreamingMixin(GrblWorkerState):
    if TYPE_CHECKING:
        def manual_queue_busy(self) -> bool: ...

    @staticmethod
    def _stream_source_line_count_known(source: object) -> bool:
        checker = getattr(source, "line_count_known", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass
        return bool(getattr(source, "_line_count_known", True))

    def _stream_source_index_exhausted(self, source: object, idx: int) -> bool:
        reader = getattr(source, "read_line_with_offsets", None)
        if callable(reader) and not self._stream_source_line_count_known(source):
            return False
        try:
            return int(idx) >= len(source)  # type: ignore[arg-type]
        except Exception:
            return False

    def _stream_source_index_known_out_of_range(self, source: object, idx: int) -> bool:
        reader = getattr(source, "read_line_with_offsets", None)
        if callable(reader) and not self._stream_source_line_count_known(source):
            return False
        try:
            return int(idx) >= len(source)  # type: ignore[arg-type]
        except Exception:
            return False

    def _stream_completion_verified(
        self,
        *,
        source: object,
        send_index: int,
        ack_index: int,
    ) -> tuple[bool, int, bool]:
        line_count_known = self._stream_source_line_count_known(source)
        total_lines = max(0, int(len(source)))  # type: ignore[arg-type]
        if not self._stream_source_index_exhausted(source, send_index):
            return False, total_lines, line_count_known
        reader = getattr(source, "read_line_with_offsets", None)
        if callable(reader) and not line_count_known:
            return False, total_lines, line_count_known
        if ack_index < total_lines - 1:
            return False, total_lines, line_count_known
        return True, total_lines, line_count_known

    def stop_stream_performs_reset(self) -> bool:
        return True

    def _resolve_stream_file_size_bytes(self, lines: Sequence[str]) -> int:
        try:
            prepared = int(getattr(lines, "_prepare_file_size_bytes", 0) or 0)
        except Exception:
            prepared = 0
        if prepared > 0:
            return prepared
        if isinstance(lines, FileGcodeSource):
            path = str(getattr(lines, "path", "") or "").strip()
            if path:
                try:
                    return max(0, int(os.path.getsize(path)))
                except Exception:
                    return 0
        return 0

    def _signal_tx_activity(self) -> None:
        evt = getattr(self, "_tx_activity_evt", None)
        if evt is None:
            return
        try:
            evt.set()
        except Exception:
            return

    def _tx_idle_wait_s(self) -> float:
        try:
            value = float(getattr(self, "_tx_loop_idle_wait_s", 0.2))
        except Exception:
            value = 0.2
        if value < EVENT_QUEUE_TIMEOUT:
            return float(EVENT_QUEUE_TIMEOUT)
        return value

    def _wait_for_tx_activity_or_stop(
        self,
        stop_evt: threading.Event,
        timeout_s: float,
    ) -> bool:
        """Wait for TX activity with periodic stop checks.

        Returns True when stop was requested, False otherwise.
        """
        timeout = max(float(EVENT_QUEUE_TIMEOUT), float(timeout_s))
        wake_evt = getattr(self, "_tx_activity_evt", None)
        if wake_evt is None or timeout <= EVENT_QUEUE_TIMEOUT:
            return bool(stop_evt.wait(timeout))
        deadline = time.monotonic() + timeout
        while not stop_evt.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                if isinstance(stop_evt, threading.Event):
                    return bool(stop_evt.is_set())
                try:
                    return bool(stop_evt.wait(timeout))
                except Exception:
                    return False
            wait_slice = min(0.05, remaining)
            try:
                if wake_evt.wait(wait_slice):
                    try:
                        wake_evt.clear()
                    except Exception:
                        pass
                    return False
            except Exception:
                return bool(stop_evt.wait(wait_slice))
        return True

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

    def _clear_gcode_send_cache(self) -> None:
        self._gcode_payload_cache = None
        self._gcode_pause_reason_cache = None
        self._gcode_spindle_state_cache = None

    @staticmethod
    def _normalize_stream_line(raw_line: str) -> str:
        if not raw_line:
            return ""
        if raw_line[0].isspace() or raw_line[-1].isspace():
            return raw_line.strip()
        return raw_line

    def _prepare_in_memory_gcode_send_cache(self, lines: Sequence[str]) -> None:
        self._clear_gcode_send_cache()
        if not isinstance(lines, list | tuple):
            return
        line_count = len(lines)
        if line_count <= 0 or line_count > int(GCODE_IN_MEMORY_SEND_CACHE_THRESHOLD):
            return
        payload_cache: list[bytes | None] = [None] * line_count
        pause_cache: list[str | None] = [None] * line_count
        spindle_cache: list[bool | None] = [None] * line_count
        for idx, raw_line in enumerate(lines):
            line = self._normalize_stream_line(raw_line)
            payload_cache[idx] = self._build_line_payload(line)
            pause_cache[idx] = self._pause_reason_for_line(line)
            spindle_cache[idx] = self._detect_spindle_state(line)
        self._gcode_payload_cache = payload_cache
        self._gcode_pause_reason_cache = pause_cache
        self._gcode_spindle_state_cache = spindle_cache

    def prime_gcode_send_cache(self, lines: Sequence[str]) -> None:
        """Prime fast-send metadata from an in-memory line list.

        This is used when the active stream source is file-backed but the UI
        already has a full in-memory line list for non-sample jobs.
        """
        self._prepare_in_memory_gcode_send_cache(lines)
    
    def load_gcode(self, lines: Sequence[str], *, name: str | None = None) -> None:
        """Load G-code for streaming.
        
        Args:
            lines: List of G-code lines (already cleaned)
            name: Optional job name for error reporting
        """
        self._gcode = lines
        self._prepare_in_memory_gcode_send_cache(lines)
        self._gcode_name = name
        self._streaming = False
        self._paused = False
        self._send_index = 0
        self._ack_index = -1
        self._ack_byte_offset = 0
        self._stream_file_size_bytes = self._resolve_stream_file_size_bytes(lines)
        self._reset_stream_buffer()
        self.ui_q.put(StreamStateEvent("loaded", len(lines)))
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
        self._ack_byte_offset = 0
        self._emit_buffer_fill()
        if int(getattr(self, "_stream_file_size_bytes", 0) or 0) > 0:
            self.ui_q.put(ProgressBytesEvent(0, int(self._stream_file_size_bytes)))
        self._signal_tx_activity()
        if self._dry_run_sanitize:
            self.ui_q.put(
                (
                    "log",
                    "[dry run] Spindle/coolant commands and M6/S/T words removed while streaming; TC: directives still run.",
                )
            )
        self.ui_q.put(StreamStateEvent("running", None))
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

        start_index = max(0, int(start_index))
        source_obj: object | None = self._gcode
        reader = getattr(source_obj, "read_line_with_offsets", None)
        if self._stream_source_index_known_out_of_range(self._gcode, start_index):
            msg = (
                f"[resume failed] Requested start line {start_index + 1} "
                "exceeds the loaded job length."
            )
            logger.warning(msg)
            self.ui_q.put(("log", msg))
            return
        if not callable(reader):
            source_obj = getattr(self, "_gcode_source", None)
            reader = getattr(source_obj, "read_line_with_offsets", None)
        initial_ack_byte_offset = 0
        if callable(reader):
            if start_index > 0:
                try:
                    _line, _start_offset, end_offset = reader(start_index - 1)
                    if end_offset is not None:
                        initial_ack_byte_offset = max(0, int(end_offset))
                except IndexError:
                    msg = (
                        f"[resume failed] Requested start line {start_index + 1} "
                        "exceeds the actual file length."
                    )
                    logger.warning(msg)
                    self.ui_q.put(("log", msg))
                    return
                except Exception:
                    initial_ack_byte_offset = 0
            try:
                reader(start_index)
            except IndexError:
                msg = (
                    f"[resume failed] Requested start line {start_index + 1} "
                    "exceeds the actual file length."
                )
                logger.warning(msg)
                self.ui_q.put(("log", msg))
                return
            except Exception:
                pass

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
            self._ack_byte_offset = int(initial_ack_byte_offset)
            
            if preamble:
                cleaned = [ln.strip() for ln in preamble if ln and ln.strip()]
                self._resume_preamble = deque(cleaned)

        self._emit_buffer_fill()
        if int(getattr(self, "_stream_file_size_bytes", 0) or 0) > 0:
            self.ui_q.put(
                ProgressBytesEvent(
                    min(
                        int(getattr(self, "_ack_byte_offset", 0) or 0),
                        int(self._stream_file_size_bytes),
                    ),
                    int(self._stream_file_size_bytes),
                )
            )
        self._signal_tx_activity()
        if self._dry_run_sanitize:
            self.ui_q.put(
                (
                    "log",
                    "[dry run] Spindle/coolant commands and M6/S/T words removed while streaming; TC: directives still run.",
                )
            )
        self.ui_q.put(ProgressEvent(int(start_index), len(self._gcode)))
        self.ui_q.put(StreamStateEvent("running", None))
        logger.info(f"Resumed streaming from line {start_index}")
    
    def pause_stream(self) -> bool | None:
        """Pause active stream (feed hold)."""
        return self._pause_stream()
    
    def resume_stream(self) -> bool | None:
        """Resume paused stream (cycle start)."""
        if not self._streaming:
            return None
        try:
            accepted = self.resume()
        except SerialWriteError as exc:
            logger.error(f"Resume failed: {exc}")
            self.ui_q.put(("log", f"[resume failed] {exc}"))
            return False
        if accepted is False:
            self.ui_q.put(("log", "[resume failed] Cycle-start was not sent."))
            return False
        self._paused = False
        self._signal_tx_activity()
        self.ui_q.put(StreamStateEvent("running", None))
        logger.info("Stream resumed")
        return True

    def _pause_stream(self, reason: str | None = None) -> bool | None:
        if not self._streaming:
            return None
        if not self._paused:
            try:
                accepted = self.hold()
            except SerialWriteError as exc:
                logger.error(f"Pause failed: {exc}")
                self.ui_q.put(("log", f"[pause failed] {exc}"))
                return False
            if accepted is False:
                self.ui_q.put(("log", "[pause failed] Feed hold was not sent."))
                return False
        self._paused = True
        self._signal_tx_activity()
        self.ui_q.put(StreamStateEvent("paused", None))
        if reason:
            self.ui_q.put(StreamPauseReasonEvent(str(reason)))
            logger.info(f"Stream paused ({reason})")
        else:
            logger.info("Stream paused")
        return True
    
    def stop_stream(self) -> bool | None:
        """Stop active stream and reset."""
        if not (self._streaming or self._paused):
            return None
        accepted = self.reset(emit_state=False)
        if accepted is False:
            self.ui_q.put(("log", "[stop failed] Ctrl-X was not sent; stream state is unchanged."))
            return False
        self.ui_q.put(StreamStateEvent("stopped", None))
        logger.info("Stream stopped")
        return True
    
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
                self._tx_loop_cycles += 1
                if not self.is_connected():
                    idle_wait = self._tx_idle_wait_s()
                    self._tx_loop_idle_cycles += 1
                    self._tx_loop_idle_wait_total_s += idle_wait
                    if stop_evt.wait(idle_wait):
                        break
                    continue
                
                # Handle streaming
                if self._streaming and not self._paused:
                    self._process_stream_queue()

                # Handle manual commands with buffer pacing
                self._process_manual_queue()

                idle_wait = EVENT_QUEUE_TIMEOUT
                blocked_waiting_for_ack = False
                with self._stream_lock:
                    has_stream_work = bool(
                        self._streaming
                        and not self._paused
                        and (
                            self._stream_pending_item is not None
                            or self._resume_preamble
                            or self._send_index < len(self._gcode)
                        )
                    )
                    has_manual_work = bool(
                        self._manual_pending_item is not None
                        or not self._outgoing_q.empty()
                        or self._purge_jog_queue.is_set()
                    )
                    blocked_waiting_for_ack = bool(
                        has_stream_work
                        and not has_manual_work
                        and self._stream_pending_item is not None
                        and self._stream_buf_used > 0
                    )
                if (not has_stream_work and not has_manual_work) or blocked_waiting_for_ack:
                    idle_wait = self._tx_idle_wait_s()
                    self._tx_loop_idle_cycles += 1
                    self._tx_loop_idle_wait_total_s += idle_wait
                else:
                    self._tx_loop_active_cycles += 1
                if self._wait_for_tx_activity_or_stop(stop_evt, idle_wait):
                    break
        
        except Exception as e:
            logger.error(f"TX thread error: {e}", exc_info=True)
            self._emit_exception("TX thread error", e)
            self._signal_disconnect(f"[tx/thread] TX thread error: {e}")
            stop_evt.set()
        
        finally:
            logger.debug("TX thread stopped")

    def _stream_loop_blocked(self) -> bool:
        return (not self._streaming) or self._paused or self._abort_writes.is_set()

    def _snapshot_file_backed_stream_fetch_locked(
        self,
    ) -> tuple[int, object, int, str] | None:
        if self._pause_after_idx is not None and self._send_index > self._pause_after_idx:
            return None
        if self._stream_tool_change_pending is not None:
            return None
        if self._stream_pending_item is not None:
            return None
        if self._resume_preamble:
            return None
        source = self._gcode
        reader = getattr(source, "read_line_with_offsets", None)
        if not callable(reader):
            return None
        if self._stream_source_index_exhausted(source, self._send_index):
            return None
        return (
            int(self._stream_token),
            source,
            int(self._send_index),
            str(getattr(source, "path", "") or "").strip(),
        )

    def _read_prefetched_stream_item(
        self,
        *,
        source: object,
        idx: int,
        source_path: str,
    ) -> StreamPendingItem | None:
        reader = getattr(source, "read_line_with_offsets", None)
        if not callable(reader):
            return None
        try:
            raw_line, _line_start_offset, line_end_offset = reader(idx)
        except IndexError:
            setter = getattr(source, "set_line_count", None)
            if callable(setter) and not isinstance(source, FileGcodeSource):
                try:
                    setter(idx, known=True)
                except Exception:
                    pass
            return None
        except Exception as exc:
            raise _StreamSourceReadError(
                idx=int(idx),
                source_path=source_path,
                error=exc,
            ) from exc
        line = self._normalize_stream_line(raw_line)
        return StreamPendingItem(
            line=line,
            is_gcode=True,
            idx=int(idx),
            file_end_offset=line_end_offset,
        )

    def _revalidate_prefetched_stream_item_locked(
        self,
        *,
        stream_token: int,
        source: object,
        idx: int,
    ) -> bool:
        if self._stream_loop_blocked():
            return False
        if stream_token != self._stream_token:
            return False
        if source is not self._gcode:
            return False
        if self._pause_after_idx is not None and self._send_index > self._pause_after_idx:
            return False
        if self._stream_tool_change_pending is not None:
            return False
        if self._stream_pending_item is not None:
            return False
        if self._resume_preamble:
            return False
        if idx != self._send_index:
            return False
        if self._stream_source_index_exhausted(source, idx):
            return False
        return True

    def _next_stream_item_locked(self) -> StreamPendingItem | None:
        if self._pause_after_idx is not None and self._send_index > self._pause_after_idx:
            return None
        if self._stream_tool_change_pending is not None:
            return None
        if self._stream_pending_item is not None:
            return self._stream_pending_item
        if self._resume_preamble:
            return StreamPendingItem(line=self._resume_preamble[0], is_gcode=False, idx=None)
        source = self._gcode
        if self._stream_source_index_exhausted(source, self._send_index):
            return None
        line_end_offset: int | None = None
        source_path = ""
        try:
            source_path = str(getattr(source, "path", "") or "").strip()
            reader = getattr(source, "read_line_with_offsets", None)
            if callable(reader):
                raw_line, _line_start_offset, line_end_offset = reader(self._send_index)
            else:
                raw_line = self._gcode[self._send_index]
        except IndexError:
            # File-backed sources may start with estimated line counts; clamp
            # to exact totals once EOF is discovered.
            setter = getattr(self._gcode, "set_line_count", None)
            if callable(setter) and not isinstance(self._gcode, FileGcodeSource):
                try:
                    setter(self._send_index, known=True)
                except Exception:
                    pass
            return None
        except Exception as exc:
            raise _StreamSourceReadError(
                idx=int(self._send_index),
                source_path=source_path,
                error=exc,
            ) from exc
        line = self._normalize_stream_line(raw_line)
        return StreamPendingItem(
            line=line,
            is_gcode=True,
            idx=self._send_index,
            file_end_offset=line_end_offset,
        )

    @staticmethod
    def _match_stream_directive(line: str) -> tuple[str, str | None] | None:
        stripped = str(line or "").strip()
        if not stripped:
            return None
        if stripped == "VACUUM_ON":
            return ("vacuum_on", None)
        if stripped == "VACUUM_OFF":
            return ("vacuum_off", None)
        if stripped.startswith("TC:"):
            return ("tool_change", stripped[3:].strip())
        return None

    def _ack_handled_stream_line(self, item: StreamPendingItem, *, emit_sent: bool = True) -> None:
        ack_idx: int | None = None
        ack_line = str(item.line or "")
        ack_byte_offset: int | None = None
        stream_file_size_bytes = 0
        with self._stream_lock:
            if not item.is_gcode:
                return
            idx = self._send_index
            if item.idx is not None:
                try:
                    idx = int(item.idx)
                except Exception:
                    idx = self._send_index
            if idx != self._send_index:
                idx = self._send_index
            self._send_index = idx + 1
            if idx > self._ack_index:
                self._ack_index = idx
            ack_idx = idx
            queued_end_offset = item.file_end_offset
            if queued_end_offset is not None:
                try:
                    queued_end = max(0, int(queued_end_offset))
                except Exception:
                    queued_end = 0
                if queued_end > int(getattr(self, "_ack_byte_offset", 0) or 0):
                    self._ack_byte_offset = queued_end
                ack_byte_offset = int(getattr(self, "_ack_byte_offset", 0) or 0)
                stream_file_size_bytes = int(
                    getattr(self, "_stream_file_size_bytes", 0) or 0
                )
            self._live_current_acked = (idx, ack_line)
            self._live_acked_ring.append((idx, ack_line))

        if ack_idx is None:
            return
        if emit_sent:
            self.ui_q.put(GcodeSentEvent(int(ack_idx), ack_line))
        self.ui_q.put(GcodeAckedEvent(int(ack_idx)))
        self.ui_q.put(ProgressEvent(int(ack_idx) + 1, len(self._gcode)))
        if ack_byte_offset is not None and stream_file_size_bytes > 0:
            self.ui_q.put(
                ProgressBytesEvent(
                    min(int(ack_byte_offset), int(stream_file_size_bytes)),
                    int(stream_file_size_bytes),
                )
            )
    def _start_stream_tool_change_locked(
        self,
        item: StreamPendingItem,
        *,
        tool_name: str,
    ) -> tuple[int | None, str]:
        idx = item.idx if item.idx is not None else self._send_index
        self._stream_pending_item = None
        self._stream_tool_change_pending = StreamPendingItem(
            line=item.line,
            is_gcode=True,
            idx=idx,
            file_end_offset=item.file_end_offset,
        )
        self._stream_tool_change_name = str(tool_name or "")
        self._stream_tool_change_active = True
        self._paused = True
        return idx, self._stream_tool_change_name

    def complete_stream_tool_change(self, success: bool, reason: str | None = None) -> None:
        pending_item: StreamPendingItem | None = None
        still_streaming = False
        with self._stream_lock:
            pending_item = self._stream_tool_change_pending
            self._stream_tool_change_pending = None
            self._stream_tool_change_name = ""
            self._stream_tool_change_active = False
            still_streaming = bool(self._streaming)
            self._paused = False
        if pending_item is None:
            return
        if not still_streaming:
            return
        if success:
            self._ack_handled_stream_line(pending_item)
            self.ui_q.put(StreamStateEvent("running", None))
            self._signal_tx_activity()
            return
        detail = str(reason or "").strip() or "Tool change workflow canceled."
        detail_lower = detail.lower()
        if "cancel" in detail_lower:
            with self._stream_lock:
                self._streaming = False
                self._paused = False
            self.ui_q.put(("log", f"[stream] Tool change canceled: {detail}"))
            self.ui_q.put(StreamStateEvent("stopped", None))
            return
        idx = pending_item.idx
        line_text = pending_item.line
        msg = self._format_stream_error(f"Tool change failed: {detail}", idx, line_text)
        with self._stream_lock:
            self._streaming = False
        self.ui_q.put(StreamErrorEvent(msg, idx, line_text, self._gcode_name))
        self.ui_q.put(("log", f"[stream error] {msg}"))
        self.ui_q.put(StreamStateEvent("error", detail))

    def _handle_stream_source_read_failure(self, failure: _StreamSourceReadError) -> None:
        source_name = ""
        if failure.source_path:
            source_name = os.path.basename(failure.source_path) or failure.source_path
        detail = str(failure.error or "").strip() or "unknown I/O error"
        line_label = max(1, int(failure.idx) + 1)
        if source_name:
            message = (
                f"File read failed during streaming ({source_name}, line {line_label}): {detail}"
            )
        else:
            message = f"File read failed during streaming (line {line_label}): {detail}"
        logger.warning("Stream source read failed: %s", message, exc_info=failure.error)
        with self._stream_lock:
            self._streaming = False
            self._paused = False
            self._stream_pending_item = None
            self._resume_preamble.clear()
        self._emit_buffer_fill()
        self.ui_q.put(StreamErrorEvent(message, failure.idx, None, self._gcode_name))
        self.ui_q.put(("log", f"[stream error] {message}"))
        self.ui_q.put(StreamStateEvent("error", "File read failed"))

    def _validate_stream_item_locked(
        self,
        item: StreamPendingItem,
    ) -> tuple[StreamPendingItem, bytes, int, bool | None] | None:
        raw_line = item.line
        use_cached_payload = False
        use_cached_pause_reason = False
        use_cached_spindle_state = False
        cached_payload: bytes | None = None
        cached_pause_reason: str | None = None
        cached_spindle_state: bool | None = None
        if item.is_gcode and item.idx is not None and not self._dry_run_sanitize:
            idx = item.idx
            payload_cache = getattr(self, "_gcode_payload_cache", None)
            if payload_cache is not None and 0 <= idx < len(payload_cache):
                cached_payload = payload_cache[idx]
                use_cached_payload = True
            pause_cache = getattr(self, "_gcode_pause_reason_cache", None)
            if pause_cache is not None and 0 <= idx < len(pause_cache):
                cached_pause_reason = pause_cache[idx]
                use_cached_pause_reason = True
            spindle_cache = getattr(self, "_gcode_spindle_state_cache", None)
            if spindle_cache is not None and 0 <= idx < len(spindle_cache):
                cached_spindle_state = spindle_cache[idx]
                use_cached_spindle_state = True
        spindle_state = (
            cached_spindle_state if use_cached_spindle_state else self._detect_spindle_state(raw_line)
        )
        line = self._sanitize_stream_line(raw_line)
        if item.is_gcode and line.startswith("$"):
            msg = self._format_stream_error(
                "GRBL system commands ($...) are not allowed inside jobs",
                item.idx,
                line,
            )
            self._pause_stream(reason="invalid system command")
            self.ui_q.put(StreamErrorEvent(msg, item.idx, line, self._gcode_name))
            self.ui_q.put(("log", f"[stream error] {msg}"))
            return None
        item = StreamPendingItem(
            line=line,
            is_gcode=item.is_gcode,
            idx=item.idx,
            file_end_offset=item.file_end_offset,
        )
        if item.is_gcode and item.idx is not None and self._pause_after_idx is None:
            reason = (
                cached_pause_reason if use_cached_pause_reason else self._pause_reason_for_line(line)
            )
            if reason:
                self._pause_after_idx = item.idx
                self._pause_after_reason = reason

        payload = cached_payload if use_cached_payload else self._build_line_payload(line)
        if payload is None:
            msg = self._format_stream_error("Non-ASCII characters in line", item.idx, line)
            self._pause_stream(reason="invalid characters")
            self.ui_q.put(StreamErrorEvent(msg, item.idx, line, self._gcode_name))
            self.ui_q.put(("log", f"[stream error] {msg}"))
            return None

        line_len = len(payload)
        if line_len > MAX_LINE_LENGTH:
            msg = self._format_stream_error(
                f"Line too long ({line_len} > {MAX_LINE_LENGTH})",
                item.idx,
                line,
            )
            self._pause_stream(reason="line too long")
            self.ui_q.put(StreamErrorEvent(msg, item.idx, line, self._gcode_name))
            self.ui_q.put(("log", f"[stream error] {msg}"))
            return None

        usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
        can_fit = (self._stream_buf_used + line_len) <= usable
        if not can_fit and self._stream_buf_used > 0:
            self._stream_pending_item = item
            return None

        return item, payload, line_len, spindle_state

    @staticmethod
    def _detect_spindle_state(line: str) -> bool | None:
        try:
            return cast(bool | None, SpindleCommandDetector.detect_state_change(line))
        except Exception:
            return None

    def _reserve_stream_item_locked(
        self,
        item: StreamPendingItem,
        line_len: int,
    ) -> StreamQueueItem:
        idx = item.idx
        self._stream_pending_item = None
        if item.is_gcode:
            idx = self._send_index
            self._send_index += 1

        queue_item = StreamQueueItem(
            line_len=line_len,
            is_gcode=item.is_gcode,
            idx=idx,
            line=item.line,
            queued_ts=time.time(),
            file_end_offset=item.file_end_offset,
        )
        self._stream_buf_used += line_len
        self._stream_line_queue.append(queue_item)
        return queue_item

    def _rollback_reserved_stream_locked(self, *, is_gcode: bool, line_len: int) -> None:
        if is_gcode and self._send_index > 0:
            self._send_index -= 1
        if self._stream_line_queue:
            try:
                last_len = self._stream_line_queue.pop().line_len
            except Exception:
                last_len = line_len
            self._stream_buf_used = max(0, self._stream_buf_used - last_len)
        else:
            self._stream_buf_used = max(0, self._stream_buf_used - line_len)

    def _stream_send_invalidated(self, stream_token: int) -> bool:
        return (
            self._abort_writes.is_set()
            or stream_token != self._stream_token
            or not self._streaming
            or self._paused
        )

    def _process_stream_queue(self) -> None:
        """Process streaming queue - fill GRBL buffer."""
        while True:
            if self._stream_loop_blocked():
                break
            read_failure: _StreamSourceReadError | None = None
            prefetched_snapshot: tuple[int, object, int, str] | None = None
            prefetched_item: StreamPendingItem | None = None

            with self._stream_lock:
                if self._stream_loop_blocked():
                    break
                stream_token = self._stream_token
                prefetched_snapshot = self._snapshot_file_backed_stream_fetch_locked()
                if prefetched_snapshot is None:
                    try:
                        item = self._next_stream_item_locked()
                    except _StreamSourceReadError as exc:
                        read_failure = exc
                        item = None
                else:
                    item = None
            if prefetched_snapshot is not None:
                fetch_token, fetch_source, fetch_idx, fetch_source_path = prefetched_snapshot
                try:
                    prefetched_item = self._read_prefetched_stream_item(
                        source=fetch_source,
                        idx=fetch_idx,
                        source_path=fetch_source_path,
                    )
                except _StreamSourceReadError as exc:
                    read_failure = exc
            if read_failure is not None:
                self._handle_stream_source_read_failure(read_failure)
                break
            handled_item: StreamPendingItem | None = None
            handled_vacuum_on = False
            directive_deferred = False
            tool_change_started = False
            prefetch_invalidated = False
            with self._stream_lock:
                if self._stream_loop_blocked():
                    break
                stream_token = self._stream_token
                if prefetched_snapshot is not None:
                    fetch_token, fetch_source, fetch_idx, _fetch_source_path = prefetched_snapshot
                    if not self._revalidate_prefetched_stream_item_locked(
                        stream_token=fetch_token,
                        source=fetch_source,
                        idx=fetch_idx,
                    ):
                        prefetch_invalidated = True
                    else:
                        item = prefetched_item
                if not prefetch_invalidated:
                    if item is None:
                        break
                    directive = None
                    if item.is_gcode:
                        directive = self._match_stream_directive(item.line)
                    if directive is not None:
                        kind, directive_payload = directive
                        if self._stream_line_queue:
                            self._stream_pending_item = item
                            directive_deferred = True
                        elif kind == "tool_change":
                            idx, tool_name = self._start_stream_tool_change_locked(
                                item,
                                tool_name=str(directive_payload or ""),
                            )
                            self.ui_q.put(StreamStateEvent("paused", None))
                            self.ui_q.put(StreamPauseReasonEvent("tool change"))
                            self.ui_q.put(("stream_tool_change", idx, tool_name))
                            tool_change_started = True
                        else:
                            self._stream_pending_item = None
                            handled_item = item
                            handled_vacuum_on = (kind == "vacuum_on")
                    if not directive_deferred and not tool_change_started and handled_item is None:
                        validated = self._validate_stream_item_locked(item)
                        if validated is None:
                            break
                        item, line_payload, line_len, spindle_state = validated
                        queue_item = self._reserve_stream_item_locked(item, line_len)
            if prefetch_invalidated:
                continue
            if handled_item is not None:
                # Handle vacuum directives in sender space and never transmit to GRBL.
                handled_idx = handled_item.idx
                self._ack_handled_stream_line(handled_item)
                self.ui_q.put(("stream_vacuum_directive", handled_vacuum_on, handled_idx))
                continue
            if directive_deferred or tool_change_started:
                break

            if self._stream_send_invalidated(stream_token):
                with self._stream_lock:
                    self._rollback_reserved_stream_locked(
                        is_gcode=queue_item.is_gcode,
                        line_len=line_len,
                    )
                    self._stream_pending_item = None
                self._emit_buffer_fill()
                break

            if not self._write_line(queue_item.line, line_payload):
                with self._stream_lock:
                    self._rollback_reserved_stream_locked(
                        is_gcode=queue_item.is_gcode,
                        line_len=line_len,
                    )
                    self._stream_pending_item = item
                self._emit_buffer_fill()
                if not self.is_connected():
                    break
                if not (self._abort_writes.is_set() or stream_token != self._stream_token):
                    self._streaming = False
                    self._paused = False
                    self.ui_q.put(StreamStateEvent("error", "Write failed"))
                break

            if not queue_item.is_gcode and self._resume_preamble:
                self._resume_preamble.popleft()
            self._record_tx_line()
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()
            if spindle_state is not None:
                self.ui_q.put(("spindle_state", bool(spindle_state), queue_item.idx))
            if queue_item.is_gcode and queue_item.idx is not None:
                self.ui_q.put(GcodeSentEvent(int(queue_item.idx), queue_item.line))

        with self._stream_lock:
            send_index = self._send_index
            ack_index = self._ack_index
            pending = bool(
                self._stream_line_queue or
                self._stream_pending_item or
                self._resume_preamble or
                self._stream_tool_change_pending is not None
            )
            completion_verified, total_lines, line_count_known = (
                self._stream_completion_verified(
                    source=self._gcode,
                    send_index=send_index,
                    ack_index=ack_index,
                )
            )

        if (self._streaming and
            not pending and
            completion_verified):
            stream_file_size = max(
                0, int(getattr(self, "_stream_file_size_bytes", 0) or 0)
            )
            if stream_file_size > 0:
                with self._stream_lock:
                    self._ack_byte_offset = int(stream_file_size)
                self.ui_q.put(
                    ProgressBytesEvent(int(stream_file_size), int(stream_file_size))
                )
            self._streaming = False
            self.ui_q.put(
                StreamCompletionEofEvent(
                    bool(completion_verified),
                    int(total_lines),
                    bool(line_count_known),
                    int(ack_index),
                    int(send_index),
                )
            )
            self.ui_q.put(StreamStateEvent("done", None))
            logger.info(
                "Streaming complete (verified_eof=%s, total_lines=%d, "
                "last_acked_index=%d, send_index=%d, total_known=%s)",
                bool(completion_verified),
                int(total_lines),
                int(ack_index),
                int(send_index),
                bool(line_count_known),
            )

    def _purge_pending_jogs(self) -> None:
        if self._purge_jog_queue.is_set():
            self._purge_jog_queue.clear()
            pending: list[str] = []
            pending_sources: list[str | None] = []
            pending_trackers = []
            try:
                with self._stream_lock:
                    while True:
                        pending.append(self._outgoing_q.get_nowait())
                        pending_sources.append(
                            self._manual_source_queue.popleft() if self._manual_source_queue else None
                        )
                        pending_trackers.append(
                            self._manual_tracker_queue.popleft() if self._manual_tracker_queue else None
                        )
            except queue.Empty:
                pass
            kept: list[tuple[str, str | None, ManualCommandResultTracker | None]] = []
            for idx, cmd in enumerate(pending):
                source = pending_sources[idx] if idx < len(pending_sources) else None
                tracker = pending_trackers[idx] if idx < len(pending_trackers) else None
                if isinstance(cmd, str) and cmd.lstrip().upper().startswith("$J="):
                    self._resolve_manual_tracker(
                        tracker,
                        success=False,
                        error="Manual jog command was purged before completion.",
                    )
                    continue
                kept.append((cmd, source, tracker))
            with self._stream_lock:
                for cmd, source, tracker in kept:
                    self._enqueue_manual_command(cmd, source, tracker=tracker)
            if self._manual_pending_item is not None:
                line = self._manual_pending_item.line
                if isinstance(line, str) and line.lstrip().upper().startswith("$J="):
                    self._resolve_manual_tracker(
                        getattr(self._manual_pending_item, "tracker", None),
                        success=False,
                        error="Manual jog command was purged before completion.",
                    )
                    self._manual_pending_item = None
            self._signal_tx_activity()

    def _manual_loop_blocked(self) -> bool:
        if self._streaming or self._paused:
            allow_tool_change_macro = bool(
                self._streaming
                and self._paused
                and bool(getattr(self, "_stream_tool_change_active", False))
            )
            if not allow_tool_change_macro:
                return True
        if not self.is_connected():
            return True
        if self._abort_writes.is_set() and not self._alarm_active:
            return True
        return False

    def _line_allowed_during_alarm(self, line: str) -> bool:
        if not self._alarm_active:
            return True
        cmd_upper = line.strip().upper()
        allowed = cmd_upper.startswith("$X") or cmd_upper.startswith("$H")
        if not allowed:
            self._clear_outgoing()
        return allowed

    def _manual_line_too_long(self, line: str, line_len: int) -> bool:
        if line_len <= MAX_LINE_LENGTH:
            return False
        self.ui_q.put((
            "log",
            f"[manual] Line too long ({line_len} > {MAX_LINE_LENGTH}): {line}",
        ))
        self._resolve_manual_tracker(
            getattr(self._manual_pending_item, "tracker", None),
            success=False,
            error=f"Manual command line too long ({line_len} > {MAX_LINE_LENGTH}).",
        )
        self._manual_pending_item = None
        return True

    def _rollback_reserved_manual_locked(self, line_len: int) -> None:
        if self._stream_line_queue:
            try:
                last_len = self._stream_line_queue.pop().line_len
            except Exception:
                last_len = line_len
            self._stream_buf_used = max(0, self._stream_buf_used - last_len)
        else:
            self._stream_buf_used = max(0, self._stream_buf_used - line_len)

    def _reserve_manual_slot_locked(
        self,
        line: str,
        payload: bytes,
        line_len: int,
        source: str | None,
        tracker=None,
    ) -> tuple[bool, bool, int]:
        usable = max(1, int(self._rx_window) - RX_BUFFER_SAFETY)
        if line_len > usable and self._stream_buf_used <= 0:
            self._manual_pending_item = None
            return True, False, usable

        can_fit = (self._stream_buf_used + line_len) <= usable
        if not can_fit and self._stream_buf_used > 0:
            self._manual_pending_item = ManualPendingItem(
                line=line,
                payload=payload,
                line_len=line_len,
                source=source,
                tracker=tracker,
            )
            return False, True, usable

        self._manual_pending_item = None
        self._stream_buf_used += line_len
        self._stream_line_queue.append(
            StreamQueueItem(
                line_len=line_len,
                is_gcode=False,
                idx=None,
                line=line,
                manual_source=source,
                queued_ts=time.time(),
                manual_tracker=tracker,
            )
        )
        return False, False, usable

    def _process_manual_queue(self) -> None:
        """Process immediate command queue with buffer pacing."""
        self._purge_pending_jogs()
        while True:
            if self._manual_loop_blocked():
                return
            payload: bytes | None = None
            line_len: int
            source: str | None
            tracker = None
            if self._manual_pending_item is not None:
                pending_item = self._manual_pending_item
                line = pending_item.line
                payload = pending_item.payload
                line_len = pending_item.line_len
                source = pending_item.source
                tracker = getattr(pending_item, "tracker", None)
            else:
                with self._stream_lock:
                    try:
                        line = self._outgoing_q.get_nowait()
                    except queue.Empty:
                        return
                    source = self._manual_source_queue.popleft() if self._manual_source_queue else None
                    tracker = self._manual_tracker_queue.popleft() if self._manual_tracker_queue else None
                if not self._line_allowed_during_alarm(line):
                    self._resolve_manual_tracker(
                        tracker,
                        success=False,
                        error="Manual command was blocked during alarm state.",
                    )
                    continue

                line = line.strip()
                if not line:
                    self._resolve_manual_tracker(
                        tracker,
                        success=False,
                        error="Manual command was empty.",
                    )
                    continue
                payload = self._build_line_payload(line)
                if payload is None:
                    self.ui_q.put((
                        "log",
                        f"[manual] Non-ASCII characters in line: {line}",
                    ))
                    self._resolve_manual_tracker(
                        tracker,
                        success=False,
                        error="Manual command contains non-ASCII characters.",
                    )
                    self._manual_pending_item = None
                    continue
                line_len = len(payload)
            if source:
                self._last_manual_source = source

            allowed_alarm_cmd = self._line_allowed_during_alarm(line)
            if not allowed_alarm_cmd:
                if self._manual_pending_item is not None:
                    self._manual_pending_item = None
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error="Manual command was blocked during alarm state.",
                )
                continue

            if self._manual_line_too_long(line, line_len):
                continue

            assert payload is not None
            with self._stream_lock:
                drop_for_buffer, deferred, usable = self._reserve_manual_slot_locked(
                    line=line,
                    payload=payload,
                    line_len=line_len,
                    source=source,
                    tracker=tracker,
                )

            if deferred:
                break
            if drop_for_buffer:
                self.ui_q.put((
                    "log",
                    f"[manual] Line too long for buffer ({line_len} > {usable}): {line}",
                ))
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error=f"Manual command line too long for GRBL buffer ({line_len} > {usable}).",
                )
                continue

            if self._abort_writes.is_set() and not allowed_alarm_cmd:
                with self._stream_lock:
                    self._rollback_reserved_manual_locked(line_len)
                    self._manual_pending_item = None
                self._resolve_manual_tracker(
                    tracker,
                    success=False,
                    error="Manual command was aborted before transmission.",
                )
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
                    self.clear_watchdog_ignore("settings_dump")
                with self._stream_lock:
                    self._rollback_reserved_manual_locked(line_len)
                    if self.is_connected():
                        self._manual_pending_item = ManualPendingItem(
                            line=line,
                            payload=payload,
                            line_len=line_len,
                            source=source,
                            tracker=tracker,
                        )
                    else:
                        self._resolve_manual_tracker(
                            tracker,
                            success=False,
                            error="Manual command send failed after disconnect.",
                        )
                        self._manual_pending_item = None
                self._emit_buffer_fill()
                break

            self.ui_q.put(("log_tx", line))
            self._record_tx_line()
            self._record_tx_bytes(line_len)
            self._emit_buffer_fill()
            self._signal_tx_activity()

    def wait_for_manual_completion(self, timeout_s: float = 30.0) -> bool:
        """Block until manual/immediate commands finish."""
        start = time.time()
        wake_event = getattr(self, "_tx_activity_evt", None)
        while True:
            manual_pending = bool(self.manual_queue_busy())
            with self._stream_lock:
                pending = (
                    manual_pending
                    or bool(self._resume_preamble)
                )
            if not pending:
                return True
            if timeout_s and (time.time() - start) > timeout_s:
                return False
            if wake_event is not None:
                try:
                    signaled = bool(wake_event.wait(0.05))
                    wake_event.clear()
                    if signaled:
                        continue
                    # Yield briefly even on timeout so callers monkeypatching sleep
                    # can still drive completion without a zero-sleep spin.
                    time.sleep(0.005)
                    continue
                except Exception:
                    pass
            time.sleep(0.05)
    
