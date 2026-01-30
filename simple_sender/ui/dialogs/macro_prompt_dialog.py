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

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable

from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.widgets import set_kb_id


def show_macro_prompt(
    app,
    title: str,
    message: str,
    choices: list[str],
    cancel_label: str,
    result_q: queue.Queue,
) -> None:
    try:
        dlg = tk.Toplevel(app)
        dlg.title(title)
        dlg.transient(app)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        lbl = ttk.Label(frm, text=message, wraplength=460, justify="left")
        lbl.pack(fill="x", pady=(0, 10))
        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x")

        def choose(label: str):
            if result_q.empty():
                result_q.put(label)
            try:
                dlg.destroy()
            except Exception:
                pass

        def _make_command(label: str) -> Callable[[], None]:
            return lambda: choose(label)

        for idx, lbl_text in enumerate(choices):
            b = ttk.Button(btn_row, text=lbl_text, command=_make_command(lbl_text))
            set_kb_id(b, f"macro_prompt_{idx}")
            b.pack(side="left", padx=(0, 6))

        def on_close():
            choose(cancel_label)

        dlg.protocol("WM_DELETE_WINDOW", on_close)
        center_window(dlg, app)
    except Exception as exc:
        try:
            app.streaming_controller.log(f"[macro] Prompt failed: {exc}")
        except Exception:
            pass
        if result_q.empty():
            result_q.put(cancel_label)
