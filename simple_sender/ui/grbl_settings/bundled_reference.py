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


def _text(*lines: str) -> str:
    return "\n".join(line.strip() for line in lines if str(line).strip())


BUNDLED_GRBL_SETTING_TOOLTIPS: dict[int, str] = {
    0: _text(
        "Sets the step pulse width sent to the motor driver.",
        "Increase only if a driver misses steps at the current pulse width.",
        "Units: microseconds.",
    ),
    1: _text(
        "Delay before GRBL disables steppers after motion stops.",
        "Set 255 to keep motors enabled continuously.",
        "Units: milliseconds.",
    ),
    2: _text(
        "Bitmask that inverts step pulse polarity per axis.",
        "Change only if your driver requires opposite step-signal logic.",
    ),
    3: _text(
        "Bitmask that reverses axis direction.",
        "Use this to correct travel direction without rewiring the motor.",
    ),
    4: _text(
        "Inverts the stepper enable pin polarity.",
        "Use only when the driver enables on the opposite signal state.",
    ),
    5: _text(
        "Inverts the limit-switch input polarity.",
        "Useful only when your switch wiring or pull-up strategy reads backward.",
    ),
    6: _text(
        "Inverts the probe input polarity.",
        "Useful for normally closed probe circuits or when probe-state indication is reversed.",
    ),
    10: _text(
        "Bitmask that controls which extra fields GRBL includes in status reports.",
        "Change cautiously because senders rely on these fields for DRO, planner, and buffer data.",
    ),
    11: _text(
        "Cornering tolerance used by the planner to blend moves.",
        "Higher values keep more speed through corners but can round sharp geometry.",
        "Units: millimeters.",
    ),
    12: _text(
        "Maximum error allowed when GRBL approximates arcs internally.",
        "Lower values improve arc fidelity but can increase planner workload.",
        "Units: millimeters.",
    ),
    13: _text(
        "Changes status and report units only.",
        "It does not rescale stored settings or change how G-code units are interpreted.",
    ),
    20: _text(
        "Stops commanded motion that would exceed configured machine travel.",
        "Requires accurate $130/$131/$132 travel values and normally a homed machine.",
    ),
    21: _text(
        "Triggers an alarm when a limit switch activates during motion.",
        "Enable only after switch wiring is stable to avoid nuisance trips.",
    ),
    22: _text(
        "Enables the homing cycle and allows $H.",
        "Required for reliable soft limits and other homing-dependent safeguards.",
    ),
    23: _text(
        "Bitmask that chooses the homing direction for each axis.",
        "Match it to the physical endstop locations before enabling homing.",
    ),
    24: _text(
        "Slow feed used for the final homing locate pass.",
        "Lower values improve repeatability but lengthen homing time.",
        "Units: mm/min.",
    ),
    25: _text(
        "Fast seek used to find the homing switch initially.",
        "Set high enough for efficiency but low enough to avoid violent switch hits.",
        "Units: mm/min.",
    ),
    26: _text(
        "Debounce time GRBL uses to ignore switch bounce during homing.",
        "Increase only if noisy switches cause false retriggers.",
        "Units: milliseconds.",
    ),
    27: _text(
        "Distance GRBL backs away from the switch after homing.",
        "Must be large enough to fully release the switch before normal motion.",
        "Units: millimeters.",
    ),
    30: _text(
        "Top spindle RPM used for PWM scaling.",
        "Match this to the highest real spindle speed so S values map accurately.",
        "Units: RPM.",
    ),
    31: _text(
        "Minimum spindle RPM used for PWM scaling.",
        "Set to zero for most simple PWM spindles unless your hardware requires a nonzero minimum.",
        "Units: RPM.",
    ),
    32: _text(
        "Optimizes motion for laser use by changing spindle-power behavior during motion.",
        "Leave this off for milling or routing because it changes M3/M4 handling.",
    ),
    100: _text(
        "Motor steps required to move X by 1 mm.",
        "This is a calibration setting; tune it before trusting commanded distances.",
    ),
    101: _text(
        "Motor steps required to move Y by 1 mm.",
        "This is a calibration setting; tune it before trusting commanded distances.",
    ),
    102: _text(
        "Motor steps required to move Z by 1 mm.",
        "This is a calibration setting; tune it before trusting commanded distances.",
    ),
    110: _text(
        "Maximum feed rate GRBL allows on X.",
        "This caps rapid and planned motion; set it below the machine's reliable limit.",
        "Units: mm/min.",
    ),
    111: _text(
        "Maximum feed rate GRBL allows on Y.",
        "This caps rapid and planned motion; set it below the machine's reliable limit.",
        "Units: mm/min.",
    ),
    112: _text(
        "Maximum feed rate GRBL allows on Z.",
        "This caps rapid and planned motion; set it below the machine's reliable limit.",
        "Units: mm/min.",
    ),
    120: _text(
        "Maximum acceleration for X.",
        "Higher values shorten ramps but can cause lost steps on weaker mechanics.",
        "Units: mm/sec^2.",
    ),
    121: _text(
        "Maximum acceleration for Y.",
        "Higher values shorten ramps but can cause lost steps on weaker mechanics.",
        "Units: mm/sec^2.",
    ),
    122: _text(
        "Maximum acceleration for Z.",
        "Higher values shorten ramps but can cause lost steps on weaker mechanics.",
        "Units: mm/sec^2.",
    ),
    130: _text(
        "Usable X travel from home to the far end of the machine.",
        "Soft limits, jogging safeguards, and sender-side bounds checks depend on this being accurate.",
        "Units: millimeters.",
    ),
    131: _text(
        "Usable Y travel from home to the far end of the machine.",
        "Soft limits, jogging safeguards, and sender-side bounds checks depend on this being accurate.",
        "Units: millimeters.",
    ),
    132: _text(
        "Usable Z travel from home to the far end of the machine.",
        "Soft limits, jogging safeguards, and sender-side bounds checks depend on this being accurate.",
        "Units: millimeters.",
    ),
}
