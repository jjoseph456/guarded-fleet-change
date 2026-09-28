"""Command-line interface: plan, check, approve, run, and inventory."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .approval import ApprovalError, approve
from .models import load_change, load_fleet, load_plan, parse_time, write_json
from .planner import build_plan
from .policy import evaluate
from .runner import WaveGateError, record_completion, wave_command


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_optional(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def cmd_plan(args: argparse.Namespace) -> int:
    change = load_change(args.change)
    fleet = load_fleet(args.fleet)
    start = parse_time(args.start_at, "--start-at") if args.start_at else _now()
    plan = build_plan(change, fleet, requested_by=args.requested_by, start_at=start)
    write_json(args.output, plan)
    print(f"Wrote {args.output}: {len(plan['waves'])} wave(s), {len(plan['excluded'])} excluded host(s).")
    for item in plan["excluded"]:
        print(f"  excluded {item['host']}: {'; '.join(item['reasons'])}")
    print(f"plan_sha256 {plan['plan_sha256']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    violations = evaluate(plan)
    if args.format == "json":
        print(json.dumps({"plan_sha256": plan["plan_sha256"], "violations": [v.to_dict() for v in violations]}, indent=2))
    elif violations:
        for violation in violations:
            print(f"DENY  {violation.rule}  {violation.message}")
    else:
        print(f"PASS  {len(plan['waves'])} wave(s) satisfy policy.")
    return 2 if violations else 0


def cmd_approve(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    record = approve(plan, approver=args.approver, now=_now())
    write_json(args.output, record)
    print(f"Approved {plan['change_id']} as {args.approver}; wrote {args.output}.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    state = _read_optional(args.state)
    command = wave_command(
        plan,
        args.wave,
        inventory=args.inventory,
        apply=args.apply,
        approval=_read_optional(args.approval),
        state=state,
    )
    print(("APPLY " if args.apply else "CHECK ") + " ".join(command))
    if args.print_only:
        return 0
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"Wave {args.wave} failed with exit code {result.returncode}; halting rollout.")
        return result.returncode
    if args.apply:
        write_json(args.state, record_completion(state, plan, args.wave))
        print(f"Recorded wave {args.wave} as complete in {args.state}.")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    fleet = load_fleet(args.fleet)
    groups: dict[str, list[str]] = {}
    for host in fleet:
        for group in (host["site"], host["role"], host["environment"]):
            groups.setdefault(group.replace("-", "_"), []).append(host["name"])
    lines = ["# Generated from a synthetic fleet by `fleetguard inventory`.", ""]
    for group in sorted(groups):
        lines.append(f"[{group}]")
        lines.extend(sorted(groups[group]))
        lines.append("")
    args.output.write_text("\n".join(lines))
    print(f"Wrote {args.output} with {len(fleet)} host(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetguard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Build a staged rollout plan.")
    p.add_argument("change", type=Path)
    p.add_argument("--fleet", type=Path, default=Path("inventory/synthetic/fleet.json"))
    p.add_argument("--requested-by", required=True)
    p.add_argument("--start-at", help="Planned start, ISO 8601 with timezone. Defaults to now.")
    p.add_argument("-o", "--output", type=Path, default=Path("plan.json"))
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("check", help="Evaluate a plan against policy.")
    p.add_argument("plan", type=Path)
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("approve", help="Approve a policy-clean plan.")
    p.add_argument("plan", type=Path)
    p.add_argument("--approver", required=True)
    p.add_argument("-o", "--output", type=Path, default=Path("approval.json"))
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("run", help="Dry-run or apply one wave with Ansible.")
    p.add_argument("plan", type=Path)
    p.add_argument("--wave", type=int, required=True)
    p.add_argument("--inventory", default="inventory/synthetic/hosts.ini")
    p.add_argument("--apply", action="store_true", help="Apply changes. Default is --check --diff.")
    p.add_argument("--approval", type=Path, default=Path("approval.json"))
    p.add_argument("--state", type=Path, default=Path(".fleetguard/state.json"))
    p.add_argument("--print-only", action="store_true", help="Print the gated command without running it.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("inventory", help="Render an Ansible INI inventory from the fleet.")
    p.add_argument("--fleet", type=Path, default=Path("inventory/synthetic/fleet.json"))
    p.add_argument("-o", "--output", type=Path, default=Path("inventory/synthetic/hosts.ini"))
    p.set_defaults(func=cmd_inventory)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ApprovalError, WaveGateError, ValueError, FileNotFoundError) as error:
        print(f"error: {error}")
        return 1
