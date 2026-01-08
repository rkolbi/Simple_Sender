import logging
from typing import Any

logger = logging.getLogger(__name__)

JOYSTICK_AXIS_THRESHOLD = 0.7
JOYSTICK_AXIS_RELEASE_THRESHOLD = 0.2
JOYSTICK_HOLD_REPEAT_MS = 60
JOYSTICK_HOLD_POLL_INTERVAL_MS = 20
JOYSTICK_HOLD_MISS_LIMIT = 2

JOYSTICK_HOLD_DEFINITIONS: list[tuple[str, str, str, int]] = [
    ("X-", "jog_hold_x_minus", "X", -1),
    ("X+", "jog_hold_x_plus", "X", 1),
    ("Y-", "jog_hold_y_minus", "Y", -1),
    ("Y+", "jog_hold_y_plus", "Y", 1),
    ("Z-", "jog_hold_z_minus", "Z", -1),
    ("Z+", "jog_hold_z_plus", "Z", 1),
]
JOYSTICK_HOLD_MAP = {binding_id: (axis, direction) for _, binding_id, axis, direction in JOYSTICK_HOLD_DEFINITIONS}


def is_virtual_hold_button(btn) -> bool:
    return getattr(btn, "_hold_axis", None) is not None


def hold_vector_for_binding(app, binding_id: str) -> tuple[str, int] | None:
    info = JOYSTICK_HOLD_MAP.get(binding_id)
    if not info:
        return None
    axis, direction = info
    return axis, direction


def jog_feed_for_axis(app, axis: str) -> float:
    feed = app.jog_feed_z.get() if axis == "Z" else app.jog_feed_xy.get()
    try:
        return float(feed)
    except Exception:
        return 0.0


def _joystick_binding_pressed(app, binding: dict[str, Any] | None, *, release: bool = False) -> bool:
    if not binding:
        return False
    py = app._get_pygame_module()
    if py is not None:
        try:
            py.event.pump()
        except Exception:
            pass
    joy_id = binding.get("joy_id")
    joy = app._joystick_instances.get(joy_id)
    if joy is None:
        return False
    kind = binding.get("kind")
    try:
        if kind == "button":
            return bool(joy.get_button(binding.get("index")))
        if kind == "axis":
            idx = binding.get("index")
            direction = binding.get("direction")
            if idx is None or direction is None:
                return False
            value = float(joy.get_axis(idx))
            threshold = JOYSTICK_AXIS_RELEASE_THRESHOLD if release else JOYSTICK_AXIS_THRESHOLD
            if direction == 1:
                return value >= threshold
            if direction == -1:
                return value <= -threshold
            return False
        if kind == "hat":
            idx = binding.get("index")
            expected = binding.get("value")
            if idx is None or expected is None:
                return False
            current = joy.get_hat(idx)
            if not isinstance(current, tuple):
                current = tuple(current) if isinstance(current, (list, tuple)) else (current, 0)
            if isinstance(expected, (list, tuple)):
                expected = tuple(expected)
            return current == expected
    except Exception:
        return False
    return False


def start_hold(app, binding_id: str):
    if not binding_id:
        return
    if app._active_joystick_hold_binding == binding_id:
        return
    stop_hold(app)
    hold_axis = hold_vector_for_binding(app, binding_id)
    if hold_axis is None:
        return
    app._active_joystick_hold_binding = binding_id
    app._joystick_hold_missed_polls = 0
    app._send_hold_jog()


def send_hold_jog(app):
    binding_id = app._active_joystick_hold_binding
    if not binding_id:
        return
    if not app.joystick_bindings_enabled.get():
        stop_hold(app, binding_id)
        return
    state_text = str(getattr(app, "_machine_state_text", "")).strip().lower()
    if state_text and not (state_text.startswith("idle") or state_text.startswith("jog")):
        stop_hold(app, binding_id)
        return
    try:
        if app.grbl.manual_queue_backpressure():
            app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)
            return
    except Exception:
        try:
            if app.grbl.manual_queue_busy():
                app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)
                return
        except Exception:
            pass
    binding = app._joystick_bindings.get(binding_id)
    if not _joystick_binding_pressed(app, binding, release=True):
        stop_hold(app, binding_id)
        return
    app._joystick_hold_missed_polls = 0
    hold_axis = hold_vector_for_binding(app, binding_id)
    if hold_axis is None:
        stop_hold(app)
        return
    axis, direction = hold_axis
    feed = jog_feed_for_axis(app, axis)
    distance = (feed / 60.0) * (JOYSTICK_HOLD_REPEAT_MS / 1000.0)
    if distance <= 0:
        stop_hold(app)
        return
    distance = max(distance, 0.01)
    dx = dy = dz = 0.0
    if axis == "X":
        dx = direction * distance
    elif axis == "Y":
        dy = direction * distance
    elif axis == "Z":
        dz = direction * distance
    try:
        app.grbl.jog(dx, dy, dz, feed, app.unit_mode.get())
    except Exception:
        pass
    app._joystick_hold_after_id = app.after(JOYSTICK_HOLD_REPEAT_MS, app._send_hold_jog)


def stop_hold(app, binding_id: str | None = None):
    if binding_id and app._active_joystick_hold_binding and binding_id != app._active_joystick_hold_binding:
        return
    if app._joystick_hold_after_id is not None:
        try:
            app.after_cancel(app._joystick_hold_after_id)
        except Exception:
            pass
        app._joystick_hold_after_id = None
    if app._active_joystick_hold_binding:
        try:
            app.grbl.jog_cancel()
        except Exception:
            pass
        try:
            app.grbl.cancel_pending_jogs()
        except Exception:
            pass
    app._active_joystick_hold_binding = None
    app._joystick_hold_missed_polls = 0


def check_release(app):
    active = getattr(app, "_active_joystick_hold_binding", None)
    if not active:
        return
    if not app.joystick_bindings_enabled.get():
        stop_hold(app, active)
        return
    binding = app._joystick_bindings.get(active)
    if _joystick_binding_pressed(app, binding, release=True):
        app._joystick_hold_missed_polls = 0
        return
    missed = getattr(app, "_joystick_hold_missed_polls", 0) + 1
    app._joystick_hold_missed_polls = missed
    if missed >= JOYSTICK_HOLD_MISS_LIMIT:
        stop_hold(app, active)


def binding_pressed(app, binding: dict[str, Any] | None, *, release: bool = False) -> bool:
    return _joystick_binding_pressed(app, binding, release=release)
