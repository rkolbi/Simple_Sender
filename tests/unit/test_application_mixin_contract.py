import ast
from pathlib import Path

import pytest

pytest.importorskip("tkinter")

from simple_sender import application

pytestmark = pytest.mark.unit


def _parse_application_module() -> ast.Module:
    source = Path("simple_sender/application.py").read_text(encoding="utf-8")
    return ast.parse(source)


def _get_app_class(module: ast.Module) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "App":
            return node
    raise AssertionError("App class not found in simple_sender/application.py")


def _type_checking_stub_names(app_class: ast.ClassDef) -> set[str]:
    for stmt in app_class.body:
        if not isinstance(stmt, ast.If):
            continue
        if not isinstance(stmt.test, ast.Name) or stmt.test.id != "TYPE_CHECKING":
            continue
        return {fn.name for fn in stmt.body if isinstance(fn, ast.FunctionDef)}
    raise AssertionError("TYPE_CHECKING block not found in App class")


def _mixin_callable_names() -> set[str]:
    names: set[str] = set()
    for mixin in application._APP_MIXINS:
        for name, member in mixin.__dict__.items():
            if name.startswith("__"):
                continue
            if not callable(member):
                continue
            names.add(name)
    return names


def _mixin_methods_referenced_in_init(app_class: ast.ClassDef, mixin_names: set[str]) -> set[str]:
    init_fn = next(
        stmt for stmt in app_class.body if isinstance(stmt, ast.FunctionDef) and stmt.name == "__init__"
    )
    refs: set[str] = set()
    for node in ast.walk(init_fn):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "self":
            continue
        if node.attr in mixin_names:
            refs.add(node.attr)
    return refs


def test_type_checking_stubs_match_curated_contract() -> None:
    module = _parse_application_module()
    app_class = _get_app_class(module)
    stubs = _type_checking_stub_names(app_class)
    expected = set(application._APP_TYPE_CHECKING_STUBS)

    assert stubs == expected


def test_curated_stubs_are_backed_by_mixin_methods() -> None:
    mixin_names = _mixin_callable_names()
    expected = set(application._APP_TYPE_CHECKING_STUBS)

    assert expected <= mixin_names


def test_app_init_mixin_method_references_are_stubbed() -> None:
    module = _parse_application_module()
    app_class = _get_app_class(module)
    mixin_names = _mixin_callable_names()
    init_refs = _mixin_methods_referenced_in_init(app_class, mixin_names)
    expected = set(application._APP_TYPE_CHECKING_STUBS)

    assert init_refs == expected


def test_installed_app_methods_include_curated_contract() -> None:
    for method_name in application._APP_TYPE_CHECKING_STUBS:
        method = getattr(application.App, method_name, None)
        assert callable(method), f"Expected callable App.{method_name} from mixin installation"
