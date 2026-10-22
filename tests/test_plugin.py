"""The Claude Code plugin around the package: the manifest in sync with pyproject, the skill reachable from the
manifest, and the hook bootstrap script run as Claude Code would run it."""

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hooks" / "tribunal-hook.sh"


def _version() -> str:
    match = re.search(r'^version = "(.*)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    assert match is not None
    return match.group(1)


def test_the_manifest_names_the_plugin_and_the_version_of_pyproject_and_every_hook_runs_the_script():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "tribunal" and plugin["version"] == _version()
    assert plugin["skills"].startswith("./") and (ROOT / plugin["skills"] / "tribunal" / "SKILL.md").is_file()
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    commands = {event: [h["command"] for e in entries for h in e["hooks"]] for event, entries in hooks.items()}
    assert all('"${CLAUDE_PLUGIN_ROOT}"/hooks/tribunal-hook.sh' in c for cs in commands.values() for c in cs)
    assert commands["SessionStart"][0].endswith("--bootstrap") and not commands["Stop"][0].endswith("--bootstrap")
    assert hooks["Stop"][0]["hooks"][0]["timeout"] >= 600
    assert os.access(SCRIPT, os.X_OK)


def _run(event: dict, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), *args], input=json.dumps(event), text=True, capture_output=True, env=env,
                          check=False, timeout=60)


def test_the_script_is_silent_outside_a_plugin_when_off_and_before_bootstrap(tmp_path):
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = _run({}, env=base)
    assert proc.returncode == 0 and proc.stdout == "" and "CLAUDE_PLUGIN_ROOT" in proc.stderr
    plugin = {**base, "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}
    proc = _run({"hook_event_name": "Stop"}, env=plugin)
    assert proc.returncode == 0 and proc.stdout == "" and "not ready yet" in proc.stderr
    assert not (tmp_path / "data").exists()
    for off in ({"MODEL_TRIBUNAL": "0"}, {"MODEL_ROUTER_CHILD": "1"}):
        proc = _run({"hook_event_name": "Stop"}, env={**plugin, **off})
        assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""
    empty = tmp_path / "nobin"
    empty.mkdir()
    proc = _run({"hook_event_name": "SessionStart"}, "--bootstrap",
                env={**plugin, "PATH": str(empty), "MODEL_TRIBUNAL_UV": str(tmp_path / "no-uv")})
    assert proc.returncode == 0 and proc.stdout == "" and "cannot find uv" in proc.stderr


def test_a_session_older_than_the_environment_runs_it_and_never_downgrades(tmp_path):
    data = tmp_path / "data"
    bindir = data / "venv" / "bin"
    bindir.mkdir(parents=True)
    hook = bindir / "tribunal-hook"
    hook.write_text("#!/bin/sh\ncat >/dev/null\necho ran\n")
    hook.chmod(0o755)
    (data / "installed-version").write_text("999.0.0")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PLUGIN_DATA": str(data)}
    proc = _run({"hook_event_name": "Stop"}, env=env)
    assert proc.returncode == 0 and proc.stdout == "ran\n" and "a new session uses it" in proc.stderr
    proc = _run({"hook_event_name": "SessionStart"}, "--bootstrap", env=env)
    assert proc.returncode == 0 and (data / "installed-version").read_text() == "999.0.0"  # nothing reinstalled

