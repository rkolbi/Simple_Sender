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
from collections import deque
from tkinter import messagebox

from simple_sender.ui.icons import ICON_CONNECT, icon_label
from simple_sender.ui.job_controls import disable_job_controls
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
_STATUS_POLL_MANUAL_GRACE_S = 2.0
_CONNECTION_TIMELINE_LIMIT = 200


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _signal_thread_event(obj, attr_name: str) -> None:
    evt = getattr(obj, attr_name, None)
    if isinstance(evt, threading.Event):
        try:
            evt.set()
        except Exception as exc:
            _log_suppressed(f"Failed signaling thread event {attr_name}", exc)


def _record_connection_timeline(app, event: str, details: str = "") -> None:
    history = getattr(app, "_connection_timeline", None)
    if history is None:
        history = deque(maxlen=_CONNECTION_TIMELINE_LIMIT)
        setattr(app, "_connection_timeline", history)
    stamp = time.time()
    payload = {
        "ts": float(stamp),
        "event": str(event or "").strip() or "unknown",
        "details": str(details or "").strip(),
    }
    try:
        history.append(payload)
    except Exception as exc:
        _log_suppressed("Failed appending connection timeline event", exc)


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
    app.connected = bool(is_on)
    app._connecting = False
    app._disconnecting = False
    app._homing_in_progress = False
    app._homing_state_seen = False
    if app.connected:
        _record_connection_timeline(app, "connected", f"port={port or ''}")
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
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Disconnect"), state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling refresh button after connect", exc)
        try:
            app.port_combo.config(state="readonly")
        except Exception as exc:
            _log_suppressed("Failed setting port combobox readonly after connect", exc)
        app._connected_port = port
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = True
        app._status_seen = False
        app.machine_state.set(f"CONNECTED ({port})")
        app._machine_state_text = f"CONNECTED ({port})"
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception as exc:
            _log_suppressed("Failed ensuring machine-state label width after connect", exc)
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text=f"Connected: {port} (waiting for Grbl)")
        app.btn_stop.config(state="normal")
        disable_job_controls(app)
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app.throughput_var.set("TX: 0 B/s")
        try:
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
            elif app._last_gcode_lines:
                name = os.path.basename(getattr(app, "_last_gcode_path", "") or "")
                app.grbl.load_gcode(app._last_gcode_lines, name=name or None)
        except Exception as exc:
            _log_suppressed("Failed restoring loaded G-code after connect", exc)
    else:
        _record_connection_timeline(app, "disconnected")
        try:
            app._stop_macro_status()
        except Exception as exc:
            _log_suppressed("Failed stopping macro status poll after disconnect", exc)
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Connect"), state="normal")
        try:
            app.btn_refresh.config(state="normal")
        except Exception as exc:
            _log_suppressed("Failed enabling refresh button after disconnect", exc)
        try:
            app.port_combo.config(state="readonly")
        except Exception as exc:
            _log_suppressed("Failed setting port combobox readonly after disconnect", exc)
        app._connected_port = None
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = False
        app._status_seen = False
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
        app.machine_state.set("DISCONNECTED")
        app._machine_state_text = "DISCONNECTED"
        try:
            app._ensure_state_label_width(app._machine_state_text)
        except Exception as exc:
            _log_suppressed("Failed ensuring machine-state label width after disconnect", exc)
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text="Disconnected")
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
    app._grbl_ready = bool(ready)
    if not app._grbl_ready:
        _record_connection_timeline(app, "ready_false")
        app._status_seen = False
        app._alarm_locked = False
        app._alarm_message = ""
        if app.connected:
            disable_job_controls(app)
            app._set_manual_controls_enabled(False)
            if app._connected_port:
                app.status.config(text=f"Connected: {app._connected_port} (waiting for Grbl)")
        apply_status_poll_profile(app)
        return
    if app._alarm_locked:
        return
    if app.connected and app._connected_port:
        _record_connection_timeline(app, "ready_true", f"port={app._connected_port}")
        app.status.config(text=f"Connected: {app._connected_port}")
        try:
            app._send_manual("$G", "status")
        except Exception as exc:
            _log_suppressed("Failed requesting modal state with $G after ready", exc)
        if getattr(app, "_resume_after_disconnect", False) and not app._alarm_locked:
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
                    preamble, _ = app._build_resume_preamble(app._last_gcode_lines, start_index)
                    app._resume_from_line(start_index, preamble)
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


def effective_status_poll_interval(app) -> float:
    try:
        base = float(app.status_poll_interval.get())
    except Exception:
        base = STATUS_POLL_DEFAULT
    if base <= 0:
        base = STATUS_POLL_DEFAULT
    if _manual_motion_fast_poll_active(app):
        return min(base, float(_STATUS_POLL_MANUAL_ACTIVE))
    if _manual_ready_fast_poll_active(app):
        return min(base, float(_STATUS_POLL_MANUAL_IDLE_READY))
    if _status_poll_should_use_running_profile(app):
        running_floor = float(STATUS_POLL_RUNNING)
        if _performance_mode_enabled(app):
            running_floor = max(running_floor, float(_STATUS_POLL_PERF_RUNNING))
        return min(base, running_floor)
    if _performance_mode_enabled(app):
        idle_floor = float(_STATUS_POLL_PERF_IDLE_FLOOR)
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
        if (
            bool(getattr(app, "connected", False))
            and bool(getattr(app, "_grbl_ready", False))
            and not bool(getattr(app, "_alarm_locked", False))
            and _normalize_status_state(app).startswith("idle")
            and quiet_idle_elapsed_s >= float(_STATUS_POLL_ULTRA_QUIET_IDLE_MIN_SECONDS)
        ):
            idle_floor = max(idle_floor, float(_STATUS_POLL_PERF_ULTRA_QUIET_IDLE_FLOOR))
        base = max(base, idle_floor)
    return max(base, float(STATUS_POLL_IDLE))


def apply_status_poll_profile(app):
    interval = effective_status_poll_interval(app)
    app.grbl.set_status_poll_interval(interval)
