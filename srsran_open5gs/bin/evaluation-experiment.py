#!/usr/bin/env python3

import argparse
import json
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from experiment_framework.config import load_and_resolve_study  # noqa: E402
from experiment_framework.modes import study_plan  # noqa: E402
from experiment_framework.results import expected_result_layout, write_json  # noqa: E402
from experiment_framework.runner import PilotRunner  # noqa: E402
from experiment_framework.summarize import summarize_run  # noqa: E402


def parser():
    result = argparse.ArgumentParser(description="Research evaluation experiment framework")
    commands = result.add_subparsers(dest="command", required=True)

    resolve = commands.add_parser("resolve", help="validate and resolve a study without running it")
    resolve.add_argument("study")
    resolve.add_argument("--parameters", action="append", default=[])
    resolve.add_argument("--output", required=True)

    plan = commands.add_parser("plan", help="print the resolved execution plan without running it")
    plan.add_argument("study")
    plan.add_argument("--parameters", action="append", default=[])

    run = commands.add_parser("run", help="run the configured benchmark study")
    run.add_argument("study")
    run.add_argument("--parameters", action="append", default=[])
    run.add_argument("--namespace")
    run.add_argument(
        "--confirm-live",
        action="store_true",
        help="required acknowledgement that Kubernetes and radio processes will be changed",
    )

    summarize = commands.add_parser("summarize", help="regenerate tables and plots for an existing run")
    summarize.add_argument("run_directory")
    return result


def main():
    args = parser().parse_args()
    if args.command == "summarize":
        summary = summarize_run(args.run_directory)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    resolved = load_and_resolve_study(args.study, parameter_files=getattr(args, "parameters", []))
    if args.command == "resolve":
        write_json(args.output, resolved)
        print(args.output)
        return
    if args.command == "plan":
        print(json.dumps({
            "study": resolved,
            "plan": study_plan(resolved),
            "result_layout": expected_result_layout(
                resolved["result_root"],
                resolved["study_id"],
            ),
        }, indent=2, sort_keys=True))
        return
    if not args.confirm_live:
        raise SystemExit("run requires --confirm-live; no experiment was started")
    output = PilotRunner(resolved, namespace=args.namespace).run()
    print(output)


if __name__ == "__main__":
    main()
