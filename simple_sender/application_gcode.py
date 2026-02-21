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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
from typing import Any, cast

from simple_sender.types import LineSource
from simple_sender.ui.dialogs import show_auto_level_dialog, show_resume_dialog
from simple_sender.ui.dialogs.resume_from import build_resume_preamble, resume_from_line
from simple_sender.ui.dialogs.streaming_metrics import (
    format_throughput,
    maybe_notify_job_completion,
)
from simple_sender.ui.dro import refresh_dro_display
from simple_sender.ui.gcode.loading import (
    ensure_gcode_loading_popup,
    finish_gcode_loading,
    hide_gcode_loading,
    set_gcode_loading_indeterminate,
    set_gcode_loading_progress,
    show_gcode_loading,
)
from simple_sender.ui.gcode.pipeline import (
    apply_loaded_gcode,
    clear_gcode,
    load_gcode_from_path,
)
from simple_sender.ui.gcode.stats import (
    apply_gcode_stats,
    estimate_factor_value,
    format_gcode_stats_text,
    get_accel_rates_for_estimate,
    get_fallback_rapid_rate,
    get_rapid_rates_for_estimate,
    make_stats_cache_key,
    on_estimate_factor_change,
    refresh_gcode_stats_display,
    update_gcode_stats,
    update_live_estimate,
)
from simple_sender.ui.viewer.gcode_viewer import reset_gcode_view_for_run

class GcodeMixin:
    _gcode_streaming_mode: bool
    _gcode_source: LineSource | None

    def _show_resume_dialog(self):
        show_resume_dialog(self)

    def _show_auto_level_dialog(self):
        show_auto_level_dialog(self)

    def _build_resume_preamble(self, lines: LineSource, stop_index: int) -> tuple[list[str], bool]:
        source = lines
        if self._gcode_streaming_mode and self._gcode_source is not None:
            source = self._gcode_source
        return cast(tuple[list[str], bool], build_resume_preamble(source, stop_index))

    def _resume_from_line(self, start_index: int, preamble: list[str]):
        resume_from_line(self, start_index, preamble)

    def _reset_gcode_view_for_run(self):
        reset_gcode_view_for_run(self)

    def _load_gcode_from_path(self, path: str):
        load_gcode_from_path(self, path)

    def _apply_loaded_gcode(
        self,
        path: str,
        lines: list[str],
        *,
        lines_hash: str | None = None,
        validated: bool = False,
        streaming_source=None,
        total_lines: int | None = None,
    ):
        apply_loaded_gcode(
            self,
            path,
            lines,
            lines_hash=lines_hash,
            validated=validated,
            streaming_source=streaming_source,
            total_lines=total_lines,
        )

    def _clear_gcode(self):
        clear_gcode(self)

    def _ensure_gcode_loading_popup(self):
        ensure_gcode_loading_popup(self)

    def _show_gcode_loading(self):
        show_gcode_loading(self)

    def _hide_gcode_loading(self):
        hide_gcode_loading(self)

    def _set_gcode_loading_indeterminate(self, text: str):
        set_gcode_loading_indeterminate(self, text)

    def _set_gcode_loading_progress(self, done: int, total: int, name: str = ""):
        set_gcode_loading_progress(self, done, total, name)

    def _finish_gcode_loading(self):
        finish_gcode_loading(self)

    def _format_throughput(self, bps: float) -> str:
        return cast(str, format_throughput(bps))

    def _estimate_factor_value(self) -> float:
        return cast(float, estimate_factor_value(self))

    def _refresh_gcode_stats_display(self):
        refresh_gcode_stats_display(self)

    def _refresh_dro_display(self):
        refresh_dro_display(self)

    def _sync_tool_reference_label(self):
        app = cast(Any, self)
        try:
            with app.macro_executor.macro_vars() as macro_vars:
                macro_ns = macro_vars.get("macro")
                state = getattr(macro_ns, "state", None)
                tool_ref = getattr(state, "TOOL_REFERENCE", None) if state is not None else None
        except Exception:
            return
        if tool_ref == getattr(app, "_tool_reference_last", None):
            return
        app._tool_reference_last = tool_ref
        if tool_ref is None:
            app.tool_reference_var.set("")
            return
        try:
            value = float(tool_ref)
        except Exception:
            text = str(tool_ref)
        else:
            text = f"{value:.4f}"
        app.tool_reference_var.set(f"Tool Ref: {text}")

    def _on_estimate_factor_change(self, _value=None):
        on_estimate_factor_change(self, _value)

    def _update_live_estimate(self, done: int, total: int):
        update_live_estimate(self, done, total)

    def _maybe_notify_job_completion(self, done: int, total: int) -> None:
        maybe_notify_job_completion(self, done, total)

    def _format_gcode_stats_text(self, stats: dict, rate_source: str | None) -> str:
        return cast(str, format_gcode_stats_text(self, stats, rate_source))

    def _apply_gcode_stats(self, token: int, stats: dict | None, rate_source: str | None):
        apply_gcode_stats(self, token, stats, rate_source)

    def _get_fallback_rapid_rate(self) -> float | None:
        return cast(float | None, get_fallback_rapid_rate(self))

    def _get_rapid_rates_for_estimate(self):
        return get_rapid_rates_for_estimate(self)

    def _get_accel_rates_for_estimate(self):
        return get_accel_rates_for_estimate(self)

    def _make_stats_cache_key(
        self,
        rapid_rates: tuple[float, float, float] | None,
        accel_rates: tuple[float, float, float] | None,
    ):
        return make_stats_cache_key(self, rapid_rates, accel_rates)

    def _update_gcode_stats(self, lines: list[str], parse_result=None):
        update_gcode_stats(self, lines, parse_result=parse_result)

