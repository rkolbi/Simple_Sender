import pytest

from simple_sender.ui.controls import jog_panel

pytestmark = pytest.mark.ui


def test_nearest_step_index_prefers_closest_value() -> None:
    values = [0.1, 0.5, 1.0, 2.0]

    assert jog_panel._nearest_step_index(values, 0.55) == 1
    assert jog_panel._nearest_step_index(values, 1.7) == 3


def test_nearest_step_index_handles_empty_values() -> None:
    assert jog_panel._nearest_step_index([], 1.0) == 0


def test_select_jog_feed_uses_z_feed_only_for_pure_z_motion() -> None:
    assert jog_panel._select_jog_feed(0.0, 0.0, 1.0, 2500.0, 300.0) == 300.0
    assert jog_panel._select_jog_feed(1.0, 0.0, 1.0, 2500.0, 300.0) == 2500.0
    assert jog_panel._select_jog_feed(1.0, 0.0, 0.0, 2500.0, 300.0) == 2500.0


def test_coerce_step_value_falls_back_and_logs(monkeypatch) -> None:
    logs: list[tuple[str, str]] = []

    def _capture(context: str, exc: BaseException) -> None:
        logs.append((context, type(exc).__name__))

    monkeypatch.setattr(jog_panel, "_log_suppressed", _capture)

    value = jog_panel._coerce_step_value("bad", 0.25, "fallback context")

    assert value == 0.25
    assert logs == [("fallback context", "ValueError")]


def test_current_mpos_axis_prefers_raw_position_and_converts_units() -> None:
    class _Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class _App:
        _mpos_raw = (25.4, 50.8, 76.2)
        _report_units = "mm"
        unit_mode = _Var("inch")
        mpos_x = _Var("0.0")
        mpos_y = _Var("0.0")
        mpos_z = _Var("0.0")

    app = _App()

    assert jog_panel._current_mpos_axis(app, "X") == pytest.approx(1.0)
    assert jog_panel._current_mpos_axis(app, "Y") == pytest.approx(2.0)
    assert jog_panel._current_mpos_axis(app, "Z") == pytest.approx(3.0)


def test_jog_axis_to_target_computes_delta_from_current_position() -> None:
    class _Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class _App:
        _mpos_raw = (10.0, 20.0, 30.0)
        _report_units = "mm"
        unit_mode = _Var("mm")
        mpos_x = _Var("0.0")
        mpos_y = _Var("0.0")
        mpos_z = _Var("0.0")

    jog_calls: list[tuple[float, float, float, str | None]] = []

    def _jog(dx: float, dy: float, dz: float, *, source=None) -> None:
        jog_calls.append((dx, dy, dz, source))

    moved = jog_panel._jog_axis_to_target(_App(), "X", 14.5, _jog, source="jog_to_target")

    assert moved is True
    assert jog_calls == [(4.5, 0.0, 0.0, "jog_to_target")]
