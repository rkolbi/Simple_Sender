import pytest

from simple_sender.ui.dro import convert_units, format_dro_value, refresh_dro_display

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


def test_convert_units_mm_inch() -> None:
    assert convert_units(25.4, "mm", "inch") == pytest.approx(1.0)
    assert convert_units(1.0, "inch", "mm") == pytest.approx(25.4)


def test_format_dro_value_rounds() -> None:
    assert format_dro_value(1.23456, "mm", "mm") == "1.235"


def test_refresh_dro_display_uses_report_units() -> None:
    class _App:
        unit_mode = _Var("mm")
        _report_units = "inch"
        _mpos_raw = (1.0, 2.0, 3.0)
        _wpos_raw = (4.0, 5.0, 6.0)
        mpos_x = _Var()
        mpos_y = _Var()
        mpos_z = _Var()
        wpos_x = _Var()
        wpos_y = _Var()
        wpos_z = _Var()

    app = _App()

    refresh_dro_display(app)

    assert app.mpos_x.value == "25.400"
    assert app.wpos_z.value == "152.400"
