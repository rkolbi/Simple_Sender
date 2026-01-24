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
"""Macro prompt helpers shared by the macro executor."""

from __future__ import annotations

import re
import shlex
import types
from typing import Any

PROMPT_BRACKET_PAT = re.compile(
    r"\s*\[(?:title\([^]]*\)|btn\([^]]*\)\s*[A-Za-z0-9]?)\]\s*",
    re.IGNORECASE,
)


def strip_prompt_tokens(line: str) -> str:
    return PROMPT_BRACKET_PAT.sub(" ", line).strip()


def format_prompt_macros(text: str, macro_vars: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        attr = match.group(1)
        value = None
        macro_ns = macro_vars.get("macro")
        if isinstance(macro_ns, types.SimpleNamespace):
            value = getattr(macro_ns, attr, None)
        if value is None:
            value = macro_vars.get(attr)
        if value is None:
            return ""
        return str(value)

    return re.sub(r"\[macro\.([A-Za-z_]\w*)\]", replace, text)


def parse_macro_prompt(
    line: str,
    macro_vars: dict[str, Any] | None = None,
) -> tuple[str, str, list[str], str, dict[str, str | None]]:
    title = "Macro Pause"
    message = ""
    buttons: list[str] = []
    show_resume = True
    resume_label = "Resume"
    cancel_label = "Cancel"
    custom_btns: list[tuple[str, str | None]] = []
    button_keys: dict[str, str | None] = {}

    fragments = []
    bracket_matches = []
    last = 0
    for match in re.finditer(r"\[(.*?)\]", line):
        bracket_text = match.group(0)
        if PROMPT_BRACKET_PAT.fullmatch(bracket_text):
            fragments.append(line[last: match.start()])
            fragments.append(" ")
            bracket_matches.append(match.group(1).strip())
            last = match.end()
        else:
            fragments.append(line[last: match.end()])
            last = match.end()
    fragments.append(line[last:])
    parsed_line = "".join(fragments)

    for token in bracket_matches:
        if not token:
            continue
        title_match = re.fullmatch(r"title\((.*?)\)", token, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip() or title
            continue
        btn_match = re.fullmatch(r"btn\((.*?)\)\s*([A-Za-z0-9])?", token, re.IGNORECASE)
        if btn_match:
            custom_btns.append((btn_match.group(1).strip(), btn_match.group(2)))
            continue

    match = re.search(r"\((.*?)\)", parsed_line)
    if match:
        message = match.group(1).strip()
    try:
        tokens = shlex.split(parsed_line)
    except Exception:
        tokens = parsed_line.split()
    tokens = tokens[1:] if tokens else []
    msg_parts: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in ("noresume", "no-resume"):
            show_resume = False
            continue
        if "=" in tok:
            key, val = tok.split("=", 1)
            key = key.lower()
            if key in ("title", "t"):
                title = val
                continue
            elif key in ("msg", "message", "text"):
                message = val
                continue
            elif key in ("buttons", "btns"):
                raw = val.replace("|", ",")
                buttons = [b.strip() for b in raw.split(",") if b.strip()]
                continue
            elif key in ("resume", "resumelabel"):
                if val.lower() in ("0", "false", "no", "off"):
                    show_resume = False
                else:
                    resume_label = val
                continue
            elif key in ("cancel", "cancellabel"):
                cancel_label = val
                continue
        msg_parts.append(tok)
    if not message and msg_parts:
        message = " ".join(msg_parts)
    if not message:
        message = "Macro paused."
    if macro_vars:
        message = format_prompt_macros(message, macro_vars)
    extras = [b for b in buttons if b and b not in (resume_label, cancel_label)]
    choices: list[str] = []
    if custom_btns:
        for label, key in custom_btns:
            if not label:
                continue
            choices.append(label)
            button_keys[label] = key
    else:
        if show_resume:
            choices.append(resume_label)
        choices.extend(extras)
    choices.append(cancel_label)
    return title, message, choices, cancel_label, button_keys
