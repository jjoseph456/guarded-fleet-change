# Guarded Fleet Change

[![CI](https://github.com/jjoseph456/guarded-fleet-change/actions/workflows/ci.yml/badge.svg)](https://github.com/jjoseph456/guarded-fleet-change/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Policy-gated, approval-bound, staged Ansible rollouts for Linux fleets.

Large fleet changes rarely fail because the playbook is wrong. They fail
because the change hit a host with a stale backup, took down both halves of a
database pair, skipped staging, or ran from a plan nobody reviewed. This
project puts those decisions in front of Ansible, where they can be checked.

The worked example is a common enterprise migration: moving Linux identity
from a legacy directory agent to SSSD across three sites.

All hosts, domains, and data are synthetic. This project contains no employer
code, inventory, credentials, or customer information.

## How a Change Flows

```text
change request ──> plan ──> policy gate ──> approval ──> wave 1 (canary)
                    │           │              │             │
            readiness checks   deny rules   bound to      check mode by default,
            exclude unsafe     block unsafe plan digest,   apply in order,
            hosts              plans        no self-       rescue restores files,
                                            approval       health gate halts
```

1. **Plan.** `fleetguard plan` selects hosts, excludes any that fail
   readiness, and packs the rest into a staging canary, staging waves, then
   production waves. It records a SHA-256 digest of the plan.
2. **Policy gate.** `fleetguard check` re-validates the plan independently of
   the planner, so a hand-edited plan cannot slip through.
3. **Approval.** `fleetguard approve` refuses a plan with violations or an
   approval by its own requester, then binds the approval to the plan digest.
4. **Run.** `fleetguard run` builds the Ansible command for one wave. Without
   `--apply` it runs `--check --diff`. With `--apply` it requires a matching
   approval and every earlier wave recorded as complete.
5. **Ansible.** The `sssd_client` role backs up identity files, configures
   SSSD, validates it with `sssctl config-check`, and restores the originals
   if any step fails. A post-task health gate stops the wave if `sssd` is not
   running.

## Policy Rules

| Rule | Denies |
| --- | --- |
| `CANARY_REQUIRED` | A plan whose first wave is not a single staging canary |
| `WAVE_TOO_LARGE` | A wave larger than the configured percentage of the fleet |
| `HA_PAIR_SAME_WAVE` | Both members of a high-availability pair in one wave |
| `BLOCKED_HOST_IN_PLAN` | A host that fails readiness, even if added by hand |
| `PRODUCTION_BEFORE_STAGING` | Returning to staging after production began |
| `FREEZE_WINDOW` | A planned start inside a change freeze |
| `UNKNOWN_HOST`, `DUPLICATE_HOST` | Hosts outside the plan facts or planned twice |

Readiness checks exclude hosts with an unsupported OS, no monitoring, or a
production backup older than 24 hours.

## Quick Start

Requires Python 3.10 or later.

```bash
git clone https://github.com/jjoseph456/guarded-fleet-change.git
cd guarded-fleet-change
python -m pip install -e ".[ansible]"

fleetguard plan changes/directory-migration.yml \
  --requested-by alex --start-at 2026-10-06T14:00:00Z
fleetguard check plan.json
fleetguard approve plan.json --approver sam
fleetguard run plan.json --wave 1 --print-only
```

Example plan output:

```text
Wrote plan.json: 8 wave(s), 3 excluded host(s).
  excluded app-03.site-a.example.test: STALE_BACKUP: latest backup is 96h old (limit 24h)
  excluded app-04.site-b.example.test: NO_MONITORING: health gates cannot observe this host
  excluded bastion-01.site-b.example.test: UNSUPPORTED_OS: centos7 is not a supported target
PASS  8 wave(s) satisfy policy.
```

`--print-only` shows the gated command without running it. The synthetic
hosts do not exist, so real runs need your own inventory.

Exit codes: `0` success, `1` invalid input or a failed gate, `2` policy
violations from `check`.

## Testing

| Layer | What it proves |
| --- | --- |
| `python -m unittest discover -s tests` | Planner, policy, digest, approval, and wave-order gates |
| `ansible-lint --offline ansible/` | The role and playbook pass the `production` lint profile |
| `molecule test` in `ansible/roles/sssd_client` | Converge, idempotence, and verify on Rocky Linux 9 and Ubuntu 24.04 containers |

Containers have no init system, so Molecule disables service management.
Joining a real directory and resolving users is outside the test scope.

## Repository Layout

```text
changes/                    change requests
inventory/synthetic/        synthetic fleet facts and generated INI inventory
src/fleetguard/             planner, readiness, policy, approval, runner, CLI
ansible/playbooks/          wave playbook with pre-run assertion and health gate
ansible/roles/sssd_client/  role, templates, and Molecule scenario
tests/                      unit and CLI tests
```

## Roadmap

- Rego policies evaluated with OPA alongside the Python gate
- Signed approvals and SLSA build provenance for releases
- A read-only planning agent that proposes change requests, with every tool
  call traced through OpenTelemetry and every plan still passing the gate
- Automatic pause and resume between waves using monitoring signals
