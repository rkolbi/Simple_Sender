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
- `BKUP_Macro-3/4/5/7` are backup/reference files and are not loaded as active macro buttons.

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
- Checklist files (`checklist-*.chk`) in this folder feed the Checklists tab and release/start-job checklist dialogs.
