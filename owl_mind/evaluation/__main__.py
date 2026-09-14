"""CLI for the evaluation harness.

    python -m owl_mind.evaluation check          # contamination only, free
    python -m owl_mind.evaluation run            # full run -- COSTS MONEY
    python -m owl_mind.evaluation sweep <run>    # re-score a run, free
    python -m owl_mind.evaluation compare <run>  # against the pinned baseline
    python -m owl_mind.evaluation promote <run>  # make a run the baseline

``run`` is the only subcommand that spends anything. ``promote`` is explicit on
purpose: a harness that overwrote its own baseline would redefine "regression"
as "worse than last time" rather than "worse than the release", which is the
reference implementation's mistake recorded in evaluator.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from owl_mind.evaluation import contamination, harness, sweep
from owl_mind.evaluation.corpus import load, summarise


def _print_summary(report: dict) -> None:
    intent = report["intent"]
    scope = report["scope"]
    routing = report["routing"]
    print(f"\n  scored {report['scored']} cases, {report['signal_failures']} failed")
    print(f"  accuracy {intent['accuracy']:.3f}   macro-F1 {intent['macro_f1']:.3f}")
    print(
        f"  OOS recall {scope['oos_recall']:.3f}   "
        f"false declines {scope['false_decline_rate']:.3f}"
    )
    print(
        f"  primary agent {routing['primary_accuracy']:.3f}   "
        f"fan-out {routing['fan_out']}   multi-agent {routing['multi_agent_share']:.3f}"
    )
    if report.get("by_source"):
        print("\n  by provenance (seed = written by a model; see data/eval/README.md):")
        for source, scores in sorted(report["by_source"].items()):
            print(f"    {source:<6} n={scores['total']:<4} macro-F1 {scores['macro_f1']:.3f}")
    worst = intent.get("worst_confusions", [])
    if worst:
        print("\n  worst confusions:")
        for row in worst[:5]:
            print(f"    {row['expected']} -> {row['predicted']}  x{row['count']}")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    command = argv[1] if len(argv) > 1 else "check"

    if command == "check":
        cases = load()
        summary = summarise(cases).as_dict()
        result = contamination.report(cases, semantic=True)
        print(json.dumps({"corpus": summary, "contamination": result}, indent=2))
        collisions = result["lexical_collisions"] + result["semantic_collisions"]
        if collisions:
            print(f"\nCONTAMINATED: {len(collisions)} collision(s)", file=sys.stderr)
            return 1
        print("\ncorpus is clean")
        return 0

    if command == "run":
        report = asyncio.run(harness.run(semantic_check=True))
        path = harness.write_run(report)
        _print_summary(report)
        print(f"\n  written to {path}")
        return 0

    if command in {"sweep", "compare", "promote"}:
        if len(argv) < 3:
            print(f"usage: python -m owl_mind.evaluation {command} <run.json>", file=sys.stderr)
            return 2
        run_path = Path(argv[2])
        payload = json.loads(run_path.read_text(encoding="utf-8"))

        if command == "sweep":
            records = sweep.load_records(run_path)
            result = sweep.report(records)
            print(sweep.format_grid(result["grid"]))
            print(f"\nF13: {result['f13']['verdict']}")
            for example in result["f13"]["examples"]:
                print(f"  {example}")
            return 0

        if command == "compare":
            if not harness.BASELINE_PATH.exists():
                print("no baseline yet; run promote first", file=sys.stderr)
                return 2
            baseline = json.loads(harness.BASELINE_PATH.read_text(encoding="utf-8"))
            drops = harness.compare(payload, baseline)
            if drops:
                print(f"REGRESSION: {len(drops)} class(es) past the margin", file=sys.stderr)
                for line in drops:
                    print(f"  {line}", file=sys.stderr)
                return 1
            print("no per-class regression past the margin")
            return 0

        harness.BASELINE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"baseline set from {run_path}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
