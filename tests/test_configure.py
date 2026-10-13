import json
import stat

from harness import configure as configure_module
from harness.configure import STEERING_HEADING, configure
from harness.definitions import Browser


def test_static_config_is_written_without_headless(task_dir):
    result = configure(task_dir, Browser(config={"stealth": True, "headless": True}))

    written = json.loads((task_dir / "environment" / "kernel-config.json").read_text())
    assert written == {"stealth": True}
    assert result["steering"] is None
    assert (task_dir / "steps/run/instruction.md").read_text() == "Book a table.\n"


def test_configurator_output_merges_over_config_and_adds_steering(task_dir, tmp_path, monkeypatch):
    script = tmp_path / "configurators" / "fake" / "configure"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "assert 'expected request' not in json.dumps(request)\n"
        "assert request['task']['instruction'].startswith('Book a table.')\n"
        "assert request['args'] == {'model': 'm'}\n"
        "json.dump({'browser': {'proxy': {'name': 'p'}}, 'steering': 'Go slowly.',"
        " 'provenance': {'url': 'https://example.com'}}, sys.stdout)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(configure_module, "ROOT", tmp_path)

    result = configure(
        task_dir,
        Browser(config={"stealth": True}, configurator="fake", configurator_args={"model": "m"}),
    )

    written = json.loads((task_dir / "environment" / "kernel-config.json").read_text())
    assert written == {"stealth": True, "proxy": {"name": "p"}}
    assert result["provenance"] == {"url": "https://example.com"}
    instruction = (task_dir / "steps/run/instruction.md").read_text()
    assert instruction.endswith(f"{STEERING_HEADING}\n\nGo slowly.\n")
