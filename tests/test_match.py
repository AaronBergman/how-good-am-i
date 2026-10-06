"""Model-id matching tests against the committed data snapshot. Run: python3 -m unittest discover tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hgai"))
os.environ["HGAI_HOME"] = tempfile.mkdtemp()  # use the repo's data/, never the installed copy
os.environ["HGAI_NO_REFRESH"] = "1"
import hgai  # noqa: E402

IDX = hgai.Index(hgai.load_data())

# (id as an agent reports it, reasoning, effort) -> expected AA slug (None = no match expected)
CASES = [
    (("claude-opus-5-5", True, "xhigh"), "claude-opus-5-5-xhigh"),
    (("claude-opus-5-5", True, "max"), "claude-opus-5-5"),
    (("claude-opus-5-5[1m]", True, "high"), "claude-opus-5-5-high"),
    (("claude-sonnet-4-5-20250929", True, "high"), "claude-4-5-sonnet-thinking"),
    (("claude-sonnet-4-5-20250929", False, None), "claude-4-5-sonnet"),
    (("us.anthropic.claude-sonnet-4-5-20250929-v1:0", True, "high"), "claude-4-5-sonnet-thinking"),
    (("claude-opus-4-1-20250805", True, "high"), "claude-4-1-opus-thinking"),
    (("claude-3-5-sonnet-20240620", None, None), "claude-35-sonnet-june-24"),
    (("claude-3-5-sonnet-20241022", None, None), "claude-35-sonnet"),
    (("claude-fable-5-1", True, "medium"), "claude-fable-5-1-medium"),
    (("gpt-6.1-sol", True, "high"), "gpt-6-1-sol-high"),
    (("gpt-5.5", True, "medium"), "gpt-5-5-medium"),
    (("gpt-5-codex", True, "medium"), "gpt-5-codex"),
    (("gpt-4o-2024-08-06", None, None), "gpt-4o-2024-08-06"),
    (("openai/gpt-5", True, "medium"), "gpt-5-medium"),
    (("google/gemini-2.5-pro", None, None), "gemini-2-5-pro"),
    (("totally-unknown-model", None, None), None),
]


class TestMatch(unittest.TestCase):
    def test_cases(self):
        for (mid, reasoning, effort), want in CASES:
            m, _, _ = IDX.match(mid, reasoning, effort)
            if want is None or want in IDX.by_slug:  # AA occasionally renames slugs; skip vanished ones
                self.assertEqual(m["slug"] if m else None, want, mid)

    def test_approximate_is_flagged(self):
        m, _, approx = IDX.match("gpt-5.1-codex-max", True, "high")
        self.assertTrue(approx)
        self.assertIsNotNone(m)

    def test_alias(self):
        slug = IDX.resolve_alias("opus")
        self.assertIn("opus", slug)


class TestHook(unittest.TestCase):
    def run_hook(self, agent, payload):
        r = subprocess.run([sys.executable, str(ROOT / "hgai" / "hgai.py"), agent], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_codex_session_start_then_change(self):
        base = {"session_id": "t1", "cwd": "/tmp", "model": "gpt-5.5", "source": "startup"}
        out = self.run_hook("codex", {**base, "hook_event_name": "SessionStart"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("GPT-5.5", ctx)
        self.assertIn("<- you", ctx)
        # same model on the next prompt: silent
        self.assertIsNone(self.run_hook("codex", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
        # model switched mid-session: re-injected as an update
        out = self.run_hook("codex", {**base, "model": "gpt-5.4", "hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        self.assertIn('update="', out["hookSpecificOutput"]["additionalContext"])

    def test_garbage_input_never_fails(self):
        r = subprocess.run([sys.executable, str(ROOT / "hgai" / "hgai.py"), "claude"], input="not json",
                           capture_output=True, text=True, timeout=30, env=os.environ)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
