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
    default_jog_feed_xy = app.settings.get("jog_feed_xy", 4000.0)
    default_jog_feed_z = app.settings.get("jog_feed_z", 500.0)
    return default_jog_feed_xy, default_jog_feed_z
