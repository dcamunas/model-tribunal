"""The tribunal on the router's verifier: every judge is asked at the same time, blind to the others; agreement
decides; a split goes to the arbiter, who reads the task, the answer and both reasoned verdicts without the
judges' names and decides for itself. One judge that cannot be reached is a judge error, not a verdict.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any  # Any: rulings are written out as JSON

from model_router.catalog import Cell, Usage
from model_router.client import ClaudeClient, ClientUnavailableError
from model_router.task import Task
from model_router.verify import JUDGE_SYSTEM, JudgeVerdict, Verifier, VerifyResult, judge_prompt

ARBITER_SYSTEM = (
    "You are the arbiter of a tribunal. Two independent judges disagree on whether an answer completes a task. "
    "You receive the task, the answer and both verdicts with their reasons. Decide for yourself from the task "
    "and the answer; treat the judges' reasons as leads to check, not as votes, and apply the same standard: "
    "correct, complete, every explicit constraint met, no invented facts. You cannot run code.\n\n"
    "Report your decision as the JSON object requested: `passed`, a `confidence` between 0 and 1, a one-sentence "
    "`reason` that says which judge was right and why, and `issues` listing each concrete problem (empty if none)."
)
ARBITER = "arbiter"  # the role of the arbiter's verdict in the record
SEATS = "AB"  # how the judges are named to the arbiter


def arbiter_prompt(task: Task, answer: str, verdicts: list[JudgeVerdict]) -> str:
    """Build the arbiter's prompt: the judge prompt plus both verdicts, anonymised as judge A and judge B."""
    lines = [f"Judge {letter} {'passed' if v.passed else 'failed'} the answer: {v.reason}"
             for letter, v in zip(SEATS, verdicts, strict=False)]
    return judge_prompt(task, answer) + "\n\n<verdicts>\n" + "\n".join(lines) + "\n</verdicts>"


@dataclass
class Ruling:
    """What the tribunal decided and what it cost; `source` is `tribunal` for a verdict and `judge-error` when a
    seat gave none. `judges` lists the judges asked, `verdicts` every seat's own verdict, the arbiter's last.
    """

    passed: bool
    source: str  # tribunal | judge-error
    reason: str
    cell: Cell | None = None  # the seat that decided
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    confidence: float | None = None
    judges: list[Cell] = field(default_factory=list)
    verdicts: list[JudgeVerdict] = field(default_factory=list)
    arbiter: Cell | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "source": self.source, "reason": self.reason,
                "cell": self.cell.label if self.cell else None, "usage": self.usage.to_dict(),
                "cost_usd": round(self.cost_usd, 6), "confidence": self.confidence,
                "judges": [j.label for j in self.judges], "verdicts": [v.to_dict() for v in self.verdicts],
                "arbiter": self.arbiter.label if self.arbiter else None}


class _NoPolicy:
    """The router's verifier wants a policy; the tribunal seats its judges itself, so this one is never asked."""

    def judges_for(self, worker: Cell, mode: str) -> list[Cell]:  # noqa: ARG002 - the protocol's shape
        return []


class Tribunal:
    """Two (or more) judges blind to each other and an arbiter on a split, asked through `client`."""

    def __init__(self, client: ClaudeClient, *, judges: list[Cell], arbiter: Cell | None,
                 judge_system: str = JUDGE_SYSTEM, arbiter_system: str = ARBITER_SYSTEM,
                 min_confidence: float = 0.0) -> None:
        if not judges:
            raise ValueError("a tribunal needs at least one judge")
        self.judges = judges
        self.arbiter = arbiter
        self.arbiter_system = arbiter_system
        self.verifier = Verifier(client, _NoPolicy(), min_confidence=min_confidence, judge_system=judge_system)

    def verify(self, task: Task, answer: str) -> Ruling:
        """Ask every judge at once; return their agreement, the arbiter's ruling on a split, or a judge error.

        Raises:
            ClientUnavailableError: the arbiter could not be reached (a judge that cannot is a judge error).
        """
        if len(self.judges) == 1:  # nothing to agree on: one judge decides and says so
            return _from(self.verifier.ask(task, answer, self.judges[0]))
        with ThreadPoolExecutor(max_workers=len(self.judges)) as pool:
            asked = list(pool.map(lambda cell: self._ask_or_error(task, answer, cell), self.judges))
        usage = Usage()
        for r in asked:
            usage = usage + r.usage
        cost = sum(r.cost_usd for r in asked)
        cells = [r.cell for r in asked if r.cell is not None]
        names = " + ".join(map(str, cells))
        verdicts = [v for r in asked for v in r.verdicts]
        broken = next((r for r in asked if r.source == "judge-error"), None)
        if broken is not None:
            return Ruling(False, "judge-error", f"tribunal ({names}): {broken.reason}", broken.cell, usage, cost, None,
                          cells, verdicts)
        confidences = [r.confidence for r in asked if r.confidence is not None]
        if len({r.passed for r in asked}) == 1:
            reason = f"tribunal agreed ({names}): " + " | ".join(r.reason for r in asked)
            return Ruling(asked[0].passed, "tribunal", reason, asked[-1].cell, usage, cost, min(confidences, default=None),
                          cells, verdicts)
        passed_by = ", ".join(str(r.cell) for r in asked if r.passed)
        failed_by = ", ".join(str(r.cell) for r in asked if not r.passed)
        split = f"tribunal split: {passed_by} passed, {failed_by} failed"
        if self.arbiter is None:
            reason = f"{split}; no arbiter available: " + " | ".join(r.reason for r in asked if not r.passed)
            return Ruling(False, "tribunal", reason, asked[-1].cell, usage, cost, min(confidences, default=None), cells,
                          verdicts)
        ruling = self.verifier.ask(task, answer, self.arbiter, prompt=arbiter_prompt(task, answer, verdicts),
                                   system=self.arbiter_system, role=ARBITER)
        usage, cost = usage + ruling.usage, cost + ruling.cost_usd
        verdicts += ruling.verdicts
        if ruling.source == "judge-error":
            return Ruling(False, "judge-error", f"{split}; arbiter {self.arbiter}: {ruling.reason}", self.arbiter, usage,
                          cost, None, cells, verdicts, self.arbiter)
        sided = ", ".join(str(r.cell) for r in asked if r.passed == ruling.passed)
        reason = f"{split}; arbiter {self.arbiter} sided with {sided}: {ruling.reason}"
        return Ruling(ruling.passed, "tribunal", reason, self.arbiter, usage, cost, ruling.confidence, cells, verdicts,
                      self.arbiter)

    def _ask_or_error(self, task: Task, answer: str, cell: Cell) -> VerifyResult:
        """Ask one judge; a judge that cannot be reached becomes a judge error, so the other judge's verdict and
        cost are kept instead of thrown away with the exception.
        """
        try:
            return self.verifier.ask(task, answer, cell)
        except ClientUnavailableError as exc:
            return VerifyResult(False, "judge-error", f"{cell} could not be reached: {exc}", cell, judges=[cell])


def _from(result: VerifyResult) -> Ruling:
    source = "judge-error" if result.source == "judge-error" else "tribunal"
    reason = result.reason if source == "judge-error" else f"one judge ({result.cell}): {result.reason}"
    return Ruling(result.passed, source, reason, result.cell, result.usage, result.cost_usd, result.confidence,
                  result.judges, result.verdicts)
