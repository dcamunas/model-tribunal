"""The `tribunal` command: `qa` reviews the code changed in a git repository, as the plugin's hook does at the end
of a turn; `judge` reviews a text written elsewhere; `config` sets who judges and the Codex home; `stats` shows
the reviews and the judges' scoreboard.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any  # Any: config.json, decoded

from model_router.catalog import ModelSpec, UnknownModelError, parse_cell
from model_router.classify import classify_rules
from model_router.cli_client import CliClient
from model_router.client import ClaudeClient, ClientUnavailableError, LiveClient
from model_router.fmt import money
from model_router.task import KINDS, RISKS, Task

from . import ledger, qa
from .catalog import MODELS_KEY, declared_models, load_declared, parse_declaration
from .codex import MultiClient, resolve_codex_home
from .codex_models import OPENAI_PRICES_DATE, Proposal, listed_models, price_note, propose
from .paths import LEDGER, config_path, home, read_config, write_config

CELL_SETTINGS = ("claude", "codex", "arbiter")
EXIT = {"pass": 0, "fail": 1, "error": 2}


def _cmd_qa(args: argparse.Namespace) -> int:
    repo = qa.repo_root((args.repo or Path.cwd()).expanduser())
    if repo is None:
        return _cannot(f"not a git repository: {args.repo or Path.cwd()}")
    brief = args.brief_file.read_text(encoding="utf-8") if args.brief_file else (args.brief or "")
    seats = qa.settings(repo, brief)
    try:
        base = qa.tree_of(repo, args.base)
    except qa.GitError as exc:
        return _cannot(f"cannot measure the change from {args.base!r}: {exc}")
    current = qa.snapshot(repo)
    files = qa.changed_files(repo, base=base, current=current)
    print(f"tribunal in {repo}: {seats.describe()}; {len(files)} files changed since {args.base}")
    for note in seats.notes:
        print(f"  note: {note}")
    if not files:
        print("nothing to judge")
        return 0
    if args.dry_run:
        print("\n".join(f"  {f}" for f in files) + "\ndry run: nothing was called")
        return 0
    review = qa.run_qa(repo, brief, diff=qa.diff_text(repo, base=base, current=current), seats=seats,
                       current_files=qa.files_now(repo, current, files=files), files=len(files))
    return _report(review)


def _prompt_from(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("no task given: pass it as an argument, with --file or on stdin")


def live_client(args: argparse.Namespace) -> ClaudeClient:
    """Return the Claude backend `--backend` asks for: the SDK with an API key, or `claude -p` with the account of
    the Claude profile in use.
    """
    backend = args.backend
    if backend == "auto":
        backend = "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"
    return LiveClient() if backend == "api" else CliClient()


def _cmd_judge(args: argparse.Namespace) -> int:
    prompt = _prompt_from(args)
    answer = sys.stdin.read() if args.answer == "-" else Path(args.answer).read_text(encoding="utf-8")
    if not answer.strip():
        return _cannot("the answer is empty")
    client = MultiClient(live_client(args))  # Claude cells to Claude, the Codex judge to Codex
    kind, risk = args.kind, args.risk
    if kind is None or risk is None:
        classification = classify_rules(prompt)
        kind, risk = kind or classification.kind, risk or classification.risk
        print(f"classification: {classification.kind} / {classification.risk}  ({classification.reason})")
    task = Task(prompt=prompt, kind=kind, risk=risk)
    author = parse_cell(args.cell) if args.cell else None
    seats = qa.resolve(qa.raw_settings(None, prompt), author.spec.short if author else None)  # never the author's model
    print(f"tribunal: {seats.describe()}")
    review = qa.judge_answer(task, answer, seats=seats, client=client, author=author)
    if args.json:
        print(json.dumps(review.to_dict(), ensure_ascii=False, indent=1))
        return EXIT[review.status]
    return _report(review)


def _report(review: qa.Review) -> int:
    for v in review.ruling.verdicts:
        print(f"  {v.role} {v.cell.label}: {'pass' if v.passed else 'fail'}: {v.reason}")
    if review.status == "error":
        print(f"  {review.ruling.reason}")
    print(f"verdict {review.status} · verification {money(review.ruling.cost_usd)} · id {review.id}")
    return EXIT[review.status]


def _cannot(message: str) -> int:
    """Say why the command could not run and return its exit code, 2."""
    print(f"tribunal: {message}", file=sys.stderr)
    return 2


def _cmd_stats(_args: argparse.Namespace) -> int:
    records = ledger.load(home() / LEDGER)
    if not records:
        print(f"no reviews yet ({home() / LEDGER})")
        return 0
    print(ledger.summarize(records).text())
    return 0


# ---- config: who judges, the Codex home, the declared models --------------------------------------------------


def _codex_setup(args: argparse.Namespace) -> None:
    """Apply `--declare`, `--codex-home` and `--codex-models`, in that order, before the seats are set."""
    for text in args.declare or []:
        _declare(text)
    if args.codex_home is not None:
        home_dir = args.codex_home.expanduser().resolve()
        write_config(codex_home=str(home_dir))
        _codex_models(args.codex_models or "", home_dir)
    elif args.codex_models is not None:
        _codex_models(args.codex_models)


def _apply_settings(args: argparse.Namespace) -> dict[str, Any]:
    """Apply the seat settings `args` carry to config.json (`default` removes one) and return what is stored.

    Raises:
        ValueError: the settings would seat two judges on one model, or name a cell the catalogue does not know.
    """
    current = qa.machine_settings()
    changes = {key: getattr(args, key) for key in ("mode", "rounds", *CELL_SETTINGS) if getattr(args, key) is not None}
    for key, value in changes.items():
        value = str(value).strip()
        if value.lower() == "default":
            current.pop(key, None)
        elif key in CELL_SETTINGS:
            current[key] = parse_cell(value).label  # an unknown model stops here, before anything is written
        elif key == "rounds":
            current[key] = int(value)
        else:
            current[key] = value
    if changes:
        qa.resolve({**qa.DEFAULTS, **current})  # judges on one model, an arbiter on a judge's: refused, nothing written
        write_config(**{key: current.get(key) for key in qa.SETTING_KEYS})
    return current


def _cmd_config(args: argparse.Namespace) -> int:
    _codex_setup(args)
    try:
        current = _apply_settings(args)
    except ValueError as exc:
        return _cannot(str(exc))
    shown = ", ".join(f"{k}={v}" for k, v in current.items()) or "defaults"
    try:
        seats = qa.resolve(qa.raw_settings(None))
    except (ValueError, UnknownModelError) as exc:
        print(f"tribunal: the stored settings do not resolve: {exc} ({shown}, in {config_path()}); "
              f"fix them with `tribunal config --KEY default`")
        return 2
    print(f"tribunal: mode {seats.mode}, judges {seats.describe()}, {seats.rounds} reviews per turn ({shown}, in "
          f"{config_path()})")
    for note in seats.notes:
        print(f"  note: {note}")
    for spec in declared_models():
        ready, line = _declared_check(spec)
        print(("  ok    " if ready else "  FAIL  ") + line)
    return 0


def _rounds_arg(text: str) -> str:
    if text.lower() != "default" and (not text.isdigit() or not 1 <= int(text) <= qa.MAX_ROUNDS):
        raise argparse.ArgumentTypeError(f"a whole number from 1 to {qa.MAX_ROUNDS}, or default")
    return text


def _declared_check(spec: ModelSpec) -> tuple[bool, str]:
    """Return one line about a declared Codex model: whether its door can open (the `codex` executable, the home
    configured and signed in, the model in the home's list) and whether its declared price is the list one.
    """
    head = f"{spec.short}: codex {spec.id}, "
    if shutil.which("codex") is None:
        return False, head + "the `codex` executable is not on the PATH"
    try:
        home_dir = resolve_codex_home()
    except ClientUnavailableError as exc:
        return False, head + str(exc)
    signed = (home_dir / "auth.json").exists()
    state = "signed in" if signed else f"not signed in: run `CODEX_HOME={home_dir} codex login`"
    known = listed_models(home_dir, include_hidden=True)
    if signed and known is not None and spec.id not in known:
        return False, head + f"home {home_dir}, signed in, but {spec.id} is not among the models it knows (Codex " \
                             f"refreshes that list when it runs; `tribunal config --codex-models` shows it)"
    note = price_note(spec.id, price_in=spec.input, price_out=spec.output)
    return signed, head + f"home {home_dir}, {state}" + (f"; {note}" if note else "")


def _forget(name: str) -> None:
    """Drop a removed declared model from the seats that named it, saying so: a reference left behind would stop
    every command with an unknown model.
    """
    config = read_config()
    dropped = [key for key in CELL_SETTINGS if str(config.get(key) or "").partition("/")[0] == name]
    if not dropped:
        return
    write_config(**dict.fromkeys(dropped))
    print(f"{name} was also removed from: {', '.join(dropped)}")


def _declare(text: str) -> None:
    """Apply one `--declare` value to config.json (declare, replace or, with `NAME=`, remove) and say so."""
    name, spec = parse_declaration(text)
    declared = dict(read_config().get(MODELS_KEY) or {})
    if spec is None:
        declared.pop(name, None)
        _forget(name)
    else:
        declared[name] = {"id": spec.id, "input": spec.input, "output": spec.output, "tier": spec.tier}
    path = write_config(**{MODELS_KEY: declared})
    print(f"Codex models declared: {', '.join(declared) or 'none'} (in {path})")
    load_declared(read_config())


def _declare_proposals(proposals: list[Proposal], names: list[str]) -> None:
    """Declare the proposals named in `names`, all or none: a name the home does not list, or a model with no
    known price, stops the command before anything is written.
    """
    by_name = {p.name: p for p in proposals}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"not in this home's list: {', '.join(unknown)}; it has {', '.join(by_name)}")
    unpriced = [n for n in names if by_name[n].declared_as is None and by_name[n].declaration is None]
    if unpriced:
        raise ValueError(f"no list price known here for {', '.join(unpriced)}; declare it by hand with --declare "
                         "(nothing was declared)")
    for n in dict.fromkeys(names):
        proposal = by_name[n]
        if proposal.declared_as is not None:
            print(f"{n}: already declared")
        elif proposal.declaration is not None:
            _declare(proposal.declaration)


def _codex_models(wanted: str, home_dir: Path | None = None) -> None:
    """Print what the Codex home can run, as ready declarations; with `wanted` (comma-separated names from that
    list), declare those. `home_dir` is the home just given; otherwise the configured one. Nothing is declared
    that was not named.
    """
    home_dir = home_dir or resolve_codex_home()
    ids = listed_models(home_dir)
    if ids is None:
        print(f"no model list in {home_dir} yet: run `CODEX_HOME={home_dir} codex` once and ask again "
              "(or declare by hand with --declare)")
        return
    proposals = propose(ids, {s.id: s.short for s in declared_models()})
    names = [n.strip() for n in wanted.split(",") if n.strip()]
    if names:
        _declare_proposals(proposals, names)
        return
    print(f"models this Codex home can run ({home_dir}):")
    for proposal in proposals:
        if proposal.declared_as is not None:
            what = f"declared as {proposal.declared_as}"
        elif proposal.declaration is not None:
            what = f"--declare {proposal.declaration}"
        else:
            what = "no list price known here: declare it by hand with --declare"
        print(f"  {proposal.name:<10} {proposal.model_id:<16} {what}")
    print(f"prices: OpenAI's list as of {OPENAI_PRICES_DATE}; tiers (after the last /): an assumption")
    if any(p.declared_as is None for p in proposals):
        print("the ones not declared stay out until you name them: `tribunal config --codex-models NAME,NAME`")


def build_parser() -> argparse.ArgumentParser:
    """Return the `tribunal` argument parser; each subcommand sets `func` to its handler."""
    p = argparse.ArgumentParser(prog="tribunal", description="Two judges blind to each other, a Claude model and a "
                                "Codex model, and an arbiter on a split: QA of code changes and of critical texts.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sq = sub.add_parser("qa", help="review the code changed in a git repository, read-only inside it")
    sq.add_argument("--repo", type=Path, help="the repository (default: the current directory)")
    sq.add_argument("--base", default="HEAD", help="what the change is measured from (default HEAD: uncommitted work)")
    sq.add_argument("--brief", help="what the change was meant to do")
    sq.add_argument("--brief-file", type=Path, help="the brief, from a file")
    sq.add_argument("--dry-run", action="store_true", help="show the judges and the changed files; call nothing")
    sq.set_defaults(func=_cmd_qa)

    sj = sub.add_parser("judge", help="review a text written elsewhere (critical prose, an analysis, a review)")
    sj.add_argument("prompt", nargs="?", help="the task the text answers; or use --file")
    sj.add_argument("--file", "-f", help="file with the task")
    sj.add_argument("--kind", "-k", choices=KINDS, help="task kind (inferred by rules when missing)")
    sj.add_argument("--risk", "-r", choices=RISKS, help="how bad a wrong answer is (inferred when missing)")
    sj.add_argument("--answer", required=True, metavar="FILE",
                    help="the answer to judge; `-` reads it from stdin (then give the task as an argument or with --file)")
    sj.add_argument("--cell", help="the cell that wrote the answer, for the record (default opus/medium)")
    sj.add_argument("--backend", choices=("auto", "api", "cli"), default="auto",
                    help="api = SDK with ANTHROPIC_API_KEY; cli = `claude -p` with your account; "
                         "auto = api when there is a key, else cli")
    sj.add_argument("--json", action="store_true", help="JSON output with every seat's verdict")
    sj.set_defaults(func=_cmd_judge)

    sc = sub.add_parser("config", help="show or set who judges; `default` goes back to the default")
    sc.add_argument("--mode", choices=(*qa.MODES, "default"), help="final (the default) or off")
    sc.add_argument("--claude", metavar="CELL", help="the Claude judge (default opus/high; never the chat's model)")
    sc.add_argument("--codex", metavar="CELL", help="the Codex judge (default: the declared codex model of the highest tier)")
    sc.add_argument("--arbiter", metavar="CELL", help="the arbiter of a split (default: a Claude model neither judge is on)")
    sc.add_argument("--codex-home", type=Path, help="the Codex home signed in with your ChatGPT account, e.g. "
                    "~/.codex; never the ambient one by accident. Lists what it can run")
    sc.add_argument("--codex-models", nargs="?", const="", metavar="NAME,NAME",
                    help="list the models the Codex home can run, with OpenAI's list price and a tier; with names, "
                         "declare those")
    sc.add_argument("--declare", action="append", metavar="NAME=codex/MODEL_ID@IN/OUT[/TIER]",
                    help="declare a Codex model by hand (prices in USD per million tokens); NAME= removes it")
    sc.add_argument("--rounds", type=_rounds_arg, help=f"reviews per turn, 1 to {qa.MAX_ROUNDS}: a fail goes back to "
                                                       "Claude until the last review, whose fail is reported to you (default 2)")
    sc.set_defaults(func=_cmd_config)

    ss = sub.add_parser("stats", help="the reviews so far and the judges' scoreboard")
    ss.set_defaults(func=_cmd_stats)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run `tribunal` with `argv` (the process arguments when None) and return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        load_declared(read_config())
        return int(args.func(args))
    except (ValueError, OSError, ClientUnavailableError, qa.GitError, subprocess.TimeoutExpired) as exc:
        print(f"tribunal: {exc}", file=sys.stderr)
        return 2


def entry() -> None:
    """Console script `tribunal`."""
    sys.exit(main())
