import json
import os
import subprocess

from harness.definitions import ROOT

CONFIGURE = ROOT / "configurators/registry/configure"

FAKE_KERNEL = """
import json, os


class _Lookup:
    def __init__(self, data):
        self.data = data

    def model_dump(self, **kwargs):
        return self.data


class _Summary:
    def __init__(self, data):
        self.__dict__.update(data)


class _Analyses:
    def retrieve(self, analysis_id):
        return _Lookup({"recommendation": json.loads(os.environ["FAKE_ANALYSIS"])})


class _Registry:
    analyses = _Analyses()

    def list(self, search):
        return [_Summary(item) for item in json.loads(os.environ["FAKE_PROJECT"])]

    def lookup(self, url, allowed_proxy_countries):
        with open(os.environ["FAKE_LOOKUP_LOG"], "w") as log:
            json.dump({"url": url, "countries": allowed_proxy_countries}, log)
        return _Lookup(json.loads(os.environ["FAKE_LOOKUP"]))


class Kernel:
    config_registry = _Registry()
"""

TASK = '[metadata]\nsite = "shop.example"\nstart_url = "https://shop.example/"\ncountry = "GB"\n'


TARGET = {"target": {"normalized": "https://shop.example/"}}


def _run(tmp_path, lookup, args=None, project=(), analysis=None):
    (tmp_path / "kernel.py").write_text(FAKE_KERNEL)
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "FAKE_LOOKUP": json.dumps({**TARGET, **lookup}),
        "FAKE_PROJECT": json.dumps(list(project)),
        "FAKE_ANALYSIS": json.dumps(analysis),
        "FAKE_LOOKUP_LOG": str(tmp_path / "lookup.json"),
    }
    payload = {"task": {"config": TASK, "instruction": ""}, "args": args or {}}
    result = subprocess.run(
        [CONFIGURE], input=json.dumps(payload), capture_output=True, text=True, env=env, check=True
    )
    return json.loads(result.stdout)


def test_a_recommendation_is_used_as_given(tmp_path):
    proxy = {"type": "isp", "config": {"country": "GB"}}
    lookup = {
        "recommendation": {
            "type": "recommendation",
            "browser": {"stealth": False, "gpu": False, "headless": False},
            "proxy": {"mode": "managed", "create": proxy},
        },
        "guidance": "Use the search box.",
    }
    output = _run(tmp_path, lookup, {"steering": True})

    assert output["browser"] == {
        "stealth": False,
        "gpu": False,
        "headless": False,
        "proxy_create": proxy,
    }
    assert output["steering"] == "Use the search box."
    assert output["provenance"]["source"] == "registry"
    log = json.loads((tmp_path / "lookup.json").read_text())
    assert log == {"url": "https://shop.example/", "countries": ["GB"]}


def test_a_site_without_a_recommendation_gets_the_geo_settings(tmp_path):
    output = _run(tmp_path, {"working_configurations": []})

    assert output["browser"] == {
        "stealth": True,
        "proxy_create": {"type": "residential", "config": {"country": "GB"}},
    }
    assert output["steering"] is None
    assert output["provenance"]["source"] == "fallback"


def test_the_project_analysis_is_preferred_over_global_knowledge(tmp_path):
    analysis = {
        "type": "recommendation",
        "browser": {"stealth": False},
        "proxy": {"create": {"type": "isp", "config": {"country": "GB"}}},
    }
    project = [
        {"target": "https://shop.example/", "analysis_status": "completed", "analysis_id": "a1"}
    ]
    output = _run(tmp_path, {}, project=project, analysis=analysis)

    assert output["browser"] == {
        "stealth": False,
        "proxy_create": {"type": "isp", "config": {"country": "GB"}},
    }
    assert output["provenance"]["source"] == "registry"
