"""Student Helpdesk Triage Agent.

Usage:
  python agent.py                 process every email (re-uses cached decisions), rewrite outbox.json
  python agent.py --only-new      process only new/changed emails and merge them into outbox.json
  python agent.py --file PATH     process one file and merge it into outbox.json
  python agent.py --watch         keep watching the inbox; process new/changed files as they appear
  python agent.py --fresh         ignore the cache and call the LLM again for everything
  python agent.py --offline       no LLM: transparent rule-based fallback only
Nothing is ever sent: replies are drafts in outbox.json.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from helpdesk.cache import DecisionCache
from helpdesk.config import load_settings
from helpdesk.llm import GeminiClient
from helpdesk.pipeline import Runner, TriageAgent
from helpdesk.policy import load_policy


def setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("helpdesk")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    file = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(console)
    logger.addHandler(file)
    for noisy in ("httpx", "google_genai", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Student Helpdesk Triage Agent")
    parser.add_argument("--inbox", default="data/inbox", type=Path)
    parser.add_argument("--policy", default="data/policy.md", type=Path)
    parser.add_argument("--out", default="outbox.json", type=Path)
    parser.add_argument("--trace", default=None, type=Path, help="decision trace (default: next to --out)")
    parser.add_argument("--log", default="logs/agent_run.log", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--only-new", action="store_true", help="only new/changed emails, merged into outbox")
    mode.add_argument("--file", type=Path, help="process one email file and merge it into outbox")
    mode.add_argument("--watch", action="store_true", help="poll the inbox and process new files live")
    parser.add_argument("--interval", type=float, default=3.0, help="--watch polling interval (seconds)")
    parser.add_argument("--fresh", action="store_true", help="ignore cached decisions")
    parser.add_argument("--offline", action="store_true", help="skip the LLM; rule-based fallback only")
    parser.add_argument("--cache-dir", default=".cache", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true", help="also print reply drafts")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging(args.log, args.verbose)
    log = logging.getLogger("helpdesk")

    settings = load_settings()
    try:
        policy = load_policy(args.policy)
    except (FileNotFoundError, ValueError) as exc:
        log.error("Policy problem: %s", exc)
        return 1
    inbox = args.file.parent if args.file else args.inbox
    if not inbox.is_dir():
        log.error("Inbox folder not found: %s", inbox)
        return 1
    if args.file and not args.file.is_file():
        log.error("File not found: %s", args.file)
        return 1

    client = None
    if args.offline:
        log.info("Offline mode: rule-based fallback only")
    elif settings.api_key:
        client = GeminiClient(settings.api_key, settings.models, settings.max_attempts,
                              state_file=args.cache_dir / "model_state.json")
        log.info("LLM: Gemini model '%s', fallbacks %s (API key loaded, not shown)%s",
                 settings.model, settings.models[1:], " | cache disabled (--fresh)" if args.fresh else "")
    else:
        log.warning("No GEMINI_API_KEY found (.env or environment): using rule-based FALLBACK for all emails")

    cache = DecisionCache(args.cache_dir, read=not args.fresh, write=True)
    agent = TriageAgent(policy, client, settings.delay_seconds, cache)
    runner = Runner(agent, inbox, args.out, args.trace)
    if args.watch:
        runner.watch(args.interval)
    elif args.file:
        runner.run("file", only=[args.file])
    else:
        runner.run("only-new" if args.only_new else "all")
    log.info("Full log: %s", args.log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
