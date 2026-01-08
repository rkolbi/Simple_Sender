import threading
import time

from simple_sender.gcode_parser import parse_gcode_lines


def _compute_stats_from_moves(
    moves,
    bounds,
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    if not moves:
        return {"bounds": bounds, "time_min": None, "rapid_min": None}
    total_time_min = 0.0
    has_time = False
    total_rapid_min = 0.0
    has_rapid = False
    last_f = None

    def axis_limits(dx: float, dy: float, dz: float):
        max_feed = None
        min_accel = None
        if rapid_rates:
            candidates = []
            if dx:
                candidates.append(rapid_rates[0])
            if dy:
                candidates.append(rapid_rates[1])
            if dz:
                candidates.append(rapid_rates[2])
            if candidates:
                max_feed = min(candidates)
        if accel_rates:
            candidates = []
            if dx:
                candidates.append(accel_rates[0])
            if dy:
                candidates.append(accel_rates[1])
            if dz:
                candidates.append(accel_rates[2])
            if candidates:
                min_accel = min(candidates)
        return max_feed, min_accel

    def move_duration(dist: float, feed_mm_min: float | None, min_accel: float | None, last_feed: float | None):
        if dist <= 0:
            return 0.0, last_feed
        if feed_mm_min is None or feed_mm_min <= 0:
            return None, last_feed
        f = feed_mm_min / 60.0
        if f <= 0:
            return None, last_feed
        accel = min_accel if (min_accel and min_accel > 0) else 0.0
        if accel <= 0:
            return dist / f, f
        if last_feed is not None and abs(f - last_feed) < 1e-6:
            return dist / f, f
        accel = accel if accel > 0 else 750.0
        half_len = dist / 2.0
        init_time = f / accel
        init_dx = 0.5 * f * init_time
        time_sec = 0.0
        if half_len >= init_dx:
            half_len -= init_dx
            time_sec += init_time
        time_sec += half_len / f
        return 2 * time_sec, f

    for move in moves:
        if move.motion == 0 and rapid_rates:
            max_feed, min_accel = axis_limits(move.dx, move.dy, move.dz)
            if max_feed:
                t_sec, last_f = move_duration(move.dist, max_feed, min_accel, last_f)
                if t_sec is not None:
                    total_rapid_min += t_sec / 60.0
                    has_rapid = True
        if move.motion in (1, 2, 3):
            if move.feed and move.feed > 0:
                if move.feed_mode == "G93":
                    total_time_min += 1.0 / move.feed
                else:
                    max_feed, min_accel = axis_limits(move.dx, move.dy, move.dz)
                    use_feed = move.feed
                    if max_feed and use_feed > max_feed:
                        use_feed = max_feed
                    t_sec, last_f = move_duration(move.dist, use_feed, min_accel, last_f)
                    if t_sec is not None:
                        total_time_min += t_sec / 60.0
                has_time = True
    return {
        "bounds": bounds,
        "time_min": total_time_min if has_time else None,
        "rapid_min": total_rapid_min if has_rapid else None,
    }


def compute_gcode_stats_from_result(
    result,
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    if result is None:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    return _compute_stats_from_moves(result.moves, result.bounds, rapid_rates, accel_rates)


def compute_gcode_stats(
    lines: list[str],
    rapid_rates: tuple[float, float, float] | None = None,
    accel_rates: tuple[float, float, float] | None = None,
) -> dict:
    if not lines:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    result = parse_gcode_lines(lines)
    if result is None:
        return {"bounds": None, "time_min": None, "rapid_min": None}
    return _compute_stats_from_moves(result.moves, result.bounds, rapid_rates, accel_rates)


def format_duration(seconds: int) -> str:
    total_minutes = int(round(seconds / 60)) if seconds else 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def estimate_factor_value(app) -> float:
    try:
        val = float(app.estimate_factor.get())
    except Exception:
        return 1.0
    if val <= 0:
        return 1.0
    return val


def refresh_gcode_stats_display(app):
    if not app._last_stats:
        return
    app.gcode_stats_var.set(format_gcode_stats_text(app, app._last_stats, app._last_rate_source))


def on_estimate_factor_change(app, _value=None):
    factor = estimate_factor_value(app)
    app._estimate_factor_label.set(f"{factor:.2f}x")
    refresh_gcode_stats_display(app)


def update_live_estimate(app, done: int, total: int):
    if app._stream_start_ts is None or done <= 0 or total <= 0:
        return
    now = time.time()
    paused_total = app._stream_pause_total
    if app._stream_paused_at is not None:
        paused_total += max(0.0, now - app._stream_paused_at)
    elapsed = max(0.0, now - app._stream_start_ts - paused_total)
    if elapsed < 1.0:
        return
    remaining = (elapsed / done) * total - elapsed
    if remaining < 0:
        remaining = 0.0
    app._live_estimate_min = remaining / 60.0
    refresh_gcode_stats_display(app)


def format_gcode_stats_text(app, stats: dict, rate_source: str | None) -> str:
    bounds = stats.get("bounds")
    if not bounds:
        return "No toolpath data"
    unit_mode = "mm"
    try:
        unit_mode = app.unit_mode.get()
    except Exception:
        unit_mode = "mm"
    unit_label = "in" if unit_mode == "inch" else "mm"
    unit_scale = 25.4 if unit_mode == "inch" else 1.0
    minx, maxx, miny, maxy, minz, maxz = bounds
    minx, maxx = minx / unit_scale, maxx / unit_scale
    miny, maxy = miny / unit_scale, maxy / unit_scale
    minz, maxz = minz / unit_scale, maxz / unit_scale
    factor = estimate_factor_value(app)
    time_min = stats.get("time_min")
    rapid_min = stats.get("rapid_min")
    if time_min is None:
        time_txt = "n/a"
    else:
        seconds = int(round(time_min * factor * 60))
        time_txt = format_duration(seconds)
    if rapid_min is None or time_min is None:
        total_txt = "n/a"
        if rate_source is None:
            total_txt = "n/a (not connected)"
    else:
        seconds = int(round((time_min + rapid_min) * factor * 60))
        total_txt = format_duration(seconds)
        if rate_source == "fallback":
            total_txt = f"{total_txt} (fallback)"
        elif rate_source == "profile":
            total_txt = f"{total_txt} (profile)"
    live_txt = ""
    if app._live_estimate_min is not None:
        live_seconds = int(round(app._live_estimate_min * factor * 60))
        live_txt = f" | Live est (stream): {format_duration(live_seconds)}"
    return (
        f"Bounds ({unit_label}) X[{minx:.3f}..{maxx:.3f}] "
        f"Y[{miny:.3f}..{maxy:.3f}] "
        f"Z[{minz:.3f}..{maxz:.3f}] | "
        f"Est time (feed only): {time_txt} | "
        f"Est time (with rapids): {total_txt}"
        f"{live_txt} | "
        "Approx"
    )


def apply_gcode_stats(app, token: int, stats: dict | None, rate_source: str | None):
    if token != app._stats_token:
        return
    app._last_stats = stats
    app._last_rate_source = rate_source
    if stats is None:
        app.gcode_stats_var.set("Estimate unavailable")
        return
    refresh_gcode_stats_display(app)


def get_fallback_rapid_rate(app) -> float | None:
    raw = app.fallback_rapid_rate.get().strip()
    if not raw:
        return None
    try:
        rate = float(raw)
    except Exception:
        return None
    if rate <= 0:
        return None
    return rate


def get_rapid_rates_for_estimate(app):
    if app._rapid_rates:
        return app._rapid_rates, "grbl"
    try:
        rx = float(app.estimate_rate_x_var.get().strip())
        ry = float(app.estimate_rate_y_var.get().strip())
        rz = float(app.estimate_rate_z_var.get().strip())
    except Exception:
        rx = ry = rz = None
    if rx and ry and rz and rx > 0 and ry > 0 and rz > 0:
        units = str(app.unit_mode.get()).lower()
        scale = 25.4 if units.startswith("in") else 1.0
        return (rx * scale, ry * scale, rz * scale), "estimate"
    fallback = get_fallback_rapid_rate(app)
    if fallback:
        return (fallback, fallback, fallback), "fallback"
    return None, None


def get_accel_rates_for_estimate(app):
    return app._accel_rates


def make_stats_cache_key(
    app,
    rapid_rates: tuple[float, float, float] | None,
    accel_rates: tuple[float, float, float] | None,
):
    if not app._gcode_hash:
        return None
    rapid = tuple(rapid_rates) if rapid_rates is not None else None
    accel = tuple(accel_rates) if accel_rates is not None else None
    return (app._gcode_hash, rapid, accel)


def update_gcode_stats(app, lines: list[str], parse_result=None):
    if not lines:
        app._last_stats = None
        app._last_rate_source = None
        app.gcode_stats_var.set("No file loaded")
        return
    if parse_result is None:
        cached_parse = getattr(app, "_last_parse_result", None)
        cached_hash = getattr(app, "_last_parse_hash", None)
        if cached_parse is not None and cached_hash == app._gcode_hash:
            parse_result = cached_parse
    app._last_stats = None
    app._last_rate_source = None
    app._stats_token += 1
    token = app._stats_token
    rapid_rates, rate_source = get_rapid_rates_for_estimate(app)
    accel_rates = get_accel_rates_for_estimate(app)
    cache_key = make_stats_cache_key(app, rapid_rates, accel_rates)
    if cache_key and cache_key in app._stats_cache:
        stats, cached_source = app._stats_cache[cache_key]
        apply_gcode_stats(app, token, stats, cached_source)
        return
    app.gcode_stats_var.set("Calculating stats...")

    def worker():
        try:
            if parse_result is None:
                stats = compute_gcode_stats(lines, rapid_rates, accel_rates)
            else:
                stats = compute_gcode_stats_from_result(parse_result, rapid_rates, accel_rates)
        except Exception as exc:
            app.after(0, lambda: apply_gcode_stats(app, token, None, rate_source))
            app.ui_q.put(("log", f"[stats] Estimate failed: {exc}"))
            return
        if cache_key:
            app._stats_cache[cache_key] = (stats, rate_source)
        app.after(0, lambda: apply_gcode_stats(app, token, stats, rate_source))

    threading.Thread(target=worker, daemon=True).start()
