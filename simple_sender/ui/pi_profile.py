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
import queue
from tkinter import messagebox

from simple_sender.utils.platform_detect import detect_raspberry_pi

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()

PI_PROFILE_STATUS_POLL_INTERVAL = 0.35
PI_PROFILE_STREAMING_LINE_THRESHOLD = 100_000
PI_PROFILE_STREAMING_RENDER_INTERVAL = 0.5
PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_MS = 180
PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_DEFAULT_MS = 125
PI_PROFILE_UI_MAINTENANCE_IDLE_INTERVAL_S = 1.5
PI_PROFILE_UI_RECONNECT_IDLE_INTERVAL_S = 1.5
PI_PROFILE_PROMPT_SHOWN_KEY = "pi_profile_prompt_shown"


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _read_bool_setting(app, *, attr_name: str, key: str, default: bool = False) -> bool:
    var = getattr(app, attr_name, None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get(key, default))
        except Exception:
            return bool(default)
    return bool(default)


def is_pi_profile_enabled(app) -> bool:
    return _read_bool_setting(
        app,
        attr_name="pi_profile_enabled",
        key="pi_profile_enabled",
        default=False,
    )


def _set_var(app, attr_name: str, value) -> bool:
    var = getattr(app, attr_name, None)
    if var is None:
        return False
    try:
        var.set(value)
        return True
    except Exception as exc:
        _log_suppressed(f"Failed setting {attr_name} for Pi profile", exc)
        return False


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


def _save_settings_safe(app) -> None:
    saver = getattr(app, "_save_settings", None)
    if not callable(saver):
        return
    try:
        saver()
    except Exception as exc:
        _log_suppressed("Failed saving settings while applying Pi profile policy", exc)


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
        except Exception as exc:
            _log_suppressed("Failed restoring default UI queue idle interval for Pi profile", exc)
        try:
            app._ui_maintenance_idle_interval_s = 1.0
            app._auto_reconnect_check_idle_interval_s = 1.0
        except Exception as exc:
            _log_suppressed("Failed restoring default idle maintenance/reconnect intervals for Pi profile", exc)
        if emit_status:
            _log_status(app, "[settings] Pi profile disabled")
        if save_settings:
            _save_settings_safe(app)
        return

    _set_var(app, "performance_mode", True)
    _invoke_handler(app, "_on_performance_mode_change")
    try:
        app._ui_queue_idle_interval_ms = PI_PROFILE_UI_QUEUE_IDLE_INTERVAL_MS
    except Exception as exc:
        _log_suppressed("Failed applying UI queue idle interval for Pi profile", exc)
    try:
        app._ui_maintenance_idle_interval_s = PI_PROFILE_UI_MAINTENANCE_IDLE_INTERVAL_S
        app._auto_reconnect_check_idle_interval_s = PI_PROFILE_UI_RECONNECT_IDLE_INTERVAL_S
    except Exception as exc:
        _log_suppressed("Failed applying idle maintenance/reconnect intervals for Pi profile", exc)
    _set_var(app, "gui_logging_enabled", False)
    _invoke_handler(app, "_on_gui_logging_change")
    _set_var(app, "validate_streaming_gcode", False)
    _set_var(app, "streaming_line_threshold", PI_PROFILE_STREAMING_LINE_THRESHOLD)
    _set_var(app, "status_poll_interval", PI_PROFILE_STATUS_POLL_INTERVAL)
    _invoke_handler(app, "_on_status_interval_change")
    _set_var(app, "toolpath_lightweight", True)
    _invoke_handler(app, "_on_toolpath_lightweight_change")
    _set_var(app, "toolpath_streaming_render_interval", PI_PROFILE_STREAMING_RENDER_INTERVAL)
    _invoke_handler(app, "_apply_toolpath_streaming_render_interval")
    _set_var(app, "render3d_enabled", False)
    _invoke_handler(app, "_refresh_render_3d_toggle_text")
    toolpath_panel = getattr(app, "toolpath_panel", None)
    if toolpath_panel is not None:
        try:
            toolpath_panel.set_enabled(False)
        except Exception as exc:
            _log_suppressed("Failed disabling 3D toolpath panel for Pi profile", exc)
    _set_var(app, "show_autolevel_overlay", False)
    _invoke_handler(app, "_on_autolevel_overlay_change")
    if emit_status:
        _log_status(app, "[settings] Pi profile enabled")
    if save_settings:
        _save_settings_safe(app)


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
    _save_settings_safe(app)
    return accepted
