# Simple Sender - Full Manual

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
- Character-count streaming with Bf-informed RX window; live buffer fill and TX throughput.
- Alarm-safe: locks controls except unlock/home; Training Wheels confirmations for critical actions.
- Handshake: waits for banner + first status before enabling controls/$$.
- Read-only file load (Read G-code), clear/unload button, inline status/progress.
- Resume From... dialog to continue a job with modal re-sync and safety warnings.
- Performance mode: batches console updates, suppresses per-line RX logs during streaming, adapts status polling by state.
- Overdrive tab: spindle control plus feed/spindle override sliders with nice sliding controls and +/-/reset shortcuts keep the console tidy while the live override summary mirrors GRBL's Ov* values and slider moves send the matching 10% real-time override bytes.
- Machine profiles for units + max rates; estimates prefer GRBL settings, then profile, then fallback.
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

Settings are stored in a per-user config folder (`%LOCALAPPDATA%\SimpleSender` on Windows or `$XDG_CONFIG_HOME/SimpleSender` on Linux). Override with `SIMPLE_SENDER_CONFIG_DIR`; if the directory cannot be created, the app falls back to `~/.simple_sender` or the app folder.

## Launching
```powershell
python main.py
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
   - Pick your COM port (auto-selects last if "Reconnect to last port on open" is enabled in App Settings).
   - Click Connect (Training Wheels may prompt). The app waits for the GRBL banner and first status before enabling controls and $$; this avoids startup races.
2) **Confirm machine readiness**
   - If the state is Alarm, use **Unlock ($X)** or **Home ($H)**. The top-bar Unlock is always available; it is safest to home if switches exist.
   - Use **Recover** to see alarm recovery steps and quick actions.
   - Verify limits/homing are configured in GRBL ($20/$21/$22) as needed.
   - Check DRO updates (MPos/WPos) to ensure status is flowing; idle status spam is muted in the console but still processed.
3) **Set units and jogging**
   - Use the unit toggle (mm/inch); jog commands insert the proper G20/G21.
   - Choose jog steps and test jogs with $J= moves; Jog Cancel (0x85) is available. Jogging is blocked during streaming/alarms.
4) **Load G-code**
   - Click **Read G-code**; file is read-only, comments/% lines stripped, chunked if large. Use **Clear G-code** to unload if needed.
   - Check the G-code viewer highlights and the 3D view (optional) for bounds sanity.
   - Review time/bounds estimates; if $110-112 are missing, set a fallback rapid rate or a Machine Profile in App Settings and adjust the estimate factor.
   - Use **Resume From...** to start at a specific line with modal re-sync if you need to continue a job.
 5) **App safety options**
    - Training Wheels ON: confirms critical actions (run/pause/resume/stop/spindle/clear/unlock/connect).
    - ALL STOP mode: choose soft reset only, or stop-stream + reset (safer mid-job).
    - Auto-reconnect: enable if you want recovery after USB blips; disable for lab environments where auto-reconnect is not desired.
    - Performance mode: reduces console churn during streaming and adapts status polling; toggle it from the Interface block inside App Settings.
 6) **Prepare the machine**
    - Home if required; set work offsets (Zero buttons use G92 by default). If you prefer persistent offsets, swap zeroing to G10 in code.
    - Position above stock; verify spindle control if using M3/M5 (or disable spindle in code for dry run).
    - Use the Overdrive tab to flip the spindle and fine-tune feed/spindle overrides via the slider controls plus +/-/reset shortcuts; each slider move nudges GRBL in 10% steps while the override summary mirrors the current Ov* values.
7) **Start and monitor**
   - Click **Run** (Training Wheels may prompt). Streaming uses character-counting flow control; buffer fill and TX throughput update as acks arrive.
   - Use **Pause/Resume** for feed hold/cycle start; **Stop/Reset** for soft reset; **ALL STOP** for immediate halt per your chosen mode.
8) **Alarms / errors**
   - On ALARM or error, streaming stops, queues clear, controls lock except Unlock/Home/ALL STOP. Use **Recover** to see a guided recovery panel.
   - Clear with $X/$H, re-home if needed, and resume or reload if appropriate.
9) **Settings and tuning**
   - Use GRBL Settings tab to refresh $$ (idle, not alarmed), edit values with numeric validation/ranges; pending edits highlight yellow until saved.
   - Raw $$ tab keeps the text capture.
10) **Macros**
    - Left-click to run; right-click to preview contents. Macros blocked during streaming/alarms; `%wait/%msg/%update` supported.
## Quick Start Workflow
1) Launch, select port (auto-selects last if enabled), Connect.
2) Wait for GRBL banner + first status (Ready/Idle).
3) Read G-code file; optional Clear to unload.
4) Run (Training Wheels may confirm). Pause/Resume/Stop as needed.
5) Clear alarms with Unlock ($X) or Home ($H).

## UI Tour
- **Top bar:** Port picker, Refresh, Connect/Disconnect, Read G-code, Clear G-code, Run/Pause/Resume/Stop, Resume From..., Unlock, Recover, unit toggle (mm/inch).
- **Left panels:** MPos (Home/Unlock/Hold/Resume), WPos (Zero per-axis/All, Goto Zero), Jog pad (XY/Z, Jog Cancel, ALL STOP), step selectors, Macro buttons (if Macro-1..Macro-7 files exist).
- **Tabs:**
  - G-code viewer (highlights sent/acked/current; light colors).
  - Console (log + manual command entry; filters).
  - Raw $$ (captured settings dump).
  - GRBL Settings (editable table, tooltips, pending-change highlight).
  - App Settings (banner showing `Simple Sender – Version: v0.1.0`, Theme picker for ttk styles, ALL STOP mode, estimation factor/fallback, status polling interval + disconnect threshold, error dialog settings with job completion dialog/beep toggles, jogging feed defaults, macro scripting toggle, keybindings, current-line highlight mode, 3D view quality, Training Wheels, auto-reconnect, machine profiles, and the Interface block for toggling Performance mode plus resume/recover button visibility—logging/error-dialog controls also live here).
  - Overdrive (Spindle ON/OFF plus feed/spindle override sliders with nice sliding controls, +/-/reset shortcuts, and a live override summary that follows GRBL's Ov* values while slider moves send the matching 10% real-time override bytes).
  - 3D View (Rapid/Feed/Arc toggles, rotate/pan/zoom, save/load/reset view).
- **Status bar:** Progress, buffer fill, TX throughput, status LEDs (Endstops/Probe/Hold), and the error-dialog status indicator (tooltips, 3D render, and keybinding toggles remain on the bar; logging/error-dialog controls moved into App Settings).
## Status Lights
- **Placement:** The LEDs sit inline with the status bar so they stay next to the logging/3D/keybinding toggles and provide a quick glance of machine triggers.
- **Meaning & data source:** GRBL 1.1h status reports include a `Pn:` token (e.g., `<Idle|Pn:XYZPDHRS|...>`). We mirror gSender's approach:
  - `X`, `Y`, `Z` light the **Endstops** indicator whenever those limit pins feed a high signal.
  - `P` (or `_macro_vars["PRB"]`) lights the **Probe** indicator, showing when a probe touch or macro-supplied probe result is active.
  - `H` or the textual **Hold** state lights the **Hold** LED while GRBL is paused/feed-hold.
- **How to use them:** Watch them before you jog to confirm no limits are stuck, rely on the Probe LED during probing macros, and note Hold when you issue `!`/`~`. They are purely informational; the rest of the UI still enforces streaming locks, alarms, and macro gating.
## Core Behaviors
- **Handshake:** Waits for GRBL banner or status + first status report before enabling controls/$$.
- **Training Wheels:** Confirms risky top-bar actions (connect/run/pause/resume/stop/spindle/clear/unlock) when enabled; debounced.
- **Auto-reconnect:** When not user-disconnected, retries last port with backoff; respects "Reconnect to last port on open".
- **Alarms:** ALARM:x, "[MSG:Reset to continue]", or status Alarm stop/clear queues, lock controls except Unlock/Home/ALL STOP; Recover button shows quick actions.
- **Performance mode:** Batches console updates, suppresses per-line RX logging during streaming, and adapts status polling by state.
- **Status polling:** Interval is configurable; consecutive status query failures trigger a disconnect.
- **Idle noise:** `<Idle|...>` not logged to console (still processed).
## Jobs, Files, and Streaming
- **Read G-code:** Strips BOM/comments/% lines; chunked loading for large files. Read-only; Clear unloads.
- **Streaming:** Character-counting; uses Bf feedback to size the RX window; stops on error/alarm; buffer fill and TX throughput shown.
- **Resume From...:** Resume at a line with modal re-sync (units, distance, plane, arc mode, feed mode, WCS, spindle/coolant, feed). Warns if G92 offsets are seen before the target line.
- **Progress:** Sent/acked/current highlighting (Processing highlights the line currently executing, i.e., the next line queued after the last ack; Sent shows the most recently queued line); status/progress bar; live estimate while running.
- **Completion alert:** When a job finishes streaming, a dialog summarizes the start/finish/elapsed wallclock so you know the file completed without monitoring the logs.
## Jogging & Units
- $J= incremental jogs (G91) with unit-aware G20/G21; jog cancel RT 0x85.
- Unit toggle button flips mm/inch and label; jogs blocked during streaming/alarm.

## Console & Manual Commands
- Manual send blocked while streaming; during alarm only $X/$H allowed.
- Filters: ALL / ERRORS / ALARMS plus a single Pos/Status toggle; when off those reports (and their carriage returns) are never written to the console, so you only see manual commands and errors unless you turn it back on.
- Performance mode batches console updates and suppresses per-line RX logs during streaming (alarms/errors still logged); toggle it from the App Settings Interface block.
## GRBL Settings UI
- Refresh $$ (idle, not alarmed, after handshake). Table shows descriptions; edits inline with numeric validation/ranges; pending edits highlighted until saved. Raw $$ tab holds capture.

## Macros
- **File format & placement.** Macros are discovered in `simple_sender/macros`, `macros/` next to `main.py`, or the folder containing `main.py`, using names `Macro-1` through `Macro-7`; legacy `Maccro-*` files and optional `.txt` extensions remain compatible. The first line is the button label, the second line is the tooltip, and every subsequent line is the body that executes when you left-click the macro button. Right-click opens a modal preview so you can inspect the contents without running them. Macros are blocked while streaming or when the controller is in an alarm state.
- **Macro scripting toggle.** In App Settings > Macros, disable scripting to allow only plain G-code lines (no `%` directives, `_` Python lines, or `[...]` expressions).
- **Why bCNC macros inspired this section.** The macro subsystem mirrors the flexibility of bCNC's macros: you can interleave GRBL commands, real-time bytes, directives like `%wait`, expressions, and Python snippets. Like bCNC, the sender maintains `_macro_vars`, emulates `$J=` jog semantics, and exposes helper macros (print, prompt, etc.) so that you can stitch together familiar motion flows from a single file without building a separate script.
- **Supported directives & commands.** The macro interpreter blends GRBL motion with helper directives:
  - `%wait`, `%msg`, and `%update` behave like their bCNC counterparts: pause until idle, log operator-facing text, or request a status update.
  - `%if running` skips the current line when a job is already in progress.
  - Control keywords (`M0/M00/PROMPT`, `ABSOLUTE/ABS`, `RELATIVE/REL`, `HOME`, `UNLOCK`, `RESET`, `PAUSE`, `RESUME`, `FEEDHOLD`, `STOP`, `RUN`, `SAFE`, `SET0/SETX/SETY/SETZ/SET`, `LOAD <path>`, `OPEN`, `CLOSE`, `HELP`, `QUIT/EXIT`, `SENDHEX`, and more) invoke the sender's helpers, so you can open/close the machine, toggle offsets, or trigger custom logic without writing raw G-code.
  - Prefixing a line with `!`, `~`, `?`, or the Ctrl-X byte (`\x18`) sends the equivalent real-time command; lines starting with `$`, `@`, `{`, comments in `(...)` or `;...`, or those matching the `MACRO_GPAT` regex are forwarded verbatim.
  - Pure G-code lines (e.g., `G0`, `G1`, `M3`, `M5`, `G92`, and any other GRBL commands) are sent directly to the controller.
  - **Prompt customization & state tracking.** `M0`, `M00`, and `PROMPT` lines present the modal built in `_show_macro_prompt`. Text can come from a trailing comment (e.g., `M0 (What's next?)`), from tokens such as `title=`, `msg=`/`message=`/`text=`, and `buttons=` (comma- or pipe-separated), or from the newer bracket syntax like `[title(My Title)] Pick an option… [btn(Choice 1)a] [btn(Choice 2)b]`. Custom buttons replace the default Resume button and remain available alongside Cancel, and you can hide Resume with `noresume` or rename the default buttons with `resume=`/`resumelabel=` and `cancel=`/`cancellabel=`. When the user picks a choice, the macro stores it in `_macro_vars["prompt_choice"]`, `_macro_vars["prompt_choice_label"]`, `_macro_vars["prompt_choice_key"]`, `_macro_vars["prompt_index"]`, and `_macro_vars["prompt_cancelled"]`, which you can also read inside macros via `macro.prompt_choice*` before/after subsequent prompts.
  - **Compile errors now surface immediately.** If the macro parser hits invalid syntax (for example malformed bracket metadata), the runner pops up a dialog with the file name, line number, and offending text so you know exactly which line failed instead of only seeing a console log.
  - **Blocking & alarm safety.** Every macro-issued GRBL command now waits for completion before the next line executes, preventing prompt transitions, `%wait`, or reprobes from racing ahead of motion. If GRBL reports an alarm mid-macro, the executor immediately cancels the rest of the macro, logs which line was running, and waits for you to clear the alarm before retrying.
  - **Logging, threading, and safety.** Macros run on a background worker (`_run_macro_worker`), log their name/tip/contents when GUI logging is enabled, and respect the streaming/alarm gate and Training Wheels confirmations so they only run when it is safe. `_macro_lock` serializes macro execution, is always released (even on exceptions), and the runner flips `_macro_vars["running"]` so `%wait`/`_macro_wait_for_idle()` stream updates know when to block or resume.
- **Mixing Python & GRBL.** Lines that begin with `_` run as Python (`_safe_height = ...`). You can reference live variables in `_macro_vars` (e.g., `wx`, `wy`, `wz`, `OvFeed`, `safe`), call the UI via `app`/`os`, or log with `app._log(...)`. Wrap Python expressions in square brackets (`G0 Z[_safe_height]`), and the expression is evaluated before the line is streamed. Use `%msg`/`%update` inside macros for progress updates or for prompting the operator mid-sequence.
- **Example macro (raise Z and park).**
  ```text
  Park & lift
  Raise to a safe height, then move to X0 Y0.
  _safe_height = max(5.0, float(_macro_vars.get("safe", 5.0)))
  %msg Raising to Z[_safe_height] before parking.
  G90
  G0 Z[_safe_height]
  G0 X0 Y0
  %msg Parked and ready.
  ```
  This macro illustrates how to:
  1. Use a Python helper to compute `_safe_height` (leveraging the pre-populated `_macro_vars` dictionary).
  2. Emit `%msg` notifications before and after motion.
  3. Mix G-code with embedded expressions (`[...]`), send absolute moves (`G90`/`G0`), and park at a known location.
  You can expand this template with `%wait`, conditional Python (`if _safe_height < 10.0: ...`), or macros that interact with `app` (e.g., `app._log(...)`) before/after sending commands. Macros always release `_macro_lock`, so even a raised exception won't hang the UI.
- **Variable math + GRBL example.**
  ```text
  Offset jog
  Compute a dynamic X offset and move there with mixed Python/math.
  _step = float(_macro_vars.get("stepz", 1.0))
  _target = float(_macro_vars.get("wx", 0.0)) + _step * 3
  %msg Moving to computed X[_target] (wx=[_macro_vars.get("wx",0.0)], step=[_step]).
  G90
  G0 X[_target] Y0
  ```
  This illustrates setting a variable, performing math against live data, and passing that result straight into a GRBL move (`G0 X[_target]`). Use `_macro_vars["wx"]`, `wx`, or any custom variables paired with macros that update them (`%update`, status parsing, or previous lines) to build macros that adapt to the current machine state.

- **Available machine information.** When macros run they can read (and modify) `_macro_vars`. The list below shows the values collected by the sender so you know what data is available for conditional logic, math, or logging:
   | Variable | Meaning |
   | --- | --- |
   | `wx`, `wy`, `wz` | Most recent work position from GRBL (WPos). |
   | `mx`, `my`, `mz` | Most recent machine position (MPos). |
   | `wcox`, `wcoy`, `wcoz` | Work coordinate offsets (WCO). |
   | `wa`/`wb`/`wc` | Auxiliary axis positions when available (also mirrored as `_macro_vars`). |
   | `prbx`/`prby`/`prbz`/`prbcmd`/`prbfeed` | Probe-related placeholders (mirrors typical bCNC macros). |
   | `curfeed`, `curspindle` | Live feed/speed values from the status report (`FS` field). |
   | `rpm` | Current spindle RPM estimate. |
   | `planner`, `rxbytes` | Planner buffer usage and remaining RX bytes from `Bf:`.
   | `OvFeed`, `OvRapid`, `OvSpindle` (plus `_Ov*` mirror flags) | Override percentages for feed/rapid/spindle (set by override buttons). |
   | `state` | Latest GRBL state string (Idle, Run, Hold, Alarm, etc.). |
   | `_macro_vars["running"]` | Boolean flag that flips when a stream is running/paused/done. |
   | `motion`, `distance`, `plane`, `feedmode`, `arc`, `units`, `WCS`, `cutter`, `tool`, `program`, `spindle`, `coolant` | Internal state placeholders that mirror the current G-code tokens processed by macros (same keys bCNC uses). |
   | `diameter`, `cutfeed`, `cutfeedz`, `safe`, `stepz`, `stepover`, `surface`, `thickness` | User-defined helper numbers that can be tweaked in macros for tooling choices. |
   | `PRB`, `version`, `controller`, `pins`, `msg`, `prompt_choice`/`prompt_index`/`prompt_cancelled` | Misc helpers used by macros and log dialogs; `PRB` holds last probe result, `pins` stores pin summary, and `prompt_*` track modal prompt outcomes.
   | `_camwx`, `_camwy` | Camera or CAM coordinates that can be reused in macros (mirrors non-GRBL data). |

  Use this table as your cheat sheet when composing macros - every variable above can be referenced directly inside `[ ... ]` expressions or Python lines to make decisions, guard moves, or report helpful messages.


## Estimation & 3D View
- Estimates bounds, feed time, rapid time (uses $110-112, then machine profile, then fallback) with factor slider; shows "fallback" or "profile" when applicable. Live remaining estimate during streaming.
- 3D View: Rapid/Feed/Arc legend toggles, rotate/pan/zoom, live position marker, save/load/reset view; quality controls (draw limits, arc detail, lightweight preview) live in App Settings.
## Keyboard Shortcuts
- Configurable (up to 3-key sequences); conflicts flagged; ignored while typing; toggle from App Settings or the status bar. Training Wheels confirmations still apply.

## Logs & Filters
- Console filters cover ALL/ERRORS/ALARMS plus the combined Pos/Status switch that omits those reports entirely when disabled; idle status spam stays muted. GUI button logging toggle remains, and performance mode (toggled from App Settings > Interface) batches console output and suppresses RX logs while streaming; jog/ALL STOP hotkeys (Space/Enter defaults).
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

## License
GPL-3.0-or-later © 2026 Bob Kolbasowski

## Appendix A: GRBL 1.1h Commands
The sender exposes a curated subset of GRBL's real-time, system, and motion commands. Below is the syntax and an example for each group.

### Real-time bytes (no newline)
| Command | Syntax | Notes / Example |
| --- | --- | --- |
| Soft reset | `Ctrl-X` | Immediately halts motion and resets GRBL. Example: used by **Stop/Reset** and ALL STOP (reset mode). |
| Status report | `?` | Requests `<State|WPos|FS:...>` update (used by tooltips/estimations). |
| Feed hold | `!` | Pauses execution (used for **Pause**). |
| Cycle start / resume | `~` | Resumes execution after hold or start a job (used for **Resume**/**Run**). |
| Jog cancel | `0x85` | Stops a `$J=` jog (bound to **JOG STOP**). |
| Feed override +10%/-10%/reset | `0x91` / `0x92` / `0x90` | Matches the buttons in the Feed Override panel. |
| Spindle override +10%/-10%/reset | `0x9A` / `0x9B` / `0x99` | Tied to the Spindle Override controls. |

### System (`$`) commands
| Command | Syntax | Example |
| --- | --- | --- |
| Help / info | `$` | Prints current build/config hints in the console. |
| Print settings | `$$` | Captures settings table (refresh button). |
| Coordinate report | `$#` | Shows offsets and workspace coordinates (rarely used). |
| Build info | `$I` | Logs firmware version and capabilities. |
| Startup lines | `$N` | Lists GRBL startup macro lines. |
| Reset settings | `$RST=*`, `$RST=$`, `$RST=#` | Soft resets stored settings/config. |
| Unlock | `$X` | Clears alarms; exposed via **Unlock** buttons. |
| Home | `$H` | Runs the homing cycle from the UI. |
| Jog command | `$J=...` | Used by the jog pad/button macros; syntax `G91 X... Y... Z... F...`. |
| Check mode | `$C` | Only available when GRBL is idle; reports planner buffer. |
| Sleep | `$SLP` | Puts GRBL into low-power mode (not exposed by default). |

### Common G-code commands used via UI / macros
| Command | Syntax | Example | Notes |
| --- | --- | --- | --- |
| Absolute positioning | `G90` | `G90` before a `G0 X10` move | Ensures subsequent moves use machine coordinates. |
| Relative positioning | `G91` | `G91` before `$J=` jog | Temporarily switches to incremental mode. |
| Units | `G20` or `G21` | `G21` when working in millimeters | The unit toggle sends the proper command automatically. |
| Zero work coords | `G92` | `G92 X0 Y0 Z0` (zero all buttons) | Sender uses this for DRO zero buttons; macros can adjust `G92` arguments. |
| Motion | `G0`, `G1`, `G2`, `G3` | `G0 Z10` or `G2 X1 Y1 I0 J1` | Standard rapid/linear/arc commands used in macros. |
| Dwell | `G4` | `G4 P1` | Macro `%wait` uses similar concepts (but there is also the `%wait` directive). |
| Spindle on/off | `M3 S<rpm>` / `M5` | `M3 S12000` (button default) / `M5` | Spindle buttons log these commands via `attach_log_gcode`. |

Use the console or macros whenever you need a command that is not exposed via buttons - every `G` current GRBL command can be typed manually. The tables above capture the commands that the UI, macros, and override controls leverage most heavily.

## Appendix C: Macro Reference

| Macro | Purpose | When to use | When to avoid | Code notes |
| --- | --- | --- | --- | --- |
| Macro-1: Return to Work X & Y Zero (Safe Height) | Raises to a safe Z before moving to X0 Y0 so you can reset work coordinates without crashing. | When you need a quick safe return to the origin before setting offsets. | Avoid while actively executing a job; use only when motion is paused. | Defines `%macro.state.SAFE_HEIGHT` and uses `M5`, `G53 G0`, and `%wait` so moves execute away from the workpiece. |
| Macro-2: Park over Tool Sensor | Drives to the fixed probe location so you can clean the sensor or stage fixtures. | Park the spindle for maintenance or measurement after a job finishes. | Don’t use while cutting or while the spindle is still rotating. | Stores `PROBE_X/Y_LOCATION` in `%macro.state` and issues `G53 G0` so the coordinates ignore any applied offsets. |
| Macro-3: Reference Tool Recovery | Re-probes the reference tool when the stored `TOOL_REFERENCE` is missing, then saves the result. | Run after a crash or failed recovery to restore the reference measurement. | Skip if `macro.state.TOOL_REFERENCE` already exists; the macro exits early if the reference is present. | Guards with `%if getattr(macro.state, "TOOL_REFERENCE", None) is not None` and logs the recovered value with `%msg`. |
| Macro-4: XYZ Touch Plate & Reference Tool Setup | Probes the touch plate (Z/X/Y) and captures the reference tool height for later restorations. | Use during initial calibration or whenever the touch plate setup changes. | Don’t run during a production cycle; the routine assumes the tool is free to move to the plate. | Mixes fast and slow probing commands with `G92` offsets before updating `%macro.state.TOOL_REFERENCE`. |
| Macro-5: Tool Change (Preserve Reference Tool Height) | Moves to the sensor, prompts for a tool swap, re-probes, and applies the stored reference height again. | Run every time you need to change tools without losing reference height. | Avoid if the reference tool hasn’t been captured yet; the macro prompts you to run Macro-1/2/4 first. | Uses `%msg` + `PROMPT` to warn about missing references, then sends `G10 L20 Z[macro.state.TOOL_REFERENCE]` after probing to keep offsets consistent. |
| Macro-6: Z Touch Plate & Reference Tool Setup | Sets X/Y manually, runs a Z touch-plate probe, and restores the reference tool height. | Useful when job-specific positions require manual X/Y placement before probing Z. | Don’t run if you expect the machine to maintain `wx/wy` automatically; it stores the start position in `%macro.state.START_X/Y`. | Captures `wx`/`wy`, probes Z with `G38.2`, stores `%macro.state.TOOL_REFERENCE`, and logs the new height. |
| Macro-7: Prompt Test Macro | Validates the new modal/dialog helpers (default Resume/Cancel, `[btn(...)]` choices, and follow-up confirmations). | Run when you want to test the prompt UX or document how custom buttons behave. | Not for production motion; it’s purely a UI verification script. | Demonstrates `[title(...)]`, `[btn(...)]`, and reads `macro.prompt_choice_key`/`macro.prompt_choice_label` in follow-up `%msg`/`PROMPT` lines. |

## Appendix B: GRBL 1.1h Settings (selected)
- $0 Step pulse, us
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
- $120/$121/$122 Max accel, mm/sec^2 (X/Y/Z)
- $130/$131/$132 Max travel, mm (X/Y/Z)

Use the Settings tab to edit; pending edits highlight in yellow until sent. Numeric validation and broad ranges are enforced; adjust as needed for your machine. 
