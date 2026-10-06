from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "agentpay"
DOMAIN_PACKAGE = SOURCE_ROOT / "domain"

FORBIDDEN_IN_DOMAIN = frozenset(
    {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "alembic",
        "redis",
        "httpx",
        "aiohttp",
        "requests",
        "razorpay",
        "openai",
        "anthropic",
        "pydantic",
        "sqlmodel",
        "agentpay.api",
        "agentpay.application",
        "agentpay.infrastructure",
        "agentpay.adapters",
        "agentpay.ai",
        "agentpay.security",
        "agentpay.workers",
    }
)


def python_files(package_root: Path) -> list[Path]:
    return sorted(package_root.rglob("*.py"))


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def forbidden_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for module in imported_modules(path):
        root = module.split(".")[0]
        if module in FORBIDDEN_IN_DOMAIN or root in FORBIDDEN_IN_DOMAIN:
            found.add(module)
    return found


def relative_imports_escaping_domain(path: Path) -> set[str]:
    """A level-1 relative import stays in the current package; level >= 2 climbs out of domain."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    escaping: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 2:
            escaping.add("." * node.level + (node.module or ""))
    return escaping


def test_domain_layer_is_discoverable() -> None:
    assert DOMAIN_PACKAGE.is_dir(), "the domain package moved; update SOURCE_ROOT in this test"


def test_domain_layer_imports_no_framework_or_outer_layer() -> None:
    violations = {
        str(path.relative_to(SOURCE_ROOT)): sorted(forbidden_imports(path))
        for path in python_files(DOMAIN_PACKAGE)
        if forbidden_imports(path)
    }
    assert not violations, f"domain must stay technology-agnostic: {violations}"


def test_domain_layer_does_not_import_relative_modules_outside_domain() -> None:
    violations = {
        str(path.relative_to(SOURCE_ROOT)): sorted(relative_imports_escaping_domain(path))
        for path in python_files(DOMAIN_PACKAGE)
        if relative_imports_escaping_domain(path)
    }
    assert not violations, violations


def test_every_source_file_is_valid_python_and_annotated() -> None:
    untyped: list[str] = []
    for path in python_files(SOURCE_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.returns is None
                and node.name != "__init__"
            ):
                untyped.append(f"{path.relative_to(SOURCE_ROOT)}::{node.name}")
    assert not untyped, f"missing return annotations: {untyped}"


def test_boundary_checker_actually_catches_a_forbidden_import(tmp_path: Path) -> None:
    """Guards the guard: a checker that silently passes everything is worse than none."""
    offender = tmp_path / "bad_domain_module.py"
    offender.write_text("import fastapi\nfrom sqlalchemy import select\n", encoding="utf-8")
    assert forbidden_imports(offender) == {"fastapi", "sqlalchemy"}

    offender.write_text("from agentpay.infrastructure import db\n", encoding="utf-8")
    assert forbidden_imports(offender) == {"agentpay.infrastructure"}


def test_boundary_checker_allows_the_imports_the_domain_actually_needs(tmp_path: Path) -> None:
    compliant = tmp_path / "good_domain_module.py"
    compliant.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from agentpay.domain.money import Money\n"
        "from .errors import DomainError\n",
        encoding="utf-8",
    )
    assert forbidden_imports(compliant) == set()
    assert relative_imports_escaping_domain(compliant) == set()


def test_boundary_checker_catches_a_relative_import_that_climbs_out_of_domain(
    tmp_path: Path,
) -> None:
    offender = tmp_path / "escaping_module.py"
    offender.write_text("from ..infrastructure import db\n", encoding="utf-8")
    assert relative_imports_escaping_domain(offender) == {"..infrastructure"}
