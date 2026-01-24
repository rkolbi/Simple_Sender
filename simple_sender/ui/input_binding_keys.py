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
"""Keyboard binding parsing helpers shared by input_bindings."""

from __future__ import annotations


def normalize_key_label(app, text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""
    chunks = [c for c in raw.replace(",", " ").split() if c.strip()]
    seq = []
    for chunk in chunks:
        chord = app._normalize_key_chord(chunk)
        if chord:
            seq.append(chord)
        if len(seq) >= 3:
            break
    return " ".join(seq)


def normalize_key_chord(app, text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    aliases = {
        "SPACE": "Space",
        "SPC": "Space",
        "ENTER": "Enter",
        "RETURN": "Enter",
        "ESC": "Escape",
        "ESCAPE": "Escape",
        "TAB": "Tab",
        "BACKSPACE": "Backspace",
        "DEL": "Delete",
        "DELETE": "Delete",
        "NONE": "",
        "CTRL": "Ctrl",
        "CONTROL": "Ctrl",
        "SHIFT": "Shift",
        "ALT": "Alt",
        "OPTION": "Alt",
    }
    mods = []
    key = ""
    for part in parts:
        up = part.upper()
        if up in aliases:
            mapped = aliases[up]
            if mapped in ("Ctrl", "Shift", "Alt"):
                if mapped not in mods:
                    mods.append(mapped)
            elif mapped:
                key = mapped
            continue
        if len(part) == 1:
            key = part.upper()
        else:
            key = part
    if not key:
        return ""
    mod_order = ("Ctrl", "Shift", "Alt")
    ordered_mods = [m for m in mod_order if m in mods]
    return "+".join(ordered_mods + [key])


def key_sequence_tuple(app, label: str) -> tuple[str, ...] | None:
    normalized = app._normalize_key_label(label)
    if not normalized:
        return None
    parts = [p for p in normalized.split(" ") if p]
    return tuple(parts[:3])


def update_modifier_state(app, event, pressed: bool) -> bool:
    keysym = getattr(event, "keysym", "")
    if not keysym:
        return False
    if keysym not in app._kb_mod_keysyms:
        return False
    if pressed:
        app._kb_mod_keys_down.add(keysym)
    else:
        app._kb_mod_keys_down.discard(keysym)
    return True


def modifier_active(app, name: str, event_state: int | None = None) -> bool:
    for keysym in app._kb_mod_keys_down:
        if app._kb_mod_keysyms.get(keysym) == name:
            return True
    if event_state is None:
        return False
    if name == "Ctrl":
        return bool(event_state & 0x4)
    if name == "Shift":
        return bool(event_state & 0x1)
    return False


def event_to_binding_label(app, event) -> str:
    keysym = event.keysym
    if app._update_modifier_state(event, pressed=True):
        return ""
    mods = []
    if app._modifier_active("Ctrl", getattr(event, "state", 0)):
        mods.append("Ctrl")
    if app._modifier_active("Shift", getattr(event, "state", 0)):
        mods.append("Shift")
    if app._modifier_active("Alt"):
        mods.append("Alt")
    key_label = app._normalize_key_chord(keysym)
    if not key_label:
        return ""
    if mods:
        return "+".join(mods + [key_label])
    return key_label


def sequence_conflict_pair(app, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
    if not seq_a or not seq_b:
        return False
    min_len = min(len(seq_a), len(seq_b))
    return seq_a[:min_len] == seq_b[:min_len]


def sequence_conflict(app, seq: tuple[str, ...], existing: dict):
    for other_seq in existing.keys():
        if app._sequence_conflict_pair(seq, other_seq):
            return other_seq
    return None
