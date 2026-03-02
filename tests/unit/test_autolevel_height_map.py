import pytest

from simple_sender.autolevel.height_map import HeightMap


def _build_plane_map() -> HeightMap:
    xs = [0.0, 5.0, 10.0]
    ys = [0.0, 5.0, 10.0]
    height_map = HeightMap(xs, ys)
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            height_map.set_index(ix, iy, x + y)
    return height_map


def test_interpolate_bilinear_and_bicubic_on_plane() -> None:
    height_map = _build_plane_map()

    bilinear = height_map.interpolate(2.5, 7.5, method="bilinear")
    bicubic = height_map.interpolate(2.5, 7.5, method="bicubic")

    assert bilinear == pytest.approx(10.0)
    assert bicubic == pytest.approx(10.0)


def test_stats_plane_reports_no_roughness_or_outliers() -> None:
    height_map = _build_plane_map()

    stats = height_map.stats()

    assert stats is not None
    assert stats.rms_roughness == pytest.approx(0.0, abs=1e-6)
    assert stats.outliers == 0
    assert stats.point_count == len(height_map.xs) * len(height_map.ys)


def test_stats_detects_outlier() -> None:
    height_map = _build_plane_map()
    height_map.set_index(1, 1, 50.0)

    stats = height_map.stats()

    assert stats is not None
    assert stats.rms_roughness > 0.0
    assert stats.outliers >= 1


def test_stats_detects_outlier_with_singular_plane_fit() -> None:
    height_map = HeightMap([0.0], [0.0, 1.0, 2.0, 3.0])
    height_map.set_index(0, 0, 0.0)
    height_map.set_index(0, 1, 0.0)
    height_map.set_index(0, 2, 0.0)
    height_map.set_index(0, 3, 10.0)

    stats = height_map.stats()

    assert stats is not None
    assert stats.outliers >= 1


def test_round_trip_preserves_values() -> None:
    height_map = HeightMap([0.0, 1.0], [0.0, 1.0])
    height_map.set_index(0, 0, 0.1)
    height_map.set_index(1, 0, 0.2)
    height_map.set_index(0, 1, 0.3)
    height_map.set_index(1, 1, 0.4)

    restored = HeightMap.from_dict(height_map.to_dict())

    assert restored.xs == [0.0, 1.0]
    assert restored.ys == [0.0, 1.0]
    assert restored.get_index(1, 1) == pytest.approx(0.4)
    assert restored.is_complete()


def test_invalid_points_are_skipped_and_round_trip() -> None:
    height_map = HeightMap([0.0, 1.0], [0.0, 1.0])
    height_map.set_index(0, 0, 0.0)
    height_map.set_index(1, 0, 1.0)
    height_map.set_index(0, 1, 1.0)

    assert height_map.is_complete() is False
    assert height_map.mark_invalid(1.0, 1.0)

    assert height_map.is_complete()
    assert height_map.set_point(1.0, 1.0, 2.0) is False

    restored = HeightMap.from_dict(height_map.to_dict())
    assert restored.is_complete()
    assert restored.get_index(1, 1) is None
    assert restored.to_dict().get("invalid") == [[1, 1]]


def test_sparse_interpolation_handles_invalid_point() -> None:
    height_map = HeightMap([0.0, 1.0], [0.0, 1.0])
    height_map.set_index(0, 0, 0.0)
    height_map.set_index(1, 0, 1.0)
    height_map.set_index(0, 1, 1.0)
    height_map.mark_invalid(1.0, 1.0)

    value = height_map.interpolate(0.75, 0.75, method="bilinear")

    assert value is not None

    points = [(0.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)]
    total = 0.0
    total_w = 0.0
    for px, py, pz in points:
        dx = 0.75 - px
        dy = 0.75 - py
        d2 = dx * dx + dy * dy
        w = 1.0 / d2
        total += w * pz
        total_w += w
    expected = total / total_w
    assert value == pytest.approx(expected, rel=1e-3)


def test_interpolate_returns_none_when_incomplete() -> None:
    height_map = HeightMap([0.0, 1.0], [0.0, 1.0])
    height_map.set_index(0, 0, 0.0)

    assert height_map.interpolate(0.5, 0.5) is None
