"""Harbor's Kernel environment with per-task browser settings.

`harness.configure` writes each task's browser create params to
`environment/kernel-config.json`. Harbor applies one set of environment
kwargs to a whole job, so this subclass reads the file per trial and passes
it to the provider as `browser_create_overrides`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harbor.environments.kernel import KernelEnvironment

CONFIG_FILE = "kernel-config.json"


class KernelTaskEnvironment(KernelEnvironment):
    def __init__(self, environment_dir: Path, *args: Any, **kwargs: Any) -> None:
        config_path = Path(environment_dir) / CONFIG_FILE
        if config_path.is_file():
            kwargs["browser_create_overrides"] = json.loads(config_path.read_text())
        super().__init__(environment_dir, *args, **kwargs)
