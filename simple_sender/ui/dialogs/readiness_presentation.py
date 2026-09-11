"""Presentation shared by readiness and recovery; never admits machine commands."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from simple_sender.types import NormalSessionPhase, RecoveryPhase
from simple_sender.ui.dialogs.popup_utils import center_window
from simple_sender.ui.scrollable_container import build_scrollable_container


def normal_session_copy(state) -> tuple[str, str]:
    if state.homing_started and state.phase is NormalSessionPhase.POSITION_REQUIRED:
        return "Homing in progress", (
            "Homing has been requested. Wait for the controller to finish and confirm position. "
            "Other movement actions remain unavailable."
        )
    return {
        NormalSessionPhase.SYNCHRONIZING: ("Synchronizing controller", (
            "Reading the controller's operating state. Machine readiness is not yet established. "
            "Wait for synchronization; use Retry State Sync if another attempt is needed."
        )),
        NormalSessionPhase.POSITION_REQUIRED: ("Homing required", (
            "Controller information is available, but machine position still needs to be established. "
            "Clear the machine's travel area, then select Home Now. "
            "I'll Home Later only dismisses this reminder; home before jogging, zeroing, probing, "
            "parking, tool changes or running jobs."
        )),
        NormalSessionPhase.SNAPSHOT_INSTALL_PENDING: ("Applying controller state", (
            "Controller checks have been gathered. Wait while Simple Sender applies and confirms them. "
            "Machine controls remain blocked until that step finishes."
        )),
        NormalSessionPhase.FAILED: ("Controller synchronization incomplete", (
            "Simple Sender could not establish the controller state needed for operation. "
            "Check the connection and controller status, then use Retry State Sync. "
            "Technical details are available below."
        )),
    }.get(state.phase, ("Machine readiness", "Check the main-window status for the current machine state."))


def recovery_copy(state, missing: tuple[str, ...]) -> tuple[str, str]:
    if state.phase is RecoveryPhase.RESET_REQUIRED:
        return "Recovery required", (
            "The machine's state is uncertain and it may still be executing. "
            "Use the physical emergency stop or isolate power if there is a hazard. "
            "Select Send Reset to request a controller reset; software reset is not an emergency stop."
        )
    if state.phase is RecoveryPhase.RESET_SENT_AWAITING_BANNER:
        return "Waiting for reset confirmation", (
            "A reset was requested, but controller confirmation is still pending. "
            "Do not assume motion or the spindle has stopped. Check the machine physically."
        )
    if state.phase is RecoveryPhase.RECOVERY_COMPLETE:
        return "Applying recovery state", (
            "Recovery checks were accepted, but the application still needs to apply and confirm them. "
            "Controls remain blocked. Use Retry Recovery Finalization if this step has not finished."
        )
    if state.phase is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED:
        if state.homing_started and "machine position" in missing:
            return "Recovery homing in progress", (
                "Homing has been requested. Wait for the controller to finish and confirm position, "
                "then complete the remaining recovery checks."
            )
        if not missing:
            return "Ready for final recovery check", (
                "The required state checks are recorded. Select Complete Recovery to request final "
                "validation. This does not resume the interrupted job or restore its tool reference."
            )
        steps = ["Reset is confirmed. Some recovery checks remain incomplete."]
        if any(item in missing for item in (
            "work coordinates/WCO", "active WCS", "G92", "modal state", "tool-length compensation",
        )):
            steps.append("Use Sync $G / $# to read the controller's coordinate offsets and operating modes.")
        if "machine position" in missing:
            steps.append("Use Home Machine, or Accept Position only after physically checking it.")
        if "spindle state" in missing:
            steps.append("Use Verify Spindle after checking that the spindle is physically stopped and the controller reports M5.")
        if "coolant state" in missing:
            steps.append("Use Verify Coolant after checking that outputs are physically off and the controller reports M9.")
        steps.append("Then select Complete Recovery for final validation.")
        return "Verify machine state", "\n\n".join(steps)
    return "Recovery status", "Check the main-window status before further machine operation."


class ReadinessPresentation:
    """Scrollable explanation with action buttons kept outside the scroll area."""

    def __init__(self, dialog, app, *, intro: str) -> None:
        self.dialog = dialog
        self.app = app
        dialog._readiness_presentation = self
        outer = ttk.Frame(dialog, padding=12)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        host = ttk.Frame(outer)
        host.grid(row=0, column=0, sticky="nsew")
        self.scroll = build_scrollable_container(host, app=app)
        if self.scroll.canvas is not None:
            self.scroll.canvas.configure(takefocus=True)
            for key, amount in (("<Prior>", -1), ("<Next>", 1)):
                self.scroll.canvas.bind(
                    key, lambda _event, step=amount: self.scroll.canvas.yview_scroll(step, "pages") or "break"
                )
        content = self.scroll.content
        self.message = tk.StringVar(master=dialog)
        self.details = tk.StringVar(master=dialog)
        self.heading = tk.StringVar(master=dialog)
        self.labels = (
            ttk.Label(content, textvariable=self.heading, font="TkHeadingFont", justify="left", wraplength=600),
            ttk.Label(content, text=intro, justify="left", wraplength=600),
            ttk.Label(content, textvariable=self.message, justify="left", wraplength=600),
        )
        for label in self.labels:
            label.pack(fill="x", pady=(0, 10))
        self.toggle = ttk.Button(content, text="Show technical details", command=self.toggle_details)
        self.toggle.pack(anchor="w", pady=(0, 8))
        self.detail_label = ttk.Label(content, textvariable=self.details, justify="left", wraplength=600)
        self.expanded = False
        self.buttons = ttk.Frame(outer, padding=(0, 10, 0, 0))
        self.buttons.grid(row=1, column=0, sticky="ew")
        for column in range(2):
            self.buttons.grid_columnconfigure(column, weight=1, uniform="action")
        content.bind("<Configure>", self._wrap, add="+")
        dialog.resizable(True, True)

    def _wrap(self, event) -> None:
        width = max(100, event.width - 12)
        for label in (*self.labels, self.detail_label):
            if int(label.cget("wraplength")) != width:
                label.configure(wraplength=width)

    def toggle_details(self) -> None:
        self.expanded = not self.expanded
        if self.expanded:
            self.detail_label.pack(fill="x", pady=(0, 8))
        else:
            self.detail_label.pack_forget()
        self.toggle.configure(text="Hide technical details" if self.expanded else "Show technical details")

    def update(self, title: str, message: str, details: str) -> None:
        self.dialog.title(title)
        self.heading.set(title)
        self.message.set(message)
        self.details.set(details)

    def present(self) -> None:
        # Keep actions visible even when the explanation requires scrolling.
        width = min(700, max(240, self.dialog.winfo_screenwidth() - 64))
        max_height = max(240, self.dialog.winfo_screenheight() - 100)
        self.dialog.geometry(f"{width}x{min(660, max_height)}")
        self.dialog.update_idletasks()
        minimum_height = min(max_height, self.buttons.winfo_reqheight() + 100)
        self.dialog.minsize(min(480, width), minimum_height)
        # Measure the explanation at the intended width, not the canvas's
        # pre-map requested width (which can greatly overestimate its height).
        for label in self.labels:
            label.configure(wraplength=max(100, width - 64))
        content_height = sum(label.winfo_reqheight() + 10 for label in self.labels)
        content_height += self.toggle.winfo_reqheight() + 8
        height = min(max_height, max(
            minimum_height,
            content_height + self.buttons.winfo_reqheight() + 32,
        ))
        self.dialog.geometry(f"{width}x{height}")
        center_window(self.dialog, self.app)
        x = max(0, min(self.dialog.winfo_x(), self.dialog.winfo_screenwidth() - width))
        y = max(0, min(self.dialog.winfo_y(), self.dialog.winfo_screenheight() - self.dialog.winfo_height() - 64))
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.lift()
