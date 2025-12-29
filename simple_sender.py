# Simple-Sender.py
# Minimal GRBL 1.1h sender
# Python 3 + Tkinter + pyserial
#
# Features:
# - Connect/disconnect, open gcode, run/pause/resume/stop (Ctrl-X), unlock, home, spindle toggle
# - DRO (WPos) display, zero buttons (G92 approach by default), goto zero
# - Jog pad using $J= incremental jogging
# - Gcode viewer highlights: current / sent / acked
# - Console log + manual command entry
#
# Safety note: This application is in the Alpha stage; test in air, spindle off.

import os
import time
import threading
import queue
import json
import re
import types
import csv
import math
import shlex
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

import serial
from serial.tools import list_ports


BAUD_DEFAULT = 115200


# ---------- GRBL real-time command bytes (GRBL 1.1) ----------
RT_RESET = b"\x18"        # Ctrl-X soft reset
RT_STATUS = b"?"          # status report query
RT_HOLD = b"!"            # feed hold
RT_RESUME = b"~"          # cycle start / resume

# Feed override
RT_FO_RESET = b"\x90"
RT_FO_PLUS_10 = b"\x91"
RT_FO_MINUS_10 = b"\x92"

# Spindle override
RT_SO_RESET = b"\x99"
RT_SO_PLUS_10 = b"\x9A"
RT_SO_MINUS_10 = b"\x9B"

# Jog cancel
RT_JOG_CANCEL = b"\x85"

# Streaming buffer window (character counting)
RX_BUFFER_SIZE = 128
RX_BUFFER_SAFETY = 8
RX_BUFFER_WINDOW = RX_BUFFER_SIZE - RX_BUFFER_SAFETY

MACRO_PREFIXES = ("Macro-", "Maccro-")
MACRO_EXTS = ("", ".txt")
MACRO_GPAT = re.compile(r"[A-Za-z]\s*[-+]?\d+.*")
MACRO_AUXPAT = re.compile(r"^(%[A-Za-z0-9]+)\\b *(.*)$")
MACRO_CMDPAT = re.compile(r"([A-Za-z]+)")
MACRO_STDEXPR = False
PAREN_COMMENT_PAT = re.compile(r"\(.*?\)")
WORD_PAT = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")

ALL_STOP_CHOICES = [
    ("Soft Reset (Ctrl-X)", "reset"),
    ("Stop Stream + Reset", "stop_reset"),
]
CURRENT_LINE_CHOICES = [
    ("Processing (acked)", "acked"),
    ("Sent (queued)", "sent"),
]
CLEAR_ICON = "X"

MAX_CONSOLE_LINES = 5000

GRBL_SETTING_DESC = {
    0: "Step pulse, microseconds",
    1: "Step idle delay, milliseconds",
    2: "Step port invert mask",
    3: "Direction port invert mask",
    4: "Step enable invert",
    5: "Limit pins invert",
    6: "Probe pin invert",
    10: "Status report mask",
    11: "Junction deviation, mm",
    12: "Arc tolerance, mm",
    13: "Report inches",
    20: "Soft limits enable",
    21: "Hard limits enable",
    22: "Homing cycle enable",
    23: "Homing direction invert mask",
    24: "Homing locate feed rate, mm/min",
    25: "Homing search seek rate, mm/min",
    26: "Homing switch debounce, ms",
    27: "Homing switch pull-off, mm",
    30: "Max spindle speed, RPM",
    31: "Min spindle speed, RPM",
    32: "Laser mode enable",
    100: "X steps/mm",
    101: "Y steps/mm",
    102: "Z steps/mm",
    110: "X max rate, mm/min",
    111: "Y max rate, mm/min",
    112: "Z max rate, mm/min",
    120: "X accel, mm/sec^2",
    121: "Y accel, mm/sec^2",
    122: "Z accel, mm/sec^2",
    130: "X max travel, mm",
    131: "Y max travel, mm",
    132: "Z max travel, mm",
}

GRBL_SETTING_KEYS = sorted(GRBL_SETTING_DESC.keys())

# Conservative numeric limits for basic validation. Values are broad to avoid blocking legitimate configs.
GRBL_SETTING_LIMITS = {
    0: (1, 1000),    # step pulse us
    1: (0, 255),
    2: (0, 255),
    3: (0, 255),
    4: (0, 1),
    5: (0, 1),
    6: (0, 1),
    10: (0, 511),
    11: (0, 5),
    12: (0, 5),
    13: (0, 1),
    20: (0, 1),
    21: (0, 1),
    22: (0, 1),
    23: (0, 255),
    24: (0, 5000),
    25: (0, 5000),
    26: (0, 255),
    27: (0, 50),
    30: (0, 100000),
    31: (0, 100000),
    32: (0, 1),
    100: (0, 2000),
    101: (0, 2000),
    102: (0, 2000),
    110: (0, 200000),
    111: (0, 200000),
    112: (0, 200000),
    120: (0, 20000),
    121: (0, 20000),
    122: (0, 20000),
    130: (0, 2000),
    131: (0, 2000),
    132: (0, 2000),
}

# Settings that are allowed to be non-numeric (currently none; placeholder for future extensions)
GRBL_NON_NUMERIC_SETTINGS: set[int] = set()

def clean_gcode_line(line: str) -> str:
    """Strip comments and whitespace; keep simple + safe."""
    # Remove parenthesis and semicolon comments
    line = line.replace("\ufeff", "")
    line = PAREN_COMMENT_PAT.sub("", line)
    if ";" in line:
        line = line.split(";", 1)[0]
    line = line.strip()
    if line.startswith("%"):
        return ""
    if not line:
        return ""
    return line


def _arc_sweep(u0: float, v0: float, u1: float, v1: float, cu: float, cv: float, cw: bool) -> float:
    start_ang = math.atan2(v0 - cv, u0 - cu)
    end_ang = math.atan2(v1 - cv, u1 - cu)
    if cw:
        sweep = (start_ang - end_ang) % (2 * math.pi)
    else:
        sweep = (end_ang - start_ang) % (2 * math.pi)
    return sweep


def _arc_center_from_radius(
    u0: float, v0: float, u1: float, v1: float, r: float, cw: bool
) -> tuple[float, float, float] | None:
    if r == 0:
        return None
    r_abs = abs(r)
    dx = u1 - u0
    dy = v1 - v0
    d = math.hypot(dx, dy)
    if d == 0 or d > 2 * r_abs:
        return None
    um = (u0 + u1) / 2.0
    vm = (v0 + v1) / 2.0
    h = math.sqrt(max(r_abs * r_abs - (d / 2) * (d / 2), 0.0))
    ux = -dy / d
    uy = dx / d
    c1 = (um + ux * h, vm + uy * h)
    c2 = (um - ux * h, vm - uy * h)
    sweep1 = _arc_sweep(u0, v0, u1, v1, c1[0], c1[1], cw)
    sweep2 = _arc_sweep(u0, v0, u1, v1, c2[0], c2[1], cw)
    if r > 0:
        if sweep1 <= sweep2:
            return c1[0], c1[1], sweep1
        return c2[0], c2[1], sweep2
    if sweep1 >= sweep2:
        return c1[0], c1[1], sweep1
    return c2[0], c2[1], sweep2


def compute_gcode_stats(
    lines: list[str],
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    x = y = z = 0.0
    units = 1.0
    absolute = True
    plane = "G17"
    feed_mode = "G94"
    arc_abs = False
    feed = None
    g92_offset = [0.0, 0.0, 0.0]
    g92_enabled = True
    total_time_min = 0.0
    has_time = False
    total_rapid_min = 0.0
    has_rapid = False
    minx = maxx = None
    miny = maxy = None
    minz = maxz = None
    last_motion = 1

    def update_bounds(nx, ny, nz):
        nonlocal minx, maxx, miny, maxy, minz, maxz
        if minx is None:
            minx = maxx = nx
            miny = maxy = ny
            minz = maxz = nz
            return
        minx = min(minx, nx)
        maxx = max(maxx, nx)
        miny = min(miny, ny)
        maxy = max(maxy, ny)
        minz = min(minz, nz)
        maxz = max(maxz, nz)

    last_f = None

    def axis_limits(dx, dy, dz):
        max_feed = None
        min_accel = None
        if rapid_rates:
            candidates = []
            if dx:
                candidates.append(rapid_rates[0])
            if dy:
                candidates.append(rapid_rates[1])
            if dz:
                candidates.append(rapid_rates[2])
            if candidates:
                max_feed = min(candidates)
        if accel_rates:
            candidates = []
            if dx:
                candidates.append(accel_rates[0])
            if dy:
                candidates.append(accel_rates[1])
            if dz:
                candidates.append(accel_rates[2])
            if candidates:
                min_accel = min(candidates)
        return max_feed, min_accel

    def move_duration(dist, feed_mm_min, min_accel, last_feed):
        if dist <= 0:
            return 0.0, last_feed
        if feed_mm_min is None or feed_mm_min <= 0:
            return None, last_feed
        f = feed_mm_min / 60.0
        if f <= 0:
            return None, last_feed
        accel = min_accel if (min_accel and min_accel > 0) else 0.0
        if accel <= 0:
            return dist / f, f
        if last_feed is not None and abs(f - last_feed) < 1e-6:
            return dist / f, f
        accel = accel if accel > 0 else 750.0
        half_len = dist / 2.0
        init_time = f / accel
        init_dx = 0.5 * f * init_time
        time_sec = 0.0
        if half_len >= init_dx:
            half_len -= init_dx
            time_sec += init_time
        time_sec += half_len / f
        return 2 * time_sec, f

    for raw in lines:
        s = raw.strip().upper()
        if not s:
            continue
        if "(" in s:
            s = PAREN_COMMENT_PAT.sub("", s)
        if ";" in s:
            s = s.split(";", 1)[0]
        s = s.strip()
        if not s or s.startswith("%"):
            continue
        words = WORD_PAT.findall(s)
        if not words:
            continue
        g_codes = []
        for w, val in words:
            if w == "G":
                try:
                    g_codes.append(float(val))
                except Exception:
                    pass

        def has_g(code):
            return any(abs(g - code) < 1e-3 for g in g_codes)

        if has_g(20):
            units = 25.4
        if has_g(21):
            units = 1.0
        if has_g(90):
            absolute = True
        if has_g(91):
            absolute = False
        if has_g(17):
            plane = "G17"
        if has_g(18):
            plane = "G18"
        if has_g(19):
            plane = "G19"
        if has_g(93):
            feed_mode = "G93"
        if has_g(94):
            feed_mode = "G94"
        if has_g(90.1):
            arc_abs = True
        if has_g(91.1):
            arc_abs = False

        nx, ny, nz = x, y, z
        has_axis = False
        has_x = False
        has_y = False
        has_z = False
        i_val = None
        j_val = None
        k_val = None
        r_val = None
        p_val = None
        for w, val in words:
            try:
                raw_val = float(val)
            except Exception:
                continue
            if w == "P":
                p_val = raw_val
                continue
            fval = raw_val * units
            if w == "X":
                has_axis = True
                has_x = True
                nx = fval if absolute else (nx + fval)
            elif w == "Y":
                has_axis = True
                has_y = True
                ny = fval if absolute else (ny + fval)
            elif w == "Z":
                has_axis = True
                has_z = True
                nz = fval if absolute else (nz + fval)
            elif w == "F":
                feed = raw_val if feed_mode == "G93" else fval
            elif w == "I":
                i_val = fval
            elif w == "J":
                j_val = fval
            elif w == "K":
                k_val = fval
            elif w == "R":
                r_val = fval

        if has_g(92):
            if not (has_x or has_y or has_z):
                if g92_enabled:
                    x += g92_offset[0]
                    y += g92_offset[1]
                    z += g92_offset[2]
                g92_offset = [0.0, 0.0, 0.0]
            else:
                if has_x:
                    mx = x + (g92_offset[0] if g92_enabled else 0.0)
                    g92_offset[0] = mx - nx
                    x = nx
                if has_y:
                    my = y + (g92_offset[1] if g92_enabled else 0.0)
                    g92_offset[1] = my - ny
                    y = ny
                if has_z:
                    mz = z + (g92_offset[2] if g92_enabled else 0.0)
                    g92_offset[2] = mz - nz
                    z = nz
            g92_enabled = True
            continue
        if has_g(92.1):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_offset = [0.0, 0.0, 0.0]
            g92_enabled = False
            continue
        if has_g(92.2):
            if g92_enabled:
                x += g92_offset[0]
                y += g92_offset[1]
                z += g92_offset[2]
            g92_enabled = False
            continue
        if has_g(92.3):
            if not g92_enabled:
                x -= g92_offset[0]
                y -= g92_offset[1]
                z -= g92_offset[2]
            g92_enabled = True
            continue
        if has_g(4):
            if p_val and p_val > 0:
                total_time_min += p_val / 60.0
                has_time = True
            continue

        motion = None
        for g in g_codes:
            if abs(g - 0) < 1e-3:
                motion = 0
            elif abs(g - 1) < 1e-3:
                motion = 1
            elif abs(g - 2) < 1e-3:
                motion = 2
            elif abs(g - 3) < 1e-3:
                motion = 3
        if motion is None and has_axis:
            motion = last_motion

        if motion in (0, 1) and has_axis:
            update_bounds(x, y, z)
            dx = nx - x
            dy = ny - y
            dz = nz - z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if motion == 1 and feed and feed > 0:
                if feed_mode == "G93":
                    total_time_min += 1.0 / feed
                else:
                    max_feed, min_accel = axis_limits(dx, dy, dz)
                    use_feed = feed
                    if max_feed and use_feed > max_feed:
                        use_feed = max_feed
                    t_sec, last_f = move_duration(dist, use_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_time_min += t_sec / 60.0
                has_time = True
            if motion == 0 and rapid_rates:
                max_feed, min_accel = axis_limits(dx, dy, dz)
                if max_feed:
                    t_sec, last_f = move_duration(dist, max_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_rapid_min += t_sec / 60.0
                        has_rapid = True
            x, y, z = nx, ny, nz
            update_bounds(x, y, z)
            last_motion = motion
            continue

        if motion in (2, 3) and has_axis:
            update_bounds(x, y, z)
            cw = motion == 2
            if plane == "G17":
                u0, v0, u1, v1 = x, y, nx, ny
                w0, w1 = z, nz
                off1, off2 = i_val, j_val
            elif plane == "G18":
                u0, v0, u1, v1 = x, z, nx, nz
                w0, w1 = y, ny
                off1, off2 = i_val, k_val
            else:
                u0, v0, u1, v1 = y, z, ny, nz
                w0, w1 = x, nx
                off1, off2 = j_val, k_val

            arc_len = math.hypot(u1 - u0, v1 - v0)
            full_circle = abs(u1 - u0) < 1e-6 and abs(v1 - v0) < 1e-6
            if r_val is not None:
                if full_circle:
                    r = abs(r_val)
                    arc_len = 2 * math.pi * r if r > 0 else 0.0
                else:
                    res = _arc_center_from_radius(u0, v0, u1, v1, r_val, cw)
                    if res:
                        cu, cv, sweep = res
                        r = math.hypot(u0 - cu, v0 - cv)
                        arc_len = abs(sweep) * r
            else:
                if off1 is None:
                    off1 = u0 if arc_abs else 0.0
                if off2 is None:
                    off2 = v0 if arc_abs else 0.0
                cu = off1 if arc_abs else (u0 + off1)
                cv = off2 if arc_abs else (v0 + off2)
                r = math.hypot(u0 - cu, v0 - cv)
                if r > 0:
                    if full_circle:
                        arc_len = 2 * math.pi * r
                    else:
                        sweep = _arc_sweep(u0, v0, u1, v1, cu, cv, cw)
                        arc_len = abs(sweep) * r

            dist = math.hypot(arc_len, w1 - w0)
            if feed and feed > 0:
                if feed_mode == "G93":
                    total_time_min += 1.0 / feed
                else:
                    dx = nx - x
                    dy = ny - y
                    dz = nz - z
                    max_feed, min_accel = axis_limits(dx, dy, dz)
                    use_feed = feed
                    if max_feed and use_feed > max_feed:
                        use_feed = max_feed
                    t_sec, last_f = move_duration(dist, use_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_time_min += t_sec / 60.0
                has_time = True
            x, y, z = nx, ny, nz
            update_bounds(x, y, z)
            last_motion = motion
            continue

    if minx is None:
        bounds = None
    else:
        bounds = (minx, maxx, miny, maxy, minz, maxz)

    return {
        "bounds": bounds,
        "time_min": total_time_min if has_time else None,
        "rapid_min": total_rapid_min if has_rapid else None,
    }


class GrblWorker:
    """
    Serial worker:
    - reads lines from GRBL
    - handles streaming: send next line only after ok/error
    - periodically queries status (?)
    """

    def __init__(self, ui_event_q: queue.Queue):
        self.ui_q = ui_event_q
        self.ser: serial.Serial | None = None

        self._rx_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_buffer_emit = None
        self._last_buffer_emit_ts = 0.0
        self._buffer_emit_interval = 0.1

        # streaming state
        self._gcode: list[str] = []
        self._streaming = False
        self._paused = False
        self._send_index = 0   # next index to send
        self._ack_index = -1   # last acked index
        self._stream_buf_used = 0
        self._stream_line_queue: deque[int] = deque()
        self._stream_pending_line = ""
        self._rx_window = RX_BUFFER_WINDOW

        self._outgoing_q: queue.Queue[str] = queue.Queue()
        self._stream_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._ready = False
        self._alarm_active = False

    # ---- connection ----
    def list_ports(self) -> list[str]:
        return [p.device for p in list_ports.comports()]

    def connect(self, port: str, baud: int = BAUD_DEFAULT):
        self.disconnect()
        self._stop_evt = threading.Event()
        stop_evt = self._stop_evt
        self._ready = False
        self._alarm_active = False

        self.ser = serial.Serial(port, baudrate=baud, timeout=0.1, write_timeout=0.5)
        # Give GRBL a moment; some setups reset on connect
        time.sleep(0.25)
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

        self._rx_thread = threading.Thread(target=self._rx_loop, args=(stop_evt,), daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, args=(stop_evt,), daemon=True)
        self._status_thread = threading.Thread(target=self._status_loop, args=(stop_evt,), daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()
        self._status_thread.start()

        self.ui_q.put(("conn", True, port))

    def disconnect(self):
        self._stop_evt.set()
        self._streaming = False
        self._paused = False
        self._gcode = []
        self._send_index = 0
        self._ack_index = -1
        self._reset_stream_buffer()
        self._last_buffer_emit = None
        self._last_buffer_emit_ts = 0.0
        self._clear_outgoing()
        self._emit_buffer_fill()
        self._ready = False
        self._alarm_active = False
        self.ui_q.put(("ready", False))
        self.ui_q.put(("stream_state", "stopped", None))

        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        for t in (self._rx_thread, self._tx_thread, self._status_thread):
            if t and t.is_alive():
                t.join(timeout=0.5)
        self._rx_thread = None
        self._tx_thread = None
        self._status_thread = None
        self.ui_q.put(("conn", False, None))

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def is_streaming(self) -> bool:
        return self._streaming

    def _mark_ready(self):
        if not self._ready:
            self._ready = True
            self.ui_q.put(("ready", True))

    def _handle_alarm(self, s: str):
        if not self._alarm_active:
            self._alarm_active = True
        if self._streaming or self._paused:
            self._streaming = False
            self._paused = False
            self._reset_stream_buffer()
            self._emit_buffer_fill()
            self.ui_q.put(("stream_state", "alarm", s))
        else:
            self.ui_q.put(("stream_state", "alarm", s))
        self._clear_outgoing()
        self.ui_q.put(("alarm", s))

    # ---- commands ----
    def send_immediate(self, s: str):
        """Manual send (console)."""
        if not self.is_connected():
            return
        if self._streaming:
            return
        if self._alarm_active:
            s_check = s.strip().upper()
            if not (s_check.startswith("$X") or s_check.startswith("$H")):
                return
        s = s.strip()
        if not s:
            return
        self._outgoing_q.put(s)

    def send_realtime(self, b: bytes):
        """Real-time byte commands (no newline)."""
        if not self.is_connected():
            return
        try:
            with self._write_lock:
                self.ser.write(b)
        except Exception as e:
            self.ui_q.put(("log", f"[write err] {e}"))

    def unlock(self):
        self.send_immediate("$X")

    def home(self):
        self.send_immediate("$H")

    def reset(self, emit_state: bool = True):
        self.send_realtime(RT_RESET)
        self._ready = False
        self._alarm_active = False
        was_streaming = self._streaming or self._paused
        self._streaming = False
        self._paused = False
        self._reset_stream_buffer()
        self._clear_outgoing()
        self.ui_q.put(("ready", False))
        if emit_state and was_streaming:
            self.ui_q.put(("stream_state", "stopped", None))

    def hold(self):
        self.send_realtime(RT_HOLD)

    def resume(self):
        self.send_realtime(RT_RESUME)

    def spindle_on(self, rpm: int = 12000):
        # Note: Many spindles ignore S unless PWM configured; safe default is still useful.
        self.send_immediate(f"M3 S{int(rpm)}")

    def spindle_off(self):
        self.send_immediate("M5")

    def jog_cancel(self):
        self.send_realtime(RT_JOG_CANCEL)

    # ---- streaming ----
    def load_gcode(self, lines: list[str]):
        self._gcode = lines
        self._streaming = False
        self._paused = False
        self._send_index = 0
        self._ack_index = -1
        self._reset_stream_buffer()
        self.ui_q.put(("stream_state", "loaded", len(lines)))

    def _clear_outgoing(self):
        try:
            while True:
                self._outgoing_q.get_nowait()
        except queue.Empty:
            pass
        self._emit_buffer_fill()

    def _reset_stream_buffer(self):
        with self._stream_lock:
            self._stream_buf_used = 0
            self._stream_line_queue.clear()
            self._stream_pending_line = ""
            self._rx_window = RX_BUFFER_WINDOW
            self._send_index = 0
            self._ack_index = -1

    def start_stream(self):
        if not self.is_connected():
            return
        if not self._gcode:
            return
        self._clear_outgoing()
        self._streaming = True
        self._paused = False
        self._reset_stream_buffer()
        self._emit_buffer_fill()
        self.ui_q.put(("stream_state", "running", None))

    def pause_stream(self):
        if self._streaming:
            self._paused = True
            self.hold()
            self.ui_q.put(("stream_state", "paused", None))

    def resume_stream(self):
        if self._streaming:
            self._paused = False
            self.resume()
            self.ui_q.put(("stream_state", "running", None))

    def stop_stream(self):
        self._streaming = False
        self._paused = False
        self.reset(emit_state=False)
        self._reset_stream_buffer()
        self._emit_buffer_fill()
        self.ui_q.put(("stream_state", "stopped", None))

    # ---- jog ----
    def jog(self, dx: float, dy: float, dz: float, feed: float, unit_mode: str):
        """
        GRBL jog: $J=G91 ... incremental
        unit_mode: "mm" or "inch" (uses G21/G20)
        """
        if not self.is_connected():
            return
        gunit = "G21" if unit_mode == "mm" else "G20"
        # Jog cancels can be sent with 0x85, but we keep it simple here.
        cmd = f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"
        self.send_immediate(cmd)

    # ---- RX/TX loops ----
    def _tx_loop(self, stop_evt: threading.Event):
        while not stop_evt.is_set():
            if not self.is_connected():
                time.sleep(0.05)
                continue

            # Streaming: fill the controller RX buffer window (character-counting)
            if self._streaming and (not self._paused):
                while self._send_index < len(self._gcode):
                    if not self._streaming or self._paused:
                        break
                    with self._stream_lock:
                        if not self._streaming or self._paused:
                            break
                        if self._stream_pending_line:
                            line = self._stream_pending_line
                        else:
                            if self._send_index >= len(self._gcode):
                                break
                            line = self._gcode[self._send_index].strip()
                        line_len = len(line) + 1  # newline consumes RX buffer space
                        can_fit = (self._stream_buf_used + line_len) < self._rx_window

                        if not can_fit and self._stream_buf_used > 0:
                            self._stream_pending_line = line
                            break

                        if not can_fit and self._stream_buf_used == 0:
                            # Allow a single oversized line to prevent deadlock
                            pass

                        self._stream_pending_line = ""
                        idx = self._send_index
                        self._send_index += 1
                        self._stream_buf_used += line_len
                        self._stream_line_queue.append(line_len)

                    if not self._write_line(line):
                        with self._stream_lock:
                            if self._send_index > 0:
                                self._send_index -= 1
                            if self._stream_line_queue:
                                try:
                                    last = self._stream_line_queue.pop()
                                except Exception:
                                    last = line_len
                                self._stream_buf_used = max(0, self._stream_buf_used - last)
                            else:
                                self._stream_buf_used = max(0, self._stream_buf_used - line_len)
                            self._stream_pending_line = line
                        self._emit_buffer_fill()
                        self._streaming = False
                        self._paused = False
                        self.ui_q.put(("stream_state", "error", "Write failed"))
                        break

                    self._emit_buffer_fill()
                    self.ui_q.put(("gcode_sent", idx, line))

                with self._stream_lock:
                    send_index = self._send_index
                    ack_index = self._ack_index
                if self._streaming and send_index >= len(self._gcode) and ack_index >= len(self._gcode) - 1:
                    # done
                    self._streaming = False
                    self.ui_q.put(("stream_state", "done", None))

            # Manual outgoing queue (console / buttons)
            try:
                s = self._outgoing_q.get_nowait()
            except queue.Empty:
                time.sleep(0.01)
                continue
            if self._alarm_active:
                s_check = s.strip().upper()
                if not (s_check.startswith("$X") or s_check.startswith("$H")):
                    self._clear_outgoing()
                    time.sleep(0.01)
                    continue
            self._write_line(s)
            self.ui_q.put(("log_tx", s))

    def _write_line(self, s: str) -> bool:
        if not self.is_connected():
            return False
        try:
            payload = (s.strip() + "\n").encode("utf-8", errors="replace")
            with self._write_lock:
                self.ser.write(payload)
            return True
        except Exception as e:
            self.ui_q.put(("log", f"[write err] {e}"))
            return False

    def _rx_loop(self, stop_evt: threading.Event):
        buf = b""
        while not stop_evt.is_set():
            if not self.is_connected():
                time.sleep(0.05)
                continue
            try:
                chunk = self.ser.read(256)
            except Exception as e:
                self.ui_q.put(("log", f"[read err] {e}"))
                time.sleep(0.1)
                continue

            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", errors="replace").strip()
                if not s:
                    continue
                self._handle_rx_line(s)

    def _handle_rx_line(self, s: str):
        is_status = s.startswith("<") and s.endswith(">")
        state = ""
        if is_status:
            parts = s.strip("<>").split("|")
            state = parts[0] if parts else ""
        # Suppress repetitive idle status noise in the console log.
        if not (is_status and state.lower().startswith("idle")):
            self.ui_q.put(("log_rx", s))

        low = s.lower()
        if low.startswith("grbl"):
            self._mark_ready()
        if low.startswith("alarm:"):
            self._handle_alarm(s)
            return
        if "[msg:" in low and "reset to continue" in low:
            self._handle_alarm(s)
            return

        if low == "ok" or low.startswith("error"):
            with self._stream_lock:
                if self._stream_line_queue:
                    self._stream_buf_used -= self._stream_line_queue.popleft()
                    if self._stream_buf_used < 0:
                        self._stream_buf_used = 0
            self._emit_buffer_fill()
            # streaming ack progression
            if self._streaming:
                with self._stream_lock:
                    self._ack_index += 1
                    ack_index = self._ack_index
                self.ui_q.put(("gcode_acked", ack_index))
                self.ui_q.put(("progress", ack_index + 1, len(self._gcode)))

            if low.startswith("error"):
                # Stop streaming on error for safety
                self._streaming = False
                self._paused = False
                self._reset_stream_buffer()
                self._emit_buffer_fill()
                self.ui_q.put(("stream_state", "error", s))

        # Status report looks like: <Idle|WPos:0.000,0.000,0.000|FS:0,0>
        if s.startswith("<") and s.endswith(">"):
            # Example: <Idle|WPos:0.000,0.000,0.000|Bf:15,128|FS:0,0>
            self._mark_ready()
            parts = s.strip("<>").split("|")
            state = parts[0] if parts else ""
            if state.lower().startswith("alarm"):
                if not self._alarm_active:
                    self._handle_alarm(state)
            elif self._alarm_active:
                self._alarm_active = False
            for p in parts:
                if p.startswith("Bf:"):
                    try:
                        _, rx_free = p[3:].split(",", 1)
                        rx_free = int(rx_free.strip())
                        with self._stream_lock:
                            window = max(1, rx_free - RX_BUFFER_SAFETY)
                            if window < self._stream_buf_used:
                                window = self._stream_buf_used
                            self._rx_window = window
                        self._emit_buffer_fill()
                    except Exception:
                        pass
            self.ui_q.put(("status", s))

    def _status_loop(self, stop_evt: threading.Event):
        # periodic status query; keeps DRO updated
        while not stop_evt.is_set():
            if self.is_connected():
                try:
                    self.send_realtime(RT_STATUS)
                except Exception:
                    pass
            time.sleep(0.2)

    def _emit_buffer_fill(self):
        with self._stream_lock:
            window = max(1, int(self._rx_window))
            used = max(0, int(self._stream_buf_used))
        if used > window:
            used = window
        pct = int(round((used / window) * 100))
        payload = (pct, used, window)
        now = time.time()
        if payload == self._last_buffer_emit and (now - self._last_buffer_emit_ts) < self._buffer_emit_interval:
            return
        self._last_buffer_emit = payload
        self._last_buffer_emit_ts = now
        self.ui_q.put(("buffer_fill", pct, used, window))


class GcodeText(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.text = tk.Text(self, wrap="none", height=18, undo=False)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Tags
        # Lighter backgrounds for readability
        self.text.tag_configure("sent", background="#c7d7f2")     # light blue
        self.text.tag_configure("acked", background="#c8e6c9")    # light green
        self.text.tag_configure("current", background="#ffe0b2")  # light orange

        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self._insert_after_id = None
        self._insert_lines = []
        self._insert_index = 0
        self._insert_chunk_size = 200
        self._insert_done_cb = None
        self._insert_progress_cb = None

    def set_lines(self, lines: list[str]):
        self._cancel_chunk_insert()
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for i, ln in enumerate(lines, start=1):
            self.text.insert("end", f"{i:5d}  {ln}\n")
        self.text.config(state="disabled")
        self.lines_count = len(lines)
        self._sent_upto = -1
        self._acked_upto = -1

        self.clear_highlights()
        if self.lines_count:
            self.highlight_current(0)

    def set_lines_chunked(
        self,
        lines: list[str],
        chunk_size: int = 200,
        on_done=None,
        on_progress=None,
    ):
        self._cancel_chunk_insert()
        self.lines_count = len(lines)
        self._insert_lines = lines
        self._insert_index = 0
        self._insert_chunk_size = max(50, int(chunk_size))
        self._insert_done_cb = on_done
        self._insert_progress_cb = on_progress
        self._sent_upto = -1
        self._acked_upto = -1
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if callable(self._insert_progress_cb):
            self._insert_progress_cb(0, self.lines_count)
        self._insert_next_chunk()

    def _cancel_chunk_insert(self):
        if self._insert_after_id is not None:
            try:
                self.after_cancel(self._insert_after_id)
            except Exception:
                pass
        self._insert_after_id = None
        self._insert_lines = []
        self._insert_index = 0
        self._insert_done_cb = None
        self._insert_progress_cb = None

    def _insert_next_chunk(self):
        if not self._insert_lines:
            self.text.config(state="disabled")
            return
        start = self._insert_index
        end = min(start + self._insert_chunk_size, len(self._insert_lines))
        chunk = self._insert_lines[start:end]
        if chunk:
            base = start + 1
            lines_out = [f"{base + i:5d}  {ln}" for i, ln in enumerate(chunk)]
            self.text.insert("end", "\n".join(lines_out) + "\n")
        self._insert_index = end
        if callable(self._insert_progress_cb):
            self._insert_progress_cb(self._insert_index, len(self._insert_lines))
        if self._insert_index >= len(self._insert_lines):
            self.text.config(state="disabled")
            self._insert_after_id = None
            self.clear_highlights()
            if self.lines_count:
                self.highlight_current(0)
            cb = self._insert_done_cb
            self._insert_done_cb = None
            self._insert_progress_cb = None
            if callable(cb):
                cb()
            return
        self._insert_after_id = self.after(1, self._insert_next_chunk)

    def _line_range(self, idx: int) -> tuple[str, str]:
        # idx is 0-based gcode index; text lines are 1-based
        line_no = idx + 1
        start = f"{line_no}.0"
        end = f"{line_no}.end"
        return start, end

    def clear_highlights(self):
        self.text.config(state="normal")
        self.text.tag_remove("sent", "1.0", "end")
        self.text.tag_remove("acked", "1.0", "end")
        self.text.tag_remove("current", "1.0", "end")
        self.text.config(state="disabled")
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1

    def mark_sent_upto(self, idx: int):
        if self.lines_count <= 0 or idx < 0:
            return
        idx = min(idx, self.lines_count - 1)
        if idx <= self._sent_upto:
            return
        start_line = self._sent_upto + 2
        end_line = idx + 1
        self.text.config(state="normal")
        self.text.tag_add("sent", f"{start_line}.0", f"{end_line}.end")
        self.text.config(state="disabled")
        self._sent_upto = idx

    def mark_acked_upto(self, idx: int):
        if self.lines_count <= 0 or idx < 0:
            return
        idx = min(idx, self.lines_count - 1)
        if idx <= self._acked_upto:
            return
        start_line = self._acked_upto + 2
        end_line = idx + 1
        self.text.config(state="normal")
        self.text.tag_remove("sent", f"{start_line}.0", f"{end_line}.end")
        self.text.tag_add("acked", f"{start_line}.0", f"{end_line}.end")
        self.text.config(state="disabled")
        self._acked_upto = idx
        if self._sent_upto < idx:
            self._sent_upto = idx

    def mark_sent(self, idx: int):
        self.mark_sent_upto(idx)

    def mark_acked(self, idx: int):
        self.mark_acked_upto(idx)

    def highlight_current(self, idx: int):
        if idx == self._current_idx:
            return
        self.text.config(state="normal")
        if 0 <= self._current_idx < self.lines_count:
            start, end = self._line_range(self._current_idx)
            self.text.tag_remove("current", start, end)
        if 0 <= idx < self.lines_count:
            start, end = self._line_range(idx)
            self.text.tag_add("current", start, end)
            # keep in view
            self.text.see(start)
            self._current_idx = idx
        else:
            self._current_idx = -1
        self.text.config(state="disabled")


class Toolpath3D(ttk.Frame):
    def __init__(self, parent, on_save_view=None, on_load_view=None):
        super().__init__(parent)
        bg = "SystemButtonFace"
        try:
            bg = parent.cget("background")
        except Exception:
            pass
        self.show_rapid = tk.BooleanVar(value=False)
        self.show_feed = tk.BooleanVar(value=True)
        self.show_arc = tk.BooleanVar(value=False)

        self.on_save_view = on_save_view
        self.on_load_view = on_load_view

        legend = ttk.Frame(self)
        legend.pack(side="top", fill="x")
        self._legend_label(legend, "#8a8a8a", "Rapid", self.show_rapid)
        self._legend_label(legend, "#2c6dd2", "Feed", self.show_feed)
        self._legend_label(legend, "#2aa876", "Arc", self.show_arc)
        self.btn_reset_view = ttk.Button(legend, text="Reset View", command=self._reset_view)
        set_kb_id(self.btn_reset_view, "view_reset")
        self.btn_reset_view.pack(side="right", padx=(6, 0))
        self.btn_load_view = ttk.Button(legend, text="Load View", command=self._load_view)
        set_kb_id(self.btn_load_view, "view_load")
        self.btn_load_view.pack(side="right", padx=(6, 0))
        self.btn_save_view = ttk.Button(legend, text="Save View", command=self._save_view)
        set_kb_id(self.btn_save_view, "view_save")
        self.btn_save_view.pack(side="right", padx=(6, 0))
        apply_tooltip(self.btn_save_view, "Save the current 3D view.")
        apply_tooltip(self.btn_load_view, "Load the saved 3D view.")
        apply_tooltip(self.btn_reset_view, "Reset the 3D view.")

        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Shift-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Shift-B1-Motion>", self._on_pan)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds = None
        self.position = None
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
        self._drag_start = None
        self._pan_start = None
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.enabled = True
        self._pending_lines = None
        self._parse_token = 0
        self._render_pending = False
        self._render_interval = 0.1
        self._last_render_ts = 0.0
        self._visible = True
        self._colors = {
            "rapid": "#8a8a8a",
            "feed": "#2c6dd2",
            "arc": "#2aa876",
        }
        self._preview_target = 1000
        self._full_parse_limit = 20000
        self._arc_step_default = math.pi / 18
        self._arc_step_fast = math.pi / 12
        self._arc_step_large = math.pi / 8
        self._arc_step_rad = self._arc_step_default
        self._max_draw_segments = 40000
        self._render_params = None
        self._position_item = None

    def _legend_label(self, parent, color, text, var):
        swatch = tk.Label(parent, width=2, background=color)
        swatch.pack(side="left", padx=(0, 4), pady=(2, 2))
        chk = ttk.Checkbutton(parent, text=text, variable=var, command=self._schedule_render)
        chk.pack(side="left", padx=(0, 10))

    def set_gcode(self, lines: list[str]):
        self.segments, self.bounds = self._parse_gcode(lines)
        self._schedule_render()

    def set_gcode_async(self, lines: list[str]):
        self._parse_token += 1
        token = self._parse_token
        line_count = len(lines)
        if line_count > self._full_parse_limit:
            self._arc_step_rad = self._arc_step_large
        elif line_count > 5000:
            self._arc_step_rad = self._arc_step_fast
        else:
            self._arc_step_rad = self._arc_step_default
        if not self.enabled:
            self._pending_lines = lines
            return
        self._pending_lines = None
        if not lines:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        quick_lines = lines
        if len(lines) > self._preview_target:
            step = max(2, len(lines) // self._preview_target)
            quick_lines = lines[::step]
        self.segments, self.bounds = self._parse_gcode(quick_lines)
        self._schedule_render()
        if len(lines) > self._full_parse_limit:
            return
        def worker():
            segs, bnds = self._parse_gcode(lines)
            self.after(0, lambda: self._apply_full_parse(token, segs, bnds))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_full_parse(self, token, segments, bounds):
        if token != self._parse_token:
            return
        if not self.enabled:
            self._pending_lines = None
            return
        self.segments = segments
        self.bounds = bounds
        self._schedule_render()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        if self._pending_lines is not None:
            pending = self._pending_lines
            self._pending_lines = None
            self.set_gcode_async(pending)

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._visible:
            self._schedule_render()

    def set_position(self, x: float, y: float, z: float):
        self.position = (x, y, z)
        if self._visible and self.enabled:
            if not self.segments:
                return
            if self._render_params and not self._render_pending:
                self._update_position_marker()
            else:
                self._schedule_render()

    def _update_position_marker(self):
        if not self._render_params:
            return
        if not self.position:
            if self._position_item is not None:
                try:
                    self.canvas.delete(self._position_item)
                except Exception:
                    pass
                self._position_item = None
            return
        params = self._render_params
        px, py = self._project(*self.position)
        cx = (px - params["minx"]) * params["scale"] + params["margin"]
        cy = (py - params["miny"]) * params["scale"] + params["margin"]
        cx = cx + params["pan_x"]
        cy = params["height"] - cy + params["pan_y"]
        r = 4
        if self._position_item is None:
            self._position_item = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#d64545", outline=""
            )
        else:
            self.canvas.coords(self._position_item, cx - r, cy - r, cx + r, cy + r)

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
        self.azimuth += dx * 0.01
        self.elevation += dy * 0.01
        limit = math.pi / 2 - 0.1
        self.elevation = max(-limit, min(limit, self.elevation))
        self._schedule_render()

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

    def _on_mousewheel(self, event):
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        else:
            direction = 1 if event.num == 4 else -1
        if direction > 0:
            self.zoom *= 1.1
        else:
            self.zoom /= 1.1
        self.zoom = max(0.2, min(5.0, self.zoom))
        self._schedule_render()

    def _reset_view(self):
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
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
        except Exception:
            return
        self._schedule_render()

    def _schedule_render(self):
        if not self._visible:
            return
        if self._render_pending:
            return
        self._render_pending = True
        now = time.time()
        delay = max(0.0, self._render_interval - (now - self._last_render_ts))
        self.after(int(delay * 1000), self._render)

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ca = math.cos(self.azimuth)
        sa = math.sin(self.azimuth)
        ce = math.cos(self.elevation)
        se = math.sin(self.elevation)
        x1 = x * ca - y * sa
        y1 = x * sa + y * ca
        y2 = y1 * ce - z * se
        return x1, y2

    def _segments_bounds(self, segments):
        if not segments:
            return None
        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")
        for x1, y1, z1, x2, y2, z2, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            minz = min(minz, z1, z2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
            maxz = max(maxz, z1, z2)
        return minx, maxx, miny, maxy, minz, maxz

    def _parse_gcode(self, lines: list[str]):
        segments = []
        x = y = z = 0.0
        units = 1.0
        absolute = True
        plane = "G17"
        arc_abs = False
        last_motion = 1
        g92_offset = [0.0, 0.0, 0.0]
        g92_enabled = True
        for raw in lines:
            s = raw.strip().upper()
            if not s:
                continue
            if "(" in s:
                s = PAREN_COMMENT_PAT.sub("", s)
            if ";" in s:
                s = s.split(";", 1)[0]
            s = s.strip()
            if not s or s.startswith("%"):
                continue
            words = WORD_PAT.findall(s)
            if not words:
                continue
            g_codes = []
            for w, val in words:
                if w == "G":
                    try:
                        g_codes.append(float(val))
                    except Exception:
                        pass

            def has_g(code):
                return any(abs(g - code) < 1e-3 for g in g_codes)

            if has_g(20):
                units = 25.4
            if has_g(21):
                units = 1.0
            if has_g(90):
                absolute = True
            if has_g(91):
                absolute = False
            if has_g(17):
                plane = "G17"
            if has_g(18):
                plane = "G18"
            if has_g(19):
                plane = "G19"
            if has_g(90.1):
                arc_abs = True
            if has_g(91.1):
                arc_abs = False

            has_axis = False
            has_x = False
            has_y = False
            has_z = False
            nx, ny, nz = x, y, z
            i_val = None
            j_val = None
            k_val = None
            r_val = None
            for w, val in words:
                try:
                    fval = float(val) * units
                except Exception:
                    continue
                if w == "X":
                    has_axis = True
                    has_x = True
                    nx = fval if absolute else (nx + fval)
                elif w == "Y":
                    has_axis = True
                    has_y = True
                    ny = fval if absolute else (ny + fval)
                elif w == "Z":
                    has_axis = True
                    has_z = True
                    nz = fval if absolute else (nz + fval)
                elif w == "I":
                    i_val = fval
                elif w == "J":
                    j_val = fval
                elif w == "K":
                    k_val = fval
                elif w == "R":
                    r_val = fval

            if has_g(92):
                if not (has_x or has_y or has_z):
                    if g92_enabled:
                        x += g92_offset[0]
                        y += g92_offset[1]
                        z += g92_offset[2]
                    g92_offset = [0.0, 0.0, 0.0]
                else:
                    if has_x:
                        mx = x + (g92_offset[0] if g92_enabled else 0.0)
                        g92_offset[0] = mx - nx
                        x = nx
                    if has_y:
                        my = y + (g92_offset[1] if g92_enabled else 0.0)
                        g92_offset[1] = my - ny
                        y = ny
                    if has_z:
                        mz = z + (g92_offset[2] if g92_enabled else 0.0)
                        g92_offset[2] = mz - nz
                        z = nz
                g92_enabled = True
                continue
            if has_g(92.1):
                if g92_enabled:
                    x += g92_offset[0]
                    y += g92_offset[1]
                    z += g92_offset[2]
                g92_offset = [0.0, 0.0, 0.0]
                g92_enabled = False
                continue
            if has_g(92.2):
                if g92_enabled:
                    x += g92_offset[0]
                    y += g92_offset[1]
                    z += g92_offset[2]
                g92_enabled = False
                continue
            if has_g(92.3):
                if not g92_enabled:
                    x -= g92_offset[0]
                    y -= g92_offset[1]
                    z -= g92_offset[2]
                g92_enabled = True
                continue

            motion = None
            for g in g_codes:
                if abs(g - 0) < 1e-3:
                    motion = 0
                elif abs(g - 1) < 1e-3:
                    motion = 1
                elif abs(g - 2) < 1e-3:
                    motion = 2
                elif abs(g - 3) < 1e-3:
                    motion = 3
            if motion is None and has_axis:
                motion = last_motion

            if motion in (0, 1):
                if has_axis and (nx != x or ny != y or nz != z):
                    color = "rapid" if motion == 0 else "feed"
                    segments.append((x, y, z, nx, ny, nz, color))
                    x, y, z = nx, ny, nz
                if motion is not None:
                    last_motion = motion
                continue

            if motion in (2, 3):
                if not has_axis:
                    continue
                cw = motion == 2
                if plane == "G17":
                    u0, v0, u1, v1 = x, y, nx, ny
                    w0, w1 = z, nz
                    off1, off2 = i_val, j_val
                    to_xyz = lambda u, v, w: (u, v, w)
                elif plane == "G18":
                    u0, v0, u1, v1 = x, z, nx, nz
                    w0, w1 = y, ny
                    off1, off2 = i_val, k_val
                    to_xyz = lambda u, v, w: (u, w, v)
                else:
                    u0, v0, u1, v1 = y, z, ny, nz
                    w0, w1 = x, nx
                    off1, off2 = j_val, k_val
                    to_xyz = lambda u, v, w: (w, u, v)

                full_circle = abs(u1 - u0) < 1e-6 and abs(v1 - v0) < 1e-6
                if r_val is not None:
                    if full_circle:
                        r = abs(r_val)
                        if r == 0:
                            x, y, z = nx, ny, nz
                            continue
                        cu = u0 + r
                        cv = v0
                        sweep = 2 * math.pi
                    else:
                        res = _arc_center_from_radius(u0, v0, u1, v1, r_val, cw)
                        if not res:
                            x, y, z = nx, ny, nz
                            continue
                        cu, cv, sweep = res
                else:
                    if off1 is None:
                        off1 = u0 if arc_abs else 0.0
                    if off2 is None:
                        off2 = v0 if arc_abs else 0.0
                    cu = off1 if arc_abs else (u0 + off1)
                    cv = off2 if arc_abs else (v0 + off2)
                    sweep = 2 * math.pi if full_circle else _arc_sweep(u0, v0, u1, v1, cu, cv, cw)

                r = math.hypot(u0 - cu, v0 - cv)
                if r == 0 or sweep == 0:
                    x, y, z = nx, ny, nz
                    continue
                steps = max(8, int(abs(sweep) / self._arc_step_rad))
                start_ang = math.atan2(v0 - cv, u0 - cu)
                px, py, pz = x, y, z
                for i in range(1, steps + 1):
                    t = i / steps
                    ang = start_ang - sweep * t if cw else start_ang + sweep * t
                    u = cu + r * math.cos(ang)
                    v = cv + r * math.sin(ang)
                    w = w0 + (w1 - w0) * t
                    qx, qy, qz = to_xyz(u, v, w)
                    segments.append((px, py, pz, qx, qy, qz, "arc"))
                    px, py, pz = qx, qy, qz
                x, y, z = nx, ny, nz
                last_motion = motion
                continue

        bounds = self._segments_bounds(segments)
        return segments, bounds

    def _render(self):
        self._render_pending = False
        if not self._visible:
            return
        self._last_render_ts = time.time()
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
        if not self.enabled:
            self.canvas.create_text(w / 2, h / 2, text="3D render disabled", fill="#666666")
            return
        if not self.segments:
            self.canvas.create_text(w / 2, h / 2, text="No G-code loaded", fill="#666666")
            return

        segments = self.segments
        if self._max_draw_segments and len(segments) > self._max_draw_segments:
            step = max(2, len(segments) // self._max_draw_segments)
            segments = segments[::step]
        proj = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for x1, y1, z1, x2, y2, z2, color in segments:
            if color == "rapid" and not self.show_rapid.get():
                continue
            if color == "feed" and not self.show_feed.get():
                continue
            if color == "arc" and not self.show_arc.get():
                continue
            px1, py1 = self._project(x1, y1, z1)
            px2, py2 = self._project(x2, y2, z2)
            minx = min(minx, px1, px2)
            miny = min(miny, py1, py2)
            maxx = max(maxx, px1, px2)
            maxy = max(maxy, py1, py2)
            proj.append((px1, py1, px2, py2, color))

        if not proj:
            self.canvas.create_text(w / 2, h / 2, text="No toolpath selected", fill="#666666")
            return

        if maxx - minx == 0 or maxy - miny == 0:
            return
        margin = 20
        sx = (w - 2 * margin) / (maxx - minx)
        sy = (h - 2 * margin) / (maxy - miny)
        scale = min(sx, sy) * self.zoom

        def to_canvas(px, py):
            cx = (px - minx) * scale + margin
            cy = (py - miny) * scale + margin
            return cx + self.pan_x, h - cy + self.pan_y

        self._render_params = {
            "minx": minx,
            "miny": miny,
            "scale": scale,
            "margin": margin,
            "height": h,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

        for px1, py1, px2, py2, color in proj:
            x1, y1 = to_canvas(px1, py1)
            x2, y2 = to_canvas(px2, py2)
            self.canvas.create_line(x1, y1, x2, y2, fill=self._colors.get(color, "#2c6dd2"))

        self._update_position_marker()

class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = 1000):
        self.widget = widget
        self.text = text
        self._tip = None
        self.delay_ms = delay_ms
        self._after_id = None
        widget.bind("<Enter>", self._schedule_show)
        widget.bind("<Leave>", self._hide)

    def _schedule_show(self, _event=None):
        # Always reset any existing tooltip so movement can update content/position.
        self._hide()
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        try:
            if not self.widget.winfo_exists():
                return
        except tk.TclError:
            return
        top = self.widget.winfo_toplevel()
        enabled = True
        try:
            enabled = bool(top.tooltip_enabled.get())
        except Exception:
            pass
        if not enabled:
            return
        if not self.text or self._tip is not None:
            return
        # Position near the current pointer location for consistent placement with other tooltips.
        try:
            x = self.widget.winfo_pointerx() + 16
            y = self.widget.winfo_pointery() + 12
        except Exception:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        try:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            label = ttk.Label(
                self._tip,
                text=self.text,
                background="#ffffe0",
                relief="solid",
                padding=(6, 3),
            )
            label.pack()
        except tk.TclError:
            self._tip = None

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def set_text(self, text: str):
        self.text = text


class StopSignButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        fill: str,
        text_color: str,
        command=None,
        size: int = 60,
        outline: str = "#2f2f2f",
        **kwargs,
    ):
        bg = kwargs.pop("bg", None)
        if bg is None:
            bg = kwargs.pop("background", None)
        super().__init__(
            master,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=bg,
            **kwargs,
        )
        self._text = text
        self._fill = fill
        self._text_color = text_color
        self._outline = outline
        self._command = command
        self._size = size
        self._state = "normal"
        self._poly = None
        self._text_id = None
        self._disabled_fill = self._blend_color(fill, "#f0f0f0", 0.55)
        self._disabled_text = self._blend_color(text_color, "#808080", 0.55)
        self._draw_octagon()
        self._apply_state()
        self._log_button = True
        self.bind("<Button-1>", self._on_click, add="+")

    def _blend_color(self, base: str, target: str, factor: float) -> str:
        base = base.lstrip("#")
        target = target.lstrip("#")
        if len(base) != 6 or len(target) != 6:
            return base if base.startswith("#") else f"#{base}"
        br, bg, bb = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
        tr, tg, tb = int(target[0:2], 16), int(target[2:4], 16), int(target[4:6], 16)
        r = int(br + (tr - br) * factor)
        g = int(bg + (tg - bg) * factor)
        b = int(bb + (tb - bb) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_octagon(self):
        size = self._size
        pad = 2
        s = size - pad * 2
        cut = s * 0.2929
        x0, y0 = pad, pad
        x1, y1 = pad + s, pad + s
        points = [
            x0 + cut, y0,
            x1 - cut, y0,
            x1, y0 + cut,
            x1, y1 - cut,
            x1 - cut, y1,
            x0 + cut, y1,
            x0, y1 - cut,
            x0, y0 + cut,
        ]
        self._poly = self.create_polygon(points, fill=self._fill, outline=self._outline, width=1)
        self._text_id = self.create_text(
            size / 2,
            size / 2,
            text=self._text,
            fill=self._text_color,
            justify="center",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _apply_state(self):
        is_disabled = self._state == "disabled"
        fill = self._disabled_fill if is_disabled else self._fill
        text_color = self._disabled_text if is_disabled else self._text_color
        if self._poly is not None:
            self.itemconfig(self._poly, fill=fill)
        if self._text_id is not None:
            self.itemconfig(self._text_id, fill=text_color)
        self.config(cursor="arrow" if is_disabled else "hand2")

    def _on_click(self, event=None):
        if self._state == "disabled":
            return
        if callable(self._command):
            self._command()

    def configure(self, **kwargs):
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            if self._text_id is not None:
                self.itemconfig(self._text_id, text=self._text)
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            self._apply_state()
        return super().configure(**kwargs)

    def config(self, **kwargs):
        return self.configure(**kwargs)

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self):
        self._on_click()


def apply_tooltip(widget, text: str):
    if not text:
        return
    try:
        widget._tooltip_text = text
    except Exception:
        pass
    ToolTip(widget, text)


def attach_log_gcode(widget, gcode_or_func):
    try:
        widget._log_gcode_get = gcode_or_func
    except Exception:
        pass

def set_kb_id(widget, kb_id: str):
    try:
        widget._kb_id = kb_id
    except Exception:
        pass
    return widget

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Simple Streamer")
        self.minsize(980, 620)
        self.settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
        self.settings = self._load_settings()

        self.tooltip_enabled = tk.BooleanVar(value=self.settings.get("tooltips_enabled", True))
        self.gui_logging_enabled = tk.BooleanVar(value=self.settings.get("gui_logging_enabled", True))
        self.render3d_enabled = tk.BooleanVar(value=self.settings.get("render3d_enabled", True))
        self.all_stop_mode = tk.StringVar(value=self.settings.get("all_stop_mode", "stop_reset"))
        self.training_wheels = tk.BooleanVar(value=self.settings.get("training_wheels", True))
        self.reconnect_on_open = tk.BooleanVar(value=self.settings.get("reconnect_on_open", True))
        self.keyboard_bindings_enabled = tk.BooleanVar(
            value=self.settings.get("keyboard_bindings_enabled", True)
        )
        self.current_line_mode = tk.StringVar(
            value=self.settings.get("current_line_mode", "acked")
        )
        raw_bindings = self.settings.get("key_bindings", {})
        if isinstance(raw_bindings, dict):
            self._key_bindings = {}
            for k, v in raw_bindings.items():
                self._key_bindings[str(k)] = self._normalize_key_label(str(v))
        else:
            self._key_bindings = {}
        self._bound_key_sequences = set()
        self._key_sequence_map = {}
        self._kb_conflicts = set()
        self._key_sequence_buffer = []
        self._key_sequence_last_time = 0.0
        self._key_sequence_timeout = 0.8
        self._key_sequence_after_id = None
        self._kb_item_to_button = {}
        self._kb_edit = None
        self._console_lines = []
        self._console_filter = None
        self._closing = False

        self.ui_q: queue.Queue = queue.Queue()
        self.grbl = GrblWorker(self.ui_q)

        self.unit_mode = tk.StringVar(value=self.settings.get("unit_mode", "mm"))
        self.step_xy = tk.DoubleVar(value=self.settings.get("step_xy", 1.0))
        self.step_z = tk.DoubleVar(value=self.settings.get("step_z", 1.0))
        self.jog_feed = tk.DoubleVar(value=self.settings.get("jog_feed", 800.0))  # mm/min default

        self.connected = False
        self.current_port = tk.StringVar(value=self.settings.get("last_port", ""))

        # State
        self.machine_state = tk.StringVar(value="DISCONNECTED")
        self.wpos_x = tk.StringVar(value="0.000")
        self.wpos_y = tk.StringVar(value="0.000")
        self.wpos_z = tk.StringVar(value="0.000")
        self.mpos_x = tk.StringVar(value="0.000")
        self.mpos_y = tk.StringVar(value="0.000")
        self.mpos_z = tk.StringVar(value="0.000")
        self._last_gcode_lines = []
        self._gcode_loading = False
        self._gcode_load_token = 0
        self.gcode_stats_var = tk.StringVar(value="No file loaded")
        self.gcode_load_var = tk.StringVar(value="")
        self._rapid_rates = None
        self._rapid_rates_source = None
        self.fallback_rapid_rate = tk.StringVar(value=self.settings.get("fallback_rapid_rate", ""))
        self.estimate_factor = tk.DoubleVar(value=self.settings.get("estimate_factor", 1.0))
        self._estimate_factor_label = tk.StringVar(value=f"{self.estimate_factor.get():.2f}x")
        self._accel_rates = None
        self._stats_token = 0
        self._last_stats = None
        self._last_rate_source = None
        self._live_estimate_min = None
        self._stream_state = None
        self._stream_start_ts = None
        self._stream_pause_total = 0.0
        self._stream_paused_at = None
        self._grbl_ready = False
        self._alarm_locked = False
        self._alarm_message = ""
        self._pending_settings_refresh = False
        self._connected_port = None
        self._status_seen = False
        self.progress_pct = tk.IntVar(value=0)
        self.buffer_fill = tk.StringVar(value="Buffer: 0%")
        self.buffer_fill_pct = tk.IntVar(value=0)
        self._manual_controls = []
        self._override_controls = []
        self._xy_step_buttons = []
        self._z_step_buttons = []
        self._macro_lock = threading.Lock()
        self._macro_vars_lock = threading.Lock()
        self._macro_local_vars = {"app": self, "os": os}
        self._macro_vars = {
            "prbx": 0.0,
            "prby": 0.0,
            "prbz": 0.0,
            "prbcmd": "G38.2",
            "prbfeed": 10.0,
            "errline": "",
            "wx": 0.0,
            "wy": 0.0,
            "wz": 0.0,
            "wa": 0.0,
            "wb": 0.0,
            "wc": 0.0,
            "mx": 0.0,
            "my": 0.0,
            "mz": 0.0,
            "ma": 0.0,
            "mb": 0.0,
            "mc": 0.0,
            "wcox": 0.0,
            "wcoy": 0.0,
            "wcoz": 0.0,
            "wcoa": 0.0,
            "wcob": 0.0,
            "wcoc": 0.0,
            "curfeed": 0.0,
            "curspindle": 0.0,
            "_camwx": 0.0,
            "_camwy": 0.0,
            "G": [],
            "TLO": 0.0,
            "motion": "G0",
            "WCS": "G54",
            "plane": "G17",
            "feedmode": "G94",
            "distance": "G90",
            "arc": "G91.1",
            "units": "G21",
            "cutter": "",
            "tlo": "",
            "program": "M0",
            "spindle": "M5",
            "coolant": "M9",
            "tool": 0,
            "feed": 0.0,
            "rpm": 0.0,
            "planner": 0,
            "rxbytes": 0,
            "OvFeed": 100,
            "OvRapid": 100,
            "OvSpindle": 100,
            "_OvChanged": False,
            "_OvFeed": 100,
            "_OvRapid": 100,
            "_OvSpindle": 100,
            "diameter": 3.175,
            "cutfeed": 1000.0,
            "cutfeedz": 500.0,
            "safe": 3.0,
            "state": "",
            "pins": "",
            "msg": "",
            "stepz": 1.0,
            "surface": 0.0,
            "thickness": 5.0,
            "stepover": 40.0,
            "PRB": None,
            "version": "",
            "controller": "",
            "running": False,
            "prompt_choice": "",
            "prompt_index": -1,
            "prompt_cancelled": False,
        }
        self._machine_state_text = "DISCONNECTED"
        self._settings_capture = False
        self._settings_data = {}
        self._settings_values = {}
        self._settings_edited = {}
        self._settings_edit_entry = None
        self._settings_baseline = {}
        self._settings_items = {}
        self._grbl_setting_info = {}
        self._grbl_setting_keys = []
        self._settings_raw_lines = []
        self._last_sent_index = -1
        self._last_acked_index = -1
        self._confirm_last_time = {}
        self._confirm_debounce_sec = 0.8
        self._auto_reconnect_last_port = self.settings.get("last_port", "")
        self._auto_reconnect_last_attempt = 0.0
        self._auto_reconnect_pending = False
        self._auto_reconnect_retry = 0
        self._auto_reconnect_delay = 3.0
        self._auto_reconnect_max_retry = 5
        self._auto_reconnect_next_ts = 0.0
        self._user_disconnect = False
        self._ui_throttle_ms = 100
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_marks_after_id = None
        self._pending_progress = None
        self._progress_after_id = None
        self._pending_buffer = None
        self._buffer_after_id = None

        # Top + main layout
        self._build_toolbar()
        self._build_main()

        self.after(50, self._drain_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_ports(auto_connect=bool(self.reconnect_on_open.get()))
        geometry = self.settings.get("window_geometry", "")
        if isinstance(geometry, str) and geometry:
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass
        self._load_grbl_setting_info()
        self._bind_button_logging()
        self._apply_keyboard_bindings()

    # ---------- UI ----------
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Port:").pack(side="left")
        self.port_combo = ttk.Combobox(bar, width=18, textvariable=self.current_port, state="readonly")
        self.port_combo.pack(side="left", padx=(6, 4))

        self.btn_refresh = ttk.Button(bar, text="Refresh", command=self.refresh_ports)
        set_kb_id(self.btn_refresh, "port_refresh")
        self.btn_refresh.pack(side="left", padx=(0, 10))
        apply_tooltip(self.btn_refresh, "Refresh the list of serial ports.")
        self.btn_conn = ttk.Button(bar, text="Connect", command=lambda: self._confirm_and_run("Connect/Disconnect", self.toggle_connect))
        set_kb_id(self.btn_conn, "port_connect")
        self.btn_conn.pack(side="left")
        apply_tooltip(self.btn_conn, "Connect or disconnect from the selected serial port.")
        attach_log_gcode(self.btn_conn, "")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_open = ttk.Button(bar, text="Read G-code", command=self.open_gcode)
        set_kb_id(self.btn_open, "gcode_open")
        self.btn_open.pack(side="left")
        self._manual_controls.append(self.btn_open)
        apply_tooltip(self.btn_open, "Load a G-code file for streaming (read-only).")
        self.btn_clear = ttk.Button(bar, text="Clear G-code", command=lambda: self._confirm_and_run("Clear G-code", self._clear_gcode))
        set_kb_id(self.btn_clear, "gcode_clear")
        self.btn_clear.pack(side="left", padx=(6, 0))
        self._manual_controls.append(self.btn_clear)
        apply_tooltip(self.btn_clear, "Unload the current G-code and reset the viewer.")
        self.btn_run = ttk.Button(bar, text="Run", command=lambda: self._confirm_and_run("Run job", self.run_job), state="disabled")
        set_kb_id(self.btn_run, "job_run")
        self.btn_run.pack(side="left", padx=(8, 0))
        apply_tooltip(self.btn_run, "Start streaming the loaded G-code.")
        attach_log_gcode(self.btn_run, "Cycle Start")
        self.btn_pause = ttk.Button(bar, text="Pause", command=lambda: self._confirm_and_run("Pause job", self.pause_job), state="disabled")
        set_kb_id(self.btn_pause, "job_pause")
        self.btn_pause.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_pause, "Feed hold the running job.")
        attach_log_gcode(self.btn_pause, "!")
        self.btn_resume = ttk.Button(bar, text="Resume", command=lambda: self._confirm_and_run("Resume job", self.resume_job), state="disabled")
        set_kb_id(self.btn_resume, "job_resume")
        self.btn_resume.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_resume, "Resume a paused job.")
        attach_log_gcode(self.btn_resume, "~")
        self.btn_stop = ttk.Button(bar, text="Stop/Reset", command=lambda: self._confirm_and_run("Stop/Reset", self.stop_job), state="disabled")
        set_kb_id(self.btn_stop, "job_stop_reset")
        self.btn_stop.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_stop, "Stop the job and soft reset GRBL.")
        attach_log_gcode(self.btn_stop, "Ctrl-X")
        self.btn_unlock_top = ttk.Button(bar, text="Unlock", command=lambda: self._confirm_and_run("Unlock ($X)", self.grbl.unlock), state="disabled")
        set_kb_id(self.btn_unlock_top, "unlock_top")
        self.btn_unlock_top.pack(side="left", padx=(6, 0))
        apply_tooltip(self.btn_unlock_top, "Send $X to clear alarm (top-bar).")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_unit_toggle = ttk.Button(bar, text="mm", command=self._toggle_unit_mode)
        set_kb_id(self.btn_unit_toggle, "unit_toggle")
        self.btn_unit_toggle.pack(side="left", padx=(0, 0))
        self._manual_controls.append(self.btn_unit_toggle)
        apply_tooltip(self.btn_unit_toggle, "Toggle units between mm and inch.")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        self.btn_spindle_on = ttk.Button(bar, text="Spindle ON", command=lambda: self._confirm_and_run("Spindle ON", lambda: self.grbl.spindle_on(12000)))
        set_kb_id(self.btn_spindle_on, "spindle_on")
        self.btn_spindle_on.pack(side="left")
        self._manual_controls.append(self.btn_spindle_on)
        apply_tooltip(self.btn_spindle_on, "Turn spindle on at default RPM.")
        attach_log_gcode(self.btn_spindle_on, "M3 S12000")
        self.btn_spindle_off = ttk.Button(bar, text="Spindle OFF", command=lambda: self._confirm_and_run("Spindle OFF", self.grbl.spindle_off))
        set_kb_id(self.btn_spindle_off, "spindle_off")
        self.btn_spindle_off.pack(side="left", padx=(6, 0))
        self._manual_controls.append(self.btn_spindle_off)
        apply_tooltip(self.btn_spindle_off, "Turn spindle off.")
        attach_log_gcode(self.btn_spindle_off, "M5")

        # right side status
        ttk.Label(bar, textvariable=self.machine_state).pack(side="right")

    def _build_main(self):
        body = ttk.Frame(self, padding=(8, 8))
        body.pack(side="top", fill="both", expand=True)

        top = ttk.Frame(body)
        top.pack(side="top", fill="x")

        # Left: DRO
        mpos = ttk.Labelframe(top, text="Machine Position (MPos)", padding=8)
        mpos.pack(side="left", fill="y", padx=(0, 10))

        self._dro_value_row(mpos, "X", self.mpos_x)
        self._dro_value_row(mpos, "Y", self.mpos_y)
        self._dro_value_row(mpos, "Z", self.mpos_z)
        self.btn_home_mpos = ttk.Button(mpos, text="Home", command=self.grbl.home)
        set_kb_id(self.btn_home_mpos, "home")
        self.btn_home_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_home_mpos)
        apply_tooltip(self.btn_home_mpos, "Run the homing cycle.")
        attach_log_gcode(self.btn_home_mpos, "$H")
        self.btn_unlock_mpos = ttk.Button(mpos, text="Unlock", command=self.grbl.unlock)
        set_kb_id(self.btn_unlock_mpos, "unlock")
        self.btn_unlock_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_unlock_mpos)
        apply_tooltip(self.btn_unlock_mpos, "Clear alarm lock ($X).")
        attach_log_gcode(self.btn_unlock_mpos, "$X")
        self.btn_hold_mpos = ttk.Button(mpos, text="Hold", command=self.grbl.hold)
        set_kb_id(self.btn_hold_mpos, "feed_hold")
        self.btn_hold_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_hold_mpos)
        apply_tooltip(self.btn_hold_mpos, "Feed hold.")
        attach_log_gcode(self.btn_hold_mpos, "!")
        self.btn_resume_mpos = ttk.Button(mpos, text="Resume", command=self.grbl.resume)
        set_kb_id(self.btn_resume_mpos, "feed_resume")
        self.btn_resume_mpos.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_resume_mpos)
        apply_tooltip(self.btn_resume_mpos, "Resume after hold.")
        attach_log_gcode(self.btn_resume_mpos, "~")

        dro = ttk.Labelframe(top, text="Work Position (WPos)", padding=8)
        dro.pack(side="left", fill="y", padx=(0, 10))

        self.btn_zero_x = self._dro_row(dro, "X", self.wpos_x, self.zero_x)
        self.btn_zero_y = self._dro_row(dro, "Y", self.wpos_y, self.zero_y)
        self.btn_zero_z = self._dro_row(dro, "Z", self.wpos_z, self.zero_z)
        self._manual_controls.extend([self.btn_zero_x, self.btn_zero_y, self.btn_zero_z])
        apply_tooltip(self.btn_zero_x, "Zero the WCS X axis (G92 X0).")
        apply_tooltip(self.btn_zero_y, "Zero the WCS Y axis (G92 Y0).")
        apply_tooltip(self.btn_zero_z, "Zero the WCS Z axis (G92 Z0).")
        attach_log_gcode(self.btn_zero_x, "G92 X0")
        attach_log_gcode(self.btn_zero_y, "G92 Y0")
        attach_log_gcode(self.btn_zero_z, "G92 Z0")

        btns = ttk.Frame(dro)
        btns.pack(fill="x", pady=(6, 0))
        self.btn_zero_all = ttk.Button(btns, text="Zero All", command=self.zero_all)
        set_kb_id(self.btn_zero_all, "zero_all")
        self.btn_zero_all.pack(side="left", expand=True, fill="x")
        self._manual_controls.append(self.btn_zero_all)
        apply_tooltip(self.btn_zero_all, "Zero all WCS axes (G92 X0 Y0 Z0).")
        attach_log_gcode(self.btn_zero_all, "G92 X0 Y0 Z0")
        self.btn_goto_zero = ttk.Button(btns, text="Goto Zero", command=self.goto_zero)
        set_kb_id(self.btn_goto_zero, "goto_zero")
        self.btn_goto_zero.pack(side="left", expand=True, fill="x", padx=(6, 0))
        self._manual_controls.append(self.btn_goto_zero)
        apply_tooltip(self.btn_goto_zero, "Rapid move to WCS X0 Y0.")
        attach_log_gcode(self.btn_goto_zero, "G0 X0 Y0")
        macro_left = ttk.Frame(dro)
        macro_left.pack(fill="x", pady=(6, 0))

        # Center: Jog
        jog = ttk.Labelframe(top, text="Jog", padding=8)
        jog.pack(side="left", fill="both", expand=True, padx=(10, 10))

        pad = ttk.Frame(jog)
        pad.pack(side="left", padx=(0, 12))

        def j(dx, dy, dz):
            feed = float(self.jog_feed.get())
            self.grbl.jog(dx, dy, dz, feed, self.unit_mode.get())

        def jog_cmd(dx, dy, dz):
            feed = float(self.jog_feed.get())
            gunit = "G21" if self.unit_mode.get() == "mm" else "G20"
            return f"$J={gunit} G91 X{dx:.4f} Y{dy:.4f} Z{dz:.4f} F{feed:.1f}"

        # 3x3 pad for XY
        self.btn_jog_y_plus = ttk.Button(pad, text="Y+", width=6, command=lambda: j(0, self.step_xy.get(), 0))
        set_kb_id(self.btn_jog_y_plus, "jog_y_plus")
        self.btn_jog_y_plus.grid(row=0, column=1, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_y_plus, lambda: jog_cmd(0, self.step_xy.get(), 0))
        self.btn_jog_x_minus = ttk.Button(pad, text="X-", width=6, command=lambda: j(-self.step_xy.get(), 0, 0))
        set_kb_id(self.btn_jog_x_minus, "jog_x_minus")
        self.btn_jog_x_minus.grid(row=1, column=0, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_x_minus, lambda: jog_cmd(-self.step_xy.get(), 0, 0))
        self.btn_jog_x_plus = ttk.Button(pad, text="X+", width=6, command=lambda: j(self.step_xy.get(), 0, 0))
        set_kb_id(self.btn_jog_x_plus, "jog_x_plus")
        self.btn_jog_x_plus.grid(row=1, column=2, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_x_plus, lambda: jog_cmd(self.step_xy.get(), 0, 0))
        self.btn_jog_y_minus = ttk.Button(pad, text="Y-", width=6, command=lambda: j(0, -self.step_xy.get(), 0))
        set_kb_id(self.btn_jog_y_minus, "jog_y_minus")
        self.btn_jog_y_minus.grid(row=2, column=1, padx=4, pady=2)
        attach_log_gcode(self.btn_jog_y_minus, lambda: jog_cmd(0, -self.step_xy.get(), 0))
        apply_tooltip(self.btn_jog_y_plus, "Jog +Y by the selected step.")
        apply_tooltip(self.btn_jog_y_minus, "Jog -Y by the selected step.")
        apply_tooltip(self.btn_jog_x_minus, "Jog -X by the selected step.")
        apply_tooltip(self.btn_jog_x_plus, "Jog +X by the selected step.")

        style = ttk.Style()
        sep_color = style.lookup("TLabelframe", "bordercolor") or style.lookup("TSeparator", "background")
        pad_bg = style.lookup("TFrame", "background") or self.cget("bg")
        style.configure("JogSeparator.TSeparator", background=sep_color)
        # Z
        self.btn_jog_cancel = StopSignButton(
            pad,
            text="JOG\nSTOP",
            fill="#f2b200",
            text_color="#000000",
            command=self.grbl.jog_cancel,
            bg=pad_bg,
        )
        set_kb_id(self.btn_jog_cancel, "jog_stop")
        self.btn_jog_cancel.grid(row=0, column=3, rowspan=3, padx=(6, 2), pady=2, sticky="ns")
        apply_tooltip(self.btn_jog_cancel, "Cancel an active jog ($J cancel).")
        attach_log_gcode(self.btn_jog_cancel, "RT 0x85")

        sep = ttk.Separator(pad, orient="vertical", style="JogSeparator.TSeparator")
        sep.grid(row=0, column=4, rowspan=5, sticky="ns", padx=(6, 6))

        self.btn_jog_z_plus = ttk.Button(pad, text="Z+", width=6, command=lambda: j(0, 0, self.step_z.get()))
        set_kb_id(self.btn_jog_z_plus, "jog_z_plus")
        self.btn_jog_z_plus.grid(row=0, column=5, padx=(6, 2), pady=2)
        self.btn_jog_z_minus = ttk.Button(pad, text="Z-", width=6, command=lambda: j(0, 0, -self.step_z.get()))
        set_kb_id(self.btn_jog_z_minus, "jog_z_minus")
        self.btn_jog_z_minus.grid(row=2, column=5, padx=(6, 2), pady=2)
        apply_tooltip(self.btn_jog_z_plus, "Jog +Z by the selected step.")
        apply_tooltip(self.btn_jog_z_minus, "Jog -Z by the selected step.")
        attach_log_gcode(self.btn_jog_z_plus, lambda: jog_cmd(0, 0, self.step_z.get()))
        attach_log_gcode(self.btn_jog_z_minus, lambda: jog_cmd(0, 0, -self.step_z.get()))

        self.btn_all_stop = StopSignButton(
            pad,
            text="ALL\nSTOP",
            fill="#d83b2d",
            text_color="#ffffff",
            command=self._all_stop_action,
            bg=pad_bg,
        )
        set_kb_id(self.btn_all_stop, "all_stop")
        self.btn_all_stop.grid(row=0, column=6, rowspan=3, padx=(6, 0), pady=2, sticky="ns")
        apply_tooltip(self.btn_all_stop, "Immediate stop (behavior from App Settings).")
        attach_log_gcode(self.btn_all_stop, self._all_stop_gcode_label)

        self._manual_controls.extend([
            self.btn_jog_y_plus,
            self.btn_jog_x_minus,
            self.btn_jog_x_plus,
            self.btn_jog_y_minus,
            self.btn_jog_z_plus,
            self.btn_jog_z_minus,
        ])
        self._manual_controls.append(self.btn_jog_cancel)
        self._manual_controls.append(self.btn_all_stop)

        spacer = ttk.Frame(pad, height=6)
        spacer.grid(row=3, column=0, columnspan=6)

        xy_steps = ttk.Frame(pad)
        xy_steps.grid(row=4, column=0, columnspan=4, pady=(6, 0))
        xy_values = [0.1, 0.5, 1.0, 5.0, 10, 25, 50, 100]
        for i, v in enumerate(xy_values):
            r = i // 4
            c = i % 4
            btn = ttk.Button(
                xy_steps,
                text=f"{v:g}",
                command=lambda value=v: self._set_step_xy(value),
            )
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            btn._kb_id = f"step_xy_{v:g}"
            self._xy_step_buttons.append((v, btn))
            apply_tooltip(btn, f"Set XY step to {v:g}.")

        z_steps = ttk.Frame(pad)
        z_steps.grid(row=4, column=5, padx=(6, 0), pady=(6, 0))
        z_values = [0.05, 0.1, 0.5, 1, 5, 10, 25, 50]
        for i, v in enumerate(z_values):
            r = 0 if i < 4 else 1
            c = i if i < 4 else i - 4
            btn = ttk.Button(
                z_steps,
                text=f"{v:g}",
                command=lambda value=v: self._set_step_z(value),
            )
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            btn._kb_id = f"step_z_{v:g}"
            self._z_step_buttons.append((v, btn))
            apply_tooltip(btn, f"Set Z step to {v:g}.")

        self._manual_controls.extend([btn for _, btn in self._xy_step_buttons])
        self._manual_controls.extend([btn for _, btn in self._z_step_buttons])
        self._set_step_xy(self.step_xy.get())
        self._set_step_z(self.step_z.get())

        macro_right = ttk.Frame(pad)
        macro_right.grid(row=5, column=0, columnspan=6, pady=(6, 0), sticky="ew")

        self._set_unit_mode(self.unit_mode.get())

        # Right: Overrides
        ov = ttk.Labelframe(top, text="Feed Override", padding=8)
        ov.pack(side="left", fill="y", padx=(10, 0))
        self.btn_fo_plus = ttk.Button(ov, text="+10%", command=lambda: self.grbl.send_realtime(RT_FO_PLUS_10))
        set_kb_id(self.btn_fo_plus, "feed_override_plus_10")
        self.btn_fo_plus.pack(fill="x")
        self._manual_controls.append(self.btn_fo_plus)
        self._override_controls.append(self.btn_fo_plus)
        apply_tooltip(self.btn_fo_plus, "Increase feed override by 10%.")
        attach_log_gcode(self.btn_fo_plus, "RT 0x91")
        self.btn_fo_minus = ttk.Button(ov, text="-10%", command=lambda: self.grbl.send_realtime(RT_FO_MINUS_10))
        set_kb_id(self.btn_fo_minus, "feed_override_minus_10")
        self.btn_fo_minus.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_fo_minus)
        self._override_controls.append(self.btn_fo_minus)
        apply_tooltip(self.btn_fo_minus, "Decrease feed override by 10%.")
        attach_log_gcode(self.btn_fo_minus, "RT 0x92")
        self.btn_fo_reset = ttk.Button(ov, text="Reset", command=lambda: self.grbl.send_realtime(RT_FO_RESET))
        set_kb_id(self.btn_fo_reset, "feed_override_reset")
        self.btn_fo_reset.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_fo_reset)
        self._override_controls.append(self.btn_fo_reset)
        apply_tooltip(self.btn_fo_reset, "Reset feed override to 100%.")
        attach_log_gcode(self.btn_fo_reset, "RT 0x90")
        ttk.Separator(ov, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(ov, text="Spindle Override").pack()
        self.btn_so_plus = ttk.Button(ov, text="+10%", command=lambda: self.grbl.send_realtime(RT_SO_PLUS_10))
        set_kb_id(self.btn_so_plus, "spindle_override_plus_10")
        self.btn_so_plus.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_so_plus)
        self._override_controls.append(self.btn_so_plus)
        apply_tooltip(self.btn_so_plus, "Increase spindle override by 10%.")
        attach_log_gcode(self.btn_so_plus, "RT 0x9A")
        self.btn_so_minus = ttk.Button(ov, text="-10%", command=lambda: self.grbl.send_realtime(RT_SO_MINUS_10))
        set_kb_id(self.btn_so_minus, "spindle_override_minus_10")
        self.btn_so_minus.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_so_minus)
        self._override_controls.append(self.btn_so_minus)
        apply_tooltip(self.btn_so_minus, "Decrease spindle override by 10%.")
        attach_log_gcode(self.btn_so_minus, "RT 0x9B")
        self.btn_so_reset = ttk.Button(ov, text="Reset", command=lambda: self.grbl.send_realtime(RT_SO_RESET))
        set_kb_id(self.btn_so_reset, "spindle_override_reset")
        self.btn_so_reset.pack(fill="x", pady=(6, 0))
        self._manual_controls.append(self.btn_so_reset)
        self._override_controls.append(self.btn_so_reset)
        apply_tooltip(self.btn_so_reset, "Reset spindle override to 100%.")
        attach_log_gcode(self.btn_so_reset, "RT 0x99")

        # Bottom notebook: G-code + Console + Settings
        nb = ttk.Notebook(body)
        self.notebook = nb
        nb.pack(side="top", fill="both", expand=True, pady=(10, 0))
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Gcode tab
        gtab = ttk.Frame(nb, padding=6)
        nb.add(gtab, text="G-code")
        stats_row = ttk.Frame(gtab)
        stats_row.pack(fill="x", pady=(0, 6))
        self.gcode_stats_label = ttk.Label(stats_row, textvariable=self.gcode_stats_var, anchor="w")
        self.gcode_stats_label.pack(side="left", fill="x", expand=True)
        self.gcode_load_bar = ttk.Progressbar(stats_row, length=140, mode="determinate")
        self.gcode_load_label = ttk.Label(stats_row, textvariable=self.gcode_load_var, anchor="e")
        self._hide_gcode_loading()
        self.gview = GcodeText(gtab)
        self.gview.pack(fill="both", expand=True)

        # Console tab
        ctab = ttk.Frame(nb, padding=6)
        nb.add(ctab, text="Console")

        self.console = tk.Text(ctab, wrap="word", height=12, state="disabled")
        csb = ttk.Scrollbar(ctab, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=csb.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        csb.grid(row=0, column=1, sticky="ns")
        ctab.grid_rowconfigure(0, weight=1)
        ctab.grid_columnconfigure(0, weight=1)

        entry_row = ttk.Frame(ctab)
        entry_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        entry_row.grid_columnconfigure(0, weight=1)

        self.cmd_entry = ttk.Entry(entry_row)
        self.cmd_entry.grid(row=0, column=0, sticky="ew")
        self.btn_send = ttk.Button(entry_row, text="Send", command=self._send_console)
        set_kb_id(self.btn_send, "console_send")
        self.btn_send.grid(row=0, column=1, padx=(8, 0))
        self._manual_controls.extend([self.cmd_entry, self.btn_send])
        apply_tooltip(self.btn_send, "Send the command from the console input.")
        attach_log_gcode(self.btn_send, lambda: self.cmd_entry.get().strip())
        self.btn_console_save = ttk.Button(entry_row, text="Save", command=self._save_console_log)
        set_kb_id(self.btn_console_save, "console_save")
        self.btn_console_save.grid(row=0, column=2, padx=(8, 0))
        apply_tooltip(self.btn_console_save, "Save the console log to a text file.")
        self.btn_console_clear = ttk.Button(entry_row, text="Clear", command=self._clear_console_log)
        set_kb_id(self.btn_console_clear, "console_clear")
        self.btn_console_clear.grid(row=0, column=3, padx=(8, 0))
        apply_tooltip(self.btn_console_clear, "Clear the console log.")
        self.console_filter_sep = ttk.Separator(entry_row, orient="vertical")
        self.console_filter_sep.grid(row=0, column=4, sticky="ns", padx=(8, 6))
        self.btn_console_all = ttk.Button(entry_row, text="ALL", command=lambda: self._set_console_filter(None))
        set_kb_id(self.btn_console_all, "console_filter_all")
        self.btn_console_all.grid(row=0, column=5, padx=(0, 0))
        apply_tooltip(self.btn_console_all, "Show all console log entries.")
        self.btn_console_errors = ttk.Button(entry_row, text="ERRORS", command=lambda: self._set_console_filter("errors"))
        set_kb_id(self.btn_console_errors, "console_filter_errors")
        self.btn_console_errors.grid(row=0, column=6, padx=(1, 0))
        apply_tooltip(self.btn_console_errors, "Show only error entries in the console log.")
        self.btn_console_alarms = ttk.Button(entry_row, text="ALARMS", command=lambda: self._set_console_filter("alarms"))
        set_kb_id(self.btn_console_alarms, "console_filter_alarms")
        self.btn_console_alarms.grid(row=0, column=7, padx=(1, 0))
        apply_tooltip(self.btn_console_alarms, "Show only alarm entries in the console log.")

        self.cmd_entry.bind("<Return>", lambda e: self._send_console())

        # Raw $$ tab
        rtab = ttk.Frame(nb, padding=6)
        nb.add(rtab, text="Raw $$")
        self.settings_raw_text = tk.Text(rtab, wrap="word", height=12, state="disabled")
        rsb = ttk.Scrollbar(rtab, orient="vertical", command=self.settings_raw_text.yview)
        self.settings_raw_text.configure(yscrollcommand=rsb.set)
        self.settings_raw_text.grid(row=0, column=0, sticky="nsew")
        rsb.grid(row=0, column=1, sticky="ns")
        rtab.grid_rowconfigure(0, weight=1)
        rtab.grid_columnconfigure(0, weight=1)

        # Settings tab
        stab = ttk.Frame(nb, padding=6)
        nb.add(stab, text="GRBL Settings")

        sbar = ttk.Frame(stab)
        sbar.pack(fill="x", pady=(0, 6))
        self.btn_settings_refresh = ttk.Button(sbar, text="Refresh $$", command=self._request_settings_dump)
        set_kb_id(self.btn_settings_refresh, "grbl_settings_refresh")
        self.btn_settings_refresh.pack(side="left")
        apply_tooltip(self.btn_settings_refresh, "Request $$ settings from GRBL.")
        attach_log_gcode(self.btn_settings_refresh, "$$")
        self._manual_controls.append(self.btn_settings_refresh)
        self.btn_settings_save = ttk.Button(sbar, text="Save Changes", command=self._save_settings_changes)
        set_kb_id(self.btn_settings_save, "grbl_settings_save")
        self.btn_settings_save.pack(side="left", padx=(8, 0))
        apply_tooltip(self.btn_settings_save, "Send edited settings to GRBL.")
        self._manual_controls.append(self.btn_settings_save)

        self.settings_tree = ttk.Treeview(
            stab,
            columns=("setting", "name", "value", "units", "desc"),
            show="headings",
            height=12,
        )
        self.settings_tree.heading("setting", text="Setting")
        self.settings_tree.heading("name", text="Name")
        self.settings_tree.heading("value", text="Value")
        self.settings_tree.heading("units", text="Units")
        self.settings_tree.heading("desc", text="Description")
        self.settings_tree.column("setting", width=80, anchor="w")
        self.settings_tree.column("name", width=200, anchor="w")
        self.settings_tree.column("value", width=120, anchor="w")
        self.settings_tree.column("units", width=100, anchor="w")
        self.settings_tree.column("desc", width=420, anchor="w")
        self.settings_tree.pack(fill="both", expand=True)
        self.settings_tree.bind("<Double-1>", self._edit_setting_value)
        self.settings_tree.bind("<Motion>", self._settings_tooltip_motion)
        self.settings_tree.bind("<Leave>", self._settings_tooltip_hide)
        self._settings_tip = ToolTip(self.settings_tree, "")
        self.settings_tree.tag_configure("edited", background="#fff5c2")
        self.settings_tree.tag_configure("edited", background="#fff5c2")

        # App Settings tab
        sstab = ttk.Frame(nb, padding=8)
        nb.add(sstab, text="App Settings")
        sstab.grid_columnconfigure(0, weight=1)
        sstab.grid_rowconfigure(2, weight=1)

        safety = ttk.LabelFrame(sstab, text="Safety", padding=8)
        safety.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        safety.grid_columnconfigure(1, weight=1)
        ttk.Label(safety, text="All Stop behavior").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.all_stop_combo = ttk.Combobox(
            safety,
            state="readonly",
            values=[label for label, _ in ALL_STOP_CHOICES],
            width=32,
        )
        self.all_stop_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.all_stop_combo.bind("<<ComboboxSelected>>", self._on_all_stop_mode_change)
        apply_tooltip(self.all_stop_combo, "Select how the ALL STOP button behaves.")
        self._sync_all_stop_mode_combo()
        self.all_stop_desc = ttk.Label(
            safety,
            text="Soft Reset (Ctrl-X) stops GRBL immediately. Stop Stream + Reset halts sending first, then resets.",
            wraplength=560,
            justify="left",
        )
        self.all_stop_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        estimation = ttk.LabelFrame(sstab, text="Estimation", padding=8)
        estimation.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        estimation.grid_columnconfigure(1, weight=1)
        ttk.Label(estimation, text="Fallback rapid rate (mm/min)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.fallback_rapid_entry = ttk.Entry(estimation, textvariable=self.fallback_rapid_rate, width=12)
        self.fallback_rapid_entry.grid(row=0, column=1, sticky="w", pady=4)
        self.fallback_rapid_entry.bind("<Return>", self._on_fallback_rate_change)
        self.fallback_rapid_entry.bind("<FocusOut>", self._on_fallback_rate_change)
        apply_tooltip(
            self.fallback_rapid_entry,
            "Used for time estimates when GRBL max rates ($110-$112) are not available.",
        )
        ttk.Label(estimation, text="Estimator adjustment").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.estimate_factor_scale = ttk.Scale(
            estimation,
            from_=0.5,
            to=2.0,
            orient="horizontal",
            variable=self.estimate_factor,
            command=self._on_estimate_factor_change,
        )
        self.estimate_factor_scale.grid(row=1, column=1, sticky="ew", pady=4)
        self.estimate_factor_value = ttk.Label(estimation, textvariable=self._estimate_factor_label)
        self.estimate_factor_value.grid(row=1, column=2, sticky="w", padx=(8, 0))
        apply_tooltip(
            self.estimate_factor_scale,
            "Scale time estimates up or down (1.00x = default).",
        )

        kb_frame = ttk.LabelFrame(sstab, text="Keyboard shortcuts", padding=8)
        kb_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        kb_frame.grid_columnconfigure(0, weight=1)
        kb_frame.grid_rowconfigure(1, weight=1)
        self.kb_enable_check = ttk.Checkbutton(
            kb_frame,
            text="Enabled",
            variable=self.keyboard_bindings_enabled,
            command=self._on_keyboard_bindings_check,
        )
        self.kb_enable_check.grid(row=0, column=0, columnspan=2, sticky="w", padx=(6, 10), pady=(4, 2))
        apply_tooltip(self.kb_enable_check, "Toggle keyboard shortcuts.")

        self.kb_table = ttk.Treeview(
            kb_frame, columns=("button", "axis", "key", "clear"), show="headings", height=6
        )
        self.kb_table.heading("button", text="Button")
        self.kb_table.heading("axis", text="Axis")
        self.kb_table.heading("key", text="Key")
        self.kb_table.heading("clear", text="")
        self.kb_table.column("button", width=220, anchor="w")
        self.kb_table.column("axis", width=50, anchor="center")
        self.kb_table.column("key", width=140, anchor="w")
        self.kb_table.column("clear", width=160, anchor="w")
        self.kb_table.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self.kb_table_scroll = ttk.Scrollbar(kb_frame, orient="vertical", command=self.kb_table.yview)
        self.kb_table.configure(yscrollcommand=self.kb_table_scroll.set)
        self.kb_table_scroll.grid(row=1, column=1, sticky="ns", padx=(4, 6), pady=(0, 6))
        self.kb_table.bind("<Double-1>", self._on_kb_table_double_click)
        self.kb_table.bind("<Button-1>", self._on_kb_table_click, add="+")

        self.kb_note = ttk.Label(
            kb_frame,
            text="Press up to three keys to bind a shortcut. Bindings are ignored while typing in text fields.",
            wraplength=560,
            justify="left",
        )
        self.kb_note.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        view_frame = ttk.LabelFrame(sstab, text="G-code view", padding=8)
        view_frame.grid(row=3, column=0, sticky="ew")
        view_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(view_frame, text="Current line highlight").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.current_line_combo = ttk.Combobox(
            view_frame,
            state="readonly",
            values=[label for label, _ in CURRENT_LINE_CHOICES],
            width=32,
        )
        self.current_line_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.current_line_combo.bind("<<ComboboxSelected>>", self._on_current_line_mode_change)
        apply_tooltip(self.current_line_combo, "Select which line is highlighted as current.")
        self._sync_current_line_mode_combo()
        self.current_line_desc = ttk.Label(
            view_frame,
            text=(
                "Processing highlights the last line accepted by GRBL. "
                "Sent highlights the most recently queued line."
            ),
            wraplength=560,
            justify="left",
        )
        self.current_line_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        tw_frame = ttk.LabelFrame(sstab, text="Safety Aids", padding=8)
        tw_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.training_wheels_check = ttk.Checkbutton(
            tw_frame,
            text="Training Wheels (confirm top-bar actions)",
            variable=self.training_wheels,
        )
        self.training_wheels_check.grid(row=0, column=0, sticky="w")
        apply_tooltip(self.training_wheels_check, "Show confirmation dialogs for top toolbar actions.")

        self.reconnect_check = ttk.Checkbutton(
            tw_frame,
            text="Reconnect to last port on open",
            variable=self.reconnect_on_open,
        )
        self.reconnect_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        apply_tooltip(self.reconnect_check, "Auto-connect to the last used port when the app starts.")

        # 3D tab
        ttab = ttk.Frame(nb, padding=6)
        nb.add(ttab, text="3D View")
        self.toolpath_view = Toolpath3D(
            ttab,
            on_save_view=self._save_3d_view,
            on_load_view=self._load_3d_view,
        )
        self.toolpath_view.pack(fill="both", expand=True)
        self.toolpath_view.set_enabled(bool(self.render3d_enabled.get()))
        self._load_3d_view(show_status=False)


        # Status bar
        status_bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        status_bar.pack(side="bottom", fill="x", before=body)
        self.status = ttk.Label(status_bar, text="Disconnected", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Label(status_bar, text="Progress").pack(side="right")
        self.progress_bar = ttk.Progressbar(
            status_bar,
            orient="horizontal",
            length=140,
            mode="determinate",
            maximum=100,
            variable=self.progress_pct,
        )
        self.progress_bar.pack(side="right", padx=(6, 12))
        self.buffer_bar = ttk.Progressbar(
            status_bar,
            orient="horizontal",
            length=120,
            mode="determinate",
            maximum=100,
            variable=self.buffer_fill_pct,
        )
        self.buffer_bar.pack(side="right", padx=(6, 0))
        ttk.Label(status_bar, textvariable=self.buffer_fill, anchor="e").pack(side="right")
        self.btn_toggle_tips = ttk.Button(
            status_bar,
            text="Tool Tips: On",
            command=self._toggle_tooltips,
        )
        set_kb_id(self.btn_toggle_tips, "toggle_tooltips")
        self.btn_toggle_tips.pack(side="right", padx=(8, 0))
        self.btn_toggle_tips.config(
            text="Tool Tips: On" if self.tooltip_enabled.get() else "Tool Tips: Off"
        )
        self.btn_toggle_logging = ttk.Button(
            status_bar,
            text="Logging: On",
            command=self._toggle_gui_logging,
        )
        set_kb_id(self.btn_toggle_logging, "toggle_logging")
        self.btn_toggle_logging.pack(side="right", padx=(8, 0))
        self.btn_toggle_logging.config(
            text="Logging: On" if self.gui_logging_enabled.get() else "Logging: Off"
        )
        apply_tooltip(self.btn_toggle_logging, "Toggle GUI button logging in the console.")
        self.btn_toggle_3d = ttk.Button(
            status_bar,
            text="3D Render: On",
            command=self._toggle_render_3d,
        )
        set_kb_id(self.btn_toggle_3d, "toggle_render_3d")
        self.btn_toggle_3d.pack(side="right", padx=(8, 0))
        apply_tooltip(self.btn_toggle_3d, "Toggle 3D toolpath rendering.")
        self.btn_toggle_3d.config(
            text="3D Render: On" if self.render3d_enabled.get() else "3D Render: Off"
        )
        self.btn_toggle_keybinds = ttk.Button(
            status_bar,
            text="Keybindings: On",
            command=self._toggle_keyboard_bindings,
        )
        set_kb_id(self.btn_toggle_keybinds, "toggle_keybindings")
        self.btn_toggle_keybinds.pack(side="right", padx=(8, 0))
        apply_tooltip(self.btn_toggle_keybinds, "Toggle keyboard shortcuts.")
        self.btn_toggle_keybinds.config(
            text="Keybindings: On" if self.keyboard_bindings_enabled.get() else "Keybindings: Off"
        )

        self.macro_frames = {
            "left": macro_left,
            "right": macro_right,
        }
        self._load_macro_buttons()
        self._update_tab_visibility()

    def _dro_value_row(self, parent, axis, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=f"{axis}:", width=3).pack(side="left")
        ttk.Label(row, textvariable=var, width=10).pack(side="left")

    def _dro_row(self, parent, axis, var, zero_cmd):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=f"{axis}:", width=3).pack(side="left")
        ttk.Label(row, textvariable=var, width=10).pack(side="left")
        btn = ttk.Button(row, text=f"Zero {axis}", command=zero_cmd)
        btn.pack(side="right")
        set_kb_id(btn, f"zero_{axis.lower()}")
        return btn

    # ---------- UI actions ----------
    def refresh_ports(self, auto_connect: bool = False):
        ports = self.grbl.list_ports()
        self.port_combo["values"] = ports
        if ports and self.current_port.get() not in ports:
            self.current_port.set(ports[0])
        if not ports:
            self.current_port.set("")
        if auto_connect and (not self.connected):
            last = self.settings.get("last_port", "").strip()
            if last and last in ports:
                self.current_port.set(last)
                try:
                    self.toggle_connect()
                except Exception:
                    pass

    def toggle_connect(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before disconnecting.")
            return
        if self.connected:
            self._user_disconnect = True
            self.grbl.disconnect()
        else:
            port = self.current_port.get().strip()
            if not port:
                messagebox.showwarning("No port", "No serial port selected.")
                return
            try:
                self.grbl.connect(port, BAUD_DEFAULT)
                self._auto_reconnect_last_port = port
            except Exception as e:
                messagebox.showerror("Connect failed", str(e))

    def open_gcode(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            return
        path = filedialog.askopenfilename(
            title="Open G-code",
            initialdir=self.settings.get("last_gcode_dir", ""),
            filetypes=[("G-code", "*.nc *.gcode *.tap *.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        self._load_gcode_from_path(path)

    def run_job(self):
        self._reset_gcode_view_for_run()
        self.grbl.start_stream()

    def pause_job(self):
        self.grbl.pause_stream()

    def resume_job(self):
        self.grbl.resume_stream()

    def stop_job(self):
        self.grbl.stop_stream()

    def _reset_gcode_view_for_run(self):
        if not hasattr(self, "gview") or self.gview.lines_count <= 0:
            return
        self._clear_pending_ui_updates()
        self.gview.clear_highlights()
        self._last_sent_index = -1
        self._last_acked_index = -1
        self.gview.highlight_current(0)

    def _load_gcode_from_path(self, path: str):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Open G-code", "File not found.")
            return
        self.settings["last_gcode_dir"] = os.path.dirname(path)
        self._gcode_load_token += 1
        token = self._gcode_load_token
        self._gcode_loading = True
        self.btn_run.config(state="disabled")
        self.btn_pause.config(state="disabled")
        self.btn_resume.config(state="disabled")
        self.gcode_stats_var.set("Loading...")
        self.status.config(text=f"Loading: {os.path.basename(path)}")
        self._set_gcode_loading_indeterminate(f"Reading {os.path.basename(path)}")
        self.gview.set_lines_chunked([])

        def worker():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = []
                    for ln in f:
                        c = clean_gcode_line(ln)
                        if c:
                            lines.append(c)
                self.ui_q.put(("gcode_loaded", token, path, lines))
            except Exception as exc:
                self.ui_q.put(("gcode_load_error", token, path, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_loaded_gcode(self, path: str, lines: list[str]):
        if self.grbl.is_streaming():
            self._gcode_loading = False
            self._finish_gcode_loading()
            messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
            self.status.config(text="G-code load skipped (streaming)")
            return
        self._clear_pending_ui_updates()
        self._last_gcode_lines = lines
        self._live_estimate_min = None
        self._refresh_gcode_stats_display()
        self.grbl.load_gcode(lines)
        self._last_sent_index = -1
        self._last_acked_index = -1
        self._update_gcode_stats(lines)
        if hasattr(self, "toolpath_view"):
            if bool(self.render3d_enabled.get()):
                self.toolpath_view.set_gcode_async(lines)
            else:
                self.toolpath_view.set_enabled(False)
        self.status.config(text=f"Loaded: {os.path.basename(path)}  ({len(lines)} lines)")

        def on_done():
            self._gcode_loading = False
            self._finish_gcode_loading()
            if (
                self.connected
                and lines
                and self._grbl_ready
                and self._status_seen
                and not self._alarm_locked
            ):
                self.btn_run.config(state="normal")
            else:
                self.btn_run.config(state="disabled")

        def on_progress(done, total):
            self._set_gcode_loading_progress(done, total, os.path.basename(path))

        if len(lines) > 2000:
            self._set_gcode_loading_progress(0, len(lines), os.path.basename(path))
            self.gview.set_lines_chunked(lines, chunk_size=300, on_done=on_done, on_progress=on_progress)
        else:
            self.gview.set_lines(lines)
            self._set_gcode_loading_progress(len(lines), len(lines), os.path.basename(path))
            on_done()

    def _clear_gcode(self):
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before clearing the G-code file.")
            return
        self._last_gcode_lines = []
        self.grbl.load_gcode([])
        self.gview.set_lines([])
        self.gcode_stats_var.set("No file loaded")
        self.progress_pct.set(0)
        self.status.config(text="G-code cleared")
        self.btn_run.config(state="disabled")
        self.btn_pause.config(state="disabled")
        self.btn_resume.config(state="disabled")
        if hasattr(self, "toolpath_view"):
            self.toolpath_view.set_gcode_async([])

    def _show_gcode_loading(self):
        if not hasattr(self, "gcode_load_bar"):
            return
        if self.gcode_load_bar.winfo_ismapped():
            return
        self.gcode_load_label.pack(side="right", padx=(6, 0))
        self.gcode_load_bar.pack(side="right")

    def _hide_gcode_loading(self):
        if not hasattr(self, "gcode_load_bar"):
            return
        if self.gcode_load_bar.winfo_ismapped():
            self.gcode_load_bar.stop()
            self.gcode_load_bar.pack_forget()
            self.gcode_load_label.pack_forget()
        self.gcode_load_var.set("")

    def _set_gcode_loading_indeterminate(self, text: str):
        self._show_gcode_loading()
        self.gcode_load_var.set(text)
        self.gcode_load_bar.config(mode="indeterminate")
        self.gcode_load_bar.start(10)

    def _set_gcode_loading_progress(self, done: int, total: int, name: str = ""):
        self._show_gcode_loading()
        self.gcode_load_bar.stop()
        display_total = int(total)
        bar_total = max(1, display_total)
        done = max(0, min(int(done), bar_total))
        display_done = min(int(done), display_total) if display_total > 0 else 0
        self.gcode_load_bar.config(mode="determinate", maximum=bar_total, value=done)
        if name:
            self.gcode_load_var.set(f"Loading {name}: {display_done}/{display_total}")
        else:
            self.gcode_load_var.set(f"Loading {display_done}/{display_total}")

    def _finish_gcode_loading(self):
        self._hide_gcode_loading()

    def _format_duration(self, seconds: int) -> str:
        total_minutes = int(round(seconds / 60)) if seconds else 0
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"Hours:{hours:02d} Minutes:{minutes:02d}"

    def _estimate_factor_value(self) -> float:
        try:
            val = float(self.estimate_factor.get())
        except Exception:
            return 1.0
        if val <= 0:
            return 1.0
        return val

    def _refresh_gcode_stats_display(self):
        if not self._last_stats:
            return
        self.gcode_stats_var.set(self._format_gcode_stats_text(self._last_stats, self._last_rate_source))

    def _on_estimate_factor_change(self, _value=None):
        factor = self._estimate_factor_value()
        self._estimate_factor_label.set(f"{factor:.2f}x")
        self._refresh_gcode_stats_display()

    def _update_live_estimate(self, done: int, total: int):
        if self._stream_start_ts is None or done <= 0 or total <= 0:
            return
        now = time.time()
        paused_total = self._stream_pause_total
        if self._stream_paused_at is not None:
            paused_total += max(0.0, now - self._stream_paused_at)
        elapsed = max(0.0, now - self._stream_start_ts - paused_total)
        if elapsed < 1.0:
            return
        remaining = (elapsed / done) * total - elapsed
        if remaining < 0:
            remaining = 0.0
        self._live_estimate_min = remaining / 60.0
        self._refresh_gcode_stats_display()

    def _format_gcode_stats_text(self, stats: dict, rate_source: str | None) -> str:
        bounds = stats.get("bounds")
        if not bounds:
            return "No toolpath data"
        minx, maxx, miny, maxy, minz, maxz = bounds
        factor = self._estimate_factor_value()
        time_min = stats.get("time_min")
        rapid_min = stats.get("rapid_min")
        if time_min is None:
            time_txt = "n/a"
        else:
            seconds = int(round(time_min * factor * 60))
            time_txt = self._format_duration(seconds)
        if rapid_min is None or time_min is None:
            total_txt = "n/a"
            if rate_source is None:
                total_txt = "n/a (not connected)"
        else:
            seconds = int(round((time_min + rapid_min) * factor * 60))
            total_txt = self._format_duration(seconds)
            if rate_source == "fallback":
                total_txt = f"{total_txt} (fallback)"
        live_txt = ""
        if self._live_estimate_min is not None:
            live_seconds = int(round(self._live_estimate_min * factor * 60))
            live_txt = f" | Live est (stream): {self._format_duration(live_seconds)}"
        return (
            f"Bounds X[{minx:.3f}..{maxx:.3f}] "
            f"Y[{miny:.3f}..{maxy:.3f}] "
            f"Z[{minz:.3f}..{maxz:.3f}] | "
            f"Est time (feed only): {time_txt} | "
            f"Est time (with rapids): {total_txt}"
            f"{live_txt} | "
            "Approx"
        )

    def _apply_gcode_stats(self, token: int, stats: dict | None, rate_source: str | None):
        if token != self._stats_token:
            return
        self._last_stats = stats
        self._last_rate_source = rate_source
        if stats is None:
            self.gcode_stats_var.set("Estimate unavailable")
            return
        self._refresh_gcode_stats_display()

    def _get_fallback_rapid_rate(self) -> float | None:
        raw = self.fallback_rapid_rate.get().strip()
        if not raw:
            return None
        try:
            rate = float(raw)
        except Exception:
            return None
        if rate <= 0:
            return None
        return rate

    def _get_rapid_rates_for_estimate(self):
        if self._rapid_rates:
            return self._rapid_rates, "grbl"
        fallback = self._get_fallback_rapid_rate()
        if fallback:
            return (fallback, fallback, fallback), "fallback"
        return None, None

    def _get_accel_rates_for_estimate(self):
        return self._accel_rates

    def _update_gcode_stats(self, lines: list[str]):
        if not lines:
            self._last_stats = None
            self._last_rate_source = None
            self.gcode_stats_var.set("No file loaded")
            return
        self._last_stats = None
        self._last_rate_source = None
        self._stats_token += 1
        token = self._stats_token
        rapid_rates, rate_source = self._get_rapid_rates_for_estimate()
        accel_rates = self._get_accel_rates_for_estimate()
        if len(lines) > 2000:
            self.gcode_stats_var.set("Calculating stats...")

            def worker():
                try:
                    stats = compute_gcode_stats(lines, rapid_rates, accel_rates)
                except Exception as exc:
                    self.after(0, lambda: self._apply_gcode_stats(token, None, rate_source))
                    self.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
                    return
                self.after(0, lambda: self._apply_gcode_stats(token, stats, rate_source))

            threading.Thread(target=worker, daemon=True).start()
            return
        try:
            stats = compute_gcode_stats(lines, rapid_rates, accel_rates)
        except Exception as exc:
            self._apply_gcode_stats(token, None, rate_source)
            self.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
            return
        self._apply_gcode_stats(token, stats, rate_source)

    def _load_grbl_setting_info(self):
        info = {}
        keys = []
        base_dir = os.path.dirname(__file__)
        csv_path = os.path.join(
            base_dir,
            "ref",
            "grbl-master",
            "grbl-master",
            "doc",
            "csv",
            "setting_codes_en_US.csv",
        )
        if os.path.isfile(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        code = row.get("$-Code", "").strip().strip('"')
                        if not code:
                            continue
                        try:
                            idx = int(code)
                        except Exception:
                            continue
                        key = f"${idx}"
                        name = (row.get(" Setting", "") or row.get("Setting", "")).strip().strip('"')
                        units = (row.get(" Units", "") or row.get("Units", "")).strip().strip('"')
                        desc = (row.get(" Setting Description", "") or row.get("Setting Description", "")).strip().strip('"')
                        info[key] = {
                            "name": name,
                            "units": units,
                            "desc": desc,
                            "tooltip": "",
                            "idx": idx,
                        }
                        keys.append(idx)
            except Exception:
                info = {}
                keys = []

        self._load_grbl_setting_tooltips(info, base_dir)

        if not info:
            for idx in GRBL_SETTING_KEYS:
                key = f"${idx}"
                info[key] = {
                    "name": GRBL_SETTING_DESC.get(idx, ""),
                    "units": "",
                    "desc": GRBL_SETTING_DESC.get(idx, ""),
                    "tooltip": "",
                    "idx": idx,
                }
            keys = GRBL_SETTING_KEYS[:]

        pocket_overrides = {
            0: ("Step Pulse Length", "Length of the step pulse delivered to drivers."),
            1: ("Step Idle Delay", "Time before steppers disable after motion (255 keeps enabled)."),
            2: ("Step Pulse Invert", "Invert step pulse signal. See axis config table."),
            3: ("Direction Invert", "Invert axis directions. See axis config table."),
            4: ("Step Enable Invert", "Invert the enable pin signal for drivers."),
            5: ("Limit Pins Invert", "Invert limit switch pins (requires pull-down)."),
            6: ("Probe Pin Invert", "Invert probe input (requires pull-down)."),
            10: ("Status Report Mask", "Select status report fields via bitmask."),
            11: ("Junction Deviation", "Cornering speed control; higher is faster, more risk."),
            12: ("Arc Tolerance", "Arc smoothing tolerance; lower is smoother."),
            13: ("Report Inches", "Status report units (0=mm, 1=inch)."),
            20: ("Soft Limits", "Enable soft limits (requires homing)."),
            21: ("Hard Limits", "Enable limit switch alarms."),
            22: ("Homing Cycle", "Enable the homing cycle."),
            23: ("Homing Dir Invert", "Homing direction mask. See axis config table."),
            24: ("Homing Feed", "Feed rate used for final homing locate."),
            25: ("Homing Seek", "Seek rate used to find the limit switch."),
            26: ("Homing Debounce", "Debounce delay for limit switches."),
            27: ("Homing Pull-off", "Pull-off distance after homing."),
            100: ("X Steps/mm", "Steps per mm for X axis."),
            101: ("Y Steps/mm", "Steps per mm for Y axis."),
            102: ("Z Steps/mm", "Steps per mm for Z axis."),
            110: ("X Max Rate", "Maximum rate for X axis."),
            111: ("Y Max Rate", "Maximum rate for Y axis."),
            112: ("Z Max Rate", "Maximum rate for Z axis."),
            120: ("X Max Accel", "Maximum acceleration for X axis."),
            121: ("Y Max Accel", "Maximum acceleration for Y axis."),
            122: ("Z Max Accel", "Maximum acceleration for Z axis."),
            130: ("X Max Travel", "Maximum travel for X axis."),
            131: ("Y Max Travel", "Maximum travel for Y axis."),
            132: ("Z Max Travel", "Maximum travel for Z axis."),
        }
        for idx, (name, desc) in pocket_overrides.items():
            key = f"${idx}"
            if key not in info:
                info[key] = {
                    "name": name,
                    "units": "",
                    "desc": desc,
                    "tooltip": "",
                    "idx": idx,
                }
            else:
                info[key]["name"] = name
                info[key]["desc"] = desc
            keys.append(idx)

        self._grbl_setting_info = info
        self._grbl_setting_keys = sorted(set(keys))

    def _load_grbl_setting_tooltips(self, info: dict, base_dir: str):
        md_path = os.path.join(
            base_dir,
            "ref",
            "grbl-master",
            "grbl-master",
            "doc",
            "markdown",
            "settings.md",
        )
        if not os.path.isfile(md_path):
            return
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                md = f.read()
        except Exception:
            return
        pattern = re.compile(r"^#### \\$(\\d+)[^\\n]*\\n(.*?)(?=^#### \\$|\\Z)", re.M | re.S)
        for match in pattern.finditer(md):
            idx = int(match.group(1))
            body = match.group(2).strip()
            if not body:
                continue
            lines = []
            for raw in body.splitlines():
                s = raw.strip()
                if not s:
                    if lines and lines[-1] != "":
                        lines.append("")
                    continue
                if s.startswith("|"):
                    continue
                if s.startswith(":"):
                    continue
                s = s.replace("`", "")
                lines.append(s)
            tooltip = "\n".join([ln for ln in lines if ln != ""]).strip()
            key = f"${idx}"
            if key in info and tooltip:
                info[key]["tooltip"] = tooltip

    def _send_console(self):
        s = self.cmd_entry.get().strip()
        if not s:
            return
        self.grbl.send_immediate(s)
        self.cmd_entry.delete(0, "end")
    def _clear_console_log(self):
        if not messagebox.askyesno("Clear console", "Clear the console log?"):
            return
        self._console_lines = []
        self._render_console()

    def _save_console_log(self):
        path = filedialog.asksaveasfilename(
            title="Save console log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        data = self.console.get("1.0", "end-1c")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _request_settings_dump(self):
        if not self.grbl.is_connected():
            messagebox.showwarning("Not connected", "Connect to GRBL first.")
            return
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before requesting settings.")
            return
        if not self._grbl_ready:
            self._pending_settings_refresh = True
            self.status.config(text="Waiting for Grbl startup...")
            return
        if self._alarm_locked:
            messagebox.showwarning("Alarm", "Clear alarm before requesting settings.")
            return
        self._log(f"[{time.strftime('%H:%M:%S')}] Settings refresh requested ($$).")
        self._settings_capture = True
        self._settings_data = {}
        self._settings_edited = {}
        self._settings_raw_lines = []
        self._render_settings_raw("Requesting $$...")
        self.grbl.send_immediate("$$")

    def _handle_settings_line(self, line: str):
        if not self._settings_capture:
            return
        s = line.strip()
        if s.startswith("<") and s.endswith(">"):
            return
        low = s.lower()
        if low != "ok" and not low.startswith("error"):
            self._settings_raw_lines.append(s)
        if s.startswith("$") and "=" in s:
            key, value = s.split("=", 1)
            try:
                idx = int(key[1:])
            except Exception:
                idx = None
            self._settings_data[key] = (value.strip(), idx)
            return
        if low == "ok":
            self._settings_capture = False
            self._render_settings()
            self._update_rapid_rates()
            self._update_accel_rates()
            if self._last_gcode_lines:
                self._update_gcode_stats(self._last_gcode_lines)
            self._render_settings_raw()
        elif low.startswith("error"):
            self._settings_capture = False
            self.status.config(text=f"Settings error: {s}")
            self._render_settings_raw()

    def _render_settings(self):
        self._settings_items = {}
        for item in self.settings_tree.get_children():
            self.settings_tree.delete(item)
        items = []
        self._settings_values = {}
        for key, (value, idx) in self._settings_data.items():
            self._settings_values[key] = value
            info = self._grbl_setting_info.get(key, {})
            name = info.get("name", "")
            units = info.get("units", "")
            desc = info.get("desc", "")
            items.append((idx if idx is not None else 9999, key, name, value, units, desc))
        for idx in self._grbl_setting_keys:
            key = f"${idx}"
            if key not in self._settings_values:
                self._settings_values[key] = ""
                info = self._grbl_setting_info.get(key, {})
                name = info.get("name", "")
                units = info.get("units", "")
                desc = info.get("desc", "")
                items.append((idx, key, name, "", units, desc))
        for _, key, name, value, units, desc in sorted(items):
            item_id = self.settings_tree.insert("", "end", values=(key, name, value, units, desc))
            self._settings_items[key] = item_id
        self._settings_baseline = dict(self._settings_values)
        for key in self._settings_items:
            self._update_setting_row_tags(key)
        self.status.config(text=f"Settings: {len(items)} values")

    def _update_rapid_rates(self):
        try:
            rx = float(self._settings_data.get("$110", ("", None))[0])
            ry = float(self._settings_data.get("$111", ("", None))[0])
            rz = float(self._settings_data.get("$112", ("", None))[0])
            if rx > 0 and ry > 0 and rz > 0:
                self._rapid_rates = (rx, ry, rz)
                self._rapid_rates_source = "grbl"
                return
        except Exception:
            pass
        self._rapid_rates = None
        self._rapid_rates_source = None

    def _update_accel_rates(self):
        try:
            ax = float(self._settings_data.get("$120", ("", None))[0])
            ay = float(self._settings_data.get("$121", ("", None))[0])
            az = float(self._settings_data.get("$122", ("", None))[0])
            if ax > 0 and ay > 0 and az > 0:
                self._accel_rates = (ax, ay, az)
                return
        except Exception:
            pass
        self._accel_rates = None

    def _render_settings_raw(self, header: str | None = None):
        if not hasattr(self, "settings_raw_text"):
            return
        lines = []
        if header:
            lines.append(header)
        if self._settings_raw_lines:
            lines.extend(self._settings_raw_lines)
        self.settings_raw_text.config(state="normal")
        self.settings_raw_text.delete("1.0", "end")
        self.settings_raw_text.insert("end", "\n".join(lines).strip() + "\n")
        self.settings_raw_text.config(state="disabled")

    def _edit_setting_value(self, event):
        item = self.settings_tree.identify_row(event.y)
        col = self.settings_tree.identify_column(event.x)
        if not item or col != "#3":
            return
        bbox = self.settings_tree.bbox(item, column=col)
        if not bbox:
            return
        x, y, w, h = bbox
        values = self.settings_tree.item(item, "values")
        if not values:
            return
        key = values[0]
        current = values[2]

        # Commit any existing in-place edit before starting a new one so multiple edits are preserved.
        self._commit_pending_setting_edit()

        entry = ttk.Entry(self.settings_tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, current)
        entry.focus_set()
        entry._item = item
        entry._key = key
        self._settings_edit_entry = entry

        def commit(_event=None):
            self._commit_pending_setting_edit()

        def cancel(_event=None):
            self._cancel_pending_setting_edit()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _commit_pending_setting_edit(self):
        """Persist any active inline edit into the edited set."""
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        key = getattr(entry, "_key", None)
        item = getattr(entry, "_item", None)
        try:
            if key and item:
                new_val = entry.get().strip()
                # Inline numeric validation; most GRBL settings are numeric.
                if new_val:
                    try:
                        idx = int(key[1:]) if key.startswith("$") else None
                    except Exception:
                        idx = None
                    if idx not in GRBL_NON_NUMERIC_SETTINGS:
                        try:
                            val_num = float(new_val)
                        except Exception:
                            messagebox.showwarning("Invalid value", f"Setting {key} must be numeric.")
                            return
                        limits = GRBL_SETTING_LIMITS.get(idx, None)
                        if limits:
                            lo, hi = limits
                            if lo is not None and val_num < lo:
                                messagebox.showwarning("Out of range", f"Setting {key} must be >= {lo}.")
                                return
                            if hi is not None and val_num > hi:
                                messagebox.showwarning("Out of range", f"Setting {key} must be <= {hi}.")
                                return
                self.settings_tree.set(item, "value", new_val)
                self._settings_values[key] = new_val
                baseline = self._settings_baseline.get(key, "")
                if new_val == baseline and key in self._settings_edited:
                    self._settings_edited.pop(key, None)
                else:
                    self._settings_edited[key] = new_val
                self._update_setting_row_tags(key)
        finally:
            try:
                entry.destroy()
            except Exception:
                pass
            self._settings_edit_entry = None

    def _cancel_pending_setting_edit(self):
        """Discard any active inline edit without saving."""
        entry = getattr(self, "_settings_edit_entry", None)
        if entry is None:
            return
        try:
            entry.destroy()
        except Exception:
            pass
        self._settings_edit_entry = None

    def _save_settings_changes(self):
        # Ensure any active cell edit is committed before saving.
        self._commit_pending_setting_edit()
        if not self.grbl.is_connected():
            messagebox.showwarning("Not connected", "Connect to GRBL first.")
            return
        if self.grbl.is_streaming():
            messagebox.showwarning("Busy", "Stop the stream before saving settings.")
            return
        if not self._settings_edited:
            messagebox.showinfo("No changes", "No settings have been edited.")
            return
        if not messagebox.askyesno("Confirm save", "Send edited settings to GRBL?"):
            return
        # Collect edits, allowing zeros but skipping blank strings.
        changes = []
        for key, value in self._settings_edited.items():
            val = "" if value is None else str(value).strip()
            if val == "":
                continue
            changes.append((key, val))
        if not changes:
            messagebox.showinfo("No changes", "No non-empty settings to send.")
            return

        def worker():
            sent = 0
            for key, val in changes:
                self.grbl.send_immediate(f"{key}={val}")
                sent += 1
                # Small spacing to avoid clobbering on noisy links.
                time.sleep(0.05)
            self._settings_edited = {}
            self.ui_q.put(("log", f"[settings] Sent {sent} change(s)."))
            try:
                self.after(0, lambda: self._mark_settings_saved(changes, sent))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _mark_settings_saved(self, changes, sent_count: int):
        for key, _ in changes:
            if key in self._settings_values:
                self._settings_baseline[key] = self._settings_values[key]
                self._update_setting_row_tags(key)
        try:
            self.status.config(text=f"Settings: sent {sent_count} change(s)")
        except Exception:
            pass

    def _settings_tooltip_motion(self, event):
        item = self.settings_tree.identify_row(event.y)
        if not item:
            self._settings_tooltip_hide()
            return
        values = self.settings_tree.item(item, "values")
        if not values:
            self._settings_tooltip_hide()
            return
        key = values[0]
        try:
            idx = int(key[1:])
        except Exception:
            idx = None
        info = self._grbl_setting_info.get(key, {})
        desc = info.get("desc", "")
        units = info.get("units", "")
        tooltip = info.get("tooltip", "")
        baseline_val = self._settings_baseline.get(key, "")
        current_val = self._settings_values.get(key, "")
        limits = None
        try:
            limits = GRBL_SETTING_LIMITS.get(int(key[1:]), None)
        except Exception:
            limits = None
        allow_text = False
        try:
            allow_text = int(key[1:]) in GRBL_NON_NUMERIC_SETTINGS
        except Exception:
            allow_text = False
        value_line = (
            f"Pending: {current_val} (last saved: {baseline_val})"
            if current_val != baseline_val
            else f"Value: {baseline_val}"
        )
        parts = []
        if tooltip:
            parts.append(tooltip)
        elif desc:
            parts.append(desc)
        if units:
            parts.append(f"Units: {units}")
        if allow_text:
            parts.append("Allows text values")
        if limits:
            lo, hi = limits
            if lo is not None and hi is not None:
                parts.append(f"Allowed: {lo} .. {hi}")
            elif lo is not None:
                parts.append(f"Allowed: >= {lo}")
            elif hi is not None:
                parts.append(f"Allowed: <= {hi}")
        parts.append(value_line)
        parts.append("Typical: machine-specific")
        self._settings_tip.set_text("\n".join([p for p in parts if p]))
        self._settings_tip._schedule_show()

    def _settings_tooltip_hide(self, _event=None):
        if self._settings_tip:
            self._settings_tip._hide()

    def _maybe_auto_reconnect(self):
        if self.connected or self._closing or (not self._auto_reconnect_pending):
            return
        if not self._auto_reconnect_last_port:
            return
        try:
            if not bool(self.reconnect_on_open.get()):
                self._auto_reconnect_pending = False
                return
        except Exception:
            pass
        now = time.time()
        if now < self._auto_reconnect_next_ts:
            return
        ports = self.grbl.list_ports()
        if self._auto_reconnect_last_port not in ports:
            # If we've exceeded retries, allow a cool-down retry later.
            if self._auto_reconnect_retry >= self._auto_reconnect_max_retry:
                self._auto_reconnect_next_ts = now + max(30.0, self._auto_reconnect_delay)
                self._auto_reconnect_pending = True
            else:
                self._auto_reconnect_next_ts = now + self._auto_reconnect_delay
            return
        self._auto_reconnect_last_attempt = now
        try:
            self.current_port.set(self._auto_reconnect_last_port)
            self.grbl.connect(self._auto_reconnect_last_port, BAUD_DEFAULT)
            # success: reset counters
            self._auto_reconnect_pending = False
            self._auto_reconnect_retry = 0
            self._auto_reconnect_delay = 3.0
            self._auto_reconnect_next_ts = 0.0
        except Exception:
            self._auto_reconnect_retry += 1
            if self._auto_reconnect_retry > self._auto_reconnect_max_retry:
                # Stop aggressive retries but leave a long cool-down in case the port reappears later.
                self._auto_reconnect_delay = 30.0
                self._auto_reconnect_next_ts = now + self._auto_reconnect_delay
                self._auto_reconnect_pending = True
                return
            self._auto_reconnect_delay = min(30.0, self._auto_reconnect_delay * 1.5)
            self._auto_reconnect_next_ts = now + self._auto_reconnect_delay

    def _update_setting_row_tags(self, key: str):
        item = self._settings_items.get(key)
        if not item:
            return
        current = self._settings_values.get(key, "")
        baseline = self._settings_baseline.get(key, "")
        tags = list(self.settings_tree.item(item, "tags"))
        if current != baseline:
            if "edited" not in tags:
                tags.append("edited")
        else:
            tags = [t for t in tags if t != "edited"]
        self.settings_tree.item(item, tags=tuple(tags))

    def _set_unit_mode(self, mode: str):
        self.unit_mode.set(mode)
        with self._macro_vars_lock:
            self._macro_vars["units"] = "G21" if mode == "mm" else "G20"
        try:
            self.btn_unit_toggle.config(text="mm" if mode == "mm" else "inch")
        except Exception:
            pass

    def _set_step_xy(self, value: float):
        self.step_xy.set(value)
        for v, btn in self._xy_step_buttons:
            btn.config(state="disabled" if v == value else "normal")

    def _set_step_z(self, value: float):
        self.step_z.set(value)
        for v, btn in self._z_step_buttons:
            btn.config(state="disabled" if v == value else "normal")

    def _set_manual_controls_enabled(self, enabled: bool):
        if getattr(self, "_alarm_locked", False):
            for w in self._manual_controls:
                try:
                    if w is getattr(self, "btn_all_stop", None):
                        continue
                    if w is getattr(self, "btn_home_mpos", None):
                        w.config(state="normal")
                        continue
                    if w is getattr(self, "btn_unlock_mpos", None):
                        w.config(state="normal")
                        continue
                    if w is getattr(self, "btn_unlock_top", None):
                        w.config(state="normal")
                        continue
                    w.config(state="disabled")
                except tk.TclError:
                    pass
            return
        state = "normal" if enabled else "disabled"
        for w in self._manual_controls:
            try:
                if not enabled and w is getattr(self, "btn_all_stop", None):
                    continue
                if not enabled and w in self._override_controls:
                    continue
                w.config(state=state)
            except tk.TclError:
                pass
        if enabled:
            self._set_unit_mode(self.unit_mode.get())
            self._set_step_xy(self.step_xy.get())
            self._set_step_z(self.step_z.get())

    def _set_streaming_lock(self, locked: bool):
        state = "disabled" if locked else "normal"
        try:
            self.btn_conn.config(state=state)
        except Exception:
            pass
        try:
            self.btn_refresh.config(state=state)
        except Exception:
            pass
        try:
            self.port_combo.config(state="disabled" if locked else "readonly")
        except Exception:
            pass

    def _format_alarm_message(self, message: str | None) -> str:
        if not message:
            return "ALARM"
        text = str(message).strip()
        if text.lower().startswith("alarm"):
            return text
        if "reset to continue" in text.lower():
            return "ALARM: Reset to continue"
        if text.startswith("[MSG:"):
            return f"ALARM: {text}"
        return f"ALARM: {text}"

    def _set_alarm_lock(self, locked: bool, message: str | None = None):
        if locked:
            self._alarm_locked = True
            if message:
                self._alarm_message = message
            self.btn_run.config(state="disabled")
            self.btn_pause.config(state="disabled")
            self.btn_resume.config(state="disabled")
            self._set_manual_controls_enabled(True)
            try:
                self.status.config(text=self._format_alarm_message(message or self._alarm_message))
            except Exception:
                pass
            return

        if not self._alarm_locked:
            return
        self._alarm_locked = False
        self._alarm_message = ""
        if (
            self.connected
            and self._grbl_ready
            and self._status_seen
            and self._stream_state not in ("running", "paused")
        ):
            self._set_manual_controls_enabled(True)
            if self.gview.lines_count:
                self.btn_run.config(state="normal")
        status_text = ""
        try:
            status_text = self.status.cget("text")
        except Exception:
            pass
        if self.connected and status_text.startswith("ALARM"):
            self.status.config(text=f"Connected: {self._connected_port}")
        if self._pending_settings_refresh and self._grbl_ready:
            self._pending_settings_refresh = False
            self._request_settings_dump()

    def _sync_all_stop_mode_combo(self):
        mode = self.all_stop_mode.get()
        label = None
        for lbl, code in ALL_STOP_CHOICES:
            if code == mode:
                label = lbl
                break
        if label is None and ALL_STOP_CHOICES:
            label = ALL_STOP_CHOICES[0][0]
            self.all_stop_mode.set(ALL_STOP_CHOICES[0][1])
        if hasattr(self, "all_stop_combo"):
            self.all_stop_combo.set(label if label else "")

    def _on_all_stop_mode_change(self, _event=None):
        label = ""
        if hasattr(self, "all_stop_combo"):
            label = self.all_stop_combo.get()
        mode = next((code for lbl, code in ALL_STOP_CHOICES if lbl == label), "stop_reset")
        self.all_stop_mode.set(mode)
        self.status.config(text=f"All Stop mode: {label}")

    def _sync_current_line_mode_combo(self):
        mode = self.current_line_mode.get()
        label = None
        for lbl, code in CURRENT_LINE_CHOICES:
            if code == mode:
                label = lbl
                break
        if label is None and CURRENT_LINE_CHOICES:
            label = CURRENT_LINE_CHOICES[0][0]
            self.current_line_mode.set(CURRENT_LINE_CHOICES[0][1])
        if hasattr(self, "current_line_combo"):
            self.current_line_combo.set(label if label else "")

    def _on_current_line_mode_change(self, _event=None):
        label = ""
        if hasattr(self, "current_line_combo"):
            label = self.current_line_combo.get()
        mode = next((code for lbl, code in CURRENT_LINE_CHOICES if lbl == label), "acked")
        self.current_line_mode.set(mode)
        self._update_current_highlight()

    def _toggle_keyboard_bindings(self):
        current = bool(self.keyboard_bindings_enabled.get())
        new_val = not current
        self.keyboard_bindings_enabled.set(new_val)
        if hasattr(self, "btn_toggle_keybinds"):
            self.btn_toggle_keybinds.config(text="Keybindings: On" if new_val else "Keybindings: Off")
        self._apply_keyboard_bindings()

    def _on_keyboard_bindings_check(self):
        new_val = bool(self.keyboard_bindings_enabled.get())
        if hasattr(self, "btn_toggle_keybinds"):
            self.btn_toggle_keybinds.config(text="Keybindings: On" if new_val else "Keybindings: Off")
        self._apply_keyboard_bindings()

    def _apply_keyboard_bindings(self):
        if "<KeyPress>" in self._bound_key_sequences:
            self.unbind_all("<KeyPress>")
            self._bound_key_sequences.clear()
        self._key_sequence_map = {}
        self._kb_conflicts = set()
        for btn in self._collect_buttons():
            binding_id = self._button_binding_id(btn)
            if binding_id in self._key_bindings:
                label = self._normalize_key_label(str(self._key_bindings.get(binding_id, "")).strip())
                if not label:
                    continue
                is_custom = True
            else:
                label = self._default_key_for_button(btn)
                if not label:
                    continue
                is_custom = False
            seq = self._key_sequence_tuple(label)
            if not seq:
                continue
            conflict_seq = self._sequence_conflict(seq, self._key_sequence_map)
            if conflict_seq:
                other_btn = self._key_sequence_map.get(conflict_seq)
                other_id = self._button_binding_id(other_btn) if other_btn else ""
                if binding_id in self._key_bindings:
                    self._key_bindings[binding_id] = ""
                self._kb_conflicts.add(binding_id)
                if other_id:
                    if other_id in self._key_bindings:
                        self._key_bindings[other_id] = ""
                    self._kb_conflicts.add(other_id)
                    self._key_sequence_map.pop(conflict_seq, None)
                continue
            self._key_sequence_map[seq] = btn
        self._refresh_keyboard_table()
        if not bool(self.keyboard_bindings_enabled.get()):
            self._clear_key_sequence_buffer()
            return
        self._bound_key_sequences.add("<KeyPress>")
        self.bind_all("<KeyPress>", self._on_key_sequence, add="+")

    def _refresh_keyboard_table(self):
        if not hasattr(self, "kb_table"):
            return
        self.kb_table.delete(*self.kb_table.get_children())
        self.kb_table.tag_configure("conflict", background="#f7d6d6")
        self._kb_item_to_button = {}
        for btn in self._collect_buttons():
            binding_id = self._button_binding_id(btn)
            label = self._button_label(btn)
            tip = getattr(btn, "_tooltip_text", "")
            if tip:
                label = f"{label} - {tip}"
            axis = self._button_axis_name(btn)
            key = self._keyboard_key_for_button(btn)
            if not key:
                key = "None"
            tags = ("conflict",) if binding_id in self._kb_conflicts else ()
            item = self.kb_table.insert(
                "",
                "end",
                values=(label, axis, key, f"{CLEAR_ICON}  Remove/Clear Binding"),
                tags=tags,
            )
            self._kb_item_to_button[item] = btn

    def _collect_buttons(self) -> list:
        buttons = []
        seen = set()

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, tk.Button, StopSignButton)):
                    if child not in seen:
                        seen.add(child)
                        buttons.append(child)
                walk(child)

        walk(self)
        buttons.sort(key=self._button_label)
        return buttons

    def _button_label(self, btn) -> str:
        label = ""
        try:
            label = btn.cget("text")
        except Exception:
            label = ""
        if not label:
            label = btn.winfo_name()
        return label.replace("\n", " ").strip()

    def _keyboard_key_for_button(self, btn) -> str:
        binding_id = self._button_binding_id(btn)
        if binding_id in self._kb_conflicts:
            return ""
        if binding_id in self._key_bindings:
            return self._normalize_key_label(str(self._key_bindings.get(binding_id, "")).strip())
        return self._default_key_for_button(btn)

    def _button_axis_name(self, btn) -> str:
        xy_buttons = {b for _, b in self._xy_step_buttons}
        z_buttons = {b for _, b in self._z_step_buttons}
        if btn in xy_buttons:
            return "XY"
        if btn in z_buttons:
            return "Z"
        return ""

    def _button_binding_id(self, btn) -> str:
        kb_id = getattr(btn, "_kb_id", "")
        if kb_id:
            return kb_id
        label = self._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        name = btn.winfo_name()
        return f"{label}|{tip}|{name}"

    def _find_binding_conflict(self, target_btn, label: str):
        seq = self._key_sequence_tuple(label)
        if not seq:
            return None
        for btn in self._collect_buttons():
            if btn is target_btn:
                continue
            other_seq = self._key_sequence_tuple(self._keyboard_key_for_button(btn))
            if other_seq and self._sequence_conflict_pair(seq, other_seq):
                return btn
        return None

    def _default_key_for_button(self, btn) -> str:
        if btn is getattr(self, "btn_jog_cancel", None):
            return "Space"
        if btn is getattr(self, "btn_all_stop", None):
            return "Enter"
        return ""

    def _on_kb_table_double_click(self, event):
        if not hasattr(self, "kb_table"):
            return
        row = self.kb_table.identify_row(event.y)
        col = self.kb_table.identify_column(event.x)
        if not row or col != "#3":
            return
        self._start_kb_edit(row, col)

    def _on_kb_table_click(self, event):
        if not hasattr(self, "kb_table"):
            return
        row = self.kb_table.identify_row(event.y)
        col = self.kb_table.identify_column(event.x)
        if not row or col != "#4":
            if row and col == "#3":
                self._start_kb_edit(row, col)
            return
        btn = self._kb_item_to_button.get(row)
        if btn is None:
            return
        binding_id = self._button_binding_id(btn)
        self._key_bindings[binding_id] = ""
        self._apply_keyboard_bindings()

    def _start_kb_edit(self, row, col):
        bbox = self.kb_table.bbox(row, col)
        if not bbox:
            return
        if self._kb_edit is not None:
            try:
                self._kb_edit.destroy()
            except Exception:
                pass
            self._kb_edit = None
        x, y, w, h = bbox
        value = self.kb_table.set(row, "key")
        entry = ttk.Entry(self.kb_table)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, "Press keys...")
        entry._kb_prev = "" if value == "None" else value
        entry._kb_placeholder = True
        entry._kb_seq = []
        entry._kb_after_id = None
        entry.focus()
        entry.bind("<KeyPress>", lambda e: self._kb_capture_key(e, row, entry))
        entry.bind("<FocusOut>", lambda e: self._commit_kb_edit(row, entry))
        self._kb_edit = entry

    def _kb_capture_key(self, event, row, entry):
        if event.keysym in ("Escape",):
            try:
                entry.destroy()
            except Exception:
                pass
            self._kb_edit = None
            return "break"
        if event.keysym in ("BackSpace", "Delete"):
            self._commit_kb_edit(row, entry, label_override="")
            return "break"
        label = self._event_to_binding_label(event)
        if not label:
            return "break"
        seq = getattr(entry, "_kb_seq", [])
        if len(seq) >= 3:
            return "break"
        seq.append(label)
        entry._kb_seq = seq
        entry._kb_placeholder = False
        entry.delete(0, "end")
        entry.insert(0, " ".join(seq))
        if entry._kb_after_id is not None:
            entry.after_cancel(entry._kb_after_id)
        if len(seq) >= 3:
            self._commit_kb_edit(row, entry, label_override=" ".join(seq))
            return "break"
        entry._kb_after_id = entry.after(
            int(self._key_sequence_timeout * 1000),
            lambda: self._commit_kb_edit(row, entry, label_override=" ".join(seq)),
        )
        return "break"

    def _commit_kb_edit(self, row, entry, label_override: str | None = None):
        if self._kb_edit is None:
            return
        if label_override is None:
            try:
                new_val = entry.get()
            except Exception:
                new_val = ""
        else:
            new_val = label_override
        try:
            if hasattr(entry, "_kb_after_id") and entry._kb_after_id is not None:
                entry.after_cancel(entry._kb_after_id)
            entry.destroy()
        except Exception:
            pass
        self._kb_edit = None
        if label_override is None and getattr(entry, "_kb_placeholder", False):
            if new_val.strip() == "Press keys...":
                return
        btn = self._kb_item_to_button.get(row)
        if btn is None:
            return
        label = self._normalize_key_label(new_val)
        binding_id = self._button_binding_id(btn)
        self._key_bindings[binding_id] = label
        self._apply_keyboard_bindings()

    def _normalize_key_label(self, text: str) -> str:
        raw = text.strip()
        if not raw:
            return ""
        chunks = [c for c in raw.replace(",", " ").split() if c.strip()]
        seq = []
        for chunk in chunks:
            chord = self._normalize_key_chord(chunk)
            if chord:
                seq.append(chord)
            if len(seq) >= 3:
                break
        return " ".join(seq)

    def _normalize_key_chord(self, text: str) -> str:
        raw = text.strip()
        if not raw:
            return ""
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        aliases = {
            "SPACE": "Space",
            "SPC": "Space",
            "ENTER": "Enter",
            "RETURN": "Enter",
            "ESC": "Escape",
            "ESCAPE": "Escape",
            "TAB": "Tab",
            "BACKSPACE": "Backspace",
            "DEL": "Delete",
            "DELETE": "Delete",
            "NONE": "",
            "CTRL": "Ctrl",
            "CONTROL": "Ctrl",
            "SHIFT": "Shift",
            "ALT": "Alt",
            "OPTION": "Alt",
        }
        mods = []
        key = ""
        for part in parts:
            up = part.upper()
            if up in aliases:
                mapped = aliases[up]
                if mapped in ("Ctrl", "Shift", "Alt"):
                    if mapped not in mods:
                        mods.append(mapped)
                elif mapped:
                    key = mapped
                continue
            if len(part) == 1:
                key = part.upper()
            else:
                key = part
        if not key:
            return ""
        mod_order = ("Ctrl", "Shift", "Alt")
        ordered_mods = [m for m in mod_order if m in mods]
        return "+".join(ordered_mods + [key])

    def _key_sequence_tuple(self, label: str) -> tuple[str, ...] | None:
        normalized = self._normalize_key_label(label)
        if not normalized:
            return None
        parts = [p for p in normalized.split(" ") if p]
        return tuple(parts[:3])

    def _event_to_binding_label(self, event) -> str:
        keysym = event.keysym
        if keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return ""
        mods = []
        if event.state & 0x4:
            mods.append("Ctrl")
        if event.state & 0x1:
            mods.append("Shift")
        alt_mask = 0x8 | 0x20000
        if event.state & alt_mask:
            mods.append("Alt")
        key_label = self._normalize_key_chord(keysym)
        if not key_label:
            return ""
        if mods:
            return "+".join(mods + [key_label])
        return key_label

    def _sequence_conflict_pair(self, seq_a: tuple[str, ...], seq_b: tuple[str, ...]) -> bool:
        if not seq_a or not seq_b:
            return False
        min_len = min(len(seq_a), len(seq_b))
        return seq_a[:min_len] == seq_b[:min_len]

    def _sequence_conflict(self, seq: tuple[str, ...], existing: dict):
        for other_seq in existing.keys():
            if self._sequence_conflict_pair(seq, other_seq):
                return other_seq
        return None

    def _on_key_sequence(self, event):
        if not self._keyboard_binding_allowed():
            return
        label = self._event_to_binding_label(event)
        if not label:
            return
        now = time.time()
        if now - self._key_sequence_last_time > self._key_sequence_timeout:
            self._key_sequence_buffer = []
        self._key_sequence_last_time = now
        self._key_sequence_buffer.append(label)
        if len(self._key_sequence_buffer) > 3:
            self._key_sequence_buffer = self._key_sequence_buffer[-3:]
        if self._key_sequence_after_id is not None:
            self.after_cancel(self._key_sequence_after_id)
            self._key_sequence_after_id = None
        seq = tuple(self._key_sequence_buffer)
        btn = self._key_sequence_map.get(seq)
        if btn is not None:
            self._key_sequence_buffer = []
            self._on_key_binding(btn)
            return
        self._key_sequence_after_id = self.after(
            int(self._key_sequence_timeout * 1000),
            self._clear_key_sequence_buffer,
        )

    def _clear_key_sequence_buffer(self):
        self._key_sequence_buffer = []
        if self._key_sequence_after_id is not None:
            try:
                self.after_cancel(self._key_sequence_after_id)
            except Exception:
                pass
        self._key_sequence_after_id = None

    def _keyboard_binding_allowed(self) -> bool:
        if not bool(self.keyboard_bindings_enabled.get()):
            return False
        if self.grab_current() is not None:
            return False
        widget = self.focus_get()
        if widget is None:
            return True
        try:
            if widget.winfo_toplevel() is not self:
                return False
        except Exception:
            return False
        cls = widget.winfo_class()
        if cls in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox"):
            return False
        return True

    def _on_key_jog_stop(self, _event=None):
        if not self._keyboard_binding_allowed():
            return
        try:
            if self.btn_jog_cancel.cget("state") == "disabled":
                return
        except Exception:
            return
        self.grbl.jog_cancel()

    def _on_key_all_stop(self, _event=None):
        if not self._keyboard_binding_allowed():
            return
        try:
            if self.btn_all_stop.cget("state") == "disabled":
                return
        except Exception:
            return
        self._all_stop_action()

    def _on_key_binding(self, btn):
        if not self._keyboard_binding_allowed():
            return
        try:
            if btn.cget("state") == "disabled":
                return
        except Exception:
            return
        self._log_button_action(btn)
        self._invoke_button(btn)

    def _invoke_button(self, btn):
        if hasattr(btn, "invoke"):
            try:
                btn.invoke()
                return
            except Exception:
                pass
        try:
            cmd = btn.cget("command")
        except Exception:
            cmd = None
        if callable(cmd):
            cmd()

    def _log_button_action(self, btn):
        if not bool(self.gui_logging_enabled.get()):
            return
        label = self._button_label(btn)
        tip = getattr(btn, "_tooltip_text", "")
        gcode = ""
        try:
            getter = getattr(btn, "_log_gcode_get", None)
            if callable(getter):
                gcode = getter()
            elif isinstance(getter, str):
                gcode = getter
        except Exception:
            gcode = ""
        ts = time.strftime("%H:%M:%S")
        if tip and gcode:
            self._log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self._log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self._log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self._log(f"[{ts}] Button: {label}")

    def _update_current_highlight(self):
        mode = self.current_line_mode.get()
        idx = None
        if mode == "acked":
            if self._last_acked_index >= 0:
                idx = self._last_acked_index
            elif self._last_sent_index >= 0:
                idx = self._last_sent_index
        else:
            if self._last_sent_index >= 0:
                idx = self._last_sent_index
            elif self._last_acked_index >= 0:
                idx = self._last_acked_index
        if idx is not None:
            self.gview.highlight_current(idx)

    def _all_stop_action(self):
        mode = self.all_stop_mode.get()
        if mode == "reset":
            self.grbl.reset()
        else:
            self.grbl.stop_stream()

    def _all_stop_gcode_label(self):
        mode = self.all_stop_mode.get()
        if mode == "reset":
            return "Ctrl-X"
        return "Stop stream + Ctrl-X"

    def _on_fallback_rate_change(self, _event=None):
        if self._last_gcode_lines:
            self._update_gcode_stats(self._last_gcode_lines)

    def _parse_macro_prompt(self, line: str):
        title = "Macro Pause"
        message = ""
        buttons = []
        show_resume = True
        resume_label = "Resume"
        cancel_label = "Cancel"

        comment = re.search(r"\((.*?)\)", line)
        if comment:
            message = comment.group(1).strip()

        try:
            tokens = shlex.split(line)
        except Exception:
            tokens = line.split()
        tokens = tokens[1:] if tokens else []
        msg_parts = []
        for tok in tokens:
            low = tok.lower()
            if low in ("noresume", "no-resume"):
                show_resume = False
                continue
            if "=" in tok:
                key, val = tok.split("=", 1)
                key = key.lower()
                if key in ("title", "t"):
                    title = val
                elif key in ("msg", "message", "text"):
                    message = val
                elif key in ("buttons", "btns"):
                    raw = val.replace("|", ",")
                    buttons = [b.strip() for b in raw.split(",") if b.strip()]
                elif key in ("resume", "resumelabel"):
                    if val.lower() in ("0", "false", "no", "off"):
                        show_resume = False
                    else:
                        resume_label = val
                elif key in ("cancel", "cancellabel"):
                    cancel_label = val
                continue
            msg_parts.append(tok)

        if not message and msg_parts:
            message = " ".join(msg_parts)
        if not message:
            message = "Macro paused."

        extras = []
        for b in buttons:
            if b and b not in (resume_label, cancel_label):
                extras.append(b)

        choices = []
        if show_resume:
            choices.append(resume_label)
        choices.extend(extras)
        choices.append(cancel_label)
        return title, message, choices, cancel_label

    def _show_macro_prompt(
        self,
        title: str,
        message: str,
        choices: list[str],
        cancel_label: str,
        result_q: queue.Queue,
    ) -> None:
        try:
            dlg = tk.Toplevel(self)
            dlg.title(title)
            dlg.transient(self)
            dlg.grab_set()
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=12)
            frm.pack(fill="both", expand=True)
            lbl = ttk.Label(frm, text=message, wraplength=460, justify="left")
            lbl.pack(fill="x", pady=(0, 10))
            btn_row = ttk.Frame(frm)
            btn_row.pack(fill="x")

            def choose(label: str):
                if result_q.empty():
                    result_q.put(label)
                try:
                    dlg.destroy()
                except Exception:
                    pass

            for idx, lbl_text in enumerate(choices):
                b = ttk.Button(btn_row, text=lbl_text, command=lambda t=lbl_text: choose(t))
                set_kb_id(b, f"macro_prompt_{idx}")
                b.pack(side="left", padx=(0, 6))

            def on_close():
                choose(cancel_label)

            dlg.protocol("WM_DELETE_WINDOW", on_close)
        except Exception as exc:
            try:
                self._log(f"[macro] Prompt failed: {exc}")
            except Exception:
                pass
            if result_q.empty():
                result_q.put(cancel_label)

    # ---------- Zeroing (simple G92-based) ----------
    # If you prefer G10 L20 (persistent work offset), say so and Iâ€™ll swap these.
    def zero_x(self):
        self.grbl.send_immediate("G92 X0")

    def zero_y(self):
        self.grbl.send_immediate("G92 Y0")

    def zero_z(self):
        self.grbl.send_immediate("G92 Z0")

    def zero_all(self):
        self.grbl.send_immediate("G92 X0 Y0 Z0")

    def goto_zero(self):
        self.grbl.send_immediate("G0 X0 Y0")

    # ---------- UI event handling ----------
    def _log(self, s: str):
        self._console_lines.append(s)
        overflow = len(self._console_lines) - MAX_CONSOLE_LINES
        if overflow > 0:
            self._console_lines = self._console_lines[overflow:]
            if self._console_filter != "all":
                self._render_console()
                return
            self._trim_console_widget(overflow)
        if not self._console_filter_match(s):
            return
        self.console.config(state="normal")
        self.console.insert("end", s + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _console_filter_match(self, s: str) -> bool:
        if self._console_filter == "alarms":
            return "ALARM" in s.upper()
        if self._console_filter == "errors":
            return "ERROR" in s.upper()
        return True

    def _render_console(self):
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        for line in self._console_lines:
            if self._console_filter_match(line):
                self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _trim_console_widget(self, count: int):
        if count <= 0:
            return
        self.console.config(state="normal")
        try:
            self.console.delete("1.0", f"{count + 1}.0")
        except Exception:
            self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def _set_console_filter(self, mode):
        self._console_filter = mode
        self._render_console()

    def _bind_button_logging(self):
        self.bind_class("TButton", "<Button-1>", self._on_button_press, add="+")
        self.bind_class("Button", "<Button-1>", self._on_button_press, add="+")
        self.bind_class("Canvas", "<Button-1>", self._on_button_press, add="+")

    def _on_button_press(self, event):
        w = event.widget
        if isinstance(w, tk.Canvas) and not getattr(w, "_log_button", False):
            return
        try:
            if w.cget("state") == "disabled":
                return
        except Exception:
            pass
        if not bool(self.gui_logging_enabled.get()):
            return
        label = ""
        try:
            label = w.cget("text")
        except Exception:
            pass
        if not label:
            label = w.winfo_name()
        tip = ""
        try:
            tip = getattr(w, "_tooltip_text", "")
        except Exception:
            tip = ""
        gcode = ""
        try:
            getter = getattr(w, "_log_gcode_get", None)
            if callable(getter):
                gcode = getter()
            elif isinstance(getter, str):
                gcode = getter
        except Exception:
            gcode = ""
        ts = time.strftime("%H:%M:%S")
        if tip and gcode:
            self._log(f"[{ts}] Button: {label} | Tip: {tip} | GCode: {gcode}")
        elif tip:
            self._log(f"[{ts}] Button: {label} | Tip: {tip}")
        elif gcode:
            self._log(f"[{ts}] Button: {label} | GCode: {gcode}")
        else:
            self._log(f"[{ts}] Button: {label}")

    def _update_tab_visibility(self, nb=None):
        if nb is None:
            nb = getattr(self, "notebook", None)
        if not nb or not hasattr(self, "toolpath_view"):
            return
        try:
            tab_id = nb.select()
            label = nb.tab(tab_id, "text")
        except Exception:
            return
        self.toolpath_view.set_visible(label == "3D View")

    def _on_tab_changed(self, event):
        self._update_tab_visibility(event.widget)
        if not bool(self.gui_logging_enabled.get()):
            return
        nb = event.widget
        try:
            tab_id = nb.select()
            label = nb.tab(tab_id, "text")
        except Exception:
            return
        if not label:
            return
        ts = time.strftime("%H:%M:%S")
        self._log(f"[{ts}] Tab: {label}")

    def _drain_ui_queue(self):
        for _ in range(100):
            try:
                evt = self.ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_evt(evt)
            except Exception as exc:
                try:
                    self._log(f"[ui] Event error: {exc}")
                except Exception:
                    pass
        self._maybe_auto_reconnect()
        self.after(50, self._drain_ui_queue)

    def _toggle_tooltips(self):
        current = bool(self.tooltip_enabled.get())
        new_val = not current
        self.tooltip_enabled.set(new_val)
        self.btn_toggle_tips.config(text="Tool Tips: On" if new_val else "Tool Tips: Off")

    def _toggle_gui_logging(self):
        current = bool(self.gui_logging_enabled.get())
        new_val = not current
        self.gui_logging_enabled.set(new_val)
        self.btn_toggle_logging.config(text="Logging: On" if new_val else "Logging: Off")

    def _toggle_render_3d(self):
        current = bool(self.render3d_enabled.get())
        new_val = not current
        self.render3d_enabled.set(new_val)
        self.btn_toggle_3d.config(text="3D Render: On" if new_val else "3D Render: Off")
        if hasattr(self, "toolpath_view"):
            self.toolpath_view.set_enabled(new_val)
            if new_val and self._last_gcode_lines:
                self.toolpath_view.set_gcode_async(self._last_gcode_lines)

    def _toggle_unit_mode(self):
        new_mode = "inch" if self.unit_mode.get() == "mm" else "mm"
        self._set_unit_mode(new_mode)


    def _confirm_and_run(self, label: str, func):
        """Optionally wrap an action with a confirmation dialog based on Training Wheels setting."""
        try:
            need_confirm = bool(self.training_wheels.get())
        except Exception:
            need_confirm = False
        now = time.time()
        last_ts = self._confirm_last_time.get(label, 0.0)
        if need_confirm:
            if (now - last_ts) < self._confirm_debounce_sec:
                return
            if not messagebox.askyesno("Confirm", f"{label}?"):
                return
        self._confirm_last_time[label] = now
        func()

    def _save_3d_view(self):
        if not hasattr(self, "toolpath_view"):
            return
        self.settings["view_3d"] = self.toolpath_view.get_view()
        self.status.config(text="3D view saved")

    def _load_3d_view(self, show_status: bool = True):
        view = self.settings.get("view_3d")
        if not view or not hasattr(self, "toolpath_view"):
            return
        self.toolpath_view.apply_view(view)
        if show_status:
            self.status.config(text="3D view loaded")

    def _schedule_gcode_mark_flush(self):
        if self._pending_marks_after_id is not None:
            return
        self._pending_marks_after_id = self.after(self._ui_throttle_ms, self._flush_gcode_marks)

    def _flush_gcode_marks(self):
        self._pending_marks_after_id = None
        sent_idx = self._pending_sent_index
        acked_idx = self._pending_acked_index
        self._pending_sent_index = None
        self._pending_acked_index = None
        if sent_idx is not None:
            self.gview.mark_sent_upto(sent_idx)
            self._last_sent_index = sent_idx
        if acked_idx is not None:
            self.gview.mark_acked_upto(acked_idx)
            self._last_acked_index = acked_idx
            if sent_idx is None and acked_idx > self._last_sent_index:
                self._last_sent_index = acked_idx
        if sent_idx is not None or acked_idx is not None:
            self._update_current_highlight()

    def _schedule_progress_flush(self):
        if self._progress_after_id is not None:
            return
        self._progress_after_id = self.after(self._ui_throttle_ms, self._flush_progress)

    def _flush_progress(self):
        self._progress_after_id = None
        if not self._pending_progress:
            return
        done, total = self._pending_progress
        self._pending_progress = None
        self.status.config(text=f"Progress: {done}/{total}")
        if total:
            self.progress_pct.set(int(round((done / total) * 100)))
        if done and total:
            self._update_live_estimate(done, total)

    def _schedule_buffer_flush(self):
        if self._buffer_after_id is not None:
            return
        self._buffer_after_id = self.after(self._ui_throttle_ms, self._flush_buffer_fill)

    def _flush_buffer_fill(self):
        self._buffer_after_id = None
        if not self._pending_buffer:
            return
        pct, used, window = self._pending_buffer
        self._pending_buffer = None
        self.buffer_fill.set(f"Buffer: {pct}% ({used}/{window})")
        self.buffer_fill_pct.set(pct)

    def _clear_pending_ui_updates(self):
        for attr in ("_pending_marks_after_id", "_progress_after_id", "_buffer_after_id"):
            after_id = getattr(self, attr, None)
            if after_id is None:
                continue
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            setattr(self, attr, None)
        self._pending_sent_index = None
        self._pending_acked_index = None
        self._pending_progress = None
        self._pending_buffer = None

    def _handle_evt(self, evt):
        kind = evt[0]

        if kind == "conn":
            is_on = evt[1]
            port = evt[2]
            self.connected = is_on

            if is_on:
                self._auto_reconnect_last_port = port or self._auto_reconnect_last_port
                self._auto_reconnect_pending = False
                self._auto_reconnect_last_attempt = 0.0
                self._auto_reconnect_retry = 0
                self._auto_reconnect_delay = 3.0
                self._auto_reconnect_next_ts = 0.0
                self.btn_conn.config(text="Disconnect")
                self._connected_port = port
                self._grbl_ready = False
                self._alarm_locked = False
                self._alarm_message = ""
                self._pending_settings_refresh = True
                self._status_seen = False
                self.machine_state.set(f"CONNECTED ({port})")
                self._machine_state_text = f"CONNECTED ({port})"
                self.status.config(text=f"Connected: {port} (waiting for Grbl)")
                self.btn_stop.config(state="normal")
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self._set_manual_controls_enabled(False)
            else:
                self.btn_conn.config(text="Connect")
                self._connected_port = None
                self._grbl_ready = False
                self._alarm_locked = False
                self._alarm_message = ""
                self._pending_settings_refresh = False
                self._status_seen = False
                self.machine_state.set("DISCONNECTED")
                self._machine_state_text = "DISCONNECTED"
                self.status.config(text="Disconnected")
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.btn_stop.config(state="disabled")
                self._set_manual_controls_enabled(True)
                self._rapid_rates = None
                self._rapid_rates_source = None
                self._accel_rates = None
                if self._last_gcode_lines:
                    self._update_gcode_stats(self._last_gcode_lines)
                if not self._user_disconnect:
                    self._auto_reconnect_pending = True
                    self._auto_reconnect_retry = 0
                    self._auto_reconnect_delay = 3.0
                    self._auto_reconnect_next_ts = 0.0
                self._user_disconnect = False

        elif kind == "ui_call":
            func, args, kwargs, result_q = evt[1], evt[2], evt[3], evt[4]
            try:
                result_q.put((True, func(*args, **kwargs)))
            except Exception as exc:
                result_q.put((False, exc))

        elif kind == "macro_prompt":
            title, message, choices, cancel_label, result_q = evt[1], evt[2], evt[3], evt[4], evt[5]
            try:
                self._show_macro_prompt(title, message, choices, cancel_label, result_q)
            except Exception as exc:
                try:
                    self._log(f"[macro] Prompt failed: {exc}")
                except Exception:
                    pass
                if result_q.empty():
                    result_q.put(cancel_label)

        elif kind == "gcode_loaded":
            token, path, lines = evt[1], evt[2], evt[3]
            if token != self._gcode_load_token:
                return
            self._apply_loaded_gcode(path, lines)

        elif kind == "gcode_load_error":
            token, path, err = evt[1], evt[2], evt[3]
            if token != self._gcode_load_token:
                return
            self._gcode_loading = False
            self._finish_gcode_loading()
            self.gcode_stats_var.set("No file loaded")
            messagebox.showerror("Open G-code", f"Failed to read file:\n{err}")
            self.status.config(text="G-code load failed")

        elif kind == "log":
            self._log(evt[1])

        elif kind == "log_tx":
            self._log(f">> {evt[1]}")

        elif kind == "log_rx":
            self._log(f"<< {evt[1]}")
            self._handle_settings_line(evt[1])

        elif kind == "ready":
            self._grbl_ready = bool(evt[1])
            if not self._grbl_ready:
                self._status_seen = False
                self._alarm_locked = False
                self._alarm_message = ""
                if self.connected:
                    self.btn_run.config(state="disabled")
                    self.btn_pause.config(state="disabled")
                    self.btn_resume.config(state="disabled")
                    self._set_manual_controls_enabled(False)
                    if self._connected_port:
                        self.status.config(text=f"Connected: {self._connected_port} (waiting for Grbl)")
                return
            if self._alarm_locked:
                return
            if self.connected and self._connected_port:
                self.status.config(text=f"Connected: {self._connected_port}")

        elif kind == "alarm":
            msg = evt[1] if len(evt) > 1 else ""
            self._set_alarm_lock(True, msg)

        elif kind == "status":
            # Parse minimal fields: state + WPos if present
            s = evt[1].strip("<>")
            parts = s.split("|")
            state = parts[0] if parts else "?"
            self._status_seen = True
            wpos = None
            mpos = None
            feed = None
            spindle = None
            planner = None
            rxbytes = None
            wco = None
            ov = None
            pins = None
            for p in parts:
                if p.startswith("WPos:"):
                    wpos = p[5:]
                elif p.startswith("MPos:"):
                    mpos = p[5:]
                elif p.startswith("FS:"):
                    try:
                        f_str, s_str = p[3:].split(",", 1)
                        feed = float(f_str)
                        spindle = float(s_str)
                    except Exception:
                        pass
                elif p.startswith("Bf:"):
                    try:
                        bf_planner, bf_rx = p[3:].split(",", 1)
                        planner = int(bf_planner)
                        rxbytes = int(bf_rx)
                    except Exception:
                        pass
                elif p.startswith("WCO:"):
                    wco = p[4:]
                elif p.startswith("Ov:"):
                    ov = p[3:]
                elif p.startswith("Pn:"):
                    pins = p[3:]

            self.machine_state.set(state)
            self._machine_state_text = state
            if str(state).lower().startswith("alarm"):
                self._set_alarm_lock(True, state)
            elif self._alarm_locked:
                self._set_alarm_lock(False)
            if self._grbl_ready and self._pending_settings_refresh and not self._alarm_locked:
                self._pending_settings_refresh = False
                self._request_settings_dump()
            if (
                self.connected
                and self._grbl_ready
                and self._status_seen
                and not self._alarm_locked
                and self._stream_state not in ("running", "paused")
            ):
                self._set_manual_controls_enabled(True)
                if self.gview.lines_count:
                    self.btn_run.config(state="normal")
            with self._macro_vars_lock:
                self._macro_vars["state"] = state
            def parse_xyz(text: str):
                parts = text.split(",")
                if len(parts) < 3:
                    return None
                try:
                    return [float(parts[0]), float(parts[1]), float(parts[2])]
                except Exception:
                    return None

            wco_vals = parse_xyz(wco) if wco else None
            mpos_vals = parse_xyz(mpos) if mpos else None
            wpos_vals = parse_xyz(wpos) if wpos else None

            if mpos_vals and wpos_vals is None and wco_vals:
                wpos_vals = [
                    mpos_vals[0] - wco_vals[0],
                    mpos_vals[1] - wco_vals[1],
                    mpos_vals[2] - wco_vals[2],
                ]
                wpos = ",".join(f"{v:.3f}" for v in wpos_vals)
            elif wpos_vals and mpos_vals is None and wco_vals:
                mpos_vals = [
                    wpos_vals[0] + wco_vals[0],
                    wpos_vals[1] + wco_vals[1],
                    wpos_vals[2] + wco_vals[2],
                ]
                mpos = ",".join(f"{v:.3f}" for v in mpos_vals)

            if mpos:
                try:
                    x, y, z = mpos.split(",")
                    self.mpos_x.set(x)
                    self.mpos_y.set(y)
                    self.mpos_z.set(z)
                    with self._macro_vars_lock:
                        self._macro_vars["mx"] = float(x)
                        self._macro_vars["my"] = float(y)
                        self._macro_vars["mz"] = float(z)
                except Exception:
                    pass
            if wpos:
                try:
                    x, y, z = wpos.split(",")
                    self.wpos_x.set(x)
                    self.wpos_y.set(y)
                    self.wpos_z.set(z)
                    with self._macro_vars_lock:
                        self._macro_vars["wx"] = float(x)
                        self._macro_vars["wy"] = float(y)
                        self._macro_vars["wz"] = float(z)
                    try:
                        if hasattr(self, "toolpath_view"):
                            self.toolpath_view.set_position(float(x), float(y), float(z))
                    except Exception:
                        pass
                except Exception:
                    pass
            if feed is not None:
                with self._macro_vars_lock:
                    self._macro_vars["curfeed"] = feed
            if spindle is not None:
                with self._macro_vars_lock:
                    self._macro_vars["curspindle"] = spindle
            if planner is not None:
                with self._macro_vars_lock:
                    self._macro_vars["planner"] = planner
            if rxbytes is not None:
                with self._macro_vars_lock:
                    self._macro_vars["rxbytes"] = rxbytes
            if wco_vals:
                with self._macro_vars_lock:
                    self._macro_vars["wcox"] = wco_vals[0]
                    self._macro_vars["wcoy"] = wco_vals[1]
                    self._macro_vars["wcoz"] = wco_vals[2]
            if pins is not None:
                with self._macro_vars_lock:
                    self._macro_vars["pins"] = pins
            if ov:
                try:
                    ov_parts = [int(float(v)) for v in ov.split(",")]
                    if len(ov_parts) >= 3:
                        with self._macro_vars_lock:
                            changed = (
                                self._macro_vars.get("OvFeed") != ov_parts[0]
                                or self._macro_vars.get("OvRapid") != ov_parts[1]
                                or self._macro_vars.get("OvSpindle") != ov_parts[2]
                            )
                            self._macro_vars["OvFeed"] = ov_parts[0]
                            self._macro_vars["OvRapid"] = ov_parts[1]
                            self._macro_vars["OvSpindle"] = ov_parts[2]
                            self._macro_vars["_OvChanged"] = bool(changed)
                except Exception:
                    pass
        elif kind == "buffer_fill":
            pct, used, window = evt[1], evt[2], evt[3]
            self._pending_buffer = (pct, used, window)
            self._schedule_buffer_flush()

        elif kind == "stream_state":
            st = evt[1]
            prev = self._stream_state
            now = time.time()
            self._stream_state = st
            if st == "running":
                if prev == "paused":
                    if self._stream_paused_at is not None:
                        self._stream_pause_total += max(0.0, now - self._stream_paused_at)
                        self._stream_paused_at = None
                elif prev != "running":
                    self._stream_start_ts = now
                    self._stream_pause_total = 0.0
                    self._stream_paused_at = None
                    self._live_estimate_min = None
                    self._refresh_gcode_stats_display()
            elif st == "paused":
                if self._stream_paused_at is None:
                    self._stream_paused_at = now
            elif st in ("done", "stopped", "error", "alarm"):
                self._stream_start_ts = None
                self._stream_pause_total = 0.0
                self._stream_paused_at = None
                self._live_estimate_min = None
                self._refresh_gcode_stats_display()
            if st == "loaded":
                self.progress_pct.set(0)
                total = evt[2]
                with self._macro_vars_lock:
                    self._macro_vars["running"] = False
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                if (
                    self.connected
                    and total
                    and self._grbl_ready
                    and self._status_seen
                    and not self._alarm_locked
                ):
                    self.btn_run.config(state="normal")
                else:
                    self.btn_run.config(state="disabled")
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "running":
                with self._macro_vars_lock:
                    self._macro_vars["running"] = True
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="normal")
                self.btn_resume.config(state="disabled")
                self._set_manual_controls_enabled(False)
                self._set_streaming_lock(True)
            elif st == "paused":
                with self._macro_vars_lock:
                    self._macro_vars["running"] = True
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="normal")
                self._set_manual_controls_enabled(False)
                self._set_streaming_lock(True)
            elif st in ("done", "stopped"):
                with self._macro_vars_lock:
                    self._macro_vars["running"] = False
                if st == "done":
                    self.progress_pct.set(100)
                else:
                    self.progress_pct.set(0)
                self.btn_run.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "error":
                with self._macro_vars_lock:
                    self._macro_vars["running"] = False
                self.progress_pct.set(0)
                self.btn_run.config(
                    state="normal"
                    if (
                        self.connected
                        and self.gview.lines_count
                        and self._grbl_ready
                        and self._status_seen
                        and not self._alarm_locked
                    )
                    else "disabled"
                )
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self.status.config(text=f"Stream error: {evt[2]}")
                self._set_manual_controls_enabled((not self.connected) or (self._grbl_ready and self._status_seen))
                self._set_streaming_lock(False)
            elif st == "alarm":
                with self._macro_vars_lock:
                    self._macro_vars["running"] = False
                self.progress_pct.set(0)
                self.btn_run.config(state="disabled")
                self.btn_pause.config(state="disabled")
                self.btn_resume.config(state="disabled")
                self._set_alarm_lock(True, evt[2] if len(evt) > 2 else None)
                self._set_streaming_lock(False)

        elif kind == "gcode_sent":
            idx = evt[1]
            if self._pending_sent_index is None or idx > self._pending_sent_index:
                self._pending_sent_index = idx
            self._schedule_gcode_mark_flush()

        elif kind == "gcode_acked":
            idx = evt[1]
            if self._pending_acked_index is None or idx > self._pending_acked_index:
                self._pending_acked_index = idx
            self._schedule_gcode_mark_flush()

        elif kind == "progress":
            done, total = evt[1], evt[2]
            self._pending_progress = (done, total)
            self._schedule_progress_flush()

    def _on_close(self):
        self._closing = True
        try:
            self._save_settings()
            self.grbl.disconnect()
        except Exception:
            pass
        self.destroy()

    def _macro_path(self, index: int) -> str | None:
        base_dir = os.path.dirname(__file__)
        for prefix in MACRO_PREFIXES:
            for ext in MACRO_EXTS:
                candidate = os.path.join(base_dir, f"{prefix}{index}{ext}")
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _load_macro_buttons(self):
        self._macro_buttons = []

        left = self.macro_frames["left"]
        right = self.macro_frames["right"]
        for w in left.winfo_children():
            w.destroy()
        for w in right.winfo_children():
            w.destroy()

        for idx in (1, 2, 3):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(left, text=name, command=lambda i=idx: self._run_macro(i))
            btn._kb_id = f"macro_{idx}"
            btn.pack(fill="x", pady=(0, 4))
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self._manual_controls.append(btn)
            self._macro_buttons.append(btn)

        col = 0
        row = 0
        for idx in (4, 5, 6, 7):
            path = self._macro_path(idx)
            if not path:
                continue
            name, tip = self._read_macro_header(path, idx)
            btn = ttk.Button(right, text=name, command=lambda i=idx: self._run_macro(i))
            btn._kb_id = f"macro_{idx}"
            btn.grid(row=row, column=col, padx=4, pady=2, sticky="ew")
            right.grid_columnconfigure(col, weight=1)
            apply_tooltip(btn, tip)
            btn.bind("<Button-3>", lambda e, i=idx: self._preview_macro(i))
            self._manual_controls.append(btn)
            self._macro_buttons.append(btn)
            col += 1
            if col > 1:
                col = 0
                row += 1
        self._refresh_keyboard_table()

    def _read_macro_header(self, path: str, index: int) -> tuple[str, str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                name = f.readline().strip()
                tip = f.readline().strip()
            if not name:
                name = f"Macro {index}"
            return name, tip
        except Exception:
            return f"Macro {index}", ""

    def _show_macro_preview(self, name: str, lines: list[str]) -> None:
        """Modal preview of macro contents (view-only)."""
        body = "".join(lines[2:]) if len(lines) > 2 else ""
        dlg = tk.Toplevel(self)
        dlg.title(f"Macro Preview - {name}")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(True, True)
        frame = ttk.Frame(dlg, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=name, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 6))
        text = tk.Text(frame, wrap="word", height=14, width=80, state="normal")
        text.insert("end", body)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="left", padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.wait_window()

    def _preview_macro(self, index: int):
        path = self._macro_path(index)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Macro error", str(e))
            return
        name = lines[0].strip() if lines else f"Macro {index}"
        self._show_macro_preview(name, lines)

    def _run_macro(self, index: int):
        if self.grbl.is_streaming():
            messagebox.showwarning("Macro blocked", "Stop the stream before running a macro.")
            return
        path = self._macro_path(index)
        if not path:
            return

        if not self._macro_lock.acquire(blocking=False):
            messagebox.showwarning("Macro busy", "Another macro is running.")
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Macro error", str(e))
            self._macro_lock.release()
            return

        name = lines[0].strip() if lines else f"Macro {index}"
        tip = lines[1].strip() if len(lines) > 1 else ""
        ts = time.strftime("%H:%M:%S")
        if bool(self.gui_logging_enabled.get()):
            if tip:
                self._log(f"[{ts}] Macro: {name} | Tip: {tip}")
            else:
                self._log(f"[{ts}] Macro: {name}")
            self._log(f"[{ts}] Macro contents:")
            for raw in lines[2:]:
                self._log(f"[{ts}]   {raw.rstrip()}")

        t = threading.Thread(target=self._run_macro_worker, args=(lines,), daemon=True)
        t.start()

    def _run_macro_worker(self, lines: list[str]):
        try:
            with self._macro_vars_lock:
                self._macro_local_vars["app"] = self
                self._macro_local_vars["os"] = os
            for raw in lines[2:]:
                line = raw.strip()
                if not line:
                    continue
                compiled = self._bcnc_compile_line(line)
                if compiled is None:
                    continue
                if isinstance(compiled, tuple):
                    kind = compiled[0]
                    if kind == "WAIT":
                        self._macro_wait_for_idle()
                    elif kind == "MSG":
                        msg = compiled[1] if len(compiled) > 1 else ""
                        if msg:
                            self.ui_q.put(("log", f"[macro] {msg}"))
                    elif kind == "UPDATE":
                        self.grbl.send_realtime(RT_STATUS)
                    continue

                evaluated = self._bcnc_evaluate_line(compiled)
                if evaluated is None:
                    continue
                if not self._execute_bcnc_command(evaluated):
                    break
        finally:
            self._macro_lock.release()

    def _macro_wait_for_idle(self, timeout_s: float = 30.0):
        if not self.grbl.is_connected():
            return
        start = time.time()
        while True:
            if not self.grbl.is_connected():
                return
            if not self.grbl.is_streaming() and str(self._machine_state_text).startswith("Idle"):
                return
            if timeout_s and (time.time() - start) > timeout_s:
                self.ui_q.put(("log", "[macro] %wait timeout"))
                return
            time.sleep(0.1)

    def _bcnc_compile_line(self, line: str):
        line = line.strip()
        if not line:
            return None
        if line[0] == "$":
            return line

        line = line.replace("#", "_")

        if line[0] == "%":
            pat = MACRO_AUXPAT.match(line.strip())
            if pat:
                cmd = pat.group(1)
                args = pat.group(2)
            else:
                cmd = None
                args = None
            if cmd == "%wait":
                return ("WAIT",)
            if cmd == "%msg":
                return ("MSG", args if args else "")
            if cmd == "%update":
                return ("UPDATE", args if args else "")
            if line.startswith("%if running"):
                with self._macro_vars_lock:
                    if not self._macro_vars.get("running"):
                        return None
            try:
                return compile(line[1:], "", "exec")
            except Exception:
                return None

        if line[0] == "_":
            try:
                return compile(line, "", "exec")
            except Exception:
                return None

        if line[0] == ";":
            return None

        out = []
        bracket = 0
        paren = 0
        expr = ""
        cmd = ""
        in_comment = False
        for i, ch in enumerate(line):
            if ch == "(":
                paren += 1
                in_comment = bracket == 0
                if not in_comment:
                    expr += ch
            elif ch == ")":
                paren -= 1
                if not in_comment:
                    expr += ch
                if paren == 0 and in_comment:
                    in_comment = False
            elif ch == "[":
                if not in_comment:
                    if MACRO_STDEXPR:
                        ch = "("
                    bracket += 1
                    if bracket == 1:
                        if cmd:
                            out.append(cmd)
                            cmd = ""
                    else:
                        expr += ch
                else:
                    pass
            elif ch == "]":
                if not in_comment:
                    if MACRO_STDEXPR:
                        ch = ")"
                    bracket -= 1
                    if bracket == 0:
                        try:
                            out.append(compile(expr, "", "eval"))
                        except Exception:
                            pass
                        expr = ""
                    else:
                        expr += ch
            elif ch == "=":
                if not out and bracket == 0 and paren == 0:
                    for t in " ()-+*/^$":
                        if t in cmd:
                            cmd += ch
                            break
                    else:
                        try:
                            return compile(line, "", "exec")
                        except Exception:
                            return None
                else:
                    cmd += ch
            elif ch == ";":
                if not in_comment and paren == 0 and bracket == 0:
                    break
                else:
                    expr += ch
            elif bracket > 0:
                expr += ch
            elif not in_comment:
                cmd += ch
            else:
                pass

        if cmd:
            out.append(cmd)
        if not out:
            return None
        if len(out) > 1:
            return out
        return out[0]

    def _bcnc_evaluate_line(self, compiled):
        if isinstance(compiled, int):
            return None
        if isinstance(compiled, list):
            for i, expr in enumerate(compiled):
                if isinstance(expr, types.CodeType):
                    with self._macro_vars_lock:
                        globals_ctx = self._macro_eval_globals()
                        result = eval(expr, globals_ctx, self._macro_local_vars)
                    if isinstance(result, float):
                        compiled[i] = str(round(result, 4))
                    else:
                        compiled[i] = str(result)
            return "".join(compiled)
        if isinstance(compiled, types.CodeType):
            with self._macro_vars_lock:
                globals_ctx = self._macro_exec_globals()
                return eval(compiled, globals_ctx, self._macro_local_vars)
        return compiled

    def _macro_eval_globals(self) -> dict:
        return self._macro_vars

    def _macro_exec_globals(self) -> dict:
        return self._macro_vars

    def _call_on_ui_thread(self, func, *args, timeout: float | None = 5.0, **kwargs):
        if threading.current_thread() is threading.main_thread():
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                self.ui_q.put(("log", f"[ui] Action failed: {exc}"))
                return None
        result_q: queue.Queue = queue.Queue()
        self.ui_q.put(("ui_call", func, args, kwargs, result_q))
        try:
            if timeout is None:
                while True:
                    try:
                        ok, value = result_q.get(timeout=0.2)
                        break
                    except queue.Empty:
                        if self._closing:
                            self.ui_q.put(("log", "[ui] Action canceled (closing)."))
                            return None
            else:
                ok, value = result_q.get(timeout=timeout)
        except queue.Empty:
            self.ui_q.put(("log", "[ui] Action timed out."))
            return None
        if ok:
            return value
        self.ui_q.put(("log", f"[ui] Action failed: {value}"))
        return None

    def _execute_bcnc_command(self, line: str):
        if line is None:
            return True
        if isinstance(line, tuple):
            return True
        s = str(line).strip()
        if not s:
            return True
        cmd_parts = s.replace(",", " ").split()
        cmd = cmd_parts[0].upper()

        if cmd in ("M0", "M00", "PROMPT"):
            title, message, choices, cancel_label = self._parse_macro_prompt(s)
            result_q: queue.Queue[str] = queue.Queue()
            self.ui_q.put(("macro_prompt", title, message, choices, cancel_label, result_q))
            while True:
                try:
                    choice = result_q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if self._closing:
                        choice = cancel_label
                        break
            if choice not in choices:
                choice = cancel_label
            with self._macro_vars_lock:
                self._macro_vars["prompt_choice"] = choice
                self._macro_vars["prompt_index"] = choices.index(choice) if choice in choices else -1
                self._macro_vars["prompt_cancelled"] = (choice == cancel_label)
            self.ui_q.put(("log", f"[macro] Prompt: {message} | Selected: {choice}"))
            if choice == cancel_label:
                self.ui_q.put(("log", "[macro] Prompt canceled; macro aborted."))
                return False
            return True

        if cmd in ("ABSOLUTE", "ABS"):
            self.grbl.send_immediate("G90")
            return True
        if cmd in ("RELATIVE", "REL"):
            self.grbl.send_immediate("G91")
            return True
        if cmd == "HOME":
            self.grbl.home()
            return True
        if cmd == "OPEN":
            if not self.connected:
                self._call_on_ui_thread(self.toggle_connect)
            return True
        if cmd == "CLOSE":
            if self.connected:
                self._call_on_ui_thread(self.toggle_connect)
            return True
        if cmd == "HELP":
            self._call_on_ui_thread(
                messagebox.showinfo,
                "Macro",
                "Help is not available in this sender.",
                timeout=None,
            )
            return True
        if cmd in ("QUIT", "EXIT"):
            self._call_on_ui_thread(self._on_close)
            return True
        if cmd == "LOAD" and len(cmd_parts) > 1:
            self._call_on_ui_thread(
                self._load_gcode_from_path,
                " ".join(cmd_parts[1:]),
                timeout=None,
            )
            return True
        if cmd == "UNLOCK":
            self.grbl.unlock()
            return True
        if cmd == "RESET":
            self.grbl.reset()
            return True
        if cmd == "PAUSE":
            self.grbl.hold()
            return True
        if cmd == "RESUME":
            self.grbl.resume()
            return True
        if cmd == "FEEDHOLD":
            self.grbl.hold()
            return True
        if cmd == "STOP":
            self.grbl.stop_stream()
            return True
        if cmd == "RUN":
            self.grbl.start_stream()
            return True
        if cmd == "SAVE":
            self.ui_q.put(("log", "[macro] SAVE is not supported."))
            return True
        if cmd == "SENDHEX" and len(cmd_parts) > 1:
            try:
                b = bytes([int(cmd_parts[1], 16)])
                self.grbl.send_realtime(b)
            except Exception:
                pass
            return True
        if cmd == "SAFE" and len(cmd_parts) > 1:
            try:
                with self._macro_vars_lock:
                    self._macro_vars["safe"] = float(cmd_parts[1])
            except Exception:
                pass
            return True
        if cmd == "SET0":
            self.grbl.send_immediate("G92 X0 Y0 Z0")
            return True
        if cmd == "SETX" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 X{cmd_parts[1]}")
            return True
        if cmd == "SETY" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 Y{cmd_parts[1]}")
            return True
        if cmd == "SETZ" and len(cmd_parts) > 1:
            self.grbl.send_immediate(f"G92 Z{cmd_parts[1]}")
            return True
        if cmd == "SET":
            parts = []
            if len(cmd_parts) > 1:
                parts.append(f"X{cmd_parts[1]}")
            if len(cmd_parts) > 2:
                parts.append(f"Y{cmd_parts[2]}")
            if len(cmd_parts) > 3:
                parts.append(f"Z{cmd_parts[3]}")
            if parts:
                self.grbl.send_immediate("G92 " + " ".join(parts))
            return True

        if s.startswith("!"):
            self.grbl.hold()
            return True
        if s.startswith("~"):
            self.grbl.resume()
            return True
        if s.startswith("?"):
            self.grbl.send_realtime(RT_STATUS)
            return True
        if s.startswith("\x18"):
            self.grbl.reset()
            return True

        if s.startswith("$") or s.startswith("@") or s.startswith("{"):
            self.grbl.send_immediate(s)
            return True
        if s.startswith("(") or MACRO_GPAT.match(s):
            self.grbl.send_immediate(s)
            return True
        self.grbl.send_immediate(s)
        return True

    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_settings(self):
        def safe_float(var, default, label):
            try:
                return float(var.get())
            except Exception:
                try:
                    fallback = float(default)
                except Exception:
                    fallback = 0.0
                self.ui_q.put(("log", f"[settings] Invalid {label}; using {fallback}."))
                return fallback

        data = {
            "last_port": self.current_port.get(),
            "unit_mode": self.unit_mode.get(),
            "step_xy": safe_float(self.step_xy, self.settings.get("step_xy", 1.0), "step XY"),
            "step_z": safe_float(self.step_z, self.settings.get("step_z", 1.0), "step Z"),
            "jog_feed": safe_float(self.jog_feed, self.settings.get("jog_feed", 800.0), "jog feed"),
            "last_gcode_dir": self.settings.get("last_gcode_dir", ""),
            "window_geometry": self.geometry(),
            "tooltips_enabled": bool(self.tooltip_enabled.get()),
            "gui_logging_enabled": bool(self.gui_logging_enabled.get()),
            "render3d_enabled": bool(self.render3d_enabled.get()),
            "view_3d": self.settings.get("view_3d"),
            "all_stop_mode": self.all_stop_mode.get(),
            "training_wheels": bool(self.training_wheels.get()),
            "reconnect_on_open": bool(self.reconnect_on_open.get()),
            "fallback_rapid_rate": self.fallback_rapid_rate.get().strip(),
            "estimate_factor": safe_float(self.estimate_factor, self.settings.get("estimate_factor", 1.0), "estimate factor"),
            "keyboard_bindings_enabled": bool(self.keyboard_bindings_enabled.get()),
            "current_line_mode": self.current_line_mode.get(),
            "key_bindings": dict(self._key_bindings),
            "last_port": self.current_port.get(),
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except Exception as exc:
            try:
                self.ui_q.put(("log", f"[settings] Save failed: {exc}"))
                self.status.config(text="Settings save failed")
            except Exception:
                pass


if __name__ == "__main__":
    App().mainloop()
