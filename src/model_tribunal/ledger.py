"""Append-only record of every review the tribunal made, and the summary `tribunal stats` prints: reviews,
verdicts, what verification cost, and a scoreboard per judge (how often it agreed with the other judge, how
often the arbiter backed it on a split).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # Any: ledger records are JSON lines

from model_router.fmt import money, table
from model_router.paths import append_jsonl, read_jsonl

from .verify import ARBITER, SEATS


def append(path: Path, record: dict[str, Any]) -> None:
    """Append `record` as one JSON line to `path`, under a lock, creating the directory."""
    append_jsonl(path, record)


def load(path: Path) -> list[dict[str, Any]]:
    """Return every record in file order; a line that is not a JSON object (a write cut short) is skipped."""
    return read_jsonl(path)


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
            for label, row in sorted(self.by_judge.items(), key=lambda kv: -kv[1]["verdicts"]):
                paired = row["agreed"] + row["split"]
                ruled = row["backed"] + row["overruled"]
                rows.append([label, row["verdicts"], f"{row['passes'] / row['verdicts']:.0%}",
                             f"{row['agreed'] / paired:.0%} of {paired}" if paired else "-",
                             f"{row['backed'] / ruled:.0%} of {ruled}" if ruled else "-"])
            lines.append("")
            lines.append("judges' scoreboard (agreed = with the other judge; backed = by the arbiter on a split)")
            lines.append(table(["judge", "verdicts", "pass rate", "agreed", "backed"], rows))
        return "\n".join(lines)


def summarize(records: list[dict[str, Any]]) -> Summary:
    """Aggregate review records into a Summary."""
    summary = Summary()
    for record in records:
        summary.reviews += 1
        passed = record.get("passed")
        if record.get("source") == "judge-error" or passed is None:
            summary.errors += 1
        elif passed:
            summary.passed += 1
        else:
            summary.failed += 1
        summary.cost += float(record.get("cost_usd") or 0.0)
    summary.by_judge = scoreboard(records)
    return summary


def scoreboard(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Return, per judge cell, how many verdicts it gave and how many passed, how often it agreed with the other
    judge when both were asked (`agreed` / `split`) and how often the arbiter sided with it on a split (`backed`
    / `overruled`).
    """
    board: dict[str, dict[str, int]] = defaultdict(
        lambda: {"verdicts": 0, "passes": 0, "agreed": 0, "split": 0, "backed": 0, "overruled": 0})
    for record in records:
        verdicts = [v for v in record.get("verdicts") or [] if isinstance(v, dict) and v.get("cell")]
        judges = [v for v in verdicts if v.get("role") != ARBITER]
        arbiter = next((v for v in verdicts if v.get("role") == ARBITER), None)
        for v in judges:
            row = board[str(v["cell"])]
            row["verdicts"] += 1
            row["passes"] += bool(v.get("passed"))
        if len(judges) == len(SEATS):  # two judges, so agreeing or splitting means something
            same = bool(judges[0].get("passed")) == bool(judges[1].get("passed"))
            for v in judges:
                board[str(v["cell"])]["agreed" if same else "split"] += 1
                if not same and arbiter is not None:
                    backed = bool(v.get("passed")) == bool(arbiter.get("passed"))
                    board[str(v["cell"])]["backed" if backed else "overruled"] += 1
    return dict(board)
