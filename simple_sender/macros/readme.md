# CNC Reference Macros

These macros implement the CNCjs workflow documented in `ref/cncjs_macros.md`. They automate touch-plate probing, reference-tool capture, safe park/return motions, and TOOL_REFERENCE recovery using the sender's existing macro engine.

## Setup

1. Enable **“Allow macro scripting (Python/eval)”** in the app settings so the macros can read/write `macro.state.*` variables and the `[macro.state.<name>]` expressions used in the G-code.
2. Home the machine and make sure the fixed tool-height sensor, touch plate, and clip are installed/clean.
3. Run the macros in the order described here, choosing Macro 2 in place of Macro 1 only when X/Y probing is not possible.

## Macro 1 – Touch Plate Setup & Reference Tool

- **Purpose:** Probes X, Y, and Z on the touch plate, then moves to the fixed sensor to capture and store `TOOL_REFERENCE` using `macro.state`.
- **How it works:** The macro copies CNCjs-style `global.state` parameters into `%macro.state.*`, probes the plate (fast and slow passes), mashes the sensor positions, and stores the result in `macro.state.TOOL_REFERENCE` after the reference-tool probe.
- **Usage:** Start here for squared stock where the tool can hit the plate in X/Y. After Macro 1 completes, the job’s work zero and tool-reference height are both established.

## Macro 2 – Z-Only Reference Tool Setup

- **Purpose:** Sets Z zero via touch plate while you manually set X/Y zero beforehand, then captures the reference tool height.
- **How it works:** The macro saves the current `wx/wy` values (or defaults to 0) so it can return to the start location, probes only Z, and then measures `macro.state.TOOL_REFERENCE` at the fixed sensor.
- **Usage:** Run this when material edges cannot be probed in X/Y. Manually jog to your intended XY origin (set to zero), place the plate, run Macro 2, and resume cutting from the saved XY.

## Macro 3 – Tool Change (Preserve Work Z)

- **Purpose:** Lets you swap tools without re-probing the touch plate by re-applying the stored `TOOL_REFERENCE` for the active WCS.
- **How it works:** Moves to the sensor, prompts for the tool swap, re-probes the new tool, and applies `G10 L20 Z[...]` using the previously captured reference value.
- **Usage:** Run this each time your G-code calls for a new tool. If `macro.state.TOOL_REFERENCE` is missing, the macro aborts and asks you to run Macro 1 or 2 first.

## Macro 4 – Park at Tool Sensor

- **Purpose:** Moves to the fixed sensor and holds position for inspections, cleaning, or manual tool swaps without altering offsets.
- **How it works:** Stops the spindle, moves to the `macro.state`-configured safe height, and parks at the sensor coordinates.
- **Usage:** Use during setup/phases between operations when you want to paused near the sensor.

## Macro 5 – Return to Work Zero

- **Purpose:** Raises to the safe Z height and returns to the WCS origin (`G0 X0 Y0`) without changing offsets.
- **How it works:** Executes a `G53` raise to `macro.state.SAFE_HEIGHT`, then rapid moves to `X0 Y0` while keeping tool offsets untouched.
- **Usage:** Run after Macro 3 or Macro 4 when you are ready to resume cutting from `X0 Y0`.

## Macro 6 – Reference Tool Recovery

- **Purpose:** Re-establishes `macro.state.TOOL_REFERENCE` from the currently installed reference tool without altering the work zero.
- **How it works:** Checks that `TOOL_REFERENCE` is absent, moves to the fixed sensor, probes the installed tool (assumes it is the original reference tool), stores the height, and leaves the machine parked at the sensor.
- **Usage:** Run only if `macro.state.TOOL_REFERENCE` was lost (e.g., after power cycle) and the reference tool is currently installed. Clear `macro.state.TOOL_REFERENCE` first if you want to overwrite it.

## Notes

- Each macro relies on `%macro.state` parameters (`SAFE_HEIGHT`, `PROBE_*`, `PLATE_THICKNESS`, etc.). Adjust those defaults directly inside the macro file to match your machine before using them.
- The macros log progress via `%msg` so you can see when each probe or wait occurs in the console.
