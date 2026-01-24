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

"""G-code text viewer widget with syntax highlighting.

This module provides a read-only text viewer for G-code with line numbers,
chunked loading for large files, and highlighting for sent/acked/current lines.
"""

import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Callable, Tuple
import logging

from ..utils.constants import (
    LINE_NUMBER_OFFSET,
    GCODE_VIEWER_CHUNK_SIZE_SMALL,
    GCODE_VIEWER_CHUNK_SIZE_MEDIUM,
    GCODE_VIEWER_CHUNK_SIZE_LARGE,
    GCODE_VIEWER_SMALL_FILE_THRESHOLD,
    GCODE_VIEWER_LARGE_FILE_THRESHOLD,
    COLOR_GCODE_SENT,
    COLOR_GCODE_ACKED,
    COLOR_GCODE_CURRENT,
    COLOR_GCODE_TEXT,
    COLOR_GCODE_BG,
)

logger = logging.getLogger(__name__)


def reset_gcode_view_for_run(app):
    if not hasattr(app, "gview") or app.gview.lines_count <= 0:
        return
    app._clear_pending_ui_updates()
    app.gview.clear_highlights()
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1
    app.gview.highlight_current(0)


class GcodeViewer(ttk.Frame):
    """Text widget for displaying G-code with line numbers and highlighting.
    
    Features:
    - Line numbers
    - Chunked loading for large files
    - Syntax highlighting (sent/acked/current)
    - Auto-scrolling to current line
    - Read-only display
    
    The viewer supports three highlight states:
    - Sent: Lines sent to GRBL but not yet acknowledged
    - Acked: Lines acknowledged by GRBL
    - Current: The line currently being processed
    """
    
    def __init__(self, parent: tk.Widget):
        """Initialize G-code viewer.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        
        # Create text widget
        self.text = tk.Text(self, wrap="none", height=18, undo=False)
        self.text.configure(
            background=COLOR_GCODE_BG,
            foreground=COLOR_GCODE_TEXT,
            insertbackground=COLOR_GCODE_TEXT,
        )
        
        # Create scrollbars
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        
        # Layout
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # Configure tags for highlighting
        self.text.tag_configure("sent", background=COLOR_GCODE_SENT, foreground=COLOR_GCODE_TEXT)
        self.text.tag_configure("acked", background=COLOR_GCODE_ACKED, foreground=COLOR_GCODE_TEXT)
        self.text.tag_configure("current", background=COLOR_GCODE_CURRENT, foreground=COLOR_GCODE_TEXT)
        
        # State
        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        
        # Chunked insertion state
        self._insert_after_id: Optional[str] = None
        self._insert_lines: List[str] = []
        self._insert_index = 0
        self._insert_chunk_size = GCODE_VIEWER_CHUNK_SIZE_SMALL
        self._insert_done_cb: Optional[Callable] = None
        self._insert_progress_cb: Optional[Callable[[int, int], None]] = None
    
    def set_lines(self, lines: List[str]) -> None:
        """Load G-code lines with adaptive chunk size.
        
        Automatically determines optimal chunk size based on file size.
        
        Args:
            lines: List of G-code lines to display
        """
        # Determine chunk size based on file size
        total_lines = len(lines)
        if total_lines < GCODE_VIEWER_SMALL_FILE_THRESHOLD:
            chunk_size = GCODE_VIEWER_CHUNK_SIZE_SMALL
        elif total_lines < GCODE_VIEWER_LARGE_FILE_THRESHOLD:
            chunk_size = GCODE_VIEWER_CHUNK_SIZE_MEDIUM
        else:
            chunk_size = GCODE_VIEWER_CHUNK_SIZE_LARGE
        
        self._start_chunk_insert(lines, chunk_size=chunk_size)
    
    def set_lines_chunked(
        self,
        lines: List[str],
        chunk_size: int = GCODE_VIEWER_CHUNK_SIZE_SMALL,
        on_done: Optional[Callable] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Load G-code lines with custom chunk size and callbacks.
        
        Args:
            lines: List of G-code lines to display
            chunk_size: Number of lines per chunk
            on_done: Callback when loading completes
            on_progress: Callback for progress updates (current, total)
        """
        self._start_chunk_insert(
            lines,
            chunk_size=chunk_size,
            on_done=on_done,
            on_progress=on_progress
        )
    
    def clear(self) -> None:
        """Clear all G-code and reset state."""
        self._cancel_chunk_insert()
        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")
    
    def clear_highlights(self) -> None:
        """Clear all highlighting tags."""
        self.text.config(state="normal")
        self.text.tag_remove("sent", "1.0", "end")
        self.text.tag_remove("acked", "1.0", "end")
        self.text.tag_remove("current", "1.0", "end")
        self.text.config(state="disabled")
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
    
    def mark_sent_upto(self, idx: int) -> None:
        """Mark lines as sent up to specified index.
        
        Args:
            idx: Last line index to mark as sent (0-based)
        """
        if self.lines_count <= 0 or idx < 0:
            return
        
        idx = min(idx, self.lines_count - 1)
        if idx <= self._sent_upto:
            return
        
        start_line = self._sent_upto + 1 + LINE_NUMBER_OFFSET
        end_line = idx + LINE_NUMBER_OFFSET

        self.text.config(state="normal")
        self.text.tag_add("sent", f"{start_line}.0", f"{end_line + 1}.0")
        self.text.config(state="disabled")
        self._sent_upto = idx
    
    def mark_acked_upto(self, idx: int) -> None:
        """Mark lines as acknowledged up to specified index.
        
        Changes sent highlighting to acked highlighting.
        
        Args:
            idx: Last line index to mark as acked (0-based)
        """
        if self.lines_count <= 0 or idx < 0:
            return
        
        idx = min(idx, self.lines_count - 1)
        if idx <= self._acked_upto:
            return
        
        start_line = self._acked_upto + 1 + LINE_NUMBER_OFFSET
        end_line = idx + LINE_NUMBER_OFFSET

        self.text.config(state="normal")
        self.text.tag_remove("sent", f"{start_line}.0", f"{end_line + 1}.0")
        self.text.tag_add("acked", f"{start_line}.0", f"{end_line + 1}.0")
        self.text.config(state="disabled")
        
        self._acked_upto = idx
        if self._sent_upto < idx:
            self._sent_upto = idx
    
    def mark_sent(self, idx: int) -> None:
        """Mark single line as sent.
        
        Args:
            idx: Line index to mark (0-based)
        """
        self.mark_sent_upto(idx)
    
    def mark_acked(self, idx: int) -> None:
        """Mark single line as acknowledged.
        
        Args:
            idx: Line index to mark (0-based)
        """
        self.mark_acked_upto(idx)
    
    def highlight_current(self, idx: int) -> None:
        """Highlight current line being processed.
        
        Removes previous current highlight and adds new one.
        Auto-scrolls to keep current line visible.
        
        Args:
            idx: Line index to highlight (0-based, -1 to clear)
        """
        if idx == self._current_idx:
            return
        
        self.text.config(state="normal")
        
        # Remove previous highlight
        if 0 <= self._current_idx < self.lines_count:
            start, end = self._line_range(self._current_idx)
            self.text.tag_remove("current", start, end)
        
        # Add new highlight
        if 0 <= idx < self.lines_count:
            start, end = self._line_range(idx)
            self.text.tag_add("current", start, end)
            self.text.see(start)  # Auto-scroll
            self._current_idx = idx
        else:
            self._current_idx = -1
        
        self.text.config(state="disabled")
    
    # ========================================================================
    # INTERNAL METHODS
    # ========================================================================
    
    def _line_range(self, idx: int) -> Tuple[str, str]:
        """Get text widget range for line index.
        
        Args:
            idx: Line index (0-based)
            
        Returns:
            Tuple of (start_pos, end_pos) in text widget notation
        """
        line_no = idx + LINE_NUMBER_OFFSET
        start = f"{line_no}.0"
        end = f"{line_no + 1}.0"
        return start, end
    
    def _cancel_chunk_insert(self) -> None:
        """Cancel ongoing chunk insertion."""
        if self._insert_after_id is not None:
            try:
                self.after_cancel(self._insert_after_id)
            except Exception as e:
                logger.warning(f"Failed to cancel chunk insert: {e}")
        
        self._insert_after_id = None
        self._insert_lines = []
        self._insert_index = 0
        self._insert_done_cb = None
        self._insert_progress_cb = None
    
    def _start_chunk_insert(
        self,
        lines: List[str],
        chunk_size: int = GCODE_VIEWER_CHUNK_SIZE_SMALL,
        on_done: Optional[Callable] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Start chunked insertion of lines.
        
        Args:
            lines: Lines to insert
            chunk_size: Lines per chunk
            on_done: Completion callback
            on_progress: Progress callback (current, total)
        """
        self._cancel_chunk_insert()
        
        self.lines_count = len(lines)
        self._insert_lines = lines
        self._insert_index = 0
        self._insert_chunk_size = max(20, int(chunk_size))
        self._insert_done_cb = on_done
        self._insert_progress_cb = on_progress
        
        # Reset state
        self._sent_upto = -1
        self._acked_upto = -1
        
        # Clear and prepare text widget
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        
        # Report initial progress
        if callable(on_progress):
            on_progress(0, self.lines_count)
        
        # Start insertion
        self._insert_next_chunk()
    
    def _insert_next_chunk(self) -> None:
        """Insert next chunk of lines."""
        if not self._insert_lines:
            self.text.config(state="disabled")
            return
        
        start = self._insert_index
        end = min(start + self._insert_chunk_size, len(self._insert_lines))
        chunk = self._insert_lines[start:end]
        
        if chunk:
            # Format lines with line numbers
            base = start + 1
            lines_out = [f"{base + i:5d}  {ln}" for i, ln in enumerate(chunk)]
            self.text.insert("end", "\n".join(lines_out) + "\n")
        
        self._insert_index = end
        
        # Report progress
        if callable(self._insert_progress_cb):
            self._insert_progress_cb(self._insert_index, len(self._insert_lines))
        
        # Check if done
        if self._insert_index >= len(self._insert_lines):
            self.text.config(state="disabled")
            self._insert_after_id = None
            
            # Clear highlights and show first line
            self.clear_highlights()
            if self.lines_count:
                self.highlight_current(0)
            
            # Call completion callback
            cb = self._insert_done_cb
            self._insert_done_cb = None
            self._insert_progress_cb = None
            if callable(cb):
                cb()
            
            logger.info(f"Loaded {self.lines_count} lines of G-code")
            return
        
        # Schedule next chunk
        self._insert_after_id = self.after(1, self._insert_next_chunk)
