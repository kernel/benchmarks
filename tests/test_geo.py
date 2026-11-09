import json
import subprocess

from harness.definitions import ROOT

CONFIGURE = ROOT / "configurators/geo/configure"


def _run(config, args=None):
    payload = {"task": {"config": config, "instruction": ""}, "args": args or {}}
    return subprocess.run(
        [CONFIGURE], input=json.dumps(payload), capture_output=True, text=True, check=False
    )


def test_the_proxy_is_in_the_task_country():
    result = _run('[metadata]\nsite = "shop.example"\ncountry = "PL"\n', {"proxy": "isp"})
    output = json.loads(result.stdout)
    assert output["browser"] == {"proxy_create": {"type": "isp", "config": {"country": "PL"}}}


def test_a_task_without_a_country_is_refused():
    result = _run('[metadata]\nsite = "shop.example"\n')
    assert result.returncode != 0
    assert "country" in result.stderr


def test_the_default_proxy_is_residential():
    output = json.loads(_run('[metadata]\ncountry = "GB"\n').stdout)
    assert output["browser"]["proxy_create"]["type"] == "residential"
