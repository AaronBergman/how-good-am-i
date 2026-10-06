# how-good-am-i

Tell your coding agent how good it is, empirically.

Language models learn what "an LLM can do" mostly from text written about earlier, weaker models, so they tend to underestimate themselves: hedging, handing work back, refusing ambitious tasks on capability grounds. This installs a small hook into **Claude Code**, **Codex** and **OpenCode** that puts real benchmark numbers into the agent's context at the start of every session: [Artificial Analysis](https://artificialanalysis.ai/) scores for the exact model (and reasoning effort) running that session, next to the same scores for a trail of earlier frontier models (GPT-3.5, GPT-4, Claude 3, ..., today's frontier).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AaronBergman/how-good-am-i/main/install.sh | bash
```

It detects which of Claude Code, Codex and OpenCode you have and sets up each one. Needs `python3` (3.8+, standard library only). Config files it edits are backed up first (`*.hgai-backup-<timestamp>`), and re-running it is safe (it updates rather than duplicates).

**Codex only:** Codex skips new hooks until you approve them. After installing, run `/hooks` once inside Codex and trust the two `how-good-am-i` hooks.

Preview exactly what your agents will see:

```bash
python3 ~/.how-good-am-i/hgai.py
```

Uninstall:

```bash
python3 ~/.how-good-am-i/install.py --uninstall
```

## What the agent sees

Abridged example for a Claude Code session on Claude Opus 5.5 at `xhigh` effort (data as of 2026-10-06):

```
<how-good-am-i source="Artificial Analysis, artificialanalysis.ai" data_as_of="2026-10-06">
You are running as: claude-opus-5-5, effort xhigh
Matched to Artificial Analysis entry: "Claude Opus 5.5 (Xhigh)" (released 2026-09-22)
- AA Intelligence Index (composite of ~10 evals): 56.0, rank #2 of 682 models AA tracks (top 0.3%)
- Terminal-Bench 4.0 (agentic terminal tasks) 60%; Humanity's Last Exam 57%; SciCode 65%; AA long-context reasoning 85%
- Same model at each reasoning setting (Intelligence Index): low 42.3 / medium 51.2 / high 53.6 / xhigh 56.0 (this one) / max 57.6

The same benchmarks across the frontier over time (one consistent snapshot; "-" = not measured):
released   model                              Index   Coding   TB 4.0  TB Hard      HLE     GPQA
2022-11-30 GPT-3.5 Turbo                        5.5     10.7        -        -        -      30%
2023-03-14 GPT-4                                6.7     13.1        -        -        -      35%
2024-03-04 Claude 3 Opus                        8.7     19.5        -        -       3%      49%
...
2025-08-07 GPT-5 (High)                        23.0     37.8        -      33%      28%      85%
...
2026-09-22 Claude Opus 5.5 (Xhigh)  <- you     56.0        -      60%        -      57%        -

Why this is here: your sense of what "an LLM" can do was learned mostly from text about earlier, weaker models ...
attempt ambitious work, and don't hand tasks back or hedge on capability grounds before actually trying.
Benchmarks are not a reliability guarantee, though, so keep verifying your work.
</how-good-am-i>
```

The full block is roughly 1,000-1,500 tokens, once per session (plus once more after a compaction or a model switch).

## How it knows which model is actually running

| Agent | Where it hooks in | How the model is identified |
|---|---|---|
| Claude Code | `SessionStart` + `UserPromptSubmit` hooks in `~/.claude/settings.json` | The exact model id of the latest reply in the session transcript. On a brand-new session (no reply yet) it uses the hook input, `--model`, `ANTHROPIC_MODEL`, or the `model` setting; aliases like `opus` are resolved to whatever that alias most recently ran as on your machine, and re-checked after the first reply. Effort comes from `--effort` / `effortLevel`. |
| Codex | `SessionStart` + `UserPromptSubmit` hooks in `~/.codex/hooks.json` | Codex passes the active model slug to hooks directly. Reasoning effort comes from `model_reasoning_effort` (config, profile, or `-c` override), else the model's default. |
| OpenCode | Plugin at `~/.config/opencode/plugins/how-good-am-i.ts` | The plugin sees the provider/model (and variant) of every request and injects the matching block into the system prompt. |

If you switch models mid-session (`/model`), the next prompt gets an updated block that says it supersedes the earlier one. If the model isn't one Artificial Analysis lists, you get the trail without a "you" row, and approximate matches are labeled as approximate.

Matching handles dated ids (`claude-sonnet-4-5-20250929`), Bedrock/Vertex ids, provider prefixes (`anthropic/...`), `[1m]` context suffixes, and picks the AA variant that matches the reasoning setting (e.g. Codex `gpt-6.1-sol` at `high` -> "GPT-6.1 Sol (High)").

## Data

- Source: the [Artificial Analysis](https://artificialanalysis.ai/) free API (benchmark fields only: no prices or speeds). Attribution to Artificial Analysis is required by their terms and is included in every injected block.
- A GitHub Action refreshes `data/benchmarks.json` daily, so you don't need an AA API key. Installed copies check for a newer file at most once a day, in a detached background process, so session start is never slowed down. That download (from `raw.githubusercontent.com`) is the only network request this tool makes. No telemetry.
- All ~700 models AA tracks are included, so any model can be matched, not only the ones in the trail.
- Artificial Analysis periodically rescales its Intelligence Index as it adds harder evals. Every number in a block comes from the same snapshot, so comparisons within the block are consistent; don't compare them with index values from other dates.

## Customizing

- **The trail:** edit `~/.how-good-am-i/trail.json` (a list of AA slugs; the current top 3 model families are always appended automatically). Re-running the installer resets it to the repo default.
- `HGAI_NO_REFRESH=1` disables the daily download; `HGAI_DATA_URL` points it at your own copy (e.g. a fork whose Action uses your own `AA_API_KEY` secret).
- `python3 ~/.how-good-am-i/hgai.py --model gpt-5.5 medium` previews the block for any model id and effort.

## Caveats

- Benchmarks measure benchmark performance. They say a lot about relative capability and much less about whether a particular task will go well, and the injected text says so.
- Model matching is heuristic. When AA has no entry at the requested effort, or no exact entry for the model id, the block says what it fell back to.
- Claude Code subagents (which may run on a different model) don't get a block of their own yet.

## Development

```bash
python3 -m unittest discover tests        # matching + hook behaviour against the committed data
AA_API_KEY=... python3 scripts/build_data.py   # rebuild data/benchmarks.json
bash install.sh                            # install from a local checkout
```

MIT licensed. Benchmark data © Artificial Analysis, used under their free API terms with attribution.
