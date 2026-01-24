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

def init_settings_store(app, script_dir: str, module) -> tuple[float, float]:
    deps = module
    os = deps.os
    get_settings_path = deps.get_settings_path
    Settings = deps.Settings
    app.settings_path = get_settings_path()
    app.settings_dir = os.path.dirname(app.settings_path)
    app._settings_store = Settings(app.settings_path)
    app.settings = app._load_settings()
    # Backward compat: older builds stored this as "keybindings_enabled".
    if "keyboard_bindings_enabled" not in app.settings and "keybindings_enabled" in app.settings:
        app.settings["keyboard_bindings_enabled"] = bool(app.settings.get("keybindings_enabled", True))
    # Migrate jog feed defaults: keep legacy jog_feed for XY, force Z to its own default when absent.
    legacy_jog_feed = app.settings.get("jog_feed")
    has_jog_xy = "jog_feed_xy" in app.settings
    has_jog_z = "jog_feed_z" in app.settings
    default_jog_feed_xy = (
        app.settings["jog_feed_xy"]
        if has_jog_xy
        else (legacy_jog_feed if legacy_jog_feed is not None else 4000.0)
    )
    default_jog_feed_z = app.settings["jog_feed_z"] if has_jog_z else 500.0
    if (
        has_jog_z
        and (legacy_jog_feed is not None)
        and (app.settings["jog_feed_z"] == legacy_jog_feed)
        and (not has_jog_xy)
    ):
        # Likely legacy single value carried over; reset to Z default.
        default_jog_feed_z = 500.0
        app.settings["jog_feed_z"] = default_jog_feed_z
    return default_jog_feed_xy, default_jog_feed_z
