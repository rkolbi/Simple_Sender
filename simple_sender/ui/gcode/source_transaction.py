"""Identity checks for UI installation of worker-owned G-code sources."""

from __future__ import annotations

from simple_sender.types import GcodeSourceIdentity


class StaleGcodeSourceTransaction(RuntimeError):
    """Raised when a callback no longer belongs to the active source load."""


def next_ui_load_generation(app) -> int:
    generation = int(getattr(app, "_gcode_source_ui_generation", 0)) + 1
    app._gcode_source_ui_generation = generation
    return generation


def source_transaction_is_current(
    app,
    identity: GcodeSourceIdentity,
    ui_generation: int,
    *,
    require_committed: bool = False,
) -> bool:
    if int(getattr(app, "_gcode_source_ui_generation", 0)) != int(ui_generation):
        return False
    if int(identity.ui_load_generation) != int(ui_generation):
        return False
    if getattr(app, "_worker_gcode_source_identity", None) != identity:
        return False
    current_identity = getattr(app.grbl, "current_gcode_source_identity", None)
    if not callable(current_identity) or current_identity() != identity:
        return False
    checker = getattr(app.grbl, "gcode_source_is_current", None)
    if not callable(checker):
        return False
    return bool(checker(identity, require_committed=require_committed))


def require_current_source_transaction(
    app,
    identity: GcodeSourceIdentity,
    ui_generation: int,
    *,
    require_committed: bool = False,
) -> None:
    if not source_transaction_is_current(
        app,
        identity,
        ui_generation,
        require_committed=require_committed,
    ):
        raise StaleGcodeSourceTransaction(
            "G-code source transaction no longer matches the worker and UI generations"
        )


def reserve_source_transaction(app, payload, *, name: str | None):
    ui_generation = next_ui_load_generation(app)
    admit_source = getattr(app.grbl, "admit_gcode_source", None)
    if not callable(admit_source):
        raise RuntimeError("Worker does not provide transactional G-code source admission")
    admission = admit_source(
        payload,
        name=name,
        ui_load_generation=ui_generation,
    )
    if not bool(getattr(admission, "accepted", False)):
        return ui_generation, admission
    identity = admission.identity
    if int(identity.ui_load_generation) != ui_generation:
        raise RuntimeError("Worker returned a mismatched G-code UI load generation")
    app._worker_gcode_source_identity = identity
    require_current_source_transaction(app, identity, ui_generation)
    return ui_generation, admission


def commit_source_transaction(
    app,
    identity: GcodeSourceIdentity,
    ui_generation: int,
) -> bool:
    require_current_source_transaction(app, identity, ui_generation)
    commit_source = getattr(app.grbl, "commit_gcode_source", None)
    if not callable(commit_source) or not bool(commit_source(identity)):
        return False
    require_current_source_transaction(
        app,
        identity,
        ui_generation,
        require_committed=True,
    )
    return True


def abort_source_transaction(
    app,
    identity: GcodeSourceIdentity,
    ui_generation: int,
    *,
    reason: str,
) -> bool:
    if int(getattr(app, "_gcode_source_ui_generation", 0)) != int(ui_generation):
        return False
    abort_source = getattr(app.grbl, "abort_gcode_source", None)
    if not callable(abort_source):
        return False
    aborted = bool(abort_source(identity, reason=reason))
    if aborted:
        current_identity = getattr(app.grbl, "current_gcode_source_identity", None)
        if callable(current_identity):
            app._worker_gcode_source_identity = current_identity()
    return aborted
