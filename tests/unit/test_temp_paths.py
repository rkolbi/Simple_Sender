import os

from simple_sender.utils import temp_paths


def test_get_preferred_temp_dir_prefers_pi_tmp_when_available(monkeypatch) -> None:
    temp_paths.get_preferred_temp_dir.cache_clear()
    monkeypatch.setattr(temp_paths, "detect_raspberry_pi", lambda: True)
    monkeypatch.setattr(temp_paths, "_ensure_writable_dir", lambda path: path == temp_paths.PI_TMP_DIR)

    result = temp_paths.get_preferred_temp_dir()

    assert result == temp_paths.PI_TMP_DIR
    temp_paths.get_preferred_temp_dir.cache_clear()


def test_get_preferred_temp_dir_falls_back_to_system_temp(monkeypatch) -> None:
    temp_paths.get_preferred_temp_dir.cache_clear()
    base_tmp = os.path.join("X:", "tmpdir")
    expected = os.path.join(base_tmp, temp_paths.APP_TEMP_SUBDIR)
    monkeypatch.setattr(temp_paths, "detect_raspberry_pi", lambda: False)
    monkeypatch.setattr(temp_paths.tempfile, "gettempdir", lambda: base_tmp)
    monkeypatch.setattr(temp_paths, "_ensure_writable_dir", lambda path: path == expected)

    result = temp_paths.get_preferred_temp_dir()

    assert result == expected
    temp_paths.get_preferred_temp_dir.cache_clear()
