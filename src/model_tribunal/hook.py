"""Claude Code hook of the `tribunal` plugin: final QA of the code a turn changed, by a Claude judge and a Codex
judge blind to each other, and an arbiter on a split (see `qa.py`).

Events, one entry point, dispatch on `hook_event_name`:

* UserPromptSubmit: forget the session's last turn; in a git repository, snapshot the working tree and keep the
  prompt as the brief and the settings as they stand.
* Stop: when the tree changed since that snapshot (and not only in documentation), judge the change against
  the brief. A pass is reported in one line. A fail goes back to Claude as the reason to keep working, with the
  judges' issues, until the turn has had `rounds` reviews (2 by default); the last fail is reported to the user
  and Claude stops. A judge that cannot be reached (a quota, a network) never blocks.
* SessionEnd: forget the session's turn.

Another session can edit the same repository during a turn; its edits are then in this turn's diff. When the
transcript of a session whose turn overlapped this one shows an editing tool (Bash included, since a command can
write) used since this turn began, a fail is reported and not sent back.

Settings that do not resolve, or a repository git cannot snapshot, are said in one line: the turn is not judged,
and the user knows why.

Sessions the router starts itself (its workers and judges, MODEL_ROUTER_CHILD=1) are never judged, and
MODEL_TRIBUNAL=0 in the environment turns the hook off. The hook never fails a session: on any error it exits 0
with a note on stderr.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any  # Any: hook events and the hook's state are decoded JSON

from model_router import clock
from model_router.hook import main_model
from model_router.paths import is_child, locked, read_json, read_jsonl, replace_file

from .catalog import load_declared
from .paths import OFF_ENV, home, read_config
from .qa import (
    MAX_BRIEF_CHARS,
    MAX_ERROR_CHARS,
    GitError,
    Review,
    Settings,
    changed_files,
    chat_cell,
    diff_text,
    files_now,
    only_docs,
    raw_settings,
    repo_root,
    resolve,
    run_qa,
    snapshot,
)

STATE_FILE = "hook-state.json"
MAX_SESSIONS = 50  # sessions kept in the state file, the most recent ones
MAX_REASON_CHARS = 4000
MAX_NOTICE_CHARS = 600  # how much of a verdict's reason a one-line notice carries
STALE_TURN = timedelta(hours=6)  # an open turn older than this is taken for one that never reached its Stop
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit", "Bash"})  # Bash may edit: counted as editing


@contextlib.contextmanager
def sessions() -> Generator[dict[str, dict[str, Any]]]:
    """Yield the per-session state (one entry per session, with its open turn), locked against other sessions,
    and save it on exit. An entry that is not an object (a hand edit, a cut write) is dropped.
    """
    path = home() / STATE_FILE
    with locked(path):
        data = {k: v for k, v in read_json(path).items() if isinstance(v, dict)}
        yield data
        if len(data) > MAX_SESSIONS:
            for key in sorted(data, key=lambda k: data[k].get("at", ""))[: len(data) - MAX_SESSIONS]:
                data.pop(key)
        replace_file(path, json.dumps(data))


def on_prompt(event: dict[str, Any]) -> dict[str, Any] | None:
    """Start a turn: forget the last one, then, in a git repository, snapshot the working tree and keep the prompt
    as the brief and the settings as they stand now, before the work can change them. Return a one-line notice
    when the repository cannot be snapshotted, else None.
    """
    session = str(event.get("session_id") or "")
    if not session:
        return None
    with sessions() as state:
        state.pop(session, None)  # a turn that cannot be set up must not be judged against the last one
    repo = repo_root(Path(str(event.get("cwd") or ".")))
    if repo is None:
        return None
    load_declared(read_config())  # a declared Codex model in the settings must resolve
    brief = str(event.get("prompt") or "")[:MAX_BRIEF_CHARS]
    raw = raw_settings(repo, brief)
    if str(raw.get("mode") or "").lower() == "off":
        return None  # the settings say so now, and they are taken now: nothing to snapshot
    try:
        tree = snapshot(repo)
    except (GitError, subprocess.TimeoutExpired) as exc:
        return {"systemMessage": f"tribunal: this turn will not be judged, git could not snapshot {repo.name}: "
                                 f"{str(exc)[:MAX_ERROR_CHARS]}"}
    now = _now()
    with sessions() as state:
        overlaps = [k for k, t in state.items()
                    if t.get("repo") == str(repo) and t.get("open") and not _stale(t.get("at", ""))]
        for k in overlaps:
            state[k].setdefault("overlaps", []).append(session)
        state[session] = {"repo": str(repo), "base": tree, "brief": brief, "settings": raw, "rounds": 0,
                          "judged": None, "at": now, "open": True, "overlaps": overlaps,
                          "transcript": str(event.get("transcript_path") or "")}
    return None


def on_stop(event: dict[str, Any]) -> dict[str, Any] | None:
    """Judge what the turn changed; return the hook's answer (a block with the issues, a one-line note) or None.
    The turn is closed whatever happens, unless it is sent back to Claude.
    """
    session = str(event.get("session_id") or "")
    with sessions() as state:
        turn = dict(state.get(session) or {})
        others = {k: dict(state[k]) for k in turn.get("overlaps", []) if k in state}
    if not turn:
        return None
    blocked = False
    try:
        out = _judge_turn(event, turn=turn, others=others)
        blocked = bool(out and out.get("decision") == "block")
        return out
    finally:
        if not blocked:
            _close(session, turn["base"])


def _judge_turn(event: dict[str, Any], *, turn: dict[str, Any], others: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Judge what the turn changed with the settings it started with; return the hook's answer, or None when there
    is nothing new to judge. A judge error leaves the turn's rounds as they were, so the next review still counts.
    """
    session = str(event.get("session_id") or "")
    repo = Path(turn["repo"])
    load_declared(read_config())
    chat = main_model(event.get("transcript_path"))
    try:
        seats = resolve(turn.get("settings") or raw_settings(repo, turn.get("brief", "")), chat)
        if seats.mode == "off":
            return None
        current = snapshot(repo)
        files = changed_files(repo, base=turn["base"], current=current)
        if not files or only_docs(files) or current == turn.get("judged") or turn["rounds"] >= seats.rounds:
            return None  # nothing new to judge, or the last review's fail was already reported
        diff, shown = diff_text(repo, base=turn["base"], current=current), files_now(repo, current, files=files)
    except ValueError as exc:  # UnknownModelError too: settings no judge can sit by
        return {"systemMessage": f"tribunal: not judged, the settings do not resolve: {str(exc)[:MAX_ERROR_CHARS]}; "
                                 "`tribunal config` shows them"}
    except (GitError, subprocess.TimeoutExpired) as exc:
        return {"systemMessage": f"tribunal: not judged, git could not read the change in {repo.name}: "
                                 f"{str(exc)[:MAX_ERROR_CHARS]}"}
    review = run_qa(repo, turn.get("brief", ""), diff=diff, seats=seats, author=chat_cell(chat), current_files=shown,
                    files=len(files))
    rounds = turn["rounds"] + (review.status != "error")
    with sessions() as state:
        if state.get(session, {}).get("base") == turn["base"] and review.status != "error":  # not a newer turn
            state[session].update(rounds=rounds, judged=current)
    edited = review.status == "fail" and any(_edited_since(t.get("transcript", ""), turn["at"]) for t in others.values())
    return _answer(review, seats=seats, repo=repo, files=len(files), rounds=rounds, edited_elsewhere=edited)


def _answer(review: Review, *, seats: Settings, repo: Path, files: int, rounds: int,
            edited_elsewhere: bool) -> dict[str, Any]:
    """Return the hook's answer to a review: one line for a pass, an error or a final fail; a fail before the last
    round goes back to Claude with the findings, unless another session edited the repository meanwhile.
    """
    ruling, who = review.ruling, seats.describe()
    if review.status == "pass":
        return {"systemMessage": f"tribunal: pass ({who}), {files} files"}
    if review.status == "error":
        heard = " | ".join(f"{v.cell}: {'pass' if v.passed else 'fail'}: {v.reason}" for v in ruling.verdicts)
        return {"systemMessage": f"tribunal: could not judge ({who}): {ruling.reason[:MAX_ERROR_CHARS]}"
                                 + (f". Heard from {heard[:MAX_NOTICE_CHARS]}" if heard else "")}
    if edited_elsewhere:  # another session edited the repository during this turn: its edits are in this diff too
        return {"systemMessage": f"tribunal: fail ({who}), not sent back because another session edited "
                                 f"{repo.name} during this turn and its edits are part of this change: "
                                 f"{ruling.reason[:MAX_NOTICE_CHARS]}"}
    if rounds < seats.rounds:
        text = (f"The tribunal ({who}) reviewed the change you made in this turn and failed it "
                f"(review {rounds} of {seats.rounds}). Fix what is right in these findings, say briefly what you changed "
                f"and what you disagree with and why, then finish:\n\n{ruling.reason}")
        return {"decision": "block", "reason": text[:MAX_REASON_CHARS]}
    return {"systemMessage": f"tribunal: fail after {rounds} reviews ({who}): {ruling.reason[:MAX_NOTICE_CHARS]}"}


def on_session_end(event: dict[str, Any]) -> None:
    """Forget a session that ended: its turn is no longer open."""
    session = str(event.get("session_id") or "")
    with sessions() as state:
        state.pop(session, None)


def _close(session: str, base: str) -> None:
    """Mark the session's turn as finished (it no longer edits the repository), unless a newer turn replaced it."""
    with sessions() as state:
        if state.get(session, {}).get("base") == base:
            state[session]["open"] = False


def _edited_since(transcript: str, since: str) -> bool:
    """Say whether the session transcript at `transcript` used an editing tool (EDIT_TOOLS) at or after `since`. An
    unreadable transcript counts as edited: when in doubt, a fail is reported rather than sent back.
    """
    path = Path(transcript)
    if not path.is_file() or not os.access(path, os.R_OK):
        return True
    start = clock.parse_moment(since)
    for entry in read_jsonl(path):
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        when = clock.parse_moment(str(entry.get("timestamp", "")))
        if when is None or start is None or when < start or not isinstance(content, list):
            continue
        if any(isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in EDIT_TOOLS for c in content):
            return True
    return False


def _stale(at: str) -> bool:
    moment = clock.parse_moment(at)
    return moment is None or clock.now() - moment > STALE_TURN


def skipped() -> bool:
    """Say whether this process must not judge: the tribunal is off, or the session is one the router started."""
    return os.environ.get(OFF_ENV) == "0" or is_child()


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one hook event; return the JSON to print, or None for nothing."""
    if skipped():
        return None
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        return on_prompt(event)
    if name == "Stop":
        return on_stop(event)
    if name == "SessionEnd":
        on_session_end(event)
    return None


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def main() -> None:
    """Entry point `tribunal-hook`: read the event on stdin, answer on stdout, never fail the session."""
    try:
        event = json.load(sys.stdin)
        out = handle(event) if isinstance(event, dict) else None
    except Exception as exc:  # noqa: BLE001 - a hook must never break the session
        print(f"tribunal-hook: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(0)
    if out is not None:
        print(json.dumps(out))
    sys.exit(0)
