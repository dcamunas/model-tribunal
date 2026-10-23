"""Where the tribunal keeps its state and the environment variable that turns it off.

State lives in the Claude profile in use, `<profile>/model-tribunal`, where the profile is $CLAUDE_CONFIG_DIR or
~/.claude, so two profiles on one machine never share judges, Codex home or reviews. It holds `config.json` with
the judges, the Codex home and the declared Codex models (`tribunal config`), `ledger.jsonl` with every review,
and the hook's state. The router's own state (its models, its limits) stays in the router's folder and is read
through the router's API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any  # Any: config.json, decoded

from model_router.paths import claude_profile, merge_json, read_json

OFF_ENV = "MODEL_TRIBUNAL"  # "0" turns the hook off for a session
LEDGER = "ledger.jsonl"


def home() -> Path:
    """Return the state directory: `model-tribunal` inside the Claude profile in use."""
    return claude_profile() / "model-tribunal"


def config_path() -> Path:
    """Return the path of config.json inside the state directory."""
    return home() / "config.json"


def read_config() -> dict[str, Any]:
    """Return the contents of config.json, or an empty dict when it is missing or not a JSON object."""
    return read_json(config_path())


def write_config(**values: Any) -> Path:
    """Merge `values` into config.json (a None value removes its key) and return its path.

    Raises:
        ValueError: config.json holds something that is not a JSON object, which a merge would throw away.
    """
    path = config_path()
    merge_json(path, values)
    return path
