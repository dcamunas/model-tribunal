import pytest
from model_router.catalog import Usage, parse_cell
from model_router.client import ClientUnavailableError
from model_router.task import Task
from model_router.testing import FakeClient, Reply
from model_router.verify import Verdict

from model_tribunal import ledger
from model_tribunal.verify import Tribunal

TASK = Task(prompt="write the incident report", kind="write", risk="critical")


def ok(reason="fine"):
    return Reply(parsed=Verdict(passed=True, confidence=0.9, reason=reason), usage=Usage(2000, 100))


def ko(reason="wrong"):
    return Reply(parsed=Verdict(passed=False, confidence=0.9, reason=reason), usage=Usage(2000, 100))


def _tribunal(client, arbiter: str | None = "fable/medium"):
    return Tribunal(client, judges=[parse_cell("sonnet/low"), parse_cell("fable/low")],
                    arbiter=parse_cell(arbiter) if arbiter else None)


def test_both_judges_are_asked_blind_and_the_arbiter_settles_a_split():
    client = FakeClient(judge={"sonnet/low": [ok("fine")], "fable/low": [ko("thin")], "fable/medium": [ok("judge A was right")]})
    ruling = _tribunal(client).verify(TASK, "the notice")
    assert ruling.passed and ruling.source == "tribunal" and ruling.arbiter == parse_cell("fable/medium")
    assert [j.label for j in ruling.judges] == ["sonnet/low", "fable/low"]
    assert [(v.cell.label, v.passed, v.role) for v in ruling.verdicts] == [
        ("sonnet/low", True, "judge"), ("fable/low", False, "judge"), ("fable/medium", True, "arbiter"),
    ]
    assert "tribunal split: sonnet/low passed, fable/low failed; arbiter fable/medium sided with sonnet/low" in ruling.reason
    calls = client.calls
    assert sorted(c["cell"] for c in calls[:2]) == ["fable/low", "sonnet/low"] and calls[2]["cell"] == "fable/medium"
    assert all("<verdicts>" not in c["prompt"] for c in calls[:2])  # the judges never see each other
    arbiter_prompt = calls[2]["prompt"]
    assert "Judge A passed" in arbiter_prompt and "Judge B failed" in arbiter_prompt
    assert "fable" not in arbiter_prompt.split("<verdicts>")[1]  # nor does the arbiter see who they were
    assert ruling.cost_usd == pytest.approx(0.005 + 0.025 + 0.025)
    assert ruling.to_dict()["arbiter"] == "fable/medium" and len(ruling.to_dict()["verdicts"]) == 3


def test_agreement_needs_no_arbiter_and_a_split_without_one_fails():
    client = FakeClient(judge={"*": [ok()]})
    ruling = _tribunal(client).verify(TASK, "x")
    assert ruling.passed and ruling.arbiter is None and "tribunal agreed" in ruling.reason and len(client.calls) == 2
    ruling = _tribunal(FakeClient(judge={"*": [ko()]})).verify(TASK, "x")
    assert not ruling.passed and "tribunal agreed" in ruling.reason
    client = FakeClient(judge={"sonnet/low": [ok()], "fable/low": [ko("no")]})
    ruling = _tribunal(client, arbiter=None).verify(TASK, "x")
    assert not ruling.passed and ruling.source == "tribunal" and "no arbiter available" in ruling.reason
    assert ruling.arbiter is None and len(ruling.verdicts) == 2


def test_one_judge_decides_alone_and_says_so():
    client = FakeClient(judge={"*": [ok("solid")]})
    ruling = Tribunal(client, judges=[parse_cell("opus/high")], arbiter=None).verify(TASK, "x")
    assert ruling.passed and ruling.source == "tribunal" and ruling.reason.startswith("one judge (opus/high)")
    with pytest.raises(ValueError, match="at least one judge"):
        Tribunal(client, judges=[], arbiter=None)


def test_a_judge_that_cannot_be_reached_is_a_judge_error_that_keeps_the_other_verdict():
    class Down(FakeClient):
        def complete(self, cell, **kw):
            if cell.label == "fable/low":
                raise ClientUnavailableError("usage limit reached")
            return super().complete(cell, **kw)

    ruling = _tribunal(Down(judge={"*": [ko("divides by zero")]})).verify(TASK, "x")
    assert ruling.source == "judge-error" and "usage limit reached" in ruling.reason
    assert [v.cell.label for v in ruling.verdicts] == ["sonnet/low"] and ruling.cost_usd > 0
    low = FakeClient(judge={"sonnet/low": [ok()], "fable/low": [ko()],
                            "fable/medium": [Reply(text="no verdict", usage=Usage(1, 1))]})
    ruling = _tribunal(low).verify(TASK, "x")
    assert ruling.source == "judge-error" and "arbiter fable/medium" in ruling.reason and ruling.arbiter is not None


def test_the_scoreboard_counts_agreement_and_the_arbiter_s_backing(tmp_path):
    path = tmp_path / "ledger.jsonl"
    client = FakeClient(judge={"sonnet/low": [ok(), ok(), ok()], "fable/low": [ko("thin"), ok(), ok()],
                               "fable/medium": [ok()]})
    for _ in range(3):
        ledger.append(path, {"passed": True, "source": "tribunal", "cost_usd": 0.1,
                             **{k: v for k, v in _tribunal(client).verify(TASK, "x").to_dict().items() if k == "verdicts"}})
    ledger.append(path, {"passed": None, "source": "judge-error", "cost_usd": 0.0, "verdicts": []})
    path.with_suffix(".cut").write_text("")
    with path.open("a") as handle:
        handle.write("{not json\n")
    summary = ledger.summarize(ledger.load(path))
    assert (summary.reviews, summary.passed, summary.errors) == (4, 3, 1) and summary.cost == pytest.approx(0.3)
    board = summary.by_judge
    assert board["sonnet/low"] == {"verdicts": 3, "passes": 3, "agreed": 2, "split": 1, "backed": 1, "overruled": 0}
    assert board["fable/low"] == {"verdicts": 3, "passes": 2, "agreed": 2, "split": 1, "backed": 0, "overruled": 1}
    assert "fable/medium" not in board  # the arbiter is not a judge on the scoreboard
    text = summary.text()
    assert "reviews 4 · passed 3" in text and "judges' scoreboard" in text and "100% of 1" in text
    assert ledger.load(tmp_path / "missing.jsonl") == []
