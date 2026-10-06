# how-good-am-i

Injects Artificial Analysis (AA) benchmark scores for the model actually running a coding-agent session, plus a trail of historical frontier models, into Claude Code / Codex / OpenCode context. Public repo: https://github.com/AaronBergman/how-good-am-i (one-command install via `install.sh`).

## Layout

- `hgai/hgai.py`: the whole runtime (stdlib only, Python 3.8+). Modes: `claude` / `codex` (hook: stdin JSON in, `hookSpecificOutput.additionalContext` JSON out), `opencode` (plain text out, called by the plugin), no args = preview, `--refresh`, `--model <id> [effort]`.
- `hgai/trail.json`: curated AA slugs for the historical trail; top-3 current families are appended automatically.
- `opencode/how-good-am-i.ts`: OpenCode plugin; shells out to `hgai.py opencode` and caches per model.
- `installer/install.py`: merges hooks into `~/.claude/settings.json` and `~/.codex/hooks.json`, copies the OpenCode plugin, installs runtime to `~/.how-good-am-i/`. Our hook entries are identified by the substring `how-good-am-i/hgai.py`. `--uninstall` reverses it.
- `scripts/build_data.py`: AA API -> `data/benchmarks.json` (benchmark fields only, no prices/speeds, so it only changes when scores change). Run daily by `.github/workflows/update-data.yml` with repo secret `AA_API_KEY`.
- `tests/test_match.py`: `python3 -m unittest discover tests`. The Action runs these before committing new data.

## Non-obvious decisions

- Claude Code's SessionStart input has no `model` in `-p` mode (verified 2026-10-06, v2.1.280); the authoritative source is the latest main-thread assistant `message.model` in the transcript. At brand-new session start we fall back to `--model` / env / settings, and aliases (`opus`, `sonnet`) are resolved from `~/.how-good-am-i/aliases.json`, learned from the user's own transcripts, because Claude Code's aliases lag the newest AA release (`sonnet` was `claude-sonnet-5` while AA's newest Sonnet was 5.5).
- `ancestor_args()` only reads the nearest non-shell ancestor, so nested `claude -p` runs don't inherit an outer session's `--effort`.
- Codex passes `model` to hooks; effort is read from config.toml / profile / `-c`, else `models_cache.json` `default_reasoning_level`. Codex requires users to trust new hooks via `/hooks` (hash-based); we do not try to bypass that.
- Matching: family key = sorted words + ordered numbers from the AA name minus its parenthetical; variant (reasoning/effort) parsed from the parenthetical. Approximate tiers (superset/subset families) are labeled as approximate in the output.
- AA rescales its Intelligence Index often; only compare numbers from the same snapshot (which every block is).
- Hooks must never break a session: any exception exits 0 silently and logs to `~/.how-good-am-i/error.log`.
