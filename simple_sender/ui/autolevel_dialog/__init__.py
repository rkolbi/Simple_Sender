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

import tempfile
import tkinter as tk
from collections.abc import Callable
from typing import Any, cast
from tkinter import ttk, messagebox, simpledialog

from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import (
    LevelFileResult,
    level_gcode_file,
    level_gcode_lines,
    write_gcode_lines,
)
from simple_sender.gcode_parser import (
    clean_gcode_line,
    split_gcode_lines,
    split_gcode_lines_stream,
)
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.widgets import set_tab_tooltip

from .workflow import _apply_auto_level_to_path as _apply_auto_level_to_path_impl


def _apply_auto_level_to_path(
    *,
    source_path: str,
    source_lines: list[str] | None,
    output_path: str,
    temp_path_fn: Callable[[str], str],
    height_map: HeightMap,
    arc_step_rad: float,
    interpolation: str,
    header_lines: list[str] | None,
    streaming_mode: bool,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[LevelFileResult, bool, str | None]:
    return cast(
        tuple[LevelFileResult, bool, str | None],
        _apply_auto_level_to_path_impl(
            source_path=source_path,
            source_lines=source_lines,
            output_path=output_path,
            temp_path_fn=temp_path_fn,
            height_map=height_map,
            arc_step_rad=arc_step_rad,
            interpolation=interpolation,
            header_lines=header_lines,
            streaming_mode=streaming_mode,
            log_fn=log_fn,
            level_gcode_lines_fn=level_gcode_lines,
            level_gcode_file_fn=level_gcode_file,
            write_gcode_lines_fn=write_gcode_lines,
            split_gcode_lines_fn=split_gcode_lines,
            split_gcode_lines_stream_fn=split_gcode_lines_stream,
            clean_gcode_line_fn=clean_gcode_line,
            tempfile_module=tempfile,
        ),
    )


def show_auto_level_dialog(app: Any) -> None:
    from .dialog_controller import AutoLevelDialogDependencies, show_auto_level_dialog as _show

    deps = AutoLevelDialogDependencies(
        tk_module=tk,
        ttk_module=ttk,
        messagebox=messagebox,
        simpledialog=simpledialog,
        center_window_fn=center_window,
        set_tab_tooltip_fn=set_tab_tooltip,
        apply_auto_level_to_path_fn=_apply_auto_level_to_path,
    )
    _show(app, deps)
