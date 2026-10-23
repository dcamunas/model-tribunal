"""Each Claude profile keeps its own tribunal state."""

from model_tribunal.paths import home


def test_the_state_lives_in_the_claude_profile_in_use(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_TRIBUNAL_HOME")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "personal"))
    assert home() == tmp_path / "personal" / "model-tribunal"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))
    assert home() == tmp_path / "work" / "model-tribunal"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert home() == tmp_path / ".claude" / "model-tribunal"
    monkeypatch.setenv("MODEL_TRIBUNAL_HOME", str(tmp_path / "elsewhere"))
    assert home() == tmp_path / "elsewhere"


def test_the_old_shared_folder_is_noticed_at_session_start_until_it_is_moved(tmp_path, monkeypatch):
    from model_tribunal.hook import handle

    monkeypatch.delenv("MODEL_TRIBUNAL_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "profile"))
    assert handle({"hook_event_name": "SessionStart"}) is None
    (tmp_path / ".model-tribunal").mkdir()
    out = handle({"hook_event_name": "SessionStart"})
    assert out is not None and "no longer read" in out["systemMessage"] and "`mv " in out["systemMessage"]
    (tmp_path / ".model-tribunal").rename(tmp_path / "profile")
    assert handle({"hook_event_name": "SessionStart"}) is None
