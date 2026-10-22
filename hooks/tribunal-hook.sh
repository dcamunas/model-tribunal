#!/bin/sh
# Run the tribunal hook from a virtualenv kept in the plugin's data directory.
#
# Claude Code installs a plugin by copying the repository; it installs no Python. So the first
# SessionStart (and every one after the plugin version changes) builds the venv with uv from the
# plugin root, which brings model-router in as a dependency, and records the version. The other
# events never build: with no venv they exit quietly, so a turn is never delayed. Any failure exits
# 0 with a note on stderr: a hook must never block Claude. The event JSON on stdin is passed through
# to the real hook untouched. MODEL_TRIBUNAL=0 turns the hook off.
set -u
PATH="${PATH:-}:/usr/bin:/bin"  # sed, cat and mkdir, whatever environment Claude Code was started from

if [ "${MODEL_TRIBUNAL:-}" = "0" ]; then
    exit 0
fi
root="${CLAUDE_PLUGIN_ROOT:-}"
data="${CLAUDE_PLUGIN_DATA:-}"
if [ -z "$root" ] || [ -z "$data" ]; then
    echo "tribunal-hook: CLAUDE_PLUGIN_ROOT or CLAUDE_PLUGIN_DATA is missing; this script only runs as a plugin" >&2
    exit 0
fi

venv="$data/venv"
hook="$venv/bin/tribunal-hook"
stamp="$data/installed-version"
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/pyproject.toml" | head -n 1)
installed=$(cat "$stamp" 2>/dev/null || true)

if [ ! -x "$hook" ] || [ "$installed" != "$version" ]; then
    if [ "${1:-}" != "--bootstrap" ]; then
        echo "tribunal-hook: the plugin environment is not ready yet; it is built when a session starts" >&2
        exit 0
    fi
    # MODEL_TRIBUNAL_UV names the uv binary when it is somewhere unusual; otherwise PATH and the usual places.
    uv="${MODEL_TRIBUNAL_UV:-}"
    if [ -z "$uv" ]; then
        uv=$(command -v uv 2>/dev/null || true)
        for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
            if [ -z "$uv" ] && [ -x "$candidate" ]; then
                uv="$candidate"
            fi
        done
    fi
    if [ -z "$uv" ] || [ ! -x "$uv" ]; then
        echo "tribunal-hook: cannot find uv; install it (https://docs.astral.sh/uv/) and start a new session" >&2
        exit 0
    fi
    mkdir -p "$data"
    if [ ! -x "$venv/bin/python" ] && ! "$uv" venv -q "$venv" </dev/null >&2; then
        echo "tribunal-hook: could not create the environment in $venv; see the uv error above" >&2
        exit 0
    fi
    # --reinstall-package: the version may be the same while the files changed (a local marketplace).
    if "$uv" pip install -q --python "$venv/bin/python" --reinstall-package model-tribunal "$root" </dev/null >&2; then
        printf '%s' "$version" >"$stamp"
    else
        echo "tribunal-hook: could not install the package in $venv; see the uv error above" >&2
        exit 0
    fi
fi

if [ "${1:-}" = "--bootstrap" ]; then
    cat >/dev/null  # SessionStart: the environment is ready, nothing to judge yet
    exit 0
fi
exec "$hook"
