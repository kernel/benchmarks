#!/usr/bin/env python3
"""Run ClawBench's browser lifecycle against Harbor's Kernel browser.

Harbor's Kernel environment runs each trial inside a Kernel browser VM and
gives the task that browser's session. ClawBench normally creates its own
browser and deletes it during verification. This wrapper keeps ClawBench's
replay, CDP bridge, metadata, and lifecycle files, but uses the trial's
browser and leaves deletion to Harbor, which owns the VM the verifier runs in.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

CONNECTION_FILE = Path("/harbor/kernel/connection.json")

spec = importlib.util.spec_from_file_location(
    "clawbench_kernel_browser", Path(__file__).with_name("kernel-browser.py")
)
assert spec is not None and spec.loader is not None
kernel_browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel_browser)

BrowserRuntimeError = kernel_browser.BrowserRuntimeError
BrowserSession = kernel_browser.BrowserSession


class HarborBrowserProvider(kernel_browser.KernelRuntimeProvider):
    def start(self, task: dict[str, Any], time_limit_s: int) -> BrowserSession:
        session_id = os.environ.get("KERNEL_SESSION_ID", "")
        connection = json.loads(CONNECTION_FILE.read_text())
        if not session_id or connection.get("session_id") != session_id:
            raise BrowserRuntimeError("Harbor did not provide a consistent Kernel browser session")
        browser = self._request_json("GET", f"/browsers/{session_id}")
        replay = self._request_json(
            "POST",
            f"/browsers/{session_id}/replays",
            {"framerate": 15, "max_duration_in_seconds": time_limit_s + 120},
        )
        replay_id = replay.get("replay_id") if isinstance(replay, dict) else None
        if not isinstance(replay_id, str) or not replay_id:
            raise BrowserRuntimeError("Kernel replay response did not include a valid replay_id")
        return BrowserSession(
            provider=self.name,
            mode="remote",
            session_id=session_id,
            cdp_url=connection["cdp_ws_url"],
            metadata={
                "region": browser.get("region"),
                "replay_id": replay_id,
                "stealth": browser.get("stealth"),
                "timeout_seconds": browser.get("timeout_seconds"),
            },
            recording_mode="provider-download",
        )


def load_provider() -> HarborBrowserProvider:
    return HarborBrowserProvider(
        api_key=os.environ["KERNEL_API_KEY"],
        options={},
        api_url=os.environ.get("KERNEL_BASE_URL") or "https://api.onkernel.com",
    )


def cmd_finalize() -> int:
    state = kernel_browser._load_state()
    if state is None:
        print("No Kernel browser was attached; nothing to finalize", flush=True)
        return 0
    try:
        kernel_browser._download_recording(load_provider(), state)
        kernel_browser._record(
            state, "replay_finalized", recording_bytes=state.get("recording_bytes")
        )
        state["status"] = "finalized"
    except BrowserRuntimeError as e:
        state["cleanup_error"] = f"replay finalization failed: {e}"
        kernel_browser._record(state, "replay_finalization_failed", error=str(e))
    state["cleanup_status"] = "harbor"
    kernel_browser._save_state(state)
    kernel_browser._write_metadata(state)
    kernel_browser._write_lifecycle(state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "finalize", "cleanup"])
    args = parser.parse_args()
    kernel_browser._load_provider = load_provider
    if args.command == "start":
        return kernel_browser.cmd_start()
    if args.command == "finalize":
        return cmd_finalize()
    print("Harbor deletes the environment browser when the trial stops", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
