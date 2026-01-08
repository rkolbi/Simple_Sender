import logging
import time
import types
import tkinter as tk
from tkinter import ttk, messagebox
from types import ModuleType
from typing import Any

from simple_sender.ui import joystick_hold
from simple_sender.ui.widgets import StopSignButton, VirtualHoldButton
from simple_sender.utils.constants import CLEAR_ICON

logger = logging.getLogger(__name__)

PYGAME_IMPORT_ERROR = ""
pygame: ModuleType | None = None
PYGAME_AVAILABLE = False
try:
    import pygame as _pygame_module
except ImportError as exc:
    pygame = None
    PYGAME_IMPORT_ERROR = str(exc)
else:
    pygame = _pygame_module
    PYGAME_AVAILABLE = True

JOYSTICK_POLL_INTERVAL_MS = 50
JOYSTICK_CAPTURE_TIMEOUT_MS = 15000
JOYSTICK_LISTENING_TEXT = "Listening for joystick input..."


def toggle_keyboard_bindings(app):
    current = bool(app.keyboard_bindings_enabled.get())
    new_val = not current
    app.keyboard_bindings_enabled.set(new_val)
    app._refresh_keybindings_toggle_text()
    app._apply_keyboard_bindings()

def toggle_joystick_bindings(app):
    if not PYGAME_AVAILABLE:
        messagebox.showwarning(
            "Joystick bindings",
            "USB joystick support requires pygame. Install pygame and restart the application.",
        )
        return
    new_state = not bool(app.joystick_bindings_enabled.get())
    app.joystick_bindings_enabled.set(new_state)
    app._refresh_joystick_toggle_text()
    app._update_joystick_polling_state()

def on_keyboard_bindings_check(app):
    new_val = bool(app.keyboard_bindings_enabled.get())
    app._refresh_keybindings_toggle_text()
    app._apply_keyboard_bindings()

def refresh_joystick_toggle_text(app):
    if not hasattr(app, "btn_toggle_joystick_bindings"):
        return
    if not PYGAME_AVAILABLE:
        app.btn_toggle_joystick_bindings.config(text="Joystick support requires pygame", state="disabled")
        return
    text = (
        "Disable USB Joystick Bindings"
        if app.joystick_bindings_enabled.get()
        else "Enable USB Joystick Bindings"
    )
    app.btn_toggle_joystick_bindings.config(text=text, state="normal")
    app._refresh_joystick_test_info()

def update_joystick_polling_state(app):
    app._refresh_joystick_toggle_text()
    if not app.joystick_bindings_enabled.get():
        app._cancel_joystick_capture()
        app._stop_joystick_polling()
        app._stop_joystick_hold()
        app._joystick_safety_active = False
        return
    if not app._ensure_joystick_backend():
        messagebox.showwarning(
            "Joystick bindings",
            "Failed to initialize the joystick backend. Check that pygame is installed and a joystick is connected.",
        )
        app.joystick_bindings_enabled.set(False)
        app._refresh_joystick_toggle_text()
        app._stop_joystick_polling()
        return
    app._start_joystick_polling()

def restore_joystick_bindings_on_start(app):
    if not getattr(app, "_joystick_auto_enable_requested", False):
        return
    if not hasattr(app, "btn_toggle_joystick_bindings"):
        app.after(100, app._restore_joystick_bindings_on_start)
        return
    app._joystick_auto_enable_requested = False
    if not app.joystick_bindings_enabled.get():
        return
    app._refresh_joystick_toggle_text()
    app._update_joystick_polling_state()

def get_pygame_module(app) -> ModuleType | None:
    if not PYGAME_AVAILABLE or pygame is None:
        return None
    return pygame

def discover_joysticks(app, py, count: int) -> list[str]:
    names: list[str] = []
    instances: dict[int, Any] = {}
    if count < 0:
        count = 0
    for idx in range(count):
        try:
            joy = py.joystick.Joystick(idx)
            joy.init()
            name = joy.get_name()
            instances[idx] = joy
        except Exception:
            name = f"Joystick {idx}"
        names.append(name)
    app._joystick_instances = instances
    app._joystick_button_poll_state.clear()
    app._joystick_axis_poll_state.clear()
    app._joystick_hat_poll_state.clear()
    return names

def refresh_joystick_test_info(app):
    if not hasattr(app, "joystick_test_status"):
        return
    py = app._get_pygame_module()
    if py is None:
        app.joystick_test_status.set("pygame is not installed. Install it to detect USB joysticks.")
        return
    try:
        py.init()
        py.joystick.init()
        count = py.joystick.get_count()
    except Exception as exc:
        app.joystick_test_status.set(f"Joystick init failed: {exc}")
        return
    if count <= 0:
        app._joystick_names = {}
        app._joystick_instances = {}
        app.joystick_test_status.set(
            "No joysticks detected. Plug in a controller and click Refresh."
        )
        return
    lines = [f"{count} joystick(s) detected:"]
    names = app._discover_joysticks(py, count)
    for idx, name in enumerate(names):
        lines.append(f"- #{idx}: {name}")
    app._joystick_names = {idx: name for idx, name in enumerate(names)}
    lines.append("Enable USB joystick bindings and press a button/axis/hat to map it.")
    app.joystick_test_status.set("\n".join(lines))

def refresh_joystick_safety_display(app):
    if not hasattr(app, "joystick_safety_status"):
        return
    binding = getattr(app, "_joystick_safety_binding", None)
    label = "None"
    if binding:
        display = app._joystick_binding_display(binding)
        if display:
            label = display
    app.joystick_safety_status.set(f"Safety button: {label}")

def _joystick_safety_ready(app) -> bool:
    if not app.joystick_safety_enabled.get():
        return True
    return bool(getattr(app, "_joystick_safety_binding", None))

def ensure_joystick_backend(app):
    py = app._get_pygame_module()
    if py is None:
        return False
    if app._joystick_backend_ready:
        return True
    try:
        py.init()
        py.joystick.init()
        count = py.joystick.get_count()
        names = app._discover_joysticks(py, count)
        app._joystick_names = {idx: name for idx, name in enumerate(names)}
        app._joystick_backend_ready = True
        return True
    except Exception as exc:
        logger.exception("Joystick backend initialization failed: %s", exc)
        return False

def start_joystick_polling(app):
    if app._joystick_poll_id is not None:
        return
    app._poll_joystick_events()

def stop_joystick_polling(app):
    if app._joystick_poll_id is not None:
        try:
            app.after_cancel(app._joystick_poll_id)
        except Exception:
            pass
        app._joystick_poll_id = None

def ensure_joystick_polling_running(app):
    if app._joystick_poll_id is None:
        app._start_joystick_polling()

def poll_joystick_events(app):
    app._joystick_poll_id = None
    py = app._get_pygame_module()
    if py is None or not app._ensure_joystick_backend():
        return
    try:
        py.event.pump()
        events = list(py.event.get())
        for event in events:
            app._handle_joystick_event(event)
        joystick_hold.check_release(app)
        if app.joystick_safety_enabled.get() and not _joystick_safety_ready(app):
            if getattr(app, "_active_joystick_hold_binding", None):
                app._stop_joystick_hold()
        if app.joystick_safety_enabled.get():
            binding = getattr(app, "_joystick_safety_binding", None)
            if binding:
                active = joystick_hold.binding_pressed(app, binding, release=True)
                if active != app._joystick_safety_active:
                    app._joystick_safety_active = active
                if not active and getattr(app, "_active_joystick_hold_binding", None):
                    app._stop_joystick_hold()
        if app._joystick_capture_state and not events:
            if app._poll_joystick_states_from_hardware(py):
                # ensure we still schedule next poll immediately after capturing
                pass
    except Exception as exc:
        logger.exception("Joystick polling failed: %s", exc)
    finally:
        if app.joystick_bindings_enabled.get() or app._joystick_capture_state:
            interval = JOYSTICK_POLL_INTERVAL_MS
            if getattr(app, "_active_joystick_hold_binding", None):
                interval = joystick_hold.JOYSTICK_HOLD_POLL_INTERVAL_MS
            app._joystick_poll_id = app.after(interval, app._poll_joystick_events)

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

def set_joystick_event_status(app, text: str):
    if hasattr(app, "joystick_event_status"):
        app.joystick_event_status.set(text)

def handle_joystick_event(app, event):
    py = app._get_pygame_module()
    if py is None:
        return
    desc = app._describe_joystick_event(event)
    if desc:
        app._set_joystick_event_status(desc)
    safety_binding = getattr(app, "_joystick_safety_binding", None)
    safety_key = app._joystick_binding_key(safety_binding) if safety_binding else None
    key = None
    button_down_event = False
    if event.type == py.JOYBUTTONUP:
        joy = getattr(event, "joy", None)
        button = getattr(event, "button", None)
        if joy is not None and button is not None:
            key = ("button", joy, button)
            if safety_key and key == safety_key:
                app._joystick_safety_active = False
                if getattr(app, "_active_joystick_hold_binding", None):
                    app._stop_joystick_hold()
                return
            app._handle_joystick_button_release(("button", joy, button))
        return
    if event.type == py.JOYBUTTONDOWN:
        key = ("button", event.joy, event.button)
        button_down_event = True
    elif event.type == py.JOYAXISMOTION:
        axis_value = getattr(event, "value", 0.0)
        joy = getattr(event, "joy", None)
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
        joy = getattr(event, "joy", None)
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
    capture_state = app._joystick_capture_state
    if capture_state:
        timer_id = capture_state.get("timer")
        if timer_id is not None:
            try:
                app.after_cancel(timer_id)
            except Exception:
                pass
        if capture_state.get("mode") == "safety":
            binding = app._joystick_binding_from_event(key)
            if binding:
                app._joystick_safety_binding = binding
                app._joystick_safety_active = False
                app._refresh_joystick_safety_display()
        else:
            binding = app._joystick_binding_from_event(key)
            if binding:
                app._joystick_bindings[capture_state["binding_id"]] = binding
                app._clear_duplicate_joystick_binding(key, capture_state["binding_id"])
            if key[0] == "axis":
                app._reset_joystick_axis_state(key[1], key[2])
            if key[0] == "hat":
                app._reset_joystick_hat_state(key[1], key[2])
            app._apply_keyboard_bindings()
        app._joystick_capture_state = None
        return
    if not app.joystick_bindings_enabled.get():
        return
    if app.joystick_safety_enabled.get() and not _joystick_safety_ready(app):
        if hasattr(app, "joystick_event_status"):
            app.joystick_event_status.set("Safety enabled but no safety button is set.")
        if getattr(app, "_active_joystick_hold_binding", None):
            app._stop_joystick_hold()
        return
    if not button_down_event:
        return
    if safety_key and key == safety_key:
        app._joystick_safety_active = True
        return
    if app.joystick_safety_enabled.get() and safety_key and not app._joystick_safety_active:
        return
    btn = app._joystick_binding_map.get(key)
    if btn:
        if app._is_virtual_hold_button(btn):
            app._log_button_action(btn)
            app._start_joystick_hold(app._button_binding_id(btn))
            return
        app._on_key_binding(btn)

def is_virtual_hold_button(app, btn) -> bool:
    return joystick_hold.is_virtual_hold_button(btn)

def handle_joystick_button_release(app, key: tuple):
    btn = app._joystick_binding_map.get(key)
    if not btn or not app._is_virtual_hold_button(btn):
        return
    binding_id = app._button_binding_id(btn)
    app._stop_joystick_hold(binding_id)

def start_joystick_hold(app, binding_id: str):
    joystick_hold.start_hold(app, binding_id)

def send_hold_jog(app):
    joystick_hold.send_hold_jog(app)

def stop_joystick_hold(app, binding_id: str | None = None):
    joystick_hold.stop_hold(app, binding_id)

def clear_duplicate_joystick_binding(app, key: tuple, keep_binding_id: str):
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
            value = joy.get_hat(hat_idx)
            prev = app._joystick_hat_poll_state.get((joy_id, hat_idx), (0, 0))
            if not isinstance(value, tuple):
                value = tuple(value) if isinstance(value, (list, tuple)) else (value, 0)
            app._joystick_hat_poll_state[(joy_id, hat_idx)] = value
            if value != (0, 0) and value != prev:
                event = types.SimpleNamespace(type=py.JOYHATMOTION, joy=joy_id, hat=hat_idx, value=value)
                app._handle_joystick_event(event)
                return True
    return False

def reset_joystick_axis_state(app, joy_id, axis):
    to_remove = [entry for entry in app._joystick_axis_active if entry[0] == joy_id and entry[1] == axis]
    for entry in to_remove:
        app._joystick_axis_active.discard(entry)

def reset_joystick_hat_state(app, joy_id, hat_index):
    to_remove = [
        entry
        for entry in app._joystick_hat_active
        if (entry[0] == joy_id and entry[1] == hat_index)
    ]
    for entry in to_remove:
        app._joystick_hat_active.discard(entry)

def apply_keyboard_bindings(app):
    for seq in ("<KeyPress>", "<KeyRelease>"):
        if seq in app._bound_key_sequences:
            app.unbind_all(seq)
    if app._bound_key_sequences:
        app._bound_key_sequences.clear()
    app._kb_mod_keys_down.clear()
    app._key_sequence_map = {}
    app._kb_conflicts = set()
    for btn in app._collect_buttons():
        binding_id = app._button_binding_id(btn)
        if binding_id in app._key_bindings:
            label = app._normalize_key_label(str(app._key_bindings.get(binding_id, "")).strip())
            if not label:
                continue
            is_custom = True
        else:
            label = app._default_key_for_button(btn)
            if not label:
                continue
            is_custom = False
        seq = app._key_sequence_tuple(label)
        if not seq:
            continue
        conflict_seq = app._sequence_conflict(seq, app._key_sequence_map)
        if conflict_seq:
            other_btn = app._key_sequence_map.get(conflict_seq)
            other_id = app._button_binding_id(other_btn) if other_btn else ""
            if binding_id in app._key_bindings:
                app._key_bindings[binding_id] = ""
            app._kb_conflicts.add(binding_id)
            if other_id:
                if other_id in app._key_bindings:
                    app._key_bindings[other_id] = ""
                app._kb_conflicts.add(other_id)
                app._key_sequence_map.pop(conflict_seq, None)
            continue
        app._key_sequence_map[seq] = btn
    app._refresh_keyboard_table()
    if not bool(app.keyboard_bindings_enabled.get()):
        app._clear_key_sequence_buffer()
        return
    app._bound_key_sequences.add("<KeyPress>")
    app._bound_key_sequences.add("<KeyRelease>")
    app.bind_all("<KeyPress>", app._on_key_sequence, add="+")
    app.bind_all("<KeyRelease>", app._on_key_modifier_release, add="+")

def refresh_keyboard_table(app):
    if not hasattr(app, "kb_table"):
        return
    app.kb_table.delete(*app.kb_table.get_children())
    app.kb_table.tag_configure("conflict", background="#f7d6d6")
    app._kb_item_to_button = {}
    app._joystick_binding_map.clear()
    for btn in app._collect_buttons():
        binding_id = app._button_binding_id(btn)
        label = app._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        if tip:
            label = f"{label} - {tip}"
        axis = app._button_axis_name(btn)
        key = app._keyboard_key_for_button(btn)
        if not key:
            key = "None"
        joystick_label = "None"
        binding = app._joystick_bindings.get(binding_id)
        if binding:
            display = app._joystick_binding_display(binding)
            if display:
                joystick_label = display
            tuple_key = app._joystick_binding_key(binding)
            if tuple_key:
                app._joystick_binding_map[tuple_key] = btn
        tags = ("conflict",) if binding_id in app._kb_conflicts else ()
        item = app.kb_table.insert(
            "",
            "end",
            values=(label, axis, key, joystick_label, f"{CLEAR_ICON}  Remove/Clear Binding"),
            tags=tags,
        )
        app._kb_item_to_button[item] = btn

def create_virtual_hold_buttons(app) -> list[VirtualHoldButton]:
    buttons: list[VirtualHoldButton] = []
    for label, binding_id, axis, direction in joystick_hold.JOYSTICK_HOLD_DEFINITIONS:
        buttons.append(VirtualHoldButton(f"{label} (Hold)", binding_id, axis, direction))
    return buttons

def collect_buttons(app) -> list:
    buttons = []
    seen = set()

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, (ttk.Button, tk.Button, StopSignButton)):
                if child not in seen:
                    seen.add(child)
                    buttons.append(child)
            walk(child)

    walk(app)
    if app._virtual_hold_buttons:
        buttons.extend(app._virtual_hold_buttons)
    buttons.sort(key=app._button_label)
    return buttons

def button_label(app, btn) -> str:
    label = ""
    try:
        label = btn.cget("text")
    except Exception:
        label = ""
    if not label:
        label = getattr(btn, "_text", "")
    if not label:
        label = getattr(btn, "_label", "")
    if not label:
        label = btn.winfo_name()
    label = label.replace("\n", " ").strip()
    if label.startswith("!"):
        tooltip = getattr(btn, "_tooltip_text", "")
        kb_id = getattr(btn, "_kb_id", "")
        meta = tooltip or kb_id or label
        label = f"{btn.winfo_class()} ({meta})"
    return label

def keyboard_key_for_button(app, btn) -> str:
    binding_id = app._button_binding_id(btn)
    if binding_id in app._kb_conflicts:
        return ""
    if binding_id in app._key_bindings:
        return app._normalize_key_label(str(app._key_bindings.get(binding_id, "")).strip())
    return app._default_key_for_button(btn)

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

def button_binding_id(app, btn) -> str:
    kb_id = getattr(btn, "_kb_id", "")
    if kb_id:
        return kb_id
    label = app._button_label(btn)
    tip = getattr(btn, "_tooltip_text", "")
    name = btn.winfo_name()
    return f"{label}|{tip}|{name}"

def find_binding_conflict(app, target_btn, label: str):
    seq = app._key_sequence_tuple(label)
    if not seq:
        return None
    for btn in app._collect_buttons():
        if btn is target_btn:
            continue
        other_seq = app._key_sequence_tuple(app._keyboard_key_for_button(btn))
        if other_seq and app._sequence_conflict_pair(seq, other_seq):
            return btn
    return None

def default_key_for_button(app, btn) -> str:
    if btn is getattr(app, "btn_jog_cancel", None):
        return "Space"
    if btn is getattr(app, "btn_all_stop", None):
        return "Enter"
    return ""

def on_kb_table_double_click(app, event):
    if not hasattr(app, "kb_table"):
        return
    row = app.kb_table.identify_row(event.y)
    col = app.kb_table.identify_column(event.x)
    if not row or col != "#3":
        return
    app._start_kb_edit(row, col)

def on_kb_table_click(app, event):
    if not hasattr(app, "kb_table"):
        return
    row = app.kb_table.identify_row(event.y)
    col = app.kb_table.identify_column(event.x)
    if not row:
        return
    if col == "#3":
        app._start_kb_edit(row, col)
        return
    if col == "#4":
        app._start_joystick_capture(row)
        return
    if col != "#5":
        return
    btn = app._kb_item_to_button.get(row)
    if btn is None:
        return
    binding_id = app._button_binding_id(btn)
    app._key_bindings[binding_id] = ""
    if binding_id in app._joystick_bindings:
        app._joystick_bindings.pop(binding_id, None)
    app._apply_keyboard_bindings()

def start_kb_edit(app, row, col):
    bbox = app.kb_table.bbox(row, col)
    if not bbox:
        return
    if app._kb_edit is not None:
        try:
            app._kb_edit.destroy()
        except Exception:
            pass
        app._kb_edit = None
    x, y, w, h = bbox
    value = app.kb_table.set(row, "key")
    entry = ttk.Entry(app.kb_table)
    entry.place(x=x, y=y, width=w, height=h)
    entry.insert(0, "Press keys...")
    app._kb_edit_state[entry] = {
        "prev": "" if value == "None" else value,
        "placeholder": True,
        "seq": [],
        "after_id": None,
    }
    entry.focus()
    entry.bind("<KeyPress>", lambda e: app._kb_capture_key(e, row, entry))
    entry.bind("<FocusOut>", lambda e: app._commit_kb_edit(row, entry))
    app._kb_edit = entry

def start_joystick_capture(app, row):
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
    except Exception:
        pass
    app._ensure_joystick_polling_running()

def cancel_joystick_capture(app):
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
        except Exception:
            pass
    row = state.get("row")
    original = state.get("original", "None")
    if row and hasattr(app, "kb_table") and app.kb_table.exists(row):
        try:
            app.kb_table.set(row, "joystick", original)
        except Exception:
            pass
    app._joystick_capture_state = None

def start_joystick_safety_capture(app):
    if not bool(app.joystick_bindings_enabled.get()):
        messagebox.showinfo(
            "Joystick safety",
            "Enable USB joystick bindings before configuring the safety button.",
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
        "original": app.joystick_safety_status.get(),
        "timer": None,
    }
    timer_id = app.after(JOYSTICK_CAPTURE_TIMEOUT_MS, app._cancel_joystick_safety_capture)
    state["timer"] = timer_id
    app._joystick_capture_state = state
    app.joystick_safety_status.set(JOYSTICK_LISTENING_TEXT)
    app._ensure_joystick_polling_running()

def cancel_joystick_safety_capture(app):
    state = app._joystick_capture_state
    if not state or state.get("mode") != "safety":
        return
    timer_id = state.get("timer")
    if timer_id is not None:
        try:
            app.after_cancel(timer_id)
        except Exception:
            pass
    original = state.get("original", "Safety button: None")
    app.joystick_safety_status.set(original)
    app._joystick_capture_state = None

def clear_joystick_safety_binding(app):
    app._joystick_safety_binding = None
    app._joystick_safety_active = False
    app._refresh_joystick_safety_display()

def on_joystick_safety_toggle(app):
    if not app.joystick_safety_enabled.get():
        app._joystick_safety_active = False
        return
    if not _joystick_safety_ready(app):
        if hasattr(app, "joystick_event_status"):
            app.joystick_event_status.set("Safety enabled but no safety button is set.")

def joystick_binding_from_event(app, key):
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

def kb_capture_key(app, event, row, entry):
    state = app._kb_edit_state.get(entry)
    if state is None:
        return "break"
    if event.keysym in ("Escape",):
        try:
            entry.destroy()
        except Exception:
            pass
        app._kb_edit_state.pop(entry, None)
        app._kb_edit = None
        return "break"
    if event.keysym in ("BackSpace", "Delete"):
        app._commit_kb_edit(row, entry, label_override="")
        return "break"
    label = app._event_to_binding_label(event)
    if not label:
        return "break"
    seq = state["seq"]
    if len(seq) >= 3:
        return "break"
    seq.append(label)
    state["placeholder"] = False
    entry.delete(0, "end")
    entry.insert(0, " ".join(seq))
    after_id = state.get("after_id")
    if after_id is not None:
        entry.after_cancel(after_id)
    if len(seq) >= 3:
        app._commit_kb_edit(row, entry, label_override=" ".join(seq))
        return "break"
    state["after_id"] = entry.after(
        int(app._key_sequence_timeout * 1000),
        lambda: app._commit_kb_edit(row, entry, label_override=" ".join(seq)),
    )
    return "break"

def commit_kb_edit(app, row, entry, label_override: str | None = None):
    if app._kb_edit is None:
        return
    state = app._kb_edit_state.pop(entry, None)
    if label_override is None:
        try:
            new_val = entry.get()
        except Exception:
            new_val = ""
    else:
        new_val = label_override
    try:
        after_id = state.get("after_id") if state else None
        if after_id is not None:
            entry.after_cancel(after_id)
        entry.destroy()
    except Exception:
        pass
    app._kb_edit = None
    placeholder = state.get("placeholder") if state else False
    if label_override is None and placeholder:
        if new_val.strip() == "Press keys...":
            return
    btn = app._kb_item_to_button.get(row)
    if btn is None:
        return
    label = app._normalize_key_label(new_val)
    binding_id = app._button_binding_id(btn)
    app._key_bindings[binding_id] = label
    app._apply_keyboard_bindings()

def normalize_key_label(app, text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""
    chunks = [c for c in raw.replace(",", " ").split() if c.strip()]
    seq = []
    for chunk in chunks:
        chord = app._normalize_key_chord(chunk)
        if chord:
            seq.append(chord)
        if len(seq) >= 3:
            break
    return " ".join(seq)

def normalize_key_chord(app, text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    aliases = {
        "SPACE": "Space",
        "SPC": "Space",
        "ENTER": "Enter",
        "RETURN": "Enter",
        "ESC": "Escape",
        "ESCAPE": "Escape",
        "TAB": "Tab",
        "BACKSPACE": "Backspace",
        "DEL": "Delete",
        "DELETE": "Delete",
        "NONE": "",
        "CTRL": "Ctrl",
        "CONTROL": "Ctrl",
        "SHIFT": "Shift",
        "ALT": "Alt",
        "OPTION": "Alt",
    }
    mods = []
    key = ""
    for part in parts:
        up = part.upper()
        if up in aliases:
            mapped = aliases[up]
            if mapped in ("Ctrl", "Shift", "Alt"):
                if mapped not in mods:
                    mods.append(mapped)
            elif mapped:
                key = mapped
            continue
        if len(part) == 1:
            key = part.upper()
        else:
            key = part
    if not key:
        return ""
    mod_order = ("Ctrl", "Shift", "Alt")
    ordered_mods = [m for m in mod_order if m in mods]
    return "+".join(ordered_mods + [key])

def key_sequence_tuple(app, label: str) -> tuple[str, ...] | None:
    normalized = app._normalize_key_label(label)
    if not normalized:
        return None
    parts = [p for p in normalized.split(" ") if p]
    return tuple(parts[:3])

def update_modifier_state(app, event, pressed: bool) -> bool:
    keysym = getattr(event, "keysym", "")
    if not keysym:
        return False
    if keysym not in app._kb_mod_keysyms:
        return False
    if pressed:
        app._kb_mod_keys_down.add(keysym)
    else:
        app._kb_mod_keys_down.discard(keysym)
    return True

def modifier_active(app, name: str, event_state: int | None = None) -> bool:
    for keysym in app._kb_mod_keys_down:
        if app._kb_mod_keysyms.get(keysym) == name:
            return True
    if event_state is None:
        return False
    if name == "Ctrl":
        return bool(event_state & 0x4)
    if name == "Shift":
        return bool(event_state & 0x1)
    return False

def event_to_binding_label(app, event) -> str:
    keysym = event.keysym
    if app._update_modifier_state(event, pressed=True):
        return ""
    mods = []
    if app._modifier_active("Ctrl", getattr(event, "state", 0)):
        mods.append("Ctrl")
    if app._modifier_active("Shift", getattr(event, "state", 0)):
        mods.append("Shift")
    if app._modifier_active("Alt"):
        mods.append("Alt")
    key_label = app._normalize_key_chord(keysym)
    if not key_label:
        return ""
    if mods:
        return "+".join(mods + [key_label])
    return key_label

def on_key_modifier_release(app, event):
    app._update_modifier_state(event, pressed=False)

def sequence_conflict_pair(app, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
    if not seq_a or not seq_b:
        return False
    min_len = min(len(seq_a), len(seq_b))
    return seq_a[:min_len] == seq_b[:min_len]

def sequence_conflict(app, seq: tuple[str, ...], existing: dict):
    for other_seq in existing.keys():
        if app._sequence_conflict_pair(seq, other_seq):
            return other_seq
    return None

def on_key_sequence(app, event):
    if not app._keyboard_binding_allowed():
        return
    label = app._event_to_binding_label(event)
    if not label:
        return
    now = time.time()
    if now - app._key_sequence_last_time > app._key_sequence_timeout:
        app._key_sequence_buffer = []
    app._key_sequence_last_time = now
    app._key_sequence_buffer.append(label)
    if len(app._key_sequence_buffer) > 3:
        app._key_sequence_buffer = app._key_sequence_buffer[-3:]
    if app._key_sequence_after_id is not None:
        app.after_cancel(app._key_sequence_after_id)
        app._key_sequence_after_id = None
    seq = tuple(app._key_sequence_buffer)
    btn = app._key_sequence_map.get(seq)
    if btn is not None:
        app._key_sequence_buffer = []
        app._on_key_binding(btn)
        return
    app._key_sequence_after_id = app.after(
        int(app._key_sequence_timeout * 1000),
        app._clear_key_sequence_buffer,
    )

def clear_key_sequence_buffer(app):
    app._key_sequence_buffer = []
    if app._key_sequence_after_id is not None:
        try:
            app.after_cancel(app._key_sequence_after_id)
        except Exception:
            pass
    app._key_sequence_after_id = None

def keyboard_binding_allowed(app) -> bool:
    if not bool(app.keyboard_bindings_enabled.get()):
        return False
    try:
        current_grab = app.grab_current()
    except Exception:
        current_grab = None
    if current_grab is not None:
        return False
    try:
        widget = app.focus_get()
    except Exception:
        return False
    if widget is None:
        return True
    try:
        if widget.winfo_toplevel() is not app:
            return False
    except Exception:
        return False
    cls = widget.winfo_class()
    if cls in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox"):
        return False
    return True

def on_key_jog_stop(app, _event=None):
    if not app._keyboard_binding_allowed():
        return
    try:
        if app.btn_jog_cancel.cget("state") == "disabled":
            return
    except Exception:
        return
    try:
        app._stop_joystick_hold()
    except Exception:
        pass
    app.grbl.jog_cancel()

def on_key_all_stop(app, _event=None):
    if not app._keyboard_binding_allowed():
        return
    try:
        if app.btn_all_stop.cget("state") == "disabled":
            return
    except Exception:
        return
    app._all_stop_action()

def on_key_binding(app, btn):
    if not app._keyboard_binding_allowed():
        return
    try:
        if btn.cget("state") == "disabled":
            return
    except Exception:
        return
    app._log_button_action(btn)
    app._invoke_button(btn)

def invoke_button(app, btn):
    if hasattr(btn, "invoke"):
        try:
            btn.invoke()
            return
        except Exception:
            pass
    try:
        cmd = btn.cget("command")
    except Exception:
        cmd = None
    if callable(cmd):
        cmd()

def log_button_action(app, btn):
    if not bool(app.gui_logging_enabled.get()):
        return
    label = app._button_label(btn)
    tip = getattr(btn, "_tooltip_text", "")
    gcode = ""
    try:
        getter = getattr(btn, "_log_gcode_get", None)
        if callable(getter):
            gcode = getter()
        elif isinstance(getter, str):
            gcode = getter
    except Exception:
        gcode = ""
    ts = time.strftime("%H:%M:%S")
    if tip and gcode:
        app.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
    elif tip:
        app.streaming_controller.log(f"[{ts}] Button: {label} | Tip: {tip}")
    elif gcode:
        app.streaming_controller.log(f"[{ts}] Button: {label} | GCode: {gcode}")
    else:
        app.streaming_controller.log(f"[{ts}] Button: {label}")
