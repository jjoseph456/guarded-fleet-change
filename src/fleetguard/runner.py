"""Build and gate the Ansible command for one rollout wave."""

from __future__ import annotations

from typing import Any

from .approval import verify_approval
from .policy import evaluate


class WaveGateError(ValueError):
    pass


def completed_waves(state: dict[str, Any] | None, plan: dict[str, Any]) -> set[int]:
    if not state or state.get("plan_sha256") != plan["plan_sha256"]:
        return set()
    return {int(i) for i in state.get("completed_waves", [])}


def wave_command(
    plan: dict[str, Any],
    wave_index: int,
    *,
    inventory: str,
    apply: bool,
    approval: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> list[str]:
    waves = {wave["index"]: wave for wave in plan["waves"]}
    if wave_index not in waves:
        raise WaveGateError(f"Wave {wave_index} does not exist in this plan.")

    violations = evaluate(plan)
    if violations:
        raise WaveGateError("Plan fails policy; run `fleetguard check` for details.")

    if apply:
        verify_approval(plan, approval)
        done = completed_waves(state, plan)
        missing = [i for i in range(1, wave_index) if i not in done]
        if missing:
            raise WaveGateError(
                f"Waves {missing} have not completed; apply waves in order."
            )
        if wave_index in done:
            raise WaveGateError(f"Wave {wave_index} is already recorded as complete.")

    command = [
        "ansible-playbook",
        plan["playbook"],
        "-i",
        inventory,
        "--limit",
        ",".join(waves[wave_index]["hosts"]),
        "-e",
        f"fleetguard_change_id={plan['change_id']} fleetguard_wave={wave_index}",
    ]
    if not apply:
        command += ["--check", "--diff"]
    return command


def record_completion(state: dict[str, Any] | None, plan: dict[str, Any], wave_index: int) -> dict[str, Any]:
    done = completed_waves(state, plan)
    done.add(wave_index)
    return {
        "change_id": plan["change_id"],
        "plan_sha256": plan["plan_sha256"],
        "completed_waves": sorted(done),
    }
