# CNC Reference Macros

These macros implement the CNCjs workflow documented in `ref/cncjs_macros.md`. They automate touch-plate probing, reference-tool capture, safe park/return motions, and TOOL_REFERENCE recovery using the sender's existing macro engine.

## Setup

1. Enable **"Allow macro scripting (Python/eval)"** in the app settings so the macros can read/write `macro.state.*` variables and the `[macro.state.<name>]` expressions used in the G-code.
2. Home the machine and make sure the fixed tool-height sensor, touch plate, and clip are installed/clean.
3. Run the macros in the order described here, choosing Macro 2 in place of Macro 1 only when X/Y probing is not possible.

## Macro 1 - Return to Work X & Y Zero (Safe Height)

- **Purpose:** Raises to a safe Z height, then returns to WCS X0 Y0 without changing offsets.
- **How it works:** Stops the spindle, moves to `macro.state.SAFE_HEIGHT` in `G53`, then rapids to `X0 Y0` in the active WCS.
- **Usage:** Use when you need a safe return to the origin between operations. Avoid while an active job is running.

## Macro 2 - Park Spindle over Tool Sensor

- **Purpose:** Moves to the fixed sensor location for inspections, cleaning, or staging.
- **How it works:** Stops the spindle, raises to `macro.state.SAFE_HEIGHT`, then parks at the configured sensor coordinates in `G53`.
- **Usage:** Use during setup or maintenance when you need clear access to the tool or sensor.

## Macro 4 - XYZ Touch Plate & Reference Tool Setup

- **Purpose:** Probes X/Y/Z on the touch plate and captures the reference tool height at the fixed sensor.
- **How it works:** Uses fast/slow probes for Z, then X and Y touch-plate probing, and finally measures the reference tool at the sensor.
- **Usage:** Run during initial setup or any time your touch plate or sensor offsets change. If `TOOL_REFERENCE` is already set, you'll be prompted to confirm overwriting it.

## Macro 5 - Tool Change (Preserve Reference Tool Height)

- **Purpose:** Swaps tools and restores the reference tool height without re-probing the touch plate.
- **How it works:** Moves to the sensor, prompts for a tool swap, re-probes, and applies `G10 L20 Z[macro.state.TOOL_REFERENCE]`.
- **Usage:** Run for every tool change once `TOOL_REFERENCE` is captured by Macro 4 or Macro 6.

## Macro 6 - Z Touch Plate & Reference Tool Setup

- **Purpose:** Probes Z on the touch plate and captures the reference tool height; identical to Macro 4 except X/Y probing is skipped.
- **How it works:** Runs the same Z fast/slow probe sequence, then measures the reference tool at the fixed sensor.
- **Usage:** Use when X/Y probing is not possible but you still want to capture Z and `TOOL_REFERENCE`. If `TOOL_REFERENCE` is already set, you'll be prompted to confirm overwriting it.

## Notes

- Each macro relies on `%macro.state` parameters (`SAFE_HEIGHT`, `PROBE_*`, `PLATE_THICKNESS`, etc.). Adjust those defaults directly inside the macro file to match your machine before using them.
- The macro runner snapshots modal state before each macro, forces `G21` (mm), and restores the original units afterward; use `STATE_RETURN` (or `%state_return`) in a macro to restore the full modal snapshot.
- The macros log progress via `%msg` so you can see when each probe or wait occurs in the console.

## Checklists

- Checklist files live in this folder and use the `checklist-*.chk` naming format.
- Each non-empty line becomes a checklist item with a checkbox in the Checklists tab.



