import json
import zipfile

from simple_sender.ui.dialogs import backup_bundle


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def config(self, *, text: str) -> None:
        self.text = text


class _Var:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _Executor:
    def __init__(self, search_dirs) -> None:
        self._macro_search_dirs = tuple(search_dirs)


class _Panel:
    def __init__(self) -> None:
        self.refreshed = False

    def refresh(self) -> None:
        self.refreshed = True


class _App:
    def __init__(self, *, settings_path: str, search_dirs: tuple[str, ...]) -> None:
        self.settings_path = settings_path
        self.macro_executor = _Executor(search_dirs)
        self.version_var = _Var("vtest")
        self.status = _Status()
        self.macro_panel = _Panel()
        self.saved = False

    def _save_settings(self) -> None:
        self.saved = True


def test_export_backup_bundle_writes_expected_members(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"k":"v"}', encoding="utf-8")
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    (macro_dir / "Macro-1").write_text("Macro One\nTip\nG0 X0\n", encoding="utf-8")
    (macro_dir / "checklist-run.chk").write_text("Step 1\n", encoding="utf-8")
    bundle_path = tmp_path / "bundle.zip"

    app = _App(settings_path=str(settings_path), search_dirs=(str(macro_dir),))

    monkeypatch.setattr(backup_bundle, "run_file_dialog", lambda *_args, **_kwargs: str(bundle_path))
    monkeypatch.setattr(
        backup_bundle,
        "messagebox",
        type(
            "MB",
            (),
            {"showinfo": staticmethod(lambda *_a, **_k: None), "showerror": staticmethod(lambda *_a, **_k: None)},
        )(),
    )

    backup_bundle.export_backup_bundle(app)

    assert app.saved is True
    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "settings/settings.json" in names
        assert "macros/Macro-1" in names
        assert "macros/checklist-run.chk" in names
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["kind"] == "simple_sender_backup_bundle"


def test_import_backup_bundle_restores_files_and_refreshes_macros(tmp_path, monkeypatch) -> None:
    target_settings = tmp_path / "settings.json"
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    bundle_path = tmp_path / "import_bundle.zip"

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"kind": "simple_sender_backup_bundle", "created": "2026-01-01T00:00:00"}),
        )
        zf.writestr("settings/settings.json", '{"foo": 1}')
        zf.writestr("macros/Macro-2", "M2\nTip\nG21\n")

    app = _App(settings_path=str(target_settings), search_dirs=(str(macro_dir),))
    monkeypatch.setattr(backup_bundle, "run_file_dialog", lambda *_args, **_kwargs: str(bundle_path))
    monkeypatch.setattr(
        backup_bundle,
        "messagebox",
        type(
            "MB",
            (),
            {
                "askyesno": staticmethod(lambda *_a, **_k: True),
                "showinfo": staticmethod(lambda *_a, **_k: None),
                "showerror": staticmethod(lambda *_a, **_k: None),
            },
        )(),
    )

    backup_bundle.import_backup_bundle(app)

    assert target_settings.read_text(encoding="utf-8") == '{"foo": 1}'
    assert (macro_dir / "Macro-2").read_text(encoding="utf-8") == "M2\nTip\nG21\n"
    assert app.macro_panel.refreshed is True


def test_import_backup_bundle_rejects_missing_manifest(tmp_path, monkeypatch) -> None:
    target_settings = tmp_path / "settings.json"
    target_settings.write_text('{"safe": true}', encoding="utf-8")
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    bundle_path = tmp_path / "import_bundle_bad.zip"

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("settings/settings.json", '{"foo": 1}')

    app = _App(settings_path=str(target_settings), search_dirs=(str(macro_dir),))
    monkeypatch.setattr(backup_bundle, "run_file_dialog", lambda *_args, **_kwargs: str(bundle_path))

    calls = {"error": 0}

    def _showerror(*_args, **_kwargs) -> None:
        calls["error"] += 1

    monkeypatch.setattr(
        backup_bundle,
        "messagebox",
        type(
            "MB",
            (),
            {
                "askyesno": staticmethod(lambda *_a, **_k: True),
                "showinfo": staticmethod(lambda *_a, **_k: None),
                "showerror": staticmethod(_showerror),
            },
        )(),
    )

    backup_bundle.import_backup_bundle(app)

    assert calls["error"] == 1
    assert target_settings.read_text(encoding="utf-8") == '{"safe": true}'
