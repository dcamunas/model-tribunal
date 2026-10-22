"""Append-only record of every review the tribunal made, and the summary `tribunal stats` prints: reviews,
verdicts, what verification cost, and a scoreboard per judge (how often it agreed with the other judge, how
often the arbiter backed it on a split).
"""

from __future__ import annotations

import fcntl
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # Any: ledger records are JSON lines

from .verify import ARBITER


def append(path: Path, record: dict[str, Any]) -> None:
    """Append `record` as one JSON line to `path`, under a lock, creating the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load(path: Path) -> list[dict[str, Any]]:
    """Return every record in file order; a line that is not a JSON object (a write cut short) is skipped."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for raw in handle:
            try:
                record = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(record, dict):
                out.append(record)
    return out


def money(value: float | None) -> str:
    """Format a USD amount for a table, with the precision small verification costs need."""
    if value is None:
        return "-"
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Render `rows` under `headers` as aligned plain text; numeric columns are right-aligned."""
    cells = [[str(h) for h in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    numeric = [all(isinstance(r[i], int | float) for r in rows) for i in range(len(headers))] if rows else []
    lines = []
    for n, row in enumerate(cells):
        parts = []
        for i, text in enumerate(row):
            right = n > 0 and numeric and numeric[i]
            parts.append(text.rjust(widths[i]) if right else text.ljust(widths[i]))
        lines.append("  ".join(parts).rstrip())
    return "\n".join(lines)


@dataclass
class Summary:
    """Totals of the reviews: how many, how they went, what verification cost, and the judges' scoreboard."""

    reviews: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    cost: float = 0.0
    by_judge: dict[str, dict[str, int]] = field(default_factory=dict)

    def text(self) -> str:
        """Render the summary as the text `tribunal stats` prints."""
        lines = [f"reviews {self.reviews} · passed {self.passed} · failed {self.failed} · could not judge {self.errors}",
                 f"verification {money(self.cost)}"]
        if self.by_judge:
            rows = []
            for label, s in sorted(self.by_judge.items(), key=lambda kv: -kv[1]["verdicts"]):
                paired = s["agreed"] + s["split"]
                ruled = s["backed"] + s["overruled"]
                rows.append([label, s["verdicts"], f"{s['passes'] / s['verdicts']:.0%}",
                             f"{s['agreed'] / paired:.0%} of {paired}" if paired else "-",
                             f"{s['backed'] / ruled:.0%} of {ruled}" if ruled else "-"])
            lines.append("")
            lines.append("judges' scoreboard (agreed = with the other judge; backed = by the arbiter on a split)")
            lines.append(table(["judge", "verdicts", "pass rate", "agreed", "backed"], rows))
        return "\n".join(lines)


def summarize(records: list[dict[str, Any]]) -> Summary:
    """Aggregate review records into a Summary."""
    s = Summary()
    for r in records:
        s.reviews += 1
        passed = r.get("passed")
        if r.get("source") == "judge-error" or passed is None:
            s.errors += 1
        elif passed:
            s.passed += 1
        else:
            s.failed += 1
        s.cost += float(r.get("cost_usd") or 0.0)
    s.by_judge = scoreboard(records)
    return s


def scoreboard(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Return, per judge cell, how many verdicts it gave and how many passed, how often it agreed with the other
    judge when both were asked (`agreed` / `split`) and how often the arbiter sided with it on a split (`backed`
    / `overruled`).
    """
    board: dict[str, dict[str, int]] = defaultdict(
        lambda: {"verdicts": 0, "passes": 0, "agreed": 0, "split": 0, "backed": 0, "overruled": 0})
    for r in records:
        verdicts = [v for v in r.get("verdicts") or [] if isinstance(v, dict) and v.get("cell")]
        judges = [v for v in verdicts if v.get("role") != ARBITER]
        arbiter = next((v for v in verdicts if v.get("role") == ARBITER), None)
        for v in judges:
            b = board[str(v["cell"])]
            b["verdicts"] += 1
            b["passes"] += bool(v.get("passed"))
        if len(judges) == 2:
            same = bool(judges[0].get("passed")) == bool(judges[1].get("passed"))
            for v in judges:
                board[str(v["cell"])]["agreed" if same else "split"] += 1
                if not same and arbiter is not None:
                    backed = bool(v.get("passed")) == bool(arbiter.get("passed"))
                    board[str(v["cell"])]["backed" if backed else "overruled"] += 1
    return dict(board)
