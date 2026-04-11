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

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from simple_sender.ui.help_content import HELP_ABOUT_SECTIONS, HELP_ABOUT_TITLE
from simple_sender.ui.theme_helpers import (
    bind_scrollbar_theme,
    bind_text_display_theme,
    notebook_page_style_name,
    plain_tk_theme_defaults,
    text_display_theme_options,
)

_HELP_SEARCH_DEBOUNCE_MS = 120


class HelpAboutPanel(ttk.Frame):
    def __init__(self, app, parent) -> None:
        super().__init__(parent, padding=8, style=notebook_page_style_name())
        self.app = app
        self.search_var = tk.StringVar(master=self, value="")
        self.match_count_var = tk.StringVar(master=self, value="Search the help")
        self._search_after_id = None
        self._search_matches: list[tuple[str, str]] = []
        self._search_match_index = -1
        self._fonts: list[tkfont.Font] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_search_row()
        self._build_text_area()
        self._configure_text_tags()
        self.render_help_document()
        self.search_var.trace_add("write", lambda *_args: self._schedule_search_refresh())

    def _build_search_row(self) -> None:
        sticky_frame = ttk.Frame(self, style=notebook_page_style_name())
        sticky_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        sticky_frame.grid_columnconfigure(0, weight=1)

        filter_row = ttk.Frame(sticky_frame, style=notebook_page_style_name())
        filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        filter_row.grid_columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.search_entry = ttk.Entry(filter_row, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.search_entry.bind("<Return>", lambda _event: self.go_to_next_match())
        self.search_entry.bind("<KP_Enter>", lambda _event: self.go_to_next_match())
        self.search_entry.bind("<Shift-Return>", lambda _event: self.go_to_previous_match())

        self.previous_button = ttk.Button(
            filter_row,
            text="Previous",
            command=self.go_to_previous_match,
            width=9,
        )
        self.previous_button.grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.next_button = ttk.Button(
            filter_row,
            text="Next",
            command=self.go_to_next_match,
            width=7,
        )
        self.next_button.grid(row=0, column=3, sticky="e", padx=(0, 8))
        self.match_count_label = ttk.Label(filter_row, textvariable=self.match_count_var, width=12)
        self.match_count_label.grid(row=0, column=4, sticky="e")

        self.sticky_heading = ttk.Label(
            sticky_frame,
            text="About",
            font=("TkDefaultFont", 10, "bold"),
        )
        self.sticky_heading.grid(row=1, column=0, sticky="w")
        ttk.Separator(sticky_frame, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(4, 0))

    def _build_text_area(self) -> None:
        content = ttk.Frame(self, style=notebook_page_style_name())
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        themed_options = text_display_theme_options(self.app)
        self.text = tk.Text(
            content,
            wrap="word",
            state="disabled",
            padx=14,
            pady=14,
            takefocus=True,
            **themed_options,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        bind_text_display_theme(self.app, self.text)

        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        bind_scrollbar_theme(self.app, scrollbar)
        self.text.configure(yscrollcommand=scrollbar.set)

    def _configure_text_tags(self) -> None:
        base_font = tkfont.nametofont("TkDefaultFont")
        title_font = base_font.copy()
        title_font.configure(size=max(int(base_font.cget("size")) + 6, 16), weight="bold")
        heading_font = base_font.copy()
        heading_font.configure(size=max(int(base_font.cget("size")) + 2, 12), weight="bold")
        subheading_font = base_font.copy()
        subheading_font.configure(size=max(int(base_font.cget("size")) + 1, 11), weight="bold")
        note_font = base_font.copy()
        note_font.configure(slant="italic")
        code_font = tkfont.nametofont("TkFixedFont").copy()
        self._fonts = [title_font, heading_font, subheading_font, note_font, code_font]

        colors = plain_tk_theme_defaults(self.app)
        match_bg = colors.get("selection_bg") or "#f5d96b"
        match_fg = colors.get("selection_fg") or colors.get("fg") or "#000000"
        current_bg = colors.get("accent") or match_bg
        current_fg = colors.get("selection_fg") or match_fg
        note_fg = colors.get("muted") or colors.get("fg") or "#000000"
        code_bg = colors.get("frame_bg") or colors.get("background") or "#f0f0f0"

        self.text.tag_configure("title", font=title_font, spacing1=4, spacing3=12)
        self.text.tag_configure("heading", font=heading_font, spacing1=10, spacing3=6)
        self.text.tag_configure("subheading", font=subheading_font, spacing1=6, spacing3=4)
        self.text.tag_configure("body", spacing1=0, spacing3=8)
        self.text.tag_configure("bullet", lmargin1=18, lmargin2=34, spacing1=0, spacing3=6)
        self.text.tag_configure("note", font=note_font, foreground=note_fg, spacing1=0, spacing3=10)
        self.text.tag_configure(
            "code",
            font=code_font,
            background=code_bg,
            lmargin1=18,
            lmargin2=18,
            rmargin=12,
            spacing1=0,
            spacing3=10,
        )
        self.text.tag_configure("search_match", background=match_bg, foreground=match_fg)
        self.text.tag_configure("search_current", background=current_bg, foreground=current_fg)

    def _set_text_state(self, state: str) -> None:
        self.text.configure(state=state)

    def render_help_document(self) -> None:
        self._set_text_state("normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", f"{HELP_ABOUT_TITLE}\n", ("title",))
        for section in HELP_ABOUT_SECTIONS:
            self._render_section(section)
        self.text.mark_set("insert", "1.0")
        self._set_text_state("disabled")
        self.refresh_search_highlights(recenter=False)

    def _render_section(self, section) -> None:
        self.text.insert("end", f"{section.title}\n", ("heading",))
        self._render_content_block(
            paragraphs=getattr(section, "paragraphs", ()),
            bullets=getattr(section, "bullets", ()),
            notes=getattr(section, "notes", ()),
            code_blocks=getattr(section, "code_blocks", ()),
        )
        for subsection in tuple(getattr(section, "subsections", ()) or ()):
            self.text.insert("end", f"{subsection.title}\n", ("subheading",))
            self._render_content_block(
                paragraphs=getattr(subsection, "paragraphs", ()),
                bullets=getattr(subsection, "bullets", ()),
                notes=getattr(subsection, "notes", ()),
                code_blocks=getattr(subsection, "code_blocks", ()),
            )

    def _render_content_block(
        self,
        *,
        paragraphs=(),
        bullets=(),
        notes=(),
        code_blocks=(),
    ) -> None:
        for paragraph in tuple(paragraphs or ()):
            self.text.insert("end", f"{paragraph}\n\n", ("body",))
        for bullet in tuple(bullets or ()):
            self.text.insert("end", f"- {bullet}\n", ("bullet",))
        if bullets:
            self.text.insert("end", "\n", ("body",))
        for code_block in tuple(code_blocks or ()):
            self.text.insert("end", f"{code_block.rstrip()}\n\n", ("code",))
        for note in tuple(notes or ()):
            self.text.insert("end", f"Note: {note}\n\n", ("note",))

    def focus_search(self) -> None:
        try:
            self.search_entry.focus_set()
        except Exception:
            pass

    def _schedule_search_refresh(self) -> None:
        if self._search_after_id is not None:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
            self._search_after_id = None
        try:
            self._search_after_id = self.after(
                _HELP_SEARCH_DEBOUNCE_MS,
                lambda: self.refresh_search_highlights(recenter=True),
            )
        except Exception:
            self.refresh_search_highlights(recenter=True)

    def refresh_search_highlights(self, *, recenter: bool = True) -> None:
        self._search_after_id = None
        query = str(self.search_var.get() or "").strip()

        self._set_text_state("normal")
        self.text.tag_remove("search_match", "1.0", "end")
        self.text.tag_remove("search_current", "1.0", "end")
        self._set_text_state("disabled")

        self._search_matches = []
        self._search_match_index = -1

        if not query:
            self.match_count_var.set("Search the help")
            self._sync_search_controls()
            return

        count = tk.IntVar(master=self, value=0)
        start_index = "1.0"
        while True:
            match_start = self.text.search(
                query,
                start_index,
                stopindex="end",
                nocase=True,
                count=count,
            )
            if not match_start:
                break
            match_length = max(1, int(count.get() or 0))
            match_end = f"{match_start}+{match_length}c"
            self._search_matches.append((match_start, match_end))
            start_index = match_end

        if not self._search_matches:
            self.match_count_var.set("0 matches")
            self._sync_search_controls()
            return

        self._set_text_state("normal")
        for match_start, match_end in self._search_matches:
            self.text.tag_add("search_match", match_start, match_end)
        self._set_text_state("disabled")

        self._search_match_index = 0
        self._apply_current_match(recenter=recenter)

    def _apply_current_match(self, *, recenter: bool = True) -> None:
        if not self._search_matches or self._search_match_index < 0:
            self.match_count_var.set("0 matches")
            self._sync_search_controls()
            return

        match_start, match_end = self._search_matches[self._search_match_index]
        self._set_text_state("normal")
        self.text.tag_remove("search_current", "1.0", "end")
        self.text.tag_add("search_current", match_start, match_end)
        self._set_text_state("disabled")
        self.match_count_var.set(f"{self._search_match_index + 1} / {len(self._search_matches)}")
        self._sync_search_controls()
        if recenter:
            self._show_match_with_context(match_start, match_end)

    def _show_match_with_context(self, match_start: str, match_end: str) -> None:
        try:
            total_lines = max(1, int(str(self.text.index("end-1c")).split(".", 1)[0]))
            match_line = max(1, int(str(match_start).split(".", 1)[0]))
        except Exception:
            self.text.see(match_start)
            self.text.see(match_end)
            return
        top_line = max(1, match_line - 3)
        denominator = max(1, total_lines - 1)
        fraction = min(1.0, max(0.0, float(top_line - 1) / float(denominator)))
        self.text.yview_moveto(fraction)
        self.text.see(match_start)
        self.text.see(match_end)

    def _sync_search_controls(self) -> None:
        if self._search_matches:
            self.previous_button.state(["!disabled"])
            self.next_button.state(["!disabled"])
            return
        self.previous_button.state(["disabled"])
        self.next_button.state(["disabled"])

    def go_to_next_match(self) -> None:
        if not self._search_matches:
            return
        self._search_match_index = (self._search_match_index + 1) % len(self._search_matches)
        self._apply_current_match(recenter=True)

    def go_to_previous_match(self) -> None:
        if not self._search_matches:
            return
        self._search_match_index = (self._search_match_index - 1) % len(self._search_matches)
        self._apply_current_match(recenter=True)


def build_help_about_panel(app, parent) -> HelpAboutPanel:
    panel = HelpAboutPanel(app, parent)
    app.help_about_panel = panel
    return panel
