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

from typing import Any


def job_controls_ready(app: Any, has_job: bool | None = None) -> bool:
    if has_job is None:
        try:
            has_job = bool(app.gview.lines_count)
        except Exception:
            has_job = False
    return bool(
        app.connected
        and has_job
        and app._grbl_ready
        and app._status_seen
        and not app._alarm_locked
    )


def set_run_resume_from(app: Any, enabled: bool) -> None:
    state = "normal" if enabled else "disabled"
    app.btn_run.config(state=state)
    app.btn_resume_from.config(state=state)


def disable_job_controls(app: Any) -> None:
    app.btn_run.config(state="disabled")
    app.btn_pause.config(state="disabled")
    app.btn_resume.config(state="disabled")
    app.btn_resume_from.config(state="disabled")
