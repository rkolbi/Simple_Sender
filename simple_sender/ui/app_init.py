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

import copy
import os
import queue
import sys
from typing import Any, cast

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

from simple_sender.autolevel.probe_controller import ProbeController
from simple_sender.autolevel.probe_runner import AutoLevelProbeRunner
from simple_sender.grbl_worker import GrblWorker
from simple_sender.macro_executor import MacroExecutor
from simple_sender.streaming_controller import StreamingController
from simple_sender.ui.grbl_settings import GRBLSettingsController
from simple_sender.ui.input_bindings import PYGAME_AVAILABLE
from simple_sender.ui.macro_panel import MacroPanel
from simple_sender.ui.toolpath import ToolpathPanel
from simple_sender.ui.ui_queue import UiEventQueue
from simple_sender.ui.app_init_preferences import init_basic_preferences as _init_basic_preferences
from simple_sender.ui.app_init_runtime import init_runtime_state as _init_runtime_state
from simple_sender.ui.app_init_settings import init_settings_store as _init_settings_store
from simple_sender.utils import Settings, get_settings_path
from simple_sender.utils.config import DEFAULT_SETTINGS
from simple_sender.utils.constants import (
    GCODE_STREAMING_LINE_THRESHOLD,
    STATUS_POLL_DEFAULT,
    UI_EVENT_QUEUE_MAXSIZE,
    WATCHDOG_HOMING_TIMEOUT,
)


def init_settings_store(app, script_dir: str) -> tuple[float, float]:
    return _init_settings_store(app, script_dir, module=sys.modules[__name__])


def init_basic_preferences(app, app_version: str):
    _init_basic_preferences(app, app_version, module=sys.modules[__name__])


def init_runtime_state(
    app,
    default_jog_feed_xy: float,
    default_jog_feed_z: float,
    macro_search_dirs: tuple[str, ...],
):
    _init_runtime_state(
        app,
        default_jog_feed_xy,
        default_jog_feed_z,
        macro_search_dirs,
        module=sys.modules[__name__],
    )

