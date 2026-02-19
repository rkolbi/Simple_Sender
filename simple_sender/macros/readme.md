# CNC Reference Macros

This folder contains the default sample macros shipped with Simple Sender.

The app loads `Macro-1` through `Macro-8` (also supports legacy `Maccro-*` names and optional `.txt` extensions) from:
- `simple_sender/macros/`
- `macros/` beside `main.py`
- the directory that contains `main.py`

## Setup

1. Enable **Allow macro scripting (Python/eval)** in App Settings > Macros.
2. Home the machine and verify your touch plate, clip, and fixed sensor are installed and clean.
3. Edit machine-specific values inside the macro files (`SAFE_HEIGHT`, `PROBE_*`, `PLATE_THICKNESS`, feedrates, etc.) before use.

## Shipped Macros

- `Macro-1` - **Park over WPos X/Y**: lifts to safe machine Z and returns to WCS X0/Y0.
- `Macro-2` - **Park over Bit Setter**: parks over fixed sensor coordinates in machine coordinates.
- `Macro-3` - **XYZ Touch Plate**: probes touch-plate Z/X/Y, then captures `macro.state.TOOL_REFERENCE` at the fixed sensor.
- `Macro-4` - **Z Touch Plate**: probes touch-plate Z only, then captures `macro.state.TOOL_REFERENCE` at the fixed sensor.
- `Macro-5` - **No Touch Plate**: for manually-set XYZ zero workflows; captures `macro.state.TOOL_REFERENCE` at the fixed sensor without touch-plate probing.
- `Macro-6` - **Tool Change**: requires existing `macro.state.TOOL_REFERENCE`, re-probes after swap, then reapplies `G10 L20 Z[...]`.
- `Macro-7` - **Prompt Test Macro**: exercises default/custom prompt dialogs and prompt-choice variables.
- `Macro-8` - **Prompt Test Macro**: duplicate prompt-test sample slot.

## Recommended Flow

1. Use `Macro-3` (or `Macro-4` if you need touch-plate probing) to establish/update `TOOL_REFERENCE`.
2. If XYZ zero is set manually (no touch plate), run `Macro-5` to establish/update `TOOL_REFERENCE`.
3. Use `Macro-6` for subsequent tool changes.
4. Use `Macro-1`/`Macro-2` for safe parking moves during setup and maintenance.

## Notes

- The macro runner snapshots modal state, forces `G21` during the run, and restores units/state via `STATE_RETURN`.
- `%msg` lines log progress in the console.
- Checklist files (`checklist-*.chk`) in this folder feed the Checklists tab and release/run checklist dialogs.
