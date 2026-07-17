# Simple Sender v3.19.1

Version 3.19.1 hardens the boundary between sender intent and controller-confirmed execution state.

- Pause, external Hold, and Safety Door close ordinary stream and tool-change workflow transmission immediately.
- Resume remains blocked until the current controller session confirms that Hold or Door has ended. A pending tool change then returns to its independent workflow pause instead of ordinary Running.
- Safety-significant realtime write failures enter Recovery Required before normal command admission can continue; uncertain current-session jog-cancel failure fails closed even when cached status appears Idle.
- Every streamed Tool Change command and completion is bound to the exact originating connection, serial object, stream/source directive, and request identity, so stale workflows cannot command or release a later tool change. A current replacement behind a still-live stale UI thread is explicitly failed with Stop/restart guidance rather than left pending.
- Auto-Level holds one exclusive worker-owned workflow lease through modal restoration and a two-phase map installation. One exact single-use ticket authorizes staging; only final worker revalidation publishes a provenance-bound map and enables Apply/Save. Reset, alarm, recovery, session/source replacement, and relevant coordinate-context changes invalidate the map. Historical loaded maps remain inactive. Cancel initiates controller stop/reset recovery and does not send later restoration motion or modal commands.
- Resume From preserves the physical `G43.1` tool-length offset across G20/G21 transitions and blocks ambiguous reconstruction, including multiple `G43.1`/`G49` or Z words in one block.
- Console filters are labeled `All`, `Errors`, and `Alarms`.
- A clean real-controller connection now opens the identity-bound Job Readiness prompt automatically after current-session synchronization. Job Ready still requires homing or explicit acceptance of a physically checked position and exact snapshot installation.
- The Job Readiness prompt's Home Machine action uses the protected homing lifecycle and watchdog grace, then waits for current-session Home-to-Idle evidence before Job Ready.

Dry run sanitize strips spindle, coolant, M6, S, and T commands while streaming. When Dry Run is enabled, both a fresh **Run** and resume-start paths such as **Resume From** and reconnect resume require explicit operator confirmation before stream side effects begin.

Software hold and soft reset are not emergency stops. A physical emergency stop and appropriate power isolation remain mandatory for machine operation.
