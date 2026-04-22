# Simple Sender v3.12

These notes describe the current stable, release-ready `3.12` baseline. This release carries forward the current split-layout/popup workflow model, the current probing and tool-measurement safeguards, the current toolbar asset pipeline, and a fresh prerelease review that tightened release-gate truthfulness and small lower-UI polish details.

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
  - Run / Job Setup validity now matches the real Tool Change prerequisites
  - alarm and warning dialogs now match the dark UI consistently
  - file/job summary surfaces keep tool metadata truthful
  - normal successful completion now requires verified cleaned EOF before clean success
  - real-job completion now enforces sender-side spindle-off plus the same Park safe-Z raise used by the built-in Park workflow, warning instead of silently claiming clean completion when that safer end state cannot be achieved
- Release-candidate hardening and polish:
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
  - runtime package metadata now reports `3.12`
  - release-facing docs now consistently describe the `3.12` baseline
  - no Raspberry Pi image artifact is checked into this repository
  - if you need strict image provenance or a version-aligned distributable image for `3.12`, rebuild it and publish it separately, for example as a release asset, before shipping it

## Practical Notes

- These notes cover the stable `3.12` release line for the present workflow architecture.
- The current stable baseline is `3.12`, with docs aligned to the shipped Job Setup, Tool Change, controller safety, progress/completion, current queue-responsiveness behavior, current logging-mode / low-overhead preset behavior, and the current cross-platform toolbar icon pipeline.
- The intended audience is operators who want the current stronger workflow baseline with stable-release truthfulness around the current runtime and docs.
- Older lower-UI visibility fallback keys from the notebook-era model have been removed; the current popup/button model is now the only supported runtime architecture.
- In the current baseline, toolbar icon assets live under `simple_sender/ui/icons`, the normal runtime path prefers app-local raster assets for Windows/Linux consistency, and SVG/drawn icons remain compatibility fallbacks instead of the primary deployment path.

## Release Focus

`3.12` is the current release-ready packaging of that workflow direction for the runtime and release-facing docs. No Raspberry Pi image artifact is checked into this repository; rebuild and publish one separately if you need a version-aligned distributable image.

## Current Validation Snapshot

As of `2026-04-22`, the current repository revision validates clean in the verified local Windows / Python `3.12.1` environment:

- `.\.venv\Scripts\python.exe -m pytest -q`: `1906 passed, 3 skipped`
- `.\.venv\Scripts\python.exe tools/run_ruff.py check .`: clean
- `.\.venv\Scripts\python.exe -m mypy main.py simple_sender`: clean (`218` source files)
- `run_tests.bat`: clean (`7/7` gates passed)
- wrapper pytest + coverage stage inside `run_tests.bat`: `1907 passed, 2 skipped`

This local validation snapshot does not by itself confirm cross-platform CI, hardware-in-the-loop behavior, or Raspberry Pi image provenance/build validation.
