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

import tkinter.font as tkfont

_FILE_DIALOG_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)


def _coerce_scale(value, default: float = 1.4) -> float:
    try:
        scale = float(value)
    except Exception:
        return default
    if scale <= 0:
        return default
    return max(1.0, min(3.0, scale))


def _get_named_font_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    for name in _FILE_DIALOG_FONTS:
        try:
            sizes[name] = int(tkfont.nametofont(name).cget("size"))
        except Exception:
            continue
    return sizes


def _scaled_font_size(size: int, scale: float) -> int:
    sign = -1 if size < 0 else 1
    value = max(1, int(round(abs(size) * scale)))
    return sign * value


def _apply_scaled_fonts(sizes: dict[str, int], scale: float) -> None:
    for name, size in sizes.items():
        try:
            tkfont.nametofont(name).configure(size=_scaled_font_size(size, scale))
        except Exception:
            continue


def _restore_fonts(sizes: dict[str, int]) -> None:
    for name, size in sizes.items():
        try:
            tkfont.nametofont(name).configure(size=size)
        except Exception:
            continue


def run_file_dialog(app, func, *args, **kwargs):
    enabled = False
    try:
        enabled = bool(app.file_manager_scaling_enabled.get())
    except Exception:
        enabled = False
    if not enabled:
        return func(*args, **kwargs)

    try:
        raw_scale = app.file_manager_scale.get()
    except Exception:
        raw_scale = None
    scale = _coerce_scale(raw_scale, 1.4)

    try:
        old_scale = float(app.tk.call("tk", "scaling"))
    except Exception:
        old_scale = 1.0
    sizes = _get_named_font_sizes()
    try:
        try:
            app.tk.call("tk", "scaling", old_scale * scale)
        except Exception:
            pass
        _apply_scaled_fonts(sizes, scale)
        return func(*args, **kwargs)
    finally:
        try:
            app.tk.call("tk", "scaling", old_scale)
        except Exception:
            pass
        _restore_fonts(sizes)
