# User Macros

This folder is for file-backed **user macros** only.

Protected built-in workflows such as **Park at Work**, **Park at Bit Setter**, **Job Setup**, and **Tool Change** are now implemented in application code. They are not stored here, are not editable, and are not discovered/imported/exported as user macro assets.

Editable user macro slots remain:
- `User Macro 1` through `User Macro 5`
- stored as `Macro-1` through `Macro-5` (optional `.txt` extensions supported)
- discovered from:
  - `simple_sender/macros/`
  - `macros/` beside `main.py`
  - the directory that contains `main.py`

## Setup

1. Enable **Allow macro scripting (Python/eval)** in `App Settings > Macros` only if your user macros need scripting.
2. Use `App Settings > Probing & Setup` for XYZ Plate and Bit Setter machine/workflow configuration. Those values are no longer authored in macro files.

## Supported Macro File Format

The current app supports only this header layout:
- line 1: button label
- line 2: tooltip
- line 3: button color or blank
- line 4: button text color or blank
- line 5+: macro body

Rules:
- the file must include at least one non-blank body line
- older/alternate header layouts are rejected instead of guessed or auto-migrated
- malformed files are marked invalid in the UI and blocked from running until repaired

## Notes

- The macro runner snapshots modal state, forces `G21` during the run, and restores units/state via `STATE_RETURN`.
- `%msg` lines log progress in the console.
- `Disable Macro Timeouts` in `App Settings > Macros` disables the normal prompt, line, and total timeout enforcement used for general user-macro runs.
- General user macros default to finite timeout guards (`120 s` per line, `900 s` total).
- Checklist files (`checklist-*.chk`) in this folder feed the Checklists popup content and release/start-job checklist dialogs.

## Core Directives

- `%wait`: pause macro execution until GRBL reports `Idle`
- `%update`: request a fresh status update before using `wx/wy/wz` or modal values
- `%msg <text>`: write status text to the console with a `[macro]` prefix
- `%if running`, `%if paused`, `%if not running`: gate a line by stream state
- `STATE_RETURN` (or `%state_return`): restore the modal snapshot captured at macro start

## Shared State

- Macros can share values via `macro.state.*`, for example:
  - `%macro.state.STOCK_TOP = wz`
  - `G0 Z[macro.state.STOCK_TOP]`
- The app also keeps runtime values in `_macro_vars` (`wx/wy/wz`, overrides, planner/rx bytes, `PRB`, etc.)

## Reliability Checklist

1. Add `%update` immediately before reading live coordinates.
2. Make modal intent explicit (`G90`/`G91`, feed mode, units) near motion lines.
3. Use `%wait` after long moves or probe cycles.
4. End with `STATE_RETURN` if the macro changes modal state.

## Quick Troubleshooting

- User macro button missing from the main panel: ensure the assigned file is named `Macro-1`..`Macro-5` in a discovered macros directory.
- Macro blocked: streaming/alarm/disconnected states prevent execution by design.
- Macro marked `[invalid]`: the file does not match the supported 4-line header plus body format; repair the file structure and try again.
- Run warns `Job Setup Not Completed`: run the built-in `Job Setup` workflow to repopulate the current-format tool reference for the current session, then retry.
- Streamed `TC:` did not trigger tool-change flow: ensure the line starts with `TC:` and the current Job Setup reference state is still valid.
- Stale coordinates: insert `%update` before using `wx/wy/wz`.
- Unexpected units/modal state: add `STATE_RETURN` or explicit restore lines.
