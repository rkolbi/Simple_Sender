import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.dialogs import file_dialogs

pytestmark = pytest.mark.ui


class _App:
    def winfo_exists(self) -> bool:
        return True


class _Var:
    def __init__(self, value: float) -> None:
        self._value = value

    def get(self) -> float:
        return self._value


def test_run_file_dialog_sets_parent_to_app_by_default() -> None:
    app = _App()
    captured = {}

    def _fake_dialog(*_args, **kwargs):
        captured["parent"] = kwargs.get("parent")
        return "chosen"

    result = file_dialogs.run_file_dialog(app, _fake_dialog, title="Save")

    assert result == "chosen"
    assert captured["parent"] is app


def test_run_file_dialog_respects_explicit_parent() -> None:
    app = _App()
    explicit_parent = object()
    captured = {}

    def _fake_dialog(*_args, **kwargs):
        captured["parent"] = kwargs.get("parent")
        return "chosen"

    result = file_dialogs.run_file_dialog(
        app,
        _fake_dialog,
        title="Save",
        parent=explicit_parent,
    )

    assert result == "chosen"
    assert captured["parent"] is explicit_parent


def test_run_file_dialog_blocks_nested_reentry() -> None:
    app = _App()
    nested_result = {"value": None}

    def _fake_dialog(*_args, **_kwargs):
        nested_result["value"] = file_dialogs.run_file_dialog(app, lambda *_a, **_k: "nested")
        return "outer"

    result = file_dialogs.run_file_dialog(app, _fake_dialog)

    assert result == "outer"
    assert nested_result["value"] == ""


def test_dialog_target_scale_has_minimum_floor() -> None:
    app = _App()
    app.ui_scale = _Var(1.0)

    assert file_dialogs._dialog_target_scale(app, 1.0) == 1.4


def test_dialog_target_scale_uses_larger_ui_scale() -> None:
    app = _App()
    app.ui_scale = _Var(2.0)

    assert file_dialogs._dialog_target_scale(app, 1.0) == 2.0
