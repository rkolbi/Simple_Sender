# Simple Sender v3.11

These notes describe the current stable, release-ready `3.11` baseline. This release carries forward the current split-layout/popup workflow model, the current probing and tool-measurement safeguards, the current toolbar asset pipeline, and a fresh prerelease review that tightened release-gate truthfulness and small lower-UI polish details.

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
  - runtime package metadata now reports `3.11`
  - release-facing docs now consistently describe the `3.11` baseline
  - the checked-in Raspberry Pi image artifact is labeled `SimpleSender_3.11-rpi4-dietpi.img.xz`
  - if you need strict image provenance for distribution, verify or rebuild that image artifact before shipping it

## Practical Notes

- These notes cover the stable `3.11` release line for the present workflow architecture.
- The current stable baseline is `3.11`, with docs aligned to the shipped Job Setup, Tool Change, controller safety, progress/completion, current queue-responsiveness behavior, current logging-mode / low-overhead preset behavior, and the current cross-platform toolbar icon pipeline.
- The intended audience is operators who want the current stronger workflow baseline with stable-release truthfulness around the current runtime and docs.
- Older lower-UI visibility fallback keys from the notebook-era model have been removed; the current popup/button model is now the only supported runtime architecture.
- In the current baseline, toolbar icon assets live under `simple_sender/ui/icons`, the normal runtime path prefers app-local raster assets for Windows/Linux consistency, and SVG/drawn icons remain compatibility fallbacks instead of the primary deployment path.

## Release Focus

`3.11` is the current release-ready packaging of that workflow direction: the runtime version, release notes, About summary, README, and release artifact naming now all identify the same baseline.

## Current Validation Snapshot

As of `2026-04-20`, the current repository revision validates clean locally:

- `pytest -q`: `1885 passed, 3 skipped`
- `python tools/run_ruff.py check .`: clean
- `python -m mypy main.py simple_sender`: clean (`217` source files)
- `run_tests.bat`: clean (`7/7` gates passed)
- targeted runtime smoke (`App()` create/update/destroy): clean
