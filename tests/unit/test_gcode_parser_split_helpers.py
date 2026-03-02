import pytest

from simple_sender.gcode_parser_split import _SplitState, _apply_modal_g_codes, _resolve_motion

pytestmark = pytest.mark.unit


def test_apply_modal_g_codes_updates_units_modes() -> None:
    state = _SplitState()

    _apply_modal_g_codes(state, {20.0, 91.0, 93.0})

    assert state.units == 25.4
    assert state.absolute is False
    assert state.feed_mode == "G93"


def test_resolve_motion_prefers_explicit_motion_codes() -> None:
    assert _resolve_motion({0.0, 1.0}, has_axis=True, last_motion=3) == 0
    assert _resolve_motion({2.0}, has_axis=False, last_motion=1) == 2


def test_resolve_motion_falls_back_to_last_motion_with_axes_only() -> None:
    assert _resolve_motion(set(), has_axis=True, last_motion=1) == 1
    assert _resolve_motion(set(), has_axis=False, last_motion=1) is None
