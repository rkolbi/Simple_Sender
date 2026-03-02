import json
import os
from pathlib import Path

import pytest

from simple_sender.utils import config
from simple_sender.utils.config import Settings, _deep_merge_defaults
from simple_sender.utils.exceptions import SettingsLoadError, SettingsValidationError

pytestmark = pytest.mark.unit


def test_deep_merge_defaults_merges_nested_and_extra_keys() -> None:
    defaults = {"a": 1, "b": {"c": 2, "d": 3}}
    loaded = {"b": {"c": 20}, "e": 5}

    merged = _deep_merge_defaults(defaults, loaded)

    assert merged == {"a": 1, "b": {"c": 20, "d": 3}, "e": 5}


def test_defaults_are_deep_copied(tmp_path: Path) -> None:
    store = Settings(str(tmp_path / "settings.json"))

    store.data["view_3d"]["zoom"] = 2.0
    store.add_recent_file("example.nc")

    assert store.data["recent_files"] == ["example.nc"]
    assert store.data["view_3d"] is not config.DEFAULT_SETTINGS["view_3d"]
    assert config.DEFAULT_SETTINGS["view_3d"]["zoom"] != 2.0
    assert config.DEFAULT_SETTINGS["recent_files"] == []


def test_get_default_settings_dir_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SIMPLE_SENDER_CONFIG_DIR", r"C:\config")

    assert config.get_default_settings_dir() == r"C:\config"


def test_get_default_settings_dir_windows_prefers_localappdata(monkeypatch) -> None:
    monkeypatch.delenv("SIMPLE_SENDER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Local")
    monkeypatch.delenv("APPDATA", raising=False)

    assert config.get_default_settings_dir() == os.path.join(r"C:\Local", "SimpleSender")


def test_get_default_settings_dir_non_windows_uses_xdg(monkeypatch) -> None:
    monkeypatch.delenv("SIMPLE_SENDER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")

    assert config.get_default_settings_dir() == os.path.join("/tmp/xdg", "SimpleSender")


def test_get_default_settings_dir_non_windows_falls_back_home(monkeypatch) -> None:
    monkeypatch.delenv("SIMPLE_SENDER_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config.os.path, "expanduser", lambda _path: "/home/tester")

    assert config.get_default_settings_dir() == os.path.join("/home/tester", "SimpleSender")


def test_get_settings_path_uses_default_dir(monkeypatch, tmp_path: Path) -> None:
    settings_dir = tmp_path / "cfg"
    created = []

    monkeypatch.setattr(config, "get_default_settings_dir", lambda: str(settings_dir))
    monkeypatch.setattr(config.os, "makedirs", lambda path, exist_ok=True: created.append(path))
    monkeypatch.setattr(config.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(config.os, "access", lambda _path, _mode: True)

    path = config.get_settings_path()

    assert path == os.path.join(str(settings_dir), config.SETTINGS_FILENAME)
    assert created == [str(settings_dir)]


def test_get_settings_path_uses_fallback_dir(monkeypatch, tmp_path: Path) -> None:
    settings_dir = tmp_path / "cfg"
    calls = []

    def _makedirs(path, exist_ok=True):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("nope")

    monkeypatch.setattr(config, "get_default_settings_dir", lambda: str(settings_dir))
    monkeypatch.setattr(config.os, "makedirs", _makedirs)
    monkeypatch.setattr(config.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(config.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(config.os.path, "expanduser", lambda _path: str(tmp_path))

    path = config.get_settings_path()

    assert path == os.path.join(str(tmp_path / ".simple_sender"), config.SETTINGS_FILENAME)
    assert calls[0] == str(settings_dir)


def test_get_settings_path_falls_back_to_module_dir(monkeypatch, tmp_path: Path) -> None:
    settings_dir = tmp_path / "cfg"

    def _makedirs(_path, exist_ok=True):
        raise OSError("nope")

    monkeypatch.setattr(config, "get_default_settings_dir", lambda: str(settings_dir))
    monkeypatch.setattr(config.os, "makedirs", _makedirs)
    monkeypatch.setattr(config.os.path, "expanduser", lambda _path: str(tmp_path))

    path = config.get_settings_path()

    expected_dir = os.path.dirname(config.__file__)
    assert path == os.path.join(expected_dir, config.SETTINGS_FILENAME)


def test_settings_load_missing_file_returns_false(tmp_path: Path) -> None:
    settings_path = tmp_path / "missing.json"
    store = Settings(str(settings_path))

    assert store.load() is False


def test_settings_load_invalid_json_raises(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{", encoding="utf-8")
    store = Settings(str(settings_path))

    with pytest.raises(SettingsLoadError):
        store.load()


def test_settings_validate_rejects_invalid_interval_and_unit(tmp_path: Path) -> None:
    store = Settings(str(tmp_path / "settings.json"))
    store.data["status_poll_interval"] = 0
    with pytest.raises(SettingsValidationError):
        store.validate()

    store.data["status_poll_interval"] = 0.5
    store.data["unit_mode"] = "yards"
    with pytest.raises(SettingsValidationError):
        store.validate()


def test_settings_save_creates_backup(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"baud_rate": 9600}), encoding="utf-8")
    store = Settings(str(settings_path))
    store.data["baud_rate"] = 115200

    store.save()

    backup_path = Path(str(settings_path) + config.SETTINGS_BACKUP_SUFFIX)
    assert backup_path.exists()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["baud_rate"] == 115200


def test_settings_export_and_import(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    export_path = tmp_path / "export.json"
    store = Settings(str(settings_path))
    store.data["baud_rate"] = 9600
    store.export_to_file(str(export_path))

    new_store = Settings(str(settings_path))
    new_store.import_from_file(str(export_path))

    assert new_store.data["baud_rate"] == 9600
    assert new_store.data["unit_mode"] == "mm"


def test_settings_roundtrip_persists_grbl_popup_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    store = Settings(str(settings_path))
    store.data["grbl_popup_enabled"] = False
    store.data["grbl_popup_auto_dismiss_sec"] = 0.0
    store.data["grbl_popup_dedupe_sec"] = 1.25

    store.save()

    reloaded = Settings(str(settings_path))
    assert reloaded.load() is True
    assert reloaded.data["grbl_popup_enabled"] is False
    assert reloaded.data["grbl_popup_auto_dismiss_sec"] == 0.0
    assert reloaded.data["grbl_popup_dedupe_sec"] == 1.25


def test_get_recent_files_filters_missing(tmp_path: Path) -> None:
    existing = tmp_path / "exists.nc"
    existing.write_text("G0", encoding="utf-8")
    store = Settings(str(tmp_path / "settings.json"))
    store.data["recent_files"] = [str(existing), str(tmp_path / "missing.nc")]

    assert store.get_recent_files() == [str(existing)]


def test_default_auto_level_avoidance_areas() -> None:
    settings = config.DEFAULT_SETTINGS["auto_level_settings"]

    avoidance = settings.get("avoidance_areas")

    assert isinstance(avoidance, list)
    assert len(avoidance) == 8
    assert avoidance[0]["enabled"] is False
    assert avoidance[0]["radius"] == 20.0
