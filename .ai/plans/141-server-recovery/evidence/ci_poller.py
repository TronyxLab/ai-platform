#!/usr/bin/env python3
# GREP_SUMMARY: ci-ops session-141 poller gh runs state tsv monitor
# STRUCTURE: load state json → gh api runs → diff transitions → render ci-runs.tsv → ⎋ print NEW/CHANGED
# Usage: python3 ci_poller.py [--limit N]  (state: .ci-state.json, tsv: ci-runs.tsv, same dir)
"""ci-ops poller for session 141 — tracks GitHub Actions runs, renders ci-runs.tsv.

Own files (untracked session tooling, not platform code):
  .ci-state.json  — run_id -> last recorded status (persistence between polls)
  ci-runs.tsv     — one row per run: run_id, workflow, status, started, duration_s, cause
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = "Tronyx161/AI-platform"
HERE = Path(__file__).resolve().parent
STATE = HERE / ".ci-state.json"
TSV = HERE / "ci-runs.tsv"
TSV_HEADER = "run_id\tworkflow\tstatus\tstarted\tduration_s\tcause\n"


def gh_api(url: str) -> list[dict]:
    """Fetch workflow runs via gh api; fail verbosely."""
    out = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{REPO}/actions/runs?{url}",
            "--jq",
            ".workflow_runs[] | {id, name, display_title, head_branch, event, status, conclusion, created_at, run_started_at, updated_at}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    return sorted(rows, key=lambda r: r["id"])


def dur_s(run: dict) -> int:
    """Duration: updated_at - run_started_at (completed) or updated_at - created_at (active)."""
    end = run["updated_at"] or run["created_at"]
    start = run["run_started_at"] or run["created_at"]
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((t1 - t0).total_seconds()))


def cause_of(run: dict) -> str:
    """Short human cause: event + commit title."""
    ev = run["event"]
    if ev == "workflow_run":
        return "parent:workflow_run"
    title = run["display_title"] or run["name"]
    title = " ".join(title.split())
    return f"{ev}:{title[:70]}"


def status_of(run: dict) -> str:
    if run["status"] != "completed":
        return "in_progress"
    return run["conclusion"] or "completed"


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def main(limit: int) -> int:
    runs = gh_api(f"per_page={limit}")[-limit:]
    state = load_state()
    changed = []
    for r in runs:
        rid = str(r["id"])
        st = status_of(r)
        prev = state.get(rid, {}).get("status")
        if prev is None:
            changed.append(("NEW", rid, r["name"], st))
        elif prev != st:
            changed.append(("CHANGED", rid, r["name"], prev, "->", st))
        state[rid] = {
            "workflow": r["name"],
            "status": st,
            "started": r["run_started_at"] or r["created_at"],
            "duration_s": dur_s(r),
            "cause": cause_of(r),
        }
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))

    rows = sorted(state.items(), key=lambda kv: kv[0])
    lines = [TSV_HEADER]
    for rid, meta in rows:
        lines.append(
            f"{rid}\t{meta['workflow']}\t{meta['status']}\t{meta['started']}\t{meta['duration_s']}\t{meta['cause']}\n"
        )
    TSV.write_text("".join(lines))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[IMP:1][ci_poller] {now} snapshot: {len(runs)} runs, {len(state)} tracked")
    if not changed:
        print("[IMP:7][ci_poller] no transitions since last poll")
    for c in changed:
        print(f"[IMP:9][ci_poller] {' '.join(str(x) for x in c)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    try:
        sys.exit(main(args.limit))
    except subprocess.CalledProcessError as e:
        print(f"[IMP:10][ci_poller] BLOCKED gh api rc={e.returncode}: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(e.returncode)
