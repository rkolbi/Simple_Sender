import pytest

from simple_sender.autolevel.grid import AdaptiveGridSpec, ProbeBounds, build_adaptive_grid


def test_build_adaptive_grid_applies_margin_and_serpentine() -> None:
    bounds = ProbeBounds(minx=0.0, maxx=10.0, miny=0.0, maxy=10.0)
    spec = AdaptiveGridSpec(
        base_spacing=100.0,
        min_spacing=1.0,
        max_spacing=200.0,
        margin=2.0,
    )

    grid = build_adaptive_grid(bounds, spec)

    assert grid.bounds.minx == pytest.approx(-2.0)
    assert grid.bounds.maxx == pytest.approx(12.0)
    assert grid.bounds.miny == pytest.approx(-2.0)
    assert grid.bounds.maxy == pytest.approx(12.0)
    assert grid.points[0] == (grid.xs[0], grid.ys[0])
    assert grid.points[len(grid.xs) - 1] == (grid.xs[-1], grid.ys[0])
    if len(grid.ys) > 1:
        assert grid.points[len(grid.xs)] == (grid.xs[-1], grid.ys[1])
        assert grid.points[len(grid.xs) * 2 - 1] == (grid.xs[0], grid.ys[1])


def test_build_adaptive_grid_spiral_starts_center() -> None:
    bounds = ProbeBounds(minx=0.0, maxx=2.0, miny=0.0, maxy=2.0)
    spec = AdaptiveGridSpec(
        base_spacing=1.0,
        min_spacing=1.0,
        max_spacing=1.0,
        margin=0.0,
    )

    grid = build_adaptive_grid(bounds, spec, path_order="spiral")

    assert grid.point_count() == 9
    assert grid.points[0] == (grid.xs[1], grid.ys[1])


def test_build_adaptive_grid_respects_max_points() -> None:
    bounds = ProbeBounds(minx=0.0, maxx=100.0, miny=0.0, maxy=100.0)
    spec_full = AdaptiveGridSpec(
        base_spacing=1.0,
        min_spacing=1.0,
        max_spacing=10.0,
        margin=0.0,
        max_points=None,
    )
    spec_limited = AdaptiveGridSpec(
        base_spacing=1.0,
        min_spacing=1.0,
        max_spacing=10.0,
        margin=0.0,
        max_points=25,
    )

    full_grid = build_adaptive_grid(bounds, spec_full)
    limited_grid = build_adaptive_grid(bounds, spec_limited)

    assert limited_grid.point_count() < full_grid.point_count()
