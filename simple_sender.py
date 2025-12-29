# Simple Sender – Full Manual

Minimal, reliable GRBL 1.1h sender for 3-axis controllers. Python + Tkinter + pyserial. This manual is the single place to learn, use, and troubleshoot the app.

Safety: Alpha software. Test in air with spindle off.

## Table of Contents
- [Overview](#overview)
- [Requirements & Installation](#requirements--installation)
- [Launching](#launching)
- [Safety Basics](#safety-basics)
- [Operation / Use Walkthrough](#operation--use-walkthrough)
- [Quick Start Workflow](#quick-start-workflow)
- [UI Tour](#ui-tour)
- [Core Behaviors](#core-behaviors)
- [Jobs, Files, and Streaming](#jobs-files-and-streaming)
- [Jogging & Units](#jogging--units)
- [Console & Manual Commands](#console--manual-commands)
- [GRBL Settings UI](#grbl-settings-ui)
- [Macros](#macros)
- [Estimation & 3D View](#estimation--3d-view)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Logs & Filters](#logs--filters)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Appendix A: GRBL 1.1h Commands](#appendix-a-grbl-11h-commands)
- [Appendix B: GRBL 1.1h Settings](#appendix-b-grbl-11h-settings)

## Overview
- Target: GRBL 1.1h, 3-axis.
- Character-count streaming with live buffer fill.
- Alarm-safe: locks controls except unlock/home; Training Wheels confirmations for critical actions.
- Handshake: waits for banner + first status before enabling controls/$$.
- Read-only file load (“Read G-code”), clear/unload button, inline status/progress.
- Idle status spam suppressed in console; filters for alarms/errors.
- Macros: left-click to run, right-click to preview.
- Auto-reconnect (configurable) to last port after unexpected disconnect.

## Requirements & Installation
- Python 3.x, Tkinter (bundled), pyserial.
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pyserial
```

## Launching
```powershell
python simple-sender.py
```

## Safety Basics
- Test in air, spindle off.
- Configure homing/limits on the controller.
- Keep an e-stop/power cutoff reachable.
- ALL STOP behavior is configurable; use it for immediate halt.
- Only run trusted macro files.

## Operation / Use Walkthrough
This is a practical, end-to-end flow with rationale for key options.

1) **Connect and handshake**
   - Pick your COM port (auto-selects last if “Reconnect to last port on open” is enabled in App Settings).
   - Click Connect (Training Wheels may prompt). The app waits for the GRBL banner and first status before enabling controls and $$; this avoids startup races.
2) **Confirm machine readiness**
   - If the state is Alarm, use **Unlock ($X)** or **Home ($H)**. The top-bar Unlock is always available; it’s safest to home if switches exist.
   - Verify limits/homing are configured in GRBL ($20/$21/$22) as needed.
   - Check DRO updates (MPos/WPos) to ensure status is flowing; idle status spam is muted in the console but still processed.
3) **Set units and jogging**
   - Use the unit toggle (mm/inch); jog commands insert the proper G20/G21.
   - Choose jog steps and test jogs with $J= moves; Jog Cancel (0x85) is available. Jogging is blocked during streaming/alarms.
4) **Load G-code**
   - Click **Read G-code**; file is read-only, comments/% lines stripped, chunked if large. Use **Clear G-code** to unload if needed.
   - Check the G-code viewer highlights and the 3D view (optional) for bounds sanity.
   - Review time/bounds estimates; if $110-112 are missing, set a fallback rapid rate in App Settings and adjust the estimate factor.
5) **App safety options**
   - Training Wheels ON: confirms critical actions (run/pause/resume/stop/spindle/clear/unlock/connect).
   - ALL STOP mode: choose soft reset only, or stop-stream + reset (safer mid-job).
   - Auto-reconnect: enable if you want recovery after USB blips; disable for lab environments where auto-reconnect is not desired.
6) **Prepare the machine**
   - Home if required; set work offsets (Zero buttons use G92 by default). If you prefer persistent offsets, swap zeroing to G10 in code.
   - Position above stock; verify spindle control if using M3/M5 (or disable spindle in code for dry run).
7) **Start and monitor**
   - Click **Run** (Training Wheels may prompt). Streaming uses character-counting flow control; buffer fill and progress update as acks arrive.
   - Use **Pause/Resume** for feed hold/cycle start; **Stop/Reset** for soft reset; **ALL STOP** for immediate halt per your chosen mode.
   - Idle status logs are muted, but status drives DRO/overrides; overrides buttons (feed/spindle) send RT codes.
8) **Alarms / errors**
   - On ALARM or error, streaming stops, queues clear, controls lock except Unlock/Home/ALL STOP. Console filters can show ALARMS/ERRORS quickly.
   - Clear with $X/$H, re-home if needed, and reload if appropriate.
9) **Settings and tuning**
   - Use GRBL Settings tab to refresh $$ (idle, not alarmed), edit values with numeric validation/ranges; pending edits highlight yellow until saved.
   - Raw $$ tab keeps the text capture.
10) **Macros**
    - Left-click to run; right-click to preview contents. Macros blocked during streaming/alarms; `%wait/%msg/%update` supported.

When to enable/disable options:
- **Training Wheels ON** for new users, shared machines, or risky setups; OFF for faster ops once comfortable.
- **Auto-reconnect ON** for field/production where USB drops happen; OFF in controlled labs to avoid unplanned reconnects.
- **3D View OFF** on low-end machines or huge files; ON for visual sanity checks.
- **Idle status mute** is fixed for console; toggle GUI logging if you don’t want button logs.

## Quick Start Workflow
1) Launch, select port (auto-selects last if enabled), Connect.
2) Wait for GRBL banner + first status (Ready/Idle).
3) Read G-code file; optional Clear to unload.
4) Run (Training Wheels may confirm). Pause/Resume/Stop as needed.
5) Clear alarms with Unlock ($X) or Home ($H).

## UI Tour
- **Top bar:** Port picker, Refresh, Connect/Disconnect, Read G-code, Clear G-code, Run/Pause/Resume/Stop, Unlock, unit toggle (mm/inch), Spindle ON/OFF.
- **Left panels:** MPos (Home/Unlock/Hold/Resume), WPos (Zero per-axis/All, Goto Zero), Jog pad (XY/Z, Jog Cancel, ALL STOP), step selectors, Feed/Spindle overrides.
- **Tabs:**
  - G-code viewer (highlights sent/acked/current; light colors).
  - Console (log + manual command entry; filters).
  - Raw $$ (captured settings dump).
  - GRBL Settings (editable table, tooltips, pending-change highlight).
  - App Settings (ALL STOP mode, estimation factor/fallback, keybindings, Training Wheels, auto-reconnect).
  - 3D View (toggle render, save/load view).
- **Status bar:** Progress, buffer fill, toggle buttons (tooltips/logging/3D/keybindings).

## Core Behaviors
- **Handshake:** Waits for GRBL banner or status + first status report before enabling controls/$$.
- **Training Wheels:** Confirms risky top-bar actions (connect/run/pause/resume/stop/spindle/clear/unlock) when enabled; debounced.
- **Auto-reconnect:** When not user-disconnected, retries last port with backoff; respects “Reconnect to last port on open”.
- **Alarms:** ALARM:x, “[MSG:Reset to continue]”, or status Alarm → stop/clear queues, lock controls except Unlock/Home/ALL STOP.
- **Idle noise:** `<Idle|...>` not logged to console (still processed).

## Jobs, Files, and Streaming
- **Read G-code:** Strips BOM/comments/% lines; chunked loading for large files. Read-only; Clear unloads.
- **Streaming:** Character-counting; uses `Bf:` to size window; stops on error/alarm; buffer fill shown.
- **Progress:** Sent/acked/current highlighting; status/progress bar; live estimate while running.

## Jogging & Units
- $J= incremental jogs (G91) with unit-aware G20/G21; jog cancel RT 0x85.
- Unit toggle button flips mm/inch and label; jogs blocked during streaming/alarm.

## Console & Manual Commands
- Manual send blocked while streaming; during alarm only $X/$H allowed.
- Filters: ALL / ERRORS / ALARMS. TX/RX logged (idle suppressed).

## GRBL Settings UI
- Refresh $$ (idle, not alarmed, after handshake). Table shows descriptions; edits inline with numeric validation/ranges; pending edits highlighted until saved. Raw $$ tab holds capture.

## Macros
- Files: Macro-1..7 or Maccro-1..7 (.txt optional). Line1 label, Line2 tooltip, Line3+ body.
- Left-click runs; Right-click previews (view-only). If streaming: blocked.
- Supports bCNC-style directives (%wait, %msg, %update), expressions, and commands.

## Estimation & 3D View
- Estimates bounds, feed time, rapid time (uses $110-112 or fallback) with factor slider; shows “fallback” when applicable. Live remaining estimate during streaming.
- 3D View: toggle render; shows rapid/feed/arc; live position; save/load/reset view.

## Keyboard Shortcuts
- Configurable (up to 3-key sequences); conflicts flagged; ignored while typing. Training Wheels confirmations still apply.

## Logs & Filters
- Console filters; idle muted; GUI button logging toggle; jog/ALL STOP hotkeys (Space/Enter defaults).

## Troubleshooting
- No ports: install driver, try another cable/port.
- Connect fails: verify port/baud 115200; close other apps.
- No $$: wait for ready/status; clear alarms; stop streaming.
- Alarm: use $X/$H; reset + re-home if needed.
- Streaming stops: check console for error/alarm; validate G-code for GRBL 1.1h.
- 3D slow: toggle 3D render off.

## FAQ
- **4-axis or grblHAL?** Not supported (3-axis GRBL 1.1h only).
- **Why $$ deferred?** Avoids startup interleaving; mirrors cncjs/gSender.
- **Why strict alarms?** Safety; matches ref senders.
- **Persistent offsets (G10)?** Swap zero commands if desired.

## Appendix A: GRBL 1.1h Commands
- Real-time (no newline): `Ctrl-X` (soft reset), `?` (status), `!` (hold), `~` (cycle start), `0x85` (jog cancel), Feed override `0x90/91/92`, Spindle override `0x99/9A/9B`.
- System: `$` (help), `$$` (settings), `$#` (coords), `$I` (build info), `$N` (startup lines), `$RST=*| $RST=$| $RST=#` (reset), `$X` (unlock), `$H` (home), `$J=...` (jog), `$C` (check mode), `$SLP` (sleep).
- Common G-code for sender UX: `G92` zeroing (default), `G90/G91` absolute/relative, `G20/G21` units, `M3/M4/M5` spindle, `G0/G1/G2/G3` motion, `G4` dwell.

## Appendix B: GRBL 1.1h Settings (selected)
- $0 Step pulse, µs
- $1 Step idle delay, ms
- $2 Step port invert mask
- $3 Direction port invert mask
- $4 Step enable invert
- $5 Limit pins invert
- $6 Probe pin invert
- $10 Status report mask
- $11 Junction deviation, mm
- $12 Arc tolerance, mm
- $13 Report inches (0/1)
- $20 Soft limits (0/1)
- $21 Hard limits (0/1)
- $22 Homing enable (0/1)
- $23 Homing dir invert mask
- $24 Homing feed, mm/min
- $25 Homing seek, mm/min
- $26 Homing debounce, ms
- $27 Homing pull-off, mm
- $30 Max spindle speed, RPM
- $31 Min spindle speed, RPM
- $32 Laser mode (0/1)
- $100/$101/$102 Steps/mm (X/Y/Z)
- $110/$111/$112 Max rate, mm/min (X/Y/Z)
- $120/$121/$122 Max accel, mm/sec² (X/Y/Z)
- $130/$131/$132 Max travel, mm (X/Y/Z)

Use the Settings tab to edit; pending edits highlight in yellow until sent. Numeric validation and broad ranges are enforced; adjust as needed for your machine. 
