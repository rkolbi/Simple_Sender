def _unit_scale(unit_mode: str) -> float:
    return 25.4 if unit_mode == "inch" else 1.0


def convert_units(value: float, from_units: str, to_units: str) -> float:
    return value * _unit_scale(from_units) / _unit_scale(to_units)


def format_dro_value(value: float, from_units: str, to_units: str) -> str:
    return f"{convert_units(value, from_units, to_units):.3f}"


def refresh_dro_display(app) -> None:
    try:
        unit_mode = app.unit_mode.get()
    except Exception:
        unit_mode = "mm"
    report_units = getattr(app, "_report_units", None) or unit_mode
    mpos = getattr(app, "_mpos_raw", None)
    if mpos and len(mpos) == 3:
        app.mpos_x.set(format_dro_value(mpos[0], report_units, unit_mode))
        app.mpos_y.set(format_dro_value(mpos[1], report_units, unit_mode))
        app.mpos_z.set(format_dro_value(mpos[2], report_units, unit_mode))
    wpos = getattr(app, "_wpos_raw", None)
    if wpos and len(wpos) == 3:
        app.wpos_x.set(format_dro_value(wpos[0], report_units, unit_mode))
        app.wpos_y.set(format_dro_value(wpos[1], report_units, unit_mode))
        app.wpos_z.set(format_dro_value(wpos[2], report_units, unit_mode))
