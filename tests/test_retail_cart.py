import json
import os
import subprocess
import tomllib

import pytest

from harness.definitions import ROOT, load_suite

suite = load_suite("retail-cart")
BENCH = ROOT / "suites/retail-cart/image/bench"


def test_every_task_has_a_unique_id_and_an_https_start_url():
    tasks = suite.load_tasks()
    assert len({task.id for task in tasks}) == len(tasks)
    assert all(task.start_url.startswith("https://") for task in tasks)


def test_generate_writes_a_harbor_task(tmp_path):
    [task_dir] = suite.generate(["costco-microfiber-towels"], tmp_path)

    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["metadata"]["site"] == "costco.com"
    assert [server["name"] for server in config["environment"]["mcp_servers"]] == ["bench"]
    instruction = (task_dir / "steps/run/instruction.md").read_text()
    assert "Kirkland Signature" in instruction
    assert "Reach the item however works" in instruction
    assert "https://www.costco.com/" in instruction
    setup = (task_dir / "steps/run/workdir/setup.sh").read_text()
    assert 'open-start-url "https://www.costco.com/"' in setup
    assert (task_dir / "environment/bench/finalize-mcp").is_file()


def test_unknown_task_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="nope"):
        suite.generate(["nope"], tmp_path)


def _call(script, messages, tmp_path):
    source = (
        (BENCH / script)
        .read_text()
        .replace('Path("/logs/agent/finalize.json")', f"Path({str(tmp_path / 'finalize.json')!r})")
    )
    output = subprocess.run(
        ["python3", "-c", source],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [json.loads(line) for line in output.splitlines()]


def test_finalize_records_the_first_report_only(tmp_path):
    report = {"evidence": "Access Denied", "reasoning": "403 on home page", "outcome": "blocked"}
    call = {"jsonrpc": "2.0", "method": "tools/call"}
    responses = _call(
        "finalize-mcp",
        [
            {**call, "id": 1, "params": {"name": "finalize", "arguments": report}},
            {**call, "id": 2, "params": {"name": "finalize", "arguments": report}},
        ],
        tmp_path,
    )
    assert responses[0]["result"]["isError"] is False
    assert responses[1]["result"]["isError"] is True
    assert json.loads((tmp_path / "finalize.json").read_text())["outcome"] == "blocked"


def test_score_counts_a_block_apart_from_other_failures(tmp_path):
    verifier = tmp_path / "verifier"
    (tmp_path / "finalize.json").write_text(json.dumps({"outcome": "blocked"}))
    (tmp_path / "task.json").write_text(
        json.dumps({"id": "t", "cart_url": "https://example.com/cart", "cart_match": "x"})
    )
    source = (
        (BENCH / "score")
        .read_text()
        .replace('Path("/logs/agent/finalize.json")', f"Path({str(tmp_path / 'finalize.json')!r})")
        .replace('Path("/logs/verifier")', f"Path({str(verifier)!r})")
        .replace('Path("/opt/bench/task.json")', f"Path({str(tmp_path / 'task.json')!r})")
    )
    env = {
        **os.environ,
        "KERNEL_SESSION_ID": "s1",
        "KERNEL_API_KEY": "k",
        "KERNEL_BASE_URL": "http://127.0.0.1:9",
    }
    subprocess.run(["python3", "-c", source], check=True, env=env)

    rewards = json.loads((verifier / "reward.json").read_text())
    assert rewards == {
        "reward": 0.0,
        "finalized": 1.0,
        "cart_verified": 0.0,
        "cart_unverifiable": 1.0,
        "outcome_failed": 0.0,
        "outcome_blocked": 1.0,
        "outcome_website_error": 0.0,
        "outcome_kernel_error": 0.0,
    }
    assert "error" in json.loads((verifier / "final-page.json").read_text())
    check = json.loads((verifier / "cart-check.json").read_text())
    assert check["verified"] is False and check["unverifiable"] is True


def test_cart_check_tells_a_bot_check_from_an_empty_cart(monkeypatch):
    source = (BENCH / "score").read_text().replace('if __name__ == "__main__":', "if False:")
    score = {}
    exec(compile(source, "score", "exec"), score)
    task = {"cart_url": "https://shop.test/cart", "cart_match": "gloves"}

    def page(text, url="https://shop.test/cart"):
        score["execute"] = lambda code, timeout: {"url": url, "title": "", "text": text}
        return score["check_cart"](task, {"url": url})

    assert page("Nitrile gloves, qty 1")["verified"] is True
    assert page("Your cart is empty")["unverifiable"] is False
    blocked = page("Please verify yourself to continue", "https://shop.test/splashui/captcha")
    assert blocked["verified"] is False and blocked["unverifiable"] is True
