"""Plan approval bound to a plan digest, with separation of duties."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import format_time
from .policy import evaluate


class ApprovalError(ValueError):
    pass


def approve(plan: dict[str, Any], *, approver: str, now: datetime) -> dict[str, Any]:
    if not approver.strip():
        raise ApprovalError("An approver is required.")
    if approver.strip() == plan["requested_by"]:
        raise ApprovalError("The requester cannot approve their own plan.")
    violations = evaluate(plan)
    if violations:
        raise ApprovalError(
            f"Plan has {len(violations)} policy violation(s); fix them before approval."
        )
    return {
        "schema": "fleetguard.approval/v1",
        "change_id": plan["change_id"],
        "plan_sha256": plan["plan_sha256"],
        "approver": approver.strip(),
        "approved_at": format_time(now),
    }


def verify_approval(plan: dict[str, Any], approval: dict[str, Any] | None) -> None:
    if approval is None:
        raise ApprovalError("Applying a change requires an approval file.")
    if approval.get("change_id") != plan["change_id"]:
        raise ApprovalError("Approval belongs to a different change.")
    if approval.get("plan_sha256") != plan["plan_sha256"]:
        raise ApprovalError("Approval is stale: the plan changed after it was approved.")
