import queue

from simple_sender.autolevel.grid import ProbeBounds, ProbeGrid
from simple_sender.autolevel.height_map import HeightMap
from simple_sender.autolevel.probe_controller import ProbeController
from simple_sender.autolevel.probe_runner import AutoLevelProbeRunner, ProbeRunSettings


class _MacroVars:
    def __init__(self, store: dict) -> None:
        self._store = store

    def __enter__(self) -> dict:
        return self._store

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _MacroExecutor:
    def __init__(self) -> None:
        self._vars: dict = {}

    def macro_vars(self):
        return _MacroVars(self._vars)


class _Grbl:
    def __init__(self, connected: bool = True, streaming: bool = False) -> None:
        self._connected = connected
        self._streaming = streaming

    def is_connected(self) -> bool:
        return self._connected

    def is_streaming(self) -> bool:
        return self._streaming

    def send_immediate(self, _cmd: str, source: str = "") -> None:
        return None

    def wait_for_manual_completion(self, timeout_s: float = 0.01) -> bool:
        return True


class _App:
    def __init__(self) -> None:
        self.ui_q = queue.Queue()
        self.grbl = _Grbl()
        self._alarm_locked = False
        self._machine_state_text = "Idle"
        self.macro_executor = _MacroExecutor()
        self.probe_controller = ProbeController(self)


def _grid_with_points(points: list[tuple[float, float]]) -> ProbeGrid:
    xs = sorted({p[0] for p in points})
    ys = sorted({p[1] for p in points})
    bounds = ProbeBounds(minx=min(xs), maxx=max(xs), miny=min(ys), maxy=max(ys))
    return ProbeGrid(
        bounds=bounds,
        xs=xs,
        ys=ys,
        points=points,
        spacing_x=xs[1] - xs[0] if len(xs) > 1 else 0.0,
        spacing_y=ys[1] - ys[0] if len(ys) > 1 else 0.0,
        margin=0.0,
    )


def test_probe_controller_parses_prb_report() -> None:
    app = _App()
    controller = app.probe_controller

    controller.handle_rx_line("[PRB:1.000,2.000,-0.125:1]")

    report = controller.last_report()
    assert report is not None
    assert report.x == 1.0
    assert report.y == 2.0
    assert report.z == -0.125
    assert report.ok is True
    with app.macro_executor.macro_vars() as macro_vars:
        assert macro_vars["PRB"].z == -0.125


def test_probe_runner_cancel_stops_early() -> None:
    app = _App()
    runner = AutoLevelProbeRunner(app)
    grid = _grid_with_points([(0.0, 0.0), (1.0, 0.0)])
    height_map = HeightMap([0.0, 1.0], [0.0])
    settings = ProbeRunSettings()
    result: dict[str, object] = {}

    runner._snapshot_modal_state = lambda: (None, None)
    runner._restore_modal_state = lambda *_args: None

    def _probe_point(x, y, _settings, height_map_ref):
        height_map_ref.set_point(x, y, 0.0)
        runner.cancel()
        return True

    runner._probe_point = _probe_point  # type: ignore[assignment]

    def on_done(ok: bool, reason: str | None) -> None:
        result["ok"] = ok
        result["reason"] = reason

    runner._run(grid, height_map, settings, None, None, on_done)

    assert result["ok"] is False
    assert result["reason"] == "Cancelled."


def test_probe_runner_reports_completion() -> None:
    app = _App()
    runner = AutoLevelProbeRunner(app)
    grid = _grid_with_points([(0.0, 0.0)])
    height_map = HeightMap([0.0], [0.0])
    settings = ProbeRunSettings()
    result: dict[str, object] = {}

    runner._snapshot_modal_state = lambda: (None, None)
    runner._restore_modal_state = lambda *_args: None

    def _probe_point(x, y, _settings, height_map_ref):
        height_map_ref.set_point(x, y, 0.0)
        return True

    runner._probe_point = _probe_point  # type: ignore[assignment]

    def on_done(ok: bool, reason: str | None) -> None:
        result["ok"] = ok
        result["reason"] = reason

    runner._run(grid, height_map, settings, None, None, on_done)

    assert result["ok"] is True
    assert result["reason"] is None
    assert height_map.is_complete()


def test_probe_runner_queues_force_g90_on_alarm_failure() -> None:
    app = _App()
    runner = AutoLevelProbeRunner(app)
    grid = _grid_with_points([(0.0, 0.0)])
    height_map = HeightMap([0.0], [0.0])
    settings = ProbeRunSettings()

    runner._snapshot_modal_state = lambda: (None, None)
    runner._restore_modal_state = lambda *_args: None

    def _probe_point(_x, _y, _settings, _height_map_ref):
        app._alarm_locked = True
        return False

    runner._probe_point = _probe_point  # type: ignore[assignment]

    runner._run(grid, height_map, settings, None, None, None)

    assert getattr(app, "_pending_force_g90", False) is True
