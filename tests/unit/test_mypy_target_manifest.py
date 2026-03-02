import pytest

from tools import check_mypy_targets

pytestmark = pytest.mark.unit


def _write_text(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_mypy_target_manifest_passes_when_in_sync(tmp_path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    _write_text(a, "x = 1\n")
    _write_text(b, "y = 2\n")
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    a.py,\n"
            "    b.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 2 source files.\n")

    rc = check_mypy_targets.main(
        ["check_mypy_targets.py", "--config", str(config), "--readme", str(readme)]
    )

    assert rc == 0


def test_mypy_target_manifest_fails_on_readme_count_mismatch(tmp_path) -> None:
    a = tmp_path / "a.py"
    _write_text(a, "x = 1\n")
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    a.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 2 source files.\n")

    rc = check_mypy_targets.main(
        ["check_mypy_targets.py", "--config", str(config), "--readme", str(readme)]
    )

    assert rc == 1


def test_mypy_target_manifest_fails_on_missing_file(tmp_path) -> None:
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    missing.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 1 source files.\n")

    rc = check_mypy_targets.main(
        ["check_mypy_targets.py", "--config", str(config), "--readme", str(readme)]
    )

    assert rc == 1


def test_mypy_target_manifest_fails_on_duplicate_entries(tmp_path) -> None:
    a = tmp_path / "a.py"
    _write_text(a, "x = 1\n")
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    a.py,\n"
            "    a.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 2 source files.\n")

    rc = check_mypy_targets.main(
        ["check_mypy_targets.py", "--config", str(config), "--readme", str(readme)]
    )

    assert rc == 1


def test_mypy_target_manifest_expected_count_passes(tmp_path) -> None:
    a = tmp_path / "a.py"
    _write_text(a, "x = 1\n")
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    a.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 1 source files.\n")

    rc = check_mypy_targets.main(
        [
            "check_mypy_targets.py",
            "--config",
            str(config),
            "--readme",
            str(readme),
            "--expected-count",
            "1",
        ]
    )

    assert rc == 0


def test_mypy_target_manifest_expected_count_fails(tmp_path) -> None:
    a = tmp_path / "a.py"
    _write_text(a, "x = 1\n")
    config = tmp_path / "mypy.ini"
    _write_text(
        config,
        (
            "[mypy]\n"
            "files =\n"
            "    a.py\n"
        ),
    )
    readme = tmp_path / "README.md"
    _write_text(readme, "Static typing gates currently run mypy against 1 source files.\n")

    rc = check_mypy_targets.main(
        [
            "check_mypy_targets.py",
            "--config",
            str(config),
            "--readme",
            str(readme),
            "--expected-count",
            "2",
        ]
    )

    assert rc == 1
