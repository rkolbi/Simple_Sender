import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Set, Tuple

PAREN_COMMENT_PAT = re.compile(r"\(.*?\)")
WORD_PAT = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")


@dataclass
class GcodeMove:
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    motion: int
    feed: float | None
    feed_mode: str
    dx: float
    dy: float
    dz: float
    dist: float
    arc_len: float | None


@dataclass
class GcodeParseResult:
    segments: List[tuple[float, float, float, float, float, float, str]]
    bounds: tuple[float, float, float, float, float, float] | None
    moves: List[GcodeMove]


def clean_gcode_line(line: str) -> str:
    """Strip comments and whitespace; keep simple + safe."""
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


def _arc_sweep(
    u0: float, v0: float, u1: float, v1: float, cu: float, cv: float, cw: bool
) -> float:
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


def parse_gcode_lines(
    lines: Iterable[str],
    arc_step_rad: float = math.pi / 18,
    keep_running: Optional[Callable[[], bool]] = None,
) -> Optional[GcodeParseResult]:
    arc_step_rad = max(1e-6, arc_step_rad)
    x = y = z = 0.0
    units = 1.0
    absolute = True
    plane = "G17"
    feed_mode = "G94"
    arc_abs = False
    feed_raw: float | None = None
    feed_mm: float | None = None
    g92_offset = [0.0, 0.0, 0.0]
    g92_enabled = True
    last_motion = 1
    segments: List[tuple[float, float, float, float, float, float, str]] = []
    moves: List[GcodeMove] = []
    minx = miny = minz = None
    maxx = maxy = maxz = None

    def update_bounds(nx: float, ny: float, nz: float) -> None:
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

    for raw in lines:
        if keep_running and not keep_running():
            return None
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
        g_codes: Set[float] = set()
        for w, val in words:
            if w == "G":
                try:
                    g_codes.add(round(float(val), 3))
                except Exception:
                    pass

        def has_g(code: float) -> bool:
            return round(code, 3) in g_codes

        if has_g(20):
            units = 25.4
            if feed_raw is not None:
                feed_mm = feed_raw * units
        if has_g(21):
            units = 1.0
            if feed_raw is not None:
                feed_mm = feed_raw * units
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
        i_val = j_val = k_val = r_val = None
        for w, val in words:
            try:
                raw_val = float(val)
            except Exception:
                continue
            if w == "P":
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
                feed_raw = raw_val
                feed_mm = raw_val * units
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

        motion: Optional[int] = None
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

        feed_for_mode = feed_raw if feed_mode == "G93" else feed_mm
        if motion in (0, 1) and has_axis:
            dx = nx - x
            dy = ny - y
            dz = nz - z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            color = "rapid" if motion == 0 else "feed"
            segments.append((x, y, z, nx, ny, nz, color))
            moves.append(
                GcodeMove(
                    start=(x, y, z),
                    end=(nx, ny, nz),
                    motion=motion,
                    feed=feed_for_mode,
                    feed_mode=feed_mode,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    dist=dist,
                    arc_len=None,
                )
            )
            update_bounds(x, y, z)
            update_bounds(nx, ny, nz)
            x, y, z = nx, ny, nz
            if motion is not None:
                last_motion = motion
            continue

        if motion in (2, 3) and has_axis:
            update_bounds(x, y, z)
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

            arc_len2d = math.hypot(u1 - u0, v1 - v0)
            full_circle = abs(u1 - u0) < 1e-6 and abs(v1 - v0) < 1e-6
            sweep = 0.0
            if r_val is not None:
                if full_circle:
                    r = abs(r_val)
                    arc_len2d = 2 * math.pi * r if r > 0 else 0.0
                    sweep = 2 * math.pi if r > 0 else 0.0
                    cu = u0 + r
                    cv = v0
                else:
                    res = _arc_center_from_radius(u0, v0, u1, v1, r_val, cw)
                    if res:
                        cu, cv, sweep = res
                        r = math.hypot(u0 - cu, v0 - cv)
                    else:
                        x, y, z = nx, ny, nz
                        continue
            else:
                if off1 is None:
                    off1 = u0 if arc_abs else 0.0
                if off2 is None:
                    off2 = v0 if arc_abs else 0.0
                cu = off1 if arc_abs else (u0 + off1)
                cv = off2 if arc_abs else (v0 + off2)
                sweep = 2 * math.pi if full_circle else _arc_sweep(u0, v0, u1, v1, cu, cv, cw)
                r = math.hypot(u0 - cu, v0 - cv)
            if sweep == 0 or r == 0:
                x, y, z = nx, ny, nz
                continue
            arc_len2d = abs(sweep) * r
            steps = max(8, int(abs(sweep) / arc_step_rad))
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
            dist = math.hypot(arc_len2d, w1 - w0)
            dx = nx - x
            dy = ny - y
            dz = nz - z
            moves.append(
                GcodeMove(
                    start=(x, y, z),
                    end=(nx, ny, nz),
                    motion=motion,
                    feed=feed_for_mode,
                    feed_mode=feed_mode,
                    dx=dx,
                    dy=dy,
                    dz=dz,
                    dist=dist,
                    arc_len=arc_len2d,
                )
            )
            update_bounds(nx, ny, nz)
            x, y, z = nx, ny, nz
            last_motion = motion
            continue

    if minx is None:
        bounds = None
    else:
        bounds = (minx, maxx, miny, maxy, minz, maxz)
    return GcodeParseResult(segments=segments, bounds=bounds, moves=moves)
