import pytest


@pytest.fixture(autouse=True)
def _own_profile(tmp_path_factory, monkeypatch):
    """Keep every test away from the machine's Claude profile, where the tribunal's and the router's state live."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("profile")))
    monkeypatch.delenv("MODEL_TRIBUNAL", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_CHILD", raising=False)


@pytest.fixture
def freeze_clock(monkeypatch):
    """Return a function that sets the engine's clock (`model_router.clock.now`) to a fixed moment."""
    from model_router import clock

    def freeze(moment):
        monkeypatch.setattr(clock, "now", lambda: moment)

    return freeze
