import time

from simple_sender.ui.icons import ICON_CONNECT, icon_label
from simple_sender.utils.constants import STATUS_POLL_DEFAULT


def handle_connection_event(app, is_on: bool, port):
    app.connected = bool(is_on)
    app._homing_in_progress = False
    app._homing_state_seen = False
    if app.connected:
        app._auto_reconnect_last_port = port or app._auto_reconnect_last_port
        app._auto_reconnect_pending = False
        app._auto_reconnect_last_attempt = 0.0
        app._auto_reconnect_retry = 0
        app._auto_reconnect_delay = 3.0
        app._auto_reconnect_next_ts = 0.0
        app._report_units = None
        try:
            app._update_unit_toggle_display()
        except Exception:
            pass
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Disconnect"))
        app._connected_port = port
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = True
        app._status_seen = False
        app.machine_state.set(f"CONNECTED ({port})")
        app._machine_state_text = f"CONNECTED ({port})"
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text=f"Connected: {port} (waiting for Grbl)")
        app.btn_stop.config(state="normal")
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app.throughput_var.set("TX: 0 B/s")
    else:
        app.btn_conn.config(text=icon_label(ICON_CONNECT, "Connect"))
        app._connected_port = None
        app._grbl_ready = False
        app._alarm_locked = False
        app._alarm_message = ""
        app._pending_settings_refresh = False
        app._status_seen = False
        app._report_units = None
        try:
            app._update_unit_toggle_display()
        except Exception:
            pass
        app.machine_state.set("DISCONNECTED")
        app._machine_state_text = "DISCONNECTED"
        app._update_state_highlight(app._machine_state_text)
        app.status.config(text="Disconnected")
        app.btn_run.config(state="disabled")
        app.btn_pause.config(state="disabled")
        app.btn_resume.config(state="disabled")
        app.btn_stop.config(state="disabled")
        app.btn_resume_from.config(state="disabled")
        app.btn_alarm_recover.config(state="disabled")
        app._set_manual_controls_enabled(False)
        app._rapid_rates = None
        app._rapid_rates_source = None
        app._accel_rates = None
        if app._last_gcode_lines:
            app._update_gcode_stats(app._last_gcode_lines)
        if not app._user_disconnect:
            app._auto_reconnect_pending = True
            app._auto_reconnect_retry = 0
            app._auto_reconnect_delay = 3.0
            app._auto_reconnect_next_ts = 0.0
        app._user_disconnect = False
        app.throughput_var.set("TX: 0 B/s")
    apply_status_poll_profile(app)


def handle_ready_event(app, ready):
    app._grbl_ready = bool(ready)
    if not app._grbl_ready:
        app._status_seen = False
        app._alarm_locked = False
        app._alarm_message = ""
        if app.connected:
            app.btn_run.config(state="disabled")
            app.btn_pause.config(state="disabled")
            app.btn_resume.config(state="disabled")
            app.btn_resume_from.config(state="disabled")
            app._set_manual_controls_enabled(False)
            if app._connected_port:
                app.status.config(text=f"Connected: {app._connected_port} (waiting for Grbl)")
        apply_status_poll_profile(app)
        return
    if app._alarm_locked:
        return
    if app.connected and app._connected_port:
        app.status.config(text=f"Connected: {app._connected_port}")
        try:
            app._send_manual("$G", "status")
        except Exception:
            pass
        try:
            app._send_manual("$$", "status")
        except Exception:
            pass
    apply_status_poll_profile(app)


def maybe_auto_reconnect(app):
    if app.connected or app._closing or (not app._auto_reconnect_pending):
        return
    if app._connecting:
        return
    if not app._auto_reconnect_last_port:
        return
    try:
        if not bool(app.reconnect_on_open.get()):
            app._auto_reconnect_pending = False
            return
    except Exception:
        pass
    now = time.time()
    if now < app._auto_reconnect_next_ts:
        return
    ports = app.grbl.list_ports()
    if app._auto_reconnect_last_port not in ports:
        # If we've exceeded retries, allow a cool-down retry later.
        if app._auto_reconnect_retry >= app._auto_reconnect_max_retry:
            app._auto_reconnect_next_ts = now + max(30.0, app._auto_reconnect_delay)
            app._auto_reconnect_pending = True
        else:
            app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
        return
    app._auto_reconnect_last_attempt = now
    app.current_port.set(app._auto_reconnect_last_port)
    app._auto_reconnect_next_ts = now + app._auto_reconnect_delay
    app._start_connect_worker(
        app._auto_reconnect_last_port,
        show_error=False,
        on_failure=app._handle_auto_reconnect_failure,
    )


def handle_auto_reconnect_failure(app, exc: Exception):
    now = time.time()
    app.ui_q.put(("log", f"[auto-reconnect] Attempt failed: {exc}"))
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
    return base


def apply_status_poll_profile(app):
    interval = effective_status_poll_interval(app)
    app.grbl.set_status_poll_interval(interval)
