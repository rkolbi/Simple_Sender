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

"""Resume-from-line helpers and cached modal preamble reconstruction."""

import logging
from simple_sender.utils.log_suppressed import log_suppressed_exception
import threading
from collections import deque
from dataclasses import dataclass
from tkinter import messagebox

from simple_sender.constants.messages import BusyMessages, DialogTitles
from simple_sender.gcode_parser import clean_gcode_line, WORD_PAT
from simple_sender.services.job_service import DryRunStartDecision
from simple_sender.types import LineSource
from simple_sender.ui.dry_run_start_prompt import confirm_dry_run_start_mode
from simple_sender.ui.job_setup_state import (
    confirm_job_start_without_setup,
    has_valid_job_setup_state,
)
from simple_sender.ui.preflight_gate import run_preflight_gate

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_RESUME_G92_WARNING_TITLE = "Resume warning"
_RESUME_G92_WARNING_BODY = (
    "G92 offsets were detected before the selected resume line. Confirm work zero and "
    "controller state before resuming.\n\nResume anyway?"
)
_RESUME_UNSUPPORTED_TLO_TITLE = "Resume blocked"
_RESUME_UNSUPPORTED_TLO_BODY = (
    "A dynamic tool length offset command (G43.1) was detected before the selected "
    "resume line, but its Z value could not be reconstructed safely. Resume From was "
    "canceled to avoid an unknown Z relationship."
)
# Keep only a few recent line sources cached so repeated dialog use stays fast
# without retaining many full job references.
_RESUME_CACHE_MAX = 8
# Checkpoint every 256 parsed lines to trade a small amount of cache state for
# much faster repeated preamble rebuilds near the same region.
_RESUME_CHECKPOINT_STRIDE = 256
_resume_cache_lock = threading.Lock()
_resume_preamble_cache: dict[int, tuple[object, "_ResumePreambleCache"]] = {}
_resume_cache_order: deque[int] = deque()


def _log_suppressed(context: str, exc: BaseException) -> None:
    log_suppressed_exception(logger, context, exc, suppressed=_logged_suppressed)


@dataclass(slots=True)
class _ResumeModalState:
    units: str | None = None
    distance: str | None = None
    plane: str | None = None
    feed_mode: str | None = None
    arc_mode: str | None = None
    coord: str | None = None
    spindle: int | None = None
    coolant: int | None = None
    feed: float | None = None
    spindle_speed: float | None = None
    dynamic_tlo_z: float | None = None
    unsupported_dynamic_tlo: bool = False
    has_g92: bool = False

    def copy(self) -> "_ResumeModalState":
        return _ResumeModalState(
            units=self.units,
            distance=self.distance,
            plane=self.plane,
            feed_mode=self.feed_mode,
            arc_mode=self.arc_mode,
            coord=self.coord,
            spindle=self.spindle,
            coolant=self.coolant,
            feed=self.feed,
            spindle_speed=self.spindle_speed,
            dynamic_tlo_z=self.dynamic_tlo_z,
            unsupported_dynamic_tlo=self.unsupported_dynamic_tlo,
            has_g92=self.has_g92,
        )

    def to_preamble(self) -> list[str]:
        preamble = []
        for item in (
            self.units,
            self.distance,
            self.plane,
            self.arc_mode,
            self.feed_mode,
            self.coord,
        ):
            if item:
                preamble.append(item)
        if self.dynamic_tlo_z is not None:
            preamble.append(f"G43.1 Z{self.dynamic_tlo_z:g}")
        if self.feed is not None:
            preamble.append(f"F{self.feed:g}")
        if self.spindle is not None:
            if self.spindle in (3, 4):
                if self.spindle_speed is not None:
                    preamble.append(f"M{self.spindle} S{self.spindle_speed:g}")
                else:
                    preamble.append(f"M{self.spindle}")
            else:
                preamble.append("M5")
        if self.coolant is not None:
            preamble.append(f"M{self.coolant}")
        return preamble


class _ResumePreambleCache:
    """Thread-safe checkpoint cache for modal resume preambles."""

    def __init__(self, lines: LineSource):
        self._lines = lines
        self._lock = threading.Lock()
        self._line_count = self._safe_len(lines)
        self._checkpoints: dict[int, _ResumeModalState] = {0: _ResumeModalState()}

    @staticmethod
    def _safe_len(lines: LineSource) -> int | None:
        try:
            return max(0, int(len(lines)))
        except Exception:
            return None

    def _reset_if_size_changed(self) -> None:
        latest_count = self._safe_len(self._lines)
        if latest_count is None:
            return
        if self._line_count is None:
            self._line_count = latest_count
            return
        if latest_count != self._line_count:
            self._line_count = latest_count
            self._checkpoints = {0: _ResumeModalState()}

    def get(self, stop_index: int) -> tuple[list[str], bool]:
        """Return the reconstructed modal preamble up to ``stop_index``."""

        target = max(0, int(stop_index))
        with self._lock:
            self._reset_if_size_changed()
            checkpoint = 0
            for idx in self._checkpoints.keys():
                if idx <= target and idx >= checkpoint:
                    checkpoint = idx
            state = self._checkpoints[checkpoint].copy()
            for idx in range(checkpoint, target):
                try:
                    raw = self._lines[idx]
                except Exception:
                    break
                _apply_line_to_state(state, raw)
                next_idx = idx + 1
                if next_idx % _RESUME_CHECKPOINT_STRIDE == 0:
                    self._checkpoints[next_idx] = state.copy()
            self._checkpoints[target] = state.copy()
            return state.to_preamble(), state.has_g92

    def get_details(self, stop_index: int) -> tuple[list[str], bool, bool]:
        """Return modal preamble plus warning flags up to ``stop_index``."""

        target = max(0, int(stop_index))
        with self._lock:
            self._reset_if_size_changed()
            checkpoint = 0
            for idx in self._checkpoints.keys():
                if idx <= target and idx >= checkpoint:
                    checkpoint = idx
            state = self._checkpoints[checkpoint].copy()
            for idx in range(checkpoint, target):
                try:
                    raw = self._lines[idx]
                except Exception:
                    break
                _apply_line_to_state(state, raw)
                next_idx = idx + 1
                if next_idx % _RESUME_CHECKPOINT_STRIDE == 0:
                    self._checkpoints[next_idx] = state.copy()
            self._checkpoints[target] = state.copy()
            return state.to_preamble(), state.has_g92, state.unsupported_dynamic_tlo


def _get_resume_cache(lines: LineSource) -> _ResumePreambleCache | None:
    try:
        len(lines)
        lines[0:0]
    except Exception:
        return None
    cache_key = id(lines)
    with _resume_cache_lock:
        cached = _resume_preamble_cache.get(cache_key)
        if cached is not None and cached[0] is lines:
            try:
                _resume_cache_order.remove(cache_key)
            except ValueError:
                pass
            _resume_cache_order.append(cache_key)
            return cached[1]
        cache = _ResumePreambleCache(lines)
        _resume_preamble_cache[cache_key] = (lines, cache)
        try:
            _resume_cache_order.remove(cache_key)
        except ValueError:
            pass
        _resume_cache_order.append(cache_key)
        while len(_resume_cache_order) > _RESUME_CACHE_MAX:
            old_key = _resume_cache_order.popleft()
            _resume_preamble_cache.pop(old_key, None)
        return cache


def _apply_line_to_state(state: _ResumeModalState, raw: str) -> None:
    s = clean_gcode_line(raw)
    if not s:
        return
    s = s.upper()

    def is_code(code: float, target: float) -> bool:
        return abs(code - target) < 1e-3

    words = WORD_PAT.findall(s)
    z_value: float | None = None
    for w, val in words:
        if w != "Z":
            continue
        try:
            z_value = float(val)
        except Exception as exc:
            _log_suppressed("Failed parsing G43.1 Z value while building resume preamble", exc)

    for w, val in words:
        if w == "G":
            try:
                code = float(val)
            except Exception:
                continue
            if (
                is_code(code, 92)
                or is_code(code, 92.1)
                or is_code(code, 92.2)
                or is_code(code, 92.3)
            ):
                state.has_g92 = True
                continue
            gstr = f"G{val}"
            if is_code(code, 20) or is_code(code, 21):
                state.units = gstr
            elif is_code(code, 90) or is_code(code, 91):
                state.distance = gstr
            elif is_code(code, 17) or is_code(code, 18) or is_code(code, 19):
                state.plane = gstr
            elif is_code(code, 93) or is_code(code, 94):
                state.feed_mode = gstr
            elif is_code(code, 90.1) or is_code(code, 91.1):
                state.arc_mode = gstr
            elif (
                is_code(code, 54)
                or is_code(code, 55)
                or is_code(code, 56)
                or is_code(code, 57)
                or is_code(code, 58)
                or is_code(code, 59)
                or is_code(code, 59.1)
                or is_code(code, 59.2)
                or is_code(code, 59.3)
            ):
                state.coord = gstr
            elif is_code(code, 43.1):
                if z_value is None:
                    state.dynamic_tlo_z = None
                    state.unsupported_dynamic_tlo = True
                else:
                    state.dynamic_tlo_z = z_value
                    state.unsupported_dynamic_tlo = False
            elif is_code(code, 49):
                state.dynamic_tlo_z = None
                state.unsupported_dynamic_tlo = False
        elif w == "M":
            try:
                code = int(float(val))
            except Exception:
                continue
            if code in (3, 4, 5):
                state.spindle = code
            elif code in (7, 8, 9):
                state.coolant = code
        elif w == "F":
            try:
                state.feed = float(val)
            except Exception as exc:
                _log_suppressed("Failed parsing feed value while building resume preamble", exc)
        elif w == "S":
            try:
                state.spindle_speed = float(val)
            except Exception as exc:
                _log_suppressed(
                    "Failed parsing spindle-speed value while building resume preamble",
                    exc,
                )


def _build_resume_preamble_fallback(lines: LineSource, stop_index: int) -> tuple[list[str], bool]:
    state = _ResumeModalState()
    max_index = max(0, int(stop_index))
    for idx, raw in enumerate(lines):
        if idx >= max_index:
            break
        try:
            _apply_line_to_state(state, raw)
        except Exception:
            continue
    return state.to_preamble(), state.has_g92


def _build_resume_preamble_details_fallback(lines: LineSource, stop_index: int) -> tuple[list[str], bool, bool]:
    state = _ResumeModalState()
    max_index = max(0, int(stop_index))
    for idx, raw in enumerate(lines):
        if idx >= max_index:
            break
        try:
            _apply_line_to_state(state, raw)
        except Exception:
            continue
    return state.to_preamble(), state.has_g92, state.unsupported_dynamic_tlo


def build_resume_preamble(lines: LineSource, stop_index: int) -> tuple[list[str], bool]:
    """Build a best-effort modal restore preamble for resume-from-line."""

    cache = _get_resume_cache(lines)
    if cache is not None:
        try:
            return cache.get(stop_index)
        except Exception as exc:
            _log_suppressed("Failed using resume preamble cache; falling back", exc)
    return _build_resume_preamble_fallback(lines, stop_index)


def build_resume_preamble_details(lines: LineSource, stop_index: int) -> tuple[list[str], bool, bool]:
    """Build modal restore preamble plus warning flags for resume-from-line."""

    cache = _get_resume_cache(lines)
    if cache is not None:
        try:
            return cache.get_details(stop_index)
        except Exception as exc:
            _log_suppressed("Failed using detailed resume preamble cache; falling back", exc)
    return _build_resume_preamble_details_fallback(lines, stop_index)


def _report_resume_failure(app, message: str) -> None:
    try:
        app.status.config(text=f"Resume failed: {message}")
    except Exception as exc:
        _log_suppressed("Failed updating status text for Resume From failure", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("log", f"[resume] {message}"))
        except Exception as exc:
            _log_suppressed("Failed queueing Resume From failure message", exc)
    messagebox.showwarning("Resume failed", message)


def _report_resume_message(app, message: str) -> None:
    try:
        app.status.config(text=message)
    except Exception as exc:
        _log_suppressed("Failed updating status text for Resume From message", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is None:
        return
    try:
        ui_q.put(("log", f"[resume] {message}"))
    except Exception as exc:
        _log_suppressed("Failed queueing Resume From message", exc)


def _is_dry_run_enabled(app) -> bool:
    dry_run_var = getattr(app, "dry_run_sanitize_stream", None)
    getter = getattr(dry_run_var, "get", None)
    if callable(getter):
        return bool(getter())
    return bool(dry_run_var)


def _set_dry_run_enabled(app, enabled: bool) -> None:
    dry_run_enabled = bool(enabled)
    dry_run_var = getattr(app, "dry_run_sanitize_stream", None)
    setter = getattr(dry_run_var, "set", None)
    if callable(setter):
        setter(dry_run_enabled)
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        settings["dry_run_sanitize_stream"] = dry_run_enabled
    grbl = getattr(app, "grbl", None)
    runtime_setter = getattr(grbl, "set_dry_run_sanitize", None)
    if callable(runtime_setter):
        runtime_setter(dry_run_enabled)


def _confirm_dry_run_resume_start(app) -> DryRunStartDecision:
    if getattr(app, "tk", None) is None:
        return DryRunStartDecision.CONTINUE_DRY_RUN
    decision = confirm_dry_run_start_mode(
        app,
        normal_run_action="Resume",
        messagebox_module=messagebox,
    )
    if isinstance(decision, DryRunStartDecision):
        return decision
    return DryRunStartDecision.CANCEL


def _confirm_resume_setup_state(app) -> bool:
    try:
        setup_valid = bool(has_valid_job_setup_state(app))
    except Exception as exc:
        _log_suppressed("Failed checking Job Setup validity before Resume From", exc)
        _report_resume_failure(app, "Job Setup state could not be validated before Resume From.")
        return False
    if setup_valid:
        return True
    try:
        allowed = bool(confirm_job_start_without_setup(app))
    except Exception as exc:
        _log_suppressed("Failed confirming Job Setup warning before Resume From", exc)
        _report_resume_failure(app, "Job Setup confirmation could not be completed before Resume From.")
        return False
    if allowed:
        return True
    _report_resume_message(app, "Resume canceled because Job Setup is not current.")
    return False


def _confirm_resume_g92_state(app, *, has_g92: bool) -> bool:
    if not bool(has_g92):
        return True
    try:
        allowed = bool(messagebox.askyesno(_RESUME_G92_WARNING_TITLE, _RESUME_G92_WARNING_BODY))
    except Exception as exc:
        _log_suppressed("Failed confirming G92 warning before Resume From", exc)
        _report_resume_failure(app, "G92 confirmation could not be completed before Resume From.")
        return False
    if allowed:
        return True
    _report_resume_message(
        app,
        "Resume canceled because G92 offsets were detected before the selected line.",
    )
    return False


def _confirm_resume_dynamic_tlo_state(app, *, unsupported_dynamic_tlo: bool) -> bool:
    if not bool(unsupported_dynamic_tlo):
        return True
    try:
        messagebox.showwarning(_RESUME_UNSUPPORTED_TLO_TITLE, _RESUME_UNSUPPORTED_TLO_BODY)
    except Exception as exc:
        _log_suppressed("Failed showing unsupported G43.1 warning before Resume From", exc)
    _report_resume_message(
        app,
        "Resume canceled because G43.1 tool length offset state could not be reconstructed.",
    )
    return False


def resume_from_line(
    app,
    start_index: int,
    preamble: list[str],
    *,
    has_g92: bool = False,
    unsupported_dynamic_tlo: bool = False,
):
    """Restart streaming from a specific line after UI-side safety checks."""

    if app.grbl.is_streaming() or bool(getattr(app, "_stream_done_pending_idle", False)):
        messagebox.showwarning(
            DialogTitles.BUSY,
            BusyMessages.STOP_STREAM_BEFORE_RESUMING,
        )
        return
    if not app._require_grbl_connection():
        return
    if not app._grbl_ready:
        messagebox.showwarning("Not ready", "Wait for GRBL to be ready.")
        return
    if app._alarm_locked:
        messagebox.showwarning("Alarm", "Clear the alarm before resuming.")
        return
    total_lines = (
        app._gcode_total_lines
        if getattr(app, "_gcode_streaming_mode", False)
        else len(app._last_gcode_lines)
    )
    if total_lines <= 0:
        messagebox.showwarning("No G-code", "Load a G-code file first.")
        return
    if start_index < 0 or start_index >= total_lines:
        messagebox.showwarning("Resume", "Line number is out of range.")
        return
    if not run_preflight_gate(app, action_label="Resume", messagebox_module=messagebox):
        return
    if not _confirm_resume_setup_state(app):
        return
    if not run_preflight_gate(app, action_label="Resume", messagebox_module=messagebox):
        return
    if not _confirm_resume_dynamic_tlo_state(
        app,
        unsupported_dynamic_tlo=unsupported_dynamic_tlo,
    ):
        return
    if not _confirm_resume_g92_state(app, has_g92=has_g92):
        return
    if _is_dry_run_enabled(app):
        try:
            decision = _confirm_dry_run_resume_start(app)
        except Exception as exc:
            _log_suppressed("Failed confirming Dry Run mode before Resume From", exc)
            _report_resume_message(app, "Resume canceled before streaming start.")
            return
        if decision is DryRunStartDecision.CANCEL:
            _report_resume_message(app, "Resume canceled before streaming start.")
            return
        if decision is DryRunStartDecision.SWITCH_TO_NORMAL_RUN:
            try:
                _set_dry_run_enabled(app, False)
            except Exception as exc:
                _log_suppressed("Failed switching Dry Run off before Resume From", exc)
                _report_resume_failure(app, "Dry Run could not be switched off before Resume From.")
                return
    prior_kasa_stream_line_index = getattr(app, "_kasa_last_stream_line_index", None)
    try:
        app._kasa_last_stream_line_index = int(start_index) - 1
    except (AttributeError, TypeError, ValueError) as exc:
        _log_suppressed("Failed setting Kasa stream-line index before resume-from", exc)
    accessory_router = getattr(app, "accessory_router", None)
    if accessory_router is not None:
        reset_debounce = getattr(accessory_router, "reset_debounce", None)
        if callable(reset_debounce):
            try:
                reset_debounce()
            except Exception as exc:
                _log_suppressed("Failed resetting Kasa debounce state before resume-from", exc)
    app.grbl.set_dry_run_sanitize(bool(app.dry_run_sanitize_stream.get()))
    app.grbl.start_stream_from(start_index, preamble)
    started = False
    try:
        started = bool(app.grbl.is_streaming())
    except Exception as exc:
        _log_suppressed("Failed checking GRBL streaming state after Resume From", exc)
    if not started:
        try:
            app._kasa_last_stream_line_index = prior_kasa_stream_line_index
        except Exception as exc:
            _log_suppressed("Failed restoring Kasa stream-line index after Resume From failure", exc)
        _report_resume_failure(
            app,
            "GRBL did not enter streaming state after Resume From.",
        )
        return
    app._clear_pending_ui_updates()
    app._last_sent_index = start_index - 1
    app._last_acked_index = start_index - 1
    app._last_error_index = -1
    if total_lines > 0:
        pct = int(round((start_index / total_lines) * 100))
        app.progress_pct.set(pct)
    app.status.config(text=f"Resuming at line {start_index + 1}")
    streaming_controller = getattr(app, "streaming_controller", None)
    if streaming_controller is not None:
        log_job_started = getattr(streaming_controller, "log_job_started", None)
        if callable(log_job_started):
            try:
                log_job_started(run_type="resume", start_index=int(start_index))
            except Exception as exc:
                _log_suppressed("Failed logging Resume From lifecycle entry", exc)
    try:
        if hasattr(app, "_start_job_accessories"):
            app._start_job_accessories("job_resume")
    except Exception as exc:
        _log_suppressed("Failed starting Kasa job accessories on Resume From", exc)
