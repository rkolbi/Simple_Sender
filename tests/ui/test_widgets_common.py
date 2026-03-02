from simple_sender.ui import widgets_common


class _FakeWidget:
    pass


def test_common_attach_log_gcode_sets_attribute() -> None:
    widget = _FakeWidget()

    widgets_common.attach_log_gcode(widget, "G0 X0")

    assert widget._log_gcode_get == "G0 X0"


def test_common_set_kb_id_sets_attribute_and_returns_widget() -> None:
    widget = _FakeWidget()

    result = widgets_common.set_kb_id(widget, "job_run")

    assert result is widget
    assert widget._kb_id == "job_run"
