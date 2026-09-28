"""Policy gate that independently validates a plan before approval or execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import parse_time


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def evaluate(plan: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    facts = plan["host_facts"]
    waves = plan["waves"]
    limit = plan["max_wave_size"]

    if not waves:
        violations.append(Violation("EMPTY_PLAN", "The plan contains no eligible hosts."))
        return violations

    first = waves[0]
    if first["kind"] != "canary" or first["environment"] != "staging":
        violations.append(
            Violation("CANARY_REQUIRED", "The first wave must be a staging canary.")
        )

    seen: set[str] = set()
    production_started = False
    for wave in waves:
        label = f"wave {wave['index']}"
        hosts = wave["hosts"]

        if wave["kind"] == "wave" and len(hosts) > limit:
            violations.append(
                Violation("WAVE_TOO_LARGE", f"{label} has {len(hosts)} hosts; limit is {limit}.")
            )

        pairs = [facts[h]["ha_pair"] for h in hosts if facts[h]["ha_pair"]]
        for pair in sorted({p for p in pairs if pairs.count(p) > 1}):
            violations.append(
                Violation("HA_PAIR_SAME_WAVE", f"{label} changes both members of {pair}.")
            )

        for host in hosts:
            if host not in facts:
                violations.append(Violation("UNKNOWN_HOST", f"{label} includes unknown host {host}."))
                continue
            if host in seen:
                violations.append(Violation("DUPLICATE_HOST", f"{host} appears in more than one wave."))
            seen.add(host)
            for reason in facts[host]["blockers"]:
                violations.append(Violation("BLOCKED_HOST_IN_PLAN", f"{host}: {reason}"))

        if wave["environment"] == "production":
            production_started = True
        elif production_started:
            violations.append(
                Violation(
                    "PRODUCTION_BEFORE_STAGING",
                    f"{label} returns to staging after production changes began.",
                )
            )

    start = parse_time(plan["start_at"], "start_at")
    for window in plan["freeze_windows"]:
        if parse_time(window["start"], "freeze start") <= start < parse_time(window["end"], "freeze end"):
            violations.append(
                Violation("FREEZE_WINDOW", f"Planned start {plan['start_at']} is inside a change freeze.")
            )

    return violations
