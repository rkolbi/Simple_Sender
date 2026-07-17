"""Explicit Job Ready initialization for a clean GRBL connection."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from simple_sender.types import NormalSessionPhase
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme, center_window
from simple_sender.ui.dialogs.startup_connection_dialog import (
    close_startup_connection_dialog,
)


def show_normal_session_initialization(app) -> None:
    close_startup_connection_dialog(app)
    worker = app.grbl
    state = worker.normal_session_state()
    if not state.required:
        messagebox.showinfo(
            "Job readiness",
            "Normal-session initialization is not currently required.",
        )
        return
    identity = state.action_identity
    existing = getattr(app, "_normal_session_initialization_dialog", None)
    existing_identity = getattr(
        app, "_normal_session_initialization_dialog_identity", None
    )
    existing_phase = getattr(app, "_normal_session_initialization_dialog_phase", None)
    if existing is not None and existing_identity == identity and existing_phase == state.phase:
        try:
            existing.lift()
            existing.focus_force()
        except Exception:
            pass
        return
    if existing is not None:
        try:
            existing.destroy()
        except Exception:
            pass

    dlg = tk.Toplevel(app)
    app._normal_session_initialization_dialog = dlg
    app._normal_session_initialization_dialog_identity = identity
    app._normal_session_initialization_dialog_phase = state.phase
    dlg.title("Machine information obtained")
    dlg.transient(app)
    dlg.resizable(False, False)
    apply_toplevel_theme(dlg, app)
    frame = ttk.Frame(dlg, padding=14)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=(
            f"Successfully connected to {getattr(app, '_connected_port', '') or 'the controller'}.\n\n"
            "Machine configuration and controller state were obtained.\n\n"
            "Home the machine before running jobs, probing, tool changes, parking, "
            "or other machine-coordinate operations."
        ),
        wraplength=560,
        justify="left",
    ).pack(fill="x", pady=(0, 10))
    phase_var = tk.StringVar()
    detail_var = tk.StringVar()
    ttk.Label(frame, textvariable=phase_var, justify="left").pack(fill="x")
    ttk.Label(frame, textvariable=detail_var, wraplength=560, justify="left").pack(
        fill="x", pady=(4, 10)
    )
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")

    def identity_current() -> bool:
        try:
            return bool(worker.normal_session_action_identity() == identity)
        except Exception:
            return False

    def close_dialog() -> None:
        if state.phase is NormalSessionPhase.POSITION_REQUIRED:
            app._normal_session_initialization_acknowledged_identity = identity
        if getattr(app, "_normal_session_initialization_dialog", None) is dlg:
            app._normal_session_initialization_dialog = None
            app._normal_session_initialization_dialog_identity = None
            app._normal_session_initialization_dialog_phase = None
        try:
            dlg.destroy()
        except Exception:
            pass

    def refresh() -> None:
        if not identity_current():
            close_dialog()
            return
        current = worker.normal_session_state()
        trust = worker.machine_trust_state()
        phase_var.set(f"Readiness phase: {current.phase.value}")
        missing = trust.missing_for_new_job()
        detail_var.set(
            str(current.reason or "")
            + ("\nMissing: " + ", ".join(missing) if missing else "")
        )

    def retry_sync() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.request_normal_session_state_sync(identity):
            messagebox.showwarning(
                "Job readiness",
                "State synchronization was not admitted for this session identity.",
                parent=dlg,
            )
        refresh()

    def home_machine() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.start_normal_session_homing(identity):
            messagebox.showwarning(
                "Home machine", "Normal-session homing did not start.", parent=dlg
            )
            return
        setattr(app, "_normal_session_home_requested_from_dialog", True)
        phase_var.set("Homing requested")
        detail_var.set("Waiting for current-session Home-to-Idle evidence.")

    if state.phase in {
        NormalSessionPhase.SYNCHRONIZING,
        NormalSessionPhase.FAILED,
    }:
        retry_button = ttk.Button(buttons, text="Retry State Sync", command=retry_sync)
        retry_button.pack(side="left", padx=(0, 6))
    if state.phase is NormalSessionPhase.POSITION_REQUIRED:
        home_button = ttk.Button(buttons, text="Home Now", command=home_machine)
        home_button.pack(side="left", padx=(0, 6))
    close_button_text = (
        "OK / I’ll Home Later"
        if state.phase is NormalSessionPhase.POSITION_REQUIRED
        else "Close"
    )
    close_button = ttk.Button(buttons, text=close_button_text, command=close_dialog)
    close_button.pack(side="right", padx=(6, 0))
    refresh()
    dlg.protocol("WM_DELETE_WINDOW", close_dialog)
    try:
        dlg.bind("<Escape>", lambda _event: close_dialog())
    except Exception:
        pass
    center_window(dlg, app)


__all__ = ["show_normal_session_initialization"]
