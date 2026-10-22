"""Where the tribunal keeps its state and the environment variables that configure it.

State lives in ~/.model-tribunal (MODEL_TRIBUNAL_HOME moves it): `config.json` with the judges, the Codex home
and the declared Codex models (`tribunal config`), `ledger.jsonl` with every review, and the hook's state. The
router's own state (its profile, its models) stays in the router's home and is read through the router's API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any  # Any: config.json, decoded

HOME_ENV = "MODEL_TRIBUNAL_HOME"
OFF_ENV = "MODEL_TRIBUNAL"  # "0" turns the hook off for a session; the judges' own sessions carry it too
CODEX_HOME_ENV = "MODEL_TRIBUNAL_CODEX_HOME"  # the Codex home (ChatGPT account) the judge runs on; never the ambient ~/.codex
LEDGER = "ledger.jsonl"


def home() -> Path:
    """Return the state directory: $MODEL_TRIBUNAL_HOME or ~/.model-tribunal."""
    return Path(os.environ.get(HOME_ENV, "~/.model-tribunal")).expanduser()


def config_path() -> Path:
    """Return the path of config.json inside the state directory."""
    return home() / "config.json"


def read_config() -> dict[str, Any]:
    """Return the contents of config.json, or an empty dict when it is missing or not a JSON object."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> Path:
    """Write `data` as the whole config.json, creating the state directory, and return its path."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_config(**values: Any) -> Path:
    """Merge `values` into config.json and return its path."""
    return save_config({**read_config(), **values})
