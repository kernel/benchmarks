"""Load benchmark and arm definitions, and the suites and toolsets they name."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from dataclasses import asdict, dataclass, field
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
class Runner:
    agent: str
    model: str
    agent_version: str | None = None
    agent_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Arm:
    name: str
    agent: str
    model: str
    toolset: str
    agent_version: str | None = None
    agent_kwargs: dict[str, Any] = field(default_factory=dict)
    browser: Browser = field(default_factory=Browser)
    toolset_ref: str | None = None
    toolset_args: dict[str, Any] = field(default_factory=dict)
    runner: str | None = None


@dataclass(frozen=True)
class Benchmark:
    name: str
    suite: str
    arms: list[str]


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Not found: {path.relative_to(ROOT)}")
    return tomllib.loads(path.read_text())


def load_benchmark(name: str) -> Benchmark:
    data = _read(ROOT / "benchmarks" / f"{name}.toml")
    return Benchmark(name=name, suite=data["suite"], arms=list(data["arms"]))


def load_runner(name: str) -> Runner:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise SystemExit("Runner names must contain lowercase letters, digits and hyphens")
    return Runner(**_read(ROOT / "runners" / f"{name}.toml"))


def load_arm(name: str, runner: str | None = None, model: str | None = None) -> Arm:
    data = _read(ROOT / "arms" / f"{name}.toml")
    default_runner = data.pop("runner", None)
    if (
        default_runner is not None
        and {"agent", "model", "agent_version", "agent_kwargs"} & data.keys()
    ):
        raise SystemExit(f"{name}: use a runner or inline agent settings, not both")
    selected_runner = default_runner if runner is None else runner
    if selected_runner is not None:
        data.update(asdict(load_runner(selected_runner)))
    if model is not None:
        data["model"] = model
    # Harbor's installed agents may interpolate the model ID into a shell command.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]*", data["model"]):
        raise SystemExit("Model must be an ID, without whitespace or shell metacharacters")
    if data["agent"] == "pi" and not re.fullmatch(
        r"[A-Za-z0-9_-]+/[A-Za-z0-9_][A-Za-z0-9_./:@+-]*", data["model"]
    ):
        raise SystemExit(
            "The pi runner requires a provider/model ID, e.g. anthropic/claude-sonnet-5"
        )
    browser = data.pop("browser", {})
    return Arm(name=name, runner=selected_runner, browser=Browser(**browser), **data)


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
    spec.loader.exec_module(module)
    return module
