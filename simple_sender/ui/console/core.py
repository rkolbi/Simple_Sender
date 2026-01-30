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

"""Console widget with batched logging and filtering.

This module provides a console for displaying logs with automatic batching
to prevent UI blocking, filtering capabilities, and command history.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Set
import logging
from collections import deque

from simple_sender.utils.constants import (
    MAX_CONSOLE_LINES,
    CONSOLE_BATCH_DELAY_MS,
)

logger = logging.getLogger(__name__)


class Console(ttk.Frame):
    """Console widget for displaying logs and manual command entry.
    
    Features:
    - Batched log updates (prevents UI blocking)
    - Line limit with automatic trimming
    - Log filtering
    - Command history
    - Auto-scroll option
    - Read-only log area
    - Manual command entry
    """
    
    def __init__(
        self,
        parent: tk.Widget,
        on_command: Optional[Callable[[str], None]] = None
    ):
        """Initialize console.
        
        Args:
            parent: Parent widget
            on_command: Callback when user enters command
        """
        super().__init__(parent)
        
        self.on_command = on_command
        
        # Create text widget for log display
        self.text = tk.Text(
            self,
            wrap="word",
            height=12,
            state="disabled",
            undo=False
        )
        
        # Scrollbar
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.vsb.set)
        
        # Command entry
        self.entry_var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.entry_var)
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Up>", self._on_history_up)
        self.entry.bind("<Down>", self._on_history_down)
        
        # Layout
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # State
        self._log_buffer: deque[str] = deque()
        self._flush_scheduled = False
        self._auto_scroll = True
        self._line_count = 0
        
        # Filtering
        self._filter_keywords: Set[str] = set()
        self._show_rx = True
        self._show_tx = True
        self._show_info = True
        self._show_alarm = True
        
        # Command history
        self._command_history: deque[str] = deque(maxlen=100)
        self._history_index = -1
    
    def log(self, message: str, tag: Optional[str] = None) -> None:
        """Add message to log buffer.
        
        Messages are batched and flushed periodically to prevent
        UI blocking during high-frequency logging.
        
        Args:
            message: Message to log
            tag: Optional tag for filtering/formatting
        """
        # Apply filtering
        if not self._should_show(message, tag):
            return
        
        # Format message with tag if provided
        if tag:
            formatted = f"[{tag}] {message}"
        else:
            formatted = message
        
        # Add to buffer
        self._log_buffer.append(formatted)
        
        # Schedule flush if not already scheduled
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.after(CONSOLE_BATCH_DELAY_MS, self._flush_logs)
    
    def log_immediate(self, message: str, tag: Optional[str] = None) -> None:
        """Log message immediately without batching.
        
        Use for critical messages that must appear immediately.
        
        Args:
            message: Message to log
            tag: Optional tag for filtering/formatting
        """
        if not self._should_show(message, tag):
            return
        
        if tag:
            formatted = f"[{tag}] {message}"
        else:
            formatted = message
        
        self._append_text(formatted + "\n")
        self._trim_if_needed()
    
    def clear(self) -> None:
        """Clear all console contents."""
        self._log_buffer.clear()
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")
        self._line_count = 0
    
    def set_auto_scroll(self, enabled: bool) -> None:
        """Enable or disable auto-scrolling.
        
        Args:
            enabled: Whether to auto-scroll to bottom
        """
        self._auto_scroll = enabled
    
    def set_filter_keywords(self, keywords: Set[str]) -> None:
        """Set keywords to filter out.
        
        Args:
            keywords: Set of keywords to filter
        """
        self._filter_keywords = keywords.copy()
    
    def set_show_rx(self, show: bool) -> None:
        """Show/hide RX messages.
        
        Args:
            show: Whether to show RX messages
        """
        self._show_rx = show
    
    def set_show_tx(self, show: bool) -> None:
        """Show/hide TX messages.
        
        Args:
            show: Whether to show TX messages
        """
        self._show_tx = show
    
    def set_show_info(self, show: bool) -> None:
        """Show/hide info messages.
        
        Args:
            show: Whether to show info messages
        """
        self._show_info = show
    
    def set_show_alarm(self, show: bool) -> None:
        """Show/hide alarm messages.
        
        Args:
            show: Whether to show alarm messages
        """
        self._show_alarm = show
    
    def get_text(self) -> str:
        """Get all console text.
        
        Returns:
            Complete console contents
        """
        return self.text.get("1.0", "end-1c")
    
    def export_to_file(self, filepath: str) -> None:
        """Export console contents to file.
        
        Args:
            filepath: Destination file path
        """
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(self.get_text())
            logger.info(f"Console exported to {filepath}")
        except IOError as e:
            logger.error(f"Failed to export console: {e}")
    
    # ========================================================================
    # INTERNAL METHODS
    # ========================================================================
    
    def _should_show(self, message: str, tag: Optional[str]) -> bool:
        """Check if message should be displayed based on filters.
        
        Args:
            message: Message text
            tag: Message tag
            
        Returns:
            True if message should be shown
        """
        # Check tag filters
        if tag == "rx" and not self._show_rx:
            return False
        if tag == "tx" and not self._show_tx:
            return False
        if tag == "info" and not self._show_info:
            return False
        if tag == "alarm" and not self._show_alarm:
            return False
        
        # Check keyword filters
        message_lower = message.lower()
        for keyword in self._filter_keywords:
            if keyword.lower() in message_lower:
                return False
        
        return True
    
    def _flush_logs(self) -> None:
        """Flush buffered log messages to text widget."""
        self._flush_scheduled = False
        
        if not self._log_buffer:
            return
        
        # Collect all buffered messages
        messages = []
        while self._log_buffer:
            messages.append(self._log_buffer.popleft())
        
        if not messages:
            return
        
        # Single batch update
        combined = "\n".join(messages) + "\n"
        self._append_text(combined)
        self._trim_if_needed()
    
    def _append_text(self, text: str) -> None:
        """Append text to console.
        
        Args:
            text: Text to append
        """
        self.text.config(state="normal")
        self.text.insert("end", text)
        self.text.config(state="disabled")
        
        # Count lines
        self._line_count += text.count("\n")
        
        # Auto-scroll if enabled
        if self._auto_scroll:
            self.text.see("end")
    
    def _trim_if_needed(self) -> None:
        """Trim console to maximum line limit."""
        if self._line_count <= MAX_CONSOLE_LINES:
            return
        
        lines_to_remove = self._line_count - MAX_CONSOLE_LINES
        
        self.text.config(state="normal")
        self.text.delete("1.0", f"{lines_to_remove + 1}.0")
        self.text.config(state="disabled")
        
        self._line_count = MAX_CONSOLE_LINES
    
    def _on_entry_return(self, event: tk.Event) -> str:
        """Handle Return key in entry widget.
        
        Args:
            event: Key event
            
        Returns:
            "break" to prevent default behavior
        """
        command = self.entry_var.get().strip()
        if not command:
            return "break"
        
        # Add to history
        if not self._command_history or self._command_history[-1] != command:
            self._command_history.append(command)
        self._history_index = -1
        
        # Clear entry
        self.entry_var.set("")
        
        # Log command
        self.log(f"> {command}", tag="tx")
        
        # Execute callback
        if callable(self.on_command):
            try:
                self.on_command(command)
            except Exception as e:
                logger.error(f"Command callback error: {e}")
                self.log(f"Error: {e}", tag="error")
        
        return "break"
    
    def _on_history_up(self, event: tk.Event) -> str:
        """Navigate up in command history.
        
        Args:
            event: Key event
            
        Returns:
            "break" to prevent default behavior
        """
        if not self._command_history:
            return "break"
        
        # Initialize or move up in history
        if self._history_index == -1:
            self._history_index = len(self._command_history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        
        # Set entry text
        if 0 <= self._history_index < len(self._command_history):
            self.entry_var.set(self._command_history[self._history_index])
            self.entry.icursor(tk.END)
        
        return "break"
    
    def _on_history_down(self, event: tk.Event) -> str:
        """Navigate down in command history.
        
        Args:
            event: Key event
            
        Returns:
            "break" to prevent default behavior
        """
        if not self._command_history or self._history_index == -1:
            return "break"
        
        self._history_index += 1
        
        if self._history_index >= len(self._command_history):
            # Clear entry
            self._history_index = -1
            self.entry_var.set("")
        else:
            # Set entry text
            self.entry_var.set(self._command_history[self._history_index])
            self.entry.icursor(tk.END)
        
        return "break"
