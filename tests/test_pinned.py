import json
import subprocess

from harness.definitions import ROOT

CONFIGURE = ROOT / "configurators/pinned/configure"


def _configure(site, args=None):
    task = {
        "task": {"config": f'[metadata]\nsite = "{site}"\n', "instruction": ""},
        "args": args or {},
    }
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
    assert "chromium_env" not in zoro["browser"]
    assert zoro["browser"]["proxy_create"]["type"] == "residential"
    assert "load it again once" in zoro["steering"]
    assert _configure("wbmason.com")["browser"]["vm_setup"]


def test_extra_steering_is_appended_for_its_site_only():
    args = {"extra_steering": {"zoro.com": "Reload on errors.", "avis.com": "Reload once."}}
    zoro = _configure("zoro.com", args)["steering"]
    assert "load it again once" in zoro and zoro.endswith("Reload on errors.")
    assert _configure("avis.com", args)["steering"] == "Reload once."
    assert _configure("avis.com")["steering"] is None
    assert _configure("costco.com", args)["steering"] is None
