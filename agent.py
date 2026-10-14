"""Student Helpdesk Triage Agent.

Usage:  python agent.py [--inbox data/inbox] [--policy data/policy.md] [--out outbox.json]
Reads every *.json email in the inbox, triages it, and writes reply DRAFTS to outbox.json.
Nothing is ever sent.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from helpdesk.config import load_settings
from helpdesk.llm import GeminiClient
from helpdesk.pipeline import TriageAgent
from helpdesk.policy import load_policy


def setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("helpdesk")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    file = logging.FileHandler(log_file, mode="w", encoding="utf-8")
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
    parser.add_argument("--log", default="logs/agent_run.log", type=Path)
    parser.add_argument("--offline", action="store_true", help="skip the LLM; rule-based fallback only")
    parser.add_argument("-v", "--verbose", action="store_true", help="also print reply drafts")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging(args.log, args.verbose)
    log = logging.getLogger("helpdesk")

    settings = load_settings()
    policy = load_policy(args.policy)
    if not args.inbox.is_dir():
        log.error("Inbox folder not found: %s", args.inbox)
        return 1

    client = None
    if args.offline:
        log.info("Offline mode: rule-based fallback only")
    elif settings.api_key:
        client = GeminiClient(settings.api_key, settings.models, settings.max_attempts)
        log.info("LLM: Gemini model '%s', fallbacks %s (API key loaded, not shown)",
                 settings.model, settings.models[1:])
    else:
        log.warning("No GEMINI_API_KEY found (.env or environment): using rule-based FALLBACK for all emails")

    TriageAgent(policy, client, settings.delay_seconds).run(args.inbox, args.out)
    log.info("Full log: %s", args.log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
