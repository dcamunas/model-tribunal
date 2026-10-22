import json

import pytest
from model_router.catalog import MODELS, parse_cell

from model_tribunal.catalog import declared_models, load_declared, parse_declaration
from model_tribunal.codex_models import OPENAI_PRICES, listed_models, price_note, propose


def cache(home, *models, raw=None):
    home.mkdir(exist_ok=True)
    data = raw if raw is not None else {"fetched_at": "x", "models": list(models)}
    (home / "models_cache.json").write_text(json.dumps(data) if not isinstance(data, str) else data)
    return home


def m(slug, visibility="list"):
    return {"slug": slug, "visibility": visibility, "supported_reasoning_levels": [{"effort": "low"}]}


def test_the_home_lists_only_the_models_its_picker_shows_and_a_strange_file_lists_nothing(tmp_path):
    home = cache(tmp_path / "h", m("gpt-6-sol"), m("gpt-reserve", "hide"), m("gpt-6-luna"), {"no": "slug"}, "junk",
                 {"slug": 7})
    assert listed_models(home) == ["gpt-6-sol", "gpt-6-luna"]
    assert listed_models(home, include_hidden=True) == ["gpt-6-sol", "gpt-reserve", "gpt-6-luna"]
    assert listed_models(cache(tmp_path / "empty")) is None  # an empty list says nothing about what runs
    assert listed_models(tmp_path / "missing") is None
    assert listed_models(cache(tmp_path / "bad", raw="{not json")) is None
    assert listed_models(cache(tmp_path / "shape", raw={"models": "nope"})) is None


def test_proposals_name_the_newest_of_a_family_plainly_price_it_and_respect_what_is_declared():
    ids = ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-7-nova"]
    by_id = {p.model_id: p for p in propose(ids, {"gpt-6-luna": "luna"})}
    assert by_id["gpt-6-astra"].declaration == "astra=codex/gpt-6-astra@10/50/3"
    assert by_id["gpt-6-sol"].declaration == "sol=codex/gpt-6-sol@2/10/1"
    assert by_id["gpt-5.6-sol"].declaration == "sol-56=codex/gpt-5.6-sol@4/20/1"
    assert by_id["gpt-5.6-terra"].declaration == "terra=codex/gpt-5.6-terra@2/12/1"
    assert by_id["gpt-5.6-luna"].declaration == "luna-56=codex/gpt-5.6-luna@0.2/1.2/0"
    assert by_id["gpt-6-luna"].declared_as == "luna" and by_id["gpt-6-luna"].declaration is None
    nova = by_id["gpt-7-nova"]
    assert nova.name == "nova" and nova.declaration is None  # no list price here: declared by hand or not at all


def test_versions_compare_as_numbers_and_no_two_proposals_or_a_claude_name_collide(monkeypatch):
    from model_tribunal import codex_models

    names = {p.model_id: p.name for p in propose(["gpt-6.1-sol", "gpt-6.10-sol", "gpt-6.9-sol"], {})}
    assert names == {"gpt-6.10-sol": "sol", "gpt-6.9-sol": "sol-69", "gpt-6.1-sol": "sol-61"}
    names = [p.name for p in propose(["gpt-6-sol", "gpt-6.0-sol"], {})]
    assert len(set(names)) == 2  # 6 and 6.0 are the same version: the second one gets its own name
    monkeypatch.setitem(codex_models.OPENAI_PRICES, "gpt-7-opus", (1.0, 2.0))
    opus = propose(["gpt-7-opus"], {})[0]
    assert opus.name != "opus" and opus.declaration is not None and "/2" in opus.declaration  # unknown family: tier 2
    taken = propose(["gpt-6-sol"], {"gpt-5.5": "sol", "x": "sol-gpt6sol"})
    assert taken[0].name == "sol-gpt6sol-2"


def test_a_name_another_model_holds_is_kept_apart_and_every_proposal_parses():
    proposals = propose(["gpt-6-sol"], {"gpt-5.5": "sol"})  # someone already called gpt-5.5 "sol"
    assert proposals[0].name == "sol-gpt6sol" and proposals[0].declaration is not None
    for p in propose(list(OPENAI_PRICES), {}):
        assert p.declaration is not None
        name, spec = parse_declaration(p.declaration)
        assert spec is not None and name == p.name and spec.id == p.model_id


def test_price_note_flags_a_declared_price_that_is_not_the_list_one():
    assert price_note("gpt-6-luna", 0.10, 0.50) is None
    note = price_note("gpt-6-luna", 2.5, 12.5)
    assert note is not None and "declared at 2.5/12.5, OpenAI lists 0.1/0.5" in note
    assert price_note("deepseek-reasoner", 1, 2) is None  # not an OpenAI model the tribunal knows a price for


def test_declarations_register_codex_models_apart_from_claude_s_and_are_validated():
    name, luna = parse_declaration("luna=codex/gpt-6-luna@0.1/0.5/0")
    assert name == "luna" and luna is not None and (luna.provider, luna.tier, luna.default_effort) == ("codex", 0, "medium")
    assert luna.efforts == ("low", "medium", "high", "xhigh")
    assert parse_declaration("luna=") == ("luna", None)
    with pytest.raises(ValueError, match="Claude model's name"):
        parse_declaration("opus=codex/gpt-6-sol@1/1")
    with pytest.raises(ValueError, match="expected NAME=codex"):
        parse_declaration("gpt=openai/gpt-5.5@5/30")  # the only door is Codex
    with pytest.raises(ValueError, match="Claude model's id"):
        parse_declaration("x=codex/claude-opus-5-5@1/1")
    assert load_declared({"models": {"x": {"id": "claude-opus-5-5", "input": 1, "output": 1}}}) == []  # skipped, not fatal
    loaded = load_declared({"models": {
        "weak": {"id": "gpt-6-luna", "input": 2.5, "output": 12.5, "tier": 0},
        "bad": {"id": "x", "input": 1, "output": 2, "tier": 9},  # a tier outside 0..3 does not load
        "junk": "not an entry"}})
    try:
        assert loaded == ["weak"] and [s.short for s in declared_models()] == ["weak"]
        assert parse_cell("weak/medium").capability == 0.37  # Haiku-class, not Opus-class
        assert load_declared({"models": {"luna": {"id": "gpt-6-luna", "input": 0.1, "output": 0.5}}}) == ["luna"]
        assert "weak" not in {s.short for s in MODELS.values()}  # replaced, not added
        with pytest.raises(ValueError, match="already declared"):
            parse_declaration("other=codex/gpt-6-luna@1/1")
    finally:
        load_declared({})
    assert declared_models() == []
