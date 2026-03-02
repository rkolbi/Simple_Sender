import queue

import pytest

from simple_sender.ui.gcode import stats as gcode_stats

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _App:
    def __init__(self) -> None:
        self.estimate_factor = _Var("1.0")
        self._estimate_factor_label = _Var("")
        self.unit_mode = _Var("mm")
        self.gcode_stats_var = _Var("")
        self._last_stats = None
        self._last_rate_source = None
        self._live_estimate_min = None
        self._stream_start_ts = None
        self._stream_pause_total = 0.0
        self._stream_paused_at = None
        self._gcode_streaming_mode = False
        self._rapid_rates = None
        self._accel_rates = None
        self.estimate_rate_x_var = _Var("")
        self.estimate_rate_y_var = _Var("")
        self.estimate_rate_z_var = _Var("")
        self.fallback_rapid_rate = _Var("")
        self._stats_cache = {}
        self._stats_token = 0
        self._stats_after_id = None
        self._stats_pending_request = None
        self._gcode_hash = "hash"
        self.ui_q = queue.Queue()

    def after(self, _delay_ms: int, func):
        func()
        return None


class _Move:
    def __init__(
        self,
        *,
        motion: int,
        dist: float,
        dx: float,
        dy: float,
        dz: float,
        feed: float | None,
        feed_mode: str = "G94",
    ) -> None:
        self.motion = motion
        self.dist = dist
        self.dx = dx
        self.dy = dy
        self.dz = dz
        self.feed = feed
        self.feed_mode = feed_mode


class _Result:
    def __init__(self, moves, bounds) -> None:
        self.moves = moves
        self.bounds = bounds


def test_format_duration_rounds_to_minutes() -> None:
    assert gcode_stats.format_duration(0) == "00:00"
    assert gcode_stats.format_duration(3660) == "01:01"


def test_estimate_factor_value_defaults_on_invalid() -> None:
    app = _App()
    app.estimate_factor = _Var("0")
    assert gcode_stats.estimate_factor_value(app) == 1.0
    app.estimate_factor = _Var("bad")
    assert gcode_stats.estimate_factor_value(app) == 1.0


def test_compute_gcode_stats_from_result_with_moves() -> None:
    moves = [
        _Move(motion=0, dist=60.0, dx=60.0, dy=0.0, dz=0.0, feed=None),
        _Move(motion=1, dist=60.0, dx=60.0, dy=0.0, dz=0.0, feed=120.0),
    ]
    result = _Result(moves, (0.0, 60.0, 0.0, 0.0, 0.0, 0.0))

    stats = gcode_stats.compute_gcode_stats_from_result(result, rapid_rates=(60.0, 60.0, 60.0))

    assert stats["rapid_min"] == pytest.approx(1.0)
    assert stats["time_min"] == pytest.approx(1.0)


def test_compute_gcode_stats_forwards_keep_running(monkeypatch) -> None:
    calls = {}

    class _ParseResult:
        def __init__(self) -> None:
            self.moves = []
            self.bounds = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0)

    def _parse(lines, keep_running=None):
        _ = lines
        calls["called"] = True
        calls["keep_running"] = keep_running
        return _ParseResult() if keep_running and keep_running() else None

    monkeypatch.setattr(gcode_stats, "parse_gcode_lines", _parse)

    stats = gcode_stats.compute_gcode_stats(["G0 X0"], keep_running=lambda: False)

    assert calls["called"] is True
    assert callable(calls["keep_running"])
    assert stats == {"bounds": None, "time_min": None, "rapid_min": None}


def test_format_gcode_stats_text_includes_live_estimate() -> None:
    app = _App()
    app._live_estimate_min = 4.0
    stats = {"bounds": (0.0, 10.0, 0.0, 20.0, -1.0, 1.0), "time_min": 2.0, "rapid_min": 1.0}

    text = gcode_stats.format_gcode_stats_text(app, stats, "fallback")

    assert "Bounds (mm)" in text
    assert "Z[-1..1]" in text
    assert "00:03 (fallback)" in text
    assert "Live est (stream): 00:04" in text


def test_format_gcode_stats_text_in_inches() -> None:
    app = _App()
    app.unit_mode = _Var("inch")
    stats = {"bounds": (25.4, 50.8, 0.0, 25.4, 0.0, 0.0), "time_min": 1.0, "rapid_min": 1.0}

    text = gcode_stats.format_gcode_stats_text(app, stats, "profile")

    assert "Bounds (in)" in text
    assert "X[1..2]" in text
    assert "00:02 (profile)" in text


def test_get_rapid_rates_prefers_grbl_rates() -> None:
    app = _App()
    app._rapid_rates = (100.0, 200.0, 300.0)

    rates, source = gcode_stats.get_rapid_rates_for_estimate(app)

    assert rates == (100.0, 200.0, 300.0)
    assert source == "grbl"


def test_get_rapid_rates_uses_estimate_rates_with_units() -> None:
    app = _App()
    app.unit_mode = _Var("inch")
    app.estimate_rate_x_var = _Var("10")
    app.estimate_rate_y_var = _Var("20")
    app.estimate_rate_z_var = _Var("30")

    rates, source = gcode_stats.get_rapid_rates_for_estimate(app)

    assert rates == (254.0, 508.0, 762.0)
    assert source == "estimate"


def test_get_rapid_rates_fallback() -> None:
    app = _App()
    app.fallback_rapid_rate = _Var("1200")

    rates, source = gcode_stats.get_rapid_rates_for_estimate(app)

    assert rates == (1200.0, 1200.0, 1200.0)
    assert source == "fallback"


def test_update_gcode_stats_streaming_mode_short_circuit() -> None:
    app = _App()
    app._gcode_streaming_mode = True

    gcode_stats.update_gcode_stats(app, ["G0 X0"])

    assert app.gcode_stats_var.value == "Preview only (streaming mode)"


def test_update_gcode_stats_no_lines() -> None:
    app = _App()
    app._stats_pending_request = ("pending",)
    app._stats_after_id = "id1"
    canceled = []

    def _after_cancel(after_id) -> None:
        canceled.append(after_id)

    app.after_cancel = _after_cancel

    gcode_stats.update_gcode_stats(app, [])

    assert app.gcode_stats_var.value == "No file loaded"
    assert app._stats_pending_request is None
    assert app._stats_after_id is None
    assert canceled == ["id1"]


def test_update_gcode_stats_cache_hit(monkeypatch) -> None:
    app = _App()
    app._rapid_rates = (100.0, 100.0, 100.0)
    app._accel_rates = (1.0, 1.0, 1.0)
    cache_key = gcode_stats.make_stats_cache_key(app, app._rapid_rates, app._accel_rates)
    stats = {"bounds": (0.0, 1.0, 0.0, 1.0, 0.0, 0.0), "time_min": 1.0, "rapid_min": 1.0}
    app._stats_cache[cache_key] = (stats, "grbl")
    captured = {}

    def _apply(app_obj, token, payload, source):
        captured["token"] = token
        captured["stats"] = payload
        captured["source"] = source

    monkeypatch.setattr(gcode_stats, "apply_gcode_stats", _apply)
    monkeypatch.setattr(gcode_stats, "compute_gcode_stats", lambda *_args, **_kwargs: 1 / 0)

    gcode_stats.update_gcode_stats(app, ["G0 X0"])

    assert captured["stats"] == stats
    assert captured["source"] == "grbl"


def test_update_gcode_stats_falls_back_when_parse_result_has_no_moves(monkeypatch) -> None:
    app = _App()
    parse_result = _Result([], (0.0, 1.0, 0.0, 1.0, 0.0, 0.0))
    calls = {"full": 0, "from_result": 0}
    expected = {"bounds": (0.0, 1.0, 0.0, 1.0, 0.0, 0.0), "time_min": 2.0, "rapid_min": 1.0}

    class _Thread:
        def __init__(self, target, daemon=False) -> None:
            _ = daemon
            self._target = target

        def start(self) -> None:
            self._target()

    def _compute(lines, rapid_rates, accel_rates, keep_running=None):
        _ = lines, rapid_rates, accel_rates, keep_running
        calls["full"] += 1
        return expected

    def _compute_from_result(result, rapid_rates, accel_rates):
        _ = result, rapid_rates, accel_rates
        calls["from_result"] += 1
        return {"bounds": None, "time_min": None, "rapid_min": None}

    monkeypatch.setattr(gcode_stats.threading, "Thread", _Thread)
    monkeypatch.setattr(gcode_stats, "compute_gcode_stats", _compute)
    monkeypatch.setattr(gcode_stats, "compute_gcode_stats_from_result", _compute_from_result)

    gcode_stats.update_gcode_stats(app, ["G0 X0"], parse_result=parse_result)

    assert calls["full"] == 1
    assert calls["from_result"] == 0
    assert app._last_stats == expected


def test_update_gcode_stats_debounces_and_cancels_previous_launch(monkeypatch) -> None:
    class _DebounceApp(_App):
        def __init__(self) -> None:
            super().__init__()
            self._stats_debounce_ms = 25
            self.after_calls = []
            self.after_cancel_calls = []

        def after(self, delay_ms: int, func):
            after_id = f"id{len(self.after_calls) + 1}"
            self.after_calls.append((delay_ms, func, after_id))
            return after_id

        def after_cancel(self, after_id) -> None:
            self.after_cancel_calls.append(after_id)

    calls = {"full": 0}

    class _Thread:
        def __init__(self, target, daemon=False) -> None:
            _ = daemon
            self._target = target

        def start(self) -> None:
            self._target()

    def _compute(lines, rapid_rates, accel_rates, keep_running=None):
        _ = lines, rapid_rates, accel_rates
        calls["full"] += 1
        assert callable(keep_running)
        assert keep_running() is True
        return {"bounds": (0.0, 1.0, 0.0, 1.0, 0.0, 0.0), "time_min": 1.0, "rapid_min": 1.0}

    app = _DebounceApp()
    monkeypatch.setattr(gcode_stats.threading, "Thread", _Thread)
    monkeypatch.setattr(gcode_stats, "compute_gcode_stats", _compute)

    lines_1 = ["G0 X0"]
    lines_2 = ["G0 X1"]
    gcode_stats.update_gcode_stats(app, lines_1)
    gcode_stats.update_gcode_stats(app, lines_2)

    assert len(app.after_calls) == 2
    assert app.after_cancel_calls == ["id1"]
    assert app._stats_pending_request is not None
    assert app._stats_pending_request[1] is lines_2

    # Execute only the latest scheduled callback.
    app.after_calls[-1][1]()
    assert calls["full"] == 1
    assert app._stats_pending_request is None


def test_update_gcode_stats_caps_cache_size(monkeypatch) -> None:
    class _Thread:
        def __init__(self, target, daemon=False) -> None:
            _ = daemon
            self._target = target

        def start(self) -> None:
            self._target()

    cache_limit = 3
    monkeypatch.setattr(gcode_stats, "GCODE_STATS_CACHE_MAX_ENTRIES", cache_limit)
    monkeypatch.setattr(gcode_stats.threading, "Thread", _Thread)
    monkeypatch.setattr(
        gcode_stats,
        "compute_gcode_stats",
        lambda *_args, **_kwargs: {
            "bounds": (0.0, 1.0, 0.0, 1.0, 0.0, 0.0),
            "time_min": 1.0,
            "rapid_min": 1.0,
        },
    )
    app = _App()
    app._rapid_rates = (100.0, 100.0, 100.0)
    app._accel_rates = (1.0, 1.0, 1.0)

    for idx in range(cache_limit + 2):
        app._gcode_hash = f"hash-{idx}"
        gcode_stats.update_gcode_stats(app, [f"G0 X{idx}"])

    assert len(app._stats_cache) == cache_limit
    assert ("hash-0", app._rapid_rates, app._accel_rates) not in app._stats_cache
