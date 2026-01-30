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

import os
from typing import Any

from simple_sender.utils.constants import GCODE_TOP_VIEW_STREAMING_SEGMENT_LIMIT


def set_preview_streaming_state(app: Any, streaming: bool) -> None:
    app._gcode_streaming_mode = bool(streaming)
    app._render3d_blocked = bool(streaming)
    try:
        app._refresh_render_3d_toggle_text()
    except Exception:
        pass


def configure_toolpath_preview(
    app: Any,
    path: str,
    lines: list[str],
    streaming_source: Any | None,
) -> None:
    enabled = bool(app.render3d_enabled.get()) and streaming_source is None
    app.toolpath_panel.set_enabled(enabled)
    app.toolpath_panel.clear()
    app.toolpath_panel.set_job_name(os.path.basename(path))
    if streaming_source is not None:
        try:
            total_lines = app._gcode_total_lines or len(streaming_source)
        except Exception:
            total_lines = len(lines)
        arc_step = app.toolpath_panel.get_arc_step_rad(total_lines)
        app.toolpath_panel.set_top_view_lines(
            streaming_source,
            max_segments=GCODE_TOP_VIEW_STREAMING_SEGMENT_LIMIT,
            arc_step_rad=arc_step,
        )
