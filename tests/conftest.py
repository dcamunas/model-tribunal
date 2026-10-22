import pytest


@pytest.fixture(autouse=True)
def _own_state_dirs(tmp_path_factory, monkeypatch):
    """Keep every test away from ~/.model-tribunal and ~/.model-router: their config and ledgers are the machine's."""
    monkeypatch.setenv("MODEL_TRIBUNAL_HOME", str(tmp_path_factory.mktemp("tribunal")))
    monkeypatch.setenv("MODEL_ROUTER_HOME", str(tmp_path_factory.mktemp("router")))
    monkeypatch.delenv("MODEL_TRIBUNAL", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_CHILD", raising=False)
    monkeypatch.delenv("MODEL_TRIBUNAL_CODEX_HOME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_MODELS", raising=False)


@pytest.fixture
def freeze_clock(monkeypatch):
    """Return a function that sets the engine's clock (`model_router.clock.now`) to a fixed moment."""
    from model_router import clock

    def freeze(moment):
        monkeypatch.setattr(clock, "now", lambda: moment)

    return freeze
