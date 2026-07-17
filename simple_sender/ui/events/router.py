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

import os
import math
import queue
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import time
from typing import Any, cast
from tkinter import messagebox, TclError

from .status import (
    _parse_modal_units,
    _parse_report_units_setting,
    handle_status_event,
)
from . import streaming as _event_router_streaming
from simple_sender.ui.grbl_lifecycle import handle_connection_event, handle_ready_event
from simple_sender.ui.autolevel_state import (
    clear_active_auto_level_map,
    clear_active_auto_level_map_if_owned,
)
from simple_sender.ui.job_setup_state import invalidate_job_setup_state
from simple_sender.ui.job_controls import (
    disable_job_controls,
    job_controls_ready,
    set_run_resume_from,
)
from simple_sender.ui.dialogs.error_dialogs_ui import show_grbl_code_popup
from simple_sender.utils.task_timing import record_task_timing
from simple_sender.utils.constants import MAX_LINE_LENGTH
from simple_sender.utils.grbl_errors import annotate_grbl_alarm, annotate_grbl_error
from simple_sender.types import (
    AlarmEvent,
    AutoLevelMapProvenance,
    ConnectionScopedEvent,
    ConnectionEvent,
    ExecutionRecoveryState,
    GcodeAckedEvent,
    GcodeSentEvent,
    NormalSessionInitializationEvent,
    NormalSessionInitializationState,
    NormalSessionPhase,
    ProgressBytesEvent,
    ProgressEvent,
    ReadyEvent,
    RecoveryCompleteEvent,
    RecoveryFinalizationIdentity,
    RecoveryPhase,
    RecoveryRequiredEvent,
    RecoveryStateSnapshot,
    SettingsDumpDoneEvent,
    StatusEvent,
    StreamCompletionEofEvent,
    StreamErrorEvent,
    StreamInterruptedEvent,
    StreamPauseReasonEvent,
    StreamStateEvent,
    StreamToolChangeIdentity,
    UiEvent,
)

logger = logging.getLogger(__name__)
_GCODE_LOADED_STREAM_APPLY_BUDGET_MS = 15.0
_GCODE_LOADED_STREAM_APPLY_WARN_MS = 50.0
_MANUAL_ERROR_HANDLER_BUDGET_MS = 15.0
_MANUAL_ERROR_COALESCE_WINDOW_S = 0.75
_STREAM_ERROR_LINE_TEXT_MAX_CHARS = 240
_STREAM_ERROR_33_HINT = (
    "Likely arc precision/tolerance issue (error:33). "
    "Increase VCarve inch-post X/Y and I/J precision (1.4-1.5) or output arcs as segments."
)


def _worker_recovery_required(app: Any) -> bool:
    checker = getattr(getattr(app, "grbl", None), "recovery_required", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return True


def _invalidate_machine_snapshot_caches(app: Any) -> None:
    """Make a partial or retired snapshot unusable by UI workflows."""
    invalidate_job_setup_state(app)
    app._recovery_snapshot = None
    app._wco_raw = None
    app._mpos_raw = None
    app._wpos_raw = None
    app._status_installed_coordinate_signature = None
    app._status_last_installed_coordinate_ts = 0.0
    status_coords_event = getattr(app, "_status_coords_update_event", None)
    if status_coords_event is not None:
        try:
            status_coords_event.clear()
        except Exception:
            pass
    app._wcs_offsets_snapshot = ()
    app._g92_raw = None
    app._tlo_value = None
    app._tlo_mode = None
    for attr in (
        "_machine_coordinates_trusted",
        "_modal_state_trusted",
        "_work_offsets_trusted",
        "_g92_trusted",
        "_tool_length_offset_trusted",
        "_spindle_state_trusted",
        "_coolant_state_trusted",
    ):
        setattr(app, attr, False)
    macro_executor = getattr(app, "macro_executor", None)
    macro_vars_context = getattr(macro_executor, "macro_vars", None)
    if callable(macro_vars_context):
        try:
            with macro_vars_context() as macro_vars:
                for key in (
                    "_position_trusted",
                    "_modal_trusted",
                    "_wcs_trusted",
                    "_g92_trusted",
                    "_tlo_trusted",
                    "_spindle_trusted",
                    "_coolant_trusted",
                ):
                    macro_vars[key] = False
                for key in (
                    "units",
                    "distance",
                    "plane",
                    "feedmode",
                    "arc",
                    "motion",
                    "WCS",
                    "wcs_offsets",
                    "spindle",
                    "coolant",
                    "feed",
                    "curfeed",
                    "spindle_speed",
                    "curspindle",
                    "tool",
                    "selected_tool",
                    "g92x",
                    "g92y",
                    "g92z",
                    "mx",
                    "my",
                    "mz",
                    "wcox",
                    "wcoy",
                    "wcoz",
                    "wx",
                    "wy",
                    "wz",
                    "tlo",
                    "tlo_mode",
                ):
                    macro_vars[key] = ""
        except Exception as exc:
            _log_suppressed("Failed clearing retired macro snapshot cache", exc)


def _install_machine_snapshot_caches(
    app: Any,
    snapshot: RecoveryStateSnapshot,
) -> None:
    """Install one immutable worker-approved machine-state snapshot."""
    trust = snapshot.to_trust_state(setup_tool_reference=False)
    if trust.missing_for_new_job():
        raise RuntimeError("Approved machine-state snapshot is incomplete")
    machine_position = snapshot.machine_position
    work_coordinate_offset = snapshot.work_coordinate_offset
    g92 = snapshot.g92
    if machine_position is None or work_coordinate_offset is None or g92 is None:
        raise RuntimeError("Approved machine-state snapshot lacks coordinate values")
    derived_work_position = tuple(
        float(machine_position[idx]) - float(work_coordinate_offset[idx])
        for idx in range(3)
    )
    if not all(math.isfinite(value) for value in derived_work_position):
        raise RuntimeError("Approved snapshot derives a non-finite work position")

    modal_units = "inch" if snapshot.modal_units == "G20" else "mm"
    app._modal_units = modal_units
    app._set_unit_mode(modal_units)
    app._recovery_snapshot = snapshot
    app._wcs_offsets_snapshot = tuple(snapshot.wcs_offsets)
    app._g92_raw = tuple(g92)
    app._tlo_value = snapshot.tlo
    app._tlo_mode = snapshot.tlo_mode
    app._wco_raw = work_coordinate_offset
    app._mpos_raw = machine_position
    app._wpos_raw = derived_work_position
    macro_values: dict[str, object] = {
        "units": snapshot.modal_units,
        "distance": snapshot.distance_mode,
        "plane": snapshot.plane,
        "feedmode": snapshot.feed_mode,
        "arc": snapshot.arc_distance_mode or "",
        "motion": snapshot.motion_mode or "",
        "WCS": snapshot.active_wcs,
        "wcs_offsets": tuple(snapshot.wcs_offsets),
        "spindle": snapshot.spindle_mode,
        "coolant": "+".join(snapshot.coolant_modes),
        "feed": snapshot.feed_rate,
        "curfeed": snapshot.feed_rate,
        "spindle_speed": snapshot.spindle_speed,
        "curspindle": snapshot.spindle_speed,
        "tool": str(snapshot.current_tool_number),
        "selected_tool": str(snapshot.selected_tool_number),
        "tlo": snapshot.tlo,
        "tlo_mode": snapshot.tlo_mode,
        "g92x": g92[0],
        "g92y": g92[1],
        "g92z": g92[2],
        "mx": machine_position[0],
        "my": machine_position[1],
        "mz": machine_position[2],
        "wcox": work_coordinate_offset[0],
        "wcoy": work_coordinate_offset[1],
        "wcoz": work_coordinate_offset[2],
        "wx": derived_work_position[0],
        "wy": derived_work_position[1],
        "wz": derived_work_position[2],
    }
    with app.macro_executor.macro_vars() as macro_vars:
        macro_vars.update(macro_values)
        macro_vars["_position_trusted"] = bool(trust.machine_position)
        macro_vars["_modal_trusted"] = bool(trust.modal_state)
        macro_vars["_wcs_trusted"] = bool(trust.work_coordinates)
        macro_vars["_g92_trusted"] = bool(trust.g92)
        macro_vars["_tlo_trusted"] = bool(trust.tool_length_offset)
        macro_vars["_spindle_trusted"] = bool(trust.spindle_state)
        macro_vars["_coolant_trusted"] = bool(trust.coolant_state)
    app._machine_coordinates_trusted = bool(trust.machine_position)
    app._modal_state_trusted = bool(trust.modal_state)
    app._work_offsets_trusted = bool(trust.work_coordinates)
    app._g92_trusted = bool(trust.g92)
    app._tool_length_offset_trusted = bool(trust.tool_length_offset)
    app._spindle_state_trusted = bool(trust.spindle_state)
    app._coolant_state_trusted = bool(trust.coolant_state)
    refresher = getattr(app, "_refresh_dro_display", None)
    if callable(refresher):
        refresher()


def _settings_snapshot_available(app: Any) -> bool:
    controller = getattr(app, "settings_controller", None)
    data = getattr(controller, "_settings_data", None)
    return bool(data)


def _settings_capture_active(app: Any) -> bool:
    controller = getattr(app, "settings_controller", None)
    return bool(getattr(controller, "_settings_capture", False))


def _normal_session_readiness_prompt_acknowledged(
    app: Any,
    state: NormalSessionInitializationState,
) -> bool:
    return getattr(
        app,
        "_normal_session_initialization_acknowledged_identity",
        None,
    ) == state.action_identity


def _normal_session_readiness_prompt_allowed(
    app: Any,
    state: NormalSessionInitializationState,
) -> bool:
    return (
        state.phase is NormalSessionPhase.POSITION_REQUIRED
        and not bool(getattr(state, "homing_started", False))
        and not _normal_session_readiness_prompt_acknowledged(app, state)
    )


def _request_normal_session_settings_snapshot(app: Any) -> None:
    if _settings_snapshot_available(app) or _settings_capture_active(app):
        return
    app._pending_settings_refresh = True
    requester = getattr(app, "_request_settings_dump", None)
    if not callable(requester):
        return
    try:
        accepted = requester()
    except Exception as exc:
        _log_suppressed("Failed requesting startup GRBL settings snapshot", exc)
        return
    if accepted is False:
        app._pending_settings_refresh = True
    elif accepted is True:
        app._pending_settings_refresh = False


def _show_deferred_normal_session_initialization_if_ready(app: Any) -> None:
    if not bool(
        getattr(app, "_normal_session_readiness_prompt_deferred_for_settings", False)
    ):
        return
    state = getattr(app, "_normal_session_initialization_state", None)
    if state is None:
        return
    try:
        if app.grbl.normal_session_state() != state:
            return
    except Exception:
        return
    if not _normal_session_readiness_prompt_allowed(app, state):
        app._normal_session_readiness_prompt_deferred_for_settings = False
        return
    if not _settings_snapshot_available(app):
        return
    if getattr(app, "_normal_session_initialization_dialog", None) is not None:
        return
    app._normal_session_readiness_prompt_deferred_for_settings = False
    try:
        app.status.config(
            text="Connected. Machine information obtained. Use Home to complete Job Ready."
        )
    except Exception as exc:
        _log_suppressed("Failed updating normal-session settings-ready status", exc)
    try:
        from simple_sender.ui.dialogs.normal_session_initialization_dialog import (
            show_normal_session_initialization,
        )

        show_normal_session_initialization(app)
    except Exception as exc:
        _log_suppressed("Failed presenting normal-session initialization", exc)


def _handle_normal_session_initialization_event(
    app: Any,
    state: NormalSessionInitializationState,
    snapshot: RecoveryStateSnapshot | None,
) -> None:
    """Project worker-owned Communication Ready versus Job Ready state."""
    try:
        if app.grbl.normal_session_state() != state:
            return
    except Exception:
        return
    app._normal_session_initialization_state = state
    dialog = getattr(app, "_normal_session_initialization_dialog", None)
    dialog_identity = getattr(
        app, "_normal_session_initialization_dialog_identity", None
    )
    dialog_phase = getattr(app, "_normal_session_initialization_dialog_phase", None)
    if dialog is not None and (
        dialog_identity != state.action_identity or dialog_phase != state.phase
    ):
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed retiring stale normal-session dialog", exc)
        app._normal_session_initialization_dialog = None
        app._normal_session_initialization_dialog_identity = None
        app._normal_session_initialization_dialog_phase = None
    app._pending_modal_sync = False
    app._modal_sync_inflight = False
    app._modal_sync_inflight_started_ts = 0.0
    if state.phase is NormalSessionPhase.SNAPSHOT_INSTALL_PENDING:
        disable_job_controls(app)
        app._set_manual_controls_enabled(False)
        identity = state.action_identity
        try:
            if snapshot is None:
                raise RuntimeError("Worker did not publish an approved snapshot")
            if (
                int(snapshot.connection_generation) != int(state.connection_generation)
                or int(snapshot.recovery_epoch) != int(state.recovery_epoch)
            ):
                raise RuntimeError("Normal-session snapshot provenance is stale")
            logger.info(
                "Installing normal-session snapshot in UI: generation=%s "
                "recovery_epoch=%s snapshot_id=0x%x",
                snapshot.connection_generation,
                snapshot.recovery_epoch,
                id(snapshot),
            )
            _invalidate_machine_snapshot_caches(app)
            _install_machine_snapshot_caches(app, snapshot)
            app.status.config(
                text=(
                    "Current-session state installed; waiting for worker Job Ready "
                    "confirmation."
                )
            )
            finalizer = getattr(
                app.grbl, "finalize_normal_session_snapshot_install", None
            )
            if not callable(finalizer) or not bool(finalizer(identity, snapshot)):
                raise RuntimeError("Worker rejected the snapshot installation acknowledgement")
            logger.info(
                "Normal-session snapshot installation acknowledged: generation=%s "
                "recovery_epoch=%s snapshot_id=0x%x",
                snapshot.connection_generation,
                snapshot.recovery_epoch,
                id(snapshot),
            )
        except Exception as exc:
            _invalidate_machine_snapshot_caches(app)
            failer = getattr(app.grbl, "fail_normal_session_snapshot_install", None)
            if snapshot is not None and callable(failer):
                try:
                    failer(
                        identity,
                        snapshot,
                        reason=(
                            "Current-session snapshot installation failed; synchronize "
                            "again before Job Ready."
                        ),
                    )
                except Exception as fail_exc:
                    _log_suppressed(
                        "Failed retiring rejected normal-session snapshot", fail_exc
                    )
            try:
                app.status.config(
                    text=(
                        "Job Ready blocked: current-session state installation failed; "
                        "synchronize again."
                    )
                )
            except Exception as status_exc:
                _log_suppressed(
                    "Failed displaying normal-session snapshot rejection", status_exc
                )
            _log_suppressed("Normal-session snapshot installation failed", exc)
        return
    if state.phase is NormalSessionPhase.READY:
        try:
            from simple_sender.ui.dialogs.startup_connection_dialog import (
                close_startup_connection_dialog,
            )

            close_startup_connection_dialog(app)
        except Exception as exc:
            _log_suppressed("Failed retiring startup connection dialog", exc)
        app.status.config(text="Job Ready: current-session machine state is established.")
        app._set_manual_controls_enabled(True)
        set_run_resume_from(app, job_controls_ready(app))
        if bool(getattr(app, "_normal_session_home_requested_from_dialog", False)):
            app._normal_session_home_requested_from_dialog = False
            try:
                messagebox.showinfo("Job Ready", "Machine homed. Job Ready.", parent=app)
            except Exception as exc:
                _log_suppressed("Failed displaying Job Ready confirmation", exc)
        try:
            app._update_recover_button_visibility()
            if not bool(getattr(app, "_alarm_locked", False)):
                app.btn_alarm_recover.config(state="disabled")
        except Exception as exc:
            _log_suppressed("Failed retiring normal-session initialization action", exc)
        return
    disable_job_controls(app)
    app._set_manual_controls_enabled(False)
    if state.phase is NormalSessionPhase.POSITION_REQUIRED:
        if _settings_snapshot_available(app):
            app._normal_session_readiness_prompt_deferred_for_settings = False
            app.status.config(
                text="Connected. Machine information obtained. Use Home to complete Job Ready."
            )
        else:
            app._normal_session_readiness_prompt_deferred_for_settings = True
            app.status.config(
                text="Connected. Retrieving GRBL settings before readiness prompt."
            )
            _request_normal_session_settings_snapshot(app)
    else:
        app._normal_session_readiness_prompt_deferred_for_settings = False
        app.status.config(text=str(state.reason or "Communication Ready; Job Ready is pending."))
    try:
        machine_text = "COMMUNICATION READY - JOB INITIALIZATION REQUIRED"
        app.machine_state.set(machine_text)
        app._machine_state_text = machine_text
    except Exception as exc:
        _log_suppressed("Failed setting normal-session machine-state label", exc)
    try:
        app._update_recover_button_visibility()
        app.btn_alarm_recover.config(state="normal")
    except Exception as exc:
        _log_suppressed("Failed exposing normal-session initialization action", exc)
    if (
        _normal_session_readiness_prompt_allowed(app, state)
        and _settings_snapshot_available(app)
        and getattr(app, "_normal_session_initialization_dialog", None) is None
    ):
        try:
            from simple_sender.ui.dialogs.normal_session_initialization_dialog import (
                show_normal_session_initialization,
            )

            show_normal_session_initialization(app)
        except Exception as exc:
            _log_suppressed("Failed presenting normal-session initialization", exc)


def _event_is_from_stale_connection(app: Any, evt: UiEvent) -> bool:
    generation = getattr(evt, "generation", None)
    if generation is None:
        return False
    getter = getattr(getattr(app, "grbl", None), "connection_generation", None)
    if not callable(getter):
        return False
    try:
        if int(generation) != int(getter()):
            return True
        worker = getattr(app, "grbl", None)
        recovery_epoch = getattr(evt, "recovery_epoch", None)
        recovery_getter = getattr(worker, "recovery_epoch", None)
        if recovery_epoch is not None and callable(recovery_getter):
            if int(recovery_epoch) != int(recovery_getter()):
                return True
        stream_epoch = getattr(evt, "stream_epoch", None)
        stream_getter = getattr(worker, "stream_epoch", None)
        if stream_epoch is not None and callable(stream_getter):
            if int(stream_epoch) != int(stream_getter()):
                return True
        reset_attempt_id = getattr(evt, "reset_attempt_id", None)
        if reset_attempt_id is not None:
            state_getter = getattr(worker, "recovery_state", None)
            if callable(state_getter):
                if state_getter().reset_attempt_id != reset_attempt_id:
                    return True
        if isinstance(evt, RecoveryRequiredEvent) and not _worker_recovery_required(app):
            return True
        if isinstance(evt, RecoveryRequiredEvent):
            state_getter = getattr(worker, "recovery_state", None)
            if callable(state_getter):
                current_state = state_getter()
                if (
                    current_state.phase != evt.state.phase
                    or current_state.reset_attempt_id != evt.state.reset_attempt_id
                ):
                    return True
        if (
            isinstance(evt, StreamStateEvent)
            and str(evt.state).strip().lower() == "recovery_required"
            and not _worker_recovery_required(app)
        ):
            return True
        return False
    except Exception:
        return True


def _event_blocked_by_recovery(app: Any, evt: UiEvent) -> bool:
    if not _worker_recovery_required(app):
        return False
    if isinstance(evt, ConnectionScopedEvent):
        return _event_blocked_by_recovery(app, cast(UiEvent, evt.payload))
    if isinstance(evt, (RecoveryRequiredEvent, RecoveryCompleteEvent)):
        return False
    if isinstance(evt, ReadyEvent):
        return bool(evt.is_ready)
    if isinstance(evt, StreamStateEvent):
        return str(evt.state).strip().lower() != "recovery_required"
    if isinstance(
        evt,
        (
            StatusEvent,
            GcodeSentEvent,
            GcodeAckedEvent,
            ProgressEvent,
            ProgressBytesEvent,
            StreamCompletionEofEvent,
            StreamPauseReasonEvent,
        ),
    ):
        return True
    if isinstance(evt, tuple) and evt:
        kind = str(evt[0])
        if kind == "recovery_complete":
            return False
        if kind == "ready":
            return len(evt) > 1 and bool(evt[1])
        if kind == "stream_state":
            return len(evt) < 2 or str(evt[1]).strip().lower() != "recovery_required"
        return kind in {
            "status",
            "gcode_sent",
            "gcode_acked",
            "progress",
            "progress_bytes",
            "stream_completion_eof",
            "stream_pause_reason",
            "stream_vacuum_directive",
            "stream_tool_change",
            "spindle_state",
        }
    return False


_JOG_LIMIT_ERROR_HINT = (
    "Jog blocked by travel limits (error:15). "
    "Move away from axis limits or verify homing and $130-$132."
)


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc)


def _cleanup_streaming_source(source: Any, *, context: str) -> None:
    cleanup_path = getattr(source, "_cleanup_path", None) if source is not None else None
    if source is not None:
        try:
            source.close()
        except (OSError, RuntimeError, ValueError) as exc:
            _log_suppressed(f"{context}: failed closing streaming source", exc)
    if cleanup_path:
        try:
            os.remove(cleanup_path)
        except OSError as exc:
            _log_suppressed(f"{context}: failed removing streamed temp file", exc)


def _signal_gcode_load_result(
    app: Any,
    *,
    token: int,
    success: bool,
    path: str | None = None,
    error: str | None = None,
) -> None:
    app._gcode_load_last_result_token = int(token)
    app._gcode_load_last_result_success = bool(success)
    app._gcode_load_last_result_error = str(error or "")
    app._gcode_load_last_result_path = str(path or "")
    evt = getattr(app, "_gcode_load_result_event", None)
    if evt is not None and hasattr(evt, "set"):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed("Failed signaling G-code load result event", exc)


def _loaded_stream_signature(
    *,
    path: str,
    lines_hash: str | None,
    total_lines: int | None,
    sample_only: bool,
) -> tuple[str, str, int, bool]:
    safe_total = 0
    if total_lines is not None:
        try:
            safe_total = max(0, int(total_lines))
        except Exception:
            safe_total = 0
    return (
        str(path or ""),
        str(lines_hash or ""),
        safe_total,
        bool(sample_only),
    )


def _loaded_stream_is_noop(app: Any, signature: tuple[str, str, int, bool]) -> bool:
    if str(getattr(app, "_stream_state", "") or "").strip().lower() != "loaded":
        return False
    current = _loaded_stream_signature(
        path=str(getattr(app, "_last_gcode_path", "") or ""),
        lines_hash=str(getattr(app, "_gcode_hash", "") or ""),
        total_lines=getattr(app, "_gcode_total_lines", 0),
        sample_only=bool(getattr(app, "_gcode_streaming_mode", False)),
    )
    return current == signature


def _queue_loaded_stream_apply(
    app: Any,
    *,
    token: int,
    signature: tuple[str, str, int, bool],
    path: str,
    source: Any,
    sample_lines: list[str],
    lines_hash: str | None,
    total_lines: int | None,
    report: Any,
    sample_only: bool,
) -> None:
    generation = int(getattr(app, "_gcode_loaded_stream_apply_generation", 0) or 0) + 1
    app._gcode_loaded_stream_apply_generation = generation
    pending = getattr(app, "_gcode_loaded_stream_pending", None)
    if pending and isinstance(pending, tuple) and len(pending) >= 4:
        stale_source = pending[4]
        if stale_source is not source:
            logger.info(
                "[ui] gcode_loaded_stream coalesced: job=%s hash=%s reason=pending_apply_replaced",
                signature[0] or "<none>",
                signature[1] or "<none>",
            )
            _cleanup_streaming_source(stale_source, context="Coalesced gcode_loaded_stream apply")
    app._gcode_loaded_stream_pending = (
        generation,
        token,
        signature,
        path,
        source,
        sample_lines,
        lines_hash,
        total_lines,
        report,
        sample_only,
    )
    _schedule_loaded_stream_apply(app)


def _cancel_load_settling_clear_timer(app: Any) -> None:
    after_id = getattr(app, "_gcode_load_settling_after_id", None)
    if after_id is None:
        return
    cancel_fn = getattr(app, "after_cancel", None)
    if callable(cancel_fn):
        try:
            cancel_fn(after_id)
        except Exception as exc:
            _log_suppressed("Failed canceling load-settling clear timer", exc)
    app._gcode_load_settling_after_id = None


def _set_load_settling(app: Any, enabled: bool, *, generation: int | None = None) -> None:
    if enabled:
        _cancel_load_settling_clear_timer(app)
        app._gcode_load_settling = True
        app._gcode_load_settling_generation = int(generation or 0)
        try:
            app._gcode_load_settling_started_at = time.monotonic()
        except Exception:
            app._gcode_load_settling_started_at = 0.0
        return
    _cancel_load_settling_clear_timer(app)
    app._gcode_load_settling = False
    app._gcode_load_settling_generation = 0
    app._status_settling_last_state = ""
    app._status_settling_last_apply_ts = 0.0
    app._status_settling_drop_count = 0


def _schedule_load_settling_clear(app: Any, *, generation: int) -> None:
    _cancel_load_settling_clear_timer(app)
    try:
        delay_ms = int(getattr(app, "_gcode_load_settling_tail_ms", 1200))
    except Exception:
        delay_ms = 1200
    delay_ms = max(0, delay_ms)

    def _clear_when_idle() -> None:
        app._gcode_load_settling_after_id = None
        if int(getattr(app, "_gcode_load_settling_generation", 0) or 0) != int(generation):
            return
        if getattr(app, "_gcode_loaded_stream_apply_after_id", None) is not None:
            _schedule_load_settling_clear(app, generation=generation)
            return
        if getattr(app, "_gcode_loaded_stream_pending", None):
            _schedule_load_settling_clear(app, generation=generation)
            return
        started_at = float(getattr(app, "_gcode_load_settling_started_at", 0.0) or 0.0)
        duration_ms = 0.0
        if started_at > 0.0:
            try:
                duration_ms = max(0.0, (time.monotonic() - started_at) * 1000.0)
            except Exception:
                duration_ms = 0.0
        pending = 0
        ui_q = getattr(app, "ui_q", None)
        if ui_q is not None and hasattr(ui_q, "qsize"):
            try:
                pending = max(0, int(ui_q.qsize()))
            except Exception:
                pending = 0
        logger.info(
            "[ui] load_settling OFF gen=%d duration_ms=%.2f tab=%s pending=%d",
            int(generation),
            duration_ms,
            str(getattr(app, "_active_tab_label", "") or "unknown"),
            int(pending),
        )
        _set_load_settling(app, False)
        if bool(getattr(app, "_stream_loaded_force_apply", False)):
            try:
                app.ui_q.put(("stream_state", "loaded", int(getattr(app, "_gcode_total_lines", 0) or 0)))
            except Exception as exc:
                _log_suppressed("Failed queueing loaded stream-state reconciliation after load settling", exc)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._gcode_load_settling_after_id = after_fn(delay_ms, _clear_when_idle)
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling load-settling clear callback", exc)
    _clear_when_idle()


def _schedule_loaded_stream_apply(app: Any) -> None:
    if getattr(app, "_gcode_loaded_stream_apply_after_id", None) is not None:
        return

    def _run_pending() -> None:
        app._gcode_loaded_stream_apply_after_id = None
        payload = getattr(app, "_gcode_loaded_stream_pending", None)
        app._gcode_loaded_stream_pending = None
        if not payload:
            return
        (
            generation,
            token,
            signature,
            path,
            source,
            sample_lines,
            lines_hash,
            total_lines,
            report,
            sample_only,
        ) = payload
        _set_load_settling(app, True, generation=generation)
        started_at = time.perf_counter()
        slice_count = 0
        max_slice_ms = 0.0
        slowest_phase = "none"
        slowest_phase_ms = 0.0
        source_consumed = False

        def _finalize_metrics(*, aborted: bool, reason: str) -> None:
            total_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            app._gcode_loaded_stream_apply_last_metrics = {
                "job": signature[0],
                "hash": signature[1],
                "generation": int(generation),
                "slices": int(slice_count),
                "total_ms": float(total_ms),
                "max_slice_ms": float(max_slice_ms),
                "slowest_phase": str(slowest_phase),
                "slowest_phase_ms": float(slowest_phase_ms),
                "aborted": bool(aborted),
                "reason": str(reason),
            }
            logger.info(
                "[ui] gcode_loaded_stream apply metrics: job=%s hash=%s gen=%d slices=%d total=%.2fms "
                "max_slice=%.2fms slowest=%s(%.2fms) aborted=%s reason=%s",
                signature[0] or "<none>",
                signature[1] or "<none>",
                int(generation),
                int(slice_count),
                total_ms,
                max_slice_ms,
                slowest_phase,
                slowest_phase_ms,
                bool(aborted),
                reason,
            )

        def _phase_prepare() -> None:
            app._gcode_validation_report = report

        def _phase_apply() -> None:
            nonlocal source_consumed
            apply_fn = getattr(app, "_apply_loaded_gcode", None)
            if not callable(apply_fn):
                raise RuntimeError("Missing _apply_loaded_gcode handler")
            apply_fn(
                path,
                sample_lines,
                lines_hash=lines_hash,
                validated=True,
                streaming_source=source,
                total_lines=total_lines,
                sample_only=sample_only,
                defer_viewer_stage_apply=True,
            )
            source_consumed = True

        def _phase_finalize() -> None:
            app._gcode_loaded_stream_last_signature = signature
            _signal_gcode_load_result(app, token=token, success=True, path=path)
            perf_monitor = getattr(app, "_perf_monitor", None)
            if perf_monitor is not None:
                try:
                    perf_monitor.note_file_loaded()
                except Exception as exc:
                    _log_suppressed("Failed forwarding streamed-file milestone to performance monitor", exc)
            _schedule_load_settling_clear(app, generation=generation)

        phases: list[tuple[str, Any]] = [
            ("prepare", _phase_prepare),
            ("apply", _phase_apply),
            ("finalize", _phase_finalize),
        ]

        def _run_phase(phase_index: int) -> None:
            nonlocal slice_count, max_slice_ms, slowest_phase, slowest_phase_ms, source_consumed
            app._gcode_loaded_stream_apply_after_id = None
            current_generation = int(getattr(app, "_gcode_loaded_stream_apply_generation", 0) or 0)
            if int(generation) != current_generation:
                if not source_consumed:
                    _cleanup_streaming_source(source, context="Canceled gcode_loaded_stream apply")
                _finalize_metrics(aborted=True, reason="coalesced_by_newer_load")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            if phase_index >= len(phases):
                _finalize_metrics(aborted=False, reason="ok")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            phase_name, phase_fn = phases[phase_index]
            phase_started_at = time.perf_counter()
            try:
                phase_fn()
            except Exception as exc:
                _log_suppressed(f"Deferred gcode_loaded_stream {phase_name} phase failed", exc)
                if not source_consumed:
                    _cleanup_streaming_source(
                        source,
                        context=f"Deferred gcode_loaded_stream {phase_name} failure",
                    )
                _fail_gcode_load(
                    app,
                    token=token,
                    path=path,
                    err=str(exc),
                    dialog_prefix="Failed to load file",
                )
                _set_load_settling(app, False)
                _finalize_metrics(aborted=True, reason=f"phase_failed:{phase_name}")
                if getattr(app, "_gcode_loaded_stream_pending", None):
                    _schedule_loaded_stream_apply(app)
                return
            phase_elapsed_ms = max(0.0, (time.perf_counter() - phase_started_at) * 1000.0)
            slice_count += 1
            if phase_elapsed_ms > max_slice_ms:
                max_slice_ms = phase_elapsed_ms
            if phase_elapsed_ms > slowest_phase_ms:
                slowest_phase_ms = phase_elapsed_ms
                slowest_phase = str(phase_name)
            if phase_elapsed_ms > _GCODE_LOADED_STREAM_APPLY_BUDGET_MS:
                level_log = logger.warning if phase_elapsed_ms >= _GCODE_LOADED_STREAM_APPLY_WARN_MS else logger.info
                level_log(
                    "[ui] gcode_loaded_stream slice exceeded %.1fms budget: phase=%s %.2fms",
                    _GCODE_LOADED_STREAM_APPLY_BUDGET_MS,
                    str(phase_name),
                    phase_elapsed_ms,
                )
            after_fn = getattr(app, "after", None)
            if callable(after_fn):
                try:
                    app._gcode_loaded_stream_apply_after_id = after_fn(
                        0,
                        lambda idx=phase_index + 1: _run_phase(idx),
                    )
                    return
                except Exception as exc:
                    _log_suppressed("Failed scheduling next gcode_loaded_stream apply slice", exc)
            _run_phase(phase_index + 1)

        _run_phase(0)

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._gcode_loaded_stream_apply_after_id = after_fn(0, _run_pending)
            return
        except Exception as exc:
            _log_suppressed("Failed scheduling deferred gcode_loaded_stream apply", exc)
    _run_pending()


def _is_error_15(message: str | None) -> bool:
    if not message:
        return False
    return "error:15" in str(message).lower()


def _is_jog_source(source: str | None) -> bool:
    if not source:
        return False
    normalized = str(source).strip().lower()
    return normalized in {"joystick", "jog", "jog_hold", "jog_button"}


def _truncate_stream_error_line_text(text: str, *, max_chars: int = _STREAM_ERROR_LINE_TEXT_MAX_CHARS) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= int(max_chars):
        return cleaned
    if int(max_chars) <= 3:
        return cleaned[: max(0, int(max_chars))]
    return cleaned[: int(max_chars) - 3] + "..."


def _stream_error_hint(message: str, line_text: str) -> str:
    haystack = f"{message} {line_text}".lower()
    if "error:33" in haystack:
        return _STREAM_ERROR_33_HINT
    return ""


def _safe_status_update(app: Any, text: str, *, context: str) -> None:
    try:
        app.status.config(text=text)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed(context, exc)


def _clear_homing_watchdog(app: Any, context: str) -> None:
    if not getattr(app, "_homing_in_progress", False):
        return
    app._homing_in_progress = False
    app._homing_state_seen = False
    try:
        app.grbl.clear_watchdog_ignore("homing")
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed(context, exc)


def _handle_log_rx_event(app: Any, raw: str) -> None:
    try:
        if str(raw).lstrip().upper().startswith("GRBL"):
            invalidate_job_setup_state(app)
            clear_active_auto_level_map(
                app,
                "Auto-Level map invalidated by controller reset.",
            )
    except Exception as exc:
        _log_suppressed("Failed invalidating job setup on GRBL reset banner", exc)
    _parse_modal_units(app, raw)
    _parse_report_units_setting(app, raw)
    probe_controller = getattr(app, "probe_controller", None)
    if probe_controller is not None:
        probe_controller.handle_rx_line(raw)
    settings_controller = getattr(app, "settings_controller", None)
    if settings_controller is not None:
        should_route_settings = bool(getattr(settings_controller, "_settings_capture", False))
        if not should_route_settings:
            stripped = str(raw).lstrip()
            should_route_settings = stripped.startswith("$") and ("=" in stripped)
        if should_route_settings:
            settings_controller.handle_line(raw)
    app.streaming_controller.handle_log_rx(raw)


def _handle_manual_error_event(app: Any, msg: str, source: str | None) -> None:
    started = time.perf_counter()
    try:
        stop_predict = getattr(app, "_stop_manual_jog_prediction", None)
        if callable(stop_predict):
            stop_predict(reason="manual_error")
    except Exception as exc:
        _log_suppressed("Failed stopping jog DRO interpolation after manual error", exc)
    raw_msg = str(msg)
    annotated = annotate_grbl_error(raw_msg)
    label = str(source).strip() if source else ""
    if label.lower() == "macro":
        macro_executor = getattr(app, "macro_executor", None)
        notify_manual_error = getattr(macro_executor, "notify_manual_error", None)
        if callable(notify_manual_error):
            try:
                notify_manual_error(annotated)
            except Exception as exc:
                _log_suppressed("Failed notifying macro executor of manual error", exc)
    show_jog_limit_hint = _is_jog_source(label) and (_is_error_15(raw_msg) or _is_error_15(annotated))
    key = (label.lower(), str(annotated), bool(show_jog_limit_hint))
    now = time.monotonic()
    last_key = getattr(app, "_manual_error_last_key", None)
    last_ts = float(getattr(app, "_manual_error_last_ts", 0.0) or 0.0)
    if key == last_key and (now - last_ts) <= _MANUAL_ERROR_COALESCE_WINDOW_S:
        app._manual_error_repeat_count = int(
            getattr(app, "_manual_error_repeat_count", 0) or 0
        ) + 1
        app._manual_error_last_ts = now
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        record_task_timing(app, "ui.manual_error", elapsed_ms, success=True)
        logger.info(
            "[ui] manual_error coalesced: source=%s repeats=%d window_s=%.2f",
            key[0] or "manual",
            int(getattr(app, "_manual_error_repeat_count", 0) or 0),
            float(_MANUAL_ERROR_COALESCE_WINDOW_S),
        )
        return
    app._manual_error_last_key = key
    app._manual_error_last_ts = now
    app._manual_error_repeat_count = 0
    _clear_homing_watchdog(app, "Failed clearing homing watchdog ignore after manual error")
    prefix = f"GRBL error ({label})" if label else "GRBL error"
    status_text = (
        _JOG_LIMIT_ERROR_HINT
        if show_jog_limit_hint
        else f"{prefix}: {annotated}"
    )
    src_tag = f" ({label})" if label else ""
    log_text = f"[ERROR{src_tag}] {annotated}"
    app._manual_error_ui_payload = (
        status_text,
        bool(show_jog_limit_hint and label.lower().startswith("joystick")),
        log_text,
    )
    if getattr(app, "_manual_error_ui_after_id", None) is not None:
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        record_task_timing(app, "ui.manual_error", elapsed_ms, success=True)
        return

    def _apply_manual_error_ui() -> None:
        apply_started = time.perf_counter()
        app._manual_error_ui_after_id = None
        payload = getattr(app, "_manual_error_ui_payload", None)
        if not payload or len(payload) != 3:
            return
        status_value = str(payload[0] or "")
        set_joystick_hint = bool(payload[1])
        log_value = str(payload[2] or "")
        status_ms = 0.0
        joy_ms = 0.0
        log_ms = 0.0
        section_started = time.perf_counter()
        _safe_status_update(
            app,
            status_value,
            context="Failed to update status for manual error",
        )
        status_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        if set_joystick_hint:
            section_started = time.perf_counter()
            try:
                if hasattr(app, "joystick_event_status"):
                    app.joystick_event_status.set(_JOG_LIMIT_ERROR_HINT)
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to update joystick status hint", exc)
            joy_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        section_started = time.perf_counter()
        try:
            app.streaming_controller.handle_log(log_value)
        except (AttributeError, RuntimeError, TclError) as exc:
            _log_suppressed("Failed to log manual error to console", exc)
        log_ms = max(0.0, (time.perf_counter() - section_started) * 1000.0)
        total_ms = max(0.0, (time.perf_counter() - apply_started) * 1000.0)
        record_task_timing(app, "ui.manual_error", total_ms, success=True)
        logger.info(
            "[ui] manual_error timing: total=%.2fms status=%.2fms joystick=%.2fms log=%.2fms",
            total_ms,
            status_ms,
            joy_ms,
            log_ms,
        )
        if total_ms > _MANUAL_ERROR_HANDLER_BUDGET_MS:
            logger.warning(
                "[ui] manual_error exceeded %.1fms budget: total=%.2fms",
                _MANUAL_ERROR_HANDLER_BUDGET_MS,
                total_ms,
            )

    after_fn = getattr(app, "after", None)
    if callable(after_fn):
        try:
            app._manual_error_ui_after_id = after_fn(0, _apply_manual_error_ui)
        except Exception as exc:
            _log_suppressed("Failed scheduling manual_error deferred UI update", exc)
            _apply_manual_error_ui()
    else:
        _apply_manual_error_ui()
    elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
    record_task_timing(app, "ui.manual_error.enqueue", elapsed_ms, success=True)


def _handle_manual_queue_drop_event(app: Any, dropped: int, total: int) -> None:
    try:
        app._manual_queue_drop_total = max(0, int(total))
    except Exception:
        app._manual_queue_drop_total = max(0, int(dropped))
    _safe_status_update(
        app,
        f"Manual queue full: dropped {app._manual_queue_drop_total} command(s).",
        context="Failed to update manual queue drop status",
    )


def _handle_alarm_event(app: Any, message: str) -> None:
    msg = annotate_grbl_alarm(str(message))
    clear_active_auto_level_map(app, "Auto-Level map invalidated by controller alarm.")
    _clear_homing_watchdog(app, "Failed clearing homing watchdog ignore after alarm")
    app._set_alarm_lock(True, msg)
    app.macro_executor.notify_alarm(msg)
    app._apply_status_poll_profile()
    try:
        show_grbl_code_popup(app, msg)
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed showing GRBL alarm popup", exc)


def _handle_stream_error_event(
    app: Any,
    msg: Any,
    err_idx: Any,
    err_line: Any,
    gcode_name: Any,
) -> None:
    message = str(msg or "").strip() or "Unknown stream error"
    parsed_idx: int | None = None
    if err_idx is not None:
        try:
            parsed_idx = int(cast(int | str, err_idx))
        except (TypeError, ValueError):
            parsed_idx = None
    line_number: int | None = None
    if parsed_idx is not None and parsed_idx >= 0:
        app._last_error_index = parsed_idx
        line_number = parsed_idx + 1
    line_text = _truncate_stream_error_line_text(str(err_line or ""))
    file_name = str(gcode_name or "").strip()
    if not file_name:
        last_path = str(getattr(app, "_last_gcode_path", "") or "").strip()
        file_name = os.path.basename(last_path) if last_path else ""
    hint = _stream_error_hint(message, line_text)
    app._last_stream_error_message = message
    app._last_stream_error_file_name = file_name
    app._last_stream_error_line_index = int(parsed_idx) if parsed_idx is not None else -1
    app._last_stream_error_line_number = int(line_number) if line_number is not None else 0
    app._last_stream_error_line_text = line_text
    app._last_stream_error_hint = hint

    line_desc = ""
    if line_number is not None:
        if file_name:
            line_desc = f"{file_name} line {line_number}"
        else:
            line_desc = f"line {line_number}"
    status_msg = f"Stream error: {message}"
    if line_desc:
        status_msg = f"{status_msg} | {line_desc}"
    if line_text:
        status_msg = f"{status_msg} -> {line_text}"
    if hint:
        status_msg = f"{status_msg} | {hint}"
    _safe_status_update(
        app,
        status_msg,
        context="Failed to update stream error status",
    )
    try:
        if line_desc:
            app.streaming_controller.handle_log(
                f"[stream error] {line_desc}{' -> ' + line_text if line_text else ''}"
            )
        if hint:
            app.streaming_controller.handle_log(f"[stream error hint] {hint}")
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed routing stream error details to console log", exc)
    try:
        show_grbl_code_popup(app, status_msg)
    except (AttributeError, RuntimeError, TclError) as exc:
        _log_suppressed("Failed showing stream error popup", exc)


def _handle_stream_pause_reason_event(app: Any, reason: Any) -> None:
    if not reason:
        return
    _safe_status_update(
        app,
        f"Paused ({reason})",
        context="Failed to update pause reason status",
    )


def _handle_unknown_event(app: Any, evt: Any) -> None:
    message = f"Unhandled UI event: {evt!r}"
    try:
        if hasattr(app, "_log_exception"):
            app._log_exception("Unhandled UI event", ValueError(message))
        else:
            _log_suppressed("Unhandled UI event (no app logger)", ValueError(message))
    except Exception as exc:
        _log_suppressed("Failed logging unhandled UI event", exc)
    try:
        if hasattr(app, "streaming_controller"):
            app.streaming_controller.handle_log(f"[ui] {message}")
    except Exception as exc:
        _log_suppressed("Failed writing unhandled UI event to console", exc)


def set_streaming_lock(app: Any, locked: bool, *, defer_toolbar_refresh: bool = False):
    locked = bool(locked)
    prior_locked = getattr(app, "_streaming_lock_state", None)
    if prior_locked is not None and bool(prior_locked) == locked:
        return
    app._streaming_lock_state = locked
    state = "disabled" if locked else "normal"
    try:
        app.btn_conn.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update connect button state", exc)
    try:
        app.btn_refresh.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update refresh button state", exc)
    try:
        app.port_combo.config(state="disabled" if locked else "readonly")
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update port combo state", exc)
    try:
        app.btn_unit_toggle.config(state=state)
    except (AttributeError, TclError, RuntimeError) as exc:
        _log_suppressed("Failed to update unit toggle state", exc)
    if not defer_toolbar_refresh and hasattr(app, "_refresh_toolbar_action_focus"):
        try:
            app._refresh_toolbar_action_focus()
        except (AttributeError, RuntimeError, TclError, TypeError, ValueError) as exc:
            _log_suppressed("Failed refreshing toolbar action focus after streaming lock change", exc)


def _report_recovery_finalization_failure(app: Any, message: str) -> None:
    """Keep the exact approved handoff reachable while admission remains closed."""
    app._grbl_ready = False
    for attr in (
        "_machine_coordinates_trusted",
        "_modal_state_trusted",
        "_work_offsets_trusted",
        "_g92_trusted",
        "_tool_length_offset_trusted",
        "_spindle_state_trusted",
        "_coolant_state_trusted",
    ):
        setattr(app, attr, False)
    try:
        macro_executor = getattr(app, "macro_executor", None)
        macro_vars_context = getattr(macro_executor, "macro_vars", None)
        if callable(macro_vars_context):
            with macro_vars_context() as macro_vars:
                for key in (
                    "_position_trusted",
                    "_modal_trusted",
                    "_wcs_trusted",
                    "_g92_trusted",
                    "_tlo_trusted",
                    "_spindle_trusted",
                    "_coolant_trusted",
                ):
                    macro_vars[key] = False
    except Exception as exc:
        _log_suppressed("Failed invalidating recovery-finalization macro trust", exc)
    try:
        disable_job_controls(app)
    except Exception as exc:
        _log_suppressed("Failed disabling controls after recovery-finalization failure", exc)
    try:
        app._set_manual_controls_enabled(False)
    except Exception as exc:
        _log_suppressed("Failed disabling manual controls after recovery-finalization failure", exc)
    try:
        app.status.config(text=message)
    except Exception as exc:
        _log_suppressed("Failed reporting recovery-finalization failure", exc)
    try:
        app._update_recover_button_visibility()
        app.btn_alarm_recover.config(state="normal")
    except Exception as exc:
        _log_suppressed("Failed exposing recovery-finalization retry", exc)


def _handle_recovery_complete_event(
    app: Any,
    completed_state: ExecutionRecoveryState,
    snapshot: RecoveryStateSnapshot,
    finalization_identity: RecoveryFinalizationIdentity,
) -> None:
    worker = app.grbl
    try:
        current_state = worker.recovery_state()
        authoritative_snapshot = worker.recovery_snapshot()
    except Exception as exc:
        logger.warning("Recovery completion preflight failed: %s", exc)
        return
    if (
        current_state.phase is not RecoveryPhase.RECOVERY_COMPLETE
        or not current_state.required
        or authoritative_snapshot is not snapshot
    ):
        logger.warning("Ignoring recovery completion outside its authoritative handoff")
        return
    if (
        int(snapshot.connection_generation) != int(completed_state.connection_generation)
        or int(snapshot.recovery_epoch) != int(completed_state.recovery_epoch)
    ):
        logger.warning("Ignoring recovery completion with mismatched snapshot provenance")
        return
    trust = snapshot.to_trust_state(setup_tool_reference=False)
    if trust.missing_for_new_job():
        logger.warning("Ignoring incomplete recovery snapshot")
        return
    machine_position = snapshot.machine_position
    work_coordinate_offset = snapshot.work_coordinate_offset
    g92 = snapshot.g92
    if machine_position is None or work_coordinate_offset is None or g92 is None:
        logger.warning("Ignoring recovery snapshot without required coordinate values")
        return
    derived_work_position = tuple(
        float(machine_position[idx]) - float(work_coordinate_offset[idx])
        for idx in range(3)
    )
    if not all(math.isfinite(value) for value in derived_work_position):
        logger.warning("Ignoring recovery snapshot with non-finite derived work position")
        return

    # Ordinary worker admission remains closed throughout this UI/cache
    # transaction. Any installation failure leaves RECOVERY_COMPLETE latched.
    try:
        clear_fn = getattr(app, "_clear_gcode", None)
        if not callable(clear_fn):
            raise RuntimeError("Interrupted-job clear operation is unavailable")
        if clear_fn() is False:
            raise RuntimeError("Interrupted-job clear transaction did not commit")
        _install_machine_snapshot_caches(app, snapshot)
        app._stream_state = "stopped"
        app._stream_done_pending_idle = False
        app._grbl_ready = True
        app._auto_reconnect_blocked = False
        app._set_manual_controls_enabled(True)
    except Exception as exc:
        logger.error("Recovery snapshot installation failed; admission remains locked: %s", exc)
        _report_recovery_finalization_failure(
            app,
            "Recovery snapshot installation failed; use Retry Recovery Finalization.",
        )
        return
    finalizer = getattr(app.grbl, "finalize_recovery_snapshot_install", None)
    if not callable(finalizer) or not bool(
        finalizer(
            snapshot,
            finalization_identity,
            connection_generation=int(getattr(app.grbl, "connection_generation")()),
            recovery_epoch=int(getattr(app.grbl, "recovery_epoch")()),
        )
    ):
        _report_recovery_finalization_failure(
            app,
            "Recovery snapshot could not be committed; use Retry Recovery Finalization.",
        )
        return
    try:
        accessory_router = getattr(app, "accessory_router", None)
        retire_recovery_off = getattr(
            accessory_router,
            "retire_confirmed_recovery_safety_off",
            None,
        )
        if callable(retire_recovery_off):
            retire_recovery_off(
                connection_generation=int(completed_state.connection_generation),
                recovery_epoch=int(completed_state.recovery_epoch),
            )
    except Exception as exc:
        _log_suppressed("Failed retiring confirmed Kasa recovery OFF tombstone", exc)
    app.status.config(text="Recovery complete. Interrupted job was closed.")
    dialog = getattr(app, "_execution_recovery_dialog", None)
    if dialog is not None:
        try:
            dialog.destroy()
        except Exception as exc:
            _log_suppressed("Failed retiring completed recovery dialog", exc)
        app._execution_recovery_dialog = None
        app._execution_recovery_dialog_identity = None
        app._execution_recovery_dialog_phase = None
    try:
        app.btn_alarm_recover.config(state="disabled")
        app._update_recover_button_visibility()
    except Exception as exc:
        _log_suppressed("Failed retiring completed recovery toolbar action", exc)


def handle_event(app: Any, evt: UiEvent):
    if _event_is_from_stale_connection(app, evt):
        logger.warning("Ignoring UI event from stale GRBL connection generation: %r", evt)
        return
    if _event_blocked_by_recovery(app, evt):
        logger.warning("Ignoring machine-state UI event while recovery is required: %r", evt)
        return
    match evt:
        case ConnectionScopedEvent(payload=payload):
            handle_event(app, cast(UiEvent, payload))
            return
        case ConnectionEvent(connected=connected, port=port):
            handle_connection_event(app, bool(connected), cast(str | None, port))
            return
        case ReadyEvent(is_ready=is_ready):
            handle_ready_event(app, bool(is_ready))
            return
        case RecoveryRequiredEvent(state=state):
            _event_router_streaming.handle_recovery_required_event(app, state)
            return
        case RecoveryCompleteEvent(
            completed_state=state,
            snapshot=snapshot,
            finalization_identity=finalization_identity,
        ):
            _handle_recovery_complete_event(
                app,
                state,
                snapshot,
                finalization_identity,
            )
            return
        case NormalSessionInitializationEvent(state=state, snapshot=snapshot):
            _handle_normal_session_initialization_event(app, state, snapshot)
            return
        case AlarmEvent(message=msg):
            _handle_alarm_event(app, cast(str, msg))
            return
        case StatusEvent(line=line):
            handle_status_event(
                app,
                cast(str, line),
                generation=evt.generation,
                recovery_epoch=evt.recovery_epoch,
            )
            return
        case SettingsDumpDoneEvent():
            try:
                app.settings_controller.handle_line("ok")
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to process settings dump completion", exc)
            _show_deferred_normal_session_initialization_if_ready(app)
            return
        case GcodeSentEvent(idx=idx, line=_line):
            app.streaming_controller.handle_gcode_sent(int(cast(int, idx)))
            return
        case GcodeAckedEvent(idx=idx):
            app.streaming_controller.handle_gcode_acked(int(cast(int, idx)))
            return
        case ProgressEvent(done=done, total=total):
            app.streaming_controller.handle_progress(
                int(cast(int, done)),
                int(cast(int, total)),
            )
            return
        case ProgressBytesEvent(acked_offset=acked_offset, file_size_bytes=file_size_bytes):
            app.streaming_controller.handle_progress_bytes(
                int(cast(int, acked_offset)),
                int(cast(int, file_size_bytes)),
            )
            return
        case StreamStateEvent(
            state="done",
            generation=generation,
            stream_epoch=stream_epoch,
            recovery_epoch=recovery_epoch,
        ):
            finalizer = getattr(app.grbl, "finalize_execution_completion", None)
            if (
                generation is None
                or stream_epoch is None
                or recovery_epoch is None
                or not callable(finalizer)
                or not bool(
                finalizer(
                    connection_generation=int(generation),
                    stream_epoch=int(stream_epoch),
                    recovery_epoch=int(recovery_epoch),
                )
                )
            ):
                logger.warning("Ignoring unowned or retired stream completion event")
                return
            handle_stream_state_event(app, evt)
            return
        case StreamStateEvent():
            handle_stream_state_event(app, evt)
            return
        case StreamCompletionEofEvent(
            verified_eof=verified_eof,
            total_lines=total_lines,
            total_lines_known=total_lines_known,
            last_acked_index=last_acked_index,
            send_index=send_index,
        ):
            _event_router_streaming.handle_stream_completion_eof_event(
                app,
                verified_eof=bool(verified_eof),
                total_lines=int(total_lines),
                total_lines_known=bool(total_lines_known),
                last_acked_index=int(last_acked_index),
                send_index=int(send_index),
            )
            return
        case StreamInterruptedEvent():
            handle_stream_interrupted(app, evt)
            return
        case StreamErrorEvent(
            message=msg,
            err_idx=err_idx,
            err_line=err_line,
            gcode_name=name,
        ):
            _handle_stream_error_event(app, msg, err_idx, err_line, name)
            return
        case StreamPauseReasonEvent(reason=reason):
            _handle_stream_pause_reason_event(app, reason)
            return
        case ("conn", connected, port):
            handle_connection_event(app, cast(bool, connected), cast(str | None, port))
            return
        case ("ui_call", func, args, kwargs, result_q):
            handle_ui_call(app, func, args, kwargs, result_q)
            return
        case ("ui_call", func, args, kwargs, result_q, cancel_token):
            handle_ui_call(
                app,
                func,
                args,
                kwargs,
                result_q,
                cancel_token=cancel_token,
            )
            return
        case ("ui_call", func, args, kwargs, result_q, cancel_token, start_q):
            handle_ui_call(
                app,
                func,
                args,
                kwargs,
                result_q,
                cancel_token=cancel_token,
                start_q=start_q,
            )
            return
        case ("ui_post", func, args, kwargs):
            handle_ui_post(app, func, args, kwargs)
            return
        case ("macro_prompt", title, message, choices, cancel_label, result_q):
            handle_macro_prompt(app, title, message, choices, cancel_label, result_q)
            return
        case ("gcode_load_progress", token, done, total, label):
            handle_gcode_load_progress(app, token, done, total, label)
            return
        case ("gcode_loaded", *_):
            handle_gcode_loaded(app, evt)
            return
        case ("gcode_loaded_stream", *_):
            handle_gcode_loaded_stream(app, evt)
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines, cleaned_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                cast(int | None, cleaned_lines),
            )
            return
        case ("gcode_load_invalid", token, path, too_long, first_idx, first_len, total_lines):
            handle_gcode_load_invalid(
                app,
                cast(int, token),
                cast(str, path),
                cast(int, too_long),
                cast(int | None, first_idx),
                cast(int | None, first_len),
                cast(int | None, total_lines),
                None,
            )
            return
        case ("gcode_load_invalid_command", token, path, line_no, line_text):
            handle_gcode_load_invalid_command(
                app,
                cast(int, token),
                cast(str, path),
                cast(int | None, line_no),
                cast(str | None, line_text),
            )
            return
        case ("gcode_load_error", token, path, err):
            handle_gcode_load_error(app, token, path, err)
            return
        case ("log", message):
            app.streaming_controller.handle_log(message)
            return
        case ("manual_queue_drop", dropped, total):
            _handle_manual_queue_drop_event(
                app,
                cast(int, dropped),
                cast(int, total),
            )
            return
        case ("log_tx", message):
            app.streaming_controller.handle_log_tx(message)
            try:
                if hasattr(app, "_handle_outgoing_gcode_line"):
                    app._handle_outgoing_gcode_line(cast(str, message), "manual")
            except Exception as exc:
                _log_suppressed("Failed processing outbound manual line for Kasa routing", exc)
            return
        case ("log_rx", raw):
            _handle_log_rx_event(app, cast(str, raw))
            return
        case ("settings_dump_done",):
            try:
                app.settings_controller.handle_line("ok")
            except (AttributeError, RuntimeError, TclError) as exc:
                _log_suppressed("Failed to process settings dump completion", exc)
            _show_deferred_normal_session_initialization_if_ready(app)
            return
        case ("manual_error", msg, source):
            _handle_manual_error_event(
                app,
                cast(str, msg),
                cast(str | None, source),
            )
            return
        case ("ready", is_ready):
            handle_ready_event(app, is_ready)
            return
        case ("recovery_required", state):
            _event_router_streaming.handle_recovery_required_event(
                app, cast(ExecutionRecoveryState, state)
            )
            return
        case ("auto_level_map_invalidated", provenance, reason):
            if isinstance(provenance, AutoLevelMapProvenance):
                clear_active_auto_level_map_if_owned(
                    app,
                    provenance,
                    cast(str, reason),
                )
            return
        case ("recovery_complete", _state, snapshot):
            logger.warning(
                "Ignoring legacy recovery completion without exact finalization identity"
            )
            return
        case ("normal_session_initialization", state, snapshot):
            _handle_normal_session_initialization_event(
                app,
                cast(NormalSessionInitializationState, state),
                cast(RecoveryStateSnapshot | None, snapshot),
            )
            return
        case ("recovery_complete", _state):
            # Compatibility-only shape. It must not reopen admission because it
            # carries no value-bound authoritative snapshot.
            logger.warning("Ignoring recovery completion without an authoritative snapshot")
            return
        case ("alarm", msg):
            _handle_alarm_event(app, cast(str, msg))
            return
        case ("status", line):
            handle_status_event(app, cast(str, line))
            return
        case ("buffer_fill", pct, used, window):
            app.streaming_controller.handle_buffer_fill(pct, used, window)
            return
        case ("throughput", bps):
            app.streaming_controller.handle_throughput(float(cast(float, bps)))
            return
        case ("stream_state", *_):
            handle_stream_state_event(app, evt)
            return
        case (
            "stream_completion_eof",
            verified_eof,
            total_lines,
            total_lines_known,
            last_acked_index,
            send_index,
        ):
            _event_router_streaming.handle_stream_completion_eof_event(
                app,
                verified_eof=bool(cast(bool, verified_eof)),
                total_lines=int(cast(int, total_lines)),
                total_lines_known=bool(cast(bool, total_lines_known)),
                last_acked_index=int(cast(int, last_acked_index)),
                send_index=int(cast(int, send_index)),
            )
            return
        case ("stream_interrupted", *_):
            handle_stream_interrupted(app, evt)
            return
        case ("stream_error", msg, err_idx, err_line, name):
            _handle_stream_error_event(app, msg, err_idx, err_line, name)
            return
        case ("stream_pause_reason", reason):
            _handle_stream_pause_reason_event(app, reason)
            return
        case ("stream_vacuum_directive", is_on):
            try:
                if hasattr(app, "_handle_stream_vacuum_directive"):
                    app._handle_stream_vacuum_directive(bool(cast(bool, is_on)))
            except Exception as exc:
                _log_suppressed("Failed handling streamed vacuum directive", exc)
            return
        case ("stream_vacuum_directive", is_on, line_index):
            try:
                if hasattr(app, "_handle_stream_vacuum_directive"):
                    app._handle_stream_vacuum_directive(
                        bool(cast(bool, is_on)),
                        line_index=cast(int | None, line_index),
                    )
            except Exception as exc:
                _log_suppressed("Failed handling streamed vacuum directive", exc)
            return
        case ("stream_vacuum_directive", is_on, line_index, requires_confirmation):
            try:
                accepted = True
                if hasattr(app, "_handle_stream_vacuum_directive"):
                    accepted = bool(
                        app._handle_stream_vacuum_directive(
                            bool(cast(bool, is_on)),
                            line_index=cast(int | None, line_index),
                            requires_confirmation=bool(cast(bool, requires_confirmation)),
                        )
                    )
                if bool(cast(bool, requires_confirmation)) and not accepted:
                    grbl = getattr(app, "grbl", None)
                    completer = getattr(grbl, "complete_stream_vacuum_directive", None)
                    if callable(completer):
                        completer(False, "Kasa directive command was not accepted.")
            except Exception as exc:
                _log_suppressed("Failed handling confirmed streamed vacuum directive", exc)
            return
        case ("stream_tool_change", line_idx, tool_name, identity):
            try:
                if hasattr(app, "_handle_stream_tool_change"):
                    app._handle_stream_tool_change(
                        cast(str, tool_name),
                        cast(StreamToolChangeIdentity, identity),
                        line_index=cast(int | None, line_idx),
                    )
            except Exception as exc:
                _log_suppressed("Failed handling streamed tool-change directive", exc)
            return
        case ("spindle_state", is_on, _idx):
            try:
                if hasattr(app, "_handle_stream_spindle_state"):
                    app._handle_stream_spindle_state(bool(cast(bool, is_on)))
            except Exception as exc:
                _log_suppressed("Failed processing streamed spindle-state event for Kasa routing", exc)
            return
        case ("gcode_sent", idx, _line):
            app.streaming_controller.handle_gcode_sent(idx)
            return
        case ("gcode_acked", idx):
            app.streaming_controller.handle_gcode_acked(idx)
            return
        case ("progress", done, total):
            app.streaming_controller.handle_progress(done, total)
            return
        case ("progress_bytes", acked_offset, file_size_bytes):
            app.streaming_controller.handle_progress_bytes(
                int(cast(int, acked_offset)),
                int(cast(int, file_size_bytes)),
            )
            return
        case _:
            _handle_unknown_event(app, evt)
            return


def handle_stream_state_event(app, evt):
    _event_router_streaming.job_controls_ready = job_controls_ready
    _event_router_streaming.set_run_resume_from = set_run_resume_from
    return _event_router_streaming.handle_stream_state_event(app, evt)


def handle_stream_interrupted(app, evt):
    return _event_router_streaming.handle_stream_interrupted(app, evt)


def handle_ui_call(app, func, args, kwargs, result_q, *, cancel_token=None, start_q=None):
    if cancel_token is not None and cancel_token.is_set():
        return
    if start_q is not None:
        try:
            start_q.put_nowait(True)
        except queue.Full as exc:
            _log_suppressed("UI call start queue full while reporting handoff", exc)
    try:
        value = func(*args, **kwargs)
    except Exception as exc:
        app._log_exception("UI action failed", exc)
        if cancel_token is not None and cancel_token.is_set():
            return
        try:
            result_q.put_nowait((False, exc))
        except queue.Full as queue_exc:
            _log_suppressed("UI call result queue full while reporting failure", queue_exc)
        return
    if cancel_token is not None and cancel_token.is_set():
        return
    try:
        result_q.put_nowait((True, value))
    except queue.Full as exc:
        _log_suppressed("UI call result queue full while reporting success", exc)


def handle_ui_post(app, func, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception as exc:
        app._log_exception("UI action failed", exc)


def handle_macro_prompt(app, title, message, choices, cancel_label, result_q):
    try:
        app._show_macro_prompt(title, message, choices, cancel_label, result_q)
    except (AttributeError, RuntimeError, TclError, TypeError) as exc:
        try:
            app.streaming_controller.log(f"[macro] Prompt failed: {exc}")
        except (AttributeError, RuntimeError, TclError) as log_exc:
            _log_suppressed("Failed to log macro prompt failure to UI console", log_exc)
        try:
            result_q.put_nowait(cancel_label)
        except queue.Full as exc:
            _log_suppressed("Macro prompt result queue was full while reporting failure", exc)


def handle_gcode_load_progress(app, token, done, total, label):
    if token != app._gcode_load_token:
        return
    if not getattr(app, "_gcode_loading", False):
        return
    try:
        app._set_gcode_loading_progress(done, total, label)
    except (AttributeError, RuntimeError, TclError, TypeError, ValueError) as exc:
        _log_suppressed("Failed to update G-code loading progress", exc)


def handle_gcode_loaded(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        return
    path = evt[2]
    lines = evt[3]
    lines_hash = evt[4] if len(evt) > 4 else None
    validated = bool(evt[5]) if len(evt) > 5 else False
    report = evt[6] if len(evt) > 6 else None
    app._gcode_validation_report = report
    try:
        app._apply_loaded_gcode(path, lines, lines_hash=lines_hash, validated=validated)
    except Exception as exc:
        _signal_gcode_load_result(app, token=token, success=False, path=path, error=str(exc))
        raise
    _signal_gcode_load_result(app, token=token, success=True, path=path)
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        try:
            perf_monitor.note_file_loaded()
        except Exception as exc:
            _log_suppressed("Failed forwarding file-loaded milestone to performance monitor", exc)


def handle_gcode_loaded_stream(app, evt):
    token = evt[1]
    if token != app._gcode_load_token:
        source = evt[3] if len(evt) > 3 else None
        _cleanup_streaming_source(source, context="Stale gcode_loaded_stream token")
        return
    path = evt[2]
    source = evt[3]
    sample_lines = evt[4] if len(evt) > 4 else []
    lines_hash = evt[5] if len(evt) > 5 else None
    total_lines = evt[6] if len(evt) > 6 else None
    report = evt[7] if len(evt) > 7 else None
    sample_only = bool(evt[8]) if len(evt) > 8 else True
    signature = _loaded_stream_signature(
        path=path,
        lines_hash=lines_hash,
        total_lines=total_lines,
        sample_only=sample_only,
    )
    if _loaded_stream_is_noop(app, signature):
        logger.info(
            "[ui] gcode_loaded_stream idempotent skip: %s->loaded job=%s hash=%s reason=already_loaded_no_changes",
            str(getattr(app, "_stream_state", "") or "").strip().lower() or "none",
            signature[0] or "<none>",
            signature[1] or "<none>",
        )
        app._gcode_validation_report = report
        app._gcode_loading = False
        app._finish_gcode_loading()
        _signal_gcode_load_result(app, token=token, success=True, path=path)
        _cleanup_streaming_source(source, context="Idempotent gcode_loaded_stream skip")
        return
    _queue_loaded_stream_apply(
        app,
        token=token,
        signature=signature,
        path=path,
        source=source,
        sample_lines=sample_lines,
        lines_hash=lines_hash,
        total_lines=total_lines,
        report=report,
        sample_only=sample_only,
    )


def handle_gcode_load_invalid(
    app,
    token,
    _path,
    too_long: int,
    first_idx: int | None,
    first_len: int | None,
    total_lines: int | None = None,
    cleaned_lines: int | None = None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    msg = f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit."
    if first_idx is not None and first_len is not None:
        msg += f"\nFirst at line {first_idx + 1} ({first_len} bytes including newline)."
    if total_lines is not None or cleaned_lines is not None:
        orig = f"{total_lines}" if total_lines is not None else "?"
        cleaned = f"{cleaned_lines}" if cleaned_lines is not None else "?"
        msg += f"\nFile lines: {orig} (non-empty: {cleaned})."
    _signal_gcode_load_result(app, token=token, success=False, error=msg)
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def handle_gcode_load_invalid_command(
    app,
    token,
    _path,
    line_no: int | None,
    line_text: str | None,
):
    if token != app._gcode_load_token:
        return
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    text = (line_text or "").strip() or "$"
    msg = "GRBL system commands ($...) are not allowed inside G-code jobs."
    if line_no is not None:
        msg += f"\nFirst at line {line_no}: {text}"
    _signal_gcode_load_result(app, token=token, success=False, error=msg)
    messagebox.showerror("Open G-code", msg)
    app.status.config(text="G-code load failed")


def _fail_gcode_load(
    app,
    *,
    token,
    path: str | None,
    err,
    dialog_prefix: str,
) -> None:
    if token != app._gcode_load_token:
        return
    _signal_gcode_load_result(
        app,
        token=token,
        success=False,
        path=path,
        error=str(err),
    )
    app._gcode_validation_report = None
    _clear_autolevel_restore(app)
    app._gcode_loading = False
    app._finish_gcode_loading()
    app.gcode_stats_var.set("")
    app._gcode_status_last_text = ""
    messagebox.showerror("Open G-code", f"{dialog_prefix}:\n{err}")
    app.status.config(text="G-code load failed")


def handle_gcode_load_error(app, token, path, err):
    _fail_gcode_load(
        app,
        token=token,
        path=path,
        err=err,
        dialog_prefix="Failed to read file",
    )


def _clear_autolevel_restore(app) -> None:
    restore = getattr(app, "_auto_level_restore", None)
    if not isinstance(restore, dict):
        return
    app._auto_level_restore = None
    leveled_path = restore.get("leveled_path")
    if restore.get("leveled_temp") and leveled_path:
        try:
            os.remove(leveled_path)
        except OSError as exc:
            _log_suppressed("Failed removing autolevel temp restore file", exc)
    app._auto_level_leveled_lines = None
    app._auto_level_leveled_path = None
    app._auto_level_leveled_temp = False
    app._auto_level_leveled_name = None
