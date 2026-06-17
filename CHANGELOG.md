# Changelog

All notable changes to this project are documented in this file.
Historical entries may reference pre-lean features (for example legacy pathview/Spatial work) that are no longer active in the current runtime.

## Unreleased

### Fixed
- `Apply RPM` now saves the requested spindle default but refuses to send a normal spindle-speed command while a job is streaming, avoiding a misleading confirmation path where the worker would block the command and the controller RPM would stay unchanged.
- Passive Probe indicator visibility no longer forces continuous 20 ms idle status polling; diagnostics now report the effective status-poll profile alongside configured/effective intervals.
- Updated the public GitHub sync workflow to copy the current `v3.16` About and release-note files instead of obsolete `v3.14` filenames.
- Updated stale About/help truthfulness tests that still expected the removed `v3.14` title and reference filename.

### Validation
- Current `3.16` local release gate on Windows / Python `3.12.1`: `1952 passed, 2 skipped`; Ruff, compileall, mypy manifest, critical-path coverage, and mypy passed.

## [3.16] - 2026-05-24

### Changed
- Runtime package metadata now reports `3.16`.
- Current release-facing docs and the in-app About title now identify the active application baseline as `3.16`.

### Documentation
- Release-note/About filenames now identify the `3.16` baseline.
- Current validation wording now keeps the `2026-05-08` full-suite snapshot tied to the earlier validated baseline instead of presenting it as newly rerun for this version-label update.

### Validation
- Targeted version/import assertion passed.
- `.\.venv\Scripts\python.exe -m pytest tests\unit\test_application.py -q`: `22 passed`.
- Full `run_tests.bat` release gate was not rerun for this version-label update.

## [3.14] - 2026-05-08

### Changed
- Runtime package metadata now reports `3.14`.
- Current release-facing docs now identify the active application baseline as `3.14`.
- Diagnostics bundles now include a best-effort Linux/Raspberry Pi network snapshot for Kasa/SSH reliability investigations, including local IP/routing/DNS, SSH service/journal, kernel network messages, Wi-Fi status commands when available, uptime, memory/load, and process state.

### Documentation
- Release-note/About filenames now identify the `3.14` baseline.
- Raspberry Pi image guidance now reflects that no Raspberry Pi image artifact is checked into the repository; build or attach a release asset separately when needed.
- Update-safety docs now note that the Windows share sync helper stages to a sibling pending-update folder when the runtime marker exists instead of hot-overwriting the live install.
- Current validation/testing docs now use the repo `.venv` Python path for copy-paste-safe Windows validation and tooling commands.
- Release-facing docs now point operators at `MACHINE_VALIDATION_CHECKLIST.md` for the remaining hardware-only validation work that the automated suite cannot fully prove.
- README/current-release validation notes now reflect the latest rerun explicit repo-supported gate instead of older `pytest -q` / wrapper counts that were not rerun for the current revision snapshot.
- Dry Run docs/help text now distinguish fresh Run from resume-start paths (`Resume From` / reconnect resume) instead of implying that every resume action prompts.
- Kasa operator help text now reflects the current single-outlet fallback instead of implying that every supported device must expose two outlets.
- README mypy-manifest wording now matches the release-gate checker's expected `mypy against <N> source files` phrasing while preserving the same current `141`-file count.
- README, release notes, and this changelog now reflect the `2026-05-08` local validation rerun, including `run_tests.bat` clean and the coverage pytest gate (`1935 passed, 3 skipped`).
- README Kasa troubleshooting now includes a short before-reboot field checklist for separating Pi network loss, hostname/IP/DNS/DHCP issues, Kasa plug LAN loss, and app-level Kasa failures.

### Fixed
- Application close now requests the same Stop Job/reset path before shutdown/disconnect when a job may still be running, paused, completion-pending-idle, or GRBL still reports streaming. If that stop request is unavailable or not accepted, shutdown is canceled instead of silently disconnecting.
- Resume From now reconstructs dynamic tool length offset state more conservatively: active `G43.1 Z...` is included in the resume preamble, `G49` clears the tracked offset, and unsupported `G43.1` reconstruction blocks Resume From instead of resuming with an unknown Z relationship.
- The bundled Simple-Sender Vectric post processors now emit a redundant `M5` before the initial header `TC:[TOOLNAME]`, matching the already safer tool-change block sequence.
- Bundled Vectric post contract tests now verify the intended `TC:` sequence around `VACUUM_OFF`, `M5`, `[S]M3`, dwell, and `VACUUM_ON`.
- Estimated-length file-backed streams no longer stop at a false EOF before the real cleaned end of the file:
  - file-backed streaming now keeps reading until true cleaned EOF is reached
  - normal stream `done` now requires verified EOF plus final-line acknowledgement instead of trusting an estimate
  - resume-from-line no longer rejects valid resume points just because the current file-backed source length estimate is still low
- Real-job completion hardening now enforces a safer end state before reporting clean completion:
  - the sender now forcibly issues spindle-off on the authoritative successful-completion path
  - the completion cleanup now reuses the same Park safe-Z command path used by the built-in Park workflow
  - completion now warns the operator instead of silently claiming clean success when EOF verification or post-job safer-state cleanup fails
- EOF-completion diagnostics are now more truthful:
  - completion dialogs and diagnostics now include explicit EOF-verification evidence
  - a falsey `0` final acknowledged-line index no longer degrades into `-1` in the EOF evidence path
  - final worker EOF evidence is now carried into completion/session diagnostics before the `done` state is handled, avoiding stale UI ack snapshots and false one-line shortfall warnings after fully acknowledged jobs
- Manual-command completion state is now more truthful:
  - queued-but-not-yet-sent manual commands now keep the manual-busy state active
  - `wait_for_manual_completion()` no longer reports success while commands still remain queued for send
  - no-source manual commands now default to `manual` instead of inheriting a stale prior source label
- UI/settings survivability diagnostics are now more truthful in degraded paths:
  - App Settings activation/deactivation and lazy section build failures now log instead of disappearing silently
  - settings save now logs when a Tk variable read fails and the previous persisted value is kept
  - Kasa command results now fall back to `ui_q` when `_post_ui_thread` fails, and an explicit warning is logged if a result still drops before UI reconciliation
  - Kasa stream directives now log explicit ignored/failure reasons with command source, stream line, device, retry, timeout, and elapsed-time context where available
  - Kasa command failures now clear cached device handles, retry through reconnect/discovery, classify timeout/DNS/refused/unreachable/API-style failures where possible, record bounded local-network reachability context, and warn that dust collection state was not confirmed
  - popup raise/focus failures now surface an operator-visible log message instead of failing silently
  - auto-level modal restore now logs incomplete restore commands even when the failure path returns `False` instead of throwing
  - connection timeline details now include reconnect/session context such as user-disconnect vs unexpected drop, resume-pending state, restore-failure state, and pending modal-sync state

### Documentation
- Release-facing docs were refreshed for the current repository revision:
  - README validation snapshots now reflect the latest explicitly rerun repo-supported gate instead of mixing in older command counts that were not rerun for the current revision note
  - release notes now mention the verified-EOF and safer-completion hardening
  - About text now reflects the current verified-EOF and post-job safety behavior

### Validation
- Current local repository validation snapshot for the current repository revision in the verified local Windows / Python `3.12.1` environment as of `2026-05-08`:
  - canonical wrapper (`run_tests.bat`): clean
  - import gate (`.\.venv\Scripts\python.exe -c "import simple_sender.ui.settings"` via `run_tests.bat`): clean
  - repo-supported Ruff path (`.\.venv\Scripts\python.exe tools/run_ruff.py check .`): clean
  - compile check (`.\.venv\Scripts\python.exe -m compileall simple_sender tests tools` via `run_tests.bat`): clean
  - mypy manifest gate (`.\.venv\Scripts\python.exe tools/check_mypy_targets.py --expected-count 141`): clean
  - repo-supported mypy config gate (`.\.venv\Scripts\python.exe -m mypy --config-file mypy.ini`): clean (`141` configured source files)
  - repo-supported pytest + coverage gate (`.\.venv\Scripts\python.exe -m pytest tests --cov=simple_sender --cov-report=xml --cov-report=term-missing` via `run_tests.bat`): `1935 passed, 3 skipped`
  - critical-path coverage gate (`.\.venv\Scripts\python.exe tools/check_core_coverage.py coverage.xml`): clean (aggregate critical coverage `90.4%`)
- Direct `pytest -q`, direct `.\.venv\Scripts\python.exe -m pytest`, direct `.\.venv\Scripts\python.exe -m ruff check .`, and direct `mypy main.py simple_sender` were not rerun for this specific snapshot, so older counts from those commands remain historical rather than being presented as current.
- This local validation snapshot still does not by itself confirm cross-platform CI, hardware-in-the-loop behavior, or Raspberry Pi image provenance/build validation.

## [3.11] - 2026-04-20

### Changed
- Runtime package version now reports `3.11` so the app title/diagnostics/runtime marker metadata match the current release docs.
- Top-toolbar asset loading is now cross-platform reliable for the current runtime:
  - toolbar assets now resolve from app-local `simple_sender/ui/icons`
  - runtime prefers repo-local PNG toolbar assets for the normal Windows/Linux path
  - repo-local SVG lookup remains available as a compatibility fallback
  - drawn toolbar shapes remain the final fallback only when asset loading truly fails
- GRBL Settings popup help now restores richer per-setting reference text in the safest possible way:
  - standard GRBL 1.1h settings now ship with bundled repo-local rich tooltip text
  - if the older upstream markdown reference file is present locally, its richer text still overrides the bundled content
  - settings that still lack richer reference text continue to use the existing compact fallback path
- Estimate-confidence wording is now more truthful across the current UI/help surface:
  - user-facing estimate labels now preserve `PROVISIONAL` instead of flattening every non-confident path to `ROUGH`
  - diagnostics/runtime metrics preserve that same distinction

### Fixed
- Cross-platform toolbar icon rendering now matches the intended design:
  - Windows and Raspberry Pi / Linux no longer rely on a fragile Qt-only SVG render path for the normal toolbar icon display path
  - the normal deployed toolbar path now uses app-local raster assets and runtime tinting through Pillow
- Release-gate hardening after the latest separator/layout work:
  - the shared separator helper is typed cleanly again, so `mypy main.py simple_sender` is green on the full tree
  - toolbar dummy-widget tests now follow the real separator helper path instead of the pre-helper architecture
- CI gate drift was corrected:
  - GitHub Actions now uses the same full Ruff gate path as the documented local release gate
  - the workflow mypy manifest check now expects the current `141`-file manifest instead of the stale `150` count
- Fixed-sensor / bit-setter operator help text now matches the current implementation:
  - coarse seek uses `Bit Setter Rough Probe Speed`
  - exact samples use `Bit Setter Fine Probe Speed`
  - dwell uses `Bit Setter Probe Dwell`
- Small visible help surfaces were tightened:
  - the `Tips` quick toggle now has state-specific tooltip text
  - Logs popup filters and action buttons now have explicit tooltip/help text
- Top toolbar group labels now use the toolbar/frame background instead of the darker root background, so `Connection`, `Job`, `Run`, and `Recovery` stay visually aligned with the surrounding header row.
- Required-tools displays are now more consistent across user-facing review surfaces:
  - Job Info now labels the list as `Tools Required:`
  - Job Info and the run-confirmation metadata summary now deduplicate repeated tool entries while preserving first-seen order
- Direct `mypy main.py simple_sender` is green again after typing the runtime toolbar-button metadata fields attached to `ToolbarShapeButton`.
- Narrow jog-row polish now matches the current rendered layout:
  - `mm/inch`, `Goto Zero`, and `Zero All` align vertically with the jog-step controls on their row
  - `mm/inch` now matches the `Jog to` button width without shifting neighboring controls
- Toolbar separator coverage now follows the current palette math instead of a stale hard-coded blend value, so the prerelease gate reflects the real toolbar styling path.

### Documentation
- README and changelog were refreshed so the current docs stay aligned with the shipped baseline:
  - direct local validation snapshot now reflects the latest full green run
  - wrapper-gate snapshot now reflects the latest clean `run_tests.bat` execution
  - runtime dependency docs now include Pillow for the current toolbar icon pipeline
  - toolbar icon docs now describe the current app-local raster-first path plus SVG/drawn fallback behavior
  - GRBL Settings docs now describe the bundled rich-help layer and preserved fallback behavior
  - estimation docs now mention the current `CONFIDENT` / `PROVISIONAL` / `ROUGH` distinction
  - release-note/About filenames now identify the current `3.11` baseline
  - Raspberry Pi image artifact naming now matches the `3.11` release label
- README and in-app About/help content were tightened again for current operator-facing truthfulness:
  - Job Info and Start Job confirmation now describe `Tools Required` instead of `Tools`
  - required-tools docs now state that repeated identical entries are deduplicated in first-seen order
  - App Settings / Macro Manager docs now describe the current child-popup ownership behavior
- Release-facing docs now identify `3.11` consistently across README, release notes, About text, and Raspberry Pi image guidance.

### Validation
- Current local repository validation snapshot after the latest release-candidate review:
  - direct `pytest -q`: `1885 passed, 3 skipped`
  - repo-supported Ruff path (`python tools/run_ruff.py check .`): clean
  - direct `mypy main.py simple_sender`: clean (`217` source files)
  - wrapper `run_tests.bat`: clean (`7/7` gates passed)
  - wrapper pytest + coverage stage inside `run_tests.bat`: `1885 passed, 3 skipped`
  - wrapper final mypy manifest gate inside `run_tests.bat`: clean (`141` source files)
  - targeted runtime smoke (`App()` create/update/destroy): clean

## [3.1] - 2026-04-14

### Fixed
- The `Preparing Job` path now fails cleanly if a deferred load/apply step breaks after initial progress has rendered, instead of leaving the popup stuck at an early progress value.
- Remaining worker-thread completion helpers in diagnostics/export/backup flows now fall back from `_post_ui_thread(...)` directly to `ui_q.put(...)` instead of attempting off-thread Tk `after(...)` scheduling on degraded paths.
- Standard logging now still preserves the recent in-memory serial activity tail used by diagnostics while continuing to suppress routine streamed TX file logging on the targeted hot path.

### Changed
- App Settings > Diagnostics now includes `Logging Mode` with `Standard` and `Verbose`:
  - `Standard` is the default and reduces routine TX file logging
  - `Verbose` preserves fuller detailed TX logging behavior
- Low-overhead preset/profile behavior is now truthful:
  - the diagnostics performance preset now forces `runtime_logging_mode = Standard`
  - Pi profile now forces `runtime_logging_mode = Standard`

### Documentation
- Current release-facing docs now present `3.1` as the stable, release-ready baseline instead of `3.0.11`.
- Current release-facing docs were refreshed again so the current direct validation snapshot, the last verified wrapper-gate snapshot, and current logging-mode/preset behavior stay truthful after the latest green runs.

### Validation
- `3.1` is the current stable, release-ready baseline for the present workflow architecture.
- Current local repository validation snapshot for `3.1`:
  - direct `pytest -q`: `1852 passed, 1 skipped`
  - repo-supported Ruff path (`python tools/run_ruff.py check .`): clean
  - direct `mypy main.py simple_sender`: clean (`215` source files)
  - last verified wrapper `run_tests.bat`: clean (`7/7` gates passed)
  - last verified wrapper pytest + coverage stage inside `run_tests.bat`: `1838 passed, 3 skipped`
  - last verified wrapper final mypy manifest gate inside `run_tests.bat`: clean (`141` source files)

## [3.0.11] - 2026-04-12

### Changed
- Joystick/controller safety behavior now matches the final two-button safety-speed model:
  - `Set Safety Button / Normal Jog Speed` and `Set Safety Button / Slow Jog Speed` are the two current safety bindings
  - with no safety held, no joypad/joystick bindings are acknowledged
  - with Normal held, all joypad/joystick bindings are active and joystick jogging uses configured speed
  - with Slow held, all joypad/joystick bindings are active and joystick jogging uses exactly `50%` speed
  - if both are held, Slow wins
  - keyboard and on-screen controls remain unchanged
  - legacy single safety-button settings migrate to the Normal binding
- Resume/reconnect safety parity was tightened for the current baseline:
  - `Resume From...` now uses the same Job Setup validity confirmation model as fresh Run before starting a resumed stream
  - reconnect-resume now carries forward the same `G92` warning state as manual Resume instead of dropping that signal
  - resume admission now reruns preflight after a `Start Anyway` Job Setup override so Resume does not become less strict than Run
- Resume dialog preview/start behavior is now safety-first:
  - the dialog no longer starts a resume before the background preview has finished when the preview is still needed to determine `G92` risk
  - disabling modal re-sync no longer creates a path that can bypass preview-backed resume warnings
- App Settings popup startup is now more responsive on first open:
  - the popup shell opens immediately
  - the heavy App Settings body now builds on idle instead of blocking the initial button press
- App Settings popup preloading is now operator-controlled:
  - `Preload App Settings popup after startup` was added under `App Settings > Interface`
  - the preload path is now disabled by default and only runs on startup when the operator enables it
- Dry Run confirmation dialog styling now uses the shared themed toplevel path, so the warning dialog no longer falls back to a bright white background under the dark UI.
- UI queue wake-up latency was tightened modestly for the current baseline:
  - when the UI queue is idle and new work is posted, the drain loop is now scheduled promptly instead of waiting for the next normal timer tick
  - the existing bounded drain/coalescing/backoff model remains in place once the loop is active

### Fixed
- Z Plate Job Setup now preserves the existing X/Y work zero instead of overwriting X/Y during a Z-only setup path.
- Built-in workflow startup snapshot acquisition now retries once before aborting, so first-run Job Setup no longer fails just because the initial `$G` modal/status snapshot lands late.
- Job Info popup first-open rendering now stays truthful when load metadata is already available, so the popup no longer opens blank until the operator closes/reopens or otherwise forces a later refresh path.
- Dry Run wording was corrected to match the actual shipped behavior:
  - spindle/coolant and `M6`/`S`/`T` sanitization still apply
  - sender-side `TC:<tool name>` directives still pause and run the built-in Tool Change workflow
- Manual-command bookkeeping during alarm transitions no longer leaves stale pending/manual-tracker state behind when a command becomes blocked before send.
- G-code motion-line counting no longer treats non-motion codes as motion just because they contain axis letters or similar substrings.
- GRBL Settings popup now hydrates from the already-captured post-connect `$$` snapshot on first open instead of staying blank until the operator clicks `Refresh $$`.
- Deferred-completion finalization now refreshes the top-right progress bar and label through the shared progress UI path, so the visible display reaches the same authoritative `100.0%` completion state reflected by byte EOF and completion logging.
- Work-position zero actions now require operator confirmation for `X`, `Y`, `Z`, and `All` before sending the zeroing command.
- App Settings first-open responsiveness no longer makes the `View Logs...` path feel like it needs a second click while the settings popup is still constructing.
- Start Job confirmation no longer shows the stale G-code validation line or validation-details affordance now that that review path is no longer offered there.
- Nested popup ownership is now truthful for `App Settings -> View Logs... -> Clear Logs`:
  - App Settings stays open underneath Logs
  - Clear Logs confirmation is parented above Logs without hiding or disturbing either underlying popup
- `App Settings -> Macros -> Open Macro Manager` now follows the same child-popup ownership pattern as the working App Settings child dialogs:
  - the first click now opens the Macro Manager immediately instead of creating a hidden-behind-parent window that only becomes reachable on the second click
  - App Settings stays open and visible underneath while Macro Manager is open
  - Macro Manager is parented/transient to the visible App Settings popup when present, then lifted and focused on first show and reuse
  - closing Macro Manager only closes that child window and leaves App Settings open
  - root cause was the Macro Manager launch path in `simple_sender/ui/dialogs/macro_manager.py` parenting to the root app and not forcing first-show lift/focus, unlike the already-correct App Settings child-popup paths
  - focused regression coverage was added in `tests/ui/test_macro_manager_dialog.py`

### Documentation
- Deep docs truthfulness pass:
  - README no longer points at missing `ref/VCarve-PP/` or `ref/test.py` assets
  - README and changelog no longer point at missing historical reference files
  - README changelog links now use normal repo-relative paths instead of machine-local paths
  - README appendix wording now matches the current popup-based UI and protected workflow/user-macro layout
- Changelog/release-facing wording now reflects the current baseline more precisely:
  - built-in tool change docs state the real current contract: park at safe Z over WCS `X0/Y0`, then rely on the posted job to reposition
  - Resume docs now describe the current Dry Run, Job Setup, and `G92` safeguard behavior
  - historical v3.0 changelog wording no longer presents a standing "recent real-machine validation" claim as if it were evergreen release proof
- README now reflects the current Start Job confirmation content and the intended App Settings/Logs/Clear Logs popup stacking behavior.
- README popup/operator wording now reflects the current first-open truthfulness of Job Info and GRBL Settings, the current sparse lifecycle console logging cadence (load, start, immediate telemetry, `10%` milestones, sparse heartbeat, completion), the current controller-wide joystick safety behavior, and the current modest UI-queue wake-up improvement.

### Validation
- `3.0.11` was the stable, release-ready baseline for the present workflow architecture at that point.
- Local repository validation snapshot recorded for `3.0.11`:
  - direct `pytest -q`: `1831 passed, 3 skipped`
  - direct `ruff check .`: clean
  - direct `mypy main.py simple_sender`: clean (`215` source files)
  - wrapper `run_tests.bat`: clean (`7/7` gates passed)
  - wrapper pytest + coverage stage inside `run_tests.bat`: `1831 passed, 3 skipped`
  - wrapper final mypy manifest gate inside `run_tests.bat`: clean (`141` source files)
- Local release-gate truthfulness was tightened:
  - `run_tests.bat` now runs the same full Ruff scope as direct `ruff check .`
  - the wrapper no longer reports green on a narrower syntax/pyflakes-only Ruff subset while direct Ruff is red

## [3.0] - 2026-04-05

### Changed
- Lower UI redesigned around the current machine-side workflow:
  - removed the visible lower G-code tab
  - Console is now persistent on the left
  - right-side machine controls remain visible alongside the Console
  - Job Info, Checklists, Logs, Raw $$, GRBL Settings, and App Settings now open as large dark-themed popups
- Workflow/macro architecture finalized for the current runtime:
  - protected built-in workflow actions are now implemented directly in application code, not as macro files
  - user macros are now the only file-backed macros
  - editable user macro storage now uses `Macro-1` through `Macro-5`
- Lower-UI cleanup/refactor completed:
  - removed notebook-era lower-layout compatibility support from the active runtime model
  - trimmed hidden legacy live-window/runtime work tied to the removed lower G-code pane
  - runtime now uses a headless live-window/job-view state holder instead of a visible lower G-code widget surface
  - aligned runtime naming, tests, and documentation with the current popup/button model
- Probing and setup workflows were hardened across the release cycle:
  - machine-Z-aware touchplate planning now clamps against real machine travel
  - fixed-sensor / bitsetter probing now validates retract vs fine re-probe tuning before motion
  - fixed-sensor fine sampling was retuned to eliminate the confirmed post-contact `ALARM:5` mismatch
  - Tool Change gets a one-time slower retry pass only when first-round spread exceeds `0.050 mm`
  - Run / Job Setup validity now matches Tool Change's current tool-reference format requirement
- Release-facing docs now reflect the current lower UI, current setup/tool-reference expectations, and the supported deployment/update workflow.

### Fixed
- Alarm and workflow dialogs now follow the dark UI consistently, including the GRBL alarm popup, alarm recovery, Start Job confirmation, and Job Setup warning surfaces.
- File metadata and run-confirmation summaries keep tool metadata truthful instead of implying false toolpath-to-tool pairings.
- Recent lower-UI/runtime cleanup reduced avoidable hidden/background work without changing machine-control semantics.

### Validation
- The v3.0 line is the stable runtime/docs baseline for the current workflow architecture.
- Machine-specific validation remains operator/machine dependent and should be performed on the target machine before production use.

### Documentation
- README, macro docs, deployment docs, About docs, and release-facing notes were refreshed for the stable v3.0 release.
- Added dedicated v3.0 release notes.

## [2.8] - 2026-03-31

### Changed
- Gemini-inspired dark theme is now the normal startup default for new/default settings while preserving saved user theme selections.
- Notebook tab visibility is now operator-configurable from App Settings:
  - `Logs` is hidden by default and controlled by `Show Logs Tab`
  - `Raw $$` is hidden by default and controlled by `Show Raw $$ Tab`
  - `Checklists` remains shown by default and is controlled by `Show Checklists Tab`
- Dry Run safeguard policy now applies consistently to resume flows:
  - `Resume From...` now uses the same explicit Dry Run decision model as Run when Dry Run is enabled
  - reconnect-resume routes through the same guarded resume path, so recovery resumes cannot bypass the Dry Run decision
  - canceling the Dry Run resume prompt now leaves resume state truthful and pre-resume side effects unapplied
- Dry Run confirmation dialog now supports action-specific wording for normal-mode continuation:
  - Run continues to use `Switch to Normal Run and Start`
  - Resume uses `Switch to Normal Run and Resume`
- Shared tooltip styling now uses explicit readable colors and theme-aware palette resolution so tooltip text stays visible under the active theme.
- Notebook tabs use slightly larger shared tab padding to better match the main UI button sizing.

### Fixed
- Reconnect loaded-job restore now ends in a truthful state:
  - if the worker-side job restore fails after reconnect, the previous job is cleared instead of continuing to look loaded
  - Run / Resume readiness no longer remains ahead of the actual restored worker state
- `Clear Job` now clears File Info truthfully instead of leaving stale metadata visible after the underlying job state is gone.
- Secondary text-display dialogs that previously bypassed the shared dark-pane styling path now follow the current theme instead of falling back to bright white panes.
- Backup-bundle async completion now clears inflight flags even if the UI-post completion callback cannot be delivered during teardown.
- Tooltips now remain readable after the theme-default and dark-theme polish work.

### Documentation
- README now documents the unified Dry Run safeguard behavior across Run and Resume paths, including reconnect-resume inheritance.
- README and macro docs now document `Disable Macro Timeouts`, the hidden-by-default `Logs` / `Raw $$` tabs, Gemini as the default theme, and the scoped no-timeout behavior used for operator-assisted Job Setup / Tool Change workflows.
- README validation baseline wording was updated at that time to reflect the then-current documented local `run_tests.bat` snapshot (`1508 passed, 2 skipped` on 2026-03-31) instead of older "latest/current" counts.
- Historical closeout docs now read as closeout snapshots rather than live project-status documents.

## [2.7.1] - 2026-03-27

### Added
- Auto-Level dialog `Test Probe` action:
  - runs a single-point probe at the current XY using the active probe settings
  - leaves the current height map/job apply state unchanged (non-destructive validation flow)
  - records a timestamped `Last test probe [...]` result line with XYZ hit details or failure reason
- `tests/unit/test_grbl_worker_status_coverage.py` to exercise status-wait and status-trace paths in `grbl_worker_status`, including idle-timeout and non-idle transition coverage used by the critical coverage gate.
- Regression coverage for performance-mode poll/visual tuning:
  - `tests/ui/test_grbl_lifecycle.py` now verifies the performance-mode running poll floor
  - `tests/ui/test_status_efficiency.py` now verifies WPos flash suppression during performance-mode streaming
- Regression coverage for queue-pressure status smoothing:
  - `tests/ui/test_status_efficiency.py` now verifies forced position-update deferral under UI queue pressure
  - `tests/ui/test_status_efficiency.py` now verifies adaptive coalesce-interval behavior while pressure is active
- Custom streamed sender directives for CAM post integration:
  - exact trimmed `VACUUM_ON` / `VACUUM_OFF` lines are intercepted and handled internally (vacuum on/off actions), never forwarded to GRBL
  - lines beginning with `TC:` are intercepted as required-tool directives, routed through the existing tool-change dialog + macro workflow, and never forwarded to GRBL
  - stream-progress accounting now treats these directives as handled lines so file progress/line advancement remains correct without controller transmission
- Regression coverage for custom directive streaming:
  - added `tests/unit/test_stream_custom_directives.py` for `VACUUM_ON`, `VACUUM_OFF`, `TC:<tool name>`, and normal-line behavior through the pre-send stream pipeline
  - added `tests/unit/test_tool_change_actions.py` for tool-name propagation, no-timeout tool-change waiting, and directive-driven vacuum action routing
- Job Setup safety-gate regression coverage:
  - `tests/ui/test_ui_commands.py` now verifies Run warning/cancel/start-anyway behavior based on tool-reference validity
  - `tests/ui/test_grbl_lifecycle.py` now verifies tool-reference invalidation across connect/disconnect/ready-loss transitions
  - `tests/ui/test_all_stop.py` now verifies reset-path invalidation for tool-reference setup state

### Changed
- 2026-03-25 pre-release stabilization pass:
  - realtime control paths (`Pause`, `Resume`, `Stop`, `Reset`, `ALL STOP`, alarm-recovery Reset, and the matching macro actions) now return explicit accepted/failed outcomes instead of silently looking like success when nothing was sent to GRBL
  - backup-bundle import/export now uses the hardened settings import/export path, stages file changes safely, detects macro/checklist overwrite collisions before commit, and applies async import completion back to the UI on success
  - machine-profile, Pi-profile, and interface-setting persistence flows now distinguish durable success from in-memory-only changes, and startup settings repair/reset paths now surface operator-visible warnings instead of silent normalization
  - close/cancel-close behavior now defers shutdown state until the operator actually commits to exit, keeping canceled close attempts fully recoverable
  - macro startup/load/connect flows now use truthful result tracking (`$G` startup snapshot completion, real loader-token/result handling, blocked `OPEN`/`CLOSE` detection, truthful `SENDHEX` / `SAFE` failures)
  - deferred post-run `done pending idle` state is now treated consistently as busy across macro start, probing, settings refresh, profile/unit changes, load/clear/connect/disconnect, and related recovery-sensitive actions
  - Macro Manager slot save/move/delete/duplicate workflows and several export/save paths now use safer atomic-write or failure-aware handling to reduce destructive partial updates
- Stabilization baseline for the closed refactor cycle:
  - the low-risk cleanup and preflight-service extraction work is now treated as complete
  - the current codebase is the recommended stable baseline for subsequent bug-fix-only work
  - compatibility-sensitive and timing-sensitive areas remain intentionally unchanged
- Release-candidate reliability hardening for v2.6:
  - worker-thread UI updates for connect/disconnect, G-code parse, settings save paths, and log viewer actions now marshal through UI-thread queue helpers (`ui_post`) instead of direct cross-thread Tk calls
  - GRBL settings `Save Changes` now completes as send-plus-verify: edited values are sent, then confirmed against a follow-up `$$` capture before success is reported
  - settings verification now treats numerically equivalent values (for example `250`, `250.0`, `250.000`) as equivalent for numeric settings while keeping strict string checks for non-numeric settings
  - Resume From dialog preview generation now runs with debounce + background computation to reduce UI-thread blocking while editing line numbers
  - log export now reports full success, partial success (with failed-file details), or total failure explicitly
- Release polish for 2.4.0:
  - removed beta branding from app title/version banner and docs
  - promoted the current build as full `2.4.0` release metadata
- App Settings layout update:
  - moved `Show Resume From...`, `Show Recover`, and `Enable Auto-Level` into a new **Experimental** section
  - kept Interface focused on startup/performance/logging/status controls
- Lean runtime cleanup pass:
  - removed obsolete `streaming_validation_prompt` UI event path and related type/constants/test hooks
  - simplified fast-load worker API by dropping stale `_stream_from_disk` validation/sample threshold parameters that were no-ops
- Documentation refresh for lean sender runtime:
  - README now documents the bounded Live G-code window (`500 past/current/500 next`) instead of legacy sent/acked highlight wording
  - run-path validation text now reflects the non-blocking shared validation/reporting flow used during load/start
  - diagnostics/settings wording updated to reflect the current fast-load thresholds
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
- Pi/performance-mode status-path tuning:
  - running-state status polling now uses a performance-mode floor (`0.35s`) instead of forcing the ultra-fast default profile, reducing status-event pressure on Pi-class hosts during active jobs
  - WPos flash-highlight cosmetics are now suppressed during performance-mode streaming to reduce per-status Tk widget churn
- Streaming status hot-path smoothing:
  - status position updates now support pressure-aware forced deferral when UI queue backlog or per-event runtime budget indicates pressure
  - coalesce cadence is now adaptive under pressure, with bounded recovery back to the baseline interval
  - new status performance metric `positions_coalesced_pressure_defer` is emitted for diagnostics/telemetry
- Job Start setup guard:
  - Run now checks the current `macro.state.TOOL_REFERENCE` (same source used by the Tool Ref display) instead of macro-click history
  - when setup is invalid/missing, Run shows `Job Setup Not Completed` with `Start Anyway` / `Cancel`
  - setup state is invalidated on connection/session resets and reset-style stop paths so stale setup does not silently carry across sessions

### Documentation
- README now reflects the 2026-03-25 pre-release stabilization work:
  - realtime control actions only acknowledge accepted sends
  - backup-bundle import validates settings, warns about repairs/collisions, and completes the async success path visibly
  - deferred-completion busy protection now covers macro start, probing, and settings refresh
  - the local release-gate baseline now reflects the latest `run_tests.bat` run (`1433 passed, 3 skipped`)
- README now documents the stabilization baseline, the direct preflight service boundary, and expanded operator troubleshooting for preflight outcomes.
- README Auto-Level docs now include the `Test Probe` operator flow and the `Last test probe` status/result line.
- README testing baseline now reflects the latest full local release-gate run (`run_tests.bat` passed end-to-end on 2026-03-27; coverage test stage reported `1433 passed, 3 skipped`).
- README and macro docs now describe custom stream directives (`VACUUM_ON`, `VACUUM_OFF`, `TC:<tool name>`), including interception-before-send behavior, Kasa vacuum integration, and no-timeout tool-change workflow handling.
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
- README profiling examples now include `tools/perf_microbench.py` and unified-load timing commands.
- Historical performance baseline documentation was updated with a 2026-03-02 runtime hooks + UI/queue microbench block.
- Historical release checklist wording was updated to include the import/compileall release gates.
- README and `simple_sender/macros/readme.md` now document the Job Setup Run warning, operator workflow expectations, and setup-state invalidation behavior.

### Fixed
- Release-candidate truthfulness/durability fixes from 2026-03-25:
  - fixed macro `LOAD` runtime result tracking so the real application wrapper returns the loader token/result used by the hardened macro wait path
  - fixed macro startup `$G` false timeouts by resolving tracked modal-snapshot requests on `[GC:...]` receipt in the shared worker path
  - fixed the async backup-bundle import success path so successful imports now refresh UI state and show completion results
  - fixed shutdown save-failure handling so canceling close no longer leaves shutdown-side state behind
  - fixed stale-state Stop Job and realtime recovery/control paths that could previously imply success or invalidate setup state when no real controller action occurred
- Progress reporting now clamps to `100%` when stream state reaches `done`, including runtime metrics/diagnostics export fields.
- `grbl_worker_status` status-wait tracing now normalizes trace payload types before serializing/logging so mypy remains clean on strict checks while preserving runtime diagnostics behavior.
- Shutdown sequencing now remains best-effort across all steps: a settings-save failure no longer skips GRBL disconnect and final resource cleanup.
- Macro parser now preserves expression-only bracket lines (for example `["G0 X0" if cond else ""]`) through the expression-evaluation path so conditional macro command lines execute instead of being dropped.

### Baseline Validation (local, 2026-03-27)
- `run_tests.bat`: PASS (`7/7` gates passed; coverage test stage `1433 passed, 3 skipped`)
- `.venv\Scripts\python.exe tools/check_mypy_targets.py --expected-count 142`: PASS
- `.venv\Scripts\python.exe -m mypy --config-file mypy.ini`: PASS (`142` source files)

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

