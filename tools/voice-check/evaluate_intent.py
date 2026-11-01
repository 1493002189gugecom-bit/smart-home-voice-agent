"""Run the configured intent API on a bounded text corpus, never on devices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "voice-service" / "src"))

import config
from agent_client import AgentClient
from ambient_intent import create_intent_classifier
from intent_evaluation import IntentEvaluation, CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=len(CASES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    # A configuration-only object, with no home-service connection or executor.
    agent = SimpleNamespace(client=AgentClient(config.agent_api_key(), base_url=config.agent_base_url(),
                            model=config.agent_model(), timeout=config.intent_timeout_seconds()))
    evaluation = IntentEvaluation(lambda: create_intent_classifier(agent))
    evaluation.start({"limit": args.limit})
    previous = -1
    try:
        while True:
            report = evaluation.status()
            completed = len(report["results"])
            if completed != previous:
                print(json.dumps({"status": report["status"], "completed": completed, "total": report["total"]}), flush=True)
                previous = completed
            if report["status"] != "running":
                break
            time.sleep(.1)
    except KeyboardInterrupt:
        evaluation.stop()
        return 130
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "summary": report["summary"]}), flush=True)
    summary = report["summary"]
    return 0 if report["status"] == "completed" and not summary.get("unavailable") and summary.get("passed") == summary.get("scored") else 1


if __name__ == "__main__":
    raise SystemExit(main())
