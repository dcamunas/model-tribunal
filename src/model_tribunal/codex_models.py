"""What a Codex home can run, and the declarations the tribunal proposes for it.

Codex keeps the list of models the signed-in account may use in `models_cache.json` inside its home: one
entry per model with its id (`slug`) and whether the picker shows it (`visibility`). That file is Codex's
own cache, not a documented interface, so it is read on a best-effort basis: when it is missing or its
shape changes, nothing is proposed and declaring by hand (`tribunal config --declare`) still works.

What the account does not say is the price, the tier, or the quota. The price comes from OPENAI_PRICES,
OpenAI's list prices on the date in OPENAI_PRICES_DATE, which the ledger needs so reviews stay comparable
when you pay by subscription. The tier is an opinion by family (FAMILY_TIERS). The quota is not read at all:
no stable interface carries it, and a model you can reach on a tiny shared quota is the reason a proposal is
only printed, never declared without being named.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # Any: Codex's models cache, decoded JSON

from model_router.catalog import MODELS

from .catalog import DEFAULT_TIER

MODELS_CACHE = "models_cache.json"

OPENAI_PRICES_DATE = "2026-10-22"
# USD per million tokens, input and output, standard tier, context up to 272K tokens, from
# https://developers.openai.com/api/docs/pricing on OPENAI_PRICES_DATE.
OPENAI_PRICES: dict[str, tuple[float, float]] = {
    "gpt-6-astra": (10.0, 50.0),
    "gpt-6.1-sol": (2.0, 10.0),
    "gpt-6-sol": (2.0, 10.0),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-5.6-sol": (4.0, 20.0),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-luna": (0.20, 1.20),
}
# The tier a family is taken to be, 0 Haiku to 3 Fable: an assumption, as the Claude capabilities are.
FAMILY_TIERS: dict[str, int] = {"luna": 0, "terra": 1, "sol": 1, "astra": 3}
PRICE_TOLERANCE = 1e-9

_SLUG = re.compile(r"^gpt-(\d+(?:\.\d+)?)-([a-z]+)$")


@dataclass(frozen=True)
class Proposal:
    """One model a Codex home lists, as the tribunal would declare it: a short name, the `--declare` value when
    the price is known (None otherwise), and the name it is already declared under, if it is.
    """

    model_id: str
    name: str
    declaration: str | None
    declared_as: str | None = None


def listed_models(home: Path, *, include_hidden: bool = False) -> list[str] | None:
    """Return the ids of the models the Codex home at `home` shows in its picker (every model it knows with
    `include_hidden`), in its order, or None when it has no usable model list: no file yet (Codex writes one
    the first time it runs with that home), a shape this code does not know, or an empty list.
    """
    try:
        data: Any = json.loads((home / MODELS_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    ids = [m["slug"] for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str) and m["slug"]
           and (include_hidden or m.get("visibility", "list") == "list")]
    return ids or None


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def propose(model_ids: list[str], declared: dict[str, str]) -> list[Proposal]:
    """Return a proposal per id in `model_ids`; `declared` maps a declared model id to its short name. The newest
    version of a family takes the family's name (`luna`), older ones add their version (`luna-56`). No two
    proposals share a name, and none takes a declared name or a Claude model's.
    """
    parsed = {m: _SLUG.match(m) for m in dict.fromkeys(model_ids)}
    newest: dict[str, tuple[int, ...]] = {}
    for m in parsed.values():
        if m is not None:
            newest[m.group(2)] = max(newest.get(m.group(2), ()), _version(m.group(1)))
    taken = set(declared.values()) | {s.short for s in MODELS.values() if s.claude}
    out = []
    for model_id, m in parsed.items():
        if model_id in declared:
            out.append(Proposal(model_id, declared[model_id], None, declared[model_id]))
            continue
        if m is None:
            name = re.sub(r"[^a-z0-9-]", "", model_id.lower()).lstrip("0123456789-") or "model"
            tier = DEFAULT_TIER
        else:
            version, family = m.group(1), m.group(2)
            name = family if _version(version) == newest[family] else f"{family}-{version.replace('.', '')}"
            tier = FAMILY_TIERS.get(family, DEFAULT_TIER)
        name = _free(name, model_id, taken)
        taken.add(name)
        price = OPENAI_PRICES.get(model_id)
        declaration = f"{name}=codex/{model_id}@{price[0]:g}/{price[1]:g}/{tier}" if price is not None else None
        out.append(Proposal(model_id, name, declaration))
    return out


def _free(name: str, model_id: str, taken: set[str]) -> str:
    """Return `name`, or a variant of it no one holds: the model id appended, then a counter."""
    if name not in taken:
        return name
    base = f"{name}-{re.sub(r'[^a-z0-9]', '', model_id.lower())}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def price_note(model_id: str, *, price_in: float, price_out: float) -> str | None:
    """Return a note when a declared price differs from OpenAI's list price for `model_id`, else None."""
    listed = OPENAI_PRICES.get(model_id)
    if listed is None or (abs(listed[0] - price_in) < PRICE_TOLERANCE and abs(listed[1] - price_out) < PRICE_TOLERANCE):
        return None
    return (f"declared at {price_in:g}/{price_out:g}, OpenAI lists {listed[0]:g}/{listed[1]:g} "
            f"(as of {OPENAI_PRICES_DATE})")
