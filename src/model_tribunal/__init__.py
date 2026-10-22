"""model-tribunal: a Claude judge and a Codex judge review a change blind to each other; an arbiter decides a
split. It runs on model-router's engine (cells, clients, one-judge verification) and adds the second company,
the third seat and the Claude Code hook that reviews each turn.
"""

from .qa import Review, Settings, judge_answer, resolve, run_qa, settings
from .verify import Ruling, Tribunal

__all__ = ["Review", "Ruling", "Settings", "Tribunal", "judge_answer", "resolve", "run_qa", "settings"]
