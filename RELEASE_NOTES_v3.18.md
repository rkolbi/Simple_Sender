# Simple Sender v3.18

These notes describe the current stable, release-ready `3.18` baseline. This version-label update carries forward the current split-layout/popup workflow model, the current probing and tool-measurement safeguards, and the current toolbar asset pipeline without intentionally changing operator workflow behavior.

## Highlights

- Lower UI redesigned for machine use:
  - persistent Console on the left
  - integrated right-side controls that stay visible during normal operation
  - popup-based Job Info, Checklists, Logs, Raw $$, GRBL Settings, and App Settings
- Probing and tool-measurement hardening:
  - safer machine-Z-aware touchplate planning
  - fixed-sensor / bitsetter sequence guardrails
  - corrected fine retract / fine re-probe mismatch that caused a confirmed `ALARM:5`
  - Tool Change one-time slower retry when clustering is too loose
- Workflow truthfulness improvements:
  - auto-level probing now accepts a valid `[PRB:...]` report that arrives during the probe command/idle wait, reducing false probe failures without changing the physical probing command sequence
  - Run / Job Setup validity now matches the real Tool Change prerequisites
  - alarm and warning dialogs now match the dark UI consistently
  - file/job summary surfaces keep tool metadata truthful
  - file-backed quick-load preview/event payloads preserve the bounded sample lines captured during quick scan while leaving streaming delivery unchanged
  - quick-scan bounds confidence is downgraded to rough when endpoint-only scan bounds include `G2`/`G3` arcs without richer `SSMETA` extents; this does not calculate true arc extents
  - normal successful completion now requires verified cleaned EOF before clean success
  - real-job completion now enforces sender-side spindle-off plus the same Park safe-Z raise used by the built-in Park workflow, warning instead of silently claiming clean completion when that safer end state cannot be achieved
  - EOF-completion diagnostics now carry explicit EOF-verification evidence into completion/session diagnostics
  - app close now requests Stop Job before shutdown/disconnect when a job may still be active, paused, completion-pending-idle, or still reported as streaming
  - shutdown timeout status/log output now reports the last reported cleanup step before forced close, without claiming that step is the proven root cause
  - Resume From now preserves safe active `G43.1 Z...` dynamic tool length offset state, clears it on `G49`, and blocks unsupported TLO reconstruction
  - Resume From documentation now states that modal reconstruction does not prove physical cutter position, so the operator must verify work zero, tool, Z clearance, spindle state, and physical position before resuming
  - send-time validation failures for `$` job lines, non-ASCII text, and lines that still exceed 80 bytes now enter terminal stream-error handling instead of a normally resumable pause
  - clean-completion accessory shutdown is deferred until after the final spindle-off / completion cleanup result is known
  - automatic completion `G53` safe-Z is gated on trusted machine coordinates and warns instead of moving when trust is unknown
- Kasa/network reliability diagnostics:
  - Kasa command failures now clear cached device handles, retry through reconnect/discovery, and classify timeout/DNS/refused/unreachable/API-style failures where possible
  - stored-IP Kasa lookup now falls back to discovery by device ID when direct-IP lookup fails
  - Linux/Raspberry Pi diagnostics bundles now include a best-effort local network snapshot for Kasa/SSH troubleshooting
  - Kasa failures warn that dust collection state was not confirmed while the CNC job may continue
  - accepted job-accessory OFF requests remain tracked until a confirmed OFF succeeds, so a queued command that later fails does not falsely clear active accessory state
  - an optional Kasa setting can require streamed `VACUUM_ON` / `VACUUM_OFF` directive confirmation during active jobs; when enabled, the stream holds until the command result succeeds or fails
- Release-candidate hardening and polish:
  - bundled Simple-Sender Vectric posts now emit redundant `M5` before the initial header `TC:[TOOLNAME]`
  - bundled Vectric post contract tests verify the intended `VACUUM_OFF` / `M5` / `TC:` / `[S]M3` / dwell / `VACUUM_ON` sequence
  - toolbar separator tests now follow the current palette math instead of a stale hard-coded blend value
  - the `mm/inch`, `Goto Zero`, and `Zero All` row now aligns cleanly with the jog-step controls
  - the `mm/inch` button now matches the `Jog to` button width without moving neighboring controls
- Cleanup and efficiency work:
  - removed the visible lower G-code tab and the old lower notebook-era runtime architecture
  - reduced hidden lower-UI background work tied to removed legacy surfaces
  - protected built-in workflow actions are now implemented directly in application code instead of bundled macro files
  - user macros are now the only file-backed macros and occupy `Macro-1` through `Macro-5`
  - live job-view bookkeeping now uses a headless runtime state object instead of a visible lower G-code widget
  - aligned tests, docs, and runtime naming with the current split-layout/popup model
- Current release alignment:
  - runtime package metadata now reports `3.18`
  - release-facing docs now consistently describe the `3.18` baseline
  - the repository now includes `MACHINE_VALIDATION_CHECKLIST.md` for the remaining on-machine validation work that automation alone cannot fully prove
  - no Raspberry Pi image artifact is checked into this repository
  - if you need strict image provenance or a version-aligned distributable image for `3.18`, rebuild it and publish it separately, for example as a release asset, before shipping it

## Practical Notes

- These notes cover the stable `3.18` release line for the present workflow architecture.
- The current stable baseline is `3.18`, with docs aligned to the shipped Job Setup, Tool Change, controller safety, progress/completion, current Kasa/network diagnostics, current queue-responsiveness behavior, current logging-mode / low-overhead preset behavior, and the current cross-platform toolbar icon pipeline.
- The intended audience is operators who want the current stronger workflow baseline with stable-release truthfulness around the current runtime and docs.
- Older lower-UI visibility fallback keys from the notebook-era model have been removed; the current popup/button model is now the only supported runtime architecture.
- In the current baseline, toolbar icon assets live under `simple_sender/ui/icons`, the normal runtime path prefers app-local raster assets for Windows/Linux consistency, and SVG/drawn icons remain compatibility fallbacks instead of the primary deployment path.

## Release Focus

`3.18` is the current release-ready packaging of that workflow direction for the runtime and release-facing docs. No Raspberry Pi image artifact is checked into this repository; rebuild and publish one separately if you need a version-aligned distributable image.

## Current Validation

As of `2026-06-28`, the `3.18` release work validated clean in the verified local Windows / Python `3.12.1` environment with the explicit repo-supported commands that were actually rerun:

- `.\.venv\Scripts\python.exe -m pytest`: `1970 passed, 2 skipped`
- `.\.venv\Scripts\python.exe tools\run_ruff.py check .`: clean
- `.\.venv\Scripts\python.exe -m compileall -q simple_sender tests tools`: clean
- `.\.venv\Scripts\python.exe tools\check_mypy_targets.py --expected-count 141`: clean
- `.\.venv\Scripts\python.exe -m mypy --config-file mypy.ini`: clean (`141` configured source files)

The `run_tests.bat` wrapper, pytest coverage gate, critical-path coverage check, cross-platform CI, hardware-in-the-loop behavior, and Raspberry Pi image provenance/build validation were not rerun for this `3.18` release work. Historical wrapper/coverage snapshots remain in the changelog instead of being presented as newly rerun for this release.
