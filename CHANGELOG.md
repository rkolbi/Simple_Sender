# Changelog

All notable changes to this project are documented in this file.
Historical entries may reference pre-lean features (for example legacy pathview/Spatial work) that are no longer active in the current runtime.

## [Unreleased]

### Changed
- Lean runtime cleanup pass:
  - removed obsolete `streaming_validation_prompt` UI event path and related type/constants/test hooks
  - simplified fast-load worker API by dropping stale `_stream_from_disk` validation/sample threshold parameters that were no-ops
  - retained manual deep validation exclusively through **Overdrive -> Validate Loaded Job**
- Documentation refresh for lean sender runtime:
  - README now documents the bounded Live G-code window (`500 past/current/500 next`) instead of legacy sent/acked highlight wording
  - run-path validation text now points to manual Overdrive validation (quick/strict) and confirms Start/Run stays non-blocking
  - diagnostics/settings wording updated to reflect Overdrive strict-default toggle and current fast-load thresholds
- Revision 2.0.0 loader architecture kickoff:
  - all file-based G-code loads now normalize through one disk-backed path (`FileGcodeSource` + temp-file offsets)
  - sample-only behavior is now controlled by an explicit flag, decoupled from "source is file-backed", so non-sample jobs keep full stats/pathview features
  - load events now carry `sample_only` state to preserve existing UI gating semantics while enabling unified job source handling
  - `FileGcodeSource` now supports an `already_clean` fast-path for normalized temp sources, removing redundant per-line cleaning overhead in streaming reads
  - non-sample file-backed jobs now explicitly re-prime GRBL worker in-memory send caches from the in-memory line list, preserving high-throughput stream behavior after the unified loader shift
  - reconnect path now re-primes send caches for non-sample file-backed jobs when a source is restored
- `run_tests.bat` now mirrors CI release gates by adding import stability (`import simple_sender.ui.settings`) and compileall syntax checks before tests.
- WPos `Goto Zero` now executes a two-step absolute move sequence:
  - sends `G90 G0 X0 Y0`
  - then sends `G0 Z0` only after XY command dispatch, reducing clamp-strike risk on combined moves
- Checklists tab checklist titles are now collapsible/expandable so operators can collapse completed lists and focus on the current checklist.
- Linux system file dialogs now force a usable minimum size and apply temporary Tk scaling while open so WM/Tk pickers do not collapse to unusable dimensions.
- Kasa controller request handling now uses bounded async timeouts (default `15s`) so stalled device calls fail fast instead of blocking the accessory worker queue indefinitely.
- App Settings now includes top-level Search filtering and a `Basic`/`Advanced` view selector for faster settings navigation on touch and desktop workflows.
- Touch command feedback now acknowledges button/checkbutton taps by pulsing the control and writing `Touch received: <control>` in the status bar.
- Disabled-control tooltip reasons now include clearer state context (connecting/disconnecting, handshake/status wait, stream running/paused, and deferred idle completion) for affected toolbar actions.
- Logs viewer now includes `Clear Logs`, which truncates active `.log` files and removes rotated log files on a background worker.
- Spatial tab visibility now follows render enablement state: when Spatial render is disabled, the `Spatial View` tab is hidden.
- App Settings now includes a session-only `force Spatial tab + render` override (not persisted; resets on restart).
- Sample-only pathview policy now honors the session-only Spatial override so operators can force Spatial sample for the current run.
- Added early bounded `SSMETA` header parsing in the quick-assessment load path:
  - scans only the header window (first lines/bytes) for `SSMETA key=value` metadata
  - records `ssmeta_present` + parsed metadata map in runtime metrics/diagnostics
  - uses metadata extents/units as preferred source when complete, setting dimensions confidence to confident
  - supports scoped prefix forms like `material_size_in x=... y=... z=...` and `extents_in xmin=...`
- Added a new scrollable **File Info** tab:
  - read-only `SSMETA` header fields plus file/scan metrics
  - includes file size, total/executable/motion counts with known/estimated flags, estimate+confidence, dimensions+confidence, and auto-level prereq summary
- Hardened G-code comment cleaning for header comments:
  - replaced non-nested regex stripping with nested `()` / `[]` comment-state scanning to prevent stray `)` artifacts from nested comment text in the G-code viewer.

### Documentation
- README testing baseline was refreshed to the current local result (`1006 passed, 2 skipped` on `python -m pytest -q`, validated 2026-03-06).
- README performance profiling examples now include `--mode unified-load` for benchmarking the 2.0.0 normalized disk-backed load path.
- `tools/profile_performance.py` now includes `--mode unified-load` with optional `--source-scan` timing for source iteration and indexed access costs.
- README `Goto Zero` behavior now documents the current XY-then-Z sequence.
- README file-picker notes now document Linux system-picker sizing behavior.
- README Kasa section now documents bounded request timeout behavior.
- README App Settings docs now include Search + `Basic`/`Advanced` global controls and touch command acknowledgment behavior.
- README checklist docs now mention collapsible checklist titles in the Checklists tab.
- README Jobs/Streaming docs now reflect the unified disk-backed load path and sample-only threshold semantics.
- README Diagnostics docs now include runtime performance profiling/leak-watch settings and the exit performance report fields.
- README Logs/Diagnostics docs now include `Clear Logs`, `Export diagnostics bundle (Save ZIP)`, and `Save final performance report (Save to Logs)`.
- README Spatial docs now include automatic Spatial-tab hide-when-disabled behavior and the session-only Spatial override.
- README now documents `SSMETA` header parsing, metadata source tags (`dimensions_source` / `units_source`), and the new scrollable File Info tab.
- README/`ref/README.md` profiling examples now include `tools/perf_microbench.py` and unified-load timing commands.
- `ref/perf_baselines.md` now includes a 2026-03-02 runtime hooks + UI/queue microbench baseline block.
- Release checklist template path was normalized to `ref/release_checklist.md` and updated with the import/compileall release gates.

### Fixed
- Progress reporting now clamps to `100%` when stream state reaches `done`, including runtime metrics/diagnostics export fields.

## [1.8.0] - 2026-02-26

### Added
- Manual-command backpressure visibility:
  - worker now emits `manual_queue_drop` UI events with both interval and cumulative drop counts
  - UI event router updates status text with cumulative dropped-command totals when the manual queue is saturated
- Integration coverage for serial-jitter disconnect behavior:
  - added streaming workflow test that forces serial write errors mid-stream and verifies interrupt/disconnect state transitions
- Touch-first G-code file browser for `Read Job`:
  - added an in-app folder/file picker with large tap targets for touchscreen workflows
  - includes Home/Up/Refresh navigation plus Windows drive shortcuts
  - dialog includes an explicit `Use System Picker` fallback to the OS-native chooser

### Changed
- Touchscreen scrollbar responsiveness:
  - scrollbar drag handling now keeps an active-drag watchdog loop so thumb movement stays responsive even when some motion events are dropped by touch drivers
  - App Settings now includes a touch scroll mode (`thumb_only` vs `thumb_and_swipe`) so operators can disable swipe-gesture interception when they want thumb-only scrollbar control
- Backend stream/manual queue hardening:
  - manual/immediate queue is now bounded (`MANUAL_COMMAND_QUEUE_MAXSIZE`) and enqueue is non-blocking
  - saturated queue paths drop new manual commands explicitly instead of blocking worker locks
- Jog-release safety hardening:
  - joystick hold polling now enforces a deadman timeout (`JOYSTICK_HOLD_DEADMAN_TIMEOUT_MS`) and force-cancels jog if polling gaps exceed the limit
  - joystick hold stop now schedules a short delayed jog-cancel retry (`0x85`) plus pending-jog clear when jog-cancel may not have resolved quickly
  - joystick button-release handling now cancels jog-bound actions (`jog_*` bindings) even when a backend release event is dropped
- G-code loading pipeline responsiveness:
  - streaming and non-streaming loader stages now perform token checks during scan/validation loops so superseded loads cancel quickly
  - stale loader workers clean up temp artifacts and return without posting stale results
- Streaming memory footprint:
  - file offset indexes now use compact contiguous storage (`array('Q')`) in streaming sources
- Settings robustness:
  - load/import now apply legacy-key migration + core-value repair before validation
  - save now writes through unique per-save temp files to avoid fixed temp-path collisions in multi-instance scenarios
- Parser/split performance:
  - parse path removes repeated modal lookup overhead and uses lower-cost bounds updates
  - split path uses lighter safe-line matching and `findall`/set-subset checks in hot loops
- Documentation refresh:
  - README now documents manual queue drop reporting, loader cancellation behavior, settings repair behavior, and latest backend hardening notes
  - README now links to a release checklist section with explicit jog-release safety criteria
  - README joystick and jogging sections now document deadman/fallback jog-stop behavior
  - README now documents the new touch-friendly `Read Job` file-browser flow and system-picker fallback
  - README testing baseline documents the then-current `pytest tests -q` snapshot (`778 passed, 2 skipped` on 2026-02-26); later refreshed in `Unreleased`
  - release checklist template now uses current mypy target count (`150`) and version placeholders
  - release checklist now includes a hardware jog-release smoke test (UI, joystick button/axis, safety-hold release, unplug, focus-loss)
  - profiling baseline document now includes current local 2026-02-25 results

### Fixed
- Disconnect cleanup on serial write exceptions:
  - write-error handlers now trigger `_signal_disconnect` whenever a serial object is present, even when `is_open` is already false due to jitter
- Jog safety resilience:
  - missed joystick release events no longer allow continued jog motion; release checks and fallback stop commands now force motion halt

## [1.7.5] - 2026-02-24

### Changed
- Runtime release metadata was aligned for this release:
  - app version string is now `1.7.5`
  - README release badge now shows `1.7.5`
- Kasa functions are now Linux-only (matching the existing System reboot/shutdown controls):
  - Kasa settings UI is hidden on non-Linux platforms
  - runtime Kasa actions are disabled on non-Linux platforms
  - saved settings force Kasa toggles off on non-Linux platforms
- Runtime dependency pinning was tightened for reproducible installs:
  - pinned `python-kasa==0.10.2`
- Legacy cleanup pass removed deprecated compatibility surfaces and duplicate module files:
  - removed `simple_sender/ui/widgets.py` compatibility shim
  - removed legacy single-file `simple_sender/ui/grbl_settings.py` in favor of `simple_sender/ui/grbl_settings/`
  - removed duplicate flat pathview modules in favor of `simple_sender/ui/pathview/`
  - removed duplicate flat `autolevel_dialog`, `console`, and `dialogs` modules in favor of package implementations
  - removed remaining unused root UI duplicates and dead entrypoints:
    - `simple_sender/ui/gcode_viewer.py`, `simple_sender/ui/sampling_policy.py`, `simple_sender/ui/popup_utils.py`
    - `simple_sender/ui/alarm_recovery_dialog.py`, `simple_sender/ui/macro_prompt_dialog.py`
    - `simple_sender/ui/autolevel_prefs.py`, `simple_sender/ui/gcode_tab.py`, `simple_sender/ui/gcode_view.py`
- Removed backup macro artifacts from runtime macro directory:
  - deleted `simple_sender/macros/BKUP_Macro-3/4/5/7`
- Local test harness hardening:
  - `run_tests.bat` now auto-selects `.venv\Scripts\python.exe` when available
  - Ruff gate now prefers `.venv\Scripts\ruff.exe` to avoid broken global launcher setups
- Homing-status behavior was tightened for very short homing cycles:
  - if status polling does not observe an explicit `Home` state, the UI now clears the temporary `Homing` indicator after a short poll-based idle grace window
  - avoids lingering `Homing` status after quick/no-travel homing completes
- Spoilboard settings migration cleanup:
  - removed legacy surfacing-depth upgrade handling from `ui/dialogs/spoilboard_generator.py`
  - removed startup compatibility migrations from `ui/app_init_settings.py`; startup now reads only current settings keys/values
- Compatibility-surface removal:
  - `ui/autolevel_dialog/__init__.py` no longer provides wrapper entry points
  - dialogs now call `ui/autolevel_dialog/dialog_controller.py` directly
  - removed legacy `console_status_enabled`/`keybindings_enabled` compatibility handling
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
  - run/manual/settings/pathview locks stay engaged during the deferred phase and release only after `Idle`
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
