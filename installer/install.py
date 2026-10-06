"""Install (or with --uninstall, remove) how-good-am-i for Claude Code, Codex and OpenCode.

Called by install.sh. Safe to re-run: existing how-good-am-i hook entries are replaced, never
duplicated, and every config file it edits is backed up first (<file>.hgai-backup-<timestamp>).
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("HGAI_HOME") or (Path.home() / ".how-good-am-i"))
MARK = "how-good-am-i/hgai.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CODEX_DIR = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
OPENCODE_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "opencode"
OPENCODE_PLUGIN = OPENCODE_DIR / "plugins" / "how-good-am-i.ts"


def hook_cmd(agent):
    # Silent no-op if the runtime was removed, so a stale hook can never error.
    return f'f="$HOME/.how-good-am-i/hgai.py"; [ -f "$f" ] && exec python3 "$f" {agent}; exit 0'


def say(msg):
    print(msg, flush=True)


def backup(p):
    if p.exists():
        shutil.copy2(p, p.with_name(f"{p.name}.hgai-backup-{STAMP}"))


def write_json(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".hgai-tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.replace(p)


def strip_ours(hooks):
    """Remove every how-good-am-i entry from a {"Event": [ {matcher, hooks: [...]}, ...]} dict."""
    for event in list(hooks):
        groups = []
        for g in hooks[event] or []:
            inner = [h for h in g.get("hooks", []) if MARK not in str(h.get("command", ""))]
            if inner:
                groups.append({**g, "hooks": inner})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    return hooks


def edit_hooks(path, agent, install):
    if not path.exists() and not install:
        return False
    try:
        d = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
    except json.JSONDecodeError as e:
        say(f"  ! {path} is not valid JSON ({e}); not touching it. Add the hooks by hand (see README).")
        return False
    hooks = strip_ours(d.get("hooks") or {})
    if install:
        for event in ("SessionStart", "UserPromptSubmit"):
            hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": hook_cmd(agent), "timeout": 10}]})
    if hooks:
        d["hooks"] = hooks
    else:
        d.pop("hooks", None)
    backup(path)
    write_json(path, d)
    return True


def install_runtime():
    HOME.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "hgai" / "hgai.py", HOME / "hgai.py")
    shutil.copy2(SRC / "hgai" / "trail.json", HOME / "trail.json")
    shutil.copy2(SRC / "opencode" / "how-good-am-i.ts", HOME / "how-good-am-i.ts")
    shutil.copy2(SRC / "installer" / "install.py", HOME / "install.py")
    # Keep whichever benchmark snapshot is newer (the installed one may have auto-refreshed).
    new = SRC / "data" / "benchmarks.json"
    cur = HOME / "benchmarks.json"
    fetched = lambda p: json.loads(p.read_text()).get("fetched_at", "") if p.exists() else ""
    if fetched(new) >= fetched(cur):
        shutil.copy2(new, cur)


def main():
    uninstall = "--uninstall" in sys.argv
    install = not uninstall
    did = []

    if install:
        install_runtime()
        say(f"Installed runtime to {HOME}")

    if CLAUDE_DIR.exists() or shutil.which("claude"):
        if edit_hooks(CLAUDE_DIR / "settings.json", "claude", install):
            did.append("Claude Code")
            say(f"  {'+' if install else '-'} Claude Code hooks in {CLAUDE_DIR / 'settings.json'}")

    if CODEX_DIR.exists() or shutil.which("codex"):
        if edit_hooks(CODEX_DIR / "hooks.json", "codex", install):
            did.append("Codex")
            say(f"  {'+' if install else '-'} Codex hooks in {CODEX_DIR / 'hooks.json'}")

    if OPENCODE_DIR.exists() or shutil.which("opencode"):
        if install:
            OPENCODE_PLUGIN.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC / "opencode" / "how-good-am-i.ts", OPENCODE_PLUGIN)
            did.append("OpenCode")
            say(f"  + OpenCode plugin at {OPENCODE_PLUGIN}")
        elif OPENCODE_PLUGIN.exists():
            OPENCODE_PLUGIN.unlink()
            did.append("OpenCode")
            say(f"  - OpenCode plugin removed from {OPENCODE_PLUGIN}")

    if uninstall:
        if HOME.exists():
            shutil.rmtree(HOME)
            say(f"Removed {HOME}")
        say("how-good-am-i uninstalled" + (f" from: {', '.join(did)}" if did else "") + ". Config backups (*.hgai-backup-*) were left in place.")
        return

    if not did:
        say("No Claude Code, Codex or OpenCode config found. Install one of them, then re-run this installer.")
        return
    say("")
    say(f"how-good-am-i is set up for: {', '.join(did)}. New sessions will start with a benchmark block for the model in use.")
    if "Codex" in did:
        say("  Codex: run /hooks once inside Codex and trust the two how-good-am-i hooks (Codex skips new hooks until you do).")
    if "Claude Code" in did:
        say("  Claude Code: already-running sessions pick it up on their next start (or /clear); check with /hooks.")
    say(f"  Preview what your agents will see:  python3 {HOME / 'hgai.py'}")
    say(f"  Uninstall:  python3 {HOME / 'install.py'} --uninstall")


if __name__ == "__main__":
    main()
