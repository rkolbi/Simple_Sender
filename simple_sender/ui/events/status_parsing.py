from dataclasses import dataclass


@dataclass(slots=True)
class _StatusFields:
    state: str
    wpos: str | None = None
    mpos: str | None = None
    feed: float | None = None
    spindle: float | None = None
    planner: int | None = None
    rxbytes: int | None = None
    wco: str | None = None
    ov: str | None = None
    pins: str | None = None


def _status_state_token(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("<"):
        text = text[1:]
    end_idx = text.find("|")
    if end_idx >= 0:
        return text[:end_idx]
    if text.endswith(">"):
        text = text[:-1]
    return text


def _status_relaxed_idle_signature(raw: str) -> str:
    text = str(raw or "").strip()
    if not (text.startswith("<") and text.endswith(">")):
        return text
    body = text[1:-1]
    parts = [part.strip() for part in body.split("|") if part.strip()]
    if not parts:
        return text
    state = str(parts[0] or "").strip()
    if not state.lower().startswith("idle"):
        return text
    filtered = [state]
    for part in parts[1:]:
        upper = part.upper()
        if upper.startswith("WCO:") or upper.startswith("OV:"):
            continue
        filtered.append(part)
    return "|".join(filtered)


def _clone_status_fields(fields: _StatusFields) -> _StatusFields:
    return _StatusFields(
        state=str(fields.state or ""),
        wpos=None if fields.wpos is None else str(fields.wpos),
        mpos=None if fields.mpos is None else str(fields.mpos),
        feed=None if fields.feed is None else float(fields.feed),
        spindle=None if fields.spindle is None else float(fields.spindle),
        planner=None if fields.planner is None else int(fields.planner),
        rxbytes=None if fields.rxbytes is None else int(fields.rxbytes),
        wco=None if fields.wco is None else str(fields.wco),
        ov=None if fields.ov is None else str(fields.ov),
        pins=None if fields.pins is None else str(fields.pins),
    )


def _parse_status_fields(raw: str, *, log_suppressed=None) -> _StatusFields:
    parts = raw.strip("<>").split("|")
    fields = _StatusFields(state=parts[0] if parts else "?")
    for part in parts:
        if part.startswith("WPos:"):
            fields.wpos = part[5:]
        elif part.startswith("MPos:"):
            fields.mpos = part[5:]
        elif part.startswith("FS:"):
            try:
                feed_str, spindle_str = part[3:].split(",", 1)
                fields.feed = float(feed_str)
                fields.spindle = float(spindle_str)
            except ValueError as exc:
                _maybe_log(log_suppressed, "Failed parsing FS field from status line", exc)
        elif part.startswith("Bf:"):
            try:
                planner_str, rx_str = part[3:].split(",", 1)
                fields.planner = int(planner_str)
                fields.rxbytes = int(rx_str)
            except ValueError as exc:
                _maybe_log(log_suppressed, "Failed parsing Bf field from status line", exc)
        elif part.startswith("WCO:"):
            fields.wco = part[4:]
        elif part.startswith("Ov:"):
            fields.ov = part[3:]
        elif part.startswith("Pn:"):
            fields.pins = part[3:]
    return fields


def _parse_xyz_triplet(text: str, *, log_suppressed=None) -> list[float] | None:
    parts = text.split(",")
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except ValueError as exc:
        _maybe_log(log_suppressed, "Failed parsing XYZ triplet", exc)
        return None


def _unit_scale_cached(unit_mode: str) -> float:
    return 25.4 if str(unit_mode or "").lower() == "inch" else 1.0


def _position_deadband_report_units(
    report_units: str,
    modal_units: str,
    *,
    dro_display_step: float = 0.001,
) -> float:
    report_scale = _unit_scale_cached(report_units)
    modal_scale = _unit_scale_cached(modal_units)
    if report_scale <= 0:
        return 1e-6
    step_report = float(dro_display_step) * (modal_scale / report_scale)
    return max(1e-6, step_report * 0.5)


def _units_ratio(from_units: str, to_units: str) -> float:
    from_scale = _unit_scale_cached(from_units)
    to_scale = _unit_scale_cached(to_units)
    if to_scale <= 0.0:
        return 1.0
    return float(from_scale) / float(to_scale)


def _rounded_xyz(value: tuple[float, float, float] | None) -> list[float] | None:
    if not isinstance(value, tuple) or len(value) < 3:
        return None
    try:
        return [
            round(float(value[0]), 6),
            round(float(value[1]), 6),
            round(float(value[2]), 6),
        ]
    except Exception:
        return None


def _maybe_log(log_suppressed, context: str, exc: BaseException) -> None:
    if callable(log_suppressed):
        log_suppressed(context, exc)
