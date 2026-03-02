import json
from pathlib import Path

import pytest

from simple_sender.utils.config import DEFAULT_SETTINGS, Settings
from simple_sender.utils.exceptions import SettingsLoadError, SettingsSaveError, SettingsValidationError

pytestmark = pytest.mark.unit


def test_settings_load_merges_defaults(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"baud_rate": 9600, "custom_key": "value"}), encoding="utf-8")

    store = Settings(str(settings_path))
    assert store.load()
    assert store.data["baud_rate"] == 9600
    assert store.data["custom_key"] == "value"
    assert store.data["unit_mode"] == "mm"


def test_settings_load_invalid_json_raises(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{", encoding="utf-8")

    store = Settings(str(settings_path))
    with pytest.raises(SettingsLoadError):
        store.load()


def test_settings_validate_rejects_invalid_values(tmp_path: Path) -> None:
    store = Settings(str(tmp_path / "settings.json"))
    store.data["baud_rate"] = 123
    with pytest.raises(SettingsValidationError):
        store.validate()


def test_settings_load_migrates_legacy_jog_step(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"jog_step": 2.5}), encoding="utf-8")

    store = Settings(str(settings_path))
    assert store.load()
    assert store.data["step_xy"] == 2.5
    assert store.data["step_z"] == 2.5


def test_settings_load_repairs_invalid_core_values(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({
            "baud_rate": 123,
            "status_poll_interval": 0,
            "unit_mode": "yards",
        }),
        encoding="utf-8",
    )

    store = Settings(str(settings_path))
    assert store.load()
    assert store.data["baud_rate"] == DEFAULT_SETTINGS["baud_rate"]
    assert store.data["status_poll_interval"] == DEFAULT_SETTINGS["status_poll_interval"]
    assert store.data["unit_mode"] == DEFAULT_SETTINGS["unit_mode"]


def test_settings_get_set_nested_key(tmp_path: Path) -> None:
    store = Settings(str(tmp_path / "settings.json"))
    store.set("3d_view_settings.zoom", 2.5)
    assert store.get("3d_view_settings.zoom") == 2.5


def test_settings_recent_files_are_trimmed(tmp_path: Path) -> None:
    store = Settings(str(tmp_path / "settings.json"))
    store.data["max_recent_files"] = 2
    first = tmp_path / "one.nc"
    second = tmp_path / "two.nc"
    third = tmp_path / "three.nc"
    first.write_text("G0", encoding="utf-8")
    second.write_text("G0", encoding="utf-8")
    third.write_text("G0", encoding="utf-8")

    store.add_recent_file(str(first))
    store.add_recent_file(str(second))
    store.add_recent_file(str(third))

    recent = store.get_recent_files()
    assert recent == [str(third), str(second)]


def test_settings_save_error_cleans_temp(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    store = Settings(str(settings_path))

    def _raise_replace(self, _target):  # noqa: ANN001
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", _raise_replace)

    with pytest.raises(SettingsSaveError):
        store.save()

    temp_path = Path(str(settings_path) + ".tmp")
    assert not temp_path.exists()
