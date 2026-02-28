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
- `Macro-3` - **Job Setup**: guided setup chooser that asks for `XYZ Plate`, `Z Plate`, or `Manual`, then runs the matching setup flow and captures `macro.state.TOOL_REFERENCE`.
- `Macro-4` - **Tool Change**: requires existing `macro.state.TOOL_REFERENCE`, re-probes after swap, then reapplies `G10 L20 Z[...]`.
- Backup/reference macro files are intentionally kept outside this folder to avoid accidental runtime loading.

## Recommended Flow

1. Use `Macro-3` (**Job Setup**) and choose `XYZ Plate`, `Z Plate`, or `Manual`.
2. Use `Macro-4` (**Tool Change**) for subsequent tool swaps.
3. Use `Macro-1`/`Macro-2` for safe parking moves during setup and maintenance.

## Notes

- Macro file header format:
  - line 1 = button label
  - line 2 = tooltip
  - line 3 = button color (`#RRGGBB`, `#RGB`, named color, or `color: ...`/`color=...`) (leave blank if unused)
  - line 4 = button text color (`#RRGGBB`, `#RGB`, named color, or `text_color: ...`/`foreground: ...`/`fg: ...`) (leave blank if unused)
  - remaining lines = executed macro body
- The macro runner snapshots modal state, forces `G21` during the run, and restores units/state via `STATE_RETURN`.
- `%msg` lines log progress in the console.
- Checklist files (`checklist-*.chk`) in this folder feed the Checklists tab (with collapsible checklist titles) and release/start-job checklist dialogs.

## Core Directives

- `%wait`: pause macro execution until GRBL reports `Idle`.
- `%update`: request a fresh status update before using `wx/wy/wz` or modal values.
- `%msg <text>`: write status text to the console with a `[macro]` prefix.
- `%if running`, `%if paused`, `%if not running`: gate a line by stream state.
- `STATE_RETURN` (or `%state_return`): restore the modal snapshot captured at macro start.

## Shared State

- Macros can share values via `macro.state.*`, for example:
  - `%macro.state.STOCK_TOP = wz`
  - `G0 Z[macro.state.STOCK_TOP]`
- The app also keeps runtime values in `_macro_vars` (`wx/wy/wz`, overrides, planner/rx bytes, `PRB`, etc.).

## Reliability Checklist

1. Add `%update` immediately before reading live coordinates.
2. Make modal intent explicit (`G90`/`G91`, feed mode, units) near motion lines.
3. Use `%wait` after long moves or probe cycles.
4. End with `STATE_RETURN` if the macro changes modal state.
5. Set line/total macro timeouts in `App Settings > Macros` for unattended routines.

## Quick Troubleshooting

- Button missing: ensure file name is `Macro-1`..`Macro-8` (or legacy `Maccro-*`) in a discovered macros directory.
- Macro blocked: streaming/alarm/disconnected states prevent execution by design.
- Stale coordinates: insert `%update` before using `wx/wy/wz`.
- Appears complete too early: keep Current Line mode on `Machine` and watch for final `Idle`.
- Unexpected units/modal state: add `STATE_RETURN` or explicit restore lines.
