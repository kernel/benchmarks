"""Load benchmark and arm definitions, and the suites and toolsets they name."""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Browser:
    config: dict[str, Any] = field(default_factory=dict)
    configurator: str | None = None
    configurator_args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Arm:
    name: str
    agent: str
    model: str
    toolset: str
    agent_version: str | None = None
    agent_kwargs: dict[str, Any] = field(default_factory=dict)
    browser: Browser = field(default_factory=Browser)
    toolset_args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Benchmark:
    name: str
    suite: str
    arms: list[str]
    suite_args: dict[str, Any] = field(default_factory=dict)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Not found: {path.relative_to(ROOT)}")
    return tomllib.loads(path.read_text())


def load_benchmark(name: str) -> Benchmark:
    data = _read(ROOT / "benchmarks" / f"{name}.toml")
    return Benchmark(
        name=name,
        suite=data["suite"],
        arms=list(data["arms"]),
        suite_args=data.get("suite_args", {}),
    )


def load_arm(name: str) -> Arm:
    data = _read(ROOT / "arms" / f"{name}.toml")
    browser = data.pop("browser", {})
    return Arm(name=name, browser=Browser(**browser), **data)


def load_suite(name: str) -> ModuleType:
    return _load_module(ROOT / "suites" / name / "suite.py")


def load_toolset(name: str) -> ModuleType:
    return _load_module(ROOT / "toolsets" / name / "toolset.py")


def _load_module(path: Path) -> ModuleType:
    if not path.is_file():
        raise SystemExit(f"Not found: {path.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location(
        f"{path.parent.parent.name}.{path.parent.name}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs, as an import would be, so dataclasses work.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
