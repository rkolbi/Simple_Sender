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

import time
from tkinter import messagebox


def request_settings_dump(app):
    if not app.grbl.is_connected():
        messagebox.showwarning("Not connected", "Connect to GRBL first.")
        return
    stream_state = getattr(app, "_stream_state", None)
    streaming_active = app.grbl.is_streaming() or stream_state in ("running", "paused")
    if streaming_active:
        app._pending_settings_refresh = True
        try:
            app.status.config(text="Settings refresh queued (streaming active)")
        except Exception:
            pass
        messagebox.showwarning(
            "Busy",
            "Stop the stream before requesting settings. The refresh will run once streaming stops.",
        )
        return
    if not app._grbl_ready:
        app._pending_settings_refresh = True
        app.status.config(text="Waiting for Grbl startup...")
        return
    if app._alarm_locked:
        messagebox.showwarning("Alarm", "Clear alarm before requesting settings.")
        return
    app._pending_settings_refresh = False
    app.streaming_controller.log(
        f"[{time.strftime('%H:%M:%S')}] Settings refresh requested ($$)."
    )
    app.settings_controller.start_capture("Requesting $$...")
    app._send_manual("$$", "settings")
