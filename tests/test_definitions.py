import pytest

from harness import definitions, providers
from harness.definitions import ROOT, load_arm, load_benchmark


def test_every_benchmark_and_arm_resolves():
    for path in (ROOT / "benchmarks").glob("*.toml"):
        benchmark = load_benchmark(path.stem)
        assert (ROOT / "suites" / benchmark.suite / "suite.py").is_file()
        for name in benchmark.arms:
            arm = load_arm(name)
            assert (ROOT / "toolsets" / arm.toolset / "toolset.py").is_file()
            if arm.browser.configurator:
                assert (ROOT / "configurators" / arm.browser.configurator / "configure").is_file()


def test_provider_arms_run_on_remote_browsers():
    benchmark = load_benchmark("retail-cart-providers")
    for name in benchmark.arms:
        arm = load_arm(name)
        assert arm.environment == "remote-browser"
        assert arm.toolset == "playwright-mcp"
        assert arm.browser.config["provider"] in providers.PROVIDERS


def test_unknown_environment_is_refused(tmp_path, monkeypatch):
    (tmp_path / "arms").mkdir()
    (tmp_path / "arms/bad.toml").write_text(
        'agent = "codex"\nmodel = "m"\ntoolset = "t"\nenvironment = "elsewhere"\n'
    )
    monkeypatch.setattr(definitions, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="elsewhere"):
        load_arm("bad")
