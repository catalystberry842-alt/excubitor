#!/usr/bin/env python3
"""Offline stand-in for parts of `hermes plugins validate` when Hermes isn't installed.

Checks: plugin.yaml present + parseable, register(ctx) succeeds, declared hooks match
what register() wires, no obvious core-override patterns in source. Exit 0 on pass.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = ROOT / "plugin.yaml"
    if not manifest_path.exists():
        errors.append("missing plugin.yaml")
        _report(errors, warnings)
        return 1

    try:
        import yaml  # type: ignore
    except Exception:
        # Minimal YAML subset parser for our simple manifest
        text = manifest_path.read_text(encoding="utf-8")
        name_m = re.search(r"^name:\s*(\S+)", text, re.M)
        hooks = re.findall(r"^provides_hooks:\n((?:  - .+\n)+)", text, re.M)
        declared = []
        if hooks:
            declared = re.findall(r"- (\S+)", hooks[0])
        manifest = {
            "name": name_m.group(1) if name_m else None,
            "provides_hooks": declared,
        }
    else:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    if not manifest.get("name"):
        errors.append("plugin.yaml missing name")
    if not (ROOT / "__init__.py").exists():
        errors.append("missing __init__.py")

    declared_hooks = list(manifest.get("provides_hooks") or [])

    # Static scan for core overrides
    ban = re.compile(
        r"(AIAgent\.\w+\s*=|setattr\(\s*(?:sys\.modules|AIAgent)|sys\.modules\[)"
    )
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        if ban.search(src):
            errors.append(f"possible core override in {path.relative_to(ROOT)}")

    # Load + register
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "excubitor_validate",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "excubitor_validate"
    mod.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules["excubitor_validate"] = mod
    assert spec.loader
    spec.loader.exec_module(mod)

    registered_hooks: list[str] = []
    registered_commands: list[str] = []

    class Ctx:
        def register_hook(self, name, fn):
            registered_hooks.append(name)

        def register_command(self, name, handler, description="", **kw):
            registered_commands.append(name)

        def get_config(self, key, default=None):
            return default

        def set_config(self, key, value):
            return None

    try:
        mod.register(Ctx())
    except Exception as exc:
        errors.append(f"register(ctx) raised: {exc}")

    missing = sorted(set(declared_hooks) - set(registered_hooks))
    extra = sorted(set(registered_hooks) - set(declared_hooks))
    if missing:
        errors.append(f"declared but not registered hooks: {missing}")
    if extra:
        warnings.append(f"registered but undeclared hooks: {extra}")
    if "excubitor" not in registered_commands:
        errors.append("/excubitor command not registered")

    # AST: every public hook callback accepts **kwargs (Hermes contract tip)
    init_ast = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    for node in init_ast.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("on_"):
            if not any(isinstance(a, ast.arg) and a.arg == "kwargs" for a in [] ) and not any(
                getattr(node.args, "kwarg", None)
            ):
                # only warn for top-level on_* that look like hooks
                if node.args.kwarg is None:
                    warnings.append(f"{node.name} should accept **kwargs")

    _report(errors, warnings)
    return 1 if errors else 0


def _report(errors: list[str], warnings: list[str]) -> None:
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if not errors:
        print("OK: local validate passed (still run `hermes plugins validate` before catalog PR)")


if __name__ == "__main__":
    raise SystemExit(main())
