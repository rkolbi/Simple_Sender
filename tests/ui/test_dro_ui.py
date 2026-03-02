import pytest

pytest.importorskip("tkinter")

from simple_sender.ui import dro

pytestmark = pytest.mark.ui


class _DummyWidget:
    def __init__(self, *_args, **kwargs) -> None:
        self.kwargs = kwargs
        self.grid_kwargs = None
        self.pack_kwargs = None

    def grid(self, **kwargs) -> None:
        self.grid_kwargs = kwargs

    def pack(self, **kwargs) -> None:
        self.pack_kwargs = kwargs

    def cget(self, key: str):
        if key == "foreground":
            return self.kwargs.get("foreground", "#000000")
        return self.kwargs.get(key)


class _DummyButton(_DummyWidget):
    pass


class _DummyLabel(_DummyWidget):
    pass


class _DummyFrame(_DummyWidget):
    pass


class _DummyTtk:
    Frame = _DummyFrame
    Label = _DummyLabel
    Button = _DummyButton


def test_dro_row_sets_label_cache() -> None:
    kb_calls = []

    class _App:
        dro_value_font = "font"
        HIDDEN_MPOS_BUTTON_STYLE = "hidden"
        _wpos_value_labels = {}
        _wpos_label_default_fg = {}

    app = _App()

    btn = dro.dro_row(
        app,
        object(),
        "X",
        object(),
        lambda: None,
        ttk_mod=_DummyTtk,
        set_kb_id_func=lambda _btn, key: kb_calls.append(key),
    )

    assert app._wpos_value_labels["X"]
    assert app._wpos_label_default_fg["X"] == "#000000"
    assert kb_calls == ["zero_x"]
    assert isinstance(btn, _DummyButton)


def test_dro_value_row_builds_hidden_button() -> None:
    class _App:
        dro_value_font = "font"
        HIDDEN_MPOS_BUTTON_STYLE = "hidden"

    app = _App()

    dro.dro_value_row(app, object(), "Y", object(), ttk_mod=_DummyTtk)


def test_dro_value_row_builds_action_button_when_command_is_provided() -> None:
    kb_calls = []

    class _App:
        dro_value_font = "font"
        HIDDEN_MPOS_BUTTON_STYLE = "hidden"
        mpos_button_style = "mpos"

    app = _App()
    btn = dro.dro_value_row(
        app,
        object(),
        "Z",
        object(),
        ttk_mod=_DummyTtk,
        set_kb_id_func=lambda _btn, key: kb_calls.append(key),
        action_text="Jog Z to:",
        action_cmd=lambda: None,
        action_kb_id="jog_mpos_z_to",
    )

    assert isinstance(btn, _DummyButton)
    assert btn.kwargs.get("text") == "Jog Z to:"
    assert btn.kwargs.get("style") == "TButton"
    assert kb_calls == ["jog_mpos_z_to"]
