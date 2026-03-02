from contextlib import contextmanager
import types

import pytest

from simple_sender.application_gcode import GcodeMixin

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self) -> None:
        self.value = None
        self.calls: list[str] = []

    def set(self, value: str) -> None:
        self.value = value
        self.calls.append(value)


class _MacroExecutor:
    def __init__(self, payload, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc

    @contextmanager
    def macro_vars(self):
        if self._exc is not None:
            raise self._exc
        yield self._payload


class _App(GcodeMixin):
    pass


def _make_app(payload, exc: Exception | None = None):
    app = _App()
    app.macro_executor = _MacroExecutor(payload, exc=exc)
    app.tool_reference_var = _Var()
    app._tool_reference_last = object()
    return app


def test_sync_tool_reference_formats_float_value() -> None:
    state = types.SimpleNamespace(TOOL_REFERENCE=1.23456)
    app = _make_app({"macro": types.SimpleNamespace(state=state)})

    app._sync_tool_reference_label()

    assert app.tool_reference_var.value == "Tool Ref: 1.2346"


def test_sync_tool_reference_falls_back_to_string_for_invalid_float() -> None:
    state = types.SimpleNamespace(TOOL_REFERENCE="abc")
    app = _make_app({"macro": types.SimpleNamespace(state=state)})

    app._sync_tool_reference_label()

    assert app.tool_reference_var.value == "Tool Ref: abc"


def test_sync_tool_reference_no_update_when_value_unchanged() -> None:
    state = types.SimpleNamespace(TOOL_REFERENCE=2.0)
    app = _make_app({"macro": types.SimpleNamespace(state=state)})
    app._tool_reference_last = 2.0

    app._sync_tool_reference_label()

    assert app.tool_reference_var.calls == []


def test_sync_tool_reference_returns_on_expected_macro_context_errors() -> None:
    app = _make_app({}, exc=RuntimeError("macro vars unavailable"))
    app._tool_reference_last = "unchanged"

    app._sync_tool_reference_label()

    assert app._tool_reference_last == "unchanged"
    assert app.tool_reference_var.calls == []


def test_sync_tool_reference_surfaces_unexpected_macro_payload_errors() -> None:
    class _BadPayload(dict):
        def get(self, _key, _default=None):
            raise KeyError("boom")

    app = _make_app(_BadPayload())

    with pytest.raises(KeyError):
        app._sync_tool_reference_label()

