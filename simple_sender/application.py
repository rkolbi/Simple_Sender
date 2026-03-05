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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
import logging
import os
import sys
import time
from typing import Any, TYPE_CHECKING

# GUI imports
import tkinter as tk

# Refactored module imports
from simple_sender import __version__
from simple_sender.application_actions import ActionsMixin
from simple_sender.application_controls import ControlsMixin
from simple_sender.application_gcode import GcodeMixin
from simple_sender.application_input_bindings import InputBindingsMixin
from simple_sender.application_layout import LayoutMixin
from simple_sender.application_lifecycle import LifecycleMixin
from simple_sender.application_status import StatusMixin
from simple_sender.application_state_ui import StateUiMixin
from simple_sender.application_ui_events import UiEventsMixin
from simple_sender.application_ui_toggles import UiTogglesMixin
from simple_sender.ui.app_init import (
    init_basic_preferences,
    init_runtime_state,
    init_settings_store,
)
from simple_sender.ui.dialogs.popup_utils import (
    patch_messagebox,
    set_default_parent,
)
from simple_sender.ui.pi_profile import (
    apply_pi_profile_from_state,
    offer_pi_profile_if_recommended,
)
from simple_sender.utils.perf_monitor import create_app_performance_monitor

if TYPE_CHECKING:
    from simple_sender.macro_executor import MacroExecutor
    from simple_sender.gcode_source import FileGcodeSource

SERIAL_IMPORT_ERROR = ""
UI_QUEUE_DRAIN_INTERVAL_MS = 50
JOYSTICK_RESTORE_DELAY_MS = 0
_APP_TYPE_CHECKING_STUBS: tuple[str, ...] = (
    "_apply_ui_scale",
    "_build_toolbar",
    "_build_main",
    "_init_screen_lock_guard",
    "_set_manual_controls_enabled",
    "_drain_ui_queue",
    "_restore_joystick_bindings_on_start",
    "_on_app_focus_out",
    "_on_close",
    "_bind_touch_command_feedback",
    "refresh_ports",
    "_load_grbl_setting_info",
    "_create_virtual_hold_buttons",
    "_apply_keyboard_bindings",
)


def _resolve_script_file() -> str:
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.isfile(argv0):
        return os.path.abspath(argv0)
    if __file__:
        return os.path.abspath(__file__)
    return os.path.abspath(argv0)


_SCRIPT_FILE = _resolve_script_file()
_SCRIPT_DIR = os.path.dirname(_SCRIPT_FILE)


# Serial imports
try:
    import serial
    from serial.tools import list_ports
    SERIAL_AVAILABLE = True
except ImportError as exc:
    serial = None
    list_ports = None
    SERIAL_AVAILABLE = False
    SERIAL_IMPORT_ERROR = str(exc)

# Utility functions
def _discover_macro_dirs() -> tuple[str, ...]:
    dirs: list[str] = []
    pkg = sys.modules.get("simple_sender")
    pkg_file = getattr(pkg, "__file__", None) if pkg else None
    if pkg_file:
        pkg_dir = os.path.dirname(pkg_file)
        macros_dir = os.path.join(pkg_dir, "macros")
        if os.path.isdir(macros_dir):
            dirs.append(macros_dir)
    script_dir = _SCRIPT_DIR
    root_macros = os.path.join(script_dir, "macros")
    if os.path.isdir(root_macros) and root_macros not in dirs:
        dirs.append(root_macros)
    if script_dir not in dirs:
        dirs.append(script_dir)
    return tuple(dirs)

_MACRO_SEARCH_DIRS = _discover_macro_dirs()

_APP_MIXINS = (
    GcodeMixin,
    ControlsMixin,
    LayoutMixin,
    ActionsMixin,
    StatusMixin,
    UiEventsMixin,
    LifecycleMixin,
    InputBindingsMixin,
    UiTogglesMixin,
    StateUiMixin,
)
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _install_app_mixin_methods(target_cls: type, mixins: tuple[type, ...]) -> None:
    for mixin in mixins:
        for name, member in mixin.__dict__.items():
            if name.startswith("__"):
                continue
            if not callable(member):
                continue
            if name in target_cls.__dict__:
                continue
            setattr(target_cls, name, member)


class App(tk.Tk):
    HIDDEN_MPOS_BUTTON_STYLE = "SimpleSender.HiddenMpos.TButton"
    # Type hints for attributes initialized in helper modules.
    connected: bool
    macro_executor: "MacroExecutor"
    settings: dict[str, Any]
    reconnect_on_open: tk.BooleanVar
    fullscreen_on_startup: tk.BooleanVar
    stop_hold_on_focus_loss: tk.BooleanVar
    validate_streaming_gcode: tk.BooleanVar
    streaming_controller: Any
    tool_reference_var: tk.StringVar
    machine_state: tk.StringVar
    _machine_state_text: str
    _last_gcode_lines: list[str]
    _last_parse_result: Any
    _last_parse_hash: str | None
    _gcode_parse_token: int
    _gcode_source: "FileGcodeSource | None"
    _gcode_streaming_mode: bool
    _gcode_total_lines: int
    _resume_after_disconnect: bool
    _resume_from_index: int | None
    _resume_job_name: str | None
    _manual_queue_drop_total: int
    _script_dir: str
    _serial_available: bool
    _serial_import_error: str

    if TYPE_CHECKING:
        # Intentionally curated stubs for critical mixin-installed methods used
        # in App.__init__ and common call sites. This is not an exhaustive list
        # of every method installed from _APP_MIXINS.
        def _apply_ui_scale(self, value: float | None = None) -> float: ...
        def _build_toolbar(self) -> None: ...
        def _build_main(self) -> None: ...
        def _init_screen_lock_guard(self) -> None: ...
        def _set_manual_controls_enabled(self, enabled: bool) -> None: ...
        def _drain_ui_queue(self) -> None: ...
        def _restore_joystick_bindings_on_start(self) -> None: ...
        def _on_app_focus_out(self, event: Any | None = None) -> None: ...
        def _on_close(self) -> None: ...
        def _bind_touch_command_feedback(self) -> None: ...
        def refresh_ports(self, auto_connect: bool = False) -> None: ...
        def _load_grbl_setting_info(self) -> None: ...
        def _create_virtual_hold_buttons(self) -> list[Any]: ...
        def _apply_keyboard_bindings(self) -> None: ...

    def __init__(self, *, startup_started_at: float | None = None):
        super().__init__()
        self._script_dir = _SCRIPT_DIR
        self._serial_available = SERIAL_AVAILABLE
        self._serial_import_error = SERIAL_IMPORT_ERROR
        self.title("Simple Sender (BETA)")
        self.minsize(980, 620)
        self.bind("<Escape>", lambda _evt: self.attributes("-fullscreen", False))
        default_jog_feed_xy, default_jog_feed_z = init_settings_store(self, _SCRIPT_DIR)
        init_basic_preferences(self, __version__)
        if bool(self.fullscreen_on_startup.get()):
            try:
                self.attributes("-fullscreen", True)
            except tk.TclError as exc:
                _log_suppressed("Failed enabling fullscreen on startup", exc)
        self._apply_ui_scale(self.settings.get("ui_scale", 1.5))
        init_runtime_state(self, default_jog_feed_xy, default_jog_feed_z, _MACRO_SEARCH_DIRS)
        self._perf_monitor = create_app_performance_monitor(
            self,
            startup_started_at=startup_started_at,
        )
        apply_pi_profile_from_state(self, save_settings=False, emit_status=False)
        set_default_parent(self)
        patch_messagebox()

        # Top + main layout
        self._build_toolbar()
        self._build_main()
        self._bind_touch_command_feedback()
        self._init_screen_lock_guard()
        self._set_manual_controls_enabled(False)

        self.after(UI_QUEUE_DRAIN_INTERVAL_MS, self._drain_ui_queue)
        self.after(JOYSTICK_RESTORE_DELAY_MS, self._restore_joystick_bindings_on_start)
        self.bind_all("<FocusOut>", self._on_app_focus_out)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_ports(auto_connect=False)
        if not self.connected and bool(self.reconnect_on_open.get()):
            last_port = (self.settings.get("last_port") or "").strip()
            if last_port:
                self._auto_reconnect_last_port = last_port
                self._auto_reconnect_pending = True
                delay_s = 0.0
                try:
                    delay_s = float(getattr(self, "_startup_auto_connect_delay_s", 5.0) or 0.0)
                except Exception:
                    delay_s = 5.0
                if delay_s > 0.0:
                    gate_until = time.time() + delay_s
                    self._auto_reconnect_startup_gate_ts = gate_until
                    self._auto_reconnect_next_ts = max(
                        float(getattr(self, "_auto_reconnect_next_ts", 0.0) or 0.0),
                        gate_until,
                    )
                    try:
                        history = getattr(self, "_connection_timeline", None)
                        if history is not None:
                            history.append(
                                {
                                    "ts": float(time.time()),
                                    "event": "startup_autoconnect_delay",
                                    "details": f"delay_s={delay_s:.1f} port={last_port}",
                                }
                            )
                    except Exception as exc:
                        _log_suppressed("Failed recording startup auto-connect delay timeline event", exc)
        geometry = self.settings.get("window_geometry", "")
        if isinstance(geometry, str) and geometry:
            try:
                self.geometry(geometry)
            except tk.TclError as exc:
                _log_suppressed("Failed applying saved window geometry", exc)
        self._load_grbl_setting_info()
        self.streaming_controller.bind_button_logging()
        self._virtual_hold_buttons = self._create_virtual_hold_buttons()
        self._apply_keyboard_bindings()
        self.after(1200, lambda: offer_pi_profile_if_recommended(self))
        perf_monitor = getattr(self, "_perf_monitor", None)
        if perf_monitor is not None:
            try:
                perf_monitor.mark_app_ready()
            except Exception as exc:
                _log_suppressed("Failed marking application startup readiness in performance monitor", exc)


_install_app_mixin_methods(App, _APP_MIXINS)
