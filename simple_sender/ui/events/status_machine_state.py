import time
from collections.abc import Callable

from .status_parsing import _status_state_token


def _status_allows_alarm_clear(app) -> bool:
    if not bool(getattr(app, "_alarm_locked", False)):
        return False
    if not bool(getattr(app, "_alarm_latched", False)):
        return True
    return bool(getattr(app, "_alarm_clear_requested", False))


def _resolve_display_state(
    app,
    state: str,
    *,
    homing_idle_grace_seconds: Callable[[object], float],
    log_suppressed: Callable[[str, BaseException], None],
) -> str:
    state_lower = state.lower()
    display_state = "Homing" if state_lower.startswith("home") else state
    if not getattr(app, "_homing_in_progress", False):
        return display_state
    if state_lower.startswith("home"):
        app._homing_state_seen = True
        return "Homing"
    if state_lower.startswith("idle"):
        start_ts = getattr(app, "_homing_start_ts", 0.0)
        timeout_s = getattr(app, "_homing_timeout_s", 30.0)
        elapsed = max(0.0, (time.time() - start_ts)) if start_ts else 0.0
        timed_out = bool(start_ts) and elapsed > timeout_s
        grace_elapsed = (not start_ts) or (elapsed >= homing_idle_grace_seconds(app))
        if getattr(app, "_homing_state_seen", False) or timed_out or grace_elapsed:
            app._homing_in_progress = False
            app._homing_state_seen = False
            try:
                app.grbl.clear_watchdog_ignore("homing")
            except Exception as exc:
                log_suppressed("Failed clearing homing watchdog ignore on idle", exc)
            return state
        return "Homing"

    app._homing_in_progress = False
    app._homing_state_seen = False
    try:
        app.grbl.clear_watchdog_ignore("homing")
    except Exception as exc:
        context = (
            "Failed clearing homing watchdog ignore on alarm/door"
            if (state_lower.startswith("alarm") or state_lower.startswith("door"))
            else "Failed clearing homing watchdog ignore on other state"
        )
        log_suppressed(context, exc)
    return state


def _format_hhmm(seconds: int) -> str:
    total_minutes = int(round(seconds / 60)) if seconds else 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def _run_progress_pct_from_bytes(app) -> float | None:
    try:
        file_size = int(
            getattr(app, "_stream_progress_file_size_bytes", 0)
            or getattr(app, "_gcode_file_size_bytes", 0)
            or 0
        )
    except Exception:
        file_size = 0
    if file_size <= 0:
        return None
    try:
        acked = int(getattr(app, "_stream_acked_byte_offset", 0) or 0)
    except Exception:
        acked = 0
    acked = max(0, min(file_size, acked))
    return max(0.0, min(100.0, (float(acked) / float(file_size)) * 100.0))


def _run_progress_pct_from_lines(app) -> tuple[float | None, bool]:
    try:
        total = int(getattr(app, "_gcode_executable_lines", 0) or 0)
    except Exception:
        total = 0
    if total > 0:
        known = bool(getattr(app, "_gcode_executable_lines_known", True))
    else:
        try:
            total = int(getattr(app, "_gcode_total_lines", 0) or 0)
        except Exception:
            total = 0
        known = bool(getattr(app, "_gcode_total_lines_known", True))
    if total <= 0:
        return None, False
    try:
        done = int(getattr(app, "_last_acked_index", -1) or -1) + 1
    except Exception:
        done = 0
    done = max(0, min(total, done))
    return max(0.0, min(100.0, (float(done) / float(total)) * 100.0)), bool(known)


def _run_progress_text(app) -> str:
    line_pct, line_known = _run_progress_pct_from_lines(app)
    byte_pct = _run_progress_pct_from_bytes(app)
    if line_pct is not None and line_known:
        pct = line_pct
    elif line_pct is not None:
        # Even estimated executable-line totals are a closer proxy for job
        # completion than raw file-byte offsets, which are skewed heavily by
        # comments, directives, and uneven line lengths.
        pct = line_pct
    elif byte_pct is not None:
        pct = byte_pct
    else:
        return "n/a"
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    done_pending_idle = bool(getattr(app, "_stream_done_pending_idle", False))
    if stream_state != "done" or done_pending_idle:
        pct = min(pct, 99.9)
    return f"{int(pct)}%"


def _stream_latched_banner_state(app, state: str, display_state: str) -> str:
    stream_state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    if stream_state == "running" or bool(getattr(app, "_stream_done_pending_idle", False)):
        return "Run"
    return str(display_state or state or "")


def _render_machine_state_text(app, state: str, display_state: str) -> str:
    state_lower = str(state or "").strip().lower()
    display_lower = str(display_state or "").strip().lower()
    if state_lower.startswith("run") or display_lower.startswith("run"):
        return f"Run: {_run_progress_text(app)}"
    return display_state


def _machine_state_highlight_key(state: str) -> str:
    return _status_state_token(state).strip().lower()


def _apply_machine_state_visuals(
    app,
    *,
    rendered_state: str,
    banner_state: str,
    width_context: str,
    highlight_context: str,
    set_var_if_changed: Callable[[object, str], bool],
    machine_state_highlight_key: Callable[[str], str],
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    rendered_changed = set_var_if_changed(app.machine_state, rendered_state)
    if rendered_changed:
        try:
            app._ensure_state_label_width(rendered_state)
        except Exception as exc:
            log_suppressed(width_context, exc)
    highlight_key = machine_state_highlight_key(banner_state)
    previous_highlight_key = str(getattr(app, "_machine_state_highlight_key", "") or "")
    if highlight_key != previous_highlight_key:
        setattr(app, "_machine_state_highlight_key", highlight_key)
        try:
            app._update_state_highlight(banner_state)
        except Exception as exc:
            log_suppressed(highlight_context, exc)
