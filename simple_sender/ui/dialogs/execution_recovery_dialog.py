"""Explicit operator workflow for execution-uncertainty recovery."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from simple_sender.types import RecoveryPhase
from simple_sender.ui.dialogs.popup_utils import apply_toplevel_theme
from simple_sender.ui.dialogs.readiness_presentation import ReadinessPresentation, recovery_copy


def show_execution_recovery(app) -> None:
    # This operator-invoked modal must not capture input while screen lock is active.
    if bool(getattr(app, "_screen_lock_active", False)):
        return
    worker = app.grbl
    state = worker.recovery_state()
    if not state.required:
        messagebox.showinfo("Execution recovery", "No execution recovery is active.")
        return

    identity = state.action_identity
    existing = getattr(app, "_execution_recovery_dialog", None)
    existing_identity = getattr(app, "_execution_recovery_dialog_identity", None)
    existing_phase = getattr(app, "_execution_recovery_dialog_phase", None)
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
    app._execution_recovery_dialog = dlg
    app._execution_recovery_dialog_identity = identity
    app._execution_recovery_dialog_phase = state.phase
    dlg.transient(app)
    dlg.grab_set()
    apply_toplevel_theme(dlg, app)
    presentation = ReadinessPresentation(
        dlg, app,
        intro=(
            "The interrupted job cannot be resumed. Reset alone does not verify machine position, "
            "work offsets (including G92), tool-length compensation, tool reference, spindle or coolant state."
        ),
    )
    button_row = presentation.buttons

    def identity_current() -> bool:
        try:
            return worker.recovery_action_identity() == identity
        except Exception:
            return False

    def close_dialog() -> None:
        if getattr(app, "_execution_recovery_dialog", None) is dlg:
            app._execution_recovery_dialog = None
            app._execution_recovery_dialog_identity = None
            app._execution_recovery_dialog_phase = None
        try:
            dlg.destroy()
        except Exception:
            pass

    def refresh() -> None:
        if not identity_current():
            close_dialog()
            return
        current = worker.recovery_state()
        trust = worker.machine_trust_state()
        missing = trust.missing_for_new_job()
        title, message = recovery_copy(current, missing)
        presentation.update(
            title, message,
            f"Recovery phase: {current.phase.value}\n"
            f"Reason: {current.reason or 'Not reported'}\n"
            f"Missing: {', '.join(missing) or 'None reported'}\n"
            f"Session identity: {identity}",
        )

    def send_reset() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.request_recovery_reset(identity):
            messagebox.showwarning(
                "Execution recovery",
                "Reset was not sent. Recovery remains locked; the machine may still be executing.",
                parent=dlg,
            )
        refresh()

    def sync_state() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.request_recovery_state_sync(identity):
            messagebox.showwarning(
                "Execution recovery",
                "Controller synchronization could not start. Check the controller status and try again.",
                parent=dlg,
            )
        refresh()

    def home_machine() -> None:
        if not identity_current():
            close_dialog()
            return
        if not worker.start_recovery_homing(identity):
            messagebox.showwarning(
                "Execution recovery", "Recovery homing did not start.", parent=dlg
            )
        refresh()

    def accept_position() -> None:
        if not identity_current():
            close_dialog()
            return
        if not messagebox.askyesno(
            "Accept unverified position",
            "Use the current controller position without homing? This records position as "
            "operator-accepted, not machine-verified. Confirm only after physically checking it.",
            parent=dlg,
        ):
            return
        if not worker.accept_recovery_position(identity):
            messagebox.showwarning(
                "Execution recovery",
                "A fresh position report is not available for this recovery. Synchronize the controller and physically check the position before trying again.",
                parent=dlg,
            )
        refresh()

    def verify_spindle() -> None:
        if not identity_current():
            close_dialog()
            return
        if not messagebox.askyesno(
            "Verify spindle",
            "Confirm the synchronized controller mode is M5 and the spindle is physically stopped.",
            parent=dlg,
        ):
            return
        if not worker.acknowledge_recovery_spindle(identity):
            messagebox.showwarning(
                "Execution recovery",
                "Spindle verification requires the current synchronized mode to be M5.",
                parent=dlg,
            )
        refresh()

    def verify_coolant() -> None:
        if not identity_current():
            close_dialog()
            return
        if not messagebox.askyesno(
            "Verify coolant",
            "Confirm the synchronized controller mode is M9 and coolant outputs are physically off.",
            parent=dlg,
        ):
            return
        if not worker.acknowledge_recovery_coolant(identity):
            messagebox.showwarning(
                "Execution recovery",
                "Coolant verification requires the current synchronized mode to be M9.",
                parent=dlg,
            )
        refresh()

    def complete() -> None:
        if not identity_current():
            close_dialog()
            return
        accepted, missing = worker.complete_recovery(identity)
        if not accepted:
            messagebox.showwarning(
                "Recovery incomplete",
                "Complete these prerequisites first: " + ", ".join(missing),
                parent=dlg,
            )
            refresh()
            return
        close_dialog()

    def retry_finalization() -> None:
        if not identity_current():
            close_dialog()
            return
        retry = getattr(worker, "retry_recovery_finalization", None)
        if not callable(retry) or not bool(retry(identity)):
            messagebox.showwarning(
                "Execution recovery",
                "The verified state could not be applied for this recovery. Controls remain blocked; check the current status.",
                parent=dlg,
            )
        refresh()

    def disconnect_serial() -> None:
        if not identity_current():
            close_dialog()
            return
        if not messagebox.askyesno(
            "Disconnect serial communication",
            "Disconnecting serial communication is NOT an emergency stop. Buffered or "
            "currently executing controller work may continue after the serial link closes. "
            "Use the physical emergency stop or isolate machine power if motion or spindle "
            "operation may be hazardous.\n\nDisconnect this controller connection?",
            parent=dlg,
        ):
            return
        disconnect = getattr(worker, "disconnect_recovery", None)
        if not callable(disconnect) or not bool(disconnect(identity)):
            if identity_current():
                messagebox.showwarning(
                    "Execution recovery",
                    "The controller connection could not be closed. Recovery remains locked.",
                    parent=dlg,
                )
                refresh()
            else:
                close_dialog()
            return
        close_dialog()

    current_phase = state.phase
    if current_phase is RecoveryPhase.RESET_REQUIRED:
        ttk.Button(button_row, text="Send Reset", command=send_reset).grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    elif current_phase is RecoveryPhase.RESET_CONFIRMED_STATE_UNTRUSTED:
        for index, (text, command) in enumerate((
            ("Sync $G / $#", sync_state),
            ("Home Machine", home_machine),
            ("Accept Position", accept_position),
            ("Verify Spindle", verify_spindle),
            ("Verify Coolant", verify_coolant),
            ("Complete Recovery", complete),
        )):
            ttk.Button(button_row, text=text, command=command).grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
    elif current_phase is RecoveryPhase.RECOVERY_COMPLETE:
        ttk.Button(
            button_row,
            text="Retry Recovery Finalization",
            command=retry_finalization,
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
    ttk.Button(
        button_row,
        text="Disconnect Serial",
        command=disconnect_serial,
    ).grid(row=3, column=0, sticky="ew", padx=4, pady=(12, 4))
    close_button = ttk.Button(button_row, text="Close", command=close_dialog)
    close_button.grid(row=3, column=1, sticky="ew", padx=4, pady=(12, 4))
    dlg._refresh_presentation = refresh
    refresh()
    dlg.protocol("WM_DELETE_WINDOW", close_dialog)
    try:
        dlg.bind("<Escape>", lambda _event: close_dialog())
    except Exception:
        pass
    presentation.present()
    close_button.focus_set()


__all__ = ["show_execution_recovery"]
