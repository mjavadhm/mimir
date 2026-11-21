"""Import boundaries between domain, application, runtime, and adapters."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "peyda_pm_bot"
_EXTERNAL = ("aiogram", "sqlalchemy", "alembic", "subprocess", "httpx")


def _modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_domain_and_application_do_not_import_io() -> None:
    for path in _files(SRC / "domain") + _files(SRC / "application"):
        for name in _modules(path):
            assert not name.startswith(_EXTERNAL), (path.name, name)
            assert not name.startswith("peyda_pm_bot.adapters"), (path.name, name)
            assert not name.startswith("peyda_pm_bot.runtime"), (path.name, name)
            assert not name.startswith("peyda_pm_bot.bootstrap"), (path.name, name)
    for path in _files(SRC / "domain"):
        for name in _modules(path):
            assert not name.startswith("peyda_pm_bot.application"), (path.name, name)
            assert not name.startswith("peyda_pm_bot.config"), (path.name, name)


def test_runtime_does_not_import_adapters_or_telegram() -> None:
    for path in _files(SRC / "runtime"):
        for name in _modules(path):
            assert not name.startswith(_EXTERNAL), (path.name, name)
            assert not name.startswith("peyda_pm_bot.adapters"), (path.name, name)


def test_bootstrap_import_does_not_construct_a_client() -> None:
    import peyda_pm_bot.bootstrap as bootstrap

    assert callable(bootstrap.build)
    assert callable(bootstrap.migrate)
