"""how-good-am-i: tell a coding agent how good it is, empirically.

Injects Artificial Analysis benchmark scores for the model actually running the session,
plus the same scores for a trail of earlier frontier models, into the agent's context.

How it is called (stdin is the agent's hook JSON; stdout is what gets injected):
  python3 hgai.py claude     Claude Code SessionStart / UserPromptSubmit hook
  python3 hgai.py codex      Codex SessionStart / UserPromptSubmit hook
  python3 hgai.py opencode   OpenCode plugin helper (stdin {"model": "provider/model", "variant": ...})
  python3 hgai.py            run by hand: previews what each installed agent would be told
  python3 hgai.py --refresh  download the latest benchmark data (normally automatic, daily)

Stdlib only (Python 3.8+). Any internal error exits 0 with no output, so a bug here can never
block or break an agent session; errors are logged to ~/.how-good-am-i/error.log.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("HGAI_HOME") or (Path.home() / ".how-good-am-i"))
HERE = Path(__file__).resolve().parent
DATA_URL = os.environ.get(
    "HGAI_DATA_URL", "https://raw.githubusercontent.com/AaronBergman/how-good-am-i/main/data/benchmarks.json"
)
REFRESH_EVERY_S = 24 * 3600
REFRESH_RETRY_S = 3 * 3600
STATE_TTL_S = 14 * 24 * 3600

EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]
REASONING_WORDS = {"reasoning", "thinking", "adaptive"}
# Words ignored when a strict family match fails (ids and AA names disagree on these).
SOFT_WORDS = {"preview", "latest", "exp", "experimental", "instruct", "chat", "it", "free"}
CLAUDE_ALIASES = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "fable": "fable", "opusplan": "opus"}

# Metric key -> (long label, short table label, kind). kind "index" = 0-100 score, "pct" = 0-1 fraction.
METRICS = {
    "artificial_analysis_intelligence_index": ("AA Intelligence Index (composite of ~10 evals)", "Index", "index"),
    "artificial_analysis_coding_index": ("AA Coding Index", "Coding", "index"),
    "terminalbench_v4_0": ("Terminal-Bench 4.0 (agentic terminal tasks)", "TB 4.0", "pct"),
    "terminalbench_v2_1": ("Terminal-Bench 2.1", "TB 2.1", "pct"),
    "terminalbench_hard": ("Terminal-Bench Hard", "TB Hard", "pct"),
    "hle": ("Humanity's Last Exam", "HLE", "pct"),
    "gpqa": ("GPQA Diamond (grad-level science)", "GPQA", "pct"),
    "scicode": ("SciCode", "SciCode", "pct"),
    "lcr": ("AA long-context reasoning", "LCR", "pct"),
    "ifbench": ("IFBench (instruction following)", "IFBench", "pct"),
    "tau2": ("tau2-Bench Telecom (agentic tool use)", "tau2", "pct"),
    "livecodebench": ("LiveCodeBench", "LCB", "pct"),
    "mmlu_pro": ("MMLU-Pro", "MMLU-Pro", "pct"),
    "aime_25": ("AIME 2025", "AIME25", "pct"),
}
II = "artificial_analysis_intelligence_index"
TABLE_METRIC_ORDER = [II, "artificial_analysis_coding_index", "terminalbench_v4_0", "terminalbench_hard",
                      "hle", "gpqa", "scicode", "lcr"]
MAX_TABLE_COLS = 6


# ---------------------------------------------------------------- data

def data_path():
    p = HOME / "benchmarks.json"
    return p if p.exists() else HERE.parent / "data" / "benchmarks.json"


def trail_path():
    p = HOME / "trail.json"
    return p if p.exists() else HERE / "trail.json"


def load_data():
    return json.loads(data_path().read_text())


def maybe_refresh_in_background():
    """If the local data is older than a day, refresh it in a detached process (never blocks)."""
    p = HOME / "benchmarks.json"
    if not HOME.exists() or os.environ.get("HGAI_NO_REFRESH"):
        return
    age = time.time() - p.stat().st_mtime if p.exists() else 1e12
    marker = HOME / ".last_refresh_attempt"
    tried = time.time() - marker.stat().st_mtime if marker.exists() else 1e12
    if age < REFRESH_EVERY_S or tried < REFRESH_RETRY_S:
        return
    marker.touch()
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--refresh"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def refresh():
    req = urllib.request.Request(DATA_URL, headers={"User-Agent": "how-good-am-i"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    d = json.loads(body)
    if len(d.get("models") or []) < 50:
        raise ValueError("downloaded data looks wrong; keeping the old file")
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = HOME / "benchmarks.json.tmp"
    tmp.write_bytes(body)
    tmp.replace(HOME / "benchmarks.json")
    print(f"Updated {HOME / 'benchmarks.json'}: {len(d['models'])} models, fetched_at {d.get('fetched_at')}")


# ---------------------------------------------------------------- matching

def tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def strip_dates(toks):
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"20\d{6}", t):  # 20250929
            i += 1
            continue
        if re.fullmatch(r"20\d\d", t) and i + 2 < len(toks) and all(re.fullmatch(r"\d\d", x) for x in toks[i + 1:i + 3]):
            i += 3  # 2024-08-06
            continue
        out.append(t)
        i += 1
    return out


def family_key(toks, soft=False):
    toks = [t for t in toks if not (soft and t in SOFT_WORDS)]
    words = sorted(t for t in toks if not t.isdigit())
    nums = [str(int(t)) for t in toks if t.isdigit()]
    return " ".join(words) + "|" + ".".join(nums)


def parse_aa_name(name):
    """'Claude Opus 5.5 (Xhigh, Default Fallback)' -> (family tokens, reasoning, effort, clean name)."""
    parens = re.findall(r"\(([^)]*)\)", name)
    base = re.sub(r"\s*\([^)]*\)", "", name).strip()
    reasoning, effort = None, None
    keep = []
    for p in parens:
        pl = p.lower()
        parts = [x.strip() for x in pl.split(",")]
        is_variant = False
        for x in parts:
            if x in ("non-reasoning", "nonreasoning"):
                reasoning, is_variant = False, True
            elif x in REASONING_WORDS or x == "adaptive reasoning":
                reasoning = True if reasoning is None else reasoning
                is_variant = True
            elif x in EFFORTS:
                effort, is_variant = x, True
                if reasoning is None:
                    reasoning = True
            elif "fallback" in x:
                is_variant = True
        if not is_variant:
            keep.append(p)  # e.g. "Oct '24" stays part of the display name
        else:
            shown = [x for x in p.split(",") if "fallback" not in x.lower()]
            if shown:
                keep.append(",".join(shown).strip())
    clean = base + "".join(f" ({k})" for k in keep)
    return tokens(base), reasoning, effort, clean


class Index:
    def __init__(self, data):
        self.data = data
        self.models = [m for m in data["models"] if m.get("name")]
        self.by_slug = {m["slug"]: m for m in self.models}
        self.strict, self.soft = {}, {}
        for m in self.models:
            toks, reasoning, effort, clean = parse_aa_name(m["name"])
            slug_toks = set(tokens(m["slug"]))
            if reasoning is None and slug_toks & {"thinking", "reasoning", "adaptive"}:
                reasoning = True
            m["_reasoning"], m["_effort"], m["_clean"] = reasoning, effort, clean
            m["_key"] = family_key(toks)
            self.strict.setdefault(m["_key"], []).append(m)
            self.soft.setdefault(family_key(toks, soft=True), []).append(m)
        ranked = sorted((m for m in self.models if m["ev"].get(II) is not None), key=lambda m: -m["ev"][II])
        self.n_ranked = len(ranked)
        for m in self.models:
            v = m["ev"].get(II)
            m["_rank"] = None if v is None else 1 + sum(1 for o in ranked if o["ev"][II] > v)

    def family(self, model_id):
        """(candidates, hint, approximate) for a raw id like 'anthropic/claude-sonnet-4-5-20250929[1m]'."""
        raw = model_id.strip().lower()
        raw = raw.split("/")[-1]
        raw = re.sub(r"\[.*?\]", "", raw)
        raw = re.sub(r"[:@].*$", "", raw)  # bedrock/vertex suffixes like ':0' or '@20250929'
        raw = re.sub(r"-v\d+$", "", raw)  # bedrock '-v1'
        raw = re.sub(r"^(us|eu|apac|global)\.", "", raw)
        raw = re.sub(r"^anthropic\.", "", raw)
        if raw in self.by_slug:
            return self.strict.get(self.by_slug[raw]["_key"], [self.by_slug[raw]]), None, False
        toks = strip_dates(tokens(raw))
        hint = "thinking" if {"thinking", "reasoning"} & set(toks) else None
        toks = [t for t in toks if t not in ("thinking", "reasoning")]
        cands = self.strict.get(family_key(toks)) or self.soft.get(family_key(toks, soft=True))
        if cands:
            return cands, hint, False
        # Approximate tiers: an AA family that extends the id ('qwen3-coder' -> 'Qwen3 Coder 480B ...'),
        # or one the id extends ('gpt-5.1-codex-max' -> 'GPT-5.1 Codex'). Numbers must agree.
        want = {t for t in toks if t not in SOFT_WORDS}
        nums = [str(int(t)) for t in toks if t.isdigit()]
        best = None
        for key, fam in self.strict.items():
            words_s, nums_s = key.split("|")
            fwords = {w for w in words_s.split() if w not in SOFT_WORDS} | set(nums_s.split(".")) - {""}
            fnums = [n for n in nums_s.split(".") if n]
            if not want - set(nums) or not fwords:
                continue
            if want <= fwords and fnums[:len(nums)] == nums:
                score = (0, len(fwords - want))
            elif fwords <= want and fnums == nums and len(fwords - set(nums)) >= 1:
                score = (1, len(want - fwords))
            else:
                continue
            newest = max((m.get("released") or "") for m in fam)
            cand = (score, "".join(chr(255 - ord(c)) for c in newest))
            if best is None or cand < best[0]:
                best = (cand, fam)
        return (best[1], hint, True) if best else ([], hint, False)

    def resolve_alias(self, alias):
        word = CLAUDE_ALIASES.get(alias)
        if not word:
            return None
        fams = [m for m in self.models if m.get("creator_slug") == "anthropic" and word in tokens(m["_clean"])]
        if not fams:
            return None
        newest = max(fams, key=lambda m: m.get("released") or "")
        return newest["slug"]

    def pick(self, cands, reasoning=None, effort=None, model_id=""):
        if not cands:
            return None
        date = re.search(r"(20\d\d)-?(\d\d)-?(\d\d)", model_id)
        want_date = datetime.date(*map(int, date.groups())) if date else None

        def score(m):
            s = 0.0
            if m["ev"].get(II) is None:
                s += 1000
            if reasoning is True and m["_reasoning"] is False:
                s += 100
            if reasoning is False and m["_reasoning"]:
                s += 100
            if effort in EFFORTS:
                me = m["_effort"] or ("high" if m["_reasoning"] else None)
                s += 10 * (abs(EFFORTS.index(effort) - EFFORTS.index(me)) if me else 3)
            elif not want_date:
                s -= (m["ev"].get(II) or 0) / 100  # unknown effort: best-scoring variant
            if want_date and m.get("released"):
                try:
                    s += abs((datetime.date.fromisoformat(m["released"]) - want_date).days) / 30
                except ValueError:
                    pass
            return s

        return min(cands, key=score)

    def match(self, model_id, reasoning=None, effort=None):
        """(best AA entry or None, its family, approximate?)"""
        cands, hint, approx = self.family(model_id)
        if hint == "thinking" and reasoning is None:
            reasoning = True
        return self.pick(cands, reasoning, effort, model_id), cands, approx


# ---------------------------------------------------------------- formatting

def fmt(key, v):
    if v is None:
        return "-"
    return f"{v:.1f}" if METRICS.get(key, ("", "", "pct"))[2] == "index" else f"{v * 100:.0f}%"


def effort_ladder(m, cands):
    rows = []
    for c in cands:
        if c["ev"].get(II) is None:
            continue
        label = "non-reasoning" if c["_reasoning"] is False else (c["_effort"] or ("reasoning" if c["_reasoning"] else "default"))
        rows.append((c["ev"][II], label, c is m))
    if len(rows) < 2:
        return None
    rows.sort()
    return " / ".join(f"{label} {v:.1f}" + (" (this one)" if mine else "") for v, label, mine in rows)


def trail_rows(idx, me):
    cfg = json.loads(trail_path().read_text())
    rows = [idx.by_slug[s] for s in cfg.get("anchors", []) if s in idx.by_slug]
    # Auto-append today's frontier: best variant of the top families, so the trail never goes stale.
    best = {}
    for m in idx.models:
        v = m["ev"].get(II)
        if v is not None and (m["_key"] not in best or v > best[m["_key"]]["ev"][II]):
            best[m["_key"]] = m
    have = {r["_key"] for r in rows}
    for m in sorted(best.values(), key=lambda m: -m["ev"][II])[: cfg.get("frontier_n", 3)]:
        if m["_key"] not in have:
            rows.append(m)
            have.add(m["_key"])
    if me is not None:
        rows = [r for r in rows if r["_key"] != me["_key"]] + [me]
    return sorted(rows, key=lambda r: (r.get("released") or "", r["ev"].get(II) or 0))


def table(rows, me):
    cols = []
    for k in TABLE_METRIC_ORDER:
        have = sum(1 for r in rows if r["ev"].get(k) is not None)
        if k == II or have >= 0.5 * len(rows) or (me is not None and me["ev"].get(k) is not None and have >= 0.25 * len(rows)):
            cols.append(k)
        if len(cols) >= MAX_TABLE_COLS:
            break
    names = [r["_clean"] + ("  <- you" if r is me else "") for r in rows]
    w = max(len(n) for n in names)
    head = f"{'released':<10} {'model':<{w}}  " + "  ".join(f"{METRICS[k][1]:>7}" for k in cols)
    lines = [head]
    for r, n in zip(rows, names):
        lines.append(f"{(r.get('released') or '?'):<10} {n:<{w}}  " + "  ".join(f"{fmt(k, r['ev'].get(k)):>7}" for k in cols))
    return "\n".join(lines)


def describe(idx, model_id, reasoning=None, effort=None, effort_note="", source_note="", agent=""):
    """The full context block for one model."""
    data = idx.data
    when = (data.get("fetched_at") or "")[:10]
    m, cands, approx = (idx.match(model_id, reasoning, effort) if model_id else (None, [], False))
    out = [f'<how-good-am-i source="Artificial Analysis, artificialanalysis.ai" data_as_of="{when}">']
    out.append("Empirical benchmark results for the model running this session, plus the same benchmarks for earlier frontier models. Injected automatically by the how-good-am-i hook.")
    out.append("")
    if m is None:
        what = f"'{model_id}'" if model_id else "not identified yet"
        out.append(f"Model in use: {what}{source_note}. No matching Artificial Analysis entry was found, so only the reference trail is shown.")
    else:
        eff = f", {effort_note}" if effort_note else ""
        out.append(f"You are running as: {model_id}{eff}{source_note}")
        caveats = []
        if approx:
            caveats.append("approximate: AA lists no exact entry for this model id, so this is the closest family")
        if effort in EFFORTS and m["_reasoning"] is not False and m["_effort"] and m["_effort"] != effort:
            caveats.append(f"AA has no {effort}-effort entry for this model; nearest measured setting shown")
        if reasoning is True and m["_reasoning"] is False:
            caveats.append("AA has no reasoning-mode entry for this model")
        cav = f" [{'; '.join(caveats)}]" if caveats else ""
        out.append(f"Matched to Artificial Analysis entry: \"{m['_clean']}\" (released {m.get('released') or 'unknown'}){cav}")
        ev = m["ev"]
        if ev.get(II) is not None:
            pct = 100 * m["_rank"] / max(1, idx.n_ranked)
            out.append(f"- {METRICS[II][0]}: {fmt(II, ev[II])}, rank #{m['_rank']} of {idx.n_ranked} models AA tracks (top {pct:.1f}%)")
        rest = [f"{METRICS[k][0]} {fmt(k, ev[k])}" for k in METRICS if k != II and ev.get(k) is not None]
        if rest:
            out.append("- " + "; ".join(rest))
        ladder = effort_ladder(m, cands)
        if ladder:
            out.append(f"- Same model at each reasoning setting (Intelligence Index): {ladder}")
    out.append("")
    out.append("The same benchmarks across the frontier over time (one consistent snapshot; \"-\" = not measured; Index is 0-100, others are % correct):")
    out.append(table(trail_rows(idx, m), m))
    out.append("")
    out.append("Why this is here: your sense of what \"an LLM\" can do was learned mostly from text about earlier, weaker models like the top rows of this table. Measured on identical tests, the model running this session is where the table says it is. When judging whether you can do something (a large refactor, a hard debugging session, a long multi-step task), weigh this evidence over inherited assumptions: attempt ambitious work, and don't hand tasks back or hedge on capability grounds before actually trying. Benchmarks are not a reliability guarantee, though, so keep verifying your work (run it, test it, read the output).")
    out.append("Note: AA periodically rescales its index as it adds harder evals, so compare numbers only within this block, not with scores remembered from training.")
    out.append("</how-good-am-i>")
    return "\n".join(out), (m["id"] if m else None)


# ---------------------------------------------------------------- per-agent model detection

def read_json(p):
    try:
        return json.loads(Path(p).expanduser().read_text())
    except Exception:
        return {}


def ancestor_args(max_depth=6):
    """Command line of the agent process that ran this hook (to catch `claude --model X --effort Y`).

    Walks up past the shell wrappers hooks run in and stops at the first real process, so a nested
    `claude -p` never inherits flags from an outer session."""
    pid = os.getppid()
    for _ in range(max_depth):
        try:
            r = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
            line = r.stdout.strip()
        except Exception:
            return []
        if not line:
            return []
        ppid, _, args = line.partition(" ")
        exe = os.path.basename(args.split(" ", 1)[0]).lstrip("-")
        if exe not in ("sh", "bash", "zsh", "dash", "fish", "env", "timeout"):
            return [args]
        pid = int(ppid.strip() or 0)
        if pid <= 1:
            return []
    return []


def flag_value(args_list, flag):
    for args in args_list:
        m = re.search(rf"(?:^|\s){re.escape(flag)}(?:=|\s+)(\S+)", args)
        if m:
            return m.group(1).strip("'\"")
    return None


def claude_transcript_model(transcript_path):
    """Exact model id of the latest main-thread assistant message, from the session transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    with open(transcript_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        chunk = f.read().decode("utf-8", "replace")
    for line in reversed(chunk.splitlines()):
        if '"assistant"' not in line or '"model"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        model = msg.get("model")
        if d.get("type") == "assistant" and model and not d.get("isSidechain") and not model.startswith("<"):
            return model
    return None


def claude_settings(cwd):
    merged = {}
    for p in [Path.home() / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.json",
              Path(cwd) / ".claude" / "settings.local.json"]:
        merged.update(read_json(p))
    return merged


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def alias_word(model_id):
    toks = tokens(model_id or "")
    return next((w for w in CLAUDE_ALIASES if w in toks), None)


def learned_aliases():
    """alias -> model id most recently seen running in this user's Claude Code transcripts.

    Claude Code's aliases ('opus', 'sonnet') can lag the newest release, so the newest AA model
    is not a safe guess. Bootstraps once by scanning recent transcripts, then is kept current by
    every hook call that sees a real model in a transcript."""
    p = HOME / "aliases.json"
    if p.exists():
        return read_json(p)
    found = {}
    try:
        files = sorted(claude_dir().glob("projects/*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:40]
    except OSError:
        files = []
    for f in files:
        mid = claude_transcript_model(str(f))
        w = alias_word(mid)
        if w and w not in found:
            found[w] = re.sub(r"\[.*?\]", "", mid)
    save_json(p, found)
    return found


def learn_alias(model_id):
    w = alias_word(model_id)
    if not w:
        return
    cur = learned_aliases()
    clean = re.sub(r"\[.*?\]", "", model_id)
    if cur.get(w) != clean:
        cur[w] = clean
        save_json(HOME / "aliases.json", cur)


def save_json(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def claude_context(inp, idx):
    """(model_id, reasoning, effort, effort_note, source_note) for a Claude Code session."""
    cwd = inp.get("cwd") or os.getcwd()
    settings = claude_settings(cwd)
    args = ancestor_args()
    model = claude_transcript_model(inp.get("transcript_path"))
    source = ""
    if model:
        learn_alias(model)
    else:
        hm = inp.get("model")
        if isinstance(hm, dict):
            hm = hm.get("id") or hm.get("display_name")
        model = hm or flag_value(args, "--model") or os.environ.get("ANTHROPIC_MODEL") or settings.get("model")
        if model:
            alias = re.sub(r"\[.*?\]", "", str(model)).strip().lower()
            if alias in CLAUDE_ALIASES:
                seen = learned_aliases().get(CLAUDE_ALIASES[alias])
                if seen:
                    model = seen
                    source = f" (from the alias '{alias}', which most recently ran as this model here; re-checked after the first reply)"
                else:
                    guess = idx.resolve_alias(alias)
                    if guess:
                        model = guess
                        source = f" (GUESS: alias '{alias}' assumed to mean the newest such model; re-checked after the first reply)"
            elif alias in ("default", ""):
                model = None
    effort = (flag_value(args, "--effort") or os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
              or settings.get("effortLevel"))
    effort = effort.lower() if isinstance(effort, str) and effort.lower() in EFFORTS else None
    reasoning = False if settings.get("alwaysThinkingEnabled") is False else True
    note = f"effort {effort}" if effort else "effort not set (shown: closest default)"
    if not effort:
        effort = "high"
    return model, reasoning, effort, note, source


def codex_effort(model):
    cfg_path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    effort = None
    args = ancestor_args()
    m = None
    for a in args:
        m = re.search(r"model_reasoning_effort\s*=\s*['\"]?(\w+)", a)
        if m:
            effort = m.group(1)
            break
    if not effort and cfg_path.exists():
        text = cfg_path.read_text()
        try:
            import tomllib
            cfg = tomllib.loads(text)
            prof = flag_value(args, "--profile") or flag_value(args, "-p") or cfg.get("profile")
            effort = ((cfg.get("profiles") or {}).get(prof) or {}).get("model_reasoning_effort") if prof else None
            effort = effort or cfg.get("model_reasoning_effort")
        except Exception:
            top = text.split("\n[", 1)[0]
            m = re.search(r"^model_reasoning_effort\s*=\s*['\"](\w+)['\"]", top, re.M)
            effort = m.group(1) if m else None
    note = f"reasoning effort {effort}" if effort else ""
    if not effort:
        cache = read_json(cfg_path.parent / "models_cache.json")
        for mm in cache.get("models") or []:
            if mm.get("slug") == model and mm.get("default_reasoning_level"):
                effort = mm["default_reasoning_level"]
                note = f"reasoning effort {effort} (model default)"
    effort = effort.lower() if effort else None
    if effort == "none":
        return False, None, "reasoning off"
    return True, (effort if effort in EFFORTS else None), note


# ---------------------------------------------------------------- hook plumbing

def state_file(agent, sid):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or "nosession"))
    return HOME / "state" / f"{agent}-{safe}.json"


def prune_state():
    d = HOME / "state"
    if not d.exists():
        return
    now = time.time()
    for p in d.glob("*.json"):
        try:
            if now - p.stat().st_mtime > STATE_TTL_S:
                p.unlink()
        except OSError:
            pass


def run_hook(agent):
    raw = sys.stdin.read()
    inp = json.loads(raw) if raw.strip() else {}
    event = inp.get("hook_event_name") or "SessionStart"
    idx = Index(load_data())

    if agent == "codex":
        model = inp.get("model")
        reasoning, effort, note = codex_effort(model)
        source = ""
    else:
        model, reasoning, effort, note, source = claude_context(inp, idx)

    text, matched = describe(idx, model, reasoning, effort, note, source, agent)
    sig = f"{model}|{matched}|{effort}|{reasoning}"
    sf = state_file(agent, inp.get("session_id"))
    prev = read_json(sf).get("sig") if sf.exists() else None

    if event == "UserPromptSubmit":
        if prev == sig or not model:
            return
        if prev is not None:
            text = text.replace("<how-good-am-i ", "<how-good-am-i update=\"the model or reasoning setting changed since the earlier how-good-am-i block; this one supersedes it\" ", 1)
    elif event != "SessionStart":
        return

    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({"sig": sig, "t": time.time()}))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    if event == "SessionStart":
        prune_state()


def run_opencode():
    inp = json.loads(sys.stdin.read() or "{}")
    idx = Index(load_data())
    model = inp.get("model") or ""
    variant = (inp.get("variant") or "").lower() or None
    if variant == "default":
        variant = None
    provider, _, mid = model.rpartition("/")
    if variant in ("none", "off", "non-reasoning"):
        reasoning, effort, note = False, None, f"variant {variant}"
    elif variant:
        reasoning, effort, note = True, (variant if variant in EFFORTS else None), f"variant {variant}"
    elif provider == "openai" and mid.startswith("gpt-5"):
        reasoning, effort, note = True, "medium", "no variant set (OpenCode sends reasoning effort medium)"
    elif "claude" in mid:
        reasoning, effort, note = True, "high", "no variant set (API default effort; shown: high)"
    else:
        reasoning, effort, note = None, None, ""
    text, _ = describe(idx, model, reasoning, effort, note, "", "opencode")
    sys.stdout.write(text)


def preview():
    idx = Index(load_data())
    print(f"Data: {data_path()} ({idx.data.get('n_models')} models, fetched {idx.data.get('fetched_at')})\n")
    s = claude_settings(os.getcwd())
    print("=" * 30, "Claude Code (from settings; live sessions use the exact model from the transcript)")
    model, reasoning, effort, note, source = claude_context({"cwd": os.getcwd()}, idx)
    print(describe(idx, model, reasoning, effort, note, source)[0] if (model or s) else "(no Claude Code settings found)")
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.exists():
        m = re.search(r"^model\s*=\s*['\"]([^'\"]+)['\"]", cfg.read_text().split("\n[", 1)[0], re.M)
        if m:
            print("\n" + "=" * 30, "Codex (from ~/.codex/config.toml)")
            reasoning, effort, note = codex_effort(m.group(1))
            print(describe(idx, m.group(1), reasoning, effort, note)[0])


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--refresh":
        refresh()
        return
    if arg in ("claude", "codex"):
        try:
            maybe_refresh_in_background()
        except Exception:
            pass
        run_hook(arg)
    elif arg == "opencode":
        run_opencode()
    elif arg == "--model":  # manual test: hgai.py --model gpt-5.5 [effort]
        idx = Index(load_data())
        eff = sys.argv[3] if len(sys.argv) > 3 else None
        print(describe(idx, sys.argv[2], None if eff is None else eff != "none", eff, f"effort {eff}" if eff else "")[0])
    else:
        preview()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if len(sys.argv) > 1 and sys.argv[1] in ("claude", "codex", "opencode"):
            try:
                HOME.mkdir(parents=True, exist_ok=True)
                with open(HOME / "error.log", "a") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(sys.argv[1:]) + "\n" + traceback.format_exc() + "\n")
            except Exception:
                pass
            sys.exit(0)
        raise
