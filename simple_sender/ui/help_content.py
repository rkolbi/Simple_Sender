#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from simple_sender.utils.grbl_errors import GRBL_ALARM_CODES, GRBL_ERROR_CODES

HELP_ABOUT_TITLE = "Simple Sender About"


@dataclass(frozen=True)
class HelpSubsection:
    title: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    code_blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpSection:
    title: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    code_blocks: tuple[str, ...] = ()
    subsections: tuple[HelpSubsection, ...] = ()


def _sub(
    title: str,
    *,
    paragraphs: tuple[str, ...] = (),
    bullets: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    code_blocks: tuple[str, ...] = (),
) -> HelpSubsection:
    return HelpSubsection(
        title=title,
        paragraphs=paragraphs,
        bullets=bullets,
        notes=notes,
        code_blocks=code_blocks,
    )


def _sec(
    title: str,
    *,
    paragraphs: tuple[str, ...] = (),
    bullets: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    code_blocks: tuple[str, ...] = (),
    subsections: tuple[HelpSubsection, ...] = (),
) -> HelpSection:
    return HelpSection(
        title=title,
        paragraphs=paragraphs,
        bullets=bullets,
        notes=notes,
        code_blocks=code_blocks,
        subsections=subsections,
    )


_GRBL_STATE_MEANINGS: tuple[tuple[str, str], ...] = (
    ("Idle", "Machine is ready and not moving. This is the normal safe state for settings refresh, probing setup, and job start."),
    ("Run", "A job or commanded motion is actively running."),
    ("Hold", "Feed hold is active and motion is paused. Resume uses ~ when it is safe to continue."),
    ("Jog", "A jog move is active. Some commands are locked out until the jog is canceled or completed."),
    ("Alarm", "GRBL latched a safety or motion alarm. Clear the cause first, then Unlock ($X) or Home ($H) as appropriate."),
    ("Door", "A safety door state is active. Motion and command acceptance are restricted until the controller returns to a safe state."),
    ("Check", "Check mode is active. GRBL validates moves without executing real machine motion."),
    ("Home", "A homing cycle is in progress."),
    ("Sleep", "GRBL is in low-power sleep mode and needs the normal wake or reset workflow before use."),
    ("Disconnected", "Simple Sender is not currently connected to a controller. This is an app state rather than a GRBL firmware state."),
)


def _format_code_bullets(prefix: str, mapping: dict[int, str]) -> tuple[str, ...]:
    return tuple(f"{prefix}:{int(code)} - {str(description)}" for code, description in sorted(mapping.items()))


def _grbl_reference_subsections() -> tuple[HelpSubsection, ...]:
    return (
        _sub(
            "GRBL Real-Time Commands",
            bullets=(
                "Ctrl-X: soft reset. Immediately halts motion and resets GRBL. Used by Stop/Reset and ALL STOP reset modes.",
                "?: status report request. Used for live status polling, diagnostics, and the %update macro directive.",
                "!: feed hold. Pauses execution and is used by Pause and related hold paths.",
                "~: cycle start or resume. Used by Resume and by run paths that continue motion.",
                "0x85: jog cancel. Used by JOG STOP and by hold-jog release and fail-safe cancellation logic.",
                "0x91 / 0x92 / 0x90: feed override plus 10, minus 10, and reset.",
                "0x9A / 0x9B / 0x99: spindle override plus 10, minus 10, and reset.",
            ),
            notes=(
                "Simple Sender reports disconnected or unsent realtime actions truthfully instead of silently acting like the controller accepted them.",
            ),
        ),
        _sub(
            "GRBL System Commands",
            bullets=(
                "$: print GRBL help or build/configuration hints in the console.",
                "$$: print or refresh the GRBL settings table.",
                "$#: coordinate report.",
                "$I: build information.",
                "$N: startup lines.",
                "$RST=*, $RST=$, $RST=#: reset stored settings or related GRBL configuration groups.",
                "$X: unlock and clear alarms.",
                "$H: home the machine.",
                "$J=...: jog command syntax used by the jog controls and related macro helpers.",
                "$C: check mode. Available only while idle.",
                "$SLP: sleep command. Not exposed by default in the UI.",
            ),
        ),
        _sub(
            "Common G-Code Used in Simple Sender",
            bullets=(
                "G90: absolute positioning.",
                "G91: relative positioning.",
                "G20 and G21: inch and millimeter units.",
                "G92 and G10 L20: temporary or persistent work zeroing.",
                "G0, G1, G2, and G3: rapid, linear, and arc motion.",
                "G4: dwell.",
                "G38.2: probing move, commonly used for touch-plate and probe workflows.",
                "M0 and M1: pauses that can stop the stream after the line is acknowledged.",
                "M3 S<rpm> and M5: spindle on and spindle off.",
            ),
            notes=(
                "If you need a GRBL command that is not exposed by a button, you can type it in the console or use it inside a macro as long as it fits the sender's manual-command safety rules.",
            ),
        ),
        _sub(
            "GRBL State Meanings",
            bullets=tuple(f"{name}: {meaning}" for name, meaning in _GRBL_STATE_MEANINGS),
        ),
        _sub(
            "GRBL Error Codes",
            bullets=_format_code_bullets("error", GRBL_ERROR_CODES),
        ),
        _sub(
            "GRBL Alarm Codes",
            bullets=_format_code_bullets("ALARM", GRBL_ALARM_CODES),
        ),
    )


HELP_ABOUT_SECTIONS: tuple[HelpSection, ...] = (
    _sec(
        "About Simple Sender",
        paragraphs=(
            "Simple Sender is a practical GRBL 1.1h sender built for everyday CNC work. It aims to stay clear, dependable, and responsive so the operator can focus on setup, motion, and cutting instead of fighting the software.",
            "The interface is designed for touch use at the machine and runs well on affordable hardware, including Raspberry Pi 4 class systems. It is intentionally focused on real production and setup tasks for 3-axis GRBL machines.",
        ),
        bullets=(
            "Designed for touchscreen use and readable at the machine.",
            "Streams very large G-code files reliably with a lean runtime model.",
            "Can manage Kasa-connected accessories such as a vacuum or spindle light on Linux.",
            "Lets you view and change GRBL settings directly in the UI.",
            "Uses tooltips to explain settings and controls throughout the app.",
            "Supports touch-plate and probe settings in the UI, normally open bit setters, and a multi-check probing approach for more consistent measurements.",
            "Supports multi-tool work with tool reference capture, offset handling, and guided tool changes that resume the paused job.",
            "Includes Vectric post processors in inch and millimeter versions.",
            "Supports keyboard shortcuts, joysticks, and gamepads.",
            "Includes Dry Run, macros, a built-in Macro Manager, a spoilboard surfacing tool, and setup safeguards.",
        ),
        notes=(
            "Simple Sender is not a safety system. Keep a physical e-stop or machine power cutoff within reach and always test new jobs in the air with the spindle off before cutting material.",
        ),
    ),
    _sec(
        "Getting Started",
        paragraphs=(
            "A typical startup flow is: choose the serial port, connect, wait for the GRBL banner and first status report, clear alarms if needed, home if your machine uses homing, jog carefully to confirm motion, load a file, review Job Info, complete Job Setup, and then run only when the machine state is fully understood.",
        ),
        subsections=(
            _sub(
                "Safety Basics",
                bullets=(
                    "Test in the air with the spindle off before cutting material.",
                    "Configure homing and limits on the controller as needed for your machine.",
                    "Keep an e-stop or power cutoff reachable.",
                    "ALL STOP behavior is configurable and should be understood before you rely on it.",
                    "Only run macro files you trust.",
                ),
            ),
            _sub(
                "Connect and Handshake",
                bullets=(
                    "Pick the serial port and click Connect. If Reconnect to last port on open is enabled, the app can remember and reopen the last port automatically.",
                    "Connect and Disconnect go through a short pending state so repeated clicks do not start overlapping workers.",
                    "The app waits for the GRBL banner and the first valid status report before enabling the main controls and GRBL settings refresh paths.",
                    "If the machine starts in Alarm, clear it with Unlock ($X) or Home ($H) as appropriate.",
                ),
                notes=(
                    "If Connect fails, verify the port, baud rate, USB cable, controller power, and that another sender is not already holding the port open.",
                ),
            ),
            _sub(
                "Read Job and Job Info",
                bullets=(
                    "Use Read Job to open the shared operating-system file picker and load a G-code file through the normal file pipeline.",
                    "On Linux, the file dialog temporarily applies the larger of the current UI scale and Linux File Dialog Scale, uses the current dialog theme, and enforces a readable minimum size so the chooser stays usable on Pi/Openbox touchscreen setups.",
                    "Linux dialogs default to /root/CNC_Jobs unless App Settings > Theme > Linux File Dialog Default Path is set to a different valid folder.",
                    "Loaded jobs stay read-only. The sender strips BOM markers, comments, and bare % lines, then compacts or splits supported lines to respect GRBL's 80-byte limit.",
                    "Job Info is a large read-only popup that shows SSMETA metadata when present, plus file size, line counters, estimate, dimensions, and separate Toolpaths and Tools lists when the metadata provides them.",
                    "If load metadata is already available, the first Job Info popup open renders it immediately without requiring a reopen or reload.",
                ),
            ),
            _sub(
                "Quick Start Workflow",
                bullets=(
                    "Launch the sender, select the port, and Connect.",
                    "Wait for the GRBL banner and the first status report so the machine is truly ready.",
                    "Read Job to load the file, then review the job dimensions and estimate.",
                    "Run the built-in Job Setup workflow and confirm the Tool reference label is populated for the session.",
                    "Press Run. If setup state is missing or invalid, rerun Job Setup or intentionally choose Start Anyway only if you accept the risk.",
                    "If Dry Run is enabled, choose whether to continue in Dry Run, switch to a normal run, or cancel before the stream starts.",
                    "If alarms appear, clear them with Unlock ($X) or Home ($H), then re-home or re-setup as needed.",
                ),
            ),
            _sub(
                "Update Safety and Shop File Transfer",
                paragraphs=(
                    "Do not sync, overwrite, or partially update a live running Simple Sender install. Close the application first, or reboot and then update the runtime files. The duplicate-instance/runtime marker is a safety check, not a hot-update workflow.",
                    "If you run Simple Sender on a Raspberry Pi, a Samba share can make file transfer easier so the Windows CAM computer can save directly to the machine over the network. This is optional but fits the intended Pi-based shop workflow well.",
                ),
                bullets=(
                    "If you use a convenience-first Samba share on a protected shop network, save directly from Vectric or another CAM tool to the machine instead of moving files with USB media.",
                    "If you rely on guest or root-based Samba settings for convenience, remember that this is less secure than a properly locked-down network share.",
                    "If Windows browsing is unreliable, connecting by the Pi's IP address is often the quickest workaround.",
                ),
            ),
        ),
    ),
    _sec(
        "Running a Job",
        paragraphs=(
            "Simple Sender keeps the run path lean, but it still exposes the state you need to decide whether a job is safe to start. Review bounds, dimensions, tool setup, and machine state before pressing Run.",
        ),
        subsections=(
            _sub(
                "Operation Walkthrough",
                bullets=(
                    "Connect and wait for the GRBL banner and first status before assuming the machine is ready.",
                    "If the controller is in Alarm, use Unlock ($X) or Home ($H), then verify limits and homing settings such as $20, $21, and $22 as needed.",
                    "Set units and jogging carefully. The unit toggle inserts G20 or G21 as needed, and jogging is blocked during streaming and alarms.",
                    "Read Job, review the dimensions and estimate, and optionally run the Preflight check from App Settings > Diagnostics.",
                    "Choose your safety options: Training Wheels confirmations, ALL STOP mode, auto-reconnect behavior, and Performance mode.",
                    "Home if required, set work zero, and complete Job Setup so the tool reference state is valid for this session.",
                    "For a dry run, enable Dry run: strip spindle/coolant/M6/S/T from streamed G-code in App Settings > Safety before pressing Run.",
                    "Press Run. If no valid tool reference is stored for the session, the app shows Job Setup Not Completed with Start Anyway and Cancel.",
                    "If Dry Run is enabled, Run opens the explicit Dry Run confirmation first so you can continue in Dry Run, switch back to normal cutting, or cancel.",
                    "Use Pause and Resume for feed hold and cycle start, Stop/Reset for the configured reset path, and ALL STOP for the fastest software stop path.",
                ),
            ),
            _sub(
                "Status Lights",
                bullets=(
                    "Endstops, Probe, and Hold LEDs live in the status bar so they stay visible next to the quick buttons.",
                    "The indicators are driven directly from GRBL status reports. X, Y, or Z in Pn: lights Endstops; P or the PRB macro result lights Probe; H or a Hold state lights the Hold LED.",
                    "Use them as quick machine-state confirmation before jogging or probing. They are informational and do not override the rest of the sender's safety gating.",
                ),
            ),
            _sub(
                "Core Behaviors",
                bullets=(
                    "Handshake waits for the banner or status plus the first status report before enabling controls and GRBL settings refresh.",
                    "Training Wheels can confirm connect, run, pause, resume, stop, spindle, clear, and unlock actions.",
                    "Auto-reconnect can retry the last port after an unexpected disconnect and can also reconnect on startup if enabled.",
                    "ALARM:x, Reset to continue messages, or an Alarm state stop and clear the sender queues and lock controls except Unlock, Home, and ALL STOP.",
                    "Optional GRBL alarm or error popups show code definitions and are deduped by the configured interval.",
                    "Performance mode batches console updates and suppresses per-line RX logging during streaming.",
                    "Status-path smoothing coalesces some UI updates during pressure so final progress and positions stay correct without spiking Tk activity.",
                    "Preflight evaluation is available through Diagnostics and uses the shared preflight service.",
                    "Status polling interval is configurable, and repeated failures can trigger a disconnect.",
                    "Idle noise is not written into the console, but it is still processed by the runtime.",
                    "Worker threads post UI work through the app's UI queue helpers instead of touching Tk widgets directly.",
                    "Manual and immediate commands use a bounded queue. If it fills, new commands are dropped and the UI reports the cumulative dropped count.",
                    "When Dry Run is enabled, job start requires an explicit operator choice before stream side effects begin.",
                ),
            ),
            _sub(
                "Jobs, Files, and Streaming",
                bullets=(
                    "Simple Sender uses a file-backed load and stream path so jobs of any size can use the same general runtime model.",
                    "The quick assessment scans the job header for SSMETA key=value metadata and uses that metadata for dimensions and units when it is complete enough to be trusted.",
                    "Diagnostics and runtime metrics record whether dimensions and units came from SSMETA or from a file scan.",
                    "Starting a new Read Job cancels the previous loader so stale background work does not overwrite the current results.",
                    "Streaming uses character-counting flow control, Bf feedback for the RX window, and stops on errors or alarms.",
                    "Each outbound line counts its trailing newline for buffer accounting, and non-ASCII or over-80-byte lines are rejected.",
                    "Exact trimmed VACUUM_ON and VACUUM_OFF lines are intercepted by the sender and never sent to GRBL.",
                    "Lines that start with TC: are intercepted and routed through the built-in Tool Change workflow, then the paused stream resumes when the operator finishes the tool change.",
                    "System commands that start with $ are rejected in job files. Use the UI or a macro for those instead.",
                    "Run progress is byte-offset based and final completion waits for GRBL to reach Idle after the last acknowledged line.",
                    "During deferred completion, macros, probing entry points, and GRBL settings refresh remain blocked until the final Idle arrives.",
                ),
            ),
            _sub(
                "Line Length Limitations and CAM Guidance",
                bullets=(
                    "Automatic line splitting is limited to linear G0 or G1 moves in G94 that use X, Y, and Z axes. Arcs, inverse-time moves, and unsupported axes must already fit within the limit.",
                    "If a line stays over 80 bytes after compaction and cannot be split safely, the load is rejected.",
                    "Recommended CAM habits: disable line numbers if possible, reduce coordinate decimal places to a practical range, and avoid long inline comments or long tool names inside motion lines.",
                    "If your CAM insists on long arc lines, consider arc-to-line approximation or a different post setting so the output stays GRBL-friendly.",
                ),
            ),
            _sub(
                "Jogging and Units",
                bullets=(
                    "Jogging uses $J= incremental moves with the correct G20 or G21 units.",
                    "Hold-jog bindings send one long jog command per press and then issue jog cancel plus queue cleanup on release.",
                    "A hold-jog deadman timeout cancels motion if joystick polling stalls.",
                    "If GRBL still reports jog state after cancel, the sender issues an additional jog-cancel fallback automatically.",
                    "Hold-jog distance targets remaining travel when max travel and machine position are known; otherwise the sender uses a conservative long move that is still canceled on release.",
                    "If joystick communication drops during a hold jog, the sender cancels the jog immediately.",
                    "The unit toggle button flips the active unit mode and label. Jogging is blocked during streaming and alarms.",
                    "Safe mode in App Settings > Jogging applies conservative jog feeds and steps for first-time setup.",
                ),
            ),
            _sub(
                "Console and Manual Commands",
                bullets=(
                    "Manual send is blocked while streaming. During alarms, only $X and $H are allowed.",
                    "Manual commands longer than 80 bytes or containing non-ASCII characters are rejected.",
                    "The manual command queue is bounded, so repeated command spam can overflow it. When that happens, the status bar shows the dropped count.",
                    "Console filters include ALL, ERRORS, ALARMS, and a combined Pos/Status toggle.",
                    "When Pos/Status is off, those reports and their carriage-return behavior are omitted from the live console and from saved console exports.",
                    "Performance mode reduces console churn and suppresses per-line RX logs during streaming while still keeping alarms and errors visible.",
                    "Manual command errors update the status bar with a source label and do not alter the stream state.",
                    "Streaming errors report the file name, line number, and line text in the error status.",
                    "Program pauses such as M0, M1, and tool changes such as M6 pause the stream after the line is acknowledged.",
                    "Console Save pre-fills a timestamped filename so exporting logs is touch-friendly.",
                ),
            ),
            _sub(
                "GRBL Settings UI",
                bullets=(
                    "If the post-connect $$ snapshot is already available, the first GRBL Settings popup open uses that cached data immediately.",
                    "Refresh $$ is available when the machine is idle, not alarmed, and fully through the handshake.",
                    "Refresh $$ requests a newer controller snapshot; it is not required for the first usable display when cached data already exists.",
                    "The GRBL Settings popup is scrollable, shows descriptions and units, supports inline numeric validation, and highlights pending edits until you save them.",
                    "If enabled, the optional Raw $$ page opens the last raw settings dump in the same popup.",
                ),
            ),
        ),
    ),
    _sec(
        "Tool Changes and Probing",
        paragraphs=(
            "Simple Sender is built to make multi-tool work easier and more truthful. The protected workflow buttons stay fixed at the beginning of the workflow row so the main setup and tool-change actions are always easy to find.",
        ),
        subsections=(
            _sub(
                "Protected Workflow Shortcuts",
                bullets=(
                    "Home runs the protected homing action.",
                    "Park at Bit Setter moves to the configured fixed-sensor location for cleaning, inspection, or staging.",
                    "Job Setup guides the operator through XYZ Plate, Z Plate, or Manual setup and then captures a valid reference tool height.",
                    "Tool Change uses the stored tool reference, re-probes the new tool, reapplies the reference logic, and then parks at safe Z over WCS X0 Y0.",
                    "Park at Work raises to a configured safe machine Z and returns to work X0 Y0 without changing offsets.",
                ),
            ),
            _sub(
                "Probing Workflow",
                bullets=(
                    "Home and clear alarms first so probing commands are accepted.",
                    "Mount the stock securely, install the cutting tool, connect the plate and clip, and make sure the Probe LED is off before contact.",
                    "Choose the correct units and jog to the XY origin, then use Zero X and Zero Y or Zero All.",
                    "Probe Z using a touch plate or other preferred method. A common manual example is G38.2 Z-10.000 F100.000.",
                    "After contact, set Z0 with G92 Z<plate_thickness> or G10 L20 Z<plate_thickness> depending on your zeroing mode.",
                    "Retract to Safe Z, remove the plate and clip, and verify that WPos Z is close to zero at the work surface.",
                    "If Run warns that Job Setup is not completed, rerun Job Setup for the current machine session or intentionally choose Start Anyway only if you fully understand the risk.",
                ),
            ),
            _sub(
                "Bit Setter and Tool Reference Behavior",
                bullets=(
                    "The fixed sensor process starts with one coarse seek using the current App Settings Z jog speed as the coarse feed for Job Setup and Tool Change.",
                    "It then takes five exact samples using the shared high-precision helper.",
                    "Each exact sample retracts 5.0 mm, dwells for 0.5 s, and re-probes 6.0 mm at 175 mm/min.",
                    "The final result discards the highest and lowest values and averages the middle three.",
                    "Normal acceptance requires the sample spread to stay at or below 0.050 mm.",
                    "Tool Change gets one retry round at 100 mm/min if the first round exceeds the normal spread limit. If the retry still fails, Tool Change fails.",
                    "Normally open bit setters are supported by the current setup workflow.",
                ),
            ),
            _sub(
                "Probe and Setup Settings",
                bullets=(
                    "Probe Z start (machine, mm) controls the machine-coordinate approach height for tool-reference probing workflows.",
                    "Probe safety margin (mm) is subtracted from $132 travel when computing probe distance for Job Setup and Tool Change.",
                    "XYZ Plate Thickness, XYZ Plate Min Safe Probe Distance, X Offset, Y Offset, Side Clearance Distance, and the staged Z and XY probe speeds are all configured in App Settings > Probing & Setup.",
                    "Bit Setter X, Bit Setter Y, Rough Probe Speed, Fine Probe Speed, and Probe Dwell are also configured there and are shared by the protected setup workflows.",
                ),
            ),
        ),
    ),
    _sec(
        "Dry Run",
        paragraphs=(
            "Dry Run is intended for motion and setup checking without real spindle activity. It is especially useful after changing workholding, zero location, post processor settings, or any other setup assumption.",
        ),
        bullets=(
            "Dry run sanitize strips spindle, coolant, tool-change, S, and T commands while streaming.",
            "When Dry Run is enabled, Run prompts you to continue in Dry Run, switch back to Normal Run and start, or cancel.",
            "Sender-side TC:<tool name> directives still use the built-in Tool Change workflow even when Dry Run sanitizing is on.",
            "Use Dry Run in the air and watch the first moves carefully so you can pause immediately if the machine heads the wrong direction.",
        ),
        notes=(
            "Dry Run is a setup aid, not a substitute for machine clearance checks, workholding checks, or safe spindle operation habits.",
        ),
    ),
    _sec(
        "Macros",
        paragraphs=(
            "Simple Sender separates protected built-in workflows from editable user macros. The protected built-in workflow buttons are always present in the row, while user macros appear only when a valid Macro-1 through Macro-5 file is assigned.",
            "Macro Manager is built into the app so you can edit, duplicate, delete, and reorder the five editable user macro slots without manually editing files by hand.",
        ),
        subsections=(
            _sub(
                "Macro Overview and File Format",
                bullets=(
                    "Protected built-in workflow actions are Home, Park at Bit Setter, Job Setup, Tool Change, and Park at Work. These are core workflows, not editable user macros.",
                    "User macros are exactly five file-backed slots: User Macro 1 through User Macro 5.",
                    "On disk, the user macro files are Macro-1 through Macro-5, with optional .txt extensions, in the discovered macro directories.",
                    "Line 1 of a macro file is the button label.",
                    "Line 2 is the tooltip text.",
                    "Line 3 is the button background color.",
                    "Line 4 is the button text color.",
                    "Line 5 and later are the executed macro body.",
                    "A macro file must include at least one non-blank body line. Unsupported or malformed macros are marked invalid and blocked from running until repaired.",
                ),
            ),
            _sub(
                "Macro Execution and Safety",
                paragraphs=(
                    "Execution happens on a background worker that holds the macro lock so only one macro can run at a time. User macro launches are blocked while the controller is streaming, during alarms, or while disconnected, and Training Wheels confirmations still apply.",
                    "The macro sender waits for GRBL to finish each command and then polls for Idle before continuing. The runner aborts and releases the lock if GRBL raises an alarm, and it logs the offending line so you can recover cleanly.",
                    "General macros now default to finite timeout guards: 120 seconds per line and 900 seconds total unless you change or disable those settings in App Settings > Macros.",
                    "Protected built-in workflows such as Job Setup, Tool Change, and streamed tool changes intentionally run outside the general user-macro timeout limits.",
                ),
                bullets=(
                    "Macro startup modal capture waits on the real $G completion signal.",
                    "LOAD waits for actual load completion or failure.",
                    "OPEN and CLOSE fail fast when the connect or disconnect transition never really started.",
                    "Local-command helpers such as SENDHEX and SAFE fail the macro if their local action fails.",
                    "Streamed TC:<tool name> directives use the protected tool-change path and intentionally wait with no timeout until the operator finishes the workflow.",
                    "Audit-style macro log entries can record raw, evaluated, and outcome details when GUI logging is enabled.",
                ),
                notes=(
                    "Treat macro files as trusted content only and re-test new or changed macros with the spindle off before using them in production.",
                ),
            ),
            _sub(
                "Macro Directives",
                bullets=(
                    "%wait: pauses until GRBL reports Idle before continuing. Use it after long moves or probes when the next line depends on settled machine state.",
                    "%msg <text>: logs text with the [macro] prefix and supports [expression] expansion for live values.",
                    "%update: sends a status request and blocks until a fresh status arrives so variables such as wx, wy, wz, curfeed, and modal tokens are current.",
                    "%if running: executes the current line only while a stream is running or paused.",
                    "%if paused: executes the current line only while streaming is paused.",
                    "%if not running: skips the line while a stream is active so setup logic runs only while the machine is idle.",
                ),
            ),
            _sub(
                "Helper Commands",
                bullets=(
                    "M0, M00, and PROMPT: open the macro prompt dialog. You can customize title=, msg=, message=, text=, buttons=, resume=, cancellabel=, and similar tokens.",
                    "ABSOLUTE or ABS: sends G90 so later moves use absolute coordinates in the active work coordinate system.",
                    "RELATIVE or REL: sends G91 for incremental sequences.",
                    "HOME: runs the same homing action as the UI.",
                    "OPEN [timeout_s]: connects if disconnected and waits for connection.",
                    "CLOSE [timeout_s]: disconnects when connected and waits for close.",
                    "HELP: opens a fixed macro help dialog without touching GRBL.",
                    "QUIT or EXIT: closes the application cleanly.",
                    "LOAD <path>: loads a specific G-code file through the app load path.",
                    "UNLOCK: sends $X to clear alarms.",
                    "RESET: sends a soft reset with Ctrl-X.",
                    "PAUSE or FEEDHOLD: sends ! to hold the stream.",
                    "RESUME or RUN: sends ~ to resume or start streaming.",
                    "STOP: stops streaming and clears the queue.",
                    "SAVE: is unsupported and logs that SAVE is not supported.",
                    "SENDHEX <xx>: sends a raw real-time byte such as SENDHEX 91.",
                    "SAFE <value>: updates the safe helper variable used by some workflows.",
                    "STATE_RETURN or %state_return: restores the modal snapshot captured when the macro started.",
                    "SET0: sends G92 X0 Y0 Z0.",
                    "SETX, SETY, and SETZ: zero a single axis with G92.",
                    "SET <X> <Y> <Z>: zero only the axes you specify.",
                    "!, ~, ?, and Ctrl-X: send feed hold, cycle start, status request, or soft reset directly as real-time bytes.",
                ),
            ),
            _sub(
                "System Variables",
                bullets=(
                    "prbx, prby, prbz: last probe coordinates.",
                    "prbcmd: current probe command, G38.2 by default.",
                    "prbfeed: probe feed rate.",
                    "errline: last macro line that triggered a failure.",
                    "wx, wy, wz: work coordinates.",
                    "mx, my, mz: machine coordinates.",
                    "wa, wb, wc and ma, mb, mc: auxiliary axes if reported by the controller.",
                    "wcox, wcoy, wcoz and related auxiliary WCO values: work coordinate offsets.",
                    "curfeed and curspindle: feed and spindle values from the FS field.",
                    "_camwx and _camwy: camera or non-GRBL helper coordinates if provided.",
                    "G: list of modal G-code words the parser has seen.",
                    "TLO: tool length offset.",
                    "motion, distance, plane, feedmode, arc, units, WCS, cutter, tlo, program, spindle, coolant: modal context tokens.",
                    "tool: current tool number.",
                    "feed: last feed rate.",
                    "rpm: estimated spindle RPM.",
                    "planner and rxbytes: planner buffer use and remaining Bf bytes.",
                    "OvFeed, OvRapid, OvSpindle plus underscored variants and _OvChanged: override state values.",
                    "diameter, cutfeed, cutfeedz, surface, thickness, stepz, stepover, and safe: helper constants for tooling or generator-style macros.",
                    "state: latest GRBL state such as Idle, Run, Hold, or Alarm.",
                    "pins: current pin summary from GRBL.",
                    "msg: last MSG block text.",
                    "PRB: last structured probe report.",
                    "version and controller: firmware metadata.",
                    "running and paused: convenient booleans for stream state checks.",
                    "prompt_choice, prompt_choice_label, prompt_choice_key, prompt_index, and prompt_cancelled: results from prompt dialogs.",
                    "macro.state: a shared namespace for values you want to keep between macro runs.",
                ),
            ),
            _sub(
                "Sharing Variables Between Macros",
                paragraphs=(
                    "Macros share macro.state across runs, so one macro can capture a value and another macro can reuse or adjust it later.",
                ),
                code_blocks=(
                    "Save stock top\n"
                    "Store the current work Z so later macros can reuse it.\n"
                    "%macro.state.STOCK_TOP = wz\n"
                    "%msg Stored stock top (wz) in macro.state.STOCK_TOP",
                    "Return to stock top + lift\n"
                    "Use the stored value, then adjust it for the next operation.\n"
                    "G90\n"
                    "G0 Z[macro.state.STOCK_TOP]\n"
                    "%macro.state.STOCK_TOP = macro.state.STOCK_TOP + 2.0\n"
                    "%msg Lifted; macro.state.STOCK_TOP updated",
                ),
            ),
            _sub(
                "Python Expressions, Loops, and New Variables",
                paragraphs=(
                    "When scripting is enabled, prefix a line with _ or use a Python statement to run logic inside the macro environment. app and os are injected for advanced use, and square brackets evaluate Python expressions before a line is sent.",
                ),
                code_blocks=(
                    "_safe_height = max(float(_macro_vars.get(\"safe\", 3.0)), 4.0)\n"
                    "for pass_num in range(3):\n"
                    "    target = _safe_height + pass_num * 2.0\n"
                    "    _macro_vars[\"last_target\"] = target\n"
                    "    %msg Pass [pass_num + 1]: raising to Z[target]\n"
                    "    G0 Z[target]\n"
                    "    %wait",
                ),
                bullets=(
                    "Square-bracket expressions such as G0 Z[_macro_vars[\"safe\"] + pass_num * 0.5] are evaluated before the line is streamed.",
                    "This is powerful, but it is also why scripting should stay disabled unless you trust the macro source and really need advanced logic.",
                ),
            ),
            _sub(
                "GUI Prompts and Blocking",
                bullets=(
                    "M0, M00, and PROMPT block the macro until the operator chooses a button.",
                    "Prompt tokens such as buttons=Resume|Cancel, [btn(Continue)c], resume=Go, or noresume control which choices are shown.",
                    "The result is stored in the prompt-related macro variables so later lines can branch on the operator's choice.",
                    "%wait blocks until Idle, and %update blocks until a fresh status report arrives.",
                    "The executor aborts if GRBL reports an alarm or if a stream appears unexpectedly where the macro logic expects an idle machine.",
                ),
            ),
            _sub(
                "Example Macro File",
                paragraphs=(
                    "The sample below matches the real user-macro file structure exactly. Line 1 is the button label, line 2 is the tooltip, line 3 is the button color, line 4 is the button text color, and line 5 onward is the macro body. In this sample, the color lines are intentionally left blank so the layout stays accurate while keeping the default colors.",
                ),
                code_blocks=(
                    "Safe Park\n"
                    "Raise to a safe height, confirm, then park at work zero.\n"
                    "\n"
                    "\n"
                    "G90\n"
                    "G0 Z5.000\n"
                    "%wait\n"
                    "PROMPT title=Continue buttons=Park|Abort message=Move to work zero?\n"
                    "G0 X0 Y0\n"
                    "%msg Safe Park completed.",
                ),
            ),
            _sub(
                "Macro Reliability Checklist",
                bullets=(
                    "Add %update before reading live coordinates, overrides, planner, rxbytes, or other state that must be current.",
                    "Make modal intent explicit around motion lines. Use G90 or G91, plane, feed mode, and STATE_RETURN when needed so modal state does not leak into later work.",
                    "Place %wait after long moves or probes when the next line depends on the machine being settled.",
                    "Use %msg at important checkpoints so log files make failures easier to diagnose.",
                    "Configure macro line and total timeout values in App Settings > Macros for unattended or long routines.",
                    "For operator intervention, use PROMPT with explicit buttons and branch on prompt_cancelled or prompt_choice.",
                ),
            ),
            _sub(
                "Macro Troubleshooting",
                bullets=(
                    "If a user macro button is missing, confirm that a valid Macro-1 through Macro-5 file exists in a discovered macro directory or assign the slot in Macro Manager.",
                    "If the button exists but will not run, check whether the app is streaming, alarmed, disconnected, or blocked by a Training Wheels confirmation.",
                    "If coordinates seem stale, insert %update before reading live machine or work values.",
                    "If a macro appears done before motion settles, watch for the final Idle state and use %wait at sequence boundaries.",
                    "If a macro hangs, inspect the state, pins, and prompts, add %msg checkpoints, and set line and total timeout limits.",
                    "If units or modal behavior seem wrong afterward, add STATE_RETURN or explicit restore commands near the macro end.",
                ),
            ),
        ),
    ),
    _sec(
        "VCarve Pro Post-Processors",
        paragraphs=(
            "Simple Sender ships VCarve Pro post processors that are meant to work directly with its multi-tool and accessory workflow. The goal is to keep the operator flow consistent and truthful rather than relying on GRBL handling raw M6 behavior.",
        ),
        subsections=(
            _sub(
                "Included Files",
                bullets=(
                    "Simple-Sender Grbl (mm) (!.gcode).pp: primary millimeter post for production jobs with sender directives.",
                    "Simple-Sender Grbl (inch) (!.gcode).pp: primary inch post for production jobs with sender directives.",
                    "Grbl (mm) warmup (!.gcode).pp: millimeter warmup or utility post without sender-managed tool-change or vacuum directives.",
                    "Grbl (inch) warmup (!.gcode).pp: inch warmup or utility post without sender-managed tool-change or vacuum directives.",
                ),
            ),
            _sub(
                "How These Posts Support the Workflow",
                bullets=(
                    "They emit SSMETA header lines that Simple Sender uses for dimensions, units, and estimate context.",
                    "The Simple-Sender posts emit TC:[TOOLNAME] and begin TOOLCHANGE blocks.",
                    "Simple Sender intercepts TC: lines, pauses streaming, runs the built-in Tool Change workflow, and then resumes streaming.",
                    "The Simple-Sender posts emit VACUUM_OFF before tool-change boundaries and VACUUM_ON at segment or spindle start so sender-managed accessory control stays synchronized with the job.",
                    "This avoids depending on raw M6 behavior in GRBL and keeps the operator flow consistent for multi-tool jobs.",
                ),
            ),
            _sub(
                "Recommended Use",
                bullets=(
                    "Use the Simple-Sender Grbl mm or inch posts for normal cutting jobs that include tool changes or accessory automation.",
                    "Use the warmup posts for warmup or utility programs where you do not want sender-managed TC: or vacuum directives.",
                    "Match the post units to the job units and machine setup so you do not start a job in the wrong unit mode.",
                ),
            ),
        ),
    ),
    _sec(
        "Estimation",
        bullets=(
            "Simple Sender estimates bounds, feed time, and rapid time using $110, $111, and $112 when available, then falls back to the manual max-rate entries and fallback rapid rate when necessary.",
            "Loaded jobs report Estimated Job Time as HH:MM with a CONFIDENT or ROUGH confidence label.",
            "Loaded jobs also report Job Dimensions in both millimeters and inches with a CONFIDENT or ROUGH label.",
            "If SSMETA includes complete extents and units, dimensions are reported from metadata and marked confident.",
            "Estimate confidence still depends on machine settings and observed data, even when dimensions come from metadata.",
            "No Top View or spatial rendering stage runs during load in the current lean sender runtime.",
        ),
    ),
    _sec(
        "Spoilboard Generator",
        paragraphs=(
            "The right-side Spoilboard button generates a surfacing program without opening a separate CAM package. It is meant for fast, repeatable spoilboard cleanup and maintenance work.",
        ),
        subsections=(
            _sub(
                "Inputs",
                bullets=(
                    "Width X",
                    "Height Y",
                    "Tool Diameter",
                    "Stepover %",
                    "Feed XY",
                    "Feed Z",
                    "Surfacing Depth (mm), default 0.50",
                    "Spindle RPM, default 18000",
                    "Start X",
                    "Start Y",
                ),
            ),
            _sub(
                "Assumptions and Motion Flow",
                bullets=(
                    "The generator assumes the spindle is already positioned at the desired work Z plane before running the program.",
                    "Surfacing Depth is an absolute cut plane below Z0, not an incremental step-down. A depth of 0.50 mm means the cut runs at Z = -0.50 mm.",
                    "The start sequence is: switch to relative, raise Z by 10 mm, start the spindle, dwell for 5 seconds, switch to absolute, rapid to the lower-left start point, plunge to the surfacing depth, and then run the raster passes.",
                    "The end sequence is: switch to relative, raise Z by 10 mm, switch to absolute, stop the spindle, and end the program with M30.",
                    "Surfacing Depth must stay at or above 0 and at or below 6.350 mm. A value of 0 cuts at Z0.",
                ),
            ),
            _sub(
                "Post-Generate Options",
                bullets=(
                    "Read G-code loads the generated program directly into the current job using the normal load pipeline without writing a file.",
                    "Save G-code opens a save dialog with a timestamped default filename and writes the program to disk without auto-loading it.",
                    "Cancel closes the modal and discards the generated program.",
                ),
            ),
            _sub(
                "How To Run It",
                bullets=(
                    "Home the machine.",
                    "Jog to the lower-left corner of the surfacing area and set X0 and Y0 there.",
                    "Install the surfacing bit and make sure screws, clamps, dust hoses, and wiring are clear.",
                    "Touch off the spoilboard surface and set Z0 to the current top of the spoilboard.",
                    "Confirm that the generated program uses millimeters and begins with a relative lift to Z+10.",
                    "Start the job with the tool at Z0 or above, not deep below the surface.",
                    "Watch the first moves closely and keep a finger near Feed Hold or Pause in case the tool moves the wrong direction or plunges too deep.",
                ),
                notes=(
                    "The spoilboard workflow assumes Start X and Start Y describe the lower-left corner of the surfacing rectangle. If you use different start coordinates, substitute those values consistently in your setup.",
                ),
            ),
            _sub(
                "Quick Safety Checklist",
                bullets=(
                    "Bit installed and tight.",
                    "Machine homed.",
                    "X and Y zero set at the lower-left corner.",
                    "Z0 set to the current top of the spoilboard.",
                    "Tool starts at or above Z0.",
                    "Area clear of screws and clamps.",
                    "Dust collection on.",
                    "Finger near Feed Hold for the first moves.",
                ),
            ),
        ),
    ),
    _sec(
        "Keyboard Shortcuts and Joystick Bindings",
        subsections=(
            _sub(
                "Keyboard Shortcuts",
                bullets=(
                    "Keyboard shortcuts are configurable and support sequences of up to three keys.",
                    "Conflicts are flagged in the shortcut table.",
                    "Shortcuts are ignored while you are typing in an entry field.",
                    "Shortcuts can be toggled from App Settings or from the Keys quick button in the status bar.",
                    "Training Wheels confirmations still apply when a shortcut triggers a risky action.",
                ),
            ),
            _sub(
                "Joystick Bindings",
                bullets=(
                    "pygame must be installed before the app can talk to USB joystick devices.",
                    "App Settings > Keyboard Shortcuts includes a joystick testing frame that reports detected controllers, shows the latest event, and includes a Refresh joystick list button.",
                    "Enable USB Joystick Bindings turns on polling and action triggering. If bindings are enabled and no joystick is present, newly plugged controllers are discovered automatically.",
                    "Require safety hold for joystick actions makes the safety buttons the controller-enable gate for all joypad and joystick bindings.",
                    "Set Safety Button / Normal Jog Speed captures the normal-speed safety button. Holding it enables all joypad and joystick bindings while joystick jogging stays at the configured speed.",
                    "Set Safety Button / Slow Jog Speed captures the slow-speed safety button. Holding it enables all joypad and joystick bindings while joystick jogging runs at exactly 50 percent speed.",
                    "If both safety buttons are held, Slow wins. If neither is held, no joypad or joystick bindings are acknowledged.",
                    "Keyboard shortcuts and on-screen jog controls are unchanged by the joystick safety hold settings.",
                    "Legacy single safety-button settings migrate to the Normal safety binding.",
                    "Clicking a row's Joystick column listens for the next joystick input so a button, axis, or hat direction can be assigned to that action.",
                    "Every custom joystick binding is saved in settings and survives restarts.",
                    "If the toggle is left on before closing, the app reopens with joystick capture enabled automatically.",
                    "The shortcut list includes X-/X+/Y-/Y+/Z-/Z+ Hold bindings that send one long jog and stop it on release with jog cancel.",
                    "Hold-jog safety is fail-safe: release checks are polled continuously, missed poll gaps trigger deadman cancel, and an extra delayed jog-cancel fallback is sent if needed.",
                    "If the joystick backend or device disappears during a hold jog, the app cancels the active jog immediately.",
                    "The app prevents a single joystick button, axis, or hat from being assigned to more than one action at once.",
                    "Stop joystick hold when app loses focus can automatically end held jog actions if the app window loses focus.",
                    "Hold release sensitivity controls how many missed joystick polls are tolerated before a held jog is ended.",
                ),
            ),
        ),
    ),
    _sec(
        "Kasa Plug (Linux)",
        paragraphs=(
            "Kasa Plug support is available on Linux only and is intended for convenience automation such as a shop vacuum or spindle light. It is not a safety system.",
        ),
        bullets=(
            "Enabled outlets can turn on at job start and turn off when a job finishes, stops, alarms, or is canceled.",
            "Exact trimmed VACUUM_ON and VACUUM_OFF lines in streamed files toggle the configured vacuum outlet immediately and are never sent to GRBL.",
            "Device operations use bounded request timeouts so a stalled Kasa call does not block the accessory worker indefinitely.",
            "The Kasa section lives in App Settings > Kasa Plug. Use Discover, choose the device, refresh the outlet list, map Vacuum and Spindle Light, and use the built-in outlet test buttons before cutting.",
            "If the device exposes only one controllable outlet, the app keeps Vacuum available and disables Spindle Light mapping automatically.",
        ),
        notes=(
            "Keep a physical e-stop or power cutoff available. Kasa control is convenience automation only.",
        ),
    ),
    _sec(
        "Logs and Filters",
        bullets=(
            "Console filters include ALL, ERRORS, ALARMS, and the combined Pos/Status toggle.",
            "Idle status spam stays muted, but the app still processes those status reports internally.",
            "GUI button logging can be turned on when you want UI actions written into the logs.",
            "Performance mode, toggled from App Settings > Interface, batches console output and suppresses RX logs while streaming.",
            "The Logs popup and View Logs... in App Settings > Interface show the rotating application, serial, UI, and error logs with Source and Level filters.",
            "Use Refresh to reload, Clear Logs to truncate active logs and remove rotated logs, and Export Logs to save a ZIP bundle for support.",
            "If you open View Logs... from App Settings, App Settings stays open underneath it, and the Clear Logs confirmation stays above Logs without closing either window.",
            "The Logs popup button is hidden by default. Enable Show Logs Button in App Settings > Interface if you want it in the lower popup row.",
        ),
    ),
    _sec(
        "Troubleshooting",
        bullets=(
            "No serial ports: check drivers, cable, permissions, and whether another app already owns the port.",
            "Connect fails: verify the port and the expected 115200 baud rate, then close any other sender software.",
            "Windows COM checks: confirm that the controller appears in Device Manager and that the COM number changes when you unplug and replug it.",
            "Linux serial permissions: make sure your user belongs to the correct serial-device group and then log out and back in after changing membership.",
            "No $$ refresh: wait for the handshake to finish, clear alarms, and stop streaming before refreshing settings.",
            "Alarm state: use $X or $H as appropriate, then re-home and re-setup as needed.",
            "Run shows Job Setup Not Completed: rerun the built-in Job Setup workflow to capture the session tool reference before cutting.",
            "Preflight says no G-code job is loaded: load or reload the file first.",
            "Preflight says job bounds are unavailable: wait for load or parse work to finish, then rerun the check.",
            "Preflight says travel settings are unavailable: refresh or import GRBL settings so $130, $131, and $132 are available.",
            "Preflight reports out-of-bounds travel: compare the reported span to the machine travel settings and either repost, reposition, or correct the controller settings.",
            "Streaming stops unexpectedly: inspect the console for errors or alarms and verify that the G-code is appropriate for GRBL 1.1h.",
            "Manual queue full: stop sending rapid repeated manual or jog commands and wait for the queue to drain.",
            "Load fails due to 80-byte limit: repost with shorter lines or simpler motion output, especially for long arcs or unsupported axes.",
            "Raspberry Pi feels sluggish: keep Performance mode enabled, avoid extra background tasks, and prefer faster local storage.",
            "Large-file handling feels slow: let the initial prepare path finish and expect some diagnostics work to be sampled or deferred on ultra-large jobs.",
            "Macro behavior is unexpected: inspect the macro in Macro Manager or the sample view and re-test with the spindle off.",
            "Need a support bundle: use App Settings > Diagnostics > Export diagnostics bundle or Export session diagnostics.",
        ),
    ),
    _sec(
        "FAQ",
        bullets=(
            "4-axis or grblHAL: not supported. This build targets 3-axis GRBL 1.1h.",
            "Why is $$ deferred: to avoid startup interleaving so the connection handshake finishes cleanly before the settings table refreshes.",
            "Why are alarms handled strictly: for safety and predictable sender behavior.",
            "Persistent offsets with G10 L20: enable persistent zeroing if you want the zero buttons to use G10 L20 instead of G92.",
        ),
    ),
    _sec(
        "Appendix A: GRBL 1.1h Commands",
        paragraphs=(
            "Simple Sender exposes a practical subset of GRBL real-time, system, and common motion commands through buttons, macros, override controls, and the manual console.",
        ),
        subsections=_grbl_reference_subsections(),
    ),
    _sec(
        "Appendix B: GRBL 1.1h Settings (Selected)",
        bullets=(
            "$0: Step pulse, us",
            "$1: Step idle delay, ms",
            "$2: Step port invert mask",
            "$3: Direction port invert mask",
            "$4: Step enable invert",
            "$5: Limit pins invert",
            "$6: Probe pin invert",
            "$10: Status report mask",
            "$11: Junction deviation, mm",
            "$12: Arc tolerance, mm",
            "$13: Report inches",
            "$20: Soft limits",
            "$21: Hard limits",
            "$22: Homing enable",
            "$23: Homing direction invert mask",
            "$24: Homing feed, mm/min",
            "$25: Homing seek, mm/min",
            "$26: Homing debounce, ms",
            "$27: Homing pull-off, mm",
            "$30: Max spindle speed, RPM",
            "$31: Min spindle speed, RPM",
            "$32: Laser mode",
            "$100, $101, $102: Steps per mm for X, Y, and Z",
            "$110, $111, $112: Max rate for X, Y, and Z",
            "$120, $121, $122: Max acceleration for X, Y, and Z",
            "$130, $131, $132: Max travel for X, Y, and Z",
        ),
        notes=(
            "Use the GRBL Settings popup to edit these values. Pending edits stay highlighted until you send them, and the table applies numeric validation and broad range checks.",
        ),
    ),
    _sec(
        "Appendix C: Workflow and Macro Reference",
        bullets=(
            "Built-in: Home. Purpose: run $H from the protected workflow row. Use it before setup, after alarms, or after controller resets when homing is required.",
            "Built-in: Park at Bit Setter. Purpose: move to the configured fixed-sensor coordinates for cleaning, inspection, or staging.",
            "Built-in: Job Setup. Purpose: guide the operator through XYZ Plate, Z Plate, or Manual setup and capture the reference tool height for the current valid session.",
            "Built-in: Tool Change. Purpose: re-probe after a tool swap, reapply the stored tool-reference logic, and then park at safe Z over WCS X0 Y0. This is also the workflow used by streamed TC:<tool name> directives.",
            "Built-in: Park at Work. Purpose: raise to a configured safe machine Z and return to WCS X0 Y0 without changing offsets.",
            "User Macro 1-5. Purpose: editable file-backed user routines managed in Macro Manager for custom operator workflows outside the protected built-in setup actions.",
        ),
    ),
    _sec(
        "Appendix D: UI Field Appendix",
        paragraphs=(
            "This appendix is a field-by-field reference for the visible controls and popups in the current interface. Numeric entries can open the touch keypad when that setting is enabled, and touch actions briefly acknowledge input in the status bar.",
        ),
        subsections=(
            _sub(
                "Top Toolbar",
                bullets=(
                    "Port selector: chooses the serial port used by Connect.",
                    "Refresh: rescans ports and repopulates the list.",
                    "Connect or Disconnect: opens or closes the selected port and shows Connecting... or Disconnecting... while workers run.",
                    "Read Job: opens the shared file dialog and loads the selected file as the current job.",
                    "Clear Job: unloads the current job and resets samples and state.",
                    "Run: starts streaming the loaded job. If Job Setup state is invalid, it shows Job Setup Not Completed with Start Anyway and Cancel.",
                    "Pause: issues feed hold during a running job.",
                    "Resume: resumes after a hold.",
                    "Stop/Reset: stops streaming and soft-resets GRBL per the configured ALL STOP behavior.",
                    "Unlock: sends $X to clear alarms.",
                    "Machine state label: shows states such as Disconnected, Idle, Run, Hold, and Alarm.",
                ),
            ),
            _sub(
                "Position + Jog Panel",
                bullets=(
                    "MPos X, Y, Z: live machine position readouts, with per-axis jog-to-target actions that use the numeric keypad for absolute machine-coordinate moves.",
                    "Home: runs the homing cycle.",
                    "Units toggle: switches modal units and reflects report-unit tracking via $13.",
                    "WPos X, Y, Z: live work position readouts.",
                    "Zero X, Zero Y, Zero Z: zero a single axis using G92 or G10 L20 depending on persistent zeroing.",
                    "Zero All: zeroes all axes in the current work coordinate system.",
                    "Goto Zero: runs G90 G0 X0 Y0 and then G0 Z0 so XY completes before Z moves.",
                    "Jog X+/X-/Y+/Y-/Z+/Z-: jog by the selected step using the current jog feed.",
                    "JOG STOP: sends jog cancel and clears pending jog state.",
                    "Tool reference label: shows the stored tool reference height used by probing workflows and the Run safeguard.",
                    "ALL STOP: immediate stop using the selected ALL STOP behavior.",
                    "XY Step and Z Step adjusters: change the jog step values shown in the panel.",
                ),
            ),
            _sub(
                "Workflow + Macro Panel (Jog Area)",
                bullets=(
                    "Protected workflow buttons: Home, Park at Bit Setter, Job Setup, Tool Change, and Park at Work always appear first.",
                    "User macro buttons: populated Macro-1 through Macro-5 files appear after the protected workflows.",
                    "User macro header colors: line 3 sets button background and line 4 sets button text color.",
                    "Invalid user macros are labeled [invalid] and cannot run.",
                    "Right-click sample opens a read-only macro sample. Malformed or unsupported macros show a Macro error dialog instead.",
                    "User macro tooltips display the second line of each macro file.",
                    "User macros are blocked while streaming, during alarms, or while disconnected.",
                ),
            ),
            _sub(
                "Console",
                bullets=(
                    "Console log: read-only GRBL traffic display.",
                    "Command entry: manual command input field.",
                    "Send: sends the entry to GRBL if the current machine state allows it.",
                    "Save: exports the console to a timestamped text file.",
                    "Clear: clears the console contents.",
                    "Filters ALL, ERRORS, ALARMS: control which messages are shown.",
                    "Pos/Status toggle: includes or omits status and position reports from the console and from saved console exports.",
                ),
            ),
            _sub(
                "Job Info Popup",
                bullets=(
                    "Read-only scrollable job summary.",
                    "Shows SSMETA header fields when present.",
                    "Shows quick-scan metrics, dimensions, estimate, and separate Toolpaths and Tools lists when metadata provides them.",
                    "If metadata is already available from the load pipeline, the first popup open shows it immediately.",
                ),
            ),
            _sub(
                "Logs Popup",
                bullets=(
                    "Read-only view of application, serial, UI, and error logs.",
                    "Source filter: Application, Serial, UI, Errors, or All.",
                    "Level filter: DEBUG, INFO, WARNING, ERROR, or CRITICAL.",
                    "Refresh: reloads the visible logs.",
                    "Clear Logs: truncates active logs and removes rotated logs after confirmation.",
                    "Export Logs: writes a ZIP bundle and reports whether the export fully succeeded or only partially succeeded.",
                ),
            ),
            _sub(
                "Right-Side Controls",
                bullets=(
                    "Feed override slider: sets the feed override target from 10 to 200 percent.",
                    "Spindle override slider: sets the spindle override target from 10 to 200 percent.",
                    "Spindle ON: starts the spindle at the saved default RPM.",
                    "Spindle OFF: stops the spindle.",
                    "Current spindle speed: read-only display of the tracked spindle RPM.",
                    "Spindle RPM / Apply RPM: saves the default RPM and can also re-issue the RPM to a running spindle after resetting spindle override to 100 percent.",
                    "Spoilboard: opens the Spoilboard Generator dialog.",
                ),
            ),
            _sub(
                "Spoilboard Generator Dialog",
                bullets=(
                    "Width X and Height Y: surfacing rectangle dimensions.",
                    "Tool Diameter: cutter diameter used to derive row spacing.",
                    "Stepover %: percentage of tool diameter used for stepover.",
                    "Feed XY and Feed Z: cutting and plunge feed rates.",
                    "Surfacing Depth (mm): absolute cut plane below Z0, default 0.50, valid from 0.00 to 6.35.",
                    "Spindle RPM: spindle speed for M3.",
                    "Start X and Start Y: lower-left origin of the surfacing rectangle.",
                    "Generate: builds G-code in memory and opens the Read/Save/Cancel modal.",
                    "Read G-code: loads the generated program immediately.",
                    "Save G-code: saves it with a timestamped default filename.",
                    "Cancel: closes the modal without loading or saving.",
                ),
            ),
            _sub(
                "Raw $$ Popup",
                bullets=(
                    "Read-only raw settings dump from the last GRBL settings refresh.",
                    "Hidden by default. Enable it with App Settings > Interface > Auxiliary panel buttons > Show Raw $$ Button.",
                ),
            ),
            _sub(
                "GRBL Settings Popup",
                bullets=(
                    "If the post-connect $$ snapshot was already captured, the first popup open renders that cached snapshot immediately.",
                    "Refresh $$: requests a fresh settings dump and populates the table when you want a newer controller snapshot.",
                    "Save Changes: writes edited settings back to GRBL in sequence and verifies them with a follow-up refresh.",
                    "Settings table: scrollable columns for Setting, Name, Value, Units, and Description.",
                    "Edited highlight: pending edits remain highlighted until saved or reverted.",
                ),
            ),
            _sub(
                "App Settings: Global Controls",
                bullets=(
                    "Search: filters App Settings sections by category, title, and keywords.",
                    "View mode: Basic shows day-to-day controls; Advanced reveals all sections.",
                    "Sticky section title: keeps the active category pinned while scrolling.",
                ),
            ),
            _sub(
                "App Settings: Theme",
                bullets=(
                    "UI theme: selects the ttk theme.",
                    "UI scale: immediate scale factor, with Apply after typing.",
                    "Linux File Dialog Scale: minimum temporary scaling used for Linux file dialogs.",
                    "Linux File Dialog Default Path: default folder for shared dialogs on Linux when no valid per-dialog path exists.",
                    "Scrollbar width: global scrollbar thickness choice.",
                    "Touch scroll mode: thumb_only or thumb_and_swipe behavior inside App Settings.",
                    "Enable tooltips: toggles hover tips across the app.",
                    "Tooltip display duration (sec): auto-hide timer, where 0 keeps tooltips visible.",
                    "Enable numeric keypad popups: toggles the touch keypad on numeric fields.",
                ),
            ),
            _sub(
                "App Settings: Estimation",
                bullets=(
                    "Fallback rapid rate: used when $110 through $112 are unavailable.",
                    "Estimator adjustment slider: multiplies the estimate.",
                    "Max rates X, Y, Z: manual max rates used when GRBL rates are unavailable.",
                ),
            ),
            _sub(
                "App Settings: Status Polling",
                bullets=(
                    "Status report interval: seconds between status requests.",
                    "Disconnect after failures: consecutive failed status requests before disconnecting.",
                ),
            ),
            _sub(
                "App Settings: Error Dialogs",
                bullets=(
                    "Enable error dialogs.",
                    "Minimum interval, burst window, and max dialogs per window.",
                    "Show GRBL alarm and error popups.",
                    "GRBL popup dedupe interval.",
                    "Show job completion dialog.",
                    "Play reminder beep on completion.",
                ),
            ),
            _sub(
                "App Settings: Macros",
                bullets=(
                    "Allow macro scripting (Python/eval).",
                    "Line timeout (sec).",
                    "Total timeout (sec).",
                    "Disable Macro Timeouts.",
                    "Open Macro Manager.",
                    "Protected built-in workflows remain outside the general user-macro timeout limits.",
                ),
            ),
            _sub(
                "App Settings: Probing & Setup",
                bullets=(
                    "Probe Z start (machine, mm).",
                    "Probe safety margin (mm).",
                    "XYZ Plate Thickness.",
                    "XYZ Plate Min Safe Probe Distance.",
                    "XYZ Plate X Offset and Y Offset.",
                    "XYZ Plate Side Clearance Distance.",
                    "XYZ Plate Z Rough, Re-Probe, and Fine Probe Speed.",
                    "XYZ Plate XY Rough and Fine Probe Speed.",
                    "XYZ Plate Probe Dwell (Seconds).",
                    "Bit Setter X and Y.",
                    "Bit Setter Rough Probe Speed.",
                    "Bit Setter Fine Probe Speed.",
                    "Bit Setter Probe Dwell (Seconds).",
                ),
            ),
            _sub(
                "App Settings: Zeroing",
                bullets=(
                    "Use persistent zeroing (G10 L20): switches zeroing buttons from G92 to G10 L20.",
                ),
            ),
            _sub(
                "App Settings: Jogging",
                bullets=(
                    "Default jog feed (X/Y).",
                    "Default jog feed (Z).",
                    "Apply safe mode.",
                    "DRO jog smoothing (interpolation): Off, UI jog only, or All jog.",
                ),
            ),
            _sub(
                "App Settings: Keyboard Shortcuts",
                bullets=(
                    "Enabled toggle.",
                    "Shortcut table and key editor.",
                    "Joystick binding capture.",
                    "Remove/Clear Binding.",
                    "Joystick testing frame.",
                    "Refresh joystick list.",
                    "Enable USB Joystick Bindings.",
                    "Require safety hold for joystick actions.",
                    "Set Safety Button / Normal Jog Speed.",
                    "Set Safety Button / Slow Jog Speed.",
                    "No safety held: no joypad or joystick bindings are acknowledged.",
                    "Both safety buttons held: all joypad and joystick bindings remain active and Slow wins for jog speed.",
                    "Stop joystick hold when app loses focus.",
                    "Hold release sensitivity.",
                    "Live input state labels.",
                ),
            ),
            _sub(
                "App Settings: Interface",
                bullets=(
                    "Start in fullscreen.",
                    "Preload App Settings popup after startup.",
                    "Performance mode.",
                    "Log GUI button actions.",
                    "View Logs...",
                    "Auxiliary panel buttons: Show Logs Button, Show Raw $$ Button, and Show Checklists Button.",
                    "Status indicators: Endstops, Probe, and Hold.",
                    "Status bar quick buttons: Tips, Keys, Vac, Light, and Release.",
                    "Status bar quick actions: Tips, Keys, Vac, and Light.",
                ),
            ),
            _sub(
                "App Settings: Diagnostics",
                bullets=(
                    "Logging Mode: Standard or Verbose.",
                    "Standard is the default and reduces routine TX file logging while keeping recent serial activity available for diagnostics.",
                    "Verbose preserves fuller detailed TX logging for troubleshooting.",
                    "Developer Options.",
                    "Preflight check (Run check).",
                    "Export session diagnostics (Save report).",
                    "Runtime telemetry (Open telemetry).",
                    "Export diagnostics bundle (Save ZIP).",
                    "Save final performance report (Save to Logs).",
                    "Apply perf-test preset.",
                    "Apply perf-test preset forces Logging Mode to Standard.",
                    "Backup bundle Export and Import.",
                    "Sample-only threshold (lines).",
                    "Ultra-large threshold (MB) and threshold info.",
                    "Enable runtime performance profiling (restart required).",
                    "Enable leak-watch snapshots (higher overhead).",
                    "Performance report log path.",
                ),
            ),
            _sub(
                "Baseline Capture (Lean Mode)",
                bullets=(
                    "Idle with no file loaded for about 5 minutes.",
                    "Idle with a representative file loaded for 5 to 10 minutes.",
                    "Make sure runtime metrics spend at least one sample interval in each phase you want to compare.",
                    "Export diagnostics and compare runtime_metrics.json and performance_report.txt across runs.",
                ),
            ),
            _sub(
                "App Settings: Safety",
                bullets=(
                    "All Stop behavior.",
                    "Dry run sanitize.",
                    "Suspend watchdog during homing.",
                    "Homing watchdog grace (seconds).",
                ),
            ),
            _sub(
                "App Settings: Safety Aids",
                bullets=(
                    "Training Wheels.",
                    "Reconnect to last port on open.",
                ),
            ),
            _sub(
                "App Settings: System",
                bullets=(
                    "Close Application.",
                    "Restart workflow: close the app and relaunch when needed, because there is no separate Restart Application button in the current build.",
                    "Shutdown (Linux only).",
                    "Reboot (Linux only).",
                    "Pi profile (Linux only).",
                    "Pi profile forces Logging Mode to Standard for lower-overhead operation.",
                ),
            ),
            _sub(
                "Checklists Popup",
                bullets=(
                    "Checklist items load from checklist-*.chk files.",
                    "Checklist title toggle collapses or expands each checklist.",
                    "Checklists are shown by default and can be hidden with Show Checklists Button in App Settings > Interface.",
                ),
            ),
            _sub(
                "Macro Sample Dialog",
                bullets=(
                    "Title shows the macro being sampled.",
                    "Macro text shows the valid macro body without the header lines.",
                    "Invalid macro behavior opens a Macro error dialog instead of the sample.",
                    "Close dismisses the sample dialog.",
                ),
            ),
            _sub(
                "Macro Manager Dialog",
                bullets=(
                    "Macro list: overview of User Macro 1 through User Macro 5, with [invalid] markers when needed.",
                    "Name, tooltip, color, text-color, and body editor.",
                    "Save and Delete for the selected slot.",
                    "Duplicate to copy one slot to another.",
                    "Reorder to move macro content between slots while keeping numbered naming.",
                ),
            ),
            _sub(
                "Macro Prompt Dialog",
                bullets=(
                    "Message: macro-supplied prompt text.",
                    "Choice buttons: macro-defined options returned to the macro.",
                    "Close window: returns the configured cancel choice.",
                ),
            ),
            _sub(
                "Status Bar",
                bullets=(
                    "Status text: current connection or job state.",
                    "Progress bar: job completion percentage.",
                    "Buffer fill bar: GRBL RX buffer usage.",
                    "Throughput label: current transmit rate.",
                    "Error dialog status: shows suppression state.",
                    "Endstops, Probe, and Hold LEDs.",
                    "Lock: locks or unlocks the screen so operator input is ignored while locked.",
                    "Tips, Keys, Vac, Light, and Release quick buttons.",
                ),
            ),
        ),
    ),
)
