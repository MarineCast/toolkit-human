from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

PACKAGE_NAME = "human.viewshed"
PACKAGE_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "human" / "viewshed"
)


def _python_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join([PACKAGE_NAME, *parts]) if parts else PACKAGE_NAME
        modules[module] = path
    return modules


def _internal_dependencies(module: str, path: Path, known: set[str]) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    dependencies: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = "." * node.level + (node.module or "")
                base = importlib.util.resolve_name(prefix, package)
            else:
                base = node.module or ""
            candidates = (
                [f"{base}.{alias.name}" for alias in node.names] if not node.module else [base]
            )
        else:
            continue
        for candidate in candidates:
            if candidate in known and candidate != module:
                dependencies.add(candidate)
    return dependencies


def test_viewshed_uses_explicit_imports_and_layering() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                violations.append(f"{relative}:{node.lineno}: wildcard import")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "globals"
            ):
                violations.append(f"{relative}:{node.lineno}: globals().update")
            if relative.parts[:1] == ("prepare",) and isinstance(node, ast.ImportFrom):
                imported = "." * node.level + (node.module or "")
                if "weights" in imported.split("."):
                    violations.append(f"{relative}:{node.lineno}: prepare depends on weights")
    assert not violations, "\n".join(violations)


def test_viewshed_internal_import_graph_is_acyclic() -> None:
    modules = _python_modules()
    graph = {
        module: _internal_dependencies(module, path, set(modules))
        for module, path in modules.items()
    }
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            start = visiting.index(module)
            cycle = [*visiting[start:], module]
            raise AssertionError("viewshed import cycle: " + " -> ".join(cycle))
        if module in visited:
            return
        visiting.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)


def test_viewshed_api_is_independent_of_cli_and_argparse() -> None:
    violations: list[str] = []
    for path in (PACKAGE_ROOT / "api").rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            for imported in imports:
                if imported == "argparse" or "cli" in imported.split("."):
                    violations.append(f"{relative}:{node.lineno}: imports {imported}")
    assert not violations, "\n".join(violations)
