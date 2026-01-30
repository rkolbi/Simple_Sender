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
from typing import Any, cast

from simple_sender.ui.tooltip_policy import resolve_disabled_reason as _policy_disabled_reason
from simple_sender.utils.constants import STOP_SIGN_CUT_RATIO, TOOLTIP_DELAY_MS

class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = TOOLTIP_DELAY_MS):
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self.delay_ms = delay_ms
        self._after_id: Any | None = None
        widget.bind("<Enter>", self._schedule_show)
        widget.bind("<Leave>", self._hide)

    def _schedule_show(self, _event=None):
        # Always reset any existing tooltip so movement can update content/position.
        self._hide()
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(self.delay_ms, self._show)

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
            except Exception:
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
        except Exception:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        try:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            label = ttk.Label(
                self._tip,
                text=text,
                background="#ffffe0",
                relief="solid",
                padding=(6, 3),
            )
            label.pack()
        except tk.TclError:
            self._tip = None

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def set_text(self, text: str):
        self.text = text
        try:
            self.widget._tooltip_text = text
        except Exception:
            pass


def _resolve_widget_bg(widget):
    if widget:
        try:
            bg = widget.cget("background")
        except Exception:
            bg = ""
        if bg:
            return bg
    style = ttk.Style()
    for target in (
        "TFrame",
        "TLabelframe",
        "TButton",
        "TLabel",
        "Entry",
        "TEntry",
        "TCombobox",
        "TLabelframe.Label",
    ):
        cfg = style.configure(target)
        if isinstance(cfg, dict):
            bg = cfg.get("background") or cfg.get("fieldbackground")
            if bg:
                return bg
        else:
            try:
                lookup = style.lookup(target, "background")
            except tk.TclError:
                lookup = ""
            if lookup:
                return lookup
    if widget:
        try:
            root = widget.winfo_toplevel()
            bg = root.cget("background")
            if bg:
                return bg
        except Exception:
            pass
    return "#f0f0f0"


class StopSignButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        fill: str,
        text_color: str,
        command=None,
        size: int = 60,
        outline: str = "#2f2f2f",
        **kwargs,
    ):
        bg = kwargs.pop("bg", None)
        if bg is None:
            bg = kwargs.pop("background", None)
        if not bg:
            bg = _resolve_widget_bg(master)
        super().__init__(
            master,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=bg,
            **kwargs,
        )
        self._default_bg = bg
        self._text = text
        self._fill = fill
        self._text_color = text_color
        self._outline = outline
        self._command = command
        self._size = size
        self._state = "normal"
        self._poly: int | None = None
        self._text_id: int | None = None
        self._disabled_fill = self._blend_color(fill, "#f0f0f0", 0.55)
        self._disabled_text = self._blend_color(text_color, "#808080", 0.55)
        self._draw_octagon()
        self._apply_state()
        self._log_button = True
        self.bind("<Button-1>", self._on_click, add="+")

    def _blend_color(self, base: str, target: str, factor: float) -> str:
        base = base.lstrip("#")
        target = target.lstrip("#")
        if len(base) != 6 or len(target) != 6:
            return base if base.startswith("#") else f"#{base}"
        br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
        tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
        r = int(br + (tr - br) * factor)
        g = int(bg + (tg - bg) * factor)
        b = int(bb + (tb - bb) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def refresh_background(self):
        bg = _resolve_widget_bg(self.master)
        self._default_bg = bg
        try:
            self.config(bg=bg)
        except Exception:
            pass

    def _draw_octagon(self):
        size = self._size
        pad = 2
        s = size - pad * 2
        cut = s * STOP_SIGN_CUT_RATIO
        x0, y0 = pad, pad
        x1, y1 = pad + s, pad + s
        points = [
            x0 + cut, y0,
            x1 - cut, y0,
            x1, y0 + cut,
            x1, y1 - cut,
            x1 - cut, y1,
            x0 + cut, y1,
            x0, y1 - cut,
            x0, y0 + cut,
        ]
        self._poly = self.create_polygon(points, fill=self._fill, outline=self._outline, width=1)
        self._text_id = self.create_text(
            size / 2,
            size / 2,
            text=self._text,
            fill=self._text_color,
            justify="center",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _apply_state(self):
        is_disabled = self._state == "disabled"
        fill = self._disabled_fill if is_disabled else self._fill
        text_color = self._disabled_text if is_disabled else self._text_color
        if self._poly is not None:
            self.itemconfig(self._poly, fill=fill)
        if self._text_id is not None:
            self.itemconfig(self._text_id, fill=text_color)
        self.config(cursor="arrow" if is_disabled else "hand2")

    def _on_click(self, event: Any | None = None) -> None:
        if self._state == "disabled":
            return
        if callable(self._command):
            self._command()

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        config_options: dict[str, Any] = {}
        if cnf:
            if isinstance(cnf, dict):
                config_options.update(cnf)
            else:
                for item in cnf:
                    if isinstance(item, tuple) and len(item) == 2:
                        config_options[item[0]] = item[1]
        config_options.update(kwargs)
        if "text" in config_options:
            self._text = config_options.pop("text")
            if self._text_id is not None:
                self.itemconfig(self._text_id, text=self._text)
        if "command" in config_options:
            self._command = config_options.pop("command")
        if "state" in config_options:
            self._state = config_options.pop("state")
            self._apply_state()
        return super().configure(**config_options)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self):
        self._on_click()


def apply_tooltip(widget, text: str):
    if not text:
        return
    try:
        widget._tooltip_text = text
    except Exception:
        pass
    existing = getattr(widget, "_tooltip", None)
    if isinstance(existing, ToolTip):
        existing.set_text(text)
        return existing
    tip = ToolTip(widget, text)
    try:
        widget._tooltip = tip
    except Exception:
        pass
    return tip


def attach_numeric_keypad(
    entry,
    *,
    allow_decimal: bool = True,
    allow_negative: bool = False,
    allow_empty: bool = True,
    title: str | None = None,
):
    spec = {
        "allow_decimal": bool(allow_decimal),
        "allow_negative": bool(allow_negative),
        "allow_empty": bool(allow_empty),
        "title": title or "Enter value",
    }
    try:
        entry._numeric_keypad_spec = spec
    except Exception:
        return entry
    entry.bind("<Button-1>", _open_numeric_keypad, add="+")
    return entry


def _open_numeric_keypad(event):
    entry = event.widget
    spec = getattr(entry, "_numeric_keypad_spec", None)
    if not spec:
        return
    try:
        if not entry.winfo_viewable():
            return
    except Exception:
        pass
    owner = _resolve_owner(entry, "numeric_keypad_enabled")
    if owner is not None:
        try:
            if not bool(owner.numeric_keypad_enabled.get()):
                return
        except Exception:
            pass
    if _widget_disabled(entry):
        return
    _show_numeric_keypad(entry, spec)
    return "break"


def _center_modal(window, parent):
    try:
        window.update_idletasks()
    except Exception:
        return
    w = window.winfo_width() or window.winfo_reqwidth()
    h = window.winfo_height() or window.winfo_reqheight()
    x = y = 0
    if parent is not None:
        try:
            parent.update_idletasks()
            pw = parent.winfo_width() or parent.winfo_reqwidth()
            ph = parent.winfo_height() or parent.winfo_reqheight()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
        except Exception:
            parent = None
    if parent is None:
        try:
            sw = window.winfo_screenwidth()
            sh = window.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
        except Exception:
            x = y = 0
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


def _show_numeric_keypad(entry, spec: dict[str, Any]):
    dlg = getattr(entry, "_numeric_keypad_dialog", None)
    if dlg is not None:
        try:
            if dlg.winfo_exists():
                dlg.lift()
                return
        except Exception:
            pass
    parent = None
    try:
        parent = entry.winfo_toplevel()
    except Exception:
        parent = entry
    original_value = entry.get()
    current_value = original_value
    value_var = tk.StringVar(value=current_value)
    dlg = tk.Toplevel(parent)
    dlg.title(spec.get("title") or "Enter value")
    dlg.transient(parent)
    try:
        dlg.lift()
    except Exception:
        pass
    dlg.resizable(False, False)
    try:
        entry._numeric_keypad_dialog = dlg
    except Exception:
        pass

    frame = ttk.Frame(dlg, padding=12)
    frame.pack(fill="both", expand=True)
    display = ttk.Entry(
        frame,
        textvariable=value_var,
        state="readonly",
        width=16,
        justify="right",
    )
    display.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))
    display.configure(takefocus=0)

    def _set_current_value(new_value: str):
        nonlocal current_value
        current_value = new_value
        value_var.set(current_value)

    def _insert_text(text: str):
        nonlocal current_value
        current_value = f"{current_value}{text}"
        value_var.set(current_value)

    def _press_digit(digit: str):
        _insert_text(digit)

    def _press_decimal():
        if not spec.get("allow_decimal", True):
            return
        current = current_value
        if "." in current:
            return
        if current in ("", "-"):
            prefix = "-" if current == "-" else ""
            _set_current_value(f"{prefix}0.")
            return
        _insert_text(".")

    def _toggle_sign():
        if not spec.get("allow_negative", False):
            return
        val = current_value
        if val.startswith("-"):
            _set_current_value(val[1:])
        else:
            _set_current_value(f"-{val}" if val else "-")

    def _backspace():
        val = current_value
        if not val:
            return
        _set_current_value(val[:-1])

    def _clear():
        if not spec.get("allow_empty", True):
            return
        _set_current_value("")

    def _apply_and_close():
        new_value = current_value
        if not spec.get("allow_empty", True) and new_value == "":
            new_value = original_value
        try:
            entry.delete(0, "end")
            if new_value:
                entry.insert(0, new_value)
        except Exception:
            pass
        try:
            entry.event_generate("<Return>")
        except Exception:
            pass
        try:
            entry.event_generate("<FocusOut>")
        except Exception:
            pass
        _close_dialog()

    def _cancel():
        _close_dialog()

    def _close_dialog():
        try:
            dlg.grab_release()
        except Exception:
            pass
        try:
            dlg.destroy()
        except Exception:
            pass
        try:
            entry._numeric_keypad_dialog = None
        except Exception:
            pass

    def _make_button(text: str, command, row: int, col: int, *, colspan: int = 1):
        btn = ttk.Button(
            frame,
            text=text,
            command=command,
            width=8,
            padding=(12, 8),
            takefocus=0,
        )
        btn.grid(row=row, column=col, columnspan=colspan, padx=4, pady=4, sticky="nsew")
        return btn

    buttons = [
        ("7", lambda: _press_digit("7")),
        ("8", lambda: _press_digit("8")),
        ("9", lambda: _press_digit("9")),
        ("4", lambda: _press_digit("4")),
        ("5", lambda: _press_digit("5")),
        ("6", lambda: _press_digit("6")),
        ("1", lambda: _press_digit("1")),
        ("2", lambda: _press_digit("2")),
        ("3", lambda: _press_digit("3")),
        ("0", lambda: _press_digit("0")),
    ]
    row = 1
    col = 0
    for label, cmd in buttons:
        _make_button(label, cmd, row, col)
        col += 1
        if col > 2:
            col = 0
            row += 1

    col = 0
    if spec.get("allow_decimal", True):
        _make_button(".", _press_decimal, row, col)
        col += 1
    if spec.get("allow_negative", False):
        _make_button("+/-", _toggle_sign, row, col)
        col += 1
    _make_button("Back", _backspace, row, col)
    row += 1
    _make_button("Clear", _clear, row, 0, colspan=2)
    _make_button("Done", _apply_and_close, row, 2)
    row += 1
    _make_button("Cancel", _cancel, row, 0, colspan=3)

    for i in range(3):
        frame.grid_columnconfigure(i, weight=1)

    try:
        entry.focus_set()
        entry.selection_range(0, "end")
        entry.icursor("end")
    except Exception:
        pass
    dlg.protocol("WM_DELETE_WINDOW", _cancel)
    _center_modal(dlg, parent)
    try:
        dlg.update_idletasks()
        dlg.wait_visibility()
    except Exception:
        pass
    try:
        dlg.grab_set()
    except Exception:
        def _retry_grab():
            try:
                dlg.grab_set()
            except Exception:
                pass
        try:
            dlg.after(0, _retry_grab)
        except Exception:
            pass


def _widget_state(widget) -> str:
    try:
        return str(widget.cget("state")).lower()
    except Exception:
        pass
    try:
        state = widget.state()
        if isinstance(state, (list, tuple, set)):
            return "disabled" if "disabled" in state else "normal"
    except Exception:
        pass
    return "normal"


def _widget_disabled(widget) -> bool:
    return _widget_state(widget) == "disabled"


def _resolve_owner(widget, attr: str):
    try:
        owner = widget.winfo_toplevel()
    except Exception:
        owner = widget
    for _ in range(8):
        if owner is None:
            break
        try:
            if hasattr(owner, attr):
                return owner
        except Exception:
            pass
        try:
            owner = owner.master
        except Exception:
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
    except Exception:
        cls = ""
    label = ""
    try:
        label = widget.cget("text") or ""
    except Exception:
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
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk_widgets(child)


def ensure_tooltips(app):
    for widget in _walk_widgets(app):
        existing = getattr(widget, "_tooltip", None)
        if existing:
            continue
        preset = getattr(widget, "_tooltip_text", None)
        if preset:
            apply_tooltip(widget, preset)
            continue
        text = _default_tooltip_text(widget)
        if text:
            apply_tooltip(widget, text)


def attach_log_gcode(widget, gcode_or_func):
    try:
        widget._log_gcode_get = gcode_or_func
    except Exception:
        pass


def set_kb_id(widget, kb_id: str):
    try:
        widget._kb_id = kb_id
    except Exception:
        pass
    return widget


class VirtualHoldButton:
    def __init__(self, label: str, kb_id: str, axis: str, direction: int):
        self._text = label
        self._kb_id = kb_id
        self._hold_axis = axis
        self._hold_direction = direction
        self._tooltip_text = ""

    def cget(self, key: str):
        if key == "text":
            return self._text
        if key == "state":
            return "normal"
        if key == "command":
            return None
        raise KeyError(key)

    def winfo_name(self):
        return f"virtual_{self._kb_id}"

    def winfo_class(self):
        return "VirtualHoldButton"
