import ast
from pathlib import Path


def _module_name_for_path(path: Path, package_root: Path) -> str:
    rel = path.relative_to(package_root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import_from(path: Path, package_root: Path, module: str | None, level: int) -> str | None:
    if level <= 0:
        return module
    current_module = _module_name_for_path(path, package_root)
    current_package = current_module
    if path.name != "__init__.py":
        current_package = current_module.rsplit(".", 1)[0]
    parts = current_package.split(".")
    up = level - 1
    if up > len(parts):
        return None
    base = parts[: len(parts) - up]
    if module:
        return ".".join(base + module.split("."))
    return ".".join(base)


def test_runtime_package_does_not_import_widgets_shim() -> None:
    package_root = Path(__file__).resolve().parents[1] / "simple_sender"
    offenders: list[str] = []

    for path in sorted(package_root.rglob("*.py")):
        if path.name == "widgets.py":
            continue
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "simple_sender.ui.widgets":
                        rel = path.relative_to(package_root.parent)
                        offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_import_from(path, package_root, node.module, node.level)
                if resolved == "simple_sender.ui.widgets":
                    rel = path.relative_to(package_root.parent)
                    offenders.append(f"{rel}:{node.lineno}")
                if resolved == "simple_sender.ui":
                    for alias in node.names:
                        if alias.name == "widgets":
                            rel = path.relative_to(package_root.parent)
                            offenders.append(f"{rel}:{node.lineno}")
                if resolved is not None and node.module is None:
                    for alias in node.names:
                        if alias.name == "widgets" and resolved.startswith("simple_sender.ui"):
                            rel = path.relative_to(package_root.parent)
                            offenders.append(f"{rel}:{node.lineno}")

    assert offenders == [], "Found runtime imports of simple_sender.ui.widgets:\n" + "\n".join(offenders)
