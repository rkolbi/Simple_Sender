from simple_sender.autolevel.grid import ProbeBounds, ProbeGrid
from simple_sender.ui.ui_actions import on_autolevel_overlay_change


class _BoolVar:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = value


class _ToolpathPanel:
    def __init__(self) -> None:
        self.grid = "unset"

    def set_autolevel_overlay(self, grid) -> None:
        self.grid = grid


class _App:
    def __init__(self, show: bool, grid: ProbeGrid | None) -> None:
        self.show_autolevel_overlay = _BoolVar(show)
        self._auto_level_grid = grid
        self.toolpath_panel = _ToolpathPanel()
        self.settings = {}
        self.refresh_calls = 0

    def _refresh_autolevel_overlay_button(self) -> None:
        self.refresh_calls += 1


def _grid_fixture() -> ProbeGrid:
    bounds = ProbeBounds(minx=0.0, maxx=1.0, miny=0.0, maxy=1.0)
    return ProbeGrid(
        bounds=bounds,
        xs=[0.0, 1.0],
        ys=[0.0, 1.0],
        points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        spacing_x=1.0,
        spacing_y=1.0,
        margin=0.0,
    )


def test_on_autolevel_overlay_change_applies_overlay() -> None:
    grid = _grid_fixture()
    app = _App(True, grid)

    on_autolevel_overlay_change(app)

    assert app.toolpath_panel.grid is grid
    assert app.settings["show_autolevel_overlay"] is True
    assert app.refresh_calls == 1


def test_on_autolevel_overlay_change_clears_overlay_when_hidden() -> None:
    grid = _grid_fixture()
    app = _App(False, grid)

    on_autolevel_overlay_change(app)

    assert app.toolpath_panel.grid is None
    assert app.settings["show_autolevel_overlay"] is False
