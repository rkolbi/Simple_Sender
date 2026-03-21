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

"""Typed default configuration values for Simple Sender."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StatusPollingConfig:
    """GRBL status-poll timing and guardrail defaults."""

    default_interval: float = 0.2
    idle_interval: float = 0.5
    running_interval: float = 0.2
    min_interval: float = 0.05
    failure_limit_default: int = 3
    failure_limit_min: int = 1
    failure_limit_max: int = 10


@dataclass(frozen=True)
class GCodeCacheConfig:
    """G-code caching and fast-scan defaults."""

    streaming_line_threshold: int = 250_000
    max_lines_default: int = 20_000
    max_lines_low_power: int = 10_000
    in_memory_send_cache_threshold: int = 50_000
    prep_fast_scan_max_lines_default: int = 50_000
    prep_fast_scan_max_lines_low_power: int = 20_000


@dataclass(frozen=True)
class PiProfileConfig:
    """Existing Raspberry Pi profile defaults."""

    status_poll_interval: float = 1.25
    streaming_line_threshold: int = 20_000
    ui_queue_idle_interval_ms: int = 360
    ui_queue_idle_interval_default_ms: int = 300
    ui_queue_idle_max_interval_ms: int = 1200
    ui_queue_idle_max_interval_default_ms: int = 900
    ui_queue_idle_backoff_step_ms: int = 90
    ui_queue_idle_backoff_step_default_ms: int = 60
    joystick_poll_interval_ms: int = 30
    joystick_poll_idle_max_interval_ms: int = 320
    joystick_poll_idle_backoff_step_ms: int = 16
    ui_maintenance_idle_interval_s: float = 3.5
    ui_maintenance_quiet_idle_interval_s: float = 6.0
    ui_reconnect_idle_interval_s: float = 3.5
    prompt_shown_key: str = "pi_profile_prompt_shown"


@dataclass(frozen=True)
class AppConfig:
    """Application-wide grouped defaults."""

    status_polling: StatusPollingConfig = field(default_factory=StatusPollingConfig)
    gcode_cache: GCodeCacheConfig = field(default_factory=GCodeCacheConfig)
    pi_profile: PiProfileConfig = field(default_factory=PiProfileConfig)

    @classmethod
    def for_raspberry_pi(cls) -> "AppConfig":
        """Create a low-power default profile without changing legacy values."""

        default = cls()
        return cls(
            status_polling=default.status_polling,
            gcode_cache=GCodeCacheConfig(
                streaming_line_threshold=default.pi_profile.streaming_line_threshold,
                max_lines_default=default.gcode_cache.max_lines_low_power,
                max_lines_low_power=default.gcode_cache.max_lines_low_power,
                in_memory_send_cache_threshold=default.gcode_cache.in_memory_send_cache_threshold,
                prep_fast_scan_max_lines_default=default.gcode_cache.prep_fast_scan_max_lines_low_power,
                prep_fast_scan_max_lines_low_power=default.gcode_cache.prep_fast_scan_max_lines_low_power,
            ),
            pi_profile=default.pi_profile,
        )


DEFAULT_APP_CONFIG = AppConfig()
"""Grouped application defaults preserving existing runtime values."""


RASPBERRY_PI_APP_CONFIG = AppConfig.for_raspberry_pi()
"""Grouped low-power defaults matching existing Pi-oriented values."""
