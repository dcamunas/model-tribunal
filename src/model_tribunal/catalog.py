"""The Codex models the tribunal may seat as judge B, declared in its config.json and registered in the router's
catalogue when the tribunal starts, so their cells can be named, priced and recorded. The router never routes
to them.

A declaration is `NAME=codex/MODEL_ID@INPUT/OUTPUT[/TIER]`: a short name of your own, the model id Codex knows,
OpenAI's list prices in USD per million tokens (declared even on a subscription, so every review is priced the
same way), and a tier from 0 (Haiku) to 3 (Fable), DEFAULT_TIER when omitted. `tribunal config --codex-models`
writes declarations from what a Codex home lists; `--declare` writes one by hand.
"""

from __future__ import annotations

import re
from typing import Any  # Any: config.json, decoded

from model_router.catalog import MODELS, ModelSpec, register, unregister

PROVIDER = "codex"
DEFAULT_TIER = 2  # a declared model whose declaration gives no tier: as capable as Opus
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh")  # what Codex accepts
MIN_TIER, MAX_TIER = 0, 3  # Haiku's tier to Fable's
SYNTAX = re.compile(rf"^([a-z][a-z0-9-]*)=(?:codex/([^@\s]+)@([0-9.]+)/([0-9.]+)(?:/([{MIN_TIER}-{MAX_TIER}]))?)?$")
MODELS_KEY = "models"  # the config.json key the declarations live under


def parse_declaration(text: str) -> tuple[str, ModelSpec | None]:
    """Parse one declaration into the short name and its spec; `NAME=` alone means remove and gives None.

    Raises:
        ValueError: the syntax is not right, or the name or the id belongs to a Claude model.
    """
    match = SYNTAX.match(text.strip())
    if match is None:
        raise ValueError(f"expected NAME=codex/MODEL_ID@INPUT/OUTPUT[/TIER] (TIER {MIN_TIER} to {MAX_TIER}; NAME= "
                         f"removes), got {text!r}")
    name, model_id, price_in, price_out, tier = match.groups()
    if any(spec.short == name and spec.claude for spec in MODELS.values()):
        raise ValueError(f"{name!r} is a Claude model's name; pick another short name")
    if model_id is None:
        return name, None
    taken = MODELS.get(model_id)
    if taken is not None and taken.claude:
        raise ValueError(f"{model_id!r} is a Claude model's id; a declaration names a Codex model")
    if taken is not None and taken.short != name:
        raise ValueError(f"{model_id!r} is already declared as {taken.short!r}; one declaration per model id")
    cost_in, cost_out = float(price_in), float(price_out)
    # cached tokens are priced at the input rate: Codex reports them, OpenAI's list has no separate price for them
    return name, ModelSpec(id=model_id, short=name, tier=int(tier) if tier else DEFAULT_TIER, input=cost_in,
                           output=cost_out, cache_read=cost_in, cache_write=cost_in, default_effort="medium",
                           server_fallbacks=False, provider=PROVIDER, efforts=EFFORTS)


def as_declaration(name: str, entry: dict[str, Any]) -> str:
    """Return the declaration text of a config.json entry (`{"id", "input", "output", "tier"}`) under `name`."""
    text = f"{name}=codex/{entry.get('id')}@{entry.get('input')}/{entry.get('output')}"
    return text + (f"/{entry['tier']}" if entry.get("tier") is not None else "")


def load_declared(config: dict[str, Any]) -> list[str]:
    """Register the models declared in `config[MODELS_KEY]`, replacing any registered before, and return their
    short names. Entries that do not parse are skipped, a second declaration of a model id among them.
    """
    for model_id in [m for m, spec in MODELS.items() if spec.provider == PROVIDER]:
        unregister(model_id)
    loaded = []
    declared = config.get(MODELS_KEY)
    for name, entry in (declared.items() if isinstance(declared, dict) else []):
        if not isinstance(entry, dict):
            continue
        try:
            _, spec = parse_declaration(as_declaration(str(name), entry))
            if spec is not None:
                register(spec)
        except ValueError:
            continue
        if spec is not None:
            loaded.append(spec.short)
    return loaded


def declared_models() -> list[ModelSpec]:
    """Return the Codex models registered now, the candidates for judge B."""
    return [spec for spec in MODELS.values() if spec.provider == PROVIDER]
