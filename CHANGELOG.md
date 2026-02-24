# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Changed
- Legacy cleanup pass removed deprecated compatibility surfaces and duplicate module files:
  - removed `simple_sender/ui/widgets.py` compatibility shim
  - removed legacy single-file `simple_sender/ui/grbl_settings.py` in favor of `simple_sender/ui/grbl_settings/`
  - removed duplicate flat toolpath modules in favor of `simple_sender/ui/toolpath/`
  - removed duplicate flat `autolevel_dialog`, `console`, and `dialogs` modules in favor of package implementations
  - removed remaining unused root UI duplicates and dead entrypoints:
    - `simple_sender/ui/gcode_viewer.py`, `simple_sender/ui/preview_policy.py`, `simple_sender/ui/popup_utils.py`
    - `simple_sender/ui/alarm_recovery_dialog.py`, `simple_sender/ui/macro_prompt_dialog.py`
    - `simple_sender/ui/autolevel_prefs.py`, `simple_sender/ui/gcode_tab.py`, `simple_sender/ui/gcode_view.py`
- Removed backup macro artifacts from runtime macro directory:
  - deleted `simple_sender/macros/BKUP_Macro-3/4/5/7`
- Local test harness hardening:
  - `run_tests.bat` now auto-selects `.venv\Scripts\python.exe` when available
  - Ruff gate now prefers `.venv\Scripts\ruff.exe` to avoid broken global launcher setups
- Typing-manifest gate was resynced after cleanup:
  - mypy explicit target count is now `150`
  - local/CI hooks now enforce `--expected-count 150`

## [1.7.0] - 2026-02-23

### Added
- Spoilboard Generator in the Overdrive tab:
  - creates surfacing G-code in-memory from width/height/tool/stepover/feed/RPM/start XY inputs plus `Surfacing Depth (mm)` (default `0.50`)
  - uses a relative-Z safe workflow (`+10 mm` lift at start, spindle start, `G4 P5` dwell, absolute move to start XY, plunge to `Z = -SurfacingDepth`, `+10 mm` lift at end)
  - shows a post-generate modal with **Read G-code**, **Save G-code**, and **Cancel**
  - **Save G-code** defaults to the app log directory with `surfacing-YYYYMMDD-HHMMSS.nc`
  - includes updated README operation docs and safety checklist for running surfacing with `Z0` set to spoilboard top
- Regression coverage for deferred stream completion:
  - added `tests/ui/test_status_deferred_completion.py` to verify completion finalizes only after `Idle`
  - expanded `tests/ui/test_event_router.py` assertions for the deferred-completion lock path
- Ruff syntax/pyflakes quality gate was added across local/CI workflows:
  - `requirements-dev.txt` now includes pinned `ruff`
  - `.github/workflows/tests.yml` now runs `python -m ruff check --select E9,F63,F7,F82 simple_sender tests tools`
  - `.pre-commit-config.yaml` now includes an equivalent `ruff` hook
  - `run_tests.bat` now executes the same `ruff` gate before mypy/pytest
- Widget-module test coverage now includes direct-module edge cases:
  - added keypad focus-hover delegation assertions in `tests/ui/test_widgets_numeric_keypad.py`
  - added tooltip instance-reuse assertions in `tests/ui/test_widgets_tooltips.py`

### Changed
- Jog panel control layout was reorganized:
  - removed the dedicated MPos `Hold` and `Resume` buttons from the left control column
  - moved `Home` into the macro button row (as the first button) so it matches macro-button formatting
  - moved the macro row to span from the far-left side of the jog area
  - kept the MPos unit toggle (`mm/inch`) in the original top-left control slot
- Theme switching now reapplies custom button metrics after `ttk` theme changes so macro/MPos button heights remain consistent across themes.
- README UI tour notes now match the current control locations (top bar, left panels, and unit toggle placement).
- Streaming completion behavior is now machine-state-safe:
  - after final line ACK, UI enters a deferred completion phase while GRBL is still moving
  - progress remains at `99%` until status reports `Idle`, then advances to `100%` and triggers completion notification
  - run/manual/settings/toolpath locks stay engaged during the deferred phase and release only after `Idle`
- README was updated for consistency with current behavior:
  - Python requirement text now matches the `3.11+` project baseline
  - Viewer "Current line highlight" docs now include `Machine (status/planner)`
  - completion notes now clarify that completion waits for `Idle`
- Type-checking compatibility for jog step controls was tightened:
  - `simple_sender/ui/controls/jog_panel.py` now safely coerces step values without mypy arg-type violations
- Module/documentation alignment updates:
  - README module layout now documents the `ui/widgets_buttons.py` extraction and `ui/widgets.py` compatibility re-exports
  - README testing and typing verification notes were refreshed to the latest validated baseline date
- Mypy target-manifest gate was resynced to the current manifest size:
  - local/CI hooks now enforce `--expected-count 151`
  - README pre-release note now reflects `151` configured mypy targets (verified `2026-02-23`)
- `simple_sender/ui/widgets.py` now carries a deprecation timeline note:
  - deprecated as of `2026-02-23`
  - planned removal target `v1.8.0` (no earlier than `2026-06-01`)
- Preflight gating now treats incremental-only (`G91`) modal hazard reports as non-blocking:
  - preserves intentional spoilboard generator relative Z-lift behavior
  - avoids blocking run start when validation issues are only standalone `G91` modal notes

### Baseline Validation (local, 2026-02-23)
- `.venv\Scripts\python.exe tools/check_mypy_targets.py --expected-count 151`: PASS
- `.venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 simple_sender tests tools`: PASS
- `.venv\Scripts\python.exe -m mypy --config-file mypy.ini`: PASS (`151` source files)
- `.venv\Scripts\python.exe -m pytest -q`: PASS
- `.venv\Scripts\python.exe -m pytest --cov=simple_sender --cov-report=xml -q`: PASS
- `.venv\Scripts\python.exe tools/check_core_coverage.py coverage.xml`: PASS (aggregate critical coverage `91.5%`)

## [1.6.0] - 2026-02-21

### Added
- `tools/check_mypy_targets.py` to validate the `mypy.ini` target manifest:
  - verifies target count
  - verifies no duplicate entries
  - verifies all listed files exist
  - verifies README count note is in sync
- `tests/unit/test_application_mixin_contract.py` to enforce the curated `App` mixin stub policy.
- `tests/unit/test_mypy_target_manifest.py` to cover manifest checker success/failure cases.
- `.pre-commit-config.yaml` hooks for:
  - manifest enforcement (`check_mypy_targets.py --expected-count 148`)
  - mypy (`python -m mypy --config-file mypy.ini`)
  - basic YAML/whitespace hygiene

### Changed
- `run_tests.bat` now:
  - enforces the mypy manifest with fixed expected count (`148`)
  - runs mypy against configured targets (`mypy.ini`) to match CI behavior
- `.github/workflows/tests.yml` now:
  - enforces fixed mypy target count (`--expected-count 148`)
  - runs `tools/check_core_coverage.py coverage.xml` so CI matches local critical-path coverage gating
- `mypy.ini` now tightens missing-import handling for selected low-risk internal modules:
  - `simple_sender.types`
  - `simple_sender.streaming_controller`
  - `simple_sender.gcode_source`
  - `simple_sender.gcode_parser`
  - `simple_sender.gcode_parser_core`
  - `simple_sender.gcode_parser_split`
- `simple_sender/application.py` now documents and centralizes the curated `TYPE_CHECKING` stub contract with `_APP_TYPE_CHECKING_STUBS`.

### Baseline Validation (local, 2026-02-21)
- `python tools/check_mypy_targets.py --expected-count 148`: PASS
- `python -m mypy --config-file mypy.ini`: PASS (`148` source files)
- `python -m pytest -q`: PASS (`567` passed, `2` skipped)
- `run_tests.bat`: PASS end-to-end (`mypy`, full `pytest`+coverage, and critical-path coverage gate)
