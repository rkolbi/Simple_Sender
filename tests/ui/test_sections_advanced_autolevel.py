import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

from simple_sender.ui.settings.sections_advanced import build_auto_level_section

pytestmark = pytest.mark.ui


def _walk_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _find_entry_with_value(widget, value: str) -> ttk.Entry | None:
    for child in _walk_widgets(widget):
        if not isinstance(child, ttk.Entry):
            continue
        var_name = child.cget("textvariable")
        if not var_name:
            continue
        try:
            current = child.getvar(var_name)
        except tk.TclError:
            continue
        if current == value:
            return child
    return None


def test_auto_level_pref_save_uses_latest_value_as_fallback(tk_root) -> None:
    parent = ttk.Frame(tk_root)
    parent.pack()

    class _App:
        def __init__(self) -> None:
            self.auto_level_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.settings = {}
            self.auto_level_job_prefs = {
                "small_max_area": 1200.0,
                "large_min_area": 6400.0,
                "small": {"spacing": 1.11, "interpolation": "bicubic"},
                "large": {"spacing": 8.88, "interpolation": "bilinear"},
                "custom": {"spacing": 5.55, "interpolation": "bicubic"},
            }

    app = _App()
    build_auto_level_section(app, parent, row=0)

    # Unique starting value for the Small spacing entry.
    small_spacing_entry = _find_entry_with_value(app.auto_level_frame, "1.11")
    assert small_spacing_entry is not None

    small_spacing_entry.delete(0, "end")
    small_spacing_entry.insert(0, "2.22")
    small_spacing_entry.event_generate("<FocusOut>")
    tk_root.update()

    assert app.auto_level_job_prefs["small"]["spacing"] == pytest.approx(2.22)

    # Invalid input should now fall back to last saved value (2.22), not initial (1.11).
    small_spacing_entry.delete(0, "end")
    small_spacing_entry.insert(0, "invalid")
    small_spacing_entry.event_generate("<FocusOut>")
    tk_root.update()

    assert app.auto_level_job_prefs["small"]["spacing"] == pytest.approx(2.22)
