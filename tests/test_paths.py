"""Each Claude profile keeps its own tribunal state, and the plugin's `tribunal` command finds the environment of
its own profile."""

import os
import shutil
import subprocess
from pathlib import Path

from model_tribunal.paths import home

ROOT = Path(__file__).resolve().parents[1]


def test_the_state_lives_in_the_claude_profile_in_use(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "personal"))
    assert home() == tmp_path / "personal" / "model-tribunal"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))
    assert home() == tmp_path / "work" / "model-tribunal"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert home() == tmp_path / ".claude" / "model-tribunal"


def _fake_cli(venv_bin: Path, says: str) -> None:
    venv_bin.mkdir(parents=True)
    cli = venv_bin / "tribunal"
    cli.write_text(f'#!/bin/sh\necho "{says} $*"\n')
    cli.chmod(0o755)


def test_the_plugin_command_runs_the_cli_of_its_own_profile_or_says_it_is_not_ready(tmp_path):
    bin_dir = tmp_path / "work" / "plugins" / "cache" / "a-market" / "tribunal" / "1.0.0" / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy(ROOT / "bin" / "tribunal", bin_dir / "tribunal")
    command = str(bin_dir / "tribunal")
    env = {"PATH": os.environ["PATH"]}
    missing = subprocess.run([command, "stats"], capture_output=True, text=True, env=env, check=False)
    assert missing.returncode == 1 and "not ready yet" in missing.stderr
    _fake_cli(tmp_path / "work" / "plugins" / "data" / "tribunal-a-market" / "venv" / "bin", "work")
    ran = subprocess.run([command, "stats"], capture_output=True, text=True, env=env, check=False)
    assert ran.stdout.strip() == "work stats"
