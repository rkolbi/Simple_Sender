# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Spoilboard Generator in the Overdrive tab:
  - creates surfacing G-code in-memory from width/height/tool/stepover/feed/RPM/start XY inputs plus `Surfacing Depth (mm)` (default `0.50`)
  - uses a relative-Z safe workflow (`+10 mm` lift at start, spindle start, `G4 P5` dwell, absolute move to start XY, plunge to `Z = -SurfacingDepth`, `+10 mm` lift at end)
  - shows a post-generate modal with **Read G-code**, **Save G-code**, and **Cancel**
  - **Save G-code** defaults to the app log directory with `surfacing-YYYYMMDD-HHMMSS.nc`
  - includes updated README operation docs and safety checklist for running surfacing with `Z0` set to spoilboard top

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
