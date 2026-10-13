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
