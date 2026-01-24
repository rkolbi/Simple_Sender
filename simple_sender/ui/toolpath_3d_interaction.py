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
"""3D toolpath interaction helpers."""

import logging

from simple_sender.utils.constants import (
    VIEW_3D_DEFAULT_AZIMUTH,
    VIEW_3D_DEFAULT_ELEVATION,
    VIEW_3D_DEFAULT_ZOOM,
    VIEW_3D_DRAG_SENSITIVITY,
    VIEW_3D_ELEVATION_LIMIT,
    VIEW_3D_ZOOM_MAX,
    VIEW_3D_ZOOM_MIN,
    VIEW_3D_ZOOM_STEP,
)

logger = logging.getLogger(__name__)


class Toolpath3DInteractionMixin:
    def _on_resize(self, _event=None):
        self._schedule_render()

    def _on_drag_start(self, event):
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self._drag_start = (event.x, event.y)
        self.azimuth += dx * VIEW_3D_DRAG_SENSITIVITY
        self.elevation += dy * VIEW_3D_DRAG_SENSITIVITY
        limit = VIEW_3D_ELEVATION_LIMIT
        self.elevation = max(-limit, min(limit, self.elevation))
        self._schedule_render()
        self._enter_fast_mode()

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y)

    def _on_pan(self, event):
        if not self._pan_start:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self._pan_start = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self._schedule_render()
        self._enter_fast_mode()

    def _on_mousewheel(self, event):
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        else:
            direction = 1 if event.num == 4 else -1
        if direction > 0:
            self.zoom *= VIEW_3D_ZOOM_STEP
        else:
            self.zoom /= VIEW_3D_ZOOM_STEP
        self.zoom = max(VIEW_3D_ZOOM_MIN, min(VIEW_3D_ZOOM_MAX, self.zoom))
        self._schedule_render()
        self._enter_fast_mode()

    def _reset_view(self):
        self.azimuth = VIEW_3D_DEFAULT_AZIMUTH
        self.elevation = VIEW_3D_DEFAULT_ELEVATION
        self.zoom = VIEW_3D_DEFAULT_ZOOM
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._schedule_render()

    def _save_view(self):
        if callable(self.on_save_view):
            self.on_save_view()

    def _load_view(self):
        if callable(self.on_load_view):
            self.on_load_view()

    def get_view(self) -> dict:
        return {
            "azimuth": self.azimuth,
            "elevation": self.elevation,
            "zoom": self.zoom,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

    def apply_view(self, view: dict):
        if not view:
            return
        try:
            self.azimuth = float(view.get("azimuth", self.azimuth))
            self.elevation = float(view.get("elevation", self.elevation))
            self.zoom = float(view.get("zoom", self.zoom))
            self.pan_x = float(view.get("pan_x", self.pan_x))
            self.pan_y = float(view.get("pan_y", self.pan_y))
        except Exception as exc:
            logger.exception("Failed to apply 3D view state: %s", exc)
            return
        self._schedule_render()

    def _enter_fast_mode(self):
        self._fast_mode = True
        if self._fast_mode_after_id is not None:
            try:
                self.after_cancel(self._fast_mode_after_id)
            except Exception:
                pass
        self._fast_mode_after_id = self.after(int(self._fast_mode_duration * 1000), self._exit_fast_mode)

    def _exit_fast_mode(self):
        self._fast_mode_after_id = None
        if not self._fast_mode:
            return
        self._fast_mode = False
        self._schedule_render()
