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
from tkinter import ttk
from typing import Any, Callable, cast

from simple_sender.ui.tooltip_policy import resolve_disabled_reason as _policy_disabled_reason
from simple_sender.utils.constants import TOOLTIP_DELAY_MS, TOOLTIP_TIMEOUT_DEFAULT


def _clamp_tooltip_position(
    x: int,
    y: int,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    *,
    margin: int = 8,
) -> tuple[int, int]:
    if screen_width <= 0 or screen_height <= 0:
        return (max(0, x), max(0, y))
    w = max(0, int(width))
    h = max(0, int(height))
    max_x = max(margin, screen_width - w - margin)
    max_y = max(margin, screen_height - h - margin)
    clamped_x = min(max(margin, int(x)), max_x)
    clamped_y = min(max(margin, int(y)), max_y)
    return clamped_x, clamped_y


def _tooltip_wraplength(widget, *, min_px: int = 240, max_px: int = 700, margin: int = 40) -> int | None:
    try:
        screen_width = int(widget.winfo_screenwidth())
    except (AttributeError, TypeError, ValueError, tk.TclError):
        return None
    usable = max(min_px, screen_width - max(0, margin))
    return max(min_px, min(max_px, usable))


def _place_tooltip_window(widget, tip: tk.Toplevel, x: int, y: int) -> None:
    try:
        tip.update_idletasks()
        width = tip.winfo_width() or tip.winfo_reqwidth()
        height = tip.winfo_height() or tip.winfo_reqheight()
        screen_width = int(widget.winfo_screenwidth())
        screen_height = int(widget.winfo_screenheight())
    except (AttributeError, TypeError, ValueError, tk.TclError):
        tip.wm_geometry(f"+{int(x)}+{int(y)}")
        return
    final_x, final_y = _clamp_tooltip_position(
        int(x),
        int(y),
        int(width),
        int(height),
        screen_width,
        screen_height,
    )
    tip.wm_geometry(f"+{final_x}+{final_y}")


class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = TOOLTIP_DELAY_MS):
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self.delay_ms = delay_ms
        self._after_id: Any | None = None
        self._timeout_after_id: Any | None = None
        self._suppress_until_leave = False
        widget.bind("<Enter>", self._schedule_show)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<ButtonPress>", self._on_press, add="+")

    def _schedule_show(self, _event=None):
        if self._suppress_until_leave:
            return
        # Always reset any existing tooltip so movement can update content/position.
        self._hide()
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None):
        self._suppress_until_leave = False
        self._hide()

    def _on_press(self, _event=None):
        self._suppress_until_leave = True
        self._hide()

    def _show(self):
        try:
            if not self.widget.winfo_exists():
                return
        except tk.TclError:
            return
        enabled = True
        owner = _resolve_owner(self.widget, "tooltip_enabled")
        if owner is not None:
            try:
                enabled = bool(owner.tooltip_enabled.get())
            except (AttributeError, TypeError, ValueError, tk.TclError):
                pass
        if not enabled:
            return
        text = _resolve_tooltip_text(self.widget, self.text)
        if not text or self._tip is not None:
            return
        # Position near the current pointer location for consistent placement with other tooltips.
        try:
            x = self.widget.winfo_pointerx() + 16
            y = self.widget.winfo_pointery() + 12
        except tk.TclError:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        try:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            wraplength = _tooltip_wraplength(self.widget)
            label_kwargs: dict[str, Any] = {}
            if wraplength is not None:
                label_kwargs["wraplength"] = wraplength
                label_kwargs["justify"] = "left"
            label = ttk.Label(
                self._tip,
                text=text,
                background="#ffffe0",
                relief="solid",
                padding=(6, 3),
                **label_kwargs,
            )
            label.pack()
            _place_tooltip_window(self.widget, self._tip, int(x), int(y))
            self._schedule_timeout()
        except tk.TclError:
            self._tip = None

    def _schedule_timeout(self):
        if self._timeout_after_id is not None:
            try:
                self.widget.after_cancel(self._timeout_after_id)
            except tk.TclError:
                pass
            self._timeout_after_id = None
        owner = _resolve_owner(self.widget, "tooltip_timeout_sec")
        try:
            duration = float(owner.tooltip_timeout_sec.get()) if owner is not None else TOOLTIP_TIMEOUT_DEFAULT
        except (AttributeError, TypeError, ValueError, tk.TclError):
            duration = TOOLTIP_TIMEOUT_DEFAULT
        if duration <= 0:
            return
        try:
            self._timeout_after_id = self.widget.after(
                int(duration * 1000),
                self._hide,
            )
        except tk.TclError:
            self._timeout_after_id = None

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._timeout_after_id is not None:
            try:
                self.widget.after_cancel(self._timeout_after_id)
            except tk.TclError:
                pass
            self._timeout_after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def set_text(self, text: str):
        self.text = text
        try:
            self.widget._tooltip_text = text
        except AttributeError:
            pass


class _NotebookTabTooltips:
    def __init__(self, notebook):
        self.notebook = notebook
        self.tooltips: dict[str, str] = {}
        self._active_tab: str | None = None
        self._active_text: str = ""
        self._tip: tk.Toplevel | None = None
        self._after_id: Any | None = None
        self._timeout_after_id: Any | None = None
        self._pending_tab: str | None = None
        self._pending_text: str = ""
        self._pending_xy: tuple[int | None, int | None] = (None, None)
        self._poll_after_id: Any | None = None
        self._poll_interval_ms = 120
        self._root = None
        try:
            self._root = notebook.winfo_toplevel()
        except (AttributeError, tk.TclError):
            self._root = notebook
        try:
            self._root.bind("<Motion>", self._on_motion, add="+")
            self._root.bind("<ButtonPress>", self._on_leave, add="+")
        except (AttributeError, tk.TclError):
            notebook.bind("<Motion>", self._on_motion, add="+")
            notebook.bind("<ButtonPress>", self._on_leave, add="+")
        notebook.bind("<<NotebookTabChanged>>", self._on_leave, add="+")
        self._schedule_poll()

    def set_tab_tooltip(self, tab, text: str) -> None:
        if not text:
            return
        tab_id = str(tab)
        self.tooltips[tab_id] = text

    def _is_descendant(self, widget) -> bool:
        current = widget
        for _ in range(8):
            if current is None:
                break
            if current == self.notebook:
                return True
            try:
                current = current.master
            except AttributeError:
                current = None
        return False

    def _tooltips_enabled(self) -> bool:
        owner = _resolve_owner(self.notebook, "tooltip_enabled")
        if owner is not None:
            try:
                return bool(owner.tooltip_enabled.get())
            except (AttributeError, TypeError, ValueError, tk.TclError):
                return True
        return True

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            try:
                self.notebook.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._poll_after_id is not None:
            try:
                self.notebook.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None

    def _schedule_timeout(self) -> None:
        if self._timeout_after_id is not None:
            try:
                self.notebook.after_cancel(self._timeout_after_id)
            except tk.TclError:
                pass
            self._timeout_after_id = None
        owner = _resolve_owner(self.notebook, "tooltip_timeout_sec")
        try:
            duration = float(owner.tooltip_timeout_sec.get()) if owner is not None else TOOLTIP_TIMEOUT_DEFAULT
        except (AttributeError, TypeError, ValueError, tk.TclError):
            duration = TOOLTIP_TIMEOUT_DEFAULT
        if duration <= 0:
            return
        try:
            self._timeout_after_id = self.notebook.after(
                int(duration * 1000),
                self._hide_tip,
            )
        except tk.TclError:
            self._timeout_after_id = None

    def _show_tip(self, text: str, x_root: int | None, y_root: int | None) -> None:
        if not self._tooltips_enabled():
            return
        text = _resolve_tooltip_text(self.notebook, text)
        if not text:
            return
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        try:
            if x_root is None or y_root is None:
                x_root = self.notebook.winfo_pointerx()
                y_root = self.notebook.winfo_pointery()
        except tk.TclError:
            x_root = y_root = None
        if x_root is None or y_root is None:
            return
        x = int(x_root) + 16
        y = int(y_root) + 12
        try:
            self._tip = tk.Toplevel(self.notebook)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            wraplength = _tooltip_wraplength(self.notebook)
            label_kwargs: dict[str, Any] = {}
            if wraplength is not None:
                label_kwargs["wraplength"] = wraplength
                label_kwargs["justify"] = "left"
            label = ttk.Label(
                self._tip,
                text=text,
                background="#ffffe0",
                relief="solid",
                padding=(6, 3),
                **label_kwargs,
            )
            label.pack()
            _place_tooltip_window(self.notebook, self._tip, int(x), int(y))
            self._schedule_timeout()
        except tk.TclError:
            self._tip = None

    def _show_pending(self) -> None:
        self._after_id = None
        if self._pending_tab is None or not self._pending_text:
            return
        if self._pending_tab != self._active_tab or self._pending_text != self._active_text:
            return
        x_root, y_root = self._pending_xy
        self._show_tip(self._pending_text, x_root, y_root)

    def _pointer_over_notebook(self, x_root: int | None, y_root: int | None) -> bool:
        if x_root is None or y_root is None:
            return False
        widget = None
        try:
            widget = self.notebook.winfo_containing(x_root, y_root)
        except tk.TclError:
            widget = None
        if widget is None and self._root is not None:
            try:
                widget = self._root.winfo_containing(x_root, y_root)
            except tk.TclError:
                widget = None
        if widget is None:
            return False
        return self._is_descendant(widget)

    def _tab_id_at_root(self, x_root: int, y_root: int) -> str | None:
        tabs = tuple(str(tab_id) for tab_id in self.notebook.tabs())
        if not tabs:
            return None
        try:
            nx = self.notebook.winfo_rootx()
            ny = self.notebook.winfo_rooty()
        except tk.TclError:
            return None
        for tab_id in tabs:
            try:
                bbox = self.notebook.bbox(tab_id)
            except tk.TclError:
                bbox = None
            if not bbox:
                continue
            bx, by, bw, bh = bbox
            rx0 = nx + bx
            ry0 = ny + by
            rx1 = rx0 + bw
            ry1 = ry0 + bh
            if rx0 <= x_root <= rx1 and ry0 <= y_root <= ry1:
                return tab_id
        try:
            rx = int(x_root - nx)
            ry = int(y_root - ny)
            idx = int(self.notebook.index(f"@{rx},{ry}"))
            if 0 <= idx < len(tabs):
                return tabs[idx]
        except (tk.TclError, TypeError, ValueError):
            pass
        return None

    def _hide_tip(self) -> None:
        self._active_tab = None
        self._active_text = ""
        self._pending_tab = None
        self._pending_text = ""
        self._pending_xy = (None, None)
        self._cancel_pending()
        if self._timeout_after_id is not None:
            try:
                self.notebook.after_cancel(self._timeout_after_id)
            except tk.TclError:
                pass
            self._timeout_after_id = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _on_leave(self, _event=None) -> None:
        self._hide_tip()

    def _process_hover(self, x_root: int | None = None, y_root: int | None = None) -> None:
        if x_root is None or y_root is None:
            try:
                x_root = self.notebook.winfo_pointerx()
                y_root = self.notebook.winfo_pointery()
            except tk.TclError:
                return
        if x_root is None or y_root is None:
            if self._active_tab is not None:
                self._hide_tip()
            return
        tab_id = self._tab_id_at_root(int(x_root), int(y_root))
        if not tab_id:
            if self._active_tab is not None:
                self._hide_tip()
            return
        text = self.tooltips.get(tab_id, "")
        if not text:
            if self._active_tab is not None:
                self._hide_tip()
            return
        if tab_id == self._active_tab and text == self._active_text:
            return
        self._active_tab = tab_id
        self._active_text = text
        self._pending_tab = tab_id
        self._pending_text = text
        self._pending_xy = (x_root, y_root)
        self._cancel_pending()
        try:
            self._after_id = self.notebook.after(TOOLTIP_DELAY_MS, self._show_pending)
        except tk.TclError:
            self._after_id = None

    def _on_motion(self, event) -> None:
        self._process_hover(getattr(event, "x_root", None), getattr(event, "y_root", None))

    def _schedule_poll(self) -> None:
        if self._poll_after_id is not None:
            return
        try:
            self._poll_after_id = self.notebook.after(self._poll_interval_ms, self._poll)
        except tk.TclError:
            self._poll_after_id = None

    def _poll(self) -> None:
        self._poll_after_id = None
        try:
            if not self.notebook.winfo_exists():
                return
        except tk.TclError:
            return
        self._process_hover()
        try:
            self._poll_after_id = self.notebook.after(self._poll_interval_ms, self._poll)
        except tk.TclError:
            self._poll_after_id = None


def apply_tooltip(widget, text: str):
    if not text:
        return
    try:
        widget._tooltip_text = text
    except AttributeError:
        pass
    existing = getattr(widget, "_tooltip", None)
    if isinstance(existing, ToolTip):
        existing.set_text(text)
        return existing
    tip = ToolTip(widget, text)
    try:
        widget._tooltip = tip
    except AttributeError:
        pass
    return tip


def set_tab_tooltip(notebook, tab, text: str):
    if not text:
        return
    handler = getattr(notebook, "_tab_tooltip_handler", None)
    if handler is None or not isinstance(handler, _NotebookTabTooltips):
        handler = _NotebookTabTooltips(notebook)
        try:
            notebook._tab_tooltip_handler = handler
        except AttributeError:
            pass
    handler.set_tab_tooltip(tab, text)


def _widget_state(widget) -> str:
    try:
        return str(widget.cget("state")).lower()
    except (AttributeError, tk.TclError):
        pass
    try:
        state = widget.state()
        if isinstance(state, (list, tuple, set)):
            return "disabled" if "disabled" in state else "normal"
    except (AttributeError, tk.TclError):
        pass
    return "normal"


def _widget_disabled(widget) -> bool:
    return _widget_state(widget) == "disabled"


def _resolve_owner(widget, attr: str):
    try:
        owner = widget.winfo_toplevel()
    except (AttributeError, tk.TclError):
        owner = widget
    for _ in range(8):
        if owner is None:
            break
        if hasattr(owner, attr):
            return owner
        try:
            owner = owner.master
        except AttributeError:
            owner = None
    return None


def _resolve_disabled_reason(widget) -> str | None:
    return cast(str | None, _policy_disabled_reason(widget, _resolve_owner))


def _resolve_tooltip_text(widget, fallback: str) -> str:
    text = getattr(widget, "_tooltip_text", "") or fallback
    if _widget_disabled(widget):
        reason = _resolve_disabled_reason(widget)
        if reason:
            if text:
                return f"Disabled: {reason}\n{text}"
            return f"Disabled: {reason}"
        if text:
            return f"Disabled: {text}"
        return "Disabled"
    return text


def _clean_label(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if text and not text[0].isalnum():
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
    return text


def _default_tooltip_text(widget) -> str | None:
    try:
        cls = widget.winfo_class()
    except (AttributeError, tk.TclError):
        cls = ""
    label = ""
    try:
        label = widget.cget("text") or ""
    except (AttributeError, tk.TclError):
        label = getattr(widget, "_text", "") or getattr(widget, "_label", "") or ""
    label = _clean_label(str(label))
    if cls in ("TButton", "Button"):
        return f"Click to {label.lower()}." if label else "Click to activate."
    if cls in ("TCheckbutton",):
        return f"Toggle {label.lower()}." if label else "Toggle this option."
    if cls in ("TRadiobutton",):
        return f"Select {label.lower()}." if label else "Select an option."
    if cls in ("TEntry", "Entry", "TSpinbox", "Spinbox"):
        return "Enter a value."
    if cls in ("TCombobox",):
        return "Select a value."
    if cls in ("TScale", "Scale"):
        return "Adjust the value."
    if cls in ("Text",):
        return "Read-only text output."
    if cls in ("Treeview",):
        return "Select a row."
    return None


def _walk_widgets(root):
    try:
        children = root.winfo_children()
    except (AttributeError, tk.TclError):
        return
    for child in children:
        yield child
        yield from _walk_widgets(child)


_TOOLTIP_DEFAULT_CLASSES = {
    "TButton",
    "Button",
    "TCheckbutton",
    "TRadiobutton",
    "TEntry",
    "Entry",
    "TSpinbox",
    "Spinbox",
    "TCombobox",
    "TScale",
    "Scale",
    "Text",
    "Treeview",
}


def _is_tooltip_candidate(widget) -> bool:
    if getattr(widget, "_tooltip_text", None):
        return True
    try:
        widget_cls = widget.winfo_class()
    except (AttributeError, tk.TclError):
        return False
    return widget_cls in _TOOLTIP_DEFAULT_CLASSES


def ensure_tooltips(app, *, apply_tooltip_func: Callable[[Any, str], Any] | None = None):
    apply_fn = apply_tooltip if apply_tooltip_func is None else apply_tooltip_func
    for widget in _walk_widgets(app):
        if not _is_tooltip_candidate(widget):
            continue
        existing = getattr(widget, "_tooltip", None)
        if existing:
            continue
        preset = getattr(widget, "_tooltip_text", None)
        if preset:
            apply_fn(widget, preset)
            continue
        text = _default_tooltip_text(widget)
        if text:
            apply_fn(widget, text)
