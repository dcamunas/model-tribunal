# Changelog

Format from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.4] - 2026-10-23

### Changed

- The state moves from `~/.model-tribunal`, shared by every Claude profile on the machine, to
  `model-tribunal/` inside the profile in use (`$CLAUDE_CONFIG_DIR`, else `~/.claude`): two profiles no longer
  share judges, Codex home or reviews. `MODEL_TRIBUNAL_HOME` still moves it.
- Depends on model-router 0.19.0, which keeps its own state per profile the same way.

### Added

- While `~/.model-tribunal` is still there, the hook says so when a session starts, and `tribunal config` lists
  it, with how to move it.

Upgrading: move `~/.model-tribunal` into the profile it belongs to before that profile's next session, for
instance `mv ~/.model-tribunal ~/.claude-personal/model-tribunal`. If the folder already exists, a plain `mv`
puts the old one inside it; merge by hand instead.

## [0.1.3] - 2026-10-22

### Changed

- Runs on model-router 0.18.0, which knows when a Claude model's usage limit is spent and the exact moment it
  resets. A seat no longer goes to that model until then: with Fable spent and an Opus chat, judge A is another
  Claude model. The first review that hits the limit still cannot judge; the ones after it can.

## [0.1.2] - 2026-10-22

### Fixed

- A session opened before a plugin update stopped judging until a new session started, and a SessionStart from
  it (a compact, a resume) put the older version back under every newer session. The hook now runs the newer
  environment as it is and never downgrades.

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

## History inside model-router (0.4.0 to 0.15.1)

Until 0.16.0 of model-router the tribunal was a verification mode, then a plugin, inside that repository.
These are its entries there, as they were written; the version numbers are model-router's.

### [0.14.0] - 2026-10-22

- Codex is the tribunal's alone: `tribunal config --codex-home`, `--codex-models` and `--declare` set it up.
  It now always runs in an empty directory with its file, image, web, browser and desktop tools off: with a
  shell its read-only sandbox read a decoy file outside the repository, `view_image` read an image outside it,
  and what they read goes to OpenAI. The judges are told to judge only the material when they cannot open files. The tribunal sends both judges the diff
  and the changed files as they are now; the Claude judge can still read the repository, and nothing outside it.

### [0.13.0] - 2026-10-22

The tribunal leaves the router and becomes a plugin of its own. The router picks a model and an effort,
checks with a checker or one judge on another model, escalates and calibrates. Two judges blind to each
other, a Claude model and a Codex model, with an arbiter on a split, are the `tribunal` plugin's: for the
code each turn changes, and for critical texts when you ask. Same repository, same engine, installed apart.
- The `tribunal` plugin, second entry in the `dcamunas` marketplace (`plugins/tribunal`, with its own
  README). Its `Stop` hook judges the code a turn changed against the request: a Claude judge that is never
  the chat's model, which wrote the change, and a Codex judge, in parallel, blind, read-only inside the
  repository; an arbiter on a split. A turn gets `rounds` reviews (2, at most 5) and a fail goes back to
  Claude with the findings until the last one; a pass is one line; a judge that cannot be reached never
  blocks, and the other judge's verdict is still shown. The change is what the
  working tree became since the request: a snapshot through a temporary index (HEAD's tree, every tracked
  file rehashed, untracked files up to 1 MB), so a commit during the turn still counts and neither the
  index nor the files are touched. The settings are fixed when the request arrives. A turn that only
  touched documentation is not judged; when another session's transcript shows an editing tool used during
  the turn, a fail is reported and not sent back. `SessionEnd` closes a session's turn. A diff over 50,000
  characters reaches the judges as the list of changed files plus its first part. The plugin installs no Python:
  it runs from the model-router plugin's environment.
- The `tribunal` command: `tribunal qa` (a code change, uncommitted or since `--base`), `tribunal judge`
  (a text written elsewhere; `--cell` keeps the author's model off the bench) and `tribunal config`
  (`--mode`, `--claude`, `--codex`, `--arbiter`, `--rounds`; `default` resets; judges on one model are
  refused before anything is written). Exit 0 on a pass, 1 on a fail, 2 when it could not judge. A repository's `.claude/tribunal.json` and a `qa:` line at the top of a
  request override the machine. `qa: parallel` is accepted and runs as a final review until it is built.
- `CliClient(tools=, cwd=)` and `CodexClient(workdir=)`: a judge that reads a repository, read-only; the
  Claude one loads only the user's settings and cannot read outside the repository. `Verifier` takes its
  judge and arbiter prompts and can ask a tribunal's judges at the same time; it needs only a policy that
  names judges and an arbiter (`JudgePolicy`).
- From the router: `panel` and `tribunal` as verification modes the policy picks (`Policy(panel_kinds=,
  critical_mode=)` are gone) or the CLI offers (`--verify` takes none, light or full), and `router config
  --arbiter`. At critical risk the router now checks with the full judge alone, and a task file that asks
  for `panel` or `tribunal` is refused. The engine still seats two judges and an arbiter when the tribunal
  asks, so a library caller can pass `verify="tribunal"`; the policy resolves its arbiter only then, so a
  stale `arbiter` setting cannot stop the router.
- The simulation's `router (one judge)` and `router (panel)` strategies.
- Every `claude -p` and `codex exec` the router starts carries `MODEL_ROUTER_TRIBUNAL=0`, so a worker's or
  a judge's own session is never sent to the tribunal plugin.
- `router stats` leaves the tribunal's records out of its tasks and cells (their judges still count on the
  scoreboard, and one line gives the tribunal's reviews and spend), and `router feedback` on one of them no
  longer moves the routing feedback.

### [0.12.0] - 2026-10-22

- `router config --codex-models` lists the models the configured Codex home can run, read from the home's own model list (`models_cache.json`, which Codex writes), each as a ready declaration with OpenAI's list price as of 2026-10-22 and a tier by family (Luna 0, Sol and Terra 1, Astra 3); `--codex-models sol,luna` declares those and nothing else, all or none, before `--models` and `--judge` in the same command so they may name them; a model already declared through another door is shown as such. `--codex-home` prints the same list when it sets the home. The quota is not read: no stable interface carries it, which is why nothing is declared without being named. A model with no list price known here is shown and left to `--external`.
- `router doctor` says when a declared OpenAI model's price is not the list one, and fails a `codex` model that is not among the models its home knows (hidden ones included; a home with no list yet is not judged).

### [0.10.0] - 2026-10-21

- Models of other providers do the work too, once `router config --models` lists them: the lattice takes them with the efforts their door accepts, a start table may name them, and a declared model that is not listed keeps judging while the Claude of its tier takes its tasks (the plan says so). Two new doors by subscription, next to the API ones: `codex` (ChatGPT through `codex exec`, on a Codex home given with `router config --codex-home` or `MODEL_ROUTER_CODEX_HOME`, never the ambient `~/.codex`) and `gemini` (`gemini -p`). Each CLI call runs in an empty directory with read-only tools and nothing persisted (Gemini has no home switch: that door runs on the machine's own `~/.gemini`), and the environment `codex exec` inherits carries no API key or base URL, since the home decides the account; a verdict is asked for with Codex's `--output-schema` (strict, as that backend demands) or in Gemini's prompt, validated here, tried once more when it is not JSON, and otherwise reported as a judge error; a run that ends without an answer is an error too, and an error text never carries the model's output (the ledger keeps error texts, never answers). `router doctor` says per declared model whether its door opens (key, home signed in, executable) and `--live` makes one call through each CLI; `router cells` says which declared models work. The arbiter now prefers a company other than the full judge's (`codex` and `openai` are both OpenAI). The Claude Code hook keeps choosing among Claude models only.
- A declared model takes the tier you give it, `router config --external NAME=DOOR/ID@IN/OUT/TIER`, 0 Haiku to 3 Fable; Opus (2) when omitted, as before. The tier is where the ladder places the model when it escalates; `router calibrate` measures what it passes. `router cells` and `router config` show it.
- The arbiter by rule is the next model up from the full judge and the worker, not from the judge alone: with a judge below the worker (a declared model at tier 0, say) the old rule handed the split to the worker's own model at low effort.
- Judge and arbiter cells take an effort their door accepts: a DeepSeek judge configured at high runs, is priced and is recorded at medium, and a cell one effort up that its door cannot give is not an arbiter, so the full judge is never asked twice. Which model is the next up, or the one below, no longer depends on set order when a declared model shares a tier with a Claude one: Claude first, then by name.
- One declaration per model id: the catalogue is keyed by it, so `router config --external` refuses a second door for an id already declared under another name, or a Claude id through another door, and loading the config skips such an entry.
- `router config --external NAME=` also drops the model from `models`, `judge` and `arbiter`, and says so; a reference left behind stopped every command.
- The doors were built against the CLIs' documented flags and output (Codex 0.156, Gemini 0.46) and exercised with doubles. The `codex` door then ran live through a ChatGPT account, as the full judge of a tribunal: it passed a right answer, failed a wrong one with the right reasons, and failed a first attempt that broke the output constraint, which went up the ladder and passed. The `gemini` door has not run live yet.

### [0.8.0] - 2026-10-20

- Tribunal verdicts were not treated as evidence: they never fed the feedback store and `router feedback` could not correct them. `VERIFIED_SOURCES` now includes `tribunal`.
- The README opens with a new figure, a critical task on trial: the tribunal told as a courtroom, every seat labelled with its cell (worker, two blind judges, arbiter, you), a split, a retry on the next cell and the ledger. Built by `docs/showcase/build.py`, which also keeps the frames (`--frames`) and the carousel; it replaces `tribunal.gif`.

### [0.7.0] - 2026-10-20

- `router run` never reached a judge from another provider: only `router bench` wrapped the client. Both do now.

### [0.6.1] - 2026-10-20

- README brought up to date with 0.6.0: judges may come from another provider (the council comparison, the simulation note, what leaves your machine, the library example), only verified verdicts feed the feedback store, a judge that cannot be reached ends the task as `error`. The skill says a judge from another provider cannot be a subagent.

### [0.6.0] - 2026-10-20

- Judges and arbiters from other providers: `router config --external NAME=PROVIDER/MODEL_ID@IN/OUT` declares a model of OpenAI, DeepSeek or xAI with its prices (USD per million tokens), `router config --judge NAME/high` or `--arbiter NAME/high` uses it, and the provider's key comes from `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` or `XAI_API_KEY`. One OpenAI-compatible client (standard library, no SDK) serves the three; structured verdicts are asked for as a JSON object and validated locally. External models never do the work: the routing lattice, the hook and the ladder stay on Anthropic cells. `router cells` lists them, `router doctor` checks their keys, and the arbiter rule prefers a cell on a provider other than the full judge's.

### [0.5.0] - 2026-10-20

- `tribunal` verification mode: the two judges of the panel, always both, blind to each other; when they agree that is the verdict, when they split an arbiter cell reads the task, the answer and both reasoned verdicts (as judge A and judge B, no names) and decides. One extra call only on a split, instead of a new attempt with a new pair of judges. It is now the mode the policy picks at critical risk for `analysis`, `write` and `review` tasks without a checker (`Policy(critical_mode="panel")` keeps the cheaper panel; `--verify tribunal` forces it anywhere).
- The arbiter: the next model up from the full judge at low effort, else the judge's model one effort up, else the worker's model one effort up; never one of the judges. `router config --arbiter` and `MODEL_ROUTER_ARBITER` fix it; `router config --arbiter rule` goes back to the rule. `router plan` prints it, `router doctor` and `router config` show it.
- A scoreboard of judges in `router stats`: per judge cell, verdicts and pass rate, agreement with the other judge, how often the arbiter backed it on a split, and how many of its verdicts a human override contradicted. Every judge's own verdict (and the arbiter's) is now in the ledger, under `verdicts`.
- The simulation compares `router` (tribunal) with `router (panel)` and `router (one judge)`.
- The skill tells the orchestrator how to run a tribunal with subagents: both reviews blind, a third subagent to arbitrate a split.
- `router (no panel)` in the simulation is now `router (one judge)`.

### [0.4.0] - 2026-10-20

- `panel` verification mode: two judges on different cells, cheapest first, blind to each other. The answer passes only when both pass, and the second judge is not asked when the first fails. The policy uses it on its own at critical risk for `analysis`, `write` and `review` tasks with no checker; `Policy(panel_kinds=...)` changes the kinds, `--verify full` forces one judge and `--verify panel` forces two on any task. It is the part of an LLM council that pays when every model is Claude: a second independent opinion, without shared answers, ranking or chairman.
