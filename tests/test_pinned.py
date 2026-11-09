import json
import subprocess

from harness.definitions import ROOT

CONFIGURE = ROOT / "configurators/pinned/configure"


def _configure(site):
    task = {"task": {"config": f'[metadata]\nsite = "{site}"\n', "instruction": ""}, "args": {}}
    result = subprocess.run(
        [CONFIGURE], input=json.dumps(task), capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def test_measured_sites_are_pinned_and_new_sites_fall_back():
    import tomllib

    from harness.definitions import load_suite

    measured = tomllib.loads((CONFIGURE.parent / "sites.toml").read_text())["sites"]
    for task in load_suite("retail-cart").load_tasks():
        output = _configure(task.site)
        if task.site in measured:
            assert output["browser"]["proxy_create"]["config"] == {"country": "US"}
        else:
            # Not yet measured by hand: the arm's static config, recorded as unpinned.
            assert output["browser"] == {} and output["provenance"]["pinned"] is False
    # The original 16 sites were all measured.
    assert len(measured) >= 16


def test_personas_become_chromium_env():
    assert _configure("amazon.com")["browser"]["chromium_env"] == {"KERNEL_MAC_PERSONA": "1"}
    zoro = _configure("zoro.com")
    assert "chromium_env" not in zoro["browser"]
    assert zoro["browser"]["proxy_create"]["type"] == "residential"
    assert "load it again once" in zoro["steering"]
    assert _configure("wbmason.com")["browser"]["vm_setup"]
