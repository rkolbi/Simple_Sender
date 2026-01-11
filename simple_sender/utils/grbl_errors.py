from __future__ import annotations

import re

GRBL_ERROR_CODES: dict[int, str] = {
    1: "G-code words consist of a letter and a value. Letter was not found.",
    2: "Numeric value format is not valid or missing an expected value.",
    3: "Grbl '$' system command was not recognized or supported.",
    4: "Negative value received for an expected positive value.",
    5: "Homing cycle is not enabled via settings.",
    6: "Minimum step pulse time must be greater than 3usec.",
    7: "EEPROM read failed. Reset and restored to default values.",
    8: "Grbl '$' command cannot be used unless Grbl is idle.",
    9: "G-code locked out during alarm or jog state.",
    10: "Soft limits cannot be enabled without homing also enabled.",
    11: "Max characters per line exceeded. Line was not processed and executed.",
    12: "Grbl '$' setting value exceeds the maximum step rate supported.",
    13: "Safety door detected as opened and door state initiated.",
    14: "Build info or startup line exceeded EEPROM line length limit.",
    15: "Jog target exceeds machine travel. Command ignored.",
    16: "Jog command with no '=' or contains prohibited g-code.",
    17: "Laser mode requires PWM output.",
    20: "Unsupported or invalid g-code command found in block.",
    21: "More than one g-code command from same modal group found in block.",
    22: "Feed rate has not yet been set or is undefined.",
    23: "G-code command in block requires an integer value.",
    24: "Two G-code commands that both require the use of the XYZ axis words were detected in the block.",
    25: "A G-code word was repeated in the block.",
    26: "A G-code command requires XYZ axis words in the block, but none were detected.",
    27: "N line number value is not within the valid range of 1 - 9,999,999.",
    28: "A G-code command is missing required P or L values in the line.",
    29: "G59.1, G59.2, and G59.3 are not supported.",
    30: "G53 requires either G0 seek or G1 feed motion mode to be active.",
    31: "Unused axis words found in block with G80 motion mode active.",
    32: "G2/G3 arc lacks XYZ axis words in the selected plane.",
    33: "Motion command has an invalid target (arc/probe target invalid).",
    34: "Arc radius definition failed to compute arc geometry.",
    35: "Arc offset definition missing IJK offset word in the selected plane.",
    36: "Unused G-code words found that are not used by any command in the block.",
}

GRBL_ALARM_CODES: dict[int, str] = {
    1: "Hard limit triggered. Machine position likely lost; re-home recommended.",
    2: "Soft limit alarm. Position retained; clear alarm to continue.",
    3: "Reset while in motion. Position likely lost; re-home recommended.",
    4: "Probe fail: probe not in expected initial state.",
    5: "Probe fail: probe did not contact within programmed travel.",
    6: "Homing fail: reset during active homing cycle.",
    7: "Homing fail: safety door opened during homing cycle.",
    8: "Homing fail: failed to clear limit switch while pulling off.",
    9: "Homing fail: limit switch not found within search distance.",
    10: "EStop asserted. Clear and reset. (grblHAL)",
    11: "Homing required. Execute $H to continue. (grblHAL)",
    12: "Limit switch engaged. Clear before continuing. (grblHAL)",
    13: "Probe protection triggered. Clear before continuing. (grblHAL)",
    14: "Spindle at speed timeout. Clear before continuing. (grblHAL)",
    15: "Homing fail: second limit switch not found. (grblHAL)",
}

_ERROR_CODE_PAT = re.compile(r"error:(\d+)", re.IGNORECASE)
_ALARM_CODE_PAT = re.compile(r"ALARM:(\d+)", re.IGNORECASE)


def annotate_grbl_error(line: str) -> str:
    match = _ERROR_CODE_PAT.search(line or "")
    if not match:
        return line
    try:
        code = int(match.group(1))
    except ValueError:
        return line
    desc = GRBL_ERROR_CODES.get(code)
    if not desc:
        return line
    lower = line.lower()
    marker = f"error:{code}"
    pos = lower.find(marker)
    if pos >= 0 and "(" in line[pos:]:
        return line
    return f"{line} ({desc})"


def annotate_grbl_alarm(line: str) -> str:
    match = _ALARM_CODE_PAT.search(line or "")
    if not match:
        return line
    try:
        code = int(match.group(1))
    except ValueError:
        return line
    desc = GRBL_ALARM_CODES.get(code)
    if not desc:
        return line
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
