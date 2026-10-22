"""The installed package version, recorded in every review so the record says which tribunal judged."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def tribunal_version() -> str:
    """Return the installed version of model-tribunal, or `unknown` from a checkout that is not installed."""
    try:
        return version("model-tribunal")
    except PackageNotFoundError:
        return "unknown"
