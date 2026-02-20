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

import re

GRBL_ERROR_CODES: dict[int, str] = {
    1: "Expected command letter.",
    2: "Bad number format.",
    3: "Invalid statement (unrecognized/unsupported '$' command).",
    4: "Value < 0.",
    5: "Setting disabled (homing not enabled).",
    6: "Value < 3 usec (step pulse too short).",
    7: "EEPROM read fail. Using defaults.",
    8: "Not idle (cannot run that '$' command unless IDLE).",
    9: "G-code lock (locked out during alarm/jog).",
    10: "Homing not enabled (soft limits require homing).",
    11: "Line overflow (too many characters; line not executed).",
    12: "Step rate > 30kHz (settings exceed max step rate).",
    13: "Check Door (safety door opened / door state).",
    14: "Line length exceeded (startup/build info too long for EEPROM storage).",
    15: "Travel exceeded (jog target exceeds travel; ignored).",
    16: "Invalid jog command (missing '=' or contains prohibited g-code).",
    17: "Setting disabled (laser mode requires PWM output).",
    20: "Unsupported command (invalid/unsupported g-code).",
    21: "Modal group violation.",
    22: "Undefined feed rate.",
    23: "Requires integer value.",
    24: ">1 axis-word-requiring command in block.",
    25: "Repeated g-code word in block.",
    26: "No axis words found when required.",
    27: "Invalid line number.",
    28: "Missing required value word.",
    29: "G59.x WCS not supported.",
    30: "G53 only allowed with G0/G1.",
    31: "Axis words present but unused by command/modal state.",
    32: "G2/G3 require at least one in-plane axis word.",
    33: "Motion target invalid.",
    34: "Arc radius invalid.",
    35: "G2/G3 require at least one in-plane offset word.",
    36: "Unused value words found in block.",
    37: "G43.1 TLO not assigned to configured tool length axis.",
    38: "Tool number > max supported.",
}

GRBL_ALARM_CODES: dict[int, str] = {
    1: "Hard limit: hard limit triggered; position likely lost; re-home recommended.",
    2: "Soft limit: target exceeds travel; position retained; may unlock safely.",
    3: "Abort during cycle: reset while in motion; position likely lost; re-home recommended.",
    4: "Probe fail: probe not in expected initial state for the probing mode used.",
    5: "Probe fail: probe did not contact within programmed travel.",
    6: "Homing fail: active homing cycle was reset.",
    7: "Homing fail: safety door opened during homing.",
    8: "Homing fail: pull-off travel failed to clear the switch.",
    9: "Homing fail: could not find switch within search distance.",
    10: "Homing fail: dual-axis second switch did not trigger after the first within the allowed distance.",
}

_ERROR_CODE_PAT = re.compile(r"error:(\d+)", re.IGNORECASE)
_ALARM_CODE_PAT = re.compile(r"ALARM:(\d+)", re.IGNORECASE)


def get_grbl_error_description(code: int) -> str | None:
    return GRBL_ERROR_CODES.get(int(code))


def get_grbl_alarm_description(code: int) -> str | None:
    return GRBL_ALARM_CODES.get(int(code))


def extract_grbl_code(line: str) -> tuple[str, int, str] | None:
    """Return (kind, code, description) for known GRBL error/alarm codes."""
    text = line or ""
    err_match = _ERROR_CODE_PAT.search(text)
    if err_match:
        try:
            err_code = int(err_match.group(1))
        except ValueError:
            return None
        err_desc = get_grbl_error_description(err_code)
        if err_desc:
            return ("error", err_code, err_desc)
    alarm_match = _ALARM_CODE_PAT.search(text)
    if alarm_match:
        try:
            alarm_code = int(alarm_match.group(1))
        except ValueError:
            return None
        alarm_desc = get_grbl_alarm_description(alarm_code)
        if alarm_desc:
            return ("alarm", alarm_code, alarm_desc)
    return None


def annotate_grbl_error(line: str) -> str:
    info = extract_grbl_code(line)
    if not info or info[0] != "error":
        return line
    code = info[1]
    desc = info[2]
    lower = line.lower()
    marker = f"error:{code}"
    pos = lower.find(marker)
    if pos >= 0 and "(" in line[pos:]:
        return line
    return f"{line} ({desc})"


def annotate_grbl_alarm(line: str) -> str:
    info = extract_grbl_code(line)
    if not info or info[0] != "alarm":
        return line
    code = info[1]
    desc = info[2]
    lower = line.lower()
    marker = f"alarm:{code}"
    pos = lower.find(marker)
    if pos >= 0 and "(" in line[pos:]:
        return line
    return f"{line} ({desc})"


def annotate_grbl_message(line: str) -> str:
    if not line:
        return line
    annotated = annotate_grbl_error(line)
    if annotated != line:
        return annotated
    return annotate_grbl_alarm(line)
