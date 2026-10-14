"""Build submission/AgentSprint_<TeamName>.zip from git-tracked files (+ latest outbox.json / run_trace.json).

Usage: python make_submission.py [TeamName] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

EXCLUDE = re.compile(
    r"(^|/)(\.env$|\.env\.(?!example$)|\.venv/|venv/|credentials/|logs/|__pycache__/|\.pytest|submission/|team_kit/|"
    r"\.cache/|\.streamlit/secrets\.toml)"
    r"|\.(sqlite3?|db|pyc|docx|pdf)$|(^|/)(token|client_secret|credentials)[^/]*\.json$"
)
SECRET = re.compile(rb"AIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])")  # real Google API key shape
GENERATED = ("outbox.json", "run_trace.json")  # results of the last run, useful for the reviewers


def submission_problems(inbox: Path = Path("data/inbox"), outbox: Path = Path("outbox.json")) -> list[str]:
    """Demo leftovers that must not be submitted: LIVE-* test emails, or outbox entries without an inbox file."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from helpdesk.dashboard_data import LIVE_PREFIX, read_json, stale_entries

    problems = []
    live = sorted(p.name for p in inbox.glob(f"{LIVE_PREFIX}*"))
    if live:
        problems.append(f"data/inbox contains live test emails: {live}")
    entries = read_json(outbox, [])
    stale = stale_entries(inbox, entries if isinstance(entries, list) else [])
    if stale:
        problems.append(f"outbox.json has entries with no file in data/inbox: {stale}")
    live_out = [e.get("email_id") for e in entries if isinstance(e, dict) and str(e.get("email_id", "")).startswith(LIVE_PREFIX)]
    if live_out:
        problems.append(f"outbox.json contains live test entries: {live_out}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("team", nargs="?", default="TeamName")
    parser.add_argument("--out-dir", default="submission", type=Path)
    parser.add_argument("--force", action="store_true", help="build even if demo leftovers are found")
    args = parser.parse_args()
    git = ["git", "ls-files"]
    files = subprocess.run(git, capture_output=True, text=True, check=True).stdout.splitlines()  # noqa: S603
    files += [f for f in GENERATED if Path(f).exists()]
    files = sorted({f for f in files if Path(f).is_file() and not EXCLUDE.search(f)})
    leaks = [f for f in files if SECRET.search(Path(f).read_bytes())]
    if leaks:
        print(f"REFUSING: these files look like they contain an API key: {leaks}")
        return 1
    problems = submission_problems()
    if problems and not args.force:
        print("REFUSING to build the submission:")
        for p in problems:
            print(f"  - {p}")
        print("Fix it (dashboard: Live Run > Clear live test emails, then `python agent.py`), or pass --force.")
        return 2
    out = args.out_dir / f"AgentSprint_{args.team}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f"AgentSprint_{args.team}/{f}", Path(f).read_bytes())
    print(f"Wrote {out} ({len(files)} files, {out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
