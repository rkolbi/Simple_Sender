"""Explicit Job Ready initialization for a clean GRBL connection."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from simple_sender.types import NormalSessionPhase
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme
from simple_sender.ui.dialogs.readiness_presentation import ReadinessPresentation, normal_session_copy
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
            "No connection readiness checks are pending. Check the main-window status before operating.",
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
            existing._refresh_presentation()
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
    dlg.transient(app)
    apply_toplevel_theme(dlg, app)
    presentation = ReadinessPresentation(
        dlg, app,
        intro=f"Controller connection: {getattr(app, '_connected_port', '') or 'current port'}.",
    )
    buttons = presentation.buttons

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
        missing = trust.missing_for_new_job()
        title, message = normal_session_copy(current)
        presentation.update(
            title, message,
            f"Readiness phase: {current.phase.value}\n"
            f"Reason: {current.reason or 'Not reported'}\n"
            f"Missing: {', '.join(missing) or 'None reported'}\n"
            f"Session identity: {identity}",
        )

    def retry_sync() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.request_normal_session_state_sync(identity):
            messagebox.showwarning(
                "Job readiness",
                "Synchronization could not start for the current connection. Check the controller status and try again.",
                parent=dlg,
            )
        refresh()

    def home_machine() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.start_normal_session_homing(identity):
            messagebox.showwarning(
                "Home machine", "Homing could not start. Check the controller status before trying again.", parent=dlg
            )
            return
        setattr(app, "_normal_session_home_requested_from_dialog", True)
        refresh()

    if state.phase in {
        NormalSessionPhase.SYNCHRONIZING,
        NormalSessionPhase.FAILED,
    }:
        retry_button = ttk.Button(buttons, text="Retry State Sync", command=retry_sync)
        retry_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
    if state.phase is NormalSessionPhase.POSITION_REQUIRED:
        home_button = ttk.Button(buttons, text="Home Now", command=home_machine)
        home_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
    close_button_text = (
        "OK / I’ll Home Later"
        if state.phase is NormalSessionPhase.POSITION_REQUIRED
        else "Close"
    )
    close_button = ttk.Button(buttons, text=close_button_text, command=close_dialog)
    close_button.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=4)
    dlg._refresh_presentation = refresh
    refresh()
    dlg.protocol("WM_DELETE_WINDOW", close_dialog)
    try:
        dlg.bind("<Escape>", lambda _event: close_dialog())
    except Exception:
        pass
    presentation.present()
    close_button.focus_set()


__all__ = ["show_normal_session_initialization"]
