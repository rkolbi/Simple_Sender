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

import csv
import os
import re

from simple_sender.ui.grbl_settings.bundled_reference import (
    BUNDLED_GRBL_SETTING_TOOLTIPS,
)
from simple_sender.utils.constants import GRBL_SETTING_DESC, GRBL_SETTING_KEYS

GRBL_SETTINGS_FALLBACK_DETAIL_NOTE = (
    "Detailed reference text is unavailable for this setting in this build; "
    "showing the compact built-in description."
)


def load_grbl_setting_info(app, base_dir: str):
    info = {}
    keys = []
    csv_path = os.path.join(
        base_dir,
        "ref",
        "grbl-master",
        "grbl-master",
        "doc",
        "csv",
        "setting_codes_en_US.csv",
    )
    if os.path.isfile(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get("$-Code", "").strip().strip('"')
                    if not code:
                        continue
                    try:
                        idx = int(code)
                    except Exception:
                        continue
                    key = f"${idx}"
                    name = (row.get(" Setting", "") or row.get("Setting", "")).strip().strip('"')
                    units = (row.get(" Units", "") or row.get("Units", "")).strip().strip('"')
                    desc = (
                        (row.get(" Setting Description", "") or row.get("Setting Description", ""))
                        .strip()
                        .strip('"')
                    )
                    info[key] = {
                        "name": name,
                        "units": units,
                        "desc": desc,
                        "tooltip": "",
                        "idx": idx,
                    }
                    keys.append(idx)
        except Exception:
            info = {}
            keys = []

    if not info:
        for idx in GRBL_SETTING_KEYS:
            key = f"${idx}"
            info[key] = {
                "name": GRBL_SETTING_DESC.get(idx, ""),
                "units": "",
                "desc": GRBL_SETTING_DESC.get(idx, ""),
                "tooltip": "",
                "idx": idx,
            }
        keys = GRBL_SETTING_KEYS[:]

    pocket_overrides = {
        0: ("Step Pulse Length", "Length of the step pulse delivered to drivers."),
        1: ("Step Idle Delay", "Time before steppers disable after motion (255 keeps enabled)."),
        2: ("Step Pulse Invert", "Invert step pulse signal. See axis config table."),
        3: ("Direction Invert", "Invert axis directions. See axis config table."),
        4: ("Step Enable Invert", "Invert the enable pin signal for drivers."),
        5: ("Limit Pins Invert", "Invert limit switch pins (requires pull-down)."),
        6: ("Probe Pin Invert", "Invert probe input (requires pull-down)."),
        10: ("Status Report Mask", "Select status report fields via bitmask."),
        11: ("Junction Deviation", "Cornering speed control; higher is faster, more risk."),
        12: ("Arc Tolerance", "Arc smoothing tolerance; lower is smoother."),
        13: ("Report Inches", "Status report units (0=mm, 1=inch)."),
        20: ("Soft Limits", "Enable soft limits (requires homing)."),
        21: ("Hard Limits", "Enable limit switch alarms."),
        22: ("Homing Cycle", "Enable the homing cycle."),
        23: ("Homing Dir Invert", "Homing direction mask. See axis config table."),
        24: ("Homing Feed", "Feed rate used for final homing locate."),
        25: ("Homing Seek", "Seek rate used to find the limit switch."),
        26: ("Homing Debounce", "Debounce delay for limit switches."),
        27: ("Homing Pull-off", "Pull-off distance after homing."),
        100: ("X Steps/mm", "Steps per mm for X axis."),
        101: ("Y Steps/mm", "Steps per mm for Y axis."),
        102: ("Z Steps/mm", "Steps per mm for Z axis."),
        110: ("X Max Rate", "Maximum rate for X axis."),
        111: ("Y Max Rate", "Maximum rate for Y axis."),
        112: ("Z Max Rate", "Maximum rate for Z axis."),
        120: ("X Max Accel", "Maximum acceleration for X axis."),
        121: ("Y Max Accel", "Maximum acceleration for Y axis."),
        122: ("Z Max Accel", "Maximum acceleration for Z axis."),
        130: ("X Max Travel", "Maximum travel for X axis."),
        131: ("Y Max Travel", "Maximum travel for Y axis."),
        132: ("Z Max Travel", "Maximum travel for Z axis."),
    }
    for idx, (name, desc) in pocket_overrides.items():
        key = f"${idx}"
        if key not in info:
            info[key] = {
                "name": name,
                "units": "",
                "desc": desc,
                "tooltip": "",
                "idx": idx,
            }
        else:
            info[key]["name"] = name
            info[key]["desc"] = desc
        keys.append(idx)

    detail_reference_available = load_grbl_setting_tooltips(info, base_dir)

    app._grbl_setting_info = info
    app._grbl_setting_keys = sorted(set(keys))
    app._grbl_setting_detail_reference_available = bool(detail_reference_available)
    app._grbl_setting_detail_fallback_note = GRBL_SETTINGS_FALLBACK_DETAIL_NOTE


def load_grbl_setting_tooltips(info: dict, base_dir: str) -> bool:
    loaded_any = False
    md_path = os.path.join(
        base_dir,
        "ref",
        "grbl-master",
        "grbl-master",
        "doc",
        "markdown",
        "settings.md",
    )
    if os.path.isfile(md_path):
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                md = f.read()
        except Exception:
            md = ""
        if md:
            pattern = re.compile(r"^#### \$(\d+)[^\n]*\n(.*?)(?=^#### \$|\Z)", re.M | re.S)
            for match in pattern.finditer(md):
                idx = int(match.group(1))
                body = match.group(2).strip()
                if not body:
                    continue
                lines: list[str] = []
                for raw in body.splitlines():
                    s = raw.strip()
                    if not s:
                        if lines and lines[-1] != "":
                            lines.append("")
                        continue
                    if s.startswith("|"):
                        continue
                    if s.startswith(":"):
                        continue
                    s = s.replace("`", "")
                    lines.append(s)
                tooltip = "\n".join([ln for ln in lines if ln != ""]).strip()
                key = f"${idx}"
                if key in info and tooltip:
                    info[key]["tooltip"] = tooltip
                    loaded_any = True
    for idx, tooltip in BUNDLED_GRBL_SETTING_TOOLTIPS.items():
        key = f"${idx}"
        if key not in info:
            continue
        if str(info[key].get("tooltip", "") or "").strip():
            continue
        info[key]["tooltip"] = str(tooltip or "").strip()
        if info[key]["tooltip"]:
            loaded_any = True
    return loaded_any
