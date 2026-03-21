from collections.abc import Callable


def _parse_modal_units(
    app,
    raw: str,
    *,
    log_suppressed: Callable[[str, BaseException], None],
    signal_thread_event: Callable[[object, str], None],
) -> None:
    line = raw.strip()
    if not (line.startswith("[GC:") and line.endswith("]")):
        return
    tokens = line.strip("[]").split()
    if not tokens:
        return
    modal_units = None
    modal_state = {}
    for token in tokens:
        if token.startswith("GC:"):
            token = token[3:]
            if not token:
                continue
        if token in ("G20", "G21"):
            modal_units = "inch" if token == "G20" else "mm"
            modal_state["units"] = token
            continue
        if token in ("G90", "G91"):
            modal_state["distance"] = token
            continue
        if token in ("G17", "G18", "G19"):
            modal_state["plane"] = token
            continue
        if token in ("G93", "G94"):
            modal_state["feedmode"] = token
            continue
        if token in ("G90.1", "G91.1"):
            modal_state["arc"] = token
            continue
        if token in ("G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3"):
            modal_state["WCS"] = token
            continue
        if token in ("G0", "G1", "G2", "G3", "G38.2", "G38.3", "G38.4", "G38.5"):
            modal_state["motion"] = token
            continue
        if token in ("M3", "M4", "M5"):
            modal_state["spindle"] = token
            continue
        if token in ("M7", "M8", "M9"):
            modal_state["coolant"] = token
            continue
        if token.startswith("T") and token[1:].isdigit():
            modal_state["tool"] = str(int(token[1:]))
    if modal_units:
        app._modal_units = modal_units
        try:
            app._set_unit_mode(modal_units)
        except Exception as exc:
            log_suppressed("Failed to apply modal unit mode", exc)
    if modal_state or modal_units:
        with app.macro_executor.macro_vars() as macro_vars:
            for key, value in modal_state.items():
                macro_vars[key] = value
            macro_vars["_modal_seq"] = int(macro_vars.get("_modal_seq", 0) or 0) + 1
        signal_thread_event(app, "_modal_update_event")


def _parse_report_units_setting(
    app,
    raw: str,
    *,
    log_suppressed: Callable[[str, BaseException], None],
) -> None:
    line = raw.strip()
    if not line.startswith("$13="):
        return
    try:
        raw_val = line.split("=", 1)[1].strip()
        raw_val = raw_val.split(" ", 1)[0]
        raw_val = raw_val.split("(", 1)[0].strip()
        val = int(raw_val)
    except Exception as exc:
        log_suppressed("Failed parsing $13 report-units setting", exc)
        return
    app._report_units = "inch" if val == 1 else "mm"
    try:
        app._update_unit_toggle_display()
    except Exception as exc:
        log_suppressed("Failed updating unit toggle display from $13", exc)
    try:
        status_text = ""
        try:
            status_text = app.status.cget("text")
        except Exception as exc:
            log_suppressed("Failed reading status label text for $13 update", exc)
            status_text = ""
        if getattr(app, "_connected_port", None) and status_text.startswith("Connected"):
            app.status.config(
                text=f"Connected: {app._connected_port} | Report: {app._report_units}"
            )
    except Exception as exc:
        log_suppressed("Failed updating connected status label after $13 update", exc)
    try:
        app._refresh_dro_display()
    except Exception as exc:
        log_suppressed("Failed refreshing DRO display after $13 update", exc)
