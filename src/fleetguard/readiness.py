"""Per-host readiness checks that decide whether a host may join a rollout."""

from __future__ import annotations

from typing import Any

SUPPORTED_OS = {"rhel8", "rhel9", "rocky9", "ubuntu2204", "ubuntu2404"}
MAX_PRODUCTION_BACKUP_AGE_HOURS = 24


def host_blockers(host: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if host["os"] not in SUPPORTED_OS:
        reasons.append(f"UNSUPPORTED_OS: {host['os']} is not a supported target")
    if not host["monitoring"]:
        reasons.append("NO_MONITORING: health gates cannot observe this host")
    if (
        host["environment"] == "production"
        and host["backup_age_hours"] > MAX_PRODUCTION_BACKUP_AGE_HOURS
    ):
        reasons.append(
            "STALE_BACKUP: latest backup is "
            f"{host['backup_age_hours']}h old (limit {MAX_PRODUCTION_BACKUP_AGE_HOURS}h)"
        )
    return reasons
