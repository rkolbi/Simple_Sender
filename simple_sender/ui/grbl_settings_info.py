import csv
import os
import re

from simple_sender.utils.constants import GRBL_SETTING_DESC, GRBL_SETTING_KEYS


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

    load_grbl_setting_tooltips(info, base_dir)

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

    app._grbl_setting_info = info
    app._grbl_setting_keys = sorted(set(keys))


def load_grbl_setting_tooltips(info: dict, base_dir: str):
    md_path = os.path.join(
        base_dir,
        "ref",
        "grbl-master",
        "grbl-master",
        "doc",
        "markdown",
        "settings.md",
    )
    if not os.path.isfile(md_path):
        return
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            md = f.read()
    except Exception:
        return
    pattern = re.compile(r"^#### \$(\d+)[^\n]*\n(.*?)(?=^#### \$|\Z)", re.M | re.S)
    for match in pattern.finditer(md):
        idx = int(match.group(1))
        body = match.group(2).strip()
        if not body:
            continue
        lines = []
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
