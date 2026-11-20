"""Executable boundaries: imports must express the architecture, not evade it."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "peyda_pm_bot"


def test_layer_imports_and_cycles():
    graph = {}
    for path in ROOT.rglob("*.py"):
        module = "peyda_pm_bot." + ".".join(path.relative_to(ROOT).with_suffix("").parts)
        module = module.removesuffix(".__init__")
        layer = path.relative_to(ROOT).parts[0]
        dependencies = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"Use absolute imports: {path}"
                dependencies.add(node.module or "")
        if layer not in {"bootstrap.py", "__main__.py"}:
            assert "peyda_pm_bot.bootstrap" not in dependencies
        if layer == "domain":
            assert not any(
                name.split(".")[0] in {"subprocess", "socket", "sqlite3", "asyncio"}
                for name in dependencies
            )
            assert all(
                name.split(".")[0] in sys.stdlib_module_names
                or name.startswith("peyda_pm_bot.domain")
                for name in dependencies
            ), (module, dependencies)
        if layer == "application":
            assert not any(
                name.startswith(
                    ("peyda_pm_bot.adapters", "peyda_pm_bot.runtime", "peyda_pm_bot.bootstrap")
                )
                or name.split(".")[0] in {"aiogram", "sqlalchemy", "subprocess"}
                for name in dependencies
            ), (module, dependencies)
        if layer == "runtime":
            assert not any(name.startswith("peyda_pm_bot.adapters") for name in dependencies)
        graph[module] = {name for name in dependencies if name.startswith("peyda_pm_bot.")}

    def visit(module, stack, done):
        assert module not in stack, f"Dependency cycle: {stack} -> {module}"
        if module in done:
            return
        for dependency in graph.get(module, set()):
            visit(dependency, stack | {module}, done)
        done.add(module)

    done = set()
    for module in graph:
        visit(module, set(), done)


def test_imports_have_no_resource_side_effects(tmp_path):
    script = """
import asyncio, importlib, pkgutil, socket, sqlite3, threading
import peyda_pm_bot

def forbidden(*args, **kwargs):
    raise AssertionError("Import opened a resource")

socket.socket = forbidden
sqlite3.connect = forbidden
asyncio.create_task = forbidden
before = set(threading.enumerate())
for item in pkgutil.walk_packages(peyda_pm_bot.__path__, peyda_pm_bot.__name__ + "."):
    importlib.import_module(item.name)
assert set(threading.enumerate()) == before
"""
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, check=True)
    assert list(tmp_path.iterdir()) == []
