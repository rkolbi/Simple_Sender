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

from dataclasses import dataclass
import bisect
import math


@dataclass(frozen=True)
class HeightMapStats:
    min_z: float
    max_z: float
    mean_z: float
    rms_roughness: float
    outliers: int
    point_count: int

    def span(self) -> float:
        return self.max_z - self.min_z


class HeightMap:
    def __init__(self, xs: list[float], ys: list[float], *, invalid_points: list[tuple[int, int]] | None = None):
        if not xs or not ys:
            raise ValueError("HeightMap requires non-empty xs and ys")
        self.xs = list(xs)
        self.ys = list(ys)
        self._rows = [[None for _ in self.xs] for _ in self.ys]
        self._x_index = {self._key(x): idx for idx, x in enumerate(self.xs)}
        self._y_index = {self._key(y): idx for idx, y in enumerate(self.ys)}
        self._invalid: set[tuple[int, int]] = set()
        if invalid_points:
            for ix, iy in invalid_points:
                self.set_invalid_index(ix, iy)

    def _key(self, value: float) -> float:
        return round(value, 6)

    def set_point(self, x: float, y: float, z: float) -> bool:
        ix = self._x_index.get(self._key(x))
        iy = self._y_index.get(self._key(y))
        if ix is None or iy is None:
            return False
        if (ix, iy) in self._invalid:
            return False
        self._rows[iy][ix] = float(z)
        return True

    def index_for(self, x: float, y: float) -> tuple[int, int] | None:
        ix = self._x_index.get(self._key(x))
        iy = self._y_index.get(self._key(y))
        if ix is None or iy is None:
            return None
        return ix, iy

    def set_index(self, ix: int, iy: int, z: float) -> None:
        if (ix, iy) in self._invalid:
            return
        self._rows[iy][ix] = float(z)

    def get_index(self, ix: int, iy: int) -> float | None:
        return self._rows[iy][ix]

    def set_invalid_index(self, ix: int, iy: int) -> None:
        if ix < 0 or iy < 0 or iy >= len(self._rows) or ix >= len(self._rows[iy]):
            return
        self._rows[iy][ix] = None
        self._invalid.add((ix, iy))

    def mark_invalid(self, x: float, y: float) -> bool:
        indices = self.index_for(x, y)
        if indices is None:
            return False
        ix, iy = indices
        self.set_invalid_index(ix, iy)
        return True

    def _value_at(self, ix: int, iy: int) -> float | None:
        if (ix, iy) in self._invalid:
            return None
        return self._rows[iy][ix]

    def is_complete(self) -> bool:
        for iy, row in enumerate(self._rows):
            for ix, val in enumerate(row):
                if val is None and (ix, iy) not in self._invalid:
                    return False
        return True

    def stats(self) -> HeightMapStats | None:
        values: list[float] = []
        points: list[tuple[float, float, float]] = []
        for iy, row in enumerate(self._rows):
            for ix, val in enumerate(row):
                if val is not None:
                    z = float(val)
                    values.append(z)
                    points.append((self.xs[ix], self.ys[iy], z))
        if not values:
            return None
        min_z = min(values)
        max_z = max(values)
        mean_z = sum(values) / len(values)
        residuals = _plane_residuals(points, mean_z)
        rms = _rms(residuals)
        outliers = _count_outliers(residuals)
        return HeightMapStats(min_z, max_z, mean_z, rms, outliers, len(values))

    def to_dict(self) -> dict:
        data = {
            "xs": list(self.xs),
            "ys": list(self.ys),
            "z": [[v if v is None else float(v) for v in row] for row in self._rows],
        }
        if self._invalid:
            data["invalid"] = [[ix, iy] for ix, iy in sorted(self._invalid)]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "HeightMap":
        if not isinstance(data, dict):
            raise ValueError("Height map data must be a dict.")
        xs = data.get("xs")
        ys = data.get("ys")
        rows = data.get("z")
        if not isinstance(xs, list) or not isinstance(ys, list) or not isinstance(rows, list):
            raise ValueError("Height map data missing xs/ys/z lists.")
        xs = [float(v) for v in xs]
        ys = [float(v) for v in ys]
        if len(rows) != len(ys):
            raise ValueError("Height map row count does not match ys length.")
        height_map = cls(xs, ys)
        for iy, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != len(xs):
                raise ValueError("Height map row length does not match xs length.")
            for ix, value in enumerate(row):
                if value is None:
                    height_map._rows[iy][ix] = None
                else:
                    height_map._rows[iy][ix] = float(value)
        invalid = data.get("invalid")
        if isinstance(invalid, list):
            for entry in invalid:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    continue
                try:
                    ix = int(entry[0])
                    iy = int(entry[1])
                except Exception:
                    continue
                if ix < 0 or iy < 0 or iy >= len(height_map.ys) or ix >= len(height_map.xs):
                    continue
                height_map.set_invalid_index(ix, iy)
        return height_map

    def interpolate(self, x: float, y: float, method: str = "bilinear") -> float | None:
        if not self.is_complete():
            return None
        x = self._clamp(x, self.xs[0], self.xs[-1])
        y = self._clamp(y, self.ys[0], self.ys[-1])
        ix0, ix1, tx = self._find_segment(self.xs, x)
        iy0, iy1, ty = self._find_segment(self.ys, y)
        if method.lower() == "bicubic":
            value = self._interpolate_bicubic(ix0, ix1, tx, iy0, iy1, ty)
        else:
            value = self._interpolate_bilinear(ix0, ix1, tx, iy0, iy1, ty)
        if value is None:
            return self._interpolate_sparse(x, y, ix0, ix1, iy0, iy1)
        return value

    def _interpolate_bilinear(
        self,
        ix0: int,
        ix1: int,
        tx: float,
        iy0: int,
        iy1: int,
        ty: float,
    ) -> float | None:
        z00 = self._value_at(ix0, iy0)
        z10 = self._value_at(ix1, iy0)
        z01 = self._value_at(ix0, iy1)
        z11 = self._value_at(ix1, iy1)
        if None in (z00, z10, z01, z11):
            return None
        z00 = float(z00)
        z10 = float(z10)
        z01 = float(z01)
        z11 = float(z11)
        return (
            (1 - tx) * (1 - ty) * z00
            + tx * (1 - ty) * z10
            + (1 - tx) * ty * z01
            + tx * ty * z11
        )

    def _interpolate_bicubic(
        self,
        ix0: int,
        ix1: int,
        tx: float,
        iy0: int,
        iy1: int,
        ty: float,
    ) -> float | None:
        if len(self.xs) < 2 or len(self.ys) < 2:
            return self._interpolate_bilinear(ix0, ix1, tx, iy0, iy1, ty)
        ixm1 = max(ix0 - 1, 0)
        ix2 = min(ix1 + 1, len(self.xs) - 1)
        iym1 = max(iy0 - 1, 0)
        iy2 = min(iy1 + 1, len(self.ys) - 1)
        rows = [iym1, iy0, iy1, iy2]
        values = []
        for iy in rows:
            p0 = self._value_at(ixm1, iy)
            p1 = self._value_at(ix0, iy)
            p2 = self._value_at(ix1, iy)
            p3 = self._value_at(ix2, iy)
            if None in (p0, p1, p2, p3):
                return self._interpolate_bilinear(ix0, ix1, tx, iy0, iy1, ty)
            values.append(self._catmull_rom(float(p0), float(p1), float(p2), float(p3), tx))
        return self._catmull_rom(values[0], values[1], values[2], values[3], ty)

    def _interpolate_sparse(
        self,
        x: float,
        y: float,
        ix0: int,
        ix1: int,
        iy0: int,
        iy1: int,
    ) -> float | None:
        points = self._sparse_neighbors(x, y, ix0, ix1, iy0, iy1)
        if not points:
            return None
        total = 0.0
        total_w = 0.0
        for px, py, pz in points:
            dx = x - px
            dy = y - py
            d2 = dx * dx + dy * dy
            if d2 <= 1e-12:
                return pz
            w = 1.0 / d2
            total += w * pz
            total_w += w
        if total_w <= 0:
            return None
        return total / total_w

    def _sparse_neighbors(
        self,
        x: float,
        y: float,
        ix0: int,
        ix1: int,
        iy0: int,
        iy1: int,
        *,
        min_points: int = 1,
        max_points: int = 8,
    ) -> list[tuple[float, float, float]]:
        points: list[tuple[float, float, float]] = []
        max_radius = max(len(self.xs), len(self.ys))
        for radius in range(max_radius):
            x_min = max(ix0 - radius, 0)
            x_max = min(ix1 + radius, len(self.xs) - 1)
            y_min = max(iy0 - radius, 0)
            y_max = min(iy1 + radius, len(self.ys) - 1)
            for iy in range(y_min, y_max + 1):
                for ix in range(x_min, x_max + 1):
                    if radius > 0 and x_min < ix < x_max and y_min < iy < y_max:
                        continue
                    val = self._value_at(ix, iy)
                    if val is None:
                        continue
                    points.append((self.xs[ix], self.ys[iy], float(val)))
            if len(points) >= min_points:
                break
        if not points:
            return []
        points.sort(key=lambda item: (item[0] - x) ** 2 + (item[1] - y) ** 2)
        return points[:max_points]

    def _catmull_rom(self, p0: float, p1: float, p2: float, p3: float, t: float) -> float:
        t2 = t * t
        t3 = t2 * t
        return 0.5 * (
            (2 * p1)
            + (-p0 + p2) * t
            + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
            + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
        )

    def _find_segment(self, axis: list[float], value: float) -> tuple[int, int, float]:
        if len(axis) == 1:
            return 0, 0, 0.0
        idx = bisect.bisect_left(axis, value)
        if idx <= 0:
            return 0, 1, 0.0
        if idx >= len(axis):
            return len(axis) - 2, len(axis) - 1, 1.0
        x0 = axis[idx - 1]
        x1 = axis[idx]
        if math.isclose(x1, x0):
            return idx - 1, idx, 0.0
        t = (value - x0) / (x1 - x0)
        return idx - 1, idx, t

    def _clamp(self, value: float, lo: float, hi: float) -> float:
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value


def _plane_residuals(points: list[tuple[float, float, float]], mean_z: float) -> list[float]:
    if len(points) < 3:
        return [z - mean_z for _, _, z in points]
    sum_x = sum_y = sum_z = 0.0
    sum_xx = sum_yy = sum_xy = 0.0
    sum_xz = sum_yz = 0.0
    for x, y, z in points:
        sum_x += x
        sum_y += y
        sum_z += z
        sum_xx += x * x
        sum_yy += y * y
        sum_xy += x * y
        sum_xz += x * z
        sum_yz += y * z
    coeffs = _solve_3x3(
        sum_xx,
        sum_xy,
        sum_x,
        sum_xy,
        sum_yy,
        sum_y,
        sum_x,
        sum_y,
        float(len(points)),
        sum_xz,
        sum_yz,
        sum_z,
    )
    if coeffs is None:
        a = b = 0.0
        c = mean_z
    else:
        a, b, c = coeffs
    return [z - (a * x + b * y + c) for x, y, z in points]


def _solve_3x3(
    a11: float,
    a12: float,
    a13: float,
    a21: float,
    a22: float,
    a23: float,
    a31: float,
    a32: float,
    a33: float,
    b1: float,
    b2: float,
    b3: float,
) -> tuple[float, float, float] | None:
    mat = [
        [a11, a12, a13],
        [a21, a22, a23],
        [a31, a32, a33],
    ]
    vec = [b1, b2, b3]
    for i in range(3):
        pivot = i
        for r in range(i + 1, 3):
            if abs(mat[r][i]) > abs(mat[pivot][i]):
                pivot = r
        if abs(mat[pivot][i]) < 1e-12:
            return None
        if pivot != i:
            mat[i], mat[pivot] = mat[pivot], mat[i]
            vec[i], vec[pivot] = vec[pivot], vec[i]
        denom = mat[i][i]
        for c in range(i, 3):
            mat[i][c] /= denom
        vec[i] /= denom
        for r in range(3):
            if r == i:
                continue
            factor = mat[r][i]
            if factor == 0:
                continue
            for c in range(i, 3):
                mat[r][c] -= factor * mat[i][c]
            vec[r] -= factor * vec[i]
    return vec[0], vec[1], vec[2]


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    total = sum(val * val for val in values)
    return math.sqrt(total / len(values))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _quartiles(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        lower = ordered[:mid]
        upper = ordered[mid:]
    else:
        lower = ordered[:mid]
        upper = ordered[mid + 1 :]
    q1 = _median(lower) if lower else ordered[0]
    q3 = _median(upper) if upper else ordered[-1]
    return q1, q3


def _count_outliers(residuals: list[float]) -> int:
    if not residuals:
        return 0
    median = _median(residuals)
    deviations = [abs(val - median) for val in residuals]
    mad = _median(deviations)
    if mad > 0:
        threshold = 4.4478 * mad
        return sum(1 for dev in deviations if dev > threshold)
    q1, q3 = _quartiles(deviations)
    iqr = q3 - q1
    if iqr <= 0:
        return 0
    threshold = 1.5 * iqr
    return sum(1 for dev in deviations if dev > threshold)
