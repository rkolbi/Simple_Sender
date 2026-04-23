# Simple Sender v3.12

This document summarizes the current stable `3.12` release baseline.

**Simple Sender v3.12** is a GRBL 1.1h g-code sender built for practical, real-world CNC use. Rather than focusing on flashy presentation or unnecessary complexity, it is designed to provide a dependable, operator-friendly workflow that works well in an actual shop environment.

At its core, Simple Sender is built around a very clear set of design objectives. It aims to run well on affordable hardware such as a **Raspberry Pi 4**, remain responsive during daily use, and provide a clean, workflow-oriented interface that avoids clutter. It is meant to be easy to operate directly at the machine, including from a touchscreen, while still being capable enough to handle demanding, real-world CNC work. The overall goal is not to impress with gimmicks, but to make machine operation clearer, more stable, and more practical.

That design philosophy shows up directly in the app's feature set. Simple Sender is designed to **stream very large G-code files reliably**, remain lightweight and efficient, and focus on the features that matter most during actual cutting. It places a strong emphasis on dependability, clarity, and usability instead of trying to become an overloaded all-in-one platform. In the current revision, normal successful completion is also tied to verified cleaned EOF rather than an estimate, so the sender does not present a clean-complete result before the executable file content has actually been streamed through to the real end.

One of the more distinctive parts of Simple Sender is its effort to improve the **entire machine-side workflow**, not just send lines of code. It supports custom sender directives such as **`VACUUM_ON`**, **`VACUUM_OFF`**, and **`TC:<tool name>`**, allowing posted jobs to include machine-side instructions that the sender can interpret as part of the machining process. This helps bridge the gap between CAM output and what the operator actually has to do at the machine. Included support for **VCarve Pro 12.5 post processors**, in both inch and mm variants, reinforces that workflow-driven approach.

Simple Sender also puts major focus on **multi-tool operation**. It supports tool reference and offset handling so the machine can compensate for differences in tool height during tool changes. On top of that, it provides a **guided tool-change workflow** that is meant to help the operator change tools correctly and, when invoked by a streamed `TC:` directive, resume the paused job in a structured, controlled way. That built-in flow parks at safe Z over work `X0/Y0` before the posted job repositions. For anyone doing more than basic single-tool work, that is a meaningful capability.

Safety and setup control are also key parts of the app's purpose. Simple Sender includes **Job Setup safeguards** intended to help prevent a job from being started before setup is complete. It is designed to reduce common operator errors, especially around preparation, tool-change handling, and workflow state. That makes it feel less like a bare-bones sender and more like a sender that actively supports safe, repeatable operation.

That same safety posture now extends to end-of-job handling. For real jobs, the sender's normal clean-completion path now enforces spindle-off and raises to the same safe Z used by the built-in Park workflow before it reports a clean completion result. If that safer end state cannot be verified or completed, the operator gets a warning instead of a silent success claim.

The `3.12` release also keeps the top toolbar's icon path practical and deployment-friendly. Toolbar assets live in `simple_sender/ui/icons`, the normal runtime path prefers app-local raster icons for consistent Windows and Raspberry Pi / Linux rendering, and the app still preserves safe fallback icons if those assets cannot be loaded.

This release also includes a deliberate prerelease truthfulness pass. The validated local gate is current, the release-facing docs are aligned to the shipped workflows and settings, and the latest lower jog-row polish is now reflected in both the runtime layout and the test suite. The repository also now includes a dedicated `MACHINE_VALIDATION_CHECKLIST.md` for the remaining reconnect, probing/modal-restore, accessory, popup, and Pi/Openbox validation work that automated tests cannot fully prove on their own.

Dry Run workflow truthfulness is also part of that safety posture. When Dry Run is enabled, both **Run** and **Resume** paths now require an explicit operator decision to continue in Dry Run, switch to Normal Run, or cancel before stream start/resume side effects are committed.

The app also includes features aimed at making everyday machine control faster and more convenient. It supports **keyboard shortcuts** and **joystick bindings** for quicker control at the machine, as well as **macro support** for repeatable setup and operating tasks. Those macros can range from simple convenience actions to more advanced Python-based logic, giving users a way to build repeatable workflows into their day-to-day operation.

Another practical feature is support for **automatic control of Kasa-connected accessories**, such as a shop vacuum or machine light. That kind of integration fits well with the overall philosophy behind the project: reduce friction, reduce repetitive manual actions, and make the sender a more useful part of the CNC workflow.

Overall, Simple Sender v3.12 is built around a straightforward idea: a CNC sender should be **lightweight, stable, clear, and genuinely useful at the machine**. It should support real workflows, help the operator stay organized, and make repetitive or error-prone tasks easier to manage. Everything in the app's design objectives points back to that same goal.

The `3.12` release-ready baseline carries forward that workflow direction with cleaner lower-UI behavior, stronger tool-change and setup truthfulness, current logging-mode behavior, the current cross-platform toolbar icon pipeline, richer validation diagnostics for popup/reconnect/accessory edge cases, and a freshly revalidated release gate.

If you want a GRBL sender that emphasizes **practical shop use, touchscreen-friendly operation, reliable large-file streaming, multi-tool workflow support, machine-side automation, and day-to-day dependability**, that is exactly what Simple Sender is built to provide.
