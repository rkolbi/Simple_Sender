from tkinter import messagebox

from simple_sender.gcode_parser import clean_gcode_line
from simple_sender.utils.constants import RESUME_WORD_PAT


def build_resume_preamble(lines: list[str], stop_index: int) -> tuple[list[str], bool]:
    units = None
    distance = None
    plane = None
    feed_mode = None
    arc_mode = None
    coord = None
    spindle = None
    coolant = None
    feed = None
    spindle_speed = None
    has_g92 = False

    def is_code(code: float, target: float) -> bool:
        return abs(code - target) < 1e-3

    for raw in lines[: max(0, stop_index)]:
        s = clean_gcode_line(raw)
        if not s:
            continue
        s = s.upper()
        for w, val in RESUME_WORD_PAT.findall(s):
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
                    has_g92 = True
                    continue
                gstr = f"G{val}"
                if is_code(code, 20) or is_code(code, 21):
                    units = gstr
                elif is_code(code, 90) or is_code(code, 91):
                    distance = gstr
                elif is_code(code, 17) or is_code(code, 18) or is_code(code, 19):
                    plane = gstr
                elif is_code(code, 93) or is_code(code, 94):
                    feed_mode = gstr
                elif is_code(code, 90.1) or is_code(code, 91.1):
                    arc_mode = gstr
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
                    coord = gstr
            elif w == "M":
                try:
                    code = int(float(val))
                except Exception:
                    continue
                if code in (3, 4, 5):
                    spindle = code
                elif code in (7, 8, 9):
                    coolant = code
            elif w == "F":
                try:
                    feed = float(val)
                except Exception:
                    pass
            elif w == "S":
                try:
                    spindle_speed = float(val)
                except Exception:
                    pass

    preamble = []
    for item in (units, distance, plane, arc_mode, feed_mode, coord):
        if item:
            preamble.append(item)
    if feed is not None:
        preamble.append(f"F{feed:g}")
    if spindle is not None:
        if spindle in (3, 4):
            if spindle_speed is not None:
                preamble.append(f"M{spindle} S{spindle_speed:g}")
            else:
                preamble.append(f"M{spindle}")
        else:
            preamble.append("M5")
    if coolant is not None:
        preamble.append(f"M{coolant}")
    return preamble, has_g92


def resume_from_line(app, start_index: int, preamble: list[str]):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before resuming.")
        return
    if not app._require_grbl_connection():
        return
    if not app._grbl_ready:
        messagebox.showwarning("Not ready", "Wait for GRBL to be ready.")
        return
    if app._alarm_locked:
        messagebox.showwarning("Alarm", "Clear the alarm before resuming.")
        return
    if not app._last_gcode_lines:
        messagebox.showwarning("No G-code", "Load a G-code file first.")
        return
    if start_index < 0 or start_index >= len(app._last_gcode_lines):
        messagebox.showwarning("Resume", "Line number is out of range.")
        return
    app.grbl.set_dry_run_sanitize(bool(app.dry_run_sanitize_stream.get()))
    app._clear_pending_ui_updates()
    app.gview.clear_highlights()
    app._last_sent_index = start_index - 1
    app._last_acked_index = start_index - 1
    if start_index > 0:
        app.gview.mark_acked_upto(start_index - 1)
    app.gview.highlight_current(start_index)
    if len(app._last_gcode_lines) > 0:
        pct = int(round((start_index / len(app._last_gcode_lines)) * 100))
        app.progress_pct.set(pct)
    app.status.config(text=f"Resuming at line {start_index + 1}")
    app.grbl.start_stream_from(start_index, preamble)
