"""Load Excubitor as a package (Hermes-style) and isolate each test's SQLite ledger."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = "excubitor_under_test"


def _load_package():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(
        PKG,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PKG
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules[PKG] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def excubitor():
    return _load_package()


@pytest.fixture
def ledger_mod(excubitor, tmp_path):
    led = importlib.import_module(f"{PKG}.ledger")
    db = tmp_path / "excubitor-test.db"
    led.use_test_db(db)
    yield led
    led.use_test_db(None)


@pytest.fixture
def cfg_mod(excubitor):
    return importlib.import_module(f"{PKG}.config")


@pytest.fixture
def modules(excubitor, ledger_mod):
    """Handy bundle of submodules with ledger pointed at the temp DB."""
    return {
        "pkg": excubitor,
        "ledger": ledger_mod,
        "config": importlib.import_module(f"{PKG}.config"),
        "intake": importlib.import_module(f"{PKG}.intake"),
        "work": importlib.import_module(f"{PKG}.work"),
        "spend": importlib.import_module(f"{PKG}.spend"),
        "watch": importlib.import_module(f"{PKG}.watch"),
        "hashing": importlib.import_module(f"{PKG}.hashing"),
        "notify": importlib.import_module(f"{PKG}.notify"),
        "command": importlib.import_module(f"{PKG}.command"),
    }
