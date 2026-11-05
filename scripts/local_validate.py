#!/usr/bin/env python3
"""Offline stand-in for parts of `hermes plugins validate` when Hermes isn't installed.

Checks: plugin.yaml present, register(ctx) succeeds, declared hooks match what
register() wires, no obvious core-override patterns. Exit 0 on pass.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = ROOT / "plugin.yaml"
    if not manifest_path.exists():
        errors.append("missing plugin.yaml")
        _report(errors, warnings)
        return 1

    text = manifest_path.read_text(encoding="utf-8")
    name_m = re.search(r"^name:\s*(\S+)", text, re.M)
    hooks_block = re.search(r"^provides_hooks:\n((?:[ \t]*-[ \t]*.+\n)+)", text, re.M)
    declared = re.findall(r"-[ \t]*(\S+)", hooks_block.group(1)) if hooks_block else []
    if not name_m:
        errors.append("plugin.yaml missing name")
    if not (ROOT / "__init__.py").exists():
        errors.append("missing __init__.py")

    ban = re.compile(
        r"(AIAgent\.\w+\s*=|setattr\(\s*(?:sys\.modules|AIAgent)|sys\.modules\[)"
    )
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts or "scripts" in path.parts:
            continue
        if ban.search(path.read_text(encoding="utf-8")):
            errors.append(f"possible core override in {path.relative_to(ROOT)}")

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

    missing = sorted(set(declared) - set(registered_hooks))
    extra = sorted(set(registered_hooks) - set(declared))
    if missing:
        errors.append(f"declared but not registered hooks: {missing}")
    if extra:
        warnings.append(f"registered but undeclared hooks: {extra}")
    if "excubitor" not in registered_commands:
        errors.append("/excubitor command not registered")

    if not errors:
        print("OK: local validate passed (still run `hermes plugins validate` before catalog PR)")
        print("     hooks:", ", ".join(registered_hooks))
        print("     commands:", ", ".join(registered_commands))
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    # Keep registered list visible on success via a second pass message
    raise SystemExit(main())
