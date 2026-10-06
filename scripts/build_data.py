"""Fetch the Artificial Analysis LLM benchmark data and write data/benchmarks.json.

Run by the daily GitHub Action (and runnable locally): `python scripts/build_data.py`.
Needs an Artificial Analysis API key in AA_API_KEY (or ARTIFICIAL_ANALYSIS_API_KEY /
ARTIFICIAL_ANALYTICS_API_KEY). Free keys: https://artificialanalysis.ai/ (Insights Platform).

Only stable, benchmark-like fields are kept (no prices or speeds), so the file only changes
when AA adds a model or re-scores one. All models are kept so any model can be matched.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmarks.json"
KEY_ENV_VARS = ["AA_API_KEY", "ARTIFICIAL_ANALYSIS_API_KEY", "ARTIFICIAL_ANALYTICS_API_KEY"]
TIMEOUT_S = 60


def api_key():
    for name in KEY_ENV_VARS:
        if os.environ.get(name):
            return os.environ[name]
    sys.exit(f"No Artificial Analysis API key found (set one of: {', '.join(KEY_ENV_VARS)})")


def fetch():
    req = urllib.request.Request(API_URL, headers={"x-api-key": api_key(), "User-Agent": "how-good-am-i/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        payload = json.load(r)
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 50:
        sys.exit(f"Unexpected API response (data has {len(data) if isinstance(data, list) else 'no'} rows); not overwriting")
    return data


def compact(m):
    ev = {k: round(v, 4) for k, v in (m.get("evaluations") or {}).items() if isinstance(v, (int, float))}
    creator = m.get("model_creator") or {}
    return {
        "id": m.get("id"),
        "slug": m.get("slug"),
        "name": m.get("name"),
        "creator": creator.get("name"),
        "creator_slug": creator.get("slug"),
        "released": m.get("release_date"),
        "ev": ev,
    }


def main():
    models = [compact(m) for m in fetch() if m.get("slug") and m.get("name")]
    models.sort(key=lambda m: (m["creator_slug"] or "", m["released"] or "", m["slug"]))
    new_body = {
        "source": "Artificial Analysis (https://artificialanalysis.ai/), free API /api/v2/data/llms/models",
        "attribution": "Benchmark data from Artificial Analysis, https://artificialanalysis.ai/",
        "n_models": len(models),
        "models": models,
    }
    # Keep fetched_at stable when nothing changed, so the daily Action only commits real changes.
    if OUT_PATH.exists():
        old = json.loads(OUT_PATH.read_text())
        if {k: v for k, v in old.items() if k != "fetched_at"} == new_body:
            print(f"No change ({len(models)} models); leaving {OUT_PATH.name} untouched")
            return
    out = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **new_body}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(OUT_PATH)
    print(f"Wrote {len(models)} models to {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
