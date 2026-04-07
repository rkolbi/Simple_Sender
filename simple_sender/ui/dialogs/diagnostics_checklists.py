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

"""Diagnostics checklist dialog helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, cast

from simple_sender.ui.checklist_files import find_named_checklist, load_checklist_items
from simple_sender.ui.theme_helpers import bind_text_display_theme, text_display_theme_options

from .popup_utils import center_window

_CHECKLIST_EMPTY_TEXT = "Checklist file is empty."
_RELEASE_CHECKLIST_WINDOW_ATTR = "_release_checklist_window"
_RUN_CHECKLIST_WINDOW_ATTR = "_run_checklist_window"


def resolve_checklist_items(app: Any, name: str, fallback: list[str]) -> list[str]:
    path = find_named_checklist(app, name)
    if not path:
        return fallback
    items = load_checklist_items(path)
    if items is None:
        return fallback
    return cast(list[str], items)


def resolve_checklist_items_any(
    app: Any, names: list[str], fallback: list[str]
) -> list[str]:
    for name in names:
        path = find_named_checklist(app, name)
        if not path:
            continue
        items = load_checklist_items(path)
        if items is not None:
            return cast(list[str], items)
    return fallback


def open_release_checklist(
    app: Any,
    *,
    fallback_items: list[str],
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    items = resolve_checklist_items(app, "release", fallback_items)
    _open_checklist_window(
        app,
        window_attr=_RELEASE_CHECKLIST_WINDOW_ATTR,
        title="Release checklist",
        heading="Release checklist",
        intro_text="Use this quick pass before release to confirm the critical GRBL workflows.",
        items=items,
        min_size=(560, 380),
        wraplength=520,
        text_height=12,
        restore_log_context="Failed restoring existing release checklist window",
        log_suppressed=log_suppressed,
    )


def open_run_checklist(
    app: Any,
    *,
    fallback_items: list[str],
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    items = resolve_checklist_items_any(app, ["start-job", "run"], fallback_items)
    _open_checklist_window(
        app,
        window_attr=_RUN_CHECKLIST_WINDOW_ATTR,
        title="Start Job checklist",
        heading="Start Job checklist",
        intro_text="Use this checklist before starting a job to reduce surprises.",
        items=items,
        min_size=(520, 320),
        wraplength=480,
        text_height=10,
        restore_log_context="Failed restoring existing run checklist window",
        log_suppressed=log_suppressed,
    )


def _open_checklist_window(
    app: Any,
    *,
    window_attr: str,
    title: str,
    heading: str,
    intro_text: str,
    items: list[str],
    min_size: tuple[int, int],
    wraplength: int,
    text_height: int,
    restore_log_context: str,
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    existing = getattr(app, window_attr, None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception as exc:
            log_suppressed(restore_log_context, exc)
    win = tk.Toplevel(app)
    setattr(app, window_attr, win)
    win.title(title)
    win.minsize(*min_size)
    win.transient(app)
    container = ttk.Frame(win, padding=12)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container,
        text=heading,
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        container,
        text=intro_text,
        wraplength=wraplength,
        justify="left",
    ).pack(anchor="w", pady=(4, 10))
    text = tk.Text(container, wrap="word", height=text_height)
    themed_options = text_display_theme_options(app)
    if themed_options:
        text.configure(themed_options)
    text.pack(fill="both", expand=True)
    bind_text_display_theme(app, text)
    if items:
        text.insert("end", "\n".join(f"- {item}" for item in items))
    else:
        text.insert("end", _CHECKLIST_EMPTY_TEXT)
    text.configure(state="disabled")
    center_window(win, app)

    def _on_close() -> None:
        setattr(app, window_attr, None)
        win.destroy()

    btn_row = ttk.Frame(container)
    btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="Close", command=_on_close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", _on_close)
