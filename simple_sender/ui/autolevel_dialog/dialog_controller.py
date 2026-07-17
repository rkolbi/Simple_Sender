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

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import math
import os
import tempfile
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk, messagebox, simpledialog
from typing import Any, cast

from simple_sender.autolevel.grid import AdaptiveGridSpec, ProbeBounds, ProbeGrid, build_adaptive_grid
from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.leveler import (
    DEFAULT_MAX_ABS_CORRECTION_MM,
    DEFAULT_MAX_Z_SPAN_MM,
    LevelFileResult,
)
from simple_sender.autolevel.probe_runner import ProbeRunSettings
from simple_sender.types import (
    AutoLevelInstallationTicket,
    AutoLevelMapProvenance,
    AutoLevelWorkflowLease,
)
from simple_sender.ui.autolevel_state import (
    active_auto_level_map_is_current,
    clear_active_auto_level_map,
)
from simple_sender.ui.dro import convert_units
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import (
    AUTOLEVEL_SPACING_MIN,
    AUTOLEVEL_START_STATE_POLL_MS,
)
from simple_sender.utils.temp_paths import get_preferred_temp_dir

from .calculations import (
    _any_avoidance_enabled,
    _apply_avoidance,
    _coerce_avoidance,
    _parse_avoidance_areas,
)
from .helpers import (
    parse_float_var,
    parse_int_optional_var,
    probe_connection_state,
    safe_float_text,
    update_stats_summary,
    validate_probe_settings_vars,
)
from .io import (
    load_height_map as load_height_map_file,
    save_height_map as save_height_map_file,
    save_leveled as save_leveled_job,
)
from .prefs import pref_float, pref_interp
from .profiles import (
    _merge_auto_level_job_prefs,
    _select_auto_level_profile,
)
from .ui_components import build_avoidance_tab, grid_row

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _post_ui_callback(app, callback, *, context: str) -> bool:
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(callback)
            return True
        except Exception as exc:
            _log_suppressed(f"{context} via _post_ui_thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return True
        except Exception as exc:
            _log_suppressed(f"{context} via ui_q", exc)
    _log_suppressed(context, RuntimeError("ui thread unavailable"))
    return False


@dataclass(frozen=True)
class AutoLevelDialogDependencies:
    tk_module: Any
    ttk_module: Any
    messagebox: Any
    simpledialog: Any
    center_window_fn: Callable[[Any, Any], None]
    set_tab_tooltip_fn: Callable[[Any, Any, str], None]
    apply_auto_level_to_path_fn: Callable[..., tuple[LevelFileResult, bool, str | None]]


def build_auto_level_dialog_dependencies() -> AutoLevelDialogDependencies:
    from simple_sender.ui.dialogs.popup_utils import center_window
    from simple_sender.ui.widgets_tooltips import set_tab_tooltip
    from .workflow import _apply_auto_level_to_path

    return AutoLevelDialogDependencies(
        tk_module=tk,
        ttk_module=ttk,
        messagebox=messagebox,
        simpledialog=simpledialog,
        center_window_fn=center_window,
        set_tab_tooltip_fn=set_tab_tooltip,
        apply_auto_level_to_path_fn=_apply_auto_level_to_path,
    )


class AutoLevelDialogController:
    def __init__(self, app: Any, deps: AutoLevelDialogDependencies) -> None:
        self.app = app
        self.deps = deps
        self.streaming_mode = bool(getattr(app, "_gcode_streaming_mode", False))
        self.last_path = getattr(app, "_last_gcode_path", None)

        self.base_bounds: ProbeBounds | None = None
        self.job_prefs: dict[str, Any] = {}
        self.chosen_profile = "custom"
        self.profile_options = ("small", "large", "custom")
        self.profile_name = "custom"
        self.pending_g90_text = "Pending G90 restore after alarm clears."
        self._active_workflow_token: object | None = None
        self._active_workflow_lease: AutoLevelWorkflowLease | None = None
        self._active_installation_ticket: AutoLevelInstallationTicket | None = None

        self.saved: dict[str, Any] = {}
        self.defaults: AdaptiveGridSpec | None = None
        self.run_defaults: ProbeRunSettings | None = None
        self.interp_saved = "bicubic"
        self.path_order_default = "serpentine"
        self.base_spacing_saved = 5.0
        self._interp_default = "bicubic"

        self.dlg: tk.Toplevel | None = None
        self.frm: ttk.Frame | None = None
        self.notebook: ttk.Notebook | None = None
        self.settings_tab: ttk.Frame | None = None
        self.avoidance_tab: ttk.Frame | None = None

        # Initialized during _init_variables before any callbacks are reachable.
        self.margin_var: tk.StringVar = cast(tk.StringVar, None)
        self.base_spacing_var: tk.StringVar = cast(tk.StringVar, None)
        self.min_spacing_var: tk.StringVar = cast(tk.StringVar, None)
        self.max_spacing_var: tk.StringVar = cast(tk.StringVar, None)
        self.max_points_var: tk.StringVar = cast(tk.StringVar, None)
        self.safe_z_var: tk.StringVar = cast(tk.StringVar, None)
        self.probe_depth_var: tk.StringVar = cast(tk.StringVar, None)
        self.probe_feed_var: tk.StringVar = cast(tk.StringVar, None)
        self.retract_var: tk.StringVar = cast(tk.StringVar, None)
        self.settle_var: tk.StringVar = cast(tk.StringVar, None)
        self.interp_var: tk.StringVar = cast(tk.StringVar, None)
        self.sample_var: tk.StringVar = cast(tk.StringVar, None)
        self.bounds_var: tk.StringVar = cast(tk.StringVar, None)
        self.status_var: tk.StringVar = cast(tk.StringVar, None)
        self.last_test_probe_var: tk.StringVar = cast(tk.StringVar, None)
        self.map_summary_var: tk.StringVar = cast(tk.StringVar, None)
        self.stats_var: tk.StringVar = cast(tk.StringVar, None)
        self.path_order_var: tk.StringVar = cast(tk.StringVar, None)
        self.profile_var: tk.StringVar = cast(tk.StringVar, None)
        self.preset_var: tk.StringVar = cast(tk.StringVar, None)

        self.path_order_options: dict[str, str] = {
            "Serpentine (bottom-left)": "serpentine",
            "Spiral (center)": "spiral",
        }
        self.profile_labels: list[str] = []
        self.job_info_text = ""

        self.avoidance_vars: list[dict[str, tk.Variable]] = []
        self.avoidance_controls: list[Any] = []
        self.grid_state: dict[str, object] = {"grid": None, "skipped_points": []}
        self.original_lines = getattr(app, "_auto_level_original_lines", None)
        self.original_path = getattr(app, "_auto_level_original_path", None)

        self.profile_combo: ttk.Combobox = cast(ttk.Combobox, None)
        self.preset_combo: ttk.Combobox = cast(ttk.Combobox, None)
        self.preset_save_btn: ttk.Button = cast(ttk.Button, None)
        self.preset_delete_btn: ttk.Button = cast(ttk.Button, None)
        self.margin_entry: ttk.Entry = cast(ttk.Entry, None)
        self.base_spacing_entry: ttk.Entry = cast(ttk.Entry, None)
        self.min_spacing_entry: ttk.Entry = cast(ttk.Entry, None)
        self.max_spacing_entry: ttk.Entry = cast(ttk.Entry, None)
        self.max_points_entry: ttk.Entry = cast(ttk.Entry, None)
        self.path_order_combo: ttk.Combobox = cast(ttk.Combobox, None)
        self.safe_z_entry: ttk.Entry = cast(ttk.Entry, None)
        self.probe_depth_entry: ttk.Entry = cast(ttk.Entry, None)
        self.probe_feed_entry: ttk.Entry = cast(ttk.Entry, None)
        self.retract_entry: ttk.Entry = cast(ttk.Entry, None)
        self.settle_entry: ttk.Entry = cast(ttk.Entry, None)
        self.interp_combo: ttk.Combobox = cast(ttk.Combobox, None)
        self.start_btn: ttk.Button = cast(ttk.Button, None)
        self.test_btn: ttk.Button = cast(ttk.Button, None)
        self.apply_btn: ttk.Button = cast(ttk.Button, None)
        self.save_btn: ttk.Button = cast(ttk.Button, None)
        self.save_map_btn: ttk.Button = cast(ttk.Button, None)
        self.load_map_btn: ttk.Button = cast(ttk.Button, None)
        self.revert_btn: ttk.Button = cast(ttk.Button, None)
        self.close_btn: ttk.Button = cast(ttk.Button, None)
        self.progress_bar: ttk.Progressbar = cast(ttk.Progressbar, None)

    def show(self) -> None:
        if not self._prepare_context():
            return
        self._create_dialog()
        self.app._auto_level_dialog_controller = self
        self._load_defaults()
        self._init_variables()
        self._build_ui()
        self._hydrate_existing_state()
        self.update_sample()
        self._poll_start_state()
        if self.dlg is not None:
            self.dlg.protocol("WM_DELETE_WINDOW", self.cancel_probe)
            self.dlg.bind("<Escape>", lambda _event: self.cancel_probe())
            self.deps.center_window_fn(self.dlg, self.app)

    def _resolve_bounds(self) -> Any:
        snapshot = getattr(self.app, "_auto_level_prereq_snapshot", None)
        if isinstance(snapshot, dict):
            snapshot_bounds = snapshot.get("bounds")
            if snapshot_bounds and isinstance(snapshot_bounds, tuple):
                return snapshot_bounds
        quick_bounds = getattr(self.app, "_gcode_bounds_box", None)
        if isinstance(quick_bounds, dict):
            try:
                return (
                    float(quick_bounds.get("min_x", 0.0) or 0.0),
                    float(quick_bounds.get("max_x", 0.0) or 0.0),
                    float(quick_bounds.get("min_y", 0.0) or 0.0),
                    float(quick_bounds.get("max_y", 0.0) or 0.0),
                    float(quick_bounds.get("min_z", 0.0) or 0.0),
                    float(quick_bounds.get("max_z", 0.0) or 0.0),
                )
            except Exception:
                pass
        parse_result = getattr(self.app, "_last_parse_result", None)
        bounds = getattr(parse_result, "bounds", None) if parse_result else None
        if bounds:
            return bounds
        return None

    def _is_al_path(self, path: str) -> bool:
        base = os.path.splitext(os.path.basename(path))[0]
        base_upper = base.upper()
        if base_upper.endswith("-AL"):
            return True
        prefix, sep, suffix = base_upper.rpartition("-AL-")
        return bool(prefix) and bool(sep) and suffix.isdigit()

    def _prepare_context(self) -> bool:
        bounds = self._resolve_bounds()
        if self.streaming_mode and not self.last_path:
            self.deps.messagebox.showwarning("Auto-Level", "Load a G-code file first.")
            return False
        if self.last_path and self._is_al_path(self.last_path):
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "Auto-Level is already applied to this file. Load the original file to re-level.",
            )
            return False
        if not bounds:
            if self.streaming_mode:
                self.deps.messagebox.showwarning(
                    "Auto-Level",
                    "Bounds are not ready yet. Wait for Preparing Job to complete.",
                )
            else:
                self.deps.messagebox.showwarning(
                    "Auto-Level",
                    "Load a G-code file with bounds first.",
                )
            return False

        minx, maxx, miny, maxy = bounds[0], bounds[1], bounds[2], bounds[3]
        base_bounds = ProbeBounds(minx=minx, maxx=maxx, miny=miny, maxy=maxy)
        if base_bounds.width() <= 0 or base_bounds.height() <= 0:
            self.deps.messagebox.showwarning("Auto-Level", "G-code bounds look empty.")
            return False
        self.base_bounds = base_bounds

        default_job_prefs = DEFAULT_SETTINGS.get("auto_level_job_prefs", {})
        raw_job_prefs = getattr(self.app, "auto_level_job_prefs", None)
        self.job_prefs = _merge_auto_level_job_prefs(default_job_prefs, raw_job_prefs)
        self.chosen_profile = _select_auto_level_profile(
            base_bounds.area(),
            self.job_prefs,
            default_job_prefs,
        )
        self.profile_name = (
            self.chosen_profile if self.chosen_profile in self.profile_options else "custom"
        )
        return True

    def _create_dialog(self) -> None:
        dlg = self.deps.tk_module.Toplevel(self.app)
        dlg.title("Auto-Level")
        dlg.transient(self.app)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = self.deps.ttk_module.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        self.dlg = dlg
        self.frm = frm

    def _load_defaults(self) -> None:
        self.saved = dict(getattr(self.app, "auto_level_settings", {}) or {})
        margin_default = float(self.saved.get("margin", 5.0) or 0.0)
        self.base_spacing_saved = float(self.saved.get("base_spacing", 5.0) or 5.0)
        base_spacing_default = self.base_spacing_saved
        min_spacing_default = float(self.saved.get("min_spacing", 2.0) or 2.0)
        max_spacing_default = float(self.saved.get("max_spacing", 12.0) or 12.0)
        max_points_default = self.saved.get("max_points", None)
        self.interp_saved = pref_interp(self.saved.get("interpolation", "bilinear"), "bilinear")
        self.path_order_default = str(self.saved.get("path_order", "serpentine") or "serpentine")
        self.run_defaults = ProbeRunSettings(
            safe_z=float(self.saved.get("safe_z", 5.0) or 0.0),
            probe_depth=float(self.saved.get("probe_depth", 3.0) or 0.0),
            probe_feed=float(self.saved.get("probe_feed", 100.0) or 0.0),
            retract_z=float(self.saved.get("retract_z", 2.0) or 0.0),
            settle_time=float(self.saved.get("settle_time", 0.0) or 0.0),
        )
        self.max_abs_correction_default = pref_float(
            self.saved.get("max_abs_correction"),
            DEFAULT_MAX_ABS_CORRECTION_MM,
        )
        self.max_z_span_default = pref_float(
            self.saved.get("max_z_span"),
            DEFAULT_MAX_Z_SPAN_MM,
        )

        profile = self.job_prefs.get(self.profile_name, {})
        if not isinstance(profile, dict):
            profile = {}
        base_spacing_default = pref_float(profile.get("spacing"), base_spacing_default)
        self._interp_default = pref_interp(profile.get("interpolation"), self.interp_saved)

        self.defaults = AdaptiveGridSpec(
            margin=margin_default,
            base_spacing=base_spacing_default,
            min_spacing=min_spacing_default,
            max_spacing=max_spacing_default,
            max_points=max_points_default,
        )
        base_bounds = self.base_bounds or ProbeBounds(0, 0, 0, 0)
        self.profile_labels = [name.title() for name in self.profile_options]
        self.job_info_text = (
            f"Job size: {base_bounds.width():.2f} x {base_bounds.height():.2f} mm "
            f"({base_bounds.area():.0f} mm^2)\n"
            f"Recommended profile: {self.chosen_profile.title()}"
        )

    def _init_variables(self) -> None:
        defaults = self.defaults or AdaptiveGridSpec(5.0, 5.0, 2.0, 12.0, None)
        run_defaults = self.run_defaults or ProbeRunSettings(5.0, 3.0, 100.0, 2.0, 0.0)

        self.margin_var = tk.StringVar(value=f"{defaults.margin:.2f}")
        self.base_spacing_var = tk.StringVar(value=f"{defaults.base_spacing:.2f}")
        self.min_spacing_var = tk.StringVar(value=f"{defaults.min_spacing:.2f}")
        self.max_spacing_var = tk.StringVar(value=f"{defaults.max_spacing:.2f}")
        self.max_points_var = tk.StringVar(
            value="" if defaults.max_points is None else str(int(defaults.max_points))
        )
        self.safe_z_var = tk.StringVar(value=f"{run_defaults.safe_z:.2f}")
        self.probe_depth_var = tk.StringVar(value=f"{run_defaults.probe_depth:.2f}")
        self.probe_feed_var = tk.StringVar(value=f"{run_defaults.probe_feed:.1f}")
        self.retract_var = tk.StringVar(value=f"{run_defaults.retract_z:.2f}")
        self.settle_var = tk.StringVar(value=f"{run_defaults.settle_time:.2f}")
        self.interp_var = tk.StringVar(value=self._interp_default)
        self.max_abs_correction_var = tk.StringVar(value=f"{self.max_abs_correction_default:.2f}")
        self.max_z_span_var = tk.StringVar(value=f"{self.max_z_span_default:.2f}")
        self.sample_var = tk.StringVar(value="")
        self.bounds_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.last_test_probe_var = tk.StringVar(
            value=str(getattr(self.app, "_auto_level_last_test_probe_result", "") or "")
        )
        self.map_summary_var = tk.StringVar(value="")
        self.stats_var = tk.StringVar(value="")
        self.profile_var = tk.StringVar(value=self.profile_name.title())
        self.path_order_var = tk.StringVar(value=self._path_order_label(self.path_order_default))
        self.preset_var = tk.StringVar(value="")
        self._build_avoidance_variables()

    def _build_avoidance_variables(self) -> None:
        avoidance_count = 8
        default_avoidance = DEFAULT_SETTINGS.get("auto_level_settings", {}).get("avoidance_areas", [])
        if not isinstance(default_avoidance, list) or len(default_avoidance) < avoidance_count:
            default_avoidance = [
                {"enabled": False, "x": 0.0, "y": 0.0, "radius": 20.0, "note": ""}
                for _ in range(avoidance_count)
            ]
        raw_avoidance = self.saved.get("avoidance_areas")
        self.avoidance_vars = []
        for idx in range(avoidance_count):
            fallback = (
                default_avoidance[idx] if idx < len(default_avoidance) else default_avoidance[0]
            )
            if isinstance(raw_avoidance, list) and idx < len(raw_avoidance):
                row = _coerce_avoidance(raw_avoidance[idx], fallback)
            else:
                row = _coerce_avoidance({}, fallback)
            self.avoidance_vars.append(
                {
                    "enabled": tk.BooleanVar(value=bool(row.get("enabled", False))),
                    "x": tk.StringVar(value=f"{float(row.get('x', 0.0)):.2f}"),
                    "y": tk.StringVar(value=f"{float(row.get('y', 0.0)):.2f}"),
                    "radius": tk.StringVar(value=f"{float(row.get('radius', 20.0)):.2f}"),
                    "note": tk.StringVar(value=str(row.get("note", "") or "")),
                }
            )

    def _path_order_label(self, order: str) -> str:
        order = (order or "").strip().lower()
        for label, value in self.path_order_options.items():
            if value == order:
                return label
        return "Serpentine (bottom-left)"

    def _path_order_value(self, label: str) -> str:
        return self.path_order_options.get(label, "serpentine")

    def apply_profile_choice(self, name: str) -> None:
        if not name:
            return
        profile = self.job_prefs.get(name, {})
        if not isinstance(profile, dict):
            profile = {}
        spacing = pref_float(profile.get("spacing"), self.base_spacing_saved)
        self.base_spacing_var.set(f"{spacing:.2f}")
        self.interp_var.set(pref_interp(profile.get("interpolation"), self.interp_saved))
        self.update_sample()

    def preset_snapshot(self) -> dict[str, Any]:
        return {
            "margin": safe_float_text(self.margin_var),
            "base_spacing": safe_float_text(self.base_spacing_var),
            "min_spacing": safe_float_text(self.min_spacing_var),
            "max_spacing": safe_float_text(self.max_spacing_var),
            "max_points": parse_int_optional_var(self.max_points_var),
            "safe_z": safe_float_text(self.safe_z_var),
            "probe_depth": safe_float_text(self.probe_depth_var),
            "probe_feed": safe_float_text(self.probe_feed_var),
            "retract_z": safe_float_text(self.retract_var),
            "settle_time": safe_float_text(self.settle_var),
            "path_order": self._path_order_value(self.path_order_var.get()),
            "interpolation": self.interp_var.get().strip().lower(),
            "max_abs_correction": safe_float_text(self.max_abs_correction_var),
            "max_z_span": safe_float_text(self.max_z_span_var),
        }

    def refresh_preset_values(self) -> None:
        if self.preset_combo is None:
            return
        values = sorted(list((self.app.auto_level_presets or {}).keys()))
        self.preset_combo.configure(values=values)

    def apply_preset(self, name: str) -> None:
        if not name:
            return
        preset = (self.app.auto_level_presets or {}).get(name)
        if not isinstance(preset, dict):
            return
        defaults = self.defaults or AdaptiveGridSpec(5.0, 5.0, 2.0, 12.0, None)
        run_defaults = self.run_defaults or ProbeRunSettings(5.0, 3.0, 100.0, 2.0, 0.0)
        self.margin_var.set(f"{float(preset.get('margin', defaults.margin)):.2f}")
        self.base_spacing_var.set(f"{float(preset.get('base_spacing', defaults.base_spacing)):.2f}")
        self.min_spacing_var.set(f"{float(preset.get('min_spacing', defaults.min_spacing)):.2f}")
        self.max_spacing_var.set(f"{float(preset.get('max_spacing', defaults.max_spacing)):.2f}")
        max_points = preset.get("max_points", None)
        self.max_points_var.set("" if max_points is None else str(int(max_points)))
        self.path_order_var.set(
            self._path_order_label(str(preset.get("path_order", self.path_order_default)))
        )
        self.safe_z_var.set(f"{float(preset.get('safe_z', run_defaults.safe_z)):.2f}")
        self.probe_depth_var.set(f"{float(preset.get('probe_depth', run_defaults.probe_depth)):.2f}")
        self.probe_feed_var.set(f"{float(preset.get('probe_feed', run_defaults.probe_feed)):.1f}")
        self.retract_var.set(f"{float(preset.get('retract_z', run_defaults.retract_z)):.2f}")
        self.settle_var.set(f"{float(preset.get('settle_time', run_defaults.settle_time)):.2f}")
        self.interp_var.set(str(preset.get("interpolation", self.interp_var.get())))
        self.max_abs_correction_var.set(
            f"{float(preset.get('max_abs_correction', self.max_abs_correction_default)):.2f}"
        )
        self.max_z_span_var.set(f"{float(preset.get('max_z_span', self.max_z_span_default)):.2f}")
        self.update_sample()

    def save_preset(self) -> None:
        self.update_sample()
        if self.grid_state.get("grid") is None:
            self.deps.messagebox.showwarning("Save preset", "Fix the grid settings before saving.")
            return
        errors = validate_probe_settings_vars(
            self.safe_z_var,
            self.probe_depth_var,
            self.probe_feed_var,
            self.retract_var,
            self.settle_var,
        )
        if errors:
            self.deps.messagebox.showwarning("Save preset", errors[0])
            return
        try:
            snapshot = self.preset_snapshot()
        except ValueError:
            self.deps.messagebox.showwarning("Save preset", "Fix the settings before saving.")
            return
        name = self.deps.simpledialog.askstring("Save preset", "Preset name:", parent=self.dlg)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if self.app.auto_level_presets is None:
            self.app.auto_level_presets = {}
        if name in self.app.auto_level_presets:
            if not self.deps.messagebox.askyesno("Save preset", f"Overwrite preset '{name}'?"):
                return
        self.app.auto_level_presets[name] = snapshot
        self._persist_auto_level_presets()
        self.refresh_preset_values()
        self.preset_var.set(name)

    def delete_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            self.deps.messagebox.showwarning("Delete preset", "Choose a preset to delete.")
            return
        if not self.deps.messagebox.askyesno("Delete preset", f"Delete preset '{name}'?"):
            return
        if name in self.app.auto_level_presets:
            del self.app.auto_level_presets[name]
        self._persist_auto_level_presets()
        self.refresh_preset_values()
        self.preset_var.set("")

    def _persist_auto_level_presets(self) -> None:
        try:
            self.app.settings["auto_level_presets"] = dict(self.app.auto_level_presets)
        except (AttributeError, TypeError) as exc:
            _log_suppressed("Failed persisting auto-level presets to settings", exc)

    def _avoidance_snapshot(self) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for row in self.avoidance_vars:
            snapshot.append(
                {
                    "enabled": bool(row["enabled"].get()),
                    "x": safe_float_text(row["x"]),
                    "y": safe_float_text(row["y"]),
                    "radius": safe_float_text(row["radius"]),
                    "note": str(row["note"].get()),
                }
            )
        return snapshot

    def _current_wpos_mm(self) -> tuple[float, float] | None:
        if not getattr(self.app, "connected", False):
            return None
        if not getattr(self.app, "_status_seen", False):
            return None
        wpos = getattr(self.app, "_wpos_raw", None)
        if not wpos or len(wpos) < 2:
            return None
        report_units = getattr(self.app, "_report_units", None)
        if not report_units:
            unit_mode = getattr(self.app, "unit_mode", None)
            try:
                report_units = unit_mode.get() if unit_mode is not None else "mm"
            except Exception:
                report_units = "mm"
        try:
            return (
                convert_units(float(wpos[0]), report_units, "mm"),
                convert_units(float(wpos[1]), report_units, "mm"),
            )
        except Exception:
            return None

    def set_avoidance_from_position(self, row_index: int) -> None:
        pos = self._current_wpos_mm()
        if pos is None:
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "Position unavailable. Connect and wait for status.",
            )
            return
        x_mm, y_mm = pos
        try:
            self.avoidance_vars[row_index]["x"].set(f"{x_mm:.2f}")
            self.avoidance_vars[row_index]["y"].set(f"{y_mm:.2f}")
        except Exception:
            return
        self.update_sample()

    def set_start_state(self) -> None:
        self._set_test_probe_state()
        if self.start_btn is None:
            return
        if self.app.auto_level_runner.is_running():
            self.start_btn.config(state="disabled")
            return
        grid = self.grid_state.get("grid")
        if not isinstance(grid, ProbeGrid):
            self.start_btn.config(state="disabled")
            return
        errors = validate_probe_settings_vars(
            self.safe_z_var,
            self.probe_depth_var,
            self.probe_feed_var,
            self.retract_var,
            self.settle_var,
        )
        if errors:
            self.status_var.set(f"Probe settings: {errors[0]}")
            self.start_btn.config(state="disabled")
            return
        ready, reason = probe_connection_state(self.app)
        if not ready:
            self.status_var.set(reason)
            self.start_btn.config(state="disabled")
            return
        if self.status_var.get().startswith(
            ("Probe settings:", "Connect", "Waiting for GRBL", "Clear the alarm")
        ):
            self.status_var.set("")
        self.start_btn.config(state="normal")
        self._sync_pending_g90_notice()

    def _set_test_probe_state(self) -> None:
        if self.test_btn is None:
            return
        if self.app.auto_level_runner.is_running():
            self.test_btn.config(state="disabled")
            return
        errors = validate_probe_settings_vars(
            self.safe_z_var,
            self.probe_depth_var,
            self.probe_feed_var,
            self.retract_var,
            self.settle_var,
        )
        if errors:
            self.test_btn.config(state="disabled")
            return
        ready, _reason = probe_connection_state(self.app)
        if not ready:
            self.test_btn.config(state="disabled")
            return
        if self._current_wpos_mm() is None:
            self.test_btn.config(state="disabled")
            return
        self.test_btn.config(state="normal")

    def _record_last_test_probe_result(self, text: str) -> None:
        value = str(text or "")
        try:
            self.app._auto_level_last_test_probe_result = value
        except Exception as exc:
            _log_suppressed("Failed storing last test probe result on app", exc)
        try:
            self.last_test_probe_var.set(value)
        except Exception as exc:
            _log_suppressed("Failed updating last test probe result variable", exc)

    def _parse_probe_run_settings(self) -> ProbeRunSettings:
        return ProbeRunSettings(
            safe_z=parse_float_var(self.safe_z_var, "safe Z"),
            probe_depth=parse_float_var(self.probe_depth_var, "probe depth"),
            probe_feed=parse_float_var(self.probe_feed_var, "probe feed"),
            retract_z=parse_float_var(self.retract_var, "retract Z"),
            settle_time=parse_float_var(self.settle_var, "settle time"),
        )

    def _sync_pending_g90_notice(self) -> None:
        if getattr(self.app, "_pending_force_g90", False):
            if not self.status_var.get():
                self.status_var.set(self.pending_g90_text)
            return
        if self.status_var.get() == self.pending_g90_text:
            self.status_var.set("")

    def _has_complete_height_map(self) -> bool:
        if not active_auto_level_map_is_current(self.app):
            return False
        height_map = getattr(self.app, "_auto_level_height_map", None)
        if height_map is None:
            return False
        try:
            return bool(height_map.is_complete())
        except Exception as exc:
            _log_suppressed("Failed checking auto-level height-map completeness", exc)
            return False

    def handle_active_map_invalidated(self, reason: str) -> None:
        if self.apply_btn is not None:
            self.apply_btn.config(state="disabled")
        if self.save_map_btn is not None:
            self.save_map_btn.config(state="disabled")
        if self.map_summary_var is not None:
            self.map_summary_var.set("No trusted active height map.")
        if self.stats_var is not None:
            self.stats_var.set("")
        if self.status_var is not None and reason:
            self.status_var.set(str(reason))

    def _has_leveled_output(self) -> bool:
        leveled_lines = getattr(self.app, "_auto_level_leveled_lines", None)
        return bool(
            (isinstance(leveled_lines, list) and bool(leveled_lines))
            or getattr(self.app, "_auto_level_leveled_path", None)
        )

    def _has_original_output(self) -> bool:
        original_lines = getattr(self.app, "_auto_level_original_lines", None)
        return bool(
            (isinstance(original_lines, list) and bool(original_lines))
            or getattr(self.app, "_auto_level_original_path", None)
            or getattr(self.app, "_auto_level_original_source_path", None)
        )

    def _sync_action_button_states(self) -> None:
        map_ready = self._has_complete_height_map()
        if self.apply_btn is not None:
            self.apply_btn.config(state="normal" if map_ready else "disabled")
        if self.save_map_btn is not None:
            self.save_map_btn.config(state="normal" if map_ready else "disabled")
        if self.save_btn is not None:
            self.save_btn.config(state="normal" if self._has_leveled_output() else "disabled")
        if self.revert_btn is not None:
            self.revert_btn.config(state="normal" if self._has_original_output() else "disabled")
        if self.load_map_btn is not None:
            self.load_map_btn.config(state="normal")

    def update_sample(self) -> None:
        if self.base_bounds is None:
            return
        try:
            margin = max(0.0, parse_float_var(self.margin_var, "margin"))
            base_spacing = max(
                AUTOLEVEL_SPACING_MIN,
                parse_float_var(self.base_spacing_var, "base spacing"),
            )
            min_spacing = max(
                AUTOLEVEL_SPACING_MIN,
                parse_float_var(self.min_spacing_var, "min spacing"),
            )
            max_spacing = max(
                min_spacing,
                parse_float_var(self.max_spacing_var, "max spacing"),
            )
            max_points = parse_int_optional_var(self.max_points_var)
            path_order = self._path_order_value(self.path_order_var.get())
            avoidance_areas = _parse_avoidance_areas(self.avoidance_vars)
            max_abs_correction = parse_float_var(self.max_abs_correction_var, "max correction")
            max_z_span = parse_float_var(self.max_z_span_var, "max Z span")
            if max_abs_correction <= 0:
                raise ValueError("Max correction must be > 0.")
            if max_z_span <= 0:
                raise ValueError("Max Z span must be > 0.")
        except ValueError as exc:
            self.sample_var.set(str(exc))
            self.bounds_var.set("")
            self.grid_state["grid"] = None
            self.grid_state["skipped_points"] = []
            if self.start_btn is not None:
                self.start_btn.config(state="disabled")
            return

        self.app.auto_level_settings = {
            "margin": margin,
            "base_spacing": base_spacing,
            "min_spacing": min_spacing,
            "max_spacing": max_spacing,
            "max_points": max_points,
            "safe_z": safe_float_text(self.safe_z_var),
            "probe_depth": safe_float_text(self.probe_depth_var),
            "probe_feed": safe_float_text(self.probe_feed_var),
            "retract_z": safe_float_text(self.retract_var),
            "settle_time": safe_float_text(self.settle_var),
            "path_order": path_order,
            "interpolation": self.interp_var.get().strip().lower(),
            "max_abs_correction": max_abs_correction,
            "max_z_span": max_z_span,
            "avoidance_areas": self._avoidance_snapshot(),
        }
        try:
            self.app.settings["auto_level_settings"] = dict(self.app.auto_level_settings)
        except Exception as exc:
            _log_suppressed("Failed persisting auto-level settings snapshot", exc)

        spec = AdaptiveGridSpec(
            base_spacing=base_spacing,
            min_spacing=min_spacing,
            max_spacing=max_spacing,
            margin=margin,
            max_points=max_points,
        )
        grid = build_adaptive_grid(self.base_bounds, spec, path_order=path_order)
        grid, skipped_points = _apply_avoidance(grid, avoidance_areas)
        if grid.point_count() == 0:
            self.sample_var.set("Avoidance areas exclude all probe points.")
            self.bounds_var.set("")
            self.grid_state["grid"] = None
            self.grid_state["skipped_points"] = skipped_points
            if self.start_btn is not None:
                self.start_btn.config(state="disabled")
            return

        self.grid_state["grid"] = grid
        self.grid_state["skipped_points"] = skipped_points
        self.sample_var.set(
            f"Grid: {len(grid.xs)} x {len(grid.ys)} ({grid.point_count()} points) "
            f"Spacing: {grid.spacing_x:.2f} x {grid.spacing_y:.2f} mm"
        )
        self.bounds_var.set(
            f"Probe area: {grid.bounds.width():.2f} x {grid.bounds.height():.2f} mm "
            f"(margin {grid.margin:.2f} mm)"
        )
        self.map_summary_var.set(
            f"Grid spacing: {grid.spacing_x:.2f} x {grid.spacing_y:.2f} mm"
        )
        self.set_start_state()

    def start_probe(self) -> None:
        self.update_sample()
        try:
            _parse_avoidance_areas(self.avoidance_vars)
        except ValueError as exc:
            self.deps.messagebox.showwarning("Auto-Level", str(exc))
            return
        if not _any_avoidance_enabled(self.avoidance_vars):
            proceed = self.deps.messagebox.askokcancel(
                "Auto-Level",
                "No avoidance areas are configured. Continue anyway?\n"
                "Select Cancel to configure avoidance areas first.",
            )
            if not proceed:
                return
        grid = self.grid_state.get("grid")
        if not isinstance(grid, ProbeGrid):
            return
        errors = validate_probe_settings_vars(
            self.safe_z_var,
            self.probe_depth_var,
            self.probe_feed_var,
            self.retract_var,
            self.settle_var,
        )
        if errors:
            self.deps.messagebox.showwarning("Auto-Level", errors[0])
            return
        ready, reason = probe_connection_state(self.app)
        if not ready:
            self.deps.messagebox.showwarning("Auto-Level", reason)
            return
        if not self.app._require_grbl_connection():
            return
        if self.app._alarm_locked:
            self.deps.messagebox.showwarning("Auto-Level", "Clear the alarm before probing.")
            return
        if not self.deps.messagebox.askokcancel(
            "Auto-Level",
            "Confirm Z0 is set to the surface plane before probing.\n"
            "Offsets are applied relative to this reference.",
        ):
            return

        height_map = HeightMap(grid.xs, grid.ys)
        skipped_points = self.grid_state.get("skipped_points", [])
        if isinstance(skipped_points, list):
            for px, py in skipped_points:
                height_map.mark_invalid(px, py)
        try:
            settings = self._parse_probe_run_settings()
        except ValueError as exc:
            self.deps.messagebox.showwarning("Auto-Level", str(exc))
            return

        self.progress_bar.configure(maximum=grid.point_count(), value=0)
        self.status_var.set("Probing...")
        self._set_controls_enabled(False)
        self.apply_btn.config(state="disabled")
        if self.save_map_btn is not None:
            self.save_map_btn.config(state="disabled")

        workflow_token = object()
        self._active_workflow_token = workflow_token

        def on_lease_acquired(lease: AutoLevelWorkflowLease) -> None:
            if self._active_workflow_token is workflow_token:
                clear_active_auto_level_map(
                    self.app,
                    "A new Auto-Level workflow is collecting a replacement map.",
                )
                self._active_workflow_lease = lease
                self.app._set_manual_controls_enabled(False)

        def on_progress(done: int, total: int) -> None:
            def update() -> None:
                if self._active_workflow_token is not workflow_token:
                    return
                self.progress_bar.configure(value=done)
                self.status_var.set(f"Probing {done}/{total}")

            _post_ui_callback(
                self.app,
                update,
                context="Failed posting auto-level probe progress callback",
            )

        def on_done(
            lease: AutoLevelWorkflowLease,
            installation_ticket: AutoLevelInstallationTicket | None,
            ok: bool,
            reason: str | None,
        ) -> None:
            def finish() -> None:
                if (
                    self._active_workflow_token is not workflow_token
                    or self._active_workflow_lease is not lease
                ):
                    if installation_ticket is not None:
                        self.app.grbl.abort_auto_level_installation(
                            installation_ticket,
                            "Stale Auto-Level UI completion was retired before installation.",
                        )
                    return
                self._active_installation_ticket = installation_ticket
                recovery_checker = getattr(
                    getattr(self.app, "grbl", None), "recovery_required", None
                )
                recovery_required = bool(recovery_checker()) if callable(recovery_checker) else False
                if recovery_required:
                    if installation_ticket is not None:
                        self.app.grbl.abort_auto_level_installation(
                            installation_ticket,
                            "Recovery began before Auto-Level map installation.",
                        )
                    self._active_workflow_token = None
                    self._active_workflow_lease = None
                    self._active_installation_ticket = None
                    clear_active_auto_level_map(
                        self.app,
                        "Auto-Level result was invalidated by controller recovery.",
                    )
                    self._set_controls_enabled(False)
                    if self.close_btn is not None:
                        self.close_btn.config(text="Close", state="normal")
                    self.status_var.set(
                        "Probe canceled; controller Recovery Required before further machine use."
                    )
                    return
                installation_provenance: AutoLevelMapProvenance | None = None
                stats = None
                if ok and installation_ticket is not None:
                    try:
                        stats = height_map.stats()

                        def commit_staged_map(
                            provenance: AutoLevelMapProvenance,
                        ) -> bool:
                            previous = (
                                getattr(self.app, "_auto_level_grid", None),
                                getattr(self.app, "_auto_level_height_map", None),
                                getattr(self.app, "_auto_level_bounds", None),
                                getattr(self.app, "_auto_level_map_provenance", None),
                            )
                            try:
                                (
                                    self.app._auto_level_grid,
                                    self.app._auto_level_height_map,
                                    self.app._auto_level_bounds,
                                    self.app._auto_level_map_provenance,
                                ) = (grid, height_map, grid.bounds, provenance)
                            except Exception:
                                (
                                    self.app._auto_level_grid,
                                    self.app._auto_level_height_map,
                                    self.app._auto_level_bounds,
                                    self.app._auto_level_map_provenance,
                                ) = previous
                                return False
                            return True

                        installation_provenance = (
                            self.app.grbl.finalize_auto_level_installation(
                                installation_ticket,
                                commit_staged_map,
                            )
                        )
                    except Exception as exc:
                        _log_suppressed(
                            "Auto-Level map installation finalizer failed", exc
                        )
                        self.app.grbl.abort_auto_level_installation(
                            installation_ticket,
                            "Auto-Level map installation finalizer raised.",
                        )

                installed = bool(
                    installation_provenance is not None
                    and active_auto_level_map_is_current(self.app)
                )
                self._active_workflow_token = None
                self._active_workflow_lease = None
                self._active_installation_ticket = None
                if ok and installed:
                    self._set_controls_enabled(True)
                    self.app._set_manual_controls_enabled(True)
                    stats = height_map.stats()
                    if stats:
                        self.status_var.set(
                            "Done. "
                            f"Min {stats.min_z:.4f} Max {stats.max_z:.4f} Span {stats.span():.4f} mm, "
                            f"RMS {stats.rms_roughness:.4f} mm, "
                            f"Outliers {stats.outliers}/{stats.point_count}"
                        )
                        if abs(stats.mean_z) > 1.0:
                            self.deps.messagebox.showwarning(
                                "Auto-Level",
                                "Probe average is far from Z0. "
                                "Verify Z0 is set to the surface before applying.",
                            )
                    else:
                        self.status_var.set("Done.")
                    update_stats_summary(height_map, self.stats_var)
                    self.apply_btn.config(state="normal")
                    if self.save_map_btn is not None:
                        self.save_map_btn.config(state="normal")
                else:
                    clear_active_auto_level_map(
                        self.app,
                        "Auto-Level map installation did not finalize.",
                    )
                    self._set_controls_enabled(True)
                    self.app._set_manual_controls_enabled(True)
                    self.apply_btn.config(state="disabled")
                    if self.save_map_btn is not None:
                        self.save_map_btn.config(state="disabled")
                    detail = reason or (
                        "stale Auto-Level installation was rejected"
                        if ok
                        else "failed"
                    )
                    message = f"Probe stopped: {detail}"
                    self.status_var.set(message)

            posted = _post_ui_callback(
                self.app,
                finish,
                context="Failed posting auto-level probe completion callback",
            )
            if not posted and installation_ticket is not None:
                self.app.grbl.abort_auto_level_installation(
                    installation_ticket,
                    "Auto-Level UI installation callback could not be posted.",
                )

        started = self.app.auto_level_runner.start(
            grid,
            height_map,
            settings,
            on_progress=on_progress,
            on_done=on_done,
            on_lease_acquired=on_lease_acquired,
        )
        if not started:
            self._active_workflow_token = None
            self._active_workflow_lease = None
            self._set_controls_enabled(True)
            self.status_var.set("Probe start failed.")

    def test_probe(self) -> None:
        errors = validate_probe_settings_vars(
            self.safe_z_var,
            self.probe_depth_var,
            self.probe_feed_var,
            self.retract_var,
            self.settle_var,
        )
        if errors:
            self.deps.messagebox.showwarning("Auto-Level", errors[0])
            return
        ready, reason = probe_connection_state(self.app)
        if not ready:
            self.deps.messagebox.showwarning("Auto-Level", reason)
            return
        if self.app.auto_level_runner.is_running():
            return
        pos = self._current_wpos_mm()
        if pos is None:
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "Position unavailable. Connect and wait for status.",
            )
            return
        proceed = self.deps.messagebox.askokcancel(
            "Auto-Level",
            "A single probe test will be performed at the current XY position.\n"
            "This does not update the height map.\n"
            "Confirm probe wiring is correct before continuing.",
        )
        if not proceed:
            return
        try:
            settings = self._parse_probe_run_settings()
        except ValueError as exc:
            self.deps.messagebox.showwarning("Auto-Level", str(exc))
            return
        x_mm, y_mm = pos
        test_bounds = ProbeBounds(minx=x_mm, maxx=x_mm, miny=y_mm, maxy=y_mm)
        test_grid = ProbeGrid(
            bounds=test_bounds,
            xs=[x_mm],
            ys=[y_mm],
            points=[(x_mm, y_mm)],
            spacing_x=0.0,
            spacing_y=0.0,
            margin=0.0,
        )
        test_map = HeightMap(test_grid.xs, test_grid.ys)

        action_states = {
            "apply": self.apply_btn.cget("state") if self.apply_btn is not None else "disabled",
            "save_map": self.save_map_btn.cget("state") if self.save_map_btn is not None else "disabled",
            "save": self.save_btn.cget("state") if self.save_btn is not None else "disabled",
            "revert": self.revert_btn.cget("state") if self.revert_btn is not None else "disabled",
        }

        self.progress_bar.configure(maximum=1, value=0)
        self.status_var.set("Running test probe...")
        self._set_controls_enabled(False)

        workflow_token = object()
        self._active_workflow_token = workflow_token

        def on_lease_acquired(lease: AutoLevelWorkflowLease) -> None:
            if self._active_workflow_token is workflow_token:
                self._active_workflow_lease = lease
                self.app._set_manual_controls_enabled(False)

        def on_progress(done: int, total: int) -> None:
            def update() -> None:
                if self._active_workflow_token is not workflow_token:
                    return
                self.progress_bar.configure(value=done)
                self.status_var.set(f"Test probe {done}/{total}")

            _post_ui_callback(
                self.app,
                update,
                context="Failed posting auto-level test-probe progress callback",
            )

        def on_done(
            lease: AutoLevelWorkflowLease,
            installation_ticket: AutoLevelInstallationTicket | None,
            ok: bool,
            reason: str | None,
        ) -> None:
            def finish() -> None:
                if (
                    self._active_workflow_token is not workflow_token
                    or self._active_workflow_lease is not lease
                ):
                    return
                recovery_checker = getattr(
                    getattr(self.app, "grbl", None), "recovery_required", None
                )
                recovery_required = bool(recovery_checker()) if callable(recovery_checker) else False
                completion_accepted = bool(
                    ok and not recovery_required and installation_ticket is None
                )
                self._active_workflow_token = None
                self._active_workflow_lease = None
                if recovery_required:
                    self._set_controls_enabled(False)
                    if self.close_btn is not None:
                        self.close_btn.config(text="Close", state="normal")
                    self.status_var.set(
                        "Test probe canceled; controller Recovery Required before further machine use."
                    )
                    return
                self._set_controls_enabled(True)
                self.app._set_manual_controls_enabled(True)
                try:
                    map_current = active_auto_level_map_is_current(self.app)
                    if self.apply_btn is not None:
                        self.apply_btn.config(
                            state=action_states["apply"] if map_current else "disabled"
                        )
                    if self.save_map_btn is not None:
                        self.save_map_btn.config(
                            state=action_states["save_map"] if map_current else "disabled"
                        )
                    if self.save_btn is not None:
                        self.save_btn.config(state=action_states["save"])
                    if self.revert_btn is not None:
                        self.revert_btn.config(state=action_states["revert"])
                except Exception as exc:
                    _log_suppressed("Failed restoring action button states after test probe", exc)
                self.set_start_state()
                if ok and completion_accepted:
                    measured = test_map.get_index(0, 0)
                    if measured is None:
                        report = getattr(self.app.probe_controller, "last_report", lambda: None)()
                        if report is not None:
                            measured = float(getattr(report, "z", 0.0))
                    timestamp = time.strftime("%H:%M:%S")
                    z_text = f"{measured:.4f}" if measured is not None else "n/a"
                    self._record_last_test_probe_result(
                        f"Last test probe [{timestamp}]: X{x_mm:.3f} Y{y_mm:.3f} Z{z_text} mm"
                    )
                    if measured is None:
                        self.status_var.set("Test probe complete.")
                    else:
                        self.status_var.set(
                            f"Test probe hit: X{x_mm:.3f} Y{y_mm:.3f} Z{measured:.4f} mm"
                        )
                else:
                    timestamp = time.strftime("%H:%M:%S")
                    detail = reason or (
                        "stale Auto-Level completion was rejected"
                        if ok
                        else "failed"
                    )
                    message = f"Test probe failed: {detail}"
                    self._record_last_test_probe_result(
                        f"Last test probe [{timestamp}]: X{x_mm:.3f} Y{y_mm:.3f} failed ({detail})"
                    )
                    self.status_var.set(message)

            _post_ui_callback(
                self.app,
                finish,
                context="Failed posting auto-level test-probe completion callback",
            )

        started = self.app.auto_level_runner.start(
            test_grid,
            test_map,
            settings,
            on_progress=on_progress,
            on_done=on_done,
            on_lease_acquired=on_lease_acquired,
            result_installation_required=False,
        )
        if not started:
            self._active_workflow_token = None
            self._active_workflow_lease = None
            self._set_controls_enabled(True)
            try:
                if self.apply_btn is not None:
                    self.apply_btn.config(state=action_states["apply"])
                if self.save_map_btn is not None:
                    self.save_map_btn.config(state=action_states["save_map"])
                if self.save_btn is not None:
                    self.save_btn.config(state=action_states["save"])
                if self.revert_btn is not None:
                    self.revert_btn.config(state=action_states["revert"])
            except Exception as exc:
                _log_suppressed("Failed restoring action button states after test probe start failure", exc)
            self.set_start_state()
            self.status_var.set("Test probe start failed.")

    def _make_output_path(self, source_path: str) -> str:
        base, ext = os.path.splitext(os.path.basename(source_path))
        ext = ext if ext else ".gcode"
        dir_name = os.path.dirname(source_path) or "."
        candidate = os.path.join(dir_name, f"{base}-AL{ext}")
        if not os.path.exists(candidate):
            return candidate
        for idx in range(1, 1000):
            candidate = os.path.join(dir_name, f"{base}-AL-{idx}{ext}")
            if not os.path.exists(candidate):
                return candidate
        return os.path.join(dir_name, f"{base}-AL-{int(time.time())}{ext}")

    def _make_temp_path(self, source_path: str) -> str:
        base, ext = os.path.splitext(os.path.basename(source_path))
        ext = ext if ext else ".gcode"
        temp_dir = get_preferred_temp_dir()
        temp = tempfile.NamedTemporaryFile(
            prefix=f"{base}_leveled_",
            suffix=ext,
            delete=False,
            dir=temp_dir,
        )
        temp_path = temp.name
        temp.close()
        return temp_path

    def _header_lines_for(self, source_path: str) -> list[str]:
        source_name = os.path.basename(source_path)
        max_len = 40
        if len(source_name) > max_len:
            source_name = f"{source_name[: max_len - 3]}..."
        return [f"(Auto-Level from {source_name})"]

    def _resolve_level_source(self, display_path: str) -> tuple[str, list[str] | None]:
        source_path = str(getattr(self.app, "_auto_level_job_source_path", "") or "").strip()
        if source_path:
            try:
                if os.path.isfile(source_path):
                    return source_path, None
            except Exception as exc:
                _log_suppressed("Failed checking cached auto-level source path", exc)
        source_obj = getattr(self.app, "_gcode_source", None)
        source_obj_path = str(getattr(source_obj, "path", "") or "").strip()
        if source_obj_path:
            try:
                if os.path.isfile(source_obj_path):
                    return source_obj_path, None
            except Exception as exc:
                _log_suppressed("Failed checking active G-code source path for auto-level", exc)
        if display_path:
            try:
                if os.path.isfile(display_path):
                    return display_path, None
            except Exception as exc:
                _log_suppressed("Failed checking loaded G-code path for auto-level", exc)
        lines = getattr(self.app, "_last_gcode_lines", None)
        if isinstance(lines, list) and lines:
            return display_path, lines
        return display_path, None

    def apply_level(self) -> None:
        if not active_auto_level_map_is_current(self.app):
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "The height map is inactive or no longer matches the current controller session and job.",
            )
            return
        map_provenance = getattr(self.app, "_auto_level_map_provenance", None)
        height_map = getattr(self.app, "_auto_level_height_map", None)
        if height_map is None or not height_map.is_complete():
            self.deps.messagebox.showwarning("Auto-Level", "Probe a complete grid before applying.")
            return
        path = getattr(self.app, "_last_gcode_path", None)
        if not path:
            self.deps.messagebox.showwarning("Auto-Level", "Load a G-code file first.")
            return
        if self._is_al_path(path):
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "Auto-Level is already applied to this file. Load the original file to re-level.",
            )
            return

        lines = getattr(self.app, "_last_gcode_lines", None) or []
        source_path, source_lines = self._resolve_level_source(path)
        line_count = getattr(self.app, "_gcode_total_lines", None) or len(lines)
        arc_step = math.pi / 18
        _ = line_count
        method = self.interp_var.get().strip().lower()
        try:
            max_abs_correction = parse_float_var(self.max_abs_correction_var, "max correction")
            max_z_span = parse_float_var(self.max_z_span_var, "max Z span")
            if max_abs_correction <= 0:
                raise ValueError("Max correction must be > 0.")
            if max_z_span <= 0:
                raise ValueError("Max Z span must be > 0.")
        except ValueError as exc:
            self.deps.messagebox.showwarning("Auto-Level", str(exc))
            return

        action_states = {
            "apply": self.apply_btn.cget("state") if self.apply_btn is not None else "disabled",
            "save_map": self.save_map_btn.cget("state") if self.save_map_btn is not None else "disabled",
            "save": self.save_btn.cget("state") if self.save_btn is not None else "disabled",
            "revert": self.revert_btn.cget("state") if self.revert_btn is not None else "disabled",
        }
        self.status_var.set("Applying height map to file...")
        self._set_controls_enabled(False)
        self.apply_btn.config(state="disabled")

        def worker() -> None:
            removed_previous_temp = False
            old_path = getattr(self.app, "_auto_level_leveled_path", None)
            if getattr(self.app, "_auto_level_leveled_temp", False) and old_path:
                try:
                    os.remove(old_path)
                    removed_previous_temp = True
                except OSError as exc:
                    _log_suppressed("Failed removing previous temporary leveled file before regeneration", exc)
            try:
                output_path = self._make_output_path(path)
                header_lines = self._header_lines_for(path)
                log_fn = None
                ui_q = getattr(self.app, "ui_q", None)
                if ui_q is not None:
                    def _log_fn(msg: str) -> None:
                        ui_q.put(("log", msg))

                    log_fn = _log_fn

                result, is_temp, fallback_warning = self.deps.apply_auto_level_to_path_fn(
                    source_path=source_path,
                    source_lines=source_lines,
                    output_path=output_path,
                    temp_path_fn=self._make_temp_path,
                    height_map=height_map,
                    arc_step_rad=arc_step,
                    interpolation=method,
                    max_abs_correction=max_abs_correction,
                    max_z_span=max_z_span,
                    header_lines=header_lines,
                    streaming_mode=self.streaming_mode,
                    log_fn=log_fn,
                )
            except Exception as exc:
                _log_suppressed("Auto-Level apply worker failed unexpectedly", exc)
                result = LevelFileResult(None, 0, str(exc), False)
                is_temp = False
                fallback_warning = None

            def _restore_action_states() -> None:
                try:
                    if self.apply_btn is not None:
                        self.apply_btn.config(state=action_states["apply"])
                    if self.save_map_btn is not None:
                        self.save_map_btn.config(state=action_states["save_map"])
                    if self.save_btn is not None:
                        self.save_btn.config(state=action_states["save"])
                    if self.revert_btn is not None:
                        self.revert_btn.config(state=action_states["revert"])
                except Exception as exc:
                    _log_suppressed("Failed restoring auto-level action button states", exc)

            def on_done() -> None:
                self._set_controls_enabled(True)
                if (
                    getattr(self.app, "_auto_level_map_provenance", None)
                    is not map_provenance
                    or not active_auto_level_map_is_current(self.app)
                ):
                    clear_active_auto_level_map(
                        self.app,
                        "Auto-Level apply was discarded because the map context changed.",
                    )
                    if result.output_path and is_temp:
                        try:
                            os.remove(result.output_path)
                        except OSError as exc:
                            _log_suppressed(
                                "Failed removing stale temporary Auto-Level output", exc
                            )
                    self.status_var.set(
                        "Auto-Level apply canceled: the map is no longer trusted."
                    )
                    return
                if result.error:
                    _restore_action_states()
                    if removed_previous_temp:
                        self.app._auto_level_leveled_lines = None
                        self.app._auto_level_leveled_path = None
                        self.app._auto_level_leveled_temp = False
                        self.app._auto_level_leveled_name = None
                        restore_state = getattr(self.app, "_auto_level_restore", None)
                        if isinstance(restore_state, dict):
                            restore_state["leveled_lines"] = None
                            restore_state["leveled_path"] = None
                            restore_state["leveled_temp"] = False
                            restore_state["leveled_name"] = None
                    self.status_var.set("Auto-Level apply failed.")
                    self.deps.messagebox.showerror("Auto-Level", result.error)
                    return
                if (
                    not self.streaming_mode
                    and not isinstance(getattr(self.app, "_auto_level_original_lines", None), list)
                    and lines
                ):
                    self.app._auto_level_original_lines = list(lines)
                if not getattr(self.app, "_auto_level_original_path", None):
                    self.app._auto_level_original_path = path
                if not getattr(self.app, "_auto_level_original_source_path", None):
                    self.app._auto_level_original_source_path = source_path or None
                if not getattr(self.app, "_auto_level_original_hash", None):
                    self.app._auto_level_original_hash = (
                        getattr(self.app, "_auto_level_job_hash", None)
                        or getattr(self.app, "_gcode_hash", None)
                    )
                if not int(getattr(self.app, "_auto_level_original_total_lines", 0) or 0):
                    try:
                        self.app._auto_level_original_total_lines = int(
                            getattr(self.app, "_auto_level_job_total_lines", 0)
                            or line_count
                            or 0
                        )
                    except (TypeError, ValueError):
                        self.app._auto_level_original_total_lines = 0
                self.app._auto_level_leveled_lines = None
                self.app._auto_level_leveled_path = result.output_path
                self.app._auto_level_leveled_temp = is_temp
                self.app._auto_level_leveled_name = os.path.basename(
                    result.output_path or path or "Leveled Job"
                )
                self.app._auto_level_restore = {
                    "original_lines": getattr(self.app, "_auto_level_original_lines", None),
                    "original_path": getattr(self.app, "_auto_level_original_path", None),
                    "original_source_path": getattr(self.app, "_auto_level_original_source_path", None),
                    "original_hash": getattr(self.app, "_auto_level_original_hash", None),
                    "original_total_lines": getattr(self.app, "_auto_level_original_total_lines", 0),
                    "leveled_lines": None,
                    "leveled_path": result.output_path,
                    "leveled_temp": is_temp,
                    "leveled_name": self.app._auto_level_leveled_name,
                }
                if self.revert_btn is not None:
                    self.revert_btn.config(state="normal")
                if self.save_btn is not None:
                    self.save_btn.config(state="normal")
                if fallback_warning:
                    self.deps.messagebox.showwarning("Auto-Level", fallback_warning)
                self.status_var.set(
                    "Loading leveled file..."
                    if not is_temp
                    else "Loading leveled file (temporary)..."
                )
                if result.output_path:
                    self.app._load_gcode_from_path(result.output_path)

            _post_ui_callback(
                self.app,
                on_done,
                context="Failed posting auto-level apply completion callback",
            )

        threading.Thread(target=worker, daemon=True).start()

    def revert_job(self) -> None:
        orig_lines = getattr(self.app, "_auto_level_original_lines", None)
        path = getattr(self.app, "_auto_level_original_path", None) or getattr(
            self.app, "_auto_level_original_source_path", None
        )
        if not orig_lines and not path:
            return
        name = os.path.basename(path) if path else "Original Job"
        if orig_lines:
            self.app._apply_loaded_gcode(name, orig_lines, validated=False)
        else:
            self.app._load_gcode_from_path(path)
        self.status_var.set("Original job restored.")

    def save_leveled(self) -> None:
        save_leveled_job(self.app, self.status_var)

    def save_height_map(self) -> None:
        if not active_auto_level_map_is_current(self.app):
            self.deps.messagebox.showwarning(
                "Auto-Level",
                "The height map is inactive or no longer trusted for this controller session.",
            )
            return
        save_height_map_file(self.app, self.status_var)

    def load_height_map(self) -> None:
        load_height_map_file(
            self.app,
            self.status_var,
            self.stats_var,
            self.map_summary_var,
            self.apply_btn,
            self.save_map_btn,
            self.save_btn,
        )
        clear_active_auto_level_map(
            self.app,
            "Loaded historical maps are inactive in this release; run a new probe before Apply or Save Map.",
        )

    def cancel_probe(self) -> None:
        if self.app.auto_level_runner.is_running():
            self._active_workflow_token = None
            self._active_workflow_lease = None
            self.app.auto_level_runner.cancel()
            self.status_var.set(
                "Cancellation requested; stopping controller and entering recovery..."
            )
        elif self.dlg is not None:
            lease = self._active_workflow_lease
            installation_ticket = self._active_installation_ticket
            self._active_workflow_token = None
            self._active_workflow_lease = None
            self._active_installation_ticket = None
            if installation_ticket is not None:
                self.app.grbl.abort_auto_level_installation(
                    installation_ticket,
                    "Auto-Level map installation aborted because its dialog closed.",
                )
            if lease is not None:
                retire = getattr(
                    self.app.grbl, "retire_auto_level_completion", None
                )
                if callable(retire):
                    retire(
                        lease,
                        "Auto-Level result retired because its dialog closed before installation.",
                    )
            self.dlg.destroy()
            if getattr(self.app, "_auto_level_dialog_controller", None) is self:
                self.app._auto_level_dialog_controller = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        settings_widgets = (
            self.profile_combo,
            self.preset_combo,
            self.preset_save_btn,
            self.preset_delete_btn,
            self.margin_entry,
            self.base_spacing_entry,
            self.min_spacing_entry,
            self.max_spacing_entry,
            self.max_points_entry,
            self.path_order_combo,
            self.safe_z_entry,
            self.probe_depth_entry,
            self.probe_feed_entry,
            self.retract_entry,
            self.settle_entry,
            self.interp_combo,
            *self.avoidance_controls,
        )
        action_widgets = (
            self.start_btn,
            self.test_btn,
            self.apply_btn,
            self.save_btn,
            self.save_map_btn,
            self.load_map_btn,
            self.revert_btn,
        )
        widgets = settings_widgets + action_widgets
        state = "normal" if enabled else "disabled"
        for widget in widgets:
            try:
                if widget is None:
                    continue
                widget.config(state=state)
            except Exception as exc:
                _log_suppressed("Failed toggling auto-level dialog control state", exc)
        if enabled:
            self.set_start_state()
            self._sync_action_button_states()
        if self.close_btn is not None:
            self.close_btn.config(text="Cancel" if not enabled else "Close")

    def _build_ui(self) -> None:
        if self.frm is None:
            return
        self.frm.grid_columnconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self.frm)
        self.notebook.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.settings_tab = ttk.Frame(self.notebook)
        self.avoidance_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.avoidance_tab, text="Avoidance Areas")
        self.deps.set_tab_tooltip_fn(
            self.notebook,
            self.settings_tab,
            "Configure grid spacing, probe order, and probe settings.",
        )
        self.deps.set_tab_tooltip_fn(
            self.notebook,
            self.avoidance_tab,
            "Define areas to skip during probing.",
        )
        self.settings_tab.grid_columnconfigure(1, weight=1)
        self.avoidance_tab.grid_columnconfigure(0, weight=1)
        self._build_header_and_presets()
        self._build_settings_fields()
        self._build_status_and_actions()
        self._bind_sample_fields()

    def _build_header_and_presets(self) -> None:
        header_row = ttk.Frame(self.settings_tab)
        header_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(header_row, text=self.job_info_text, wraplength=460, justify="left").pack(
            fill="x",
            pady=(0, 6),
        )

        profile_row = ttk.Frame(header_row)
        profile_row.pack(fill="x", pady=(0, 6))
        ttk.Label(profile_row, text="Profile").pack(side="left")
        self.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.profile_var,
            values=self.profile_labels,
            state="readonly",
            width=12,
        )
        self.profile_combo.pack(side="left", padx=(8, 6))
        self.profile_combo.bind(
            "<<ComboboxSelected>>",
            lambda _evt: self.apply_profile_choice(self.profile_var.get().strip().lower()),
        )

        preset_row = ttk.Frame(header_row)
        preset_row.pack(fill="x")
        ttk.Label(preset_row, text="Preset").pack(side="left")
        self.preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.preset_var,
            values=sorted(list((self.app.auto_level_presets or {}).keys())),
            state="readonly",
            width=16,
        )
        self.preset_combo.pack(side="left", padx=(8, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _evt: self.apply_preset(self.preset_var.get()))
        self.preset_save_btn = ttk.Button(preset_row, text="Save", command=self.save_preset)
        self.preset_save_btn.pack(side="left", padx=(0, 6))
        self.preset_delete_btn = ttk.Button(preset_row, text="Delete", command=self.delete_preset)
        self.preset_delete_btn.pack(side="left")

        if self.preset_var.get():
            self.apply_preset(self.preset_var.get())

    def _build_settings_fields(self) -> None:
        settings_tab = self.settings_tab
        avoidance_tab = self.avoidance_tab
        if settings_tab is None or avoidance_tab is None:
            return

        ttk.Label(settings_tab, text="Adaptive grid").grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
        )
        self.margin_entry = grid_row(settings_tab, "Margin (mm)", self.margin_var, 2)
        self.base_spacing_entry = grid_row(settings_tab, "Base spacing (mm)", self.base_spacing_var, 3)
        self.min_spacing_entry = grid_row(settings_tab, "Min spacing (mm)", self.min_spacing_var, 4)
        self.max_spacing_entry = grid_row(settings_tab, "Max spacing (mm)", self.max_spacing_var, 5)
        self.max_points_entry = grid_row(
            settings_tab,
            "Max points (optional)",
            self.max_points_var,
            6,
            allow_decimal=False,
        )

        ttk.Label(settings_tab, text="Probe order").grid(
            row=7,
            column=0,
            sticky="w",
            pady=(4, 2),
        )
        self.path_order_combo = ttk.Combobox(
            settings_tab,
            textvariable=self.path_order_var,
            values=tuple(self.path_order_options.keys()),
            state="readonly",
            width=20,
        )
        self.path_order_combo.grid(row=7, column=1, sticky="w", pady=(4, 2))
        self.path_order_combo.bind("<<ComboboxSelected>>", lambda _evt: self.update_sample())

        ttk.Separator(settings_tab, orient="horizontal").grid(
            row=8,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=6,
        )
        ttk.Label(settings_tab, text="Probe settings").grid(
            row=9,
            column=0,
            columnspan=2,
            sticky="w",
        )
        self.safe_z_entry = grid_row(settings_tab, "Safe Z (mm)", self.safe_z_var, 10)
        self.probe_depth_entry = grid_row(settings_tab, "Probe depth (mm)", self.probe_depth_var, 11)
        self.probe_feed_entry = grid_row(settings_tab, "Probe feed (mm/min)", self.probe_feed_var, 12)
        self.retract_entry = grid_row(settings_tab, "Retract Z (mm)", self.retract_var, 13)
        self.settle_entry = grid_row(settings_tab, "Settle time (sec)", self.settle_var, 14)
        ttk.Label(settings_tab, text="Interpolation").grid(
            row=15,
            column=0,
            sticky="w",
            pady=(4, 2),
        )
        self.interp_combo = ttk.Combobox(
            settings_tab,
            textvariable=self.interp_var,
            values=("bilinear", "bicubic"),
            state="readonly",
            width=10,
        )
        self.interp_combo.grid(row=15, column=1, sticky="w", pady=(4, 2))
        self.interp_combo.bind("<<ComboboxSelected>>", lambda _evt: self.update_sample())
        ttk.Separator(settings_tab, orient="horizontal").grid(
            row=16,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=6,
        )
        ttk.Label(settings_tab, text="Safety limits").grid(
            row=17,
            column=0,
            columnspan=2,
            sticky="w",
        )
        self.max_abs_correction_entry = grid_row(
            settings_tab,
            "Max correction (mm)",
            self.max_abs_correction_var,
            18,
        )
        self.max_z_span_entry = grid_row(
            settings_tab,
            "Max Z span (mm)",
            self.max_z_span_var,
            19,
        )
        self.avoidance_controls = build_avoidance_tab(
            avoidance_tab,
            self.avoidance_vars,
            self.update_sample,
            self.set_avoidance_from_position,
        )

        ttk.Label(settings_tab, textvariable=self.sample_var, wraplength=460, justify="left").grid(
            row=20,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Label(settings_tab, textvariable=self.bounds_var, wraplength=460, justify="left").grid(
            row=21,
            column=0,
            columnspan=2,
            sticky="w",
        )
        ttk.Label(settings_tab, textvariable=self.map_summary_var, wraplength=460, justify="left").grid(
            row=22,
            column=0,
            columnspan=2,
            sticky="w",
        )
        ttk.Label(settings_tab, textvariable=self.stats_var, wraplength=460, justify="left").grid(
            row=23,
            column=0,
            columnspan=2,
            sticky="w",
        )

    def _build_status_and_actions(self) -> None:
        ttk.Label(self.frm, textvariable=self.status_var, wraplength=460, justify="left").grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Label(self.frm, textvariable=self.last_test_probe_var, wraplength=460, justify="left").grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(2, 0),
        )
        self.progress_bar = ttk.Progressbar(self.frm, mode="determinate", length=240)
        self.progress_bar.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        btn_row = ttk.Frame(self.frm)
        btn_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.start_btn = ttk.Button(btn_row, text="Start Probe", command=self.start_probe)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.test_btn = ttk.Button(btn_row, text="Test Probe", command=self.test_probe)
        self.test_btn.pack(side="left", padx=(0, 6))
        self.apply_btn = ttk.Button(btn_row, text="Apply to Job", command=self.apply_level, state="disabled")
        self.apply_btn.pack(side="left", padx=(0, 6))
        self.save_btn = ttk.Button(btn_row, text="Save Leveled", command=self.save_leveled, state="disabled")
        self.save_btn.pack(side="left", padx=(0, 6))
        self.save_map_btn = ttk.Button(btn_row, text="Save Map", command=self.save_height_map, state="disabled")
        self.save_map_btn.pack(side="left", padx=(0, 6))
        self.load_map_btn = ttk.Button(btn_row, text="Load Map", command=self.load_height_map)
        self.load_map_btn.pack(side="left", padx=(0, 6))
        self.revert_btn = ttk.Button(btn_row, text="Revert Job", command=self.revert_job, state="disabled")
        self.revert_btn.pack(side="left", padx=(0, 6))
        self.close_btn = ttk.Button(btn_row, text="Close", command=self.cancel_probe)
        self.close_btn.pack(side="left")

    def _bind_sample_fields(self) -> None:
        for entry in (
            self.margin_entry,
            self.base_spacing_entry,
            self.min_spacing_entry,
            self.max_spacing_entry,
            self.max_points_entry,
            self.safe_z_entry,
            self.probe_depth_entry,
            self.probe_feed_entry,
            self.retract_entry,
            self.settle_entry,
            self.max_abs_correction_entry,
            self.max_z_span_entry,
        ):
            entry.bind("<KeyRelease>", lambda _evt: self.update_sample())

    def _hydrate_existing_state(self) -> None:
        map_current = active_auto_level_map_is_current(self.app)
        if map_current and getattr(self.app, "_auto_level_height_map", None) is not None:
            try:
                if self.app._auto_level_height_map.is_complete():
                    self.apply_btn.config(state="normal")
            except Exception as exc:
                _log_suppressed("Failed enabling auto-level Apply button while hydrating dialog state", exc)
        if (isinstance(self.original_lines, list) and self.original_lines) or self.original_path:
            self.revert_btn.config(state="normal")
        if isinstance(getattr(self.app, "_auto_level_leveled_lines", None), list) or getattr(
            self.app, "_auto_level_leveled_path", None
        ):
            self.save_btn.config(state="normal")
        if map_current and getattr(self.app, "_auto_level_height_map", None) is not None:
            try:
                if self.app._auto_level_height_map.is_complete():
                    self.save_map_btn.config(state="normal")
            except Exception as exc:
                _log_suppressed("Failed enabling auto-level Save Map button while hydrating dialog state", exc)
            try:
                height_map = self.app._auto_level_height_map
                self.map_summary_var.set(
                    f"Loaded map: {len(height_map.xs)} x {len(height_map.ys)} "
                    f"({len(height_map.xs) * len(height_map.ys)} points)"
                )
                update_stats_summary(height_map, self.stats_var)
            except Exception as exc:
                _log_suppressed("Failed hydrating auto-level loaded-map summary state", exc)

    def _poll_start_state(self) -> None:
        if self.dlg is None or not self.dlg.winfo_exists():
            return
        self.set_start_state()
        self.dlg.after(AUTOLEVEL_START_STATE_POLL_MS, self._poll_start_state)


def show_auto_level_dialog(
    app: Any,
    deps: AutoLevelDialogDependencies | None = None,
) -> None:
    if deps is None:
        deps = build_auto_level_dialog_dependencies()
    AutoLevelDialogController(app, deps).show()
