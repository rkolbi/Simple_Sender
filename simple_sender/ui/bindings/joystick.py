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
"""Joystick polling and event helpers shared by input_bindings."""

from __future__ import annotations

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import time
import types
from typing import Any, Callable
from tkinter import messagebox

from . import joystick_hold
from simple_sender.utils.constants import (
    JOYSTICK_CAPTURE_TIMEOUT_MS,
    JOYSTICK_DISCOVERY_INTERVAL_MS,
    JOYSTICK_LISTENING_TEXT,
    JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS,
    JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS,
    JOYSTICK_POLL_INTERVAL_MS,
)
from simple_sender.utils.task_timing import record_task_timing

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _normal_safety_binding(app) -> dict[str, Any] | None:
    binding = getattr(app, "_joystick_safety_normal_binding", None)
    if not isinstance(binding, dict):
        binding = getattr(app, "_joystick_safety_binding", None)
    return dict(binding) if isinstance(binding, dict) else None


def _slow_safety_binding(app) -> dict[str, Any] | None:
    binding = getattr(app, "_joystick_safety_slow_binding", None)
    return dict(binding) if isinstance(binding, dict) else None


def _set_normal_safety_binding(app, binding: dict[str, Any] | None) -> None:
    value = dict(binding) if isinstance(binding, dict) else None
    app._joystick_safety_normal_binding = value
    app._joystick_safety_binding = value


def _set_slow_safety_binding(app, binding: dict[str, Any] | None) -> None:
    app._joystick_safety_slow_binding = dict(binding) if isinstance(binding, dict) else None


def _set_joystick_safety_state(app, mode: str | None) -> None:
    normalized = str(mode or "").strip().lower() or None
    if normalized not in {"normal", "slow"}:
        normalized = None
    app._joystick_safety_speed_mode = normalized
    app._joystick_safety_active = normalized is not None


def _resolve_joystick_safety_mode(app) -> str | None:
    slow_binding = _slow_safety_binding(app)
    if slow_binding and joystick_hold.binding_pressed(app, slow_binding, release=True):
        return "slow"
    normal_binding = _normal_safety_binding(app)
    if normal_binding and joystick_hold.binding_pressed(app, normal_binding, release=True):
        return "normal"
    return None


def joystick_jog_speed_scale(app) -> float:
    safety_var = getattr(app, "joystick_safety_enabled", None)
    if safety_var is None:
        return 1.0
    try:
        safety_enabled = bool(safety_var.get())
    except Exception:
        safety_enabled = False
    if not safety_enabled:
        return 1.0
    mode = getattr(app, "_joystick_safety_speed_mode", None)
    if mode not in {"normal", "slow"}:
        mode = _resolve_joystick_safety_mode(app)
        _set_joystick_safety_state(app, mode)
    if mode == "slow":
        return 0.5
    if mode == "normal":
        return 1.0
    return 0.0


def _active_joystick_safety_mode(app) -> str | None:
    mode = getattr(app, "_joystick_safety_speed_mode", None)
    if mode not in {"normal", "slow"}:
        mode = _resolve_joystick_safety_mode(app)
        _set_joystick_safety_state(app, mode)
    return mode if mode in {"normal", "slow"} else None


def _stream_busy(app) -> bool:
    if bool(getattr(app, "_stream_done_pending_idle", False)):
        return True
    state = str(getattr(app, "_stream_state", "") or "").strip().lower()
    return state in {"running", "paused"}


def _joystick_live_status_visible(app) -> bool:
    if bool(getattr(app, "_joystick_capture_state", None)):
        return True
    return bool(getattr(app, "_app_settings_tab_active", False))


def _app_settings_interaction_recent(app, now: float | None = None) -> bool:
    if not bool(getattr(app, "_app_settings_tab_active", False)):
        return False
    ts = float(getattr(app, "_app_settings_last_interaction_ts", 0.0) or 0.0)
    if ts <= 0.0:
        return True
    if now is None:
        now = time.monotonic()
    window_s = float(
        getattr(app, "_app_settings_interaction_active_window_s", 4.0) or 4.0
    )
    if window_s <= 0.0:
        window_s = 4.0
    return (float(now) - ts) <= window_s


def _app_window_backgrounded(app) -> bool:
    target = app
    toplevel = getattr(app, "winfo_toplevel", None)
    if callable(toplevel):
        try:
            target = toplevel()
        except Exception:
            target = app
    for widget in (target, app):
        visible_fn = getattr(widget, "winfo_viewable", None)
        if callable(visible_fn):
            try:
                if not bool(visible_fn()):
                    return True
            except Exception:
                pass
        state_fn = getattr(widget, "state", None)
        if callable(state_fn):
            try:
                state = str(state_fn() or "").strip().lower()
            except Exception:
                state = ""
            if state in {"iconic", "withdrawn"}:
                return True
    return False


def _noninteractive_tab_idle(app) -> bool:
    label = str(getattr(app, "_active_tab_label", "") or "").strip().lower()
    return label in {"logs", "app settings", "checklists"}


def _manual_control_ready(app) -> bool:
    if not bool(getattr(app, "connected", False)):
        return False
    if _stream_busy(app):
        return False
    state_text = str(getattr(app, "_machine_state_text", "") or "").strip().lower()
    if not state_text:
        return True
    return state_text.startswith(("idle", "jog", "hold"))


def _manual_ready_fast_poll_allowed(app) -> bool:
    if _noninteractive_tab_idle(app):
        return False
    return True


def _joystick_poll_intervals_ms(app) -> tuple[int, int, int]:
    base = max(
        1,
        int(getattr(app, "_joystick_poll_interval_ms", JOYSTICK_POLL_INTERVAL_MS)),
    )
    idle_step = max(
        1,
        int(getattr(app, "_joystick_poll_idle_backoff_step_ms", JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS)),
    )
    idle_max = max(
        base,
        int(getattr(app, "_joystick_poll_idle_max_interval_ms", JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS)),
    )
    return base, idle_step, idle_max


def _save_bindings(app) -> None:
    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed("Failed saving joystick bindings", exc)


def _single_connected_joy_id(app) -> Any | None:
    instances = getattr(app, "_joystick_instances", None)
    if not isinstance(instances, dict) or len(instances) != 1:
        return None
    return next(iter(instances.keys()))


def _canonicalize_joy_id(app, joy_id: Any | None) -> Any | None:
    if joy_id is None:
        return None
    instances = getattr(app, "_joystick_instances", None)
    if isinstance(instances, dict) and joy_id in instances:
        return joy_id
    single = _single_connected_joy_id(app)
    if single is not None:
        return single
    return joy_id


def _canonicalize_binding_key(app, key: tuple[Any, ...] | None) -> tuple[Any, ...] | None:
    if not key or len(key) < 2:
        return key
    joy_id = _canonicalize_joy_id(app, key[1])
    if joy_id == key[1]:
        return key
    return (key[0], joy_id, *key[2:])


def _event_joy_id(event) -> Any | None:
    joy_id = getattr(event, "joy", None)
    if joy_id is not None:
        return joy_id
    return getattr(event, "instance_id", None)


def _lookup_bound_button(app, key: tuple[Any, ...] | None):
    canonical_key = _canonicalize_binding_key(app, key)
    if not canonical_key:
        return canonical_key, None
    return canonical_key, app._joystick_binding_map.get(canonical_key)


def poll_joystick_events(
    app,
    *,
    maybe_refresh_joystick_devices: Callable[..., bool],
    update_joystick_live_status: Callable[..., Any],
    joystick_safety_ready: Callable[[Any], bool],
) -> None:
    started = time.perf_counter()
    poll_ok = True
    record_poll_timing = bool(getattr(app, "_app_settings_tab_active", False))
    app._joystick_poll_id = None
    device_change = False
    events = []
    py = app._get_pygame_module()
    if py is None or not app._ensure_joystick_backend():
        if getattr(app, "_active_joystick_hold_binding", None):
            app._stop_joystick_hold()
        app._joystick_poll_idle_streak = 0
        return
    if _stream_busy(app) and not app._joystick_capture_state:
        if getattr(app, "_active_joystick_hold_binding", None):
            app._stop_joystick_hold()
        app._joystick_poll_idle_streak = 0
        return
    try:
        py.event.pump()
        raw_events = list(py.event.get())
        device_added_evt = False
        device_removed_evt = False
        device_added = getattr(py, "JOYDEVICEADDED", None)
        device_removed = getattr(py, "JOYDEVICEREMOVED", None)
        for event in raw_events:
            if device_added is not None and event.type == device_added:
                device_change = True
                device_added_evt = True
                continue
            if device_removed is not None and event.type == device_removed:
                device_change = True
                device_removed_evt = True
                continue
            events.append(event)
        reason = None
        if device_added_evt and device_removed_evt:
            reason = "device change"
        elif device_added_evt:
            reason = "device added"
        elif device_removed_evt:
            reason = "device removed"
        maybe_refresh_joystick_devices(app, py, force=device_change, reason=reason)
        if (not getattr(app, "_joystick_instances", None)) and getattr(
            app, "_active_joystick_hold_binding", None
        ):
            app._stop_joystick_hold()
        if _joystick_live_status_visible(app):
            update_joystick_live_status(app, py)
        for event in events:
            app._handle_joystick_event(event)
        joystick_hold.check_release(app)
        if app.joystick_safety_enabled.get() and not joystick_safety_ready(app):
            if getattr(app, "_active_joystick_hold_binding", None):
                app._stop_joystick_hold()
        if app.joystick_safety_enabled.get():
            mode = _resolve_joystick_safety_mode(app)
            _set_joystick_safety_state(app, mode)
            if mode is None and getattr(app, "_active_joystick_hold_binding", None):
                app._stop_joystick_hold()
        else:
            _set_joystick_safety_state(app, None)
        if app._joystick_capture_state and not events:
            app._poll_joystick_states_from_hardware(py)
    except Exception as exc:
        poll_ok = False
        logger.exception("Joystick polling failed: %s", exc)
        if getattr(app, "_active_joystick_hold_binding", None):
            app._stop_joystick_hold()
        app._joystick_poll_idle_streak = 0
    finally:
        can_poll = bool(app.joystick_bindings_enabled.get() or app._joystick_capture_state)
        should_schedule_poll = True
        if can_poll and (_stream_busy(app) and not app._joystick_capture_state):
            app._joystick_poll_idle_streak = 0
            should_schedule_poll = False
        if can_poll and should_schedule_poll:
            base_interval, idle_step, idle_max = _joystick_poll_intervals_ms(app)
            interval = base_interval
            manual_ready = (
                bool(app.joystick_bindings_enabled.get())
                and _manual_control_ready(app)
                and not bool(getattr(app, "_joystick_capture_state", None))
            )
            manual_ready_fast_poll = (
                manual_ready
                and _manual_ready_fast_poll_allowed(app)
                and not bool(getattr(app, "_active_joystick_hold_binding", None))
            )
            if getattr(app, "_active_joystick_hold_binding", None):
                interval = joystick_hold.JOYSTICK_HOLD_POLL_INTERVAL_MS
                app._joystick_poll_idle_streak = 0
            elif app._joystick_capture_state:
                app._joystick_poll_idle_streak = 0
            elif events or device_change:
                app._joystick_poll_idle_streak = 0
            else:
                idle_streak = int(getattr(app, "_joystick_poll_idle_streak", 0) or 0) + 1
                app._joystick_poll_idle_streak = idle_streak
                interval = min(idle_max, base_interval + (idle_streak * idle_step))
                # When no joystick devices are connected, stop high-rate polling
                # after a short backoff and fall back to discovery cadence.
                if (
                    idle_streak >= 3
                    and (not getattr(app, "_joystick_instances", None))
                    and not app._joystick_capture_state
                ):
                    interval = max(interval, int(JOYSTICK_DISCOVERY_INTERVAL_MS))
            if manual_ready_fast_poll:
                # Jogging is a manual-control path, not a streaming path. Keep
                # the poll loop at the base cadence whenever the machine is
                # ready for manual motion so first-press response stays
                # predictable instead of drifting up to a slower idle cap.
                interval = base_interval
            if (
                bool(getattr(app, "_app_settings_tab_active", False))
                and not bool(getattr(app, "_joystick_capture_state", None))
                and not bool(getattr(app, "_active_joystick_hold_binding", None))
                and not manual_ready_fast_poll
                and not _app_settings_interaction_recent(app, now=time.monotonic())
            ):
                app_settings_idle_interval = max(
                    200,
                    int(
                        getattr(
                            app,
                            "_joystick_poll_app_settings_idle_interval_ms",
                            450,
                        )
                        or 450
                    ),
                )
                interval = max(interval, app_settings_idle_interval)
            elif (
                _noninteractive_tab_idle(app)
                and not bool(getattr(app, "_joystick_capture_state", None))
                and not bool(getattr(app, "_active_joystick_hold_binding", None))
            ):
                noninteractive_interval = max(
                    300,
                    int(
                        getattr(
                            app,
                            "_joystick_poll_noninteractive_tab_interval_ms",
                            900,
                        )
                        or 900
                    ),
                )
                interval = max(interval, noninteractive_interval)
            if (
                _app_window_backgrounded(app)
                and not bool(getattr(app, "_joystick_capture_state", None))
                and not bool(getattr(app, "_active_joystick_hold_binding", None))
            ):
                background_interval = max(
                    500,
                    int(
                        getattr(
                            app,
                            "_joystick_poll_background_interval_ms",
                            1600,
                        )
                        or 1600
                    ),
                )
                interval = max(interval, background_interval)
            app._joystick_poll_id = app.after(interval, app._poll_joystick_events)
        if record_poll_timing:
            elapsed_ms = 0.0
            if started > 0.0:
                elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            record_task_timing(
                app,
                "joystick.poll",
                elapsed_ms,
                success=poll_ok,
            )


def describe_joystick_event(app, event) -> str | None:
    py = app._get_pygame_module()
    if py is None:
        return None
    kind = py.event.event_name(event.type)
    parts = [f"{kind}: joy={getattr(event, 'joy', None)}"]
    if hasattr(event, "button"):
        parts.append(f"button={event.button}")
    if hasattr(event, "axis"):
        parts.append(f"axis={event.axis} value={event.value:.3f}")
    if hasattr(event, "hat"):
        parts.append(f"hat={event.hat} value={event.value}")
    return " ".join(parts)


def set_joystick_event_status(app, text: str) -> None:
    if hasattr(app, "joystick_event_status"):
        app.joystick_event_status.set(text)


def joystick_safety_ready(app) -> bool:
    if not app.joystick_safety_enabled.get():
        return True
    return bool(_normal_safety_binding(app) or _slow_safety_binding(app))


def handle_joystick_event(
    app,
    event,
    *,
    joystick_safety_ready: Callable[[Any], bool],
    set_joystick_event_status: Callable[..., Any],
    describe_joystick_event: Callable[..., str | None],
) -> None:
    py = app._get_pygame_module()
    if py is None:
        return
    desc = describe_joystick_event(app, event)
    if desc:
        set_joystick_event_status(app, desc)
    normal_safety_binding = _normal_safety_binding(app)
    slow_safety_binding = _slow_safety_binding(app)
    normal_safety_key = (
        app._joystick_binding_key(normal_safety_binding) if normal_safety_binding else None
    )
    slow_safety_key = app._joystick_binding_key(slow_safety_binding) if slow_safety_binding else None
    key: tuple[Any, ...] | None = None
    button_down_event = False
    if event.type == py.JOYBUTTONUP:
        joy = _canonicalize_joy_id(app, _event_joy_id(event))
        button = getattr(event, "button", None)
        if joy is not None and button is not None:
            key = ("button", joy, button)
            if (normal_safety_key and key == normal_safety_key) or (
                slow_safety_key and key == slow_safety_key
            ):
                _set_joystick_safety_state(app, _resolve_joystick_safety_mode(app))
                if getattr(app, "_active_joystick_hold_binding", None):
                    if getattr(app, "_joystick_safety_speed_mode", None) is None:
                        app._stop_joystick_hold()
                return
            app._handle_joystick_button_release(key)
        return
    if event.type == py.JOYBUTTONDOWN:
        joy = _canonicalize_joy_id(app, _event_joy_id(event))
        button = getattr(event, "button", None)
        if joy is None or button is None:
            return
        key = ("button", joy, button)
        button_down_event = True
    elif event.type == py.JOYAXISMOTION:
        axis_value = getattr(event, "value", 0.0)
        joy = _canonicalize_joy_id(app, _event_joy_id(event))
        axis = getattr(event, "axis", None)
        if joy is None or axis is None:
            return
        if axis_value >= joystick_hold.JOYSTICK_AXIS_THRESHOLD:
            direction = 1
        elif axis_value <= -joystick_hold.JOYSTICK_AXIS_THRESHOLD:
            direction = -1
        else:
            app._reset_joystick_axis_state(joy, axis)
            return
        key = ("axis", joy, axis, direction)
        axis_state_key = (joy, axis, direction)
        if axis_state_key in app._joystick_axis_active:
            return
        app._joystick_axis_active.add(axis_state_key)
        button_down_event = True
    elif event.type == py.JOYHATMOTION:
        joy = _canonicalize_joy_id(app, _event_joy_id(event))
        hat_index = getattr(event, "hat", None)
        raw_value = getattr(event, "value", (0, 0))
        if joy is None or hat_index is None:
            return
        hat_tuple = tuple(raw_value) if isinstance(raw_value, (list, tuple)) else (raw_value,)
        if len(hat_tuple) < 2:
            if hat_tuple:
                hat_tuple = (hat_tuple[0], 0)
            else:
                hat_tuple = (0, 0)
        hat_value = (int(hat_tuple[0]), int(hat_tuple[1]))
        if hat_value == (0, 0):
            app._reset_joystick_hat_state(joy, hat_index)
            return
        hat_state_key = (joy, hat_index, hat_value)
        key = ("hat", joy, hat_index, hat_value)
        if hat_state_key in app._joystick_hat_active:
            return
        app._joystick_hat_active.add(hat_state_key)
        button_down_event = True
    if key is None:
        return
    key = _canonicalize_binding_key(app, key)
    if key is None:
        return
    capture_state = app._joystick_capture_state
    if capture_state:
        timer_id = capture_state.get("timer")
        if timer_id is not None:
            try:
                app.after_cancel(timer_id)
            except Exception as exc:
                _log_suppressed("Failed canceling joystick capture timeout timer in key-handler path", exc)
        if capture_state.get("mode") == "safety":
            binding = app._joystick_binding_from_event(key)
            if binding:
                if str(capture_state.get("safety_mode", "normal")).strip().lower() == "slow":
                    _set_slow_safety_binding(app, binding)
                else:
                    _set_normal_safety_binding(app, binding)
                _set_joystick_safety_state(app, None)
                app._refresh_joystick_safety_display()
                _save_bindings(app)
        else:
            binding = app._joystick_binding_from_event(key)
            if binding:
                app._joystick_bindings[capture_state["binding_id"]] = binding
                app._clear_duplicate_joystick_binding(key, capture_state["binding_id"])
                _save_bindings(app)
        if key[0] == "axis":
            app._reset_joystick_axis_state(key[1], key[2])
        if key[0] == "hat":
            app._reset_joystick_hat_state(key[1], key[2])
        app._apply_keyboard_bindings()
        app._joystick_capture_state = None
        return
    if not app.joystick_bindings_enabled.get():
        return
    if not button_down_event:
        return
    if slow_safety_key and key == slow_safety_key:
        _set_joystick_safety_state(app, "slow")
        return
    if normal_safety_key and key == normal_safety_key:
        if getattr(app, "_joystick_safety_speed_mode", None) != "slow":
            _set_joystick_safety_state(app, "normal")
        return
    key, btn = _lookup_bound_button(app, key)
    if btn:
        binding_id = app._button_binding_id(btn)
        if app.joystick_safety_enabled.get():
            if not joystick_safety_ready(app):
                if hasattr(app, "joystick_event_status"):
                    app.joystick_event_status.set(
                        "Safety enabled but no normal/slow safety button is set."
                    )
                if getattr(app, "_active_joystick_hold_binding", None):
                    app._stop_joystick_hold()
                return
            if _active_joystick_safety_mode(app) is None:
                return
        if app._is_virtual_hold_button(btn):
            app._log_button_action(btn)
            app._start_joystick_hold(binding_id)
            return
        try:
            if btn.cget("state") == "disabled":
                return
        except Exception:
            return
        app._log_button_action(btn)
        prior_source = getattr(app, "_manual_input_source", None)
        try:
            app._manual_input_source = "joystick"
            app._invoke_button(btn)
        finally:
            if prior_source is None:
                try:
                    delattr(app, "_manual_input_source")
                except Exception as exc:
                    _log_suppressed("Failed clearing manual-input-source marker after joystick action", exc)
            else:
                app._manual_input_source = prior_source


def is_virtual_hold_button(app, btn) -> bool:
    _ = app
    return joystick_hold.is_virtual_hold_button(btn)


def handle_joystick_button_release(app, key: tuple) -> None:
    _resolved_key, btn = _lookup_bound_button(app, key)
    if not btn:
        return
    binding_id = app._button_binding_id(btn)
    if app._is_virtual_hold_button(btn):
        app._stop_joystick_hold(binding_id)
        return
    if not str(binding_id).startswith("jog_"):
        return
    grbl = getattr(app, "grbl", None)
    if grbl is None:
        return
    try:
        grbl.jog_cancel()
    except Exception as exc:
        _log_suppressed("Failed sending jog cancel on joystick button release", exc)
    try:
        grbl.cancel_pending_jogs()
    except Exception as exc:
        _log_suppressed("Failed canceling pending jogs on joystick button release", exc)


def start_joystick_hold(app, binding_id: str) -> None:
    joystick_hold.start_hold(app, binding_id)


def send_hold_jog(app) -> None:
    joystick_hold.send_hold_jog(app)


def stop_joystick_hold(app, binding_id: str | None = None) -> None:
    joystick_hold.stop_hold(app, binding_id)


def clear_duplicate_joystick_binding(app, key: tuple, keep_binding_id: str) -> None:
    if not key:
        return
    for binding_id, binding in list(app._joystick_bindings.items()):
        if binding_id == keep_binding_id:
            continue
        tuple_key = app._joystick_binding_key(binding)
        if tuple_key == key:
            app._joystick_bindings.pop(binding_id, None)


def poll_joystick_states_from_hardware(app, py) -> bool:
    if not app._joystick_capture_state:
        return False
    for joy_id, joy in app._joystick_instances.items():
        for btn_idx in range(getattr(joy, "get_numbuttons", lambda: 0)()):
            pressed = bool(joy.get_button(btn_idx))
            prev = app._joystick_button_poll_state.get((joy_id, btn_idx), False)
            app._joystick_button_poll_state[(joy_id, btn_idx)] = pressed
            if pressed and not prev:
                event = types.SimpleNamespace(type=py.JOYBUTTONDOWN, joy=joy_id, button=btn_idx)
                app._handle_joystick_event(event)
                return True
            if not pressed and prev:
                app._handle_joystick_button_release(("button", joy_id, btn_idx))
        for axis_idx in range(getattr(joy, "get_numaxes", lambda: 0)()):
            value = float(joy.get_axis(axis_idx))
            prev = app._joystick_axis_poll_state.get((joy_id, axis_idx), 0.0)
            app._joystick_axis_poll_state[(joy_id, axis_idx)] = value
            if value >= joystick_hold.JOYSTICK_AXIS_THRESHOLD and prev < joystick_hold.JOYSTICK_AXIS_THRESHOLD:
                event = types.SimpleNamespace(type=py.JOYAXISMOTION, joy=joy_id, axis=axis_idx, value=value)
                app._handle_joystick_event(event)
                return True
        for hat_idx in range(getattr(joy, "get_numhats", lambda: 0)()):
            hat_value = joy.get_hat(hat_idx)
            prev = app._joystick_hat_poll_state.get((joy_id, hat_idx), (0, 0))
            if not isinstance(hat_value, tuple):
                hat_value = (
                    tuple(hat_value)
                    if isinstance(hat_value, (list, tuple))
                    else (hat_value, 0)
                )
            app._joystick_hat_poll_state[(joy_id, hat_idx)] = hat_value
            if hat_value != (0, 0) and hat_value != prev:
                event = types.SimpleNamespace(
                    type=py.JOYHATMOTION, joy=joy_id, hat=hat_idx, value=hat_value
                )
                app._handle_joystick_event(event)
                return True
    return False


def reset_joystick_axis_state(app, joy_id, axis) -> None:
    to_remove = [entry for entry in app._joystick_axis_active if entry[0] == joy_id and entry[1] == axis]
    for entry in to_remove:
        app._joystick_axis_active.discard(entry)


def reset_joystick_hat_state(app, joy_id, hat_index) -> None:
    to_remove = [
        entry
        for entry in app._joystick_hat_active
        if (entry[0] == joy_id and entry[1] == hat_index)
    ]
    for entry in to_remove:
        app._joystick_hat_active.discard(entry)


def start_joystick_capture(app, row) -> None:
    if not bool(app.joystick_bindings_enabled.get()):
        messagebox.showinfo(
            "Joystick bindings",
            "Enable USB joystick bindings before configuring joystick shortcuts.",
        )
        return
    if not app._ensure_joystick_backend():
        messagebox.showwarning(
            "Joystick bindings",
            "Failed to initialize the joystick backend. Verify that pygame is installed and joysticks are available.",
        )
        app.joystick_bindings_enabled.set(False)
        app._refresh_joystick_toggle_text()
        return
    btn = app._kb_item_to_button.get(row)
    if btn is None:
        return
    app._cancel_joystick_capture()
    state = {
        "mode": "binding",
        "row": row,
        "binding_id": app._button_binding_id(btn),
        "original": app.kb_table.set(row, "joystick"),
        "timer": None,
    }
    timer_id = app.after(JOYSTICK_CAPTURE_TIMEOUT_MS, app._cancel_joystick_capture)
    state["timer"] = timer_id
    app._joystick_capture_state = state
    try:
        app.kb_table.set(row, "joystick", JOYSTICK_LISTENING_TEXT)
    except Exception as exc:
        _log_suppressed("Failed showing joystick capture listening text", exc)
    app._ensure_joystick_polling_running()


def cancel_joystick_capture(app) -> None:
    state = app._joystick_capture_state
    if not state:
        return
    if state.get("mode") == "safety":
        app._cancel_joystick_safety_capture()
        return
    timer_id = state.get("timer")
    if timer_id is not None:
        try:
            app.after_cancel(timer_id)
        except Exception as exc:
            _log_suppressed("Failed canceling joystick capture timer", exc)
    row = state.get("row")
    original = state.get("original", "None")
    if row and hasattr(app, "kb_table") and app.kb_table.exists(row):
        try:
            app.kb_table.set(row, "joystick", original)
        except Exception as exc:
            _log_suppressed("Failed restoring joystick capture table cell text", exc)
    app._joystick_capture_state = None


def _safety_status_var(app, mode: str):
    attr_name = (
        "joystick_safety_slow_status"
        if str(mode).strip().lower() == "slow"
        else "joystick_safety_normal_status"
    )
    var = getattr(app, attr_name, None)
    if var is None and hasattr(app, "joystick_safety_status"):
        return app.joystick_safety_status
    return var


def start_joystick_safety_capture(app, mode: str = "normal") -> None:
    safety_mode = "slow" if str(mode).strip().lower() == "slow" else "normal"
    if not bool(app.joystick_bindings_enabled.get()):
        messagebox.showinfo(
            "Joystick safety",
            (
                "Enable USB joystick bindings before configuring the "
                f"{safety_mode} jog-speed safety button."
            ),
        )
        return
    if not app._ensure_joystick_backend():
        messagebox.showwarning(
            "Joystick safety",
            "Failed to initialize the joystick backend. Verify that pygame is installed and joysticks are available.",
        )
        app.joystick_bindings_enabled.set(False)
        app._refresh_joystick_toggle_text()
        return
    app._cancel_joystick_safety_capture()
    state = {
        "mode": "safety",
        "safety_mode": safety_mode,
        "timer": None,
    }
    timer_id = app.after(JOYSTICK_CAPTURE_TIMEOUT_MS, app._cancel_joystick_safety_capture)
    state["timer"] = timer_id
    app._joystick_capture_state = state
    status_var = _safety_status_var(app, safety_mode)
    if status_var is not None:
        status_var.set(JOYSTICK_LISTENING_TEXT)
    app._ensure_joystick_polling_running()


def cancel_joystick_safety_capture(app) -> None:
    state = app._joystick_capture_state
    if not state or state.get("mode") != "safety":
        return
    timer_id = state.get("timer")
    if timer_id is not None:
        try:
            app.after_cancel(timer_id)
        except Exception as exc:
            _log_suppressed("Failed canceling joystick safety capture timer", exc)
    app._joystick_capture_state = None
    app._refresh_joystick_safety_display()


def clear_joystick_safety_binding(app, mode: str = "normal") -> None:
    if str(mode).strip().lower() == "slow":
        _set_slow_safety_binding(app, None)
    else:
        _set_normal_safety_binding(app, None)
    _set_joystick_safety_state(app, _resolve_joystick_safety_mode(app))
    app._refresh_joystick_safety_display()
    _save_bindings(app)


def on_joystick_safety_toggle(app) -> None:
    if not app.joystick_safety_enabled.get():
        _set_joystick_safety_state(app, None)
        return
    if not joystick_safety_ready(app):
        if hasattr(app, "joystick_event_status"):
            app.joystick_event_status.set("Safety enabled but no normal/slow safety button is set.")


def joystick_binding_from_event(app, key):
    _ = app
    if not key:
        return None
    kind = key[0]
    joy_id = key[1]
    if kind == "button":
        return {"kind": "button", "joy_id": joy_id, "index": key[2]}
    if kind == "axis":
        return {
            "kind": "axis",
            "joy_id": joy_id,
            "index": key[2],
            "direction": key[3],
        }
    if kind == "hat":
        value = key[3]
        if isinstance(value, (list, tuple)):
            value = tuple(value)
        return {"kind": "hat", "joy_id": joy_id, "index": key[2], "value": value}
    return None


def joystick_binding_display(app, binding: dict[str, Any]) -> str:
    joy_id = binding.get("joy_id")
    if isinstance(joy_id, int):
        name = app._joystick_names.get(joy_id, f"Joystick {joy_id}")
    else:
        name = "Joystick"
    kind = binding.get("kind")
    if kind == "button":
        idx = binding.get("index")
        return f"{name} Button {idx}"
    if kind == "axis":
        direction = binding.get("direction")
        suffix = "+" if direction == 1 else "-" if direction == -1 else ""
        return f"{name} Axis {binding.get('index')}{suffix}"
    if kind == "hat":
        value = binding.get("value")
        if isinstance(value, (list, tuple)):
            value = tuple(value)
        if value:
            return f"{name} Hat {binding.get('index')} ({value[0]}, {value[1]})"
        return f"{name} Hat {binding.get('index')}"
    return ""


def joystick_binding_key(app, binding: dict[str, Any]):
    _ = app
    if not isinstance(binding, dict):
        return None
    kind = binding.get("kind")
    joy_id = binding.get("joy_id")
    if kind == "button":
        return ("button", joy_id, binding.get("index"))
    if kind == "axis":
        return ("axis", joy_id, binding.get("index"), binding.get("direction"))
    if kind == "hat":
        value = binding.get("value")
        if isinstance(value, (list, tuple)):
            value = tuple(value)
        return ("hat", joy_id, binding.get("index"), value)
    return None


def button_axis_name(app, btn) -> str:
    xy_buttons = {b for _, b in app._xy_step_buttons}
    z_buttons = {b for _, b in app._z_step_buttons}
    hold_axis = getattr(btn, "_hold_axis", None)
    if hold_axis in ("X", "Y"):
        return "XY"
    if hold_axis == "Z":
        return "Z"
    if btn in xy_buttons:
        return "XY"
    if btn in z_buttons:
        return "Z"
    return ""

