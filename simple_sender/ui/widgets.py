import tkinter as tk
from tkinter import ttk
from typing import Any


class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = 1000):
        self.widget = widget
        self.text = text
        self._tip = None
        self.delay_ms = delay_ms
        self._after_id = None
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
        top = self.widget.winfo_toplevel()
        enabled = True
        try:
            enabled = bool(top.tooltip_enabled.get())
        except Exception:
            pass
        if not enabled:
            return
        if not self.text or self._tip is not None:
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
                text=self.text,
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
        self._poly = None
        self._text_id = None
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
        cut = s * 0.2929
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

    def _on_click(self, event=None):
        if self._state == "disabled":
            return
        if callable(self._command):
            self._command()

    def configure(self, cnf=None, **kwargs):
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

    def config(self, **kwargs):
        return self.configure(**kwargs)

    def cget(self, key):
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
    ToolTip(widget, text)


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
