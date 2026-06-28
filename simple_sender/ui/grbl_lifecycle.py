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
import queue
import threading
import time
import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
from tkinter import messagebox

from simple_sender.constants.messages import MachineStateMessages, StatusMessages
from simple_sender.ui.connection_runtime_state import (
    append_connection_timeline_event,
    get_connection_runtime_state,
    sync_connection_runtime_state_to_app,
)
from simple_sender.ui.controls.toolbar import set_toolbar_button_label
from simple_sender.ui.icons import ICON_CONNECT
from simple_sender.ui.job_setup_state import invalidate_job_setup_state
from simple_sender.ui.job_controls import disable_job_controls
from simple_sender.ui.modal_sync import clear_modal_sync_state, request_modal_state_sync
from simple_sender.utils.constants import (
    STATUS_POLL_DEFAULT,
    STATUS_POLL_IDLE,
    STATUS_POLL_RUNNING,
)

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_AUTO_RECONNECT_PORT_SCAN_MIN_INTERVAL_S = 1.0
_AUTO_RECONNECT_PORT_SCAN_CACHE_MAX_AGE_S = 8.0
_STATUS_POLL_PERF_IDLE_FLOOR = 3.0
_STATUS_POLL_PERF_QUIET_IDLE_FLOOR = 4.0
_STATUS_POLL_QUIET_IDLE_MIN_SECONDS = 15.0
_STATUS_POLL_PERF_ULTRA_QUIET_IDLE_FLOOR = 5.0
_STATUS_POLL_ULTRA_QUIET_IDLE_MIN_SECONDS = 60.0
_STATUS_POLL_PERF_RUNNING = 0.35
_STATUS_POLL_MANUAL_ACTIVE = 0.1
_STATUS_POLL_MANUAL_IDLE_READY = STATUS_POLL_RUNNING
_STATUS_POLL_PROBE_INDICATOR = 0.02
_STATUS_POLL_PROBE_INDICATOR_IDLE = 0.5
_STATUS_POLL_MACRO_ACTIVE = 0.1
_STATUS_POLL_MACRO_CRITICAL = 0.05
_STATUS_POLL_MANUAL_GRACE_S = 2.0
_STATUS_CONNECT_SETTLING_WINDOW_S = 1.5
_STATUS_CONNECT_SETTLING_READY_TAIL_S = 1.0


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _signal_thread_event(obj, attr_name: str) -> None:
    evt = getattr(obj, attr_name, None)
    if isinstance(evt, threading.Event):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed(f"Failed signaling thread event {attr_name}", exc)


def _post_ui(app, func, *args, **kwargs) -> None:
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(func, *args, **kwargs)
            return
        except Exception as exc:
            _log_suppressed("Failed posting reconnect callback via _post_ui_thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", func, args, kwargs))
            return
        except Exception as exc:
            _log_suppressed("Failed posting reconnect callback via ui_q", exc)


def _report_modal_sync_failure(app, message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    try:
        app.status.config(text=text)
    except Exception:
        pass
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("log", f"[status] {text}"))
        except Exception:
            pass


def _clear_status_frame_cache(app) -> None:
    """Invalidate cached status-frame continuity across connection resets."""
    try:
        app._last_status_raw = ""
    except Exception as exc:
        _log_suppressed("Failed clearing cached last status frame", exc)
    try:
        app._status_duplicate_count = 0
    except Exception as exc:
        _log_suppressed("Failed clearing cached duplicate-status counter", exc)


def _set_gcode_restore_state(
    app,
    *,
    failed: bool,
    message: str = "",
) -> None:
    try:
        app._gcode_restore_failed = bool(failed)
    except Exception:
        pass
    try:
        app._gcode_restore_failure_message = str(message or "").strip()
    except Exception:
        pass


def _clear_loaded_job_after_restore_failure(app) -> None:
    clear_fn = getattr(app, "_clear_gcode", None)
    if callable(clear_fn):
        try:
            clear_fn()
            return
        except Exception as exc:
            _log_suppressed("Failed clearing job after reconnect restore failure", exc)
    try:
        app._gcode_source = None
        app._last_gcode_lines = []
        app._last_gcode_path = None
        app._gcode_hash = None
        app._gcode_total_lines = 0
        app._resume_after_disconnect = False
        app._resume_from_index = None
        app._resume_job_name = None
    except Exception as exc:
        _log_suppressed(
            "Failed clearing local G-code state after reconnect restore failure",
            exc,
        )
    gview = getattr(app, "gview", None)
    clear_view = getattr(gview, "clear", None)
    if callable(clear_view):
        try:
            clear_view()
        except Exception as exc:
            _log_suppressed(
                "Failed clearing headless G-code state after reconnect restore failure",
                exc,
            )
    refresh_file_info = getattr(app, "_refresh_file_info_tab", None)
    if callable(refresh_file_info):
        try:
            refresh_file_info()
        except Exception as exc:
            _log_suppressed("Failed refreshing Job Info after reconnect restore failure", exc)
    try:
        disable_job_controls(app)
    except Exception as exc:
        _log_suppressed("Failed disabling job controls after reconnect restore failure", exc)


def _handle_reconnect_gcode_restore_failure(app, exc: BaseException) -> None:
    _log_suppressed("Failed restoring loaded G-code after connect", exc)
    message = "Connected, but the previous job could not be restored. Reload the job before running."
    _clear_loaded_job_after_restore_failure(app)
    _set_gcode_restore_state(app, failed=True, message=message)
    try:
        app.status.config(text=message)
    except Exception:
        pass
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("log", f"[status] {message}"))
        except Exception:
            pass


def _restore_loaded_gcode_after_connect(app) -> None:
    if getattr(app, "_gcode_source", None) is not None:
        name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
        app.grbl.load_gcode(app._gcode_source, name=name or None)
        if (
            not _pi_profile_enabled(app)
            and not getattr(app, "_gcode_streaming_mode", False)
            and app._last_gcode_lines
        ):
            prime_cache = getattr(app.grbl, "prime_gcode_send_cache", None)
            if callable(prime_cache):
                try:
                    prime_cache(app._last_gcode_lines)
                except Exception as exc:
                    _log_suppressed("Failed priming in-memory send cache after reconnect", exc)
        _set_gcode_restore_state(app, failed=False)
        return
    if app._last_gcode_lines:
        name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
        app.grbl.load_gcode(app._last_gcode_lines, name=name or None)
        _set_gcode_restore_state(app, failed=False)
        return
    _set_gcode_restore_state(app, failed=False)


def _schedule_reconnect_resume(app, *, start_index: int) -> None:
    try:
        app.status.config(text=f"Preparing reconnect resume from line {int(start_index) + 1}...")
    except Exception:
        pass

    def worker() -> None:
        result = app._build_resume_preamble(app._last_gcode_lines, int(start_index))
        if len(result) >= 3:
            preamble, has_g92, unsupported_dynamic_tlo = result[:3]
        else:
            preamble, has_g92 = result
            unsupported_dynamic_tlo = False

        def apply_resume() -> None:
            if bool(getattr(app, "_closing", False)):
                return
            try:
                if not bool(getattr(app, "connected", False)):
                    return
            except Exception:
                return
            app._resume_from_line(
                int(start_index),
                preamble,
                has_g92=bool(has_g92),
                unsupported_dynamic_tlo=bool(unsupported_dynamic_tlo),
            )

        _post_ui(app, apply_resume)

    threading.Thread(target=worker, name="reconnect-resume-preamble", daemon=True).start()


def _record_connection_timeline(app, event: str, details: str = "") -> None:
    try:
        append_connection_timeline_event(app, event, details, now_ts=time.time())
    except Exception as exc:
        _log_suppressed("Failed appending connection timeline event", exc)


def _format_connection_timeline_details(**values: object) -> str:
    parts: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "1" if value else "0"
        else:
            text = str(value or "").strip()
        if not text:
            continue
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _arm_status_connect_settling(app, *, duration_s: float) -> None:
    try:
        duration = float(duration_s)
    except Exception:
        duration = 0.0
    duration = max(0.0, duration)
    if duration <= 0.0:
        return
    try:
        now_mono = time.monotonic()
    except Exception:
        return
    runtime = get_connection_runtime_state(app)
    current_until = float(runtime.status_connect_settling_until_ts)
    new_until = now_mono + duration
    if new_until > current_until:
        runtime.status_connect_settling_until_ts = float(new_until)
        sync_connection_runtime_state_to_app(app, runtime)


def _normalize_status_state(app) -> str:
    return str(getattr(app, "_machine_state_text", "") or "").strip().lower()


def _stream_running_or_paused(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    return stream_state in {"running", "paused"}


def _status_poll_should_use_running_profile(app) -> bool:
    if bool(getattr(app, "connected", False)) and not bool(getattr(app, "_grbl_ready", False)):
        return True
    if _stream_running_or_paused(app):
        return True
    state = _normalize_status_state(app)
    if state.startswith("home"):
        return True
    if state.startswith("run"):
        return True
    if state.startswith("hold"):
        return True
    if state.startswith("alarm") or bool(getattr(app, "_alarm_locked", False)):
        return True
    if state.startswith("jog"):
        return True
    return False


def _performance_mode_enabled(app) -> bool:
    mode_var = getattr(app, "performance_mode", None)
    if mode_var is not None and hasattr(mode_var, "get"):
        try:
            return bool(mode_var.get())
        except Exception:
            return False
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("performance_mode", False))
        except Exception:
            return False
    return False


def _manual_motion_fast_poll_active(app) -> bool:
    if _stream_running_or_paused(app):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    if not bool(getattr(app, "_grbl_ready", False)):
        return False
    if bool(getattr(app, "_alarm_locked", False)):
        return False
    state = _normalize_status_state(app)
    if state.startswith("jog") or state.startswith("hold"):
        return True
    if bool(getattr(app, "_active_joystick_hold_binding", None)):
        return True
    grbl = getattr(app, "grbl", None)
    busy_fn = getattr(grbl, "manual_queue_busy", None)
    if callable(busy_fn):
        try:
            if bool(busy_fn()):
                return True
        except Exception as exc:
            _log_suppressed("Failed checking manual-queue busy state for poll profile", exc)
    try:
        until_ts = float(getattr(app, "_manual_motion_fast_poll_until_ts", 0.0) or 0.0)
    except Exception:
        until_ts = 0.0
    if until_ts > time.monotonic():
        return True
    return False


def _manual_ready_fast_poll_active(app) -> bool:
    if _performance_mode_enabled(app):
        return False
    if _stream_running_or_paused(app):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    if not bool(getattr(app, "_grbl_ready", False)):
        return False
    if bool(getattr(app, "_alarm_locked", False)):
        return False
    return _normalize_status_state(app).startswith("idle")


def _probe_indicator_fast_poll_active(app) -> bool:
    if _stream_running_or_paused(app):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    if not bool(getattr(app, "_grbl_ready", False)):
        return False
    if bool(getattr(app, "_alarm_locked", False)):
        return False
    if bool(getattr(app, "_homing_in_progress", False)):
        return False
    if not _normalize_status_state(app).startswith("idle"):
        return False
    probe_var = getattr(app, "show_probe_indicator", None)
    if probe_var is not None and hasattr(probe_var, "get"):
        try:
            return bool(probe_var.get())
        except Exception:
            return False
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("show_probe_indicator", False))
        except Exception:
            return False
    return False


def _probe_workflow_fast_poll_active(app) -> bool:
    if not _probe_indicator_fast_poll_active(app):
        return False
    return _macro_fast_poll_active(app) or _macro_critical_fast_poll_active(app)


def _macro_fast_poll_active(app) -> bool:
    if _stream_running_or_paused(app):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    try:
        return int(getattr(app, "_macro_active_fast_poll_count", 0) or 0) > 0
    except Exception:
        return False


def _macro_critical_fast_poll_active(app) -> bool:
    if _stream_running_or_paused(app):
        return False
    if not bool(getattr(app, "connected", False)):
        return False
    try:
        return int(getattr(app, "_macro_critical_fast_poll_count", 0) or 0) > 0
    except Exception:
        return False


def mark_manual_motion_activity(app, *, duration_s: float | None = None) -> None:
    now = time.monotonic()
    try:
        duration = float(duration_s) if duration_s is not None else float(_STATUS_POLL_MANUAL_GRACE_S)
    except Exception:
        duration = float(_STATUS_POLL_MANUAL_GRACE_S)
    duration = max(0.1, duration)
    until_ts = now + duration
    current_until_ts = float(getattr(app, "_manual_motion_fast_poll_until_ts", 0.0) or 0.0)
    if until_ts > current_until_ts:
        app._manual_motion_fast_poll_until_ts = until_ts
    try:
        apply_status_poll_profile(app)
    except Exception as exc:
        _log_suppressed("Failed applying fast manual-motion status poll profile", exc)
    after_fn = getattr(app, "after", None)
    if not callable(after_fn):
        return
    pending_after_id = getattr(app, "_manual_motion_fast_poll_after_id", None)
    if pending_after_id is not None and hasattr(app, "after_cancel"):
        try:
            app.after_cancel(pending_after_id)
        except Exception as exc:
            _log_suppressed("Failed cancelling manual-motion poll restore timer", exc)
    app._manual_motion_fast_poll_after_id = None

    def _restore_profile() -> None:
        app._manual_motion_fast_poll_after_id = None
        remaining_s = float(getattr(app, "_manual_motion_fast_poll_until_ts", 0.0) or 0.0) - time.monotonic()
        if remaining_s > 0.05:
            try:
                app._manual_motion_fast_poll_after_id = after_fn(
                    max(50, int(remaining_s * 1000.0) + 25),
                    _restore_profile,
                )
                return
            except Exception as exc:
                _log_suppressed("Failed rescheduling manual-motion poll restore timer", exc)
        try:
            apply_status_poll_profile(app)
        except Exception as exc:
            _log_suppressed("Failed restoring status poll profile after manual-motion boost", exc)

    try:
        app._manual_motion_fast_poll_after_id = after_fn(
            max(50, int(duration * 1000.0) + 25),
            _restore_profile,
        )
    except Exception as exc:
        _log_suppressed("Failed scheduling manual-motion poll restore timer", exc)


def _ensure_auto_reconnect_scan_queue(app):
    result_q = getattr(app, "_auto_reconnect_port_scan_result_q", None)
    if result_q is None:
        result_q = queue.Queue(maxsize=1)
        setattr(app, "_auto_reconnect_port_scan_result_q", result_q)
    return result_q


def _drain_auto_reconnect_port_scan_results(app) -> None:
    result_q = getattr(app, "_auto_reconnect_port_scan_result_q", None)
    latest = None
    if result_q is not None:
        while True:
            try:
                latest = result_q.get_nowait()
            except queue.Empty:
                break
            except Exception as exc:
                _log_suppressed("Failed reading auto-reconnect port-scan queue", exc)
                break
    scan_thread = getattr(app, "_auto_reconnect_port_scan_thread", None)
    if scan_thread is not None and not scan_thread.is_alive():
        setattr(app, "_auto_reconnect_port_scan_inflight", False)
    if latest is None:
        return
    try:
        stamp, ports = latest
    except Exception:
        stamp = time.time()
        ports = latest if isinstance(latest, (list, tuple, set)) else ()
    normalized_ports = tuple(
        str(port).strip()
        for port in ports
        if str(port).strip()
    )
    setattr(app, "_auto_reconnect_ports_cache", normalized_ports)
    try:
        setattr(app, "_auto_reconnect_ports_cache_ts", float(stamp))
    except Exception:
        setattr(app, "_auto_reconnect_ports_cache_ts", time.time())
    setattr(app, "_auto_reconnect_port_scan_inflight", False)


def _auto_reconnect_cache_age_s(app, now: float) -> float:
    try:
        cache_ts = float(getattr(app, "_auto_reconnect_ports_cache_ts", 0.0) or 0.0)
    except Exception:
        cache_ts = 0.0
    if cache_ts <= 0:
        return float("inf")
    return max(0.0, now - cache_ts)


def _auto_reconnect_scan_interval_s(app) -> float:
    try:
        value = float(getattr(app, "_auto_reconnect_port_scan_min_interval_s", 0.0) or 0.0)
    except Exception:
        value = 0.0
    if value <= 0:
        value = _AUTO_RECONNECT_PORT_SCAN_MIN_INTERVAL_S
    return max(0.1, value)


def _start_auto_reconnect_port_scan(app, now: float) -> bool:
    _drain_auto_reconnect_port_scan_results(app)
    if bool(getattr(app, "_auto_reconnect_port_scan_inflight", False)):
        return False
    cache_age_s = _auto_reconnect_cache_age_s(app, now)
    if cache_age_s < _auto_reconnect_scan_interval_s(app):
        return False

    result_q = _ensure_auto_reconnect_scan_queue(app)
    setattr(app, "_auto_reconnect_port_scan_inflight", True)

    def worker() -> None:
        if bool(getattr(app, "_closing", False)):
            setattr(app, "_auto_reconnect_port_scan_inflight", False)
            return
        try:
            ports = tuple(app.grbl.list_ports())
        except Exception as exc:
            ports = ()
            _log_suppressed("Auto-reconnect port scan failed", exc)
        payload = (time.time(), ports)
        try:
            while True:
                result_q.get_nowait()
        except queue.Empty:
            pass
        except Exception as exc:
            _log_suppressed("Failed draining stale auto-reconnect scan results", exc)
        try:
            result_q.put_nowait(payload)
        except Exception as exc:
            _log_suppressed("Failed publishing auto-reconnect scan result", exc)
        finally:
            setattr(app, "_auto_reconnect_port_scan_inflight", False)

    scan_thread = threading.Thread(target=worker, name="auto-reconnect-port-scan", daemon=True)
    setattr(app, "_auto_reconnect_port_scan_thread", scan_thread)
    scan_thread.start()
    return True


def _pi_profile_enabled(app) -> bool:
    var = getattr(app, "pi_profile_enabled", None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("pi_profile_enabled", False))
        except Exception:
            return False
    return False


def _worker_reports_connected(app) -> bool:
    worker = getattr(app, "grbl", None)
    checker = getattr(worker, "is_connected", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception as exc:
        _log_suppressed("Failed checking GRBL worker serial connection state", exc)
        return False


def handle_connection_event(app, is_on: bool, port):
    runtime = get_connection_runtime_state(app)
    runtime.connected = bool(is_on)
    runtime.connecting = False
    runtime.disconnecting = False
    sync_connection_runtime_state_to_app(app, runtime)
    app._homing_in_progress = False
    app._homing_state_seen = False
    app._machine_coordinates_trusted = False
    invalidate_job_setup_state(app)
    alarm_latched = bool(getattr(app, "_alarm_latched", False))
    alarm_message = str(getattr(app, "_alarm_message", "") or "")
    if runtime.connected:
        app._auto_reconnect_last_port = port or app._auto_reconnect_last_port
        app._auto_reconnect_pending = False
        app._auto_reconnect_last_attempt = 0.0
        app._auto_reconnect_retry = 0
        app._auto_reconnect_delay = 3.0
        app._auto_reconnect_next_ts = 0.0
        app._auto_reconnect_startup_gate_ts = 0.0
        app._auto_reconnect_blocked = False
        app._auto_reconnect_port_scan_inflight = False
        app._report_units = None
        app._zero_all_pending_active = False
        app._zero_all_pending_expected_wco_raw = None
        app._zero_all_pending_until_ts = 0.0
        app._zero_all_pending_hard_until_ts = 0.0
        app._zero_all_pending_post_timeout_wco_seen = False
        try:
            app._update_unit_toggle_display()
        except Exception as exc:
            _log_suppressed("Failed refreshing unit toggle display after connect", exc)
        set_toolbar_button_label(app.btn_conn, ICON_CONNECT, "Disconnect")
        app.btn_conn.config(state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling refresh button after connect", exc)
        try:
            app.port_combo.config(state="readonly")
        except Exception as exc:
            _log_suppressed("Failed setting port combobox readonly after connect", exc)
        runtime.connected_port = str(port or "").strip() or None
        runtime.ready = False
        if alarm_latched:
            runtime.alarm_locked = True
        else:
            runtime.alarm_locked = False
            app._alarm_message = ""
        app._pending_settings_refresh = True
        app._pending_modal_sync = True
        app._modal_sync_inflight = False
        app._modal_sync_inflight_started_ts = 0.0
        app._modal_sync_retry_after_ts = 0.0
        runtime.status_seen = False
        sync_connection_runtime_state_to_app(app, runtime)
        _clear_status_frame_cache(app)
        try:
            connect_settling_s = float(
                getattr(app, "_status_connect_settling_window_s", _STATUS_CONNECT_SETTLING_WINDOW_S)
                or _STATUS_CONNECT_SETTLING_WINDOW_S
            )
        except Exception:
            connect_settling_s = _STATUS_CONNECT_SETTLING_WINDOW_S
        _arm_status_connect_settling(app, duration_s=connect_settling_s)
        connected_text = MachineStateMessages.connected(port)
        app.machine_state.set(connected_text)
        app._machine_state_text = connected_text
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception as exc:
            _log_suppressed("Failed ensuring machine-state label width after connect", exc)
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text=StatusMessages.connected_waiting_for_grbl(port))
        app.btn_stop.config(state="normal")
        disable_job_controls(app)
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app.throughput_var.set("TX: 0 B/s")
        try:
            _restore_loaded_gcode_after_connect(app)
        except Exception as exc:
            _handle_reconnect_gcode_restore_failure(app, exc)
        _record_connection_timeline(
            app,
            "connected",
            _format_connection_timeline_details(
                port=port,
                alarm_latched=alarm_latched,
                resume_pending=bool(getattr(app, "_resume_after_disconnect", False)),
                restore_failed=bool(getattr(app, "_gcode_restore_failed", False)),
            ),
        )
        if alarm_latched:
            alarm_status = alarm_message or "ALARM latched: verify machine, then use Unlock ($X) or Home ($H)."
            try:
                app._set_alarm_lock(True, alarm_status)
            except Exception as exc:
                _log_suppressed("Failed restoring latched alarm lock after reconnect", exc)
    else:
        preserve_latched_alarm = bool(alarm_latched) and not bool(getattr(app, "_user_disconnect", False))
        _record_connection_timeline(
            app,
            "disconnected",
            _format_connection_timeline_details(
                port=getattr(app, "_connected_port", None),
                user_disconnect=bool(getattr(app, "_user_disconnect", False)),
                preserve_alarm=preserve_latched_alarm,
                resume_pending=bool(getattr(app, "_resume_after_disconnect", False)),
            ),
        )
        try:
            app._stop_macro_status()
        except Exception as exc:
            _log_suppressed("Failed stopping macro status poll after disconnect", exc)
        set_toolbar_button_label(app.btn_conn, ICON_CONNECT, "Connect")
        app.btn_conn.config(state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling refresh button after disconnect", exc)
        try:
            app.port_combo.config(state="readonly")
        except Exception as exc:
            _log_suppressed("Failed setting port combobox readonly after disconnect", exc)
        runtime.connected_port = None
        runtime.ready = False
        runtime.alarm_locked = False
        if preserve_latched_alarm:
            app._alarm_message = alarm_message
            app._alarm_recovery_log_key = None
        else:
            app._alarm_latched = False
            app._alarm_clear_requested = False
            app._alarm_message = ""
        app._pending_settings_refresh = False
        clear_modal_sync_state(app)
        setattr(app, "_pending_unit_mode", None)
        runtime.status_seen = False
        _clear_status_frame_cache(app)
        runtime.status_connect_settling_until_ts = 0.0
        sync_connection_runtime_state_to_app(app, runtime)
        app._report_units = None
        app._zero_all_pending_active = False
        app._zero_all_pending_expected_wco_raw = None
        app._zero_all_pending_until_ts = 0.0
        app._zero_all_pending_hard_until_ts = 0.0
        app._zero_all_pending_post_timeout_wco_seen = False
        try:
            app._update_unit_toggle_display()
        except Exception as exc:
            _log_suppressed("Failed refreshing unit toggle display after disconnect", exc)
        app.machine_state.set(MachineStateMessages.DISCONNECTED)
        app._machine_state_text = MachineStateMessages.DISCONNECTED
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception as exc:
            _log_suppressed("Failed ensuring machine-state label width after disconnect", exc)
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text=StatusMessages.DISCONNECTED)
        disable_job_controls(app)
        app.btn_stop.config(state="disabled")
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app._rapid_rates = None
        app._rapid_rates_source = None
        app._accel_rates = None
        if app._last_gcode_lines:
            app._update_gcode_stats(app._last_gcode_lines)
        if app._user_disconnect:
            app._resume_after_disconnect = False
            app._resume_from_index = None
            app._resume_job_name = None
            app._auto_reconnect_pending = False
            app._auto_reconnect_retry = 0
            app._auto_reconnect_next_ts = 0.0
            app._auto_reconnect_blocked = True
        if not app._user_disconnect:
            app._auto_reconnect_pending = True
            app._auto_reconnect_retry = 0
            app._auto_reconnect_delay = 3.0
            app._auto_reconnect_next_ts = 0.0
        app._user_disconnect = False
        app.throughput_var.set("TX: 0 B/s")
    _signal_thread_event(app, "_connection_state_event")
    _signal_thread_event(app, "_status_update_event")
    apply_status_poll_profile(app)


def handle_ready_event(app, ready):
    runtime = get_connection_runtime_state(app)
    runtime.ready = bool(ready)
    sync_connection_runtime_state_to_app(app, runtime)
    if not runtime.ready:
        invalidate_job_setup_state(app)
        _record_connection_timeline(
            app,
            "ready_false",
            _format_connection_timeline_details(
                port=runtime.connected_port,
                alarm_latched=bool(getattr(app, "_alarm_latched", False)),
                connected=bool(runtime.connected),
            ),
        )
        runtime.status_seen = False
        _clear_status_frame_cache(app)
        runtime.alarm_locked = False
        sync_connection_runtime_state_to_app(app, runtime)
        if not bool(getattr(app, "_alarm_latched", False)):
            app._alarm_message = ""
        if runtime.connected:
            disable_job_controls(app)
            app._set_manual_controls_enabled(False)
            if runtime.connected_port:
                app.status.config(
                    text=StatusMessages.connected_waiting_for_grbl(runtime.connected_port)
                )
        apply_status_poll_profile(app)
        return
    if runtime.alarm_locked:
        return
    if runtime.connected and runtime.connected_port:
        _record_connection_timeline(
            app,
            "ready_true",
            _format_connection_timeline_details(
                port=runtime.connected_port,
                restore_failed=bool(getattr(app, "_gcode_restore_failed", False)),
                modal_sync_pending=bool(getattr(app, "_pending_modal_sync", False)),
                resume_pending=bool(getattr(app, "_resume_after_disconnect", False)),
            ),
        )
        restore_failure_message = str(
            getattr(app, "_gcode_restore_failure_message", "") or ""
        ).strip()
        app.status.config(
            text=restore_failure_message or StatusMessages.connected(runtime.connected_port)
        )
        try:
            ready_tail_s = float(
                getattr(
                    app,
                    "_status_connect_settling_ready_tail_s",
                    _STATUS_CONNECT_SETTLING_READY_TAIL_S,
                )
                or _STATUS_CONNECT_SETTLING_READY_TAIL_S
            )
        except Exception:
            ready_tail_s = _STATUS_CONNECT_SETTLING_READY_TAIL_S
        _arm_status_connect_settling(app, duration_s=ready_tail_s)
        request_modal_state_sync(
            app,
            source="status",
            failure_status="Connected, but modal-state sync is pending retry.",
            failure_log="[status] Connected, but $G modal sync was rejected; retry pending.",
        )
        if bool(getattr(app, "_gcode_restore_failed", False)):
            disable_job_controls(app)
            app._resume_after_disconnect = False
            app._resume_from_index = None
            app._resume_job_name = None
            apply_status_poll_profile(app)
            return
        if getattr(app, "_resume_after_disconnect", False) and not runtime.alarm_locked:
            app._resume_after_disconnect = False
            total_lines = (
                app._gcode_total_lines
                if getattr(app, "_gcode_streaming_mode", False)
                else len(app._last_gcode_lines)
            )
            if total_lines > 0:
                start_index = app._resume_from_index
                if start_index is None:
                    start_index = max(0, app._last_acked_index + 1)
                start_index = max(0, min(start_index, total_lines - 1))
                job_name = app._resume_job_name or os.path.basename(
                    getattr(app, "_last_gcode_path", "") or ""
                )
                label = f" '{job_name}'" if job_name else ""
                prompt = f"Resume interrupted job{label} from line {start_index + 1}?"
                if messagebox.askyesno("Resume job", prompt):
                    _schedule_reconnect_resume(app, start_index=start_index)
            app._resume_from_index = None
            app._resume_job_name = None
    apply_status_poll_profile(app)


def maybe_auto_reconnect(app):
    if app.connected or app._closing or (not app._auto_reconnect_pending):
        if bool(getattr(app, "connected", False)) and bool(getattr(app, "_grbl_ready", False)):
            app._auto_reconnect_pending = False
        return
    # Prevent duplicate connect attempts while the worker already has an
    # open serial connection and the UI is still processing conn events.
    if _worker_reports_connected(app):
        if bool(getattr(app, "_grbl_ready", False)):
            app._auto_reconnect_pending = False
        return
    if getattr(app, "_user_disconnect", False):
        return
    if getattr(app, "_auto_reconnect_blocked", False):
        return
    if app._connecting:
        return
    if not app._auto_reconnect_last_port:
        return
    try:
        if not bool(app.reconnect_on_open.get()):
            app._auto_reconnect_pending = False
            return
    except Exception as exc:
        _log_suppressed("Failed reading reconnect-on-open setting during auto-reconnect", exc)
    now = time.time()
    _drain_auto_reconnect_port_scan_results(app)
    cached_ports = tuple(getattr(app, "_auto_reconnect_ports_cache", ()) or ())
    cache_age = _auto_reconnect_cache_age_s(app, now)
    startup_gate_ts = float(getattr(app, "_auto_reconnect_startup_gate_ts", 0.0) or 0.0)
    if startup_gate_ts > 0.0 and now < startup_gate_ts:
        if app._auto_reconnect_last_port in cached_ports:
            app._auto_reconnect_startup_gate_ts = 0.0
            _record_connection_timeline(
                app,
                "startup_autoconnect_gate_bypassed",
                f"port={app._auto_reconnect_last_port}",
            )
        else:
            if cache_age == float("inf") or app._auto_reconnect_last_port not in cached_ports:
                _start_auto_reconnect_port_scan(app, now)
            app._auto_reconnect_next_ts = min(
                startup_gate_ts,
                now + min(1.0, float(app._auto_reconnect_delay)),
            )
            return
    if now < app._auto_reconnect_next_ts:
        return
    if cache_age == float("inf"):
        _start_auto_reconnect_port_scan(app, now)
        app._auto_reconnect_next_ts = now + min(1.0, app._auto_reconnect_delay)
        return
    try:
        max_cache_age = float(
            getattr(app, "_auto_reconnect_port_scan_cache_max_age_s", _AUTO_RECONNECT_PORT_SCAN_CACHE_MAX_AGE_S)
            or _AUTO_RECONNECT_PORT_SCAN_CACHE_MAX_AGE_S
        )
    except Exception:
        max_cache_age = _AUTO_RECONNECT_PORT_SCAN_CACHE_MAX_AGE_S
    if max_cache_age <= 0:
        max_cache_age = _AUTO_RECONNECT_PORT_SCAN_CACHE_MAX_AGE_S
    if cache_age >= max_cache_age:
        _start_auto_reconnect_port_scan(app, now)
    if app._auto_reconnect_last_port not in cached_ports:
        scan_started = _start_auto_reconnect_port_scan(app, now)
        # If we've exceeded retries, allow a cool-down retry later.
        if app._auto_reconnect_retry >= app._auto_reconnect_max_retry:
            app._auto_reconnect_next_ts = now + max(30.0, app._auto_reconnect_delay)
            app._auto_reconnect_pending = True
        else:
            if scan_started or bool(getattr(app, "_auto_reconnect_port_scan_inflight", False)):
                app._auto_reconnect_next_ts = now + min(1.0, app._auto_reconnect_delay)
            else:
                app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
        return
    app._auto_reconnect_last_attempt = now
    app.current_port.set(app._auto_reconnect_last_port)
    app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
    _record_connection_timeline(
        app,
        "auto_reconnect_attempt",
        f"port={app._auto_reconnect_last_port} retry={app._auto_reconnect_retry}",
    )
    app._start_connect_worker(
        app._auto_reconnect_last_port,
        show_error=False,
        on_failure=app._handle_auto_reconnect_failure,
    )


def handle_auto_reconnect_failure(app, exc: Exception):
    now = time.time()
    app.ui_q.put(("log", f"[auto-reconnect] Attempt failed: {exc}"))
    _record_connection_timeline(app, "auto_reconnect_failed", str(exc))
    app._auto_reconnect_retry += 1
    if app._auto_reconnect_retry > app._auto_reconnect_max_retry:
        app._auto_reconnect_delay = 30.0
    else:
        app._auto_reconnect_delay = min(30.0, app._auto_reconnect_delay * 1.5)
    app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
    app._auto_reconnect_pending = True


def status_poll_profile(app) -> tuple[str, float, str]:
    try:
        base = float(app.status_poll_interval.get())
    except Exception:
        base = STATUS_POLL_DEFAULT
    if base <= 0:
        base = STATUS_POLL_DEFAULT
    if _probe_workflow_fast_poll_active(app):
        return (
            "probing_fast",
            min(base, float(_STATUS_POLL_PROBE_INDICATOR)),
            "probe indicator visible during macro/probe workflow",
        )
    if _macro_critical_fast_poll_active(app):
        return (
            "macro_critical_fast",
            min(base, float(_STATUS_POLL_MACRO_CRITICAL)),
            "macro critical wait active",
        )
    if _macro_fast_poll_active(app):
        return (
            "macro_active_fast",
            min(base, float(_STATUS_POLL_MACRO_ACTIVE)),
            "macro command wait active",
        )
    if _manual_motion_fast_poll_active(app):
        return (
            "manual_motion_fast",
            min(base, float(_STATUS_POLL_MANUAL_ACTIVE)),
            "manual motion or responsive window active",
        )
    if _probe_indicator_fast_poll_active(app):
        return (
            "probe_indicator_idle",
            min(base, float(_STATUS_POLL_PROBE_INDICATOR_IDLE)),
            "probe indicator visible while idle",
        )
    if _manual_ready_fast_poll_active(app):
        return (
            "connected_idle_ready",
            min(base, float(_STATUS_POLL_MANUAL_IDLE_READY)),
            "connected idle ready state",
        )
    if _status_poll_should_use_running_profile(app):
        state = _normalize_status_state(app)
        if _stream_running_or_paused(app):
            profile = "streaming"
            reason = "streaming running or paused"
        elif (
            state.startswith("hold")
            or state.startswith("alarm")
            or bool(getattr(app, "_alarm_locked", False))
        ):
            profile = "alarm_hold"
            reason = "alarm or hold state"
        elif bool(getattr(app, "connected", False)) and not bool(
            getattr(app, "_grbl_ready", False)
        ):
            profile = "connecting"
            reason = "connected before ready"
        else:
            profile = "machine_running"
            reason = "machine state requires running poll rate"
        running_floor = float(STATUS_POLL_RUNNING)
        if _performance_mode_enabled(app):
            running_floor = max(running_floor, float(_STATUS_POLL_PERF_RUNNING))
        return (profile, min(base, running_floor), reason)
    if _performance_mode_enabled(app):
        idle_floor = float(_STATUS_POLL_PERF_IDLE_FLOOR)
        profile = "connected_idle_perf"
        reason = "performance mode idle floor"
        try:
            last_non_idle_ts = float(getattr(app, "_status_last_non_idle_ts", 0.0) or 0.0)
        except Exception:
            last_non_idle_ts = 0.0
        quiet_idle_elapsed_s = 0.0
        if last_non_idle_ts > 0.0:
            quiet_idle_elapsed_s = max(0.0, time.monotonic() - last_non_idle_ts)
        if (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and not bool(getattr(app, "_alarm_locked", False))
            and _normalize_status_state(app).startswith("idle")
            and quiet_idle_elapsed_s >= float(_STATUS_POLL_QUIET_IDLE_MIN_SECONDS)
        ):
            idle_floor = max(idle_floor, float(_STATUS_POLL_PERF_QUIET_IDLE_FLOOR))
            profile = "connected_idle_quiet"
            reason = "performance mode quiet idle floor"
        if (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and not bool(getattr(app, "_alarm_locked", False))
            and _normalize_status_state(app).startswith("idle")
            and quiet_idle_elapsed_s >= float(_STATUS_POLL_ULTRA_QUIET_IDLE_MIN_SECONDS)
        ):
            idle_floor = max(idle_floor, float(_STATUS_POLL_PERF_ULTRA_QUIET_IDLE_FLOOR))
            profile = "connected_idle_ultra_quiet"
            reason = "performance mode long-idle floor"
        base = max(base, idle_floor)
        return (profile, base, reason)
    return (
        "connected_idle",
        max(base, float(STATUS_POLL_IDLE)),
        "default connected idle floor",
    )


def effective_status_poll_interval(app) -> float:
    return status_poll_profile(app)[1]


def apply_status_poll_profile(app):
    profile, interval, reason = status_poll_profile(app)
    app.grbl.set_status_poll_interval(interval)
    previous_profile = str(getattr(app, "_status_poll_profile", "") or "")
    try:
        previous_interval = float(
            getattr(app, "_status_poll_interval_effective_s", 0.0) or 0.0
        )
    except Exception:
        previous_interval = 0.0
    app._status_poll_profile = profile
    app._status_poll_profile_reason = reason
    app._status_poll_interval_effective_s = float(interval)
    if previous_profile != profile or abs(previous_interval - float(interval)) > 1e-9:
        logger.info(
            "Status poll profile changed: poll_profile=%s interval=%.3fs reason=%s",
            profile,
            float(interval),
            reason,
        )
