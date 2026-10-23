"""The door to Codex: ChatGPT through `codex exec`, by subscription, on a Codex home given explicitly.

Every call runs in an empty directory, read-only, with nothing persisted and Codex's file, image, web and
browser tools turned off (CODEX_DISABLED): with a shell the read-only sandbox still reads any file the user can
read, `view_image` reads any image, and what they read goes to OpenAI. The judge sees what the prompt carries.
A structured verdict is asked for with `--output-schema`, validated here, and tried once more when it does not
come back as JSON. `MultiClient` sends Claude cells to the router's backend and Codex cells to this door.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any  # Any: Codex's JSON events, and a test double for subprocess.run

from model_router.catalog import Cell, Usage, nearest_effort
from model_router.cli_client import DEFAULT_SYSTEM
from model_router.client import NON_STREAMING_MAX_TOKENS, ClaudeClient, ClientUnavailableError, Completion
from model_router.task import json_payload
from pydantic import BaseModel, ValidationError

from .catalog import EFFORTS, PROVIDER
from .paths import read_config

TIMEOUT_SECONDS = 900.0
ERROR_DETAIL_CHARS = 300
Run = Callable[..., Any]  # subprocess.run or a test double that returns a CompletedProcess
# Codex tools the judge must not have: a shell reads any file the user can read, `view_image` sends any image
# to the model, and the rest reach the web, a browser, the desktop or other code. The prompt is all it judges.
CODEX_DISABLED = (
    "shell_tool", "unified_exec", "view_image", "standalone_web_search", "browser_use", "browser_use_external",
    "in_app_browser", "computer_use", "apps", "image_generation", "js_repl", "code_mode_host", "goals", "plugins",
    "plugin_hooks", "tool_search", "search_tool",
)
# Kept from `codex exec`: the home decides the account, and an API key or base URL in the environment would move the
# call to API billing, maybe on another account.
CODEX_ENV_DROPPED = ("CODEX_HOME", "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return `schema` as OpenAI's strict structured output wants it: every object closed
    (`additionalProperties: false`) with every property required, `$defs` included. Pydantic leaves a field with a
    default out of `required`, and Codex rejects the schema for that.
    """
    out = dict(schema)
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        out["properties"] = {k: strict_schema(v) if isinstance(v, dict) else v for k, v in out["properties"].items()}
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    for key in ("items", "additionalProperties"):
        if isinstance(out.get(key), dict):
            out[key] = strict_schema(out[key])
    if isinstance(out.get("prefixItems"), list):
        out["prefixItems"] = [strict_schema(v) if isinstance(v, dict) else v for v in out["prefixItems"]]
    for key in ("$defs", "definitions"):
        if isinstance(out.get(key), dict):
            out[key] = {k: strict_schema(v) if isinstance(v, dict) else v for k, v in out[key].items()}
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [strict_schema(v) if isinstance(v, dict) else v for v in out[key]]
    return out


def _structured(text: str, output_format: type[BaseModel] | None) -> BaseModel | None:
    """Parse `text` into `output_format` (the first fenced block, else the whole text); None when it does not
    validate, which the tribunal reports as a judge error.
    """
    if output_format is None or not text:
        return None
    try:
        return output_format.model_validate(json_payload(text))
    except (ValueError, ValidationError):
        return None


def resolve_codex_home() -> Path:
    """Return the Codex home (the ChatGPT account) the judge runs on: `codex_home` in config.json (`tribunal config
    --codex-home`). Nothing else: the ambient ~/.codex may be another account, and the tribunal must never land on
    it by accident.

    Raises:
        ClientUnavailableError: none is set.
    """
    value = read_config().get("codex_home")
    if not value:
        raise ClientUnavailableError("Codex home not set: run `tribunal config --codex-home ~/.codex` (a home signed in "
                                     "with the ChatGPT account the tribunal may use)")
    return Path(str(value)).expanduser()


def _find(binary: str, hint: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise ClientUnavailableError(f"cannot find the executable {binary!r}; {hint}")
    return path


def _events(stdout: str) -> list[dict[str, Any]]:
    """Parse the JSONL events `codex exec --json` prints; lines that are not JSON objects are skipped."""
    out: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


class CodexClient:
    """Completions through `codex exec`: ChatGPT by subscription. Satisfies the router's `ClaudeClient` protocol. The
    prompt goes on stdin with the instructions in front (the CLI has no system prompt); a structured answer is
    asked for with `--output-schema` and validated here; `-o` gives the final message and the `turn.completed`
    event the usage. Each call runs in an empty directory, read-only sandbox, no shell, ephemeral session, user
    config ignored, on the Codex home the tribunal was given.
    """

    def __init__(self, home: Path, *, binary: str = "codex", timeout: float = TIMEOUT_SECONDS,
                 run: Run = subprocess.run) -> None:
        self.binary = _find(binary, "install the Codex CLI")
        home = home.expanduser().resolve()  # the child runs elsewhere: a relative home would point somewhere else there
        if not home.is_dir():  # a typo must not make codex start a fresh home there
            raise ClientUnavailableError(f"Codex home {home} does not exist: sign in there first "
                                         f"(`CODEX_HOME={home} codex login`)")
        self.home = home
        self.timeout = timeout
        self.run = run

    def complete(
        self, cell: Cell, *, prompt: str, system: str | None = None,
        max_tokens: int = NON_STREAMING_MAX_TOKENS,  # noqa: ARG002 - the CLI manages output length itself
        output_format: type[BaseModel] | None = None,
    ) -> Completion:
        text, usage, seconds = self._exec(cell, prompt=prompt, system=system, output_format=output_format)
        parsed = _structured(text, output_format)
        if output_format is not None and parsed is None:  # once more: the CLI does not always honour the schema
            text, more, extra = self._exec(cell, prompt=prompt, system=system, output_format=output_format)
            usage, seconds, parsed = usage + more, seconds + extra, _structured(text, output_format)
        return Completion(text=text, usage=usage, stop_reason="end_turn", model=cell.model, parsed=parsed, seconds=seconds)

    def _exec(self, cell: Cell, *, prompt: str, system: str | None, output_format: type[BaseModel] | None,
              ) -> tuple[str, Usage, float]:
        effort = nearest_effort(cell.effort, EFFORTS)
        with tempfile.TemporaryDirectory(prefix="tribunal-codex-") as scratch:
            last = Path(scratch) / "last-message.md"
            args = [self.binary, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                    "--color", "never", "-s", "read-only", *_disabled(), "-C", scratch, "-m", cell.spec.id,
                    "-c", f'model_reasoning_effort="{effort}"', "-o", str(last)]
            if output_format is not None:
                schema = Path(scratch) / "schema.json"
                schema.write_text(json.dumps(strict_schema(output_format.model_json_schema())), encoding="utf-8")
                args += ["--output-schema", str(schema)]
            args.append("-")
            env = {k: v for k, v in os.environ.items() if k not in CODEX_ENV_DROPPED}
            env["CODEX_HOME"] = str(self.home)
            started = time.monotonic()
            try:
                proc = self.run(args, input=f"{system or DEFAULT_SYSTEM}\n\n{prompt}", capture_output=True, text=True,
                                env=env, cwd=scratch, timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ClientUnavailableError(f"codex exec exceeded {self.timeout:.0f}s") from exc
            except OSError as exc:
                raise ClientUnavailableError(f"codex exec could not start: {exc}") from exc
            seconds = time.monotonic() - started
            events = _events(proc.stdout or "")
            errors = _codex_errors(events)
            if proc.returncode != 0:
                # never stdout: it carries the answer, and this text ends in the ledger
                detail = (errors[0] if errors else (proc.stderr or "").strip() or "no error message")[:ERROR_DETAIL_CHARS]
                raise ClientUnavailableError(f"codex exec exited with code {proc.returncode}: {detail}")
            text = (last.read_text(encoding="utf-8").strip() if last.exists() else "") or _last_message(events)
            if not text:  # an error event with no answer behind it (with an answer it was transient), or no answer at all
                detail = errors[0][:ERROR_DETAIL_CHARS] if errors else "no final message and no error event"
                raise ClientUnavailableError(f"codex exec gave no answer: {detail}")
        return text, _codex_usage(events), seconds


def _disabled() -> list[str]:
    """Return the arguments that turn the judge's tools off: every feature in CODEX_DISABLED, and web search,
    which is a setting of its own (its old feature flags are ignored).
    """
    return [arg for feature in CODEX_DISABLED for arg in ("--disable", feature)] + ["-c", 'web_search="disabled"']


def _codex_errors(events: list[dict[str, Any]]) -> list[str]:
    """Return the messages of the `error` and `turn.failed` events among `events`."""
    out = []
    for event in events:
        if event.get("type") in ("error", "turn.failed"):
            error = event.get("error")
            message = _dict(error).get("message") if isinstance(error, dict) else error
            out.append(str(message or event.get("message") or event))
    return out


def _last_message(events: list[dict[str, Any]]) -> str:
    """Return the text of the last `agent_message` item among `events`, or an empty string."""
    text = ""
    for event in events:
        item = _dict(event.get("item"))
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
    return text


def _codex_usage(events: list[dict[str, Any]]) -> Usage:
    """Sum the usage of every `turn.completed` event: input tokens net of the cached ones, which are kept apart."""
    usage = Usage()
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        counted = _dict(event.get("usage"))
        cached = int(counted.get("cached_input_tokens") or 0)
        usage = usage + Usage(input_tokens=max(0, int(counted.get("input_tokens") or 0) - cached),
                              output_tokens=int(counted.get("output_tokens") or 0), cache_read_tokens=cached)
    return usage


def codex_client() -> ClaudeClient:
    """Return the Codex door on the configured home.

    Raises:
        ClientUnavailableError: no home is set, it does not exist, or the `codex` executable is missing.
    """
    return CodexClient(resolve_codex_home())


class MultiClient:
    """Sends each cell to its door: Claude cells to the router's backend, Codex cells to the client `factory` makes
    on first use, so a missing home only fails when Codex is asked.
    """

    def __init__(self, claude: ClaudeClient, *, factory: Callable[[], ClaudeClient] = codex_client) -> None:
        self.claude = claude
        self.factory = factory
        self._codex: ClaudeClient | None = None

    def complete(
        self, cell: Cell, *, prompt: str, system: str | None = None, max_tokens: int = NON_STREAMING_MAX_TOKENS,
        output_format: type[BaseModel] | None = None,
    ) -> Completion:
        if cell.spec.provider != PROVIDER:
            client = self.claude
        else:
            if self._codex is None:
                self._codex = self.factory()
            client = self._codex
        return client.complete(cell, prompt=prompt, system=system, max_tokens=max_tokens, output_format=output_format)
