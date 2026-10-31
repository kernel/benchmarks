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


def test_every_retail_cart_site_has_a_pinned_configuration():
    from harness.definitions import load_suite

    for task in load_suite("retail-cart").load_tasks():
        output = _configure(task.site)
        assert output["browser"]["proxy_create"]["config"] == {"country": "US"}


def test_personas_become_chromium_env():
    assert _configure("amazon.com")["browser"]["chromium_env"] == {"KERNEL_MAC_PERSONA": "1"}
    zoro = _configure("zoro.com")
    assert len(zoro["browser"]["chromium_env"]) == 2
    assert zoro["browser"]["proxy_create"]["type"] == "residential"
    assert "30 seconds" in zoro["steering"]
