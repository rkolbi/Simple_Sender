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

"""Shared Raspberry Pi profile policy helpers."""

from __future__ import annotations

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import queue
from tkinter import messagebox

from simple_sender.config.defaults import DEFAULT_APP_CONFIG
from simple_sender.ui.tk_vars import read_bool_pref, safe_set_var_attr
from simple_sender.utils.platform_detect import detect_raspberry_pi

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()

PI_PROFILE_STATUS_POLL_INTERVAL = DEFAULT_APP_CONFIG.pi_profile.status_poll_interval
PI_PROFILE_STREAMING_LINE_THRESHOLD = (
    DEFAULT_APP_CONFIG.pi_profile.streaming_line_threshold
)
PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_interval_ms
)
PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_DEFAULT_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_interval_default_ms
)
PI_PROFILE_UI_QUEUE_IDLE_MAX_INTERVAL_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_max_interval_ms
)
PI_PROFILE_UI_QUEUE_IDLE_MAX_INTERVAL_DEFAULT_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_max_interval_default_ms
)
PI_PROFILE_UI_QUEUE_IDLE_BACKOFF_STEP_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_backoff_step_ms
)
PI_PROFILE_UI_QUEUE_IDLE_BACKOFF_STEP_DEFAULT_MS = (
    DEFAULT_APP_CONFIG.pi_profile.ui_queue_idle_backoff_step_default_ms
)
PI_PROFILE_JOYSTICK_POLL_INTERVAL_MS = (
    DEFAULT_APP_CONFIG.pi_profile.joystick_poll_interval_ms
)
PI_PROFILE_JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS = (
    DEFAULT_APP_CONFIG.pi_profile.joystick_poll_idle_max_interval_ms
)
PI_PROFILE_JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS = (
    DEFAULT_APP_CONFIG.pi_profile.joystick_poll_idle_backoff_step_ms
)
PI_PROFILE_UI_MAINTENANCE_IDLE_INTERVAL_S = (
    DEFAULT_APP_CONFIG.pi_profile.ui_maintenance_idle_interval_s
)
PI_PROFILE_UI_MAINTENANCE_QUIET_IDLE_INTERVAL_S = (
    DEFAULT_APP_CONFIG.pi_profile.ui_maintenance_quiet_idle_interval_s
)
PI_PROFILE_UI_RECONNECT_IDLE_INTERVAL_S = (
    DEFAULT_APP_CONFIG.pi_profile.ui_reconnect_idle_interval_s
)
PI_PROFILE_PROMPT_SHOWN_KEY = DEFAULT_APP_CONFIG.pi_profile.prompt_shown_key


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


def _read_bool_setting(app, *, attr_name: str, key: str, default: bool = False) -> bool:
    return read_bool_pref(app, attr_name=attr_name, key=key, default=default)


def is_pi_profile_enabled(app) -> bool:
    return _read_bool_setting(
        app,
        attr_name="pi_profile_enabled",
        key="pi_profile_enabled",
        default=False,
    )


def _set_var(app, attr_name: str, value) -> bool:
    return safe_set_var_attr(
        app,
        attr_name,
        value,
        log_suppressed=_log_suppressed,
        context=f"Failed setting {attr_name} for Pi profile",
    )


def _invoke_handler(app, handler_name: str) -> None:
    handler = getattr(app, handler_name, None)
    if not callable(handler):
        return
    try:
        handler()
    except Exception as exc:
        _log_suppressed(f"Failed applying Pi profile handler {handler_name}", exc)


def _log_status(app, text: str) -> None:
    try:
        app.ui_q.put(("log", text))
    except (AttributeError, TypeError, queue.Full) as exc:
        _log_suppressed("Failed queueing Pi-profile status message", exc)
    try:
        app.status.config(text=text)
    except Exception as exc:
        _log_suppressed("Failed updating status label for Pi profile", exc)


def _save_settings_safe(app) -> bool:
    saver = getattr(app, "_save_settings", None)
    if not callable(saver):
        return True
    try:
        saver()
        return True
    except Exception as exc:
        _log_suppressed("Failed saving settings while applying Pi profile policy", exc)
        return False


def apply_pi_profile(
    app,
    *,
    enabled: bool,
    save_settings: bool = True,
    emit_status: bool = True,
) -> None:
    enabled = bool(enabled)
    if not enabled:
        try:
            app._ui_queue_idle_interval_ms = PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_DEFAULT_MS
            app._ui_queue_idle_max_interval_ms = PI_PROFILE_UI_QUEUE_IDLE_MAX_INTERVAL_DEFAULT_MS
            app._ui_queue_idle_backoff_step_ms = PI_PROFILE_UI_QUEUE_IDLE_BACKOFF_STEP_DEFAULT_MS
            app._ui_queue_idle_streak = 0
        except Exception as exc:
            _log_suppressed("Failed restoring default UI queue idle interval for Pi profile", exc)
        try:
            app._joystick_poll_interval_ms = int(getattr(app, "_joystick_poll_interval_default_ms", 50))
            app._joystick_poll_idle_max_interval_ms = int(
                getattr(app, "_joystick_poll_idle_max_interval_default_ms", 200)
            )
            app._joystick_poll_idle_backoff_step_ms = int(
                getattr(app, "_joystick_poll_idle_backoff_step_default_ms", 10)
            )
            app._joystick_poll_idle_streak = 0
        except Exception as exc:
            _log_suppressed("Failed restoring default joystick polling intervals for Pi profile", exc)
        try:
            app._ui_maintenance_idle_interval_s = 1.25
            app._ui_maintenance_quiet_idle_interval_s = 4.0
            app._auto_reconnect_check_idle_interval_s = 1.25
        except Exception as exc:
            _log_suppressed("Failed restoring default idle maintenance/reconnect intervals for Pi profile", exc)
        persisted = True
        if save_settings:
            persisted = bool(_save_settings_safe(app))
        if emit_status or not persisted:
            text = (
                "[settings] Pi profile disabled"
                if persisted
                else "[settings] Pi profile disabled in memory only; settings save failed"
            )
            _log_status(app, text)
        return

    previous_defer = bool(getattr(app, "_defer_ui_settings_save", False))
    app._defer_ui_settings_save = True
    try:
        _set_var(app, "performance_mode", True)
        _invoke_handler(app, "_on_performance_mode_change")
        try:
            app._ui_queue_idle_interval_ms = PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_MS
            app._ui_queue_idle_max_interval_ms = PI_PROFILE_UI_QUEUE_IDLE_MAX_INTERVAL_MS
            app._ui_queue_idle_backoff_step_ms = PI_PROFILE_UI_QUEUE_IDLE_BACKOFF_STEP_MS
            app._ui_queue_idle_streak = 0
        except Exception as exc:
            _log_suppressed("Failed applying UI queue idle interval for Pi profile", exc)
        try:
            app._joystick_poll_interval_ms = PI_PROFILE_JOYSTICK_POLL_INTERVAL_MS
            app._joystick_poll_idle_max_interval_ms = PI_PROFILE_JOYSTICK_POLL_IDLE_MAX_INTERVAL_MS
            app._joystick_poll_idle_backoff_step_ms = PI_PROFILE_JOYSTICK_POLL_IDLE_BACKOFF_STEP_MS
            app._joystick_poll_idle_streak = 0
        except Exception as exc:
            _log_suppressed("Failed applying joystick polling intervals for Pi profile", exc)
        try:
            app._ui_maintenance_idle_interval_s = PI_PROFILE_UI_MAINTENANCE_IDLE_INTERVAL_S
            app._ui_maintenance_quiet_idle_interval_s = PI_PROFILE_UI_MAINTENANCE_QUIET_IDLE_INTERVAL_S
            app._auto_reconnect_check_idle_interval_s = PI_PROFILE_UI_RECONNECT_IDLE_INTERVAL_S
        except Exception as exc:
            _log_suppressed("Failed applying idle maintenance/reconnect intervals for Pi profile", exc)
        _set_var(app, "gui_logging_enabled", False)
        _invoke_handler(app, "_on_gui_logging_change")
        _set_var(app, "runtime_logging_mode", "Standard")
        _invoke_handler(app, "_on_runtime_logging_mode_change")
        _set_var(app, "streaming_line_threshold", PI_PROFILE_STREAMING_LINE_THRESHOLD)
        _set_var(app, "status_poll_interval", PI_PROFILE_STATUS_POLL_INTERVAL)
        _invoke_handler(app, "_on_status_interval_change")
        _set_var(app, "show_autolevel_overlay", False)
        _invoke_handler(app, "_on_autolevel_overlay_change")
    finally:
        app._defer_ui_settings_save = previous_defer
    persisted = True
    if save_settings:
        persisted = bool(_save_settings_safe(app))
    if emit_status or not persisted:
        text = (
            "[settings] Pi profile enabled"
            if persisted
            else "[settings] Pi profile enabled in memory only; settings save failed"
        )
        _log_status(app, text)


def apply_pi_profile_from_state(
    app,
    *,
    save_settings: bool = False,
    emit_status: bool = False,
) -> None:
    apply_pi_profile(
        app,
        enabled=is_pi_profile_enabled(app),
        save_settings=save_settings,
        emit_status=emit_status,
    )


def should_offer_pi_profile(app) -> bool:
    if not detect_raspberry_pi():
        return False
    if is_pi_profile_enabled(app):
        return False
    settings = getattr(app, "settings", None)
    if not isinstance(settings, dict):
        return False
    return not bool(settings.get(PI_PROFILE_PROMPT_SHOWN_KEY, False))


def offer_pi_profile_if_recommended(app) -> bool:
    if not should_offer_pi_profile(app):
        return False
    settings = getattr(app, "settings", None)
    if not isinstance(settings, dict):
        return False

    try:
        accepted = bool(
            messagebox.askyesno(
                "Enable Pi profile?",
                (
                    "Raspberry Pi detected.\n"
                    "Enable Pi profile for lower CPU/memory usage and smoother streaming?"
                ),
            )
        )
    except Exception as exc:
        _log_suppressed("Failed prompting user for Pi profile recommendation", exc)
        accepted = False
    settings[PI_PROFILE_PROMPT_SHOWN_KEY] = True
    if accepted:
        _set_var(app, "pi_profile_enabled", True)
        settings["pi_profile_enabled"] = True
        apply_pi_profile(app, enabled=True, save_settings=False, emit_status=True)
    persisted = bool(_save_settings_safe(app))
    if accepted and not persisted:
        _log_status(app, "[settings] Pi profile enabled in memory only; settings save failed")
    return accepted

