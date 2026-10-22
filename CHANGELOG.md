# Changelog

Format from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.1] - 2026-10-22

### Changed

- The plugin is installed from the marketplace [dcamunas/claude-plugins](https://github.com/dcamunas/claude-plugins).

## [0.1.0] - 2026-10-22

### Added

- The tribunal as its own project. Until 0.15.1 of model-router it was a plugin inside that repository and
  shared its code; it now runs on model-router as a dependency, with its own state (`~/.model-tribunal`),
  its own ledger and `tribunal stats`, and its own hook environment.
- `tribunal qa`, `tribunal judge`, `tribunal config` and `tribunal stats`; the `tribunal` plugin with the
  `Stop` hook that reviews the code each turn changes, the `router`-free setup (`tribunal config --codex-home`,
  `--codex-models`, `--declare`), and the Codex door with every tool off.
- Settings live at the top of `~/.model-tribunal/config.json` (`mode`, `claude`, `codex`, `arbiter`,
  `rounds`, `codex_home`, `models`); `MODEL_TRIBUNAL=0` turns the hook off, `MODEL_TRIBUNAL_CODEX_HOME`
  names the Codex home, `MODEL_TRIBUNAL_HOME` moves the state.

### Upgrading from the plugin inside model-router (0.15.1 and before)

Nothing is read from the router's state. Set the Codex home and the models again (`tribunal config
--codex-home ... --codex-models ...`); the old settings in `~/.model-router/config.json` (`codex_home`,
`external`, `qa`) can be deleted, the router ignores them. Reviews the old plugin wrote into the router's
ledger stay there; `tribunal stats` starts from zero.
