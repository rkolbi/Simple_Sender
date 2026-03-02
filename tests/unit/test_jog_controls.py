import math

from simple_sender.ui.controls.jog_controls import apply_safe_mode_profile
from simple_sender.utils.constants import (
    SAFE_JOG_FEED_XY,
    SAFE_JOG_FEED_Z,
    SAFE_JOG_STEP_XY,
    SAFE_JOG_STEP_Z,
)


class _Var:
    def __init__(self, value=None) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Logger:
    def __init__(self) -> None:
        self.messages = []

    def handle_log(self, message: str) -> None:
        self.messages.append(message)


class _App:
    def __init__(self, unit: str) -> None:
        self.unit_mode = _Var(unit)
        self.jog_feed_xy = _Var(0.0)
        self.jog_feed_z = _Var(0.0)
        self.step_xy = _Var(0.0)
        self.step_z = _Var(0.0)
        self.settings = {}
        self.streaming_controller = _Logger()
        self._step_xy = None
        self._step_z = None
        self._feed_xy_changed = False
        self._feed_z_changed = False

    def _set_step_xy(self, value: float) -> None:
        self._step_xy = value

    def _set_step_z(self, value: float) -> None:
        self._step_z = value

    def _on_jog_feed_change_xy(self) -> None:
        self._feed_xy_changed = True

    def _on_jog_feed_change_z(self) -> None:
        self._feed_z_changed = True


def test_apply_safe_mode_profile_mm() -> None:
    app = _App("mm")
    apply_safe_mode_profile(app)

    assert app.jog_feed_xy.get() == SAFE_JOG_FEED_XY
    assert app.jog_feed_z.get() == SAFE_JOG_FEED_Z
    assert app.step_xy.get() == SAFE_JOG_STEP_XY
    assert app.step_z.get() == SAFE_JOG_STEP_Z
    assert app.settings["jog_feed_xy"] == SAFE_JOG_FEED_XY
    assert app.settings["jog_feed_z"] == SAFE_JOG_FEED_Z
    assert app.settings["step_xy"] == SAFE_JOG_STEP_XY
    assert app.settings["step_z"] == SAFE_JOG_STEP_Z


def test_apply_safe_mode_profile_inch() -> None:
    app = _App("inch")
    apply_safe_mode_profile(app)

    scale = 1.0 / 25.4
    assert math.isclose(app.jog_feed_xy.get(), SAFE_JOG_FEED_XY * scale, rel_tol=1e-9)
    assert math.isclose(app.jog_feed_z.get(), SAFE_JOG_FEED_Z * scale, rel_tol=1e-9)
    assert math.isclose(app.step_xy.get(), SAFE_JOG_STEP_XY * scale, rel_tol=1e-9)
    assert math.isclose(app.step_z.get(), SAFE_JOG_STEP_Z * scale, rel_tol=1e-9)
